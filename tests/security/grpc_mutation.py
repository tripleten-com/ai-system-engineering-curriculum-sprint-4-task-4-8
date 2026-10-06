"""Coldline.

===================

File:              tests/security/grpc_mutation.py
Component:         Security tooling — gRPC interceptor mutations
Purpose:           Record what the student's gRPC tests actually call, then rerun them against
                    the API with mutated copies of the supplied token interceptor.
Interacts With:    tests/student/test_grpc_ingest.py, tests/security/grpc_harness.py,
                    tests/security/trace.py, src/api/security/grpc_auth.py, the running API
                    container, tests/contract/test_grpc_ingest_contract.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Mutation testing, tests that can fail, exact status assertions, evidence from
                    executed calls
Tools:             Python 3.12, pytest (as a subprocess), Docker Compose

A test proves something only if it can fail. This module first runs the student's
``tests/student/test_grpc_ingest.py`` once against the running API as it is, with the
interceptor in place and every call recorded through the harness (``tests/security/trace.py``).
That run is the **inventory**, one entry per collected pytest case:

- an **accepted-call case** made a ``SubmitReading`` call with ``gateway-valid`` that ended
  ``OK`` and then read the returned exception id through ``GET /api/v1/exceptions/{id}`` as
  ``dispatcher-valid`` (through ``ingest.api_client`` or ``ingest.exception_exists``);
- a **refused-call case** sent a fresh reading with ``dispatcher-valid`` through the helper
  ``ingest.send_fresh_reading``.

At least one case of each kind is required, and every one of them must pass as written. Then,
for each mutation, it replaces ``src/api/security/grpc_auth.py`` **inside the running API
container** with a mutated copy, restarts the API container, runs the student file again, and
requires every case the mutation targets to have the junit outcome ``failed``:

- ``interceptor-removed``: the interceptor in the container admits every call, as removing your
  registration does. Every refused-call case must fail.
- ``status-swapped``: it answers ``UNAUTHENTICATED`` for ``PERMISSION_DENIED`` and the reverse.
  Every refused-call case must fail.
- ``refusal-after-acceptance``: it lets a refused call run the method, then ends it
  ``PERMISSION_DENIED``. Every refused-call case must fail.
- ``identity-swapped``: it answers an accepted call with another exception id. Every
  accepted-call case must fail.

Only ``failed`` counts: ``passed`` or ``skipped`` means the test did not notice the change, and
an errored case makes the run invalid. The repository's own copy of the interceptor never
changes, so the integrity bookends of ``poe verify`` still hold; ``restore`` recreates the API
container from its image, which brings the supplied interceptor back, and the assessed module
calls it when its last mutation row has run. ``python -m tests.security.grpc_mutation``
(``poe grpc-mutation``) prints the inventory and every verdict, then restores the container.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import httpx

from tests.runtime_config import host_port
from tests.security import trace

TASK_ROOT = Path(__file__).resolve().parents[2]
STUDENT_TEST = Path("tests/student/test_grpc_ingest.py")
INTERCEPTOR = Path("src/api/security/grpc_auth.py")
INTERCEPTOR_MODULE = "api.security.grpc_auth"
COMPOSE = ("docker", "compose", "--profile", "observability", "--profile", "localstack")
ACCEPTED_TOKEN = "gateway-valid"
REFUSED_TOKEN = "dispatcher-valid"
READER_TOKEN = "dispatcher-valid"
MUTATIONS: tuple[str, ...] = (
    "interceptor-removed",
    "status-swapped",
    "refusal-after-acceptance",
    "identity-swapped",
)
# Which inventory each mutation must make fail.
TARGETS: dict[str, str] = {
    "interceptor-removed": "refused",
    "status-swapped": "refused",
    "refusal-after-acceptance": "refused",
    "identity-swapped": "accepted",
}
PYTEST_TIMEOUT_SECONDS = 600
COMPOSE_TIMEOUT_SECONDS = 300
READY_TIMEOUT_SECONDS = 180.0
# pytest's exit codes that are verdicts: 0 all passed, 1 some failed, 5 nothing collected.
PYTEST_VERDICT_EXITS = frozenset({0, 1, 5})
WRITE_SCRIPT = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
LOCATE_SCRIPT = f"import {INTERCEPTOR_MODULE} as module; print(module.__file__)"


class MutationError(RuntimeError):
    """Report that a mutation could not be applied or run, as opposed to a test verdict."""


# --- The mutants: exact edits of the supplied interceptor ---------------------------------

STATUS_TABLE = (
    '    "unauthenticated": grpc.StatusCode.UNAUTHENTICATED,\n'
    '    "permission_denied": grpc.StatusCode.PERMISSION_DENIED,\n'
)
STATUS_TABLE_SWAPPED = (
    '    "unauthenticated": grpc.StatusCode.PERMISSION_DENIED,\n'
    '    "permission_denied": grpc.StatusCode.UNAUTHENTICATED,\n'
)
CHECK_BLOCK = (
    "        authorization = metadata_value(call_details.invocation_metadata, METADATA_KEY)\n"
    "        try:\n"
    "            check_access(self._verifier, authorization, role=self.role, scope=self.scope)\n"
    "        except AccessDenied as exc:\n"
    "            return refusal_handler(STATUS_BY_KIND[exc.kind], exc.reason)\n"
    "        handler = await continuation(call_details)\n"
    "        return handler\n"
)
CHECK_BLOCK_REMOVED = "        return await continuation(call_details)\n"
REFUSAL = "            return refusal_handler(STATUS_BY_KIND[exc.kind], exc.reason)\n"
REFUSAL_AFTER = (
    "            return refuse_after(\n"
    "                await continuation(call_details), STATUS_BY_KIND[exc.kind], exc.reason\n"
    "            )\n"
)
RETURN_HANDLER = "        return handler\n"
RETURN_SWAPPED = "        return swap_identity(handler)\n"
REFUSE_AFTER_HELPER = '''

def refuse_after(handler: Any, code: Any, details: str) -> Any:
    """Mutation refusal-after-acceptance: run the method, then end the call refused."""
    import inspect

    if handler is None or handler.unary_unary is None:
        return refusal_handler(code, details)
    behaviour = handler.unary_unary

    async def run_then_refuse(request: object, context: Any) -> None:
        outcome = behaviour(request, context)
        if inspect.isawaitable(outcome):
            await outcome
        await context.abort(code, details)

    return grpc.unary_unary_rpc_method_handler(
        run_then_refuse,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )
'''
SWAP_IDENTITY_HELPER = '''

def swap_identity(handler: Any) -> Any:
    """Mutation identity-swapped: answer an accepted call with another exception id."""
    import inspect

    if handler is None or handler.unary_unary is None:
        return handler
    behaviour = handler.unary_unary

    async def swapped(request: object, context: Any) -> Any:
        response = behaviour(request, context)
        if inspect.isawaitable(response):
            response = await response
        if response is not None and hasattr(response, "exception_id"):
            response.exception_id = "exc-mutation-identity"
        return response

    return grpc.unary_unary_rpc_method_handler(
        swapped,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )
'''
# Each mutation's edits, applied in order: (text to find exactly once, its replacement), and
# the helper appended to the module, if any.
EDITS: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {
    "interceptor-removed": (((CHECK_BLOCK, CHECK_BLOCK_REMOVED),), ""),
    "status-swapped": (((STATUS_TABLE, STATUS_TABLE_SWAPPED),), ""),
    "refusal-after-acceptance": (((REFUSAL, REFUSAL_AFTER),), REFUSE_AFTER_HELPER),
    "identity-swapped": (((RETURN_HANDLER, RETURN_SWAPPED),), SWAP_IDENTITY_HELPER),
}


def mutate(name: str, source: str) -> str:
    """Return the supplied interceptor's source with one mutation applied, or raise.

    Every anchor must occur exactly once in the source, and the result must compile: the
    supplied module is fixed, so a missing anchor means the module is not the supplied one.
    """
    if name not in EDITS:
        raise MutationError(f"unknown mutation {name!r}; choose one of {', '.join(MUTATIONS)}")
    edits, helper = EDITS[name]
    mutated = source.replace("\r\n", "\n")
    for anchor, replacement in edits:
        count = mutated.count(anchor)
        if count != 1:
            raise MutationError(
                f"{INTERCEPTOR.as_posix()} is not the supplied interceptor: mutation {name} "
                f"expected one anchor and found {count}"
            )
        mutated = mutated.replace(anchor, replacement)
    mutated += helper
    try:
        compile(mutated, INTERCEPTOR.as_posix(), "exec")
    except SyntaxError as exc:
        raise MutationError(f"mutation {name} does not compile: {exc}") from exc
    return mutated


# --- The inventory: what each collected test actually called ------------------------------


@dataclass(frozen=True)
class RunResult:
    """What one run of the student file reported: outcomes per case, the trace, pytest's exit."""

    label: str
    outcomes: dict[str, str]
    events: list[dict[str, object]]
    returncode: int
    output: str = ""


