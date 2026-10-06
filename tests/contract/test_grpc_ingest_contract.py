"""Coldline.

===================

File:              tests/contract/test_grpc_ingest_contract.py
Component:         Contract tests — gRPC reading intake (Add-On Task 4.8)
Purpose:           One assessed check per automatable Check-list row: the .proto against the
                    contract, the four calls, the shared acceptance path, the recorded answers,
                    and the student's two tests with the interceptor in place and mutated.
Interacts With:    tests/security/proto_check.py, tests/security/grpc_calls.py,
                    tests/security/grpc_harness.py, tests/security/grpc_mutation.py,
                    tests/contract/submission_validation.py, the running stack
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          A contract checked from both sides, statuses as evidence, tests that can fail
Tools:             Python 3.12, pytest, grpcio, httpx

``poe grpc-contract`` runs this module inside ``poe verify``, after ``poe proto-gen`` and
``poe proto-check``, in the order the rows appear: the two schema rows, the four calls
(``gateway-valid``, ``dispatcher-valid`` and ``expired`` with the supplied reading, and the
malformed reading with ``gateway-valid``), the readable-record and shared-path rows, the
answer-sheet rows, which compare the two recorded statuses with the calls this run made, and
last the rows about your own tests, which run ``tests/student/test_grpc_ingest.py`` as written
and then once under each of four interceptor mutations installed inside the API container
(``tests/security/grpc_mutation.py``). The container is recreated from its image after the
last mutation row, so the steps after this one see the supplied interceptor again. A fresh
starter fails every row here. The protected answer check of the two sizes and the open gaps is
not here: it runs on the platform after you submit.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.contract.submission_validation import (
    OPEN_GAP_OPTIONS,
    RECOMMENDATIONS,
    SubmissionError,
    _load_one_document,
)
from tests.security import grpc_calls, grpc_mutation, proto_check
from tests.security.fixtures import bearer_headers
from tests.security.grpc_harness import (
    GrpcIngestHarness,
    HarnessError,
    reading_exception_id,
)

pytestmark = pytest.mark.assessed

TASK_ROOT = Path(__file__).resolve().parents[2]
READER = "dispatcher-valid"
FINISHED_STATES = frozenset({"COMPLETED", "NEEDS_REVIEW"})
FINISH_TIMEOUT_SECONDS = 60.0
# The other fields `docs/contracts/ingest-grpc.md` checks for presence, by their JSON-form
# names (`recorded_at` is sent as `recorded_at_unix_ms`). The `poe verify` malformed call
# omits `temperature_c`; the malformed row sends one more reading without each of these.
OTHER_PRESENCE_CHECKED = ("allowed_min_c", "allowed_max_c", "recorded_at")


@pytest.fixture(scope="module")
def calls() -> dict[str, grpc_calls.VerifyCall]:
    """Make the four calls of `poe verify` once for the module; a call not made records why."""
    return grpc_calls.verify_calls(TASK_ROOT)


@pytest.fixture(scope="module")
def mutations() -> Iterator[grpc_mutation.MutationSession]:
    """Share one inventory and one run per mutation; restore the API container afterwards."""
    session = grpc_mutation.MutationSession(TASK_ROOT)
    yield session
    session.close()


def _answers() -> dict[str, Any]:
    """Return the answer sheet's answers mapping, or an empty mapping when it cannot be read."""
    try:
        document = _load_one_document(TASK_ROOT / "submission.yaml")
    except SubmissionError:
        return {}
    answers = document.get("answers")
    return answers if isinstance(answers, dict) else {}


def _api_url() -> str:
    """Return the API's host origin."""
    return GrpcIngestHarness(TASK_ROOT, trace_path=None).api_url


def _read(exception_id: str) -> httpx.Response:
    """Read one exception through the summary endpoint as the dispatcher."""
    return httpx.get(
        f"{_api_url()}/api/v1/exceptions/{exception_id}",
        headers=bearer_headers(READER),
        timeout=10.0,
    )


def _instant(value: object) -> datetime | None:
    """Return an ISO 8601 instant as an aware datetime, or None for anything else."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _verdict_message(verdict: grpc_mutation.Verdict) -> str:
    """Return a mutation verdict's problems as one message."""
    return f"{verdict.mutation}: " + "; ".join(verdict.problems)


# --- Step 1: the schema ---------------------------------------------------------------------


def test_the_proto_declares_the_contract_names_fields_numbers_and_presence() -> None:
    """The .proto defines the message, the response and the one method the contract names."""
    report = proto_check.check(TASK_ROOT)

    assert not report.failures, "\n".join(item.render() for item in report.failures)


