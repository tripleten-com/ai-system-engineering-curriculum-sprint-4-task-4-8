"""Coldline.

===================

File:              tests/security/grpc_harness.py
Component:         Security tooling — gRPC reading intake harness
Purpose:           Give the student tests, `poe grpc-call` and the assessed checks one gRPC
                    client, one bearer-token HTTP client, the token loader and the fresh-reading
                    helper, all against the running stack.
Interacts With:    src/api/grpc_contract.py (the generated code), the API's gRPC port and
                    GET /api/v1/exceptions/{exception_id}, tests/fixtures/grpc/readings.json,
                    tests/fixtures/tokens/fixtures.yaml, tests/security/trace.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          One client for every caller, fresh identities, evidence from executed calls
Tools:             Python 3.12, grpcio, httpx

Supplied, not student-editable. ``tests/student/test_grpc_ingest.py`` reaches everything here
through its ``ingest`` fixture, a ``GrpcIngestHarness``:

- ``ingest.submit(reading, token="<fixture>")``: the gRPC client. It encodes ``reading`` (the
  JSON form ``POST /api/v1/readings`` takes, as ``fresh_reading()`` returns it) as a
  ``GatewayReading`` with the code ``poe proto-gen`` generated, sends it to ``SubmitReading``
  on the gRPC port ``README.md`` publishes with the fixture's token in the ``authorization``
  metadata (no token for ``token=None``), and returns a ``CallResult``: ``status`` is the
  status name (``"OK"`` on success), ``details`` the status details, ``response`` the
  ``ReadingAccepted`` message or ``None`` when no response came back, and ``exception_id``
  the response's id or ``None``.
- ``ingest.api_client("<fixture>")``: an ``httpx.Client`` against the API that sends that
  fixture as a bearer token; ``dispatcher-valid`` is the one the summary endpoint admits.
- ``ingest.token("<fixture>")``: the token fixture loader.
- ``ingest.fresh_reading()``: the supplied reading with a new ``reading_id``, so it is a new
  exception.
- ``ingest.send_fresh_reading("<fixture>")``: the helper. It sends a fresh reading with that
  fixture's token, then reads the exception id the reading's identity gives through the
  summary endpoint as ``dispatcher-valid``, and returns a ``FreshReadingResult``: the same
  ``status``, ``details`` and ``response``, plus ``exception_id`` and
  ``exception_created`` (``True`` when the summary endpoint has a record for that reading).

When the assessed checks run the student file they set ``COLDLINE_ACCESS_TRACE``, and every
call and request made through the harness is recorded against the pytest case that made it
(``tests/security/trace.py``): which fixture each gRPC call carried and what status it got,
which exception id each summary read requested, and what each "was an exception created"
check found. That record, not the test's source text, is how the checks find the accepted-call
test and the refused-call test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import grpc
import httpx

from api.grpc_contract import METHOD, REQUEST, SERVICE, ContractUnavailable, GeneratedContract, load
from domain import exception_id_for
from domain.contracts import SensorReading
from tests.runtime_config import host_port
from tests.security import trace
from tests.security.fixtures import token as fixture_token

TASK_ROOT = Path(__file__).resolve().parents[2]
READINGS = Path("tests/fixtures/grpc/readings.json")
TOKENS = Path("tests/fixtures/tokens/fixtures.yaml")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
GRPC_PORT_VARIABLE = "COLDLINE_GRPC_HOST_PORT"
GRPC_DEFAULT_PORT = 50051
API_PORT_VARIABLE = "COLDLINE_API_HOST_PORT"
API_DEFAULT_PORT = 8000
CALL_TIMEOUT_SECONDS = 15.0
HTTP_TIMEOUT_SECONDS = 10.0
# The one fixture the summary endpoint admits: the helper reads with it.
SUMMARY_READER = "dispatcher-valid"
# The JSON form names the instant `recorded_at`; the Protobuf form carries it as milliseconds.
JSON_TIMESTAMP = "recorded_at"
WIRE_TIMESTAMP = "recorded_at_unix_ms"


class HarnessError(RuntimeError):
    """Report that a call could not be made at all, as opposed to a call that got a status."""


def readings_document(root: Path = TASK_ROOT) -> dict[str, Any]:
    """Return the supplied readings fixture."""
    document = json.loads((root / READINGS).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("reading"), dict):
        raise HarnessError(f"{READINGS.as_posix()} must hold a `reading` object")
    return document


def supplied_reading(root: Path = TASK_ROOT) -> dict[str, Any]:
    """Return the supplied reading exactly as the fixture holds it, keys in the fixture's order."""
    return dict(readings_document(root)["reading"])