@dataclass(frozen=True)
class Inventory:
    """The student file's cases as written, sorted into accepted-call and refused-call cases."""

    outcomes: dict[str, str]
    accepted: frozenset[str]
    refused: frozenset[str]

    @classmethod
    def from_run(cls, outcomes: dict[str, str], events: list[dict[str, object]]) -> Inventory:
        """Sort the run's cases by the calls and reads the trace recorded for each."""
        accepted_ids: dict[str, set[str]] = {case: set() for case in outcomes}
        read_ids: dict[str, set[str]] = {case: set() for case in outcomes}
        refused: set[str] = set()
        for event in events:
            case = event.get("case")
            if not isinstance(case, str) or case not in outcomes:
                continue
            kind = event.get("kind")
            exception_id = event.get("exception_id")
            if kind == "grpc":
                if (
                    event.get("fixture") == ACCEPTED_TOKEN
                    and event.get("status") == "OK"
                    and isinstance(exception_id, str)
                ):
                    accepted_ids[case].add(exception_id)
                if event.get("helper") is True and event.get("fixture") == REFUSED_TOKEN:
                    refused.add(case)
            elif kind == "http":
                if (
                    event.get("fixture") == READER_TOKEN
                    and event.get("method") == "GET"
                    and isinstance(exception_id, str)
                ):
                    read_ids[case].add(exception_id)
            elif kind == "created" and isinstance(exception_id, str):
                read_ids[case].add(exception_id)
        accepted = {case for case in outcomes if accepted_ids[case] & read_ids[case]}
        return cls(dict(outcomes), frozenset(accepted), frozenset(refused))

    def cases(self, kind: str) -> frozenset[str]:
        """Return the accepted-call or the refused-call cases."""
        return self.accepted if kind == "accepted" else self.refused

    def problems(self, kind: str) -> list[str]:
        """Return why the cases of one kind cannot carry a mutation verdict yet."""
        if not self.outcomes:
            return [
                f"{STUDENT_TEST.as_posix()} ran no test; write the accepted-call and the "
                "refused-call tests Step 4 asks for"
            ]
        found = self.cases(kind)
        if not found:
            if kind == "accepted":
                return [
                    f"no test made a call with {ACCEPTED_TOKEN} that returned OK and then read "
                    f"the returned exception id through GET /api/v1/exceptions/<exception_id> "
                    f"as {READER_TOKEN}: write the accepted-call test"
                ]
            return [
                f"no test sent a fresh reading with {REFUSED_TOKEN} through "
                "ingest.send_fresh_reading: write the refused-call test with the helper"
            ]
        return [
            f"{case} {self.outcomes[case]} with the interceptor in place; it must pass first"
            for case in sorted(found)
            if self.outcomes[case] != "passed"
        ]

    def describe(self) -> str:
        """Render the inventory as a Markdown table."""
        lines = ["| Case | Kind | Outcome as written |", "|---|---|---|"]
        for case in sorted(self.outcomes):
            kinds = [kind for kind in ("accepted", "refused") if case in self.cases(kind)]
            lines.append(f"| `{case}` | {', '.join(kinds) or '-'} | {self.outcomes[case]} |")
        return "\n".join(lines)