def test_proto_gen_and_proto_check_pass() -> None:
    """`poe proto-gen` generates the code and `poe proto-check` reports every item matching."""
    for module in ("api.grpc_codegen", "tests.security.proto_check"):
        completed = subprocess.run(
            [sys.executable, "-m", module],
            cwd=TASK_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        output = (completed.stdout + completed.stderr).strip()
        assert completed.returncode == 0, (
            f"python -m {module} exited {completed.returncode}:\n{output}"
        )


# --- Steps 2 and 3: the four calls ------------------------------------------------------------


@pytest.mark.runtime
def test_a_malformed_reading_is_refused_with_invalid_argument(
    calls: dict[str, grpc_calls.VerifyCall],
) -> None:
    """A reading missing any presence-checked field, sent as gateway-valid, ends INVALID_ARGUMENT.

    The `poe verify` malformed call omits temperature_c. A fresh reading without each other
    field the contract checks for presence is sent the same way, so checking one field is not
    enough.
    """
    call = calls["malformed"]

    assert call.status == "INVALID_ARGUMENT", call.describe()
    harness = GrpcIngestHarness(TASK_ROOT)
    for name in OTHER_PRESENCE_CHECKED:
        reading = harness.fresh_reading()
        reading.pop(name, None)
        try:
            result = harness.submit(reading, "gateway-valid")
        except HarnessError as exc:
            pytest.fail(f"the reading without {name} was not sent: {exc}")
        assert result.status == "INVALID_ARGUMENT", (
            f"a reading without {name}, sent as gateway-valid, ended {result.status}, "
            "not INVALID_ARGUMENT: check the presence of every field the contract marks"
        )


@pytest.mark.runtime
def test_gateway_valid_is_accepted_with_ok(calls: dict[str, grpc_calls.VerifyCall]) -> None:
    """The supplied reading sent as gateway-valid ends OK with an exception id."""
    call = calls["gateway-valid"]

    assert call.status == "OK", call.describe()
    assert call.result is not None and call.result.exception_id, call.describe()


@pytest.mark.runtime
def test_dispatcher_valid_is_refused_with_permission_denied(
    calls: dict[str, grpc_calls.VerifyCall],
) -> None:
    """A dispatcher's valid token is refused PERMISSION_DENIED, with no response message."""
    call = calls["dispatcher-valid"]

    assert call.status == "PERMISSION_DENIED", call.describe()
    assert call.result is not None and call.result.response is None, call.describe()


@pytest.mark.runtime
def test_expired_is_refused_with_unauthenticated(calls: dict[str, grpc_calls.VerifyCall]) -> None:
    """An expired token is refused UNAUTHENTICATED, with no response message."""
    call = calls["expired"]

    assert call.status == "UNAUTHENTICATED", call.describe()
    assert call.result is not None and call.result.response is None, call.describe()


@pytest.mark.runtime
def test_a_reading_accepted_through_grpc_is_readable_through_the_summary_endpoint(
    calls: dict[str, grpc_calls.VerifyCall],
) -> None:
    """The exception id the gateway-valid call returned reads back as the dispatcher."""
    call = calls["gateway-valid"]
    exception_id = None if call.result is None else call.result.exception_id
    assert exception_id, f"no exception id to read: {call.describe()}"

    response = _read(exception_id)

    assert response.status_code == 200, f"GET {exception_id} answered {response.status_code}"
    assert response.json()["exception_id"] == exception_id


@pytest.mark.runtime
def test_the_method_converts_the_reading_and_uses_the_json_intakes_acceptance_path() -> None:
    """A gRPC reading becomes the same SensorReading and the same record the JSON intake makes.

    The record's id follows the reading's identity; the JSON intake, sent the same reading
    afterwards, answers with that same record rather than a second one; the stored reading
    carries every field the contract maps; and the worker takes it to a finished state, so it
    went through the queue like any reading the JSON intake accepts.
    """
    harness = GrpcIngestHarness(TASK_ROOT, trace_path=None)
    reading = harness.fresh_reading()
    try:
        result = harness.submit(reading, "gateway-valid")
    except HarnessError as exc:
        pytest.fail(f"the call was not made: {exc}")
    expected = reading_exception_id(reading)
    assert result.status == "OK", f"the call ended {result.status}: {result.details}"
    assert result.exception_id == expected, (
        f"the method answered {result.exception_id}; the reading's identity gives {expected}"
    )

    replay = httpx.post(f"{_api_url()}/api/v1/readings", json=reading, timeout=10.0)
    assert replay.status_code == 202, f"the JSON intake answered {replay.status_code}"
    assert replay.json()["exception_id"] == expected, "the JSON intake made a second record"

    record: dict[str, Any] = {}
    deadline = time.monotonic() + FINISH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = _read(expected)
        assert response.status_code == 200, f"GET {expected} answered {response.status_code}"
        record = response.json()
        if record["state"] in FINISHED_STATES or record["state"] == "FAILED":
            break
        time.sleep(1.0)
    stored = record.get("reading", {})
    for name in ("reading_id", "shipment_id", "temperature_c", "allowed_min_c", "allowed_max_c"):
        assert stored.get(name) == reading[name], f"the stored {name} is {stored.get(name)!r}"
    assert _instant(stored.get("recorded_at")) == _instant(reading["recorded_at"]), (
        f"the stored recorded_at is {stored.get('recorded_at')!r}"
    )
    assert stored.get("handling_note") == reading["handling_note"], "the note was not carried"
    assert stored.get("context") == "", "an unset context must map to the model's default"
    assert stored.get("emulator_response") is None, "the contract does not carry the selector"
    assert record.get("state") in FINISHED_STATES, (
        f"the record is {record.get('state')}, not finished by the worker within "
        f"{FINISH_TIMEOUT_SECONDS:.0f}s: did it reach the queue?"
    )


# --- Step 4: the answer sheet ---------------------------------------------------------------


@pytest.mark.runtime
def test_answers_record_the_statuses_of_the_calls_this_run_made(
    calls: dict[str, grpc_calls.VerifyCall],
) -> None:
    """accepted_status is the gateway-valid call's status and refused_status the dispatcher's."""
    answers = _answers()
    accepted, refused = calls["gateway-valid"], calls["dispatcher-valid"]
    assert accepted.status is not None, accepted.describe()
    assert refused.status is not None, refused.describe()

    assert answers.get("accepted_status") == accepted.status, (
        f"answers.accepted_status is {answers.get('accepted_status')!r}; this run's "
        f"gateway-valid call ended {accepted.status}"
    )
    assert answers.get("refused_status") == refused.status, (
        f"answers.refused_status is {answers.get('refused_status')!r}; this run's "
        f"dispatcher-valid call ended {refused.status}"
    )


def test_recommendation_is_one_of_the_allowed_values() -> None:
    """answers.recommendation is one of the values submission.yaml lists; any of them passes."""
    value = _answers().get("recommendation")

    assert value in RECOMMENDATIONS, f"answers.recommendation is {value!r}"


def test_sizes_and_open_gaps_use_the_allowed_format() -> None:
    """The two sizes are whole numbers of bytes and open_gaps lists only the sheet's options."""
    answers = _answers()
    for name in ("json_bytes", "protobuf_bytes"):
        value = answers.get(name)
        assert isinstance(value, int) and not isinstance(value, bool) and value > 0, (
            f"answers.{name} is {value!r}, not a positive whole number of bytes"
        )
    gaps = answers.get("open_gaps")
    assert isinstance(gaps, list) and gaps, "answers.open_gaps must list at least one option"
    unknown = [gap for gap in gaps if gap not in OPEN_GAP_OPTIONS]
    assert not unknown, f"answers.open_gaps names options submission.yaml does not list: {unknown}"
    assert len(set(gaps)) == len(gaps), "answers.open_gaps names an option twice"


# --- Step 4: your tests, with the interceptor in place and mutated --------------------------


@pytest.mark.runtime
def test_your_accepted_and_refused_call_tests_pass_with_the_interceptor_in_place(
    mutations: grpc_mutation.MutationSession,
) -> None:
    """Your file has an accepted-call test and a refused-call test, and both pass as written."""
    try:
        inventory = mutations.inventory()
    except grpc_mutation.MutationError as exc:
        pytest.fail(f"your tests could not be run: {exc}")
    problems = inventory.problems("accepted") + inventory.problems("refused")

    assert not problems, "\n".join(problems) + "\n\n" + inventory.describe()


@pytest.mark.runtime
def test_the_accepted_call_test_verifies_the_returned_id_through_the_summary_endpoint(
    mutations: grpc_mutation.MutationSession,
) -> None:
    """With the server answering another exception id, your accepted-call test fails."""
    verdict = mutations.verdict("identity-swapped")

    assert verdict.ok, _verdict_message(verdict)


@pytest.mark.runtime
def test_the_refused_call_test_verifies_permission_denied_and_that_no_exception_was_created(
    mutations: grpc_mutation.MutationSession,
) -> None:
    """With the statuses swapped, or the refusal after the method ran, your refused test fails."""
    verdicts = [mutations.verdict("status-swapped"), mutations.verdict("refusal-after-acceptance")]
    problems = [_verdict_message(verdict) for verdict in verdicts if not verdict.ok]

    assert not problems, "\n".join(problems)


@pytest.mark.runtime
def test_the_refused_call_test_fails_when_the_interceptor_is_removed(
    mutations: grpc_mutation.MutationSession,
) -> None:
    """With an interceptor that admits every call, your refused-call test fails."""
    verdict = mutations.verdict("interceptor-removed")

    assert verdict.ok, _verdict_message(verdict)