def fresh_reading(root: Path = TASK_ROOT) -> dict[str, Any]:
    """Return the supplied reading with a new ``reading_id``, so it names a new exception."""
    reading = supplied_reading(root)
    reading["reading_id"] = f"reading-grpc-{uuid4().hex[:20]}"
    return reading


def malformed_reading(root: Path = TASK_ROOT) -> dict[str, Any]:
    """Return a fresh supplied reading without the fields ``malformed_omits`` names."""
    omitted = readings_document(root).get("malformed_omits", [])
    reading = fresh_reading(root)
    for name in omitted:
        reading.pop(str(name), None)
    return reading


def unix_ms(value: str) -> int:
    """Return an ISO 8601 instant with a zone as whole milliseconds since the Unix epoch."""
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise HarnessError(f"{JSON_TIMESTAMP} {value!r} names no time zone")
    return (instant - EPOCH) // timedelta(milliseconds=1)


def to_message(contract: GeneratedContract, reading: Mapping[str, Any]) -> Any:
    """Encode one JSON-form reading as a ``GatewayReading``, setting only the fields it carries.

    ``recorded_at`` becomes ``recorded_at_unix_ms``; every other key is the field of the same
    name. A key whose value is ``None`` is left unset, as an absent key is.
    """
    message_class = contract.message_class(REQUEST)
    if message_class is None:
        raise HarnessError(f"the generated code defines no {REQUEST} message: see Step 1")
    message = message_class()
    for name, value in reading.items():
        if value is None:
            continue
        field = WIRE_TIMESTAMP if name == JSON_TIMESTAMP else name
        try:
            setattr(message, field, unix_ms(value) if name == JSON_TIMESTAMP else value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise HarnessError(f"{REQUEST}.{field} cannot hold {value!r}: {exc}") from exc
    return message


def reading_exception_id(reading: Mapping[str, Any]) -> str:
    """Return the exception id the reading's identity gives, by the domain's own rule."""
    return exception_id_for(SensorReading.model_construct(reading_id=str(reading["reading_id"])))


def _status_name(error: grpc.RpcError) -> str:
    """Return the status name a failed call ended with."""
    code = getattr(error, "code", None)
    status = code() if callable(code) else None
    return str(getattr(status, "name", "UNKNOWN"))


def _details(error: grpc.RpcError) -> str:
    """Return the status details a failed call carried, or an empty string."""
    details = getattr(error, "details", None)
    value = details() if callable(details) else None
    return "" if value is None else str(value)


@dataclass(frozen=True)
class CallResult:
    """What one ``SubmitReading`` call ended with."""

    token: str | None
    status: str
    details: str
    response: Any | None

    @property
    def exception_id(self) -> str | None:
        """Return the response's exception id, or None when no response came back."""
        value = getattr(self.response, "exception_id", None)
        return str(value) if value else None

    @property
    def state(self) -> str | None:
        """Return the response's state, or None when no response came back."""
        value = getattr(self.response, "state", None)
        return str(value) if value else None

    @property
    def status_url(self) -> str | None:
        """Return the response's status URL, or None when no response came back."""
        value = getattr(self.response, "status_url", None)
        return str(value) if value else None


@dataclass(frozen=True)
class FreshReadingResult:
    """What the fresh-reading helper saw: the call, the reading's exception id, and the record."""

    call: CallResult
    reading_id: str
    exception_id: str
    exception_created: bool

    @property
    def status(self) -> str:
        """Return the call's status name."""
        return self.call.status

    @property
    def details(self) -> str:
        """Return the call's status details."""
        return self.call.details

    @property
    def response(self) -> Any | None:
        """Return the call's response message, or None when none came back."""
        return self.call.response


class GrpcIngestHarness:
    """The gRPC client, the HTTP client, the token loader and the fresh-reading helper."""

    def __init__(self, root: Path = TASK_ROOT, *, trace_path: Path | None = None) -> None:
        """Bind the harness to one checkout; the trace defaults to ``COLDLINE_ACCESS_TRACE``."""
        self.root = root
        self._trace = trace.trace_path() if trace_path is None else trace_path
        self._contract: GeneratedContract | None = None

    @property
    def grpc_target(self) -> str:
        """Return the host address of the API's gRPC port."""
        port = host_port(GRPC_PORT_VARIABLE, GRPC_DEFAULT_PORT, root=self.root)
        return f"127.0.0.1:{port}"

    @property
    def api_url(self) -> str:
        """Return the host origin of the API's HTTP port."""
        return f"http://localhost:{host_port(API_PORT_VARIABLE, API_DEFAULT_PORT, root=self.root)}"

    def token(self, name: str) -> str:
        """Return one token fixture's compact token by its name."""
        return fixture_token(name, path=self.root / TOKENS)

    def contract(self) -> GeneratedContract:
        """Return the generated contract code, or raise saying what is missing."""
        if self._contract is None:
            try:
                contract = load(self.root)
            except ContractUnavailable as exc:
                raise HarnessError(str(exc)) from exc
            missing = contract.missing()
            if missing:
                raise HarnessError(
                    f"the generated code does not define {', '.join(missing)}: see Step 1, "
                    "then run `poe proto-gen`"
                )
            self._contract = contract
        return self._contract

    def supplied_reading(self) -> dict[str, Any]:
        """Return the supplied reading with its own fixed ``reading_id``."""
        return supplied_reading(self.root)

    def fresh_reading(self) -> dict[str, Any]:
        """Return the supplied reading with a new ``reading_id``."""
        return fresh_reading(self.root)

    def malformed_reading(self) -> dict[str, Any]:
        """Return a fresh supplied reading without its temperature."""
        return malformed_reading(self.root)

    def submit(self, reading: Mapping[str, Any], token: str | None) -> CallResult:
        """Send one reading to ``SubmitReading`` with one fixture's token; return the outcome."""
        return self._call(reading, token, helper=False)

    def send_fresh_reading(self, token: str) -> FreshReadingResult:
        """Send a fresh reading with ``token``, then report whether an exception was created.

        The exception id is the one the reading's identity gives, so the check works whether
        or not a response came back.
        """
        reading = self.fresh_reading()
        expected = reading_exception_id(reading)
        call = self._call(reading, token, helper=True)
        created = self.exception_exists(expected)
        return FreshReadingResult(call, str(reading["reading_id"]), expected, created)

    def api_client(self, token: str | None = SUMMARY_READER) -> httpx.Client:
        """Return an HTTP client against the API that sends ``token`` as a bearer token."""
        headers = {} if token is None else {"Authorization": f"Bearer {self.token(token)}"}

        def record_response(response: httpx.Response) -> None:
            """Record one request and its status against the pytest case now running."""
            request = response.request
            trace.record(
                self._trace,
                {
                    "kind": "http",
                    "fixture": token,
                    "method": request.method,
                    "path": request.url.path,
                    "exception_id": trace.exception_id_of(request.url.path),
                    "status_code": response.status_code,
                },
            )

        return httpx.Client(
            base_url=self.api_url,
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
            event_hooks={"response": [record_response]},
        )

    def exception_exists(self, exception_id: str) -> bool:
        """Return whether the summary endpoint has a record for ``exception_id``."""
        headers = {"Authorization": f"Bearer {self.token(SUMMARY_READER)}"}
        with httpx.Client(
            base_url=self.api_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS
        ) as client:
            response = client.get(f"/api/v1/exceptions/{exception_id}")
        if response.status_code not in (200, 404):
            raise HarnessError(
                f"GET /api/v1/exceptions/{exception_id} as {SUMMARY_READER} answered "
                f"{response.status_code}: {response.text[:200]}"
            )
        created = response.status_code == 200
        trace.record(
            self._trace, {"kind": "created", "exception_id": exception_id, "created": created}
        )
        return created

    def _call(self, reading: Mapping[str, Any], token: str | None, *, helper: bool) -> CallResult:
        """Make one ``SubmitReading`` call and record it."""
        contract = self.contract()
        message = to_message(contract, reading)
        metadata = [] if token is None else [("authorization", f"Bearer {self.token(token)}")]
        stub_class = getattr(contract.services, f"{SERVICE}Stub")
        with grpc.insecure_channel(self.grpc_target) as channel:
            method = getattr(stub_class(channel), METHOD)
            try:
                response = method(message, metadata=metadata, timeout=CALL_TIMEOUT_SECONDS)
            except grpc.RpcError as exc:
                result = CallResult(token, _status_name(exc), _details(exc), None)
            else:
                result = CallResult(token, "OK", "", response)
        trace.record(
            self._trace,
            {
                "kind": "grpc",
                "fixture": token,
                "status": result.status,
                "exception_id": result.exception_id,
                "reading_exception_id": reading_exception_id(reading),
                "helper": helper,
            },
        )
        return result