@dataclass(frozen=True)
class Verdict:
    """Whether one mutation made every targeted case fail, and why not."""

    mutation: str
    required: frozenset[str]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return whether the mutation is proven by the student's tests."""
        return not self.problems


def parse_junit(report: Path) -> dict[str, str]:
    """Return each junit case's outcome, keyed by the full case id the trace also uses."""
    outcomes: dict[str, str] = {}
    if not report.is_file():
        return outcomes
    for case in ET.parse(report).iter("testcase"):
        identity = trace.junit_case_id(
            case.attrib.get("classname", ""), case.attrib.get("name", "")
        )
        if identity in outcomes:
            raise MutationError(f"the junit report names {identity} twice")
        if case.find("error") is not None:
            outcomes[identity] = "error"
        elif case.find("failure") is not None:
            outcomes[identity] = "failed"
        elif case.find("skipped") is not None:
            outcomes[identity] = "skipped"
        else:
            outcomes[identity] = "passed"
    return outcomes


def judge(name: str, inventory: Inventory, result: RunResult) -> Verdict:
    """Decide whether one mutation made every targeted case fail; only ``failed`` counts."""
    kind = TARGETS[name]
    required = inventory.cases(kind)
    problems = inventory.problems(kind)
    if problems:
        return Verdict(name, required, problems)
    errored = sorted(case for case, outcome in result.outcomes.items() if outcome == "error")
    if errored:
        problems.append(
            f"the run under {name} is invalid: {', '.join(errored)} errored; a fixture, setup "
            "or collection error is not a failing assertion"
        )
    for case in sorted(required):
        outcome = result.outcomes.get(case)
        if outcome is None:
            problems.append(f"{case} produced no test case under {name}")
        elif outcome == "passed":
            problems.append(f"{case} still passes under {name}")
        elif outcome == "skipped":
            problems.append(f"{case} was skipped under {name}")
    return Verdict(name, required, problems)


