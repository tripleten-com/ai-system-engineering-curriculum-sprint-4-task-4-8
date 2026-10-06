"""Coldline.

===================

File:              tests/unit/security/test_grpc_mutation.py
Component:         Unit tests — gRPC interceptor mutations
Purpose:           Prove the four mutants apply to the supplied interceptor and compile, that the
                    inventory sorts cases by their recorded calls, and that only `failed` proves
                    a mutation.
Interacts With:    tests/security/grpc_mutation.py, src/api/security/grpc_auth.py,
                    tests/security/trace.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Mutation testing, closed inventories, skipped is not failed
Tools:             Python 3.12, pytest

No container and no student file are involved: the runs are stand-ins with invented case
names and recorded events, and the container is a stand-in that records what was installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.security import grpc_mutation
from tests.security.grpc_mutation import Inventory, MutationSession, RunResult

TASK_ROOT = Path(__file__).resolve().parents[3]
INTERCEPTOR = TASK_ROOT / "src/api/security/grpc_auth.py"
ACCEPTED = "tests/student/test_example.py::test_accepted_example"
REFUSED = "tests/student/test_example.py::test_refused_example"
OTHER = "tests/student/test_example.py::test_other_example"


def _events() -> list[dict[str, object]]:
    """Return the trace of one accepted-call case, one refused-call case and one other case."""
    return [
        {
            "case": ACCEPTED,
            "kind": "grpc",
            "fixture": "gateway-valid",
            "status": "OK",
            "exception_id": "exc-example-1",
            "helper": False,
        },
        {
            "case": ACCEPTED,
            "kind": "http",
            "fixture": "dispatcher-valid",
            "method": "GET",
            "exception_id": "exc-example-1",
            "status_code": 200,
        },
        {
            "case": REFUSED,
            "kind": "grpc",
            "fixture": "dispatcher-valid",
            "status": "PERMISSION_DENIED",
            "exception_id": None,
            "helper": True,
        },
        {"case": REFUSED, "kind": "created", "exception_id": "exc-example-2", "created": False},
        {
            "case": OTHER,
            "kind": "grpc",
            "fixture": "gateway-valid",
            "status": "OK",
            "exception_id": "exc-example-3",
            "helper": False,
        },
    ]


def _passing() -> dict[str, str]:
    """Return every invented case passing."""
    return {ACCEPTED: "passed", REFUSED: "passed", OTHER: "passed"}


@pytest.mark.parametrize("name", grpc_mutation.MUTATIONS)
def test_every_mutation_applies_once_to_the_supplied_interceptor_and_compiles(name: str) -> None:
    """Each mutant changes the shipped interceptor where it should and still compiles."""
    source = INTERCEPTOR.read_text(encoding="utf-8")

    mutated = grpc_mutation.mutate(name, source)

    assert mutated != source
    compile(mutated, "grpc_auth.py", "exec")


def test_the_mutants_change_what_their_names_say() -> None:
    """The removed mutant admits every call, the swapped one trades statuses, two add helpers."""
    source = INTERCEPTOR.read_text(encoding="utf-8")

    removed = grpc_mutation.mutate("interceptor-removed", source)
    assert "check_access(self._verifier" not in removed
    swapped = grpc_mutation.mutate("status-swapped", source)
    assert '"unauthenticated": grpc.StatusCode.PERMISSION_DENIED' in swapped
    after = grpc_mutation.mutate("refusal-after-acceptance", source)
    assert "return refuse_after(" in after and "def refuse_after(" in after
    identity = grpc_mutation.mutate("identity-swapped", source)
    assert "return swap_identity(handler)" in identity and "def swap_identity(" in identity


def test_a_source_that_is_not_the_supplied_interceptor_is_refused() -> None:
    """A missing anchor means the module is not the supplied one: refused, never guessed."""
    with pytest.raises(grpc_mutation.MutationError, match="not the supplied interceptor"):
        grpc_mutation.mutate("status-swapped", "STATUS_BY_KIND = {}\n")
    with pytest.raises(grpc_mutation.MutationError, match="unknown mutation"):
        grpc_mutation.mutate("route-removed", INTERCEPTOR.read_text(encoding="utf-8"))


def test_the_inventory_sorts_cases_by_their_recorded_calls() -> None:
    """An OK gateway call read back as the dispatcher is accepted; a helper call refused."""
    inventory = Inventory.from_run(_passing(), _events())

    assert inventory.accepted == frozenset({ACCEPTED})
    assert inventory.refused == frozenset({REFUSED})
    assert inventory.problems("accepted") == []
    assert inventory.problems("refused") == []


def test_an_accepted_call_read_back_through_the_helper_check_also_counts() -> None:
    """A check of the returned id through ``exception_exists`` reads through the endpoint too."""
    events = [event for event in _events() if event.get("kind") != "http"]
    events.append(
        {"case": ACCEPTED, "kind": "created", "exception_id": "exc-example-1", "created": True}
    )

    assert Inventory.from_run(_passing(), events).accepted == frozenset({ACCEPTED})


def test_a_read_of_another_id_or_as_another_fixture_does_not_count() -> None:
    """The read must be of the id the call returned, as the dispatcher."""
    events = _events()
    events[1] = {**events[1], "exception_id": "exc-example-9"}
    assert Inventory.from_run(_passing(), events).accepted == frozenset()

    events = _events()
    events[1] = {**events[1], "fixture": "gateway-valid"}
    assert Inventory.from_run(_passing(), events).accepted == frozenset()


def test_problems_name_an_empty_file_a_missing_kind_and_a_failing_case() -> None:
    """No cases, no case of a kind, and a case that fails as written each have their message."""
    empty = Inventory.from_run({}, [])
    assert "ran no test" in empty.problems("refused")[0]

    only_refused = Inventory.from_run(
        {REFUSED: "passed"}, [event for event in _events() if event["case"] == REFUSED]
    )
    assert "write the accepted-call test" in only_refused.problems("accepted")[0]

    failing = Inventory.from_run({**_passing(), REFUSED: "failed"}, _events())
    assert failing.problems("refused") == [
        f"{REFUSED} failed with the interceptor in place; it must pass first"
    ]


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("failed", None),
        ("passed", "still passes"),
        ("skipped", "was skipped"),
        ("error", "is invalid"),
    ],
)
def test_only_a_failed_case_proves_a_mutation(outcome: str, expected: str | None) -> None:
    """A passed or skipped targeted case is not proof, and an errored run is invalid."""
    inventory = Inventory.from_run(_passing(), _events())
    result = RunResult("status-swapped", {**_passing(), REFUSED: outcome}, [], 1)

    verdict = grpc_mutation.judge("status-swapped", inventory, result)

    if expected is None:
        assert verdict.ok
    else:
        assert any(expected in problem for problem in verdict.problems), verdict.problems


def test_a_targeted_case_missing_from_the_run_is_named() -> None:
    """A case that produced no outcome under the mutation is not a pass."""
    inventory = Inventory.from_run(_passing(), _events())
    result = RunResult("identity-swapped", {REFUSED: "passed"}, [], 0)

    verdict = grpc_mutation.judge("identity-swapped", inventory, result)

    assert f"{ACCEPTED} produced no test case under identity-swapped" in verdict.problems


class StandInContainer:
    """Record what was installed and restored instead of touching Docker."""

    def __init__(self) -> None:
        """Start with nothing installed."""
        self.mutated: str | None = None
        self.installed: list[str] = []
        self.restored = 0

    def install(self, name: str) -> None:
        """Record one install."""
        self.mutated = name
        self.installed.append(name)

    def restore(self) -> None:
        """Record one restore."""
        self.mutated = None
        self.restored += 1


def test_a_session_runs_each_mutation_once_and_restores_at_the_end() -> None:
    """The inventory runs once unmutated, each mutation once, and close restores the container."""
    container = StandInContainer()
    runs: list[str] = []

    def runner(root: Path, label: str) -> RunResult:
        runs.append(label)
        if label == "as written":
            return RunResult(label, _passing(), _events(), 0)
        return RunResult(label, {ACCEPTED: "failed", REFUSED: "failed", OTHER: "passed"}, [], 1)

    session = MutationSession(TASK_ROOT, container=container, runner=runner)
    for name in (*grpc_mutation.MUTATIONS, "status-swapped"):
        assert session.verdict(name).ok
    session.close()

    assert runs == ["as written", *grpc_mutation.MUTATIONS]
    assert container.installed == list(grpc_mutation.MUTATIONS)
    assert container.restored == 1
    assert container.mutated is None


def test_a_session_without_the_cases_installs_nothing() -> None:
    """When the file has no refused-call case, no mutation is installed and the verdict says why."""
    container = StandInContainer()

    def runner(root: Path, label: str) -> RunResult:
        return RunResult(label, {}, [], 5)

    session = MutationSession(TASK_ROOT, container=container, runner=runner)
    verdict = session.verdict("interceptor-removed")
    session.close()

    assert not verdict.ok
    assert container.installed == []
    assert container.restored == 0
