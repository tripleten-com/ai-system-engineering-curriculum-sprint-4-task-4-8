"""Coldline.

===================

File:              tests/student/test_grpc_ingest.py
Component:         Student tests — gRPC reading intake
Purpose:           Your two tests for the gRPC reading method: one accepted call and one refused
                    call.
Interacts With:    tests/security/grpc_harness.py, the API's gRPC port and
                    GET /api/v1/exceptions/{exception_id}, tests/fixtures/tokens/fixtures.yaml
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Tests that can fail, exact status assertions, proving a refusal had no effect
Tools:             Python 3.12, pytest, grpcio, httpx

This file is yours. Write the two tests Step 4 asks for in the two marked places below. They
call the running API, so start the stack first, and run `poe restart-api` after an edit to
`src/api/grpc_ingest.py` or the `.proto` so the API serves what you wrote. `poe student-tests`
runs them beside the carried tests.

The `ingest` fixture gives you the supplied pieces (`tests/security/grpc_harness.py`):

- `ingest.submit(reading, token="<fixture>")`: the gRPC client. It sends one reading to
  `SubmitReading` with that token fixture in the `authorization` metadata and returns a
  result with `status` (the status name, "OK" on success), `details`, `response` (the
  `ReadingAccepted` message, or None when no response came back) and `exception_id`.
- `ingest.fresh_reading()`: the supplied reading with a new `reading_id`, so every call
  with it is a new exception.
- `ingest.api_client("<fixture>")`: an HTTP client against the API that sends that fixture
  as a bearer token; use it as `with ingest.api_client("dispatcher-valid") as client:` and
  read `client.get(f"/api/v1/exceptions/{exception_id}")`.
- `ingest.token("<fixture>")`: the token fixture loader.
- `ingest.send_fresh_reading("<fixture>")`: the helper. It sends a fresh reading with that
  fixture's token and then reports whether an exception was created for it: the result has
  `status`, `details` and `response` as above, plus `exception_id` (the id the reading's
  identity gives) and `exception_created` (True or False).

`poe verify` runs this file with the interceptor in place, and again against the API with
the supplied interceptor replaced inside its container: once admitting every call, once with
UNAUTHENTICATED and PERMISSION_DENIED swapped, once refusing a call only after the method has
run, and once answering an accepted call with another exception id. Your refused-call test
must fail under the first three and your accepted-call test under the last, which is what
asserting the exact status, the missing response, the missing exception and the id read back
through the summary endpoint gives you.
"""

import pytest

from tests.security.grpc_harness import GrpcIngestHarness


@pytest.fixture
def ingest() -> GrpcIngestHarness:
    """Return the gRPC client, the HTTP client, the token loader and the fresh-reading helper."""
    return GrpcIngestHarness()


# --- Test 1 of 2: the accepted call.
# Send a fresh reading with the `gateway-valid` token. Assert that the call ends OK with an
# exception id, then read that id through GET /api/v1/exceptions/{exception_id} with the
# `dispatcher-valid` token and assert that the summary endpoint returns it.


# --- Test 2 of 2: the refused call.
# Send a fresh reading with the `dispatcher-valid` token through `ingest.send_fresh_reading`.
# Assert the exact status, that no response message came back, and that the helper reports
# no exception created for that reading.