# --- Running the student file -------------------------------------------------------------


def run_student_file(root: Path, label: str) -> RunResult:
    """Run the student file once against the API as it is now, with a junit report and a trace."""
    if not (root / STUDENT_TEST).is_file():
        raise MutationError(f"{STUDENT_TEST.as_posix()} does not exist")
    with tempfile.TemporaryDirectory(prefix="coldline-grpc-mutation-") as temporary:
        report = Path(temporary) / "report.xml"
        recorded = Path(temporary) / "trace.jsonl"
        environment = os.environ.copy()
        environment[trace.TRACE_VARIABLE] = str(recorded)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(root / STUDENT_TEST),
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    f"--junitxml={report}",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=PYTEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise MutationError(
                f"{STUDENT_TEST.as_posix()} ({label}) ran past {PYTEST_TIMEOUT_SECONDS}s"
            ) from exc
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode not in PYTEST_VERDICT_EXITS:
            tail = "\n".join(output.splitlines()[-15:])
            raise MutationError(
                f"pytest exited {completed.returncode} running {STUDENT_TEST.as_posix()} "
                f"({label}), which is not a verdict (collection, usage or internal error):\n{tail}"
            )
        if completed.returncode != 5 and not report.is_file():
            raise MutationError(f"pytest wrote no junit report running {label}")
        return RunResult(
            label, parse_junit(report), trace.read_events(recorded), completed.returncode, output
        )


# --- The API container --------------------------------------------------------------------


