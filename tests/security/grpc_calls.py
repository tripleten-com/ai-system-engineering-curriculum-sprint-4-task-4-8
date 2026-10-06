"""Coldline.

===================

File:              tests/security/grpc_calls.py
Component:         Security tooling — gRPC reading calls
Purpose:           Send one supplied reading to the gRPC method with a named token fixture, or the
                    malformed one, and print the status (`poe grpc-call`); make the four calls
                    `poe verify` compares the answer sheet with.
Interacts With:    tests/security/grpc_harness.py, tests/fixtures/grpc/readings.json,
                    tests/fixtures/tokens/fixtures.yaml, the API's gRPC port,
                    tests/contract/test_grpc_ingest_contract.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          One accepted and one refused call, status names as evidence
Tools:             Python 3.12, grpcio

Supplied, not student-editable. ``poe grpc-call --token <fixture>`` sends the supplied reading
(``tests/fixtures/grpc/readings.json``) with a fresh ``reading_id`` each run, so each accepted
run is a new exception, with that fixture's token in the ``authorization`` metadata.
``--reading malformed`` sends the same reading without ``temperature_c``. It prints the
status name, then either the response's exception id, state and status URL, or the status
details. It exits 0 whenever the call ended with a status, whatever the status was, and 2
when the call could not be made (no generated code, an unknown fixture).

``verify_calls`` makes the four calls ``poe verify`` makes: ``gateway-valid``,
``dispatcher-valid`` and ``expired`` with the supplied reading, and the malformed reading with
``gateway-valid``, so that only the reading, not the token, can refuse it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.security.fixtures import FIXTURE_NAMES
from tests.security.grpc_harness import CallResult, GrpcIngestHarness, HarnessError

TASK_ROOT = Path(__file__).resolve().parents[2]
READING_KINDS = ("supplied", "malformed")
# The four calls `poe verify` makes, by label: (token fixture, reading kind).
VERIFY_CALLS: dict[str, tuple[str, str]] = {
    "gateway-valid": ("gateway-valid", "supplied"),
    "dispatcher-valid": ("dispatcher-valid", "supplied"),
    "expired": ("expired", "supplied"),
    "malformed": ("gateway-valid", "malformed"),
}


@dataclass(frozen=True)
class VerifyCall:
    """One of the four calls: its token, its reading, and its outcome or why it was not made."""

    label: str
    token: str
    reading: str
    result: CallResult | None
    error: str | None = None

    @property
    def status(self) -> str | None:
        """Return the status name, or None when the call could not be made."""
        return None if self.result is None else self.result.status

    def describe(self) -> str:
        """Return one line naming the call and its outcome."""
        sent = f"{self.reading} reading with {self.token}"
        if self.result is None:
            return f"{self.label}: not made ({sent}): {self.error}"
        detail = self.result.exception_id or self.result.details or "no details"
        return f"{self.label}: {self.result.status} ({sent}; {detail})"


def call(token: str, reading: str = "supplied", *, root: Path = TASK_ROOT) -> CallResult:
    """Send one fresh supplied or malformed reading with ``token``; return what it ended with."""
    harness = GrpcIngestHarness(root)
    body = harness.malformed_reading() if reading == "malformed" else harness.fresh_reading()
    return harness.submit(body, token)


def verify_calls(root: Path = TASK_ROOT) -> dict[str, VerifyCall]:
    """Make the four calls `poe verify` makes; a call that cannot be made records why."""
    calls: dict[str, VerifyCall] = {}
    for label, (token, reading) in VERIFY_CALLS.items():
        try:
            result = call(token, reading, root=root)
        except HarnessError as exc:
            calls[label] = VerifyCall(label, token, reading, None, str(exc))
        else:
            calls[label] = VerifyCall(label, token, reading, result)
    return calls


def main(argv: list[str] | None = None) -> int:
    """Send one reading and print its status; exit 2 when the call cannot be made."""
    parser = argparse.ArgumentParser(description="Send one supplied reading to SubmitReading.")
    parser.add_argument("--token", required=True, choices=FIXTURE_NAMES, help="token fixture")
    parser.add_argument(
        "--reading",
        choices=READING_KINDS,
        default="supplied",
        help="the supplied reading (default), or the same reading without temperature_c",
    )
    arguments = parser.parse_args(argv)
    try:
        result = call(arguments.token, arguments.reading)
    except HarnessError as exc:
        print(f"grpc-call: the call was not made: {exc}", file=sys.stderr)
        return 2
    print(f"grpc-call: SubmitReading, {arguments.reading} reading, token {arguments.token}")
    print(f"status: {result.status}")
    if result.response is not None:
        print(f"exception_id: {result.exception_id}")
        print(f"state: {result.state}")
        print(f"status_url: {result.status_url}")
    else:
        print(f"details: {result.details or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
