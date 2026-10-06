"""Coldline.

===================

File:              tests/security/payload_size.py
Component:         Security tooling — Payload size comparison
Purpose:           Encode the one supplied reading as JSON and as Protobuf and print both sizes in
                    bytes (`poe payload-size`).
Interacts With:    tests/fixtures/grpc/readings.json, src/api/grpc_contract.py (the generated
                    code), tests/security/proto_check.py, tests/security/grpc_harness.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Text versus binary encoding, measuring what the claim is about
Tools:             Python 3.12, json, Protobuf

Supplied, not student-editable. The reading is the ``reading`` object of
``tests/fixtures/grpc/readings.json``, exactly as written there, with its own ``reading_id``.

- **JSON** is that object as the body of ``POST /api/v1/readings``, encoded by one fixed
  serializer: Python's ``json.dumps`` with the compact separators ``","`` and ``":"`` (no
  whitespace anywhere), the keys in the fixture's own order, ``ensure_ascii=False``, then
  UTF-8. The size is the number of bytes of that text.
- **Protobuf** is the same reading as a ``GatewayReading``, built with the code
  ``poe proto-gen`` generated from your ``.proto`` and the contract's mapping (``recorded_at``
  becomes ``recorded_at_unix_ms``; every other key sets the field of the same name), then
  serialized. The size is the length of the serialized bytes.

Both sizes measure the encoded reading only: no request metadata, no token, no HTTP or HTTP/2
framing, no TLS. The command first runs the contract check of ``poe proto-check`` and the
current-code check of the generated-code loader, and prints no size until both pass: the
Protobuf size depends on the field numbers and types, and only a message that matches the
contract gives the size this Task asks about.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from api.grpc_contract import REQUEST, GeneratedContract
from tests.security import proto_check
from tests.security.grpc_harness import (
    READINGS,
    GrpcIngestHarness,
    HarnessError,
    supplied_reading,
    to_message,
)

TASK_ROOT = Path(__file__).resolve().parents[2]


def json_bytes(reading: Mapping[str, Any]) -> int:
    """Return the size of ``reading`` as compact UTF-8 JSON, keys in their given order."""
    text = json.dumps(dict(reading), separators=(",", ":"), ensure_ascii=False)
    return len(text.encode("utf-8"))


def protobuf_bytes(contract: GeneratedContract, reading: Mapping[str, Any]) -> int:
    """Return the size of ``reading`` serialized as the contract's request message."""
    return len(to_message(contract, reading).SerializeToString())


def main(argv: list[str] | None = None) -> int:
    """Print both sizes, or why they cannot be measured yet (exit 1)."""
    del argv
    try:
        report = proto_check.check(TASK_ROOT)
    except proto_check.ContractDocumentError as exc:
        print(f"payload-size: {exc}", file=sys.stderr)
        return 2
    if report.failures:
        print(
            "payload-size: not measured: your .proto does not match the contract yet. "
            "Run `poe proto-check` for the items that differ.",
            file=sys.stderr,
        )
        return 1
    try:
        contract = GrpcIngestHarness(TASK_ROOT).contract()
        reading = supplied_reading(TASK_ROOT)
        encoded_json = json_bytes(reading)
        encoded_protobuf = protobuf_bytes(contract, reading)
    except HarnessError as exc:
        print(f"payload-size: not measured: {exc}", file=sys.stderr)
        return 1
    print(f"payload-size: the supplied reading in {READINGS.as_posix()}, encoded two ways")
    print(f"  JSON       {encoded_json:>5} bytes  (UTF-8, compact separators, fixture order)")
    print(f"  Protobuf   {encoded_protobuf:>5} bytes  ({REQUEST}, from your generated code)")
    print(
        "Both sizes count the encoded reading only: no request metadata, no token, no HTTP or "
        "HTTP/2 framing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