def _compose(
    root: Path, *arguments: str, stdin: bytes | None = None, label: str
) -> subprocess.CompletedProcess[bytes]:
    """Run one Docker Compose command for this checkout's project, or raise naming ``label``."""
    try:
        completed = subprocess.run(
            [*COMPOSE, *arguments],
            cwd=root,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=COMPOSE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MutationError(f"{label} could not run: {exc}") from exc
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise MutationError(f"{label} failed (exit {completed.returncode}): {message}")
    return completed


def wait_until_serving(root: Path, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    """Wait until the API answers ready and its gRPC port accepts a connection, or raise."""
    api = f"http://localhost:{host_port('COLDLINE_API_HOST_PORT', 8000, root=root)}/health/ready"
    target = f"127.0.0.1:{host_port('COLDLINE_GRPC_HOST_PORT', 50051, root=root)}"
    deadline = time.monotonic() + timeout
    last = "no answer yet"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(api, timeout=3.0)
            if response.status_code == 200:
                break
            last = f"ready answered {response.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(1.0)
    else:
        raise MutationError(f"the API did not become ready within {timeout:.0f}s: {last}")
    with grpc.insecure_channel(target) as channel:
        try:
            grpc.channel_ready_future(channel).result(timeout=30)
        except grpc.FutureTimeoutError as exc:
            raise MutationError(f"the gRPC port {target} accepted no connection") from exc


class ApiContainer:
    """Install a mutated interceptor in the running API container, and restore the supplied one."""

    def __init__(self, root: Path = TASK_ROOT) -> None:
        """Bind to one checkout's Compose project."""
        self.root = root
        self.mutated: str | None = None

    def install(self, name: str) -> None:
        """Write mutation ``name`` over the interceptor inside the container and restart the API."""
        source = mutate(name, (self.root / INTERCEPTOR).read_text(encoding="utf-8-sig"))
        located = _compose(
            self.root, "exec", "-T", "api", "python", "-c", LOCATE_SCRIPT, label="locating"
        )
        target = located.stdout.decode("utf-8").strip().splitlines()[-1]
        self.mutated = name
        _compose(
            self.root,
            "exec",
            "-T",
            "--user",
            "root",
            "api",
            "python",
            "-c",
            WRITE_SCRIPT,
            target,
            stdin=source.encode("utf-8"),
            label=f"writing the {name} interceptor",
        )
        _compose(self.root, "restart", "api", label="restarting the API")
        wait_until_serving(self.root)
        written = _compose(
            self.root,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            f"import hashlib; print(hashlib.sha256(open({target!r}, 'rb').read()).hexdigest())",
            label="reading the installed interceptor back",
        )
        expected = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if written.stdout.decode("utf-8").strip() != expected:
            raise MutationError(f"the {name} interceptor was not the one the container holds")

    def restore(self) -> None:
        """Recreate the API container from its image, with the supplied interceptor."""
        _compose(
            self.root,
            "up",
            "--detach",
            "--wait",
            "--force-recreate",
            "--no-deps",
            "api",
            label="restoring the API container",
        )
        wait_until_serving(self.root)
        self.mutated = None


# --- One process's runs, kept so the assessed rows share them ----------------------------


class MutationSession:
    """The inventory and the mutation runs of one process, run at most once each."""

    def __init__(
        self,
        root: Path = TASK_ROOT,
        *,
        container: ApiContainer | None = None,
        runner: Callable[[Path, str], RunResult] = run_student_file,
    ) -> None:
        """Bind to one checkout; ``container`` and ``runner`` are replaceable for unit tests."""
        self.root = root
        self.container = ApiContainer(root) if container is None else container
        self._runner = runner
        self._inventory: Inventory | None = None
        self._inventory_error: str | None = None
        self._verdicts: dict[str, Verdict] = {}

    def inventory(self) -> Inventory:
        """Run the student file as written once, with the supplied interceptor; sort its cases."""
        if self._inventory is None:
            if self._inventory_error is not None:
                raise MutationError(self._inventory_error)
            try:
                if self.container.mutated is not None:
                    self.container.restore()
                result = self._runner(self.root, "as written")
            except MutationError as exc:
                self._inventory_error = str(exc)
                raise
            self._inventory = Inventory.from_run(result.outcomes, result.events)
        return self._inventory

    def verdict(self, name: str) -> Verdict:
        """Return mutation ``name``'s verdict, installing it and running the file the first time."""
        if name not in self._verdicts:
            kind = TARGETS[name]
            try:
                inventory = self.inventory()
            except MutationError as exc:
                return Verdict(name, frozenset(), [f"your tests could not be run: {exc}"])
            problems = inventory.problems(kind)
            if problems:
                self._verdicts[name] = Verdict(name, inventory.cases(kind), problems)
            else:
                try:
                    self.container.install(name)
                    result = self._runner(self.root, name)
                except MutationError as exc:
                    self._verdicts[name] = Verdict(name, inventory.cases(kind), [str(exc)])
                else:
                    self._verdicts[name] = judge(name, inventory, result)
        return self._verdicts[name]

    def close(self) -> None:
        """Restore the supplied interceptor if a mutation is still installed."""
        if self.container.mutated is not None:
            self.container.restore()


def main(argv: list[str] | None = None) -> int:
    """Print the inventory and every mutation's verdict, then restore the API container."""
    del argv
    session = MutationSession()
    exit_code = 0
    try:
        try:
            inventory = session.inventory()
        except MutationError as exc:
            print(f"## inventory\n\nnot run: {exc}")
            return 1
        print("## inventory\n")
        print(inventory.describe())
        print()
        for name in MUTATIONS:
            verdict = session.verdict(name)
            print(f"## {name}\n")
            print(f"must fail: {', '.join(sorted(verdict.required)) or '(none found)'}")
            if verdict.ok:
                print("verdict: every targeted case failed under this mutation\n")
            else:
                exit_code = 1
                print("verdict: NOT proven")
                for problem in verdict.problems:
                    print(f"- {problem}")
                print()
    finally:
        session.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
