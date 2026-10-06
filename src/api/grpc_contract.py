"""Coldline.

===================

File:              src/api/grpc_contract.py
Component:         API — gRPC contract names and generated-code loader
Purpose:           Name the gRPC reading intake's package, service, method and messages, and
                    load the Python code `poe proto-gen` generates from the .proto file.
Interacts With:    proto/coldline/ingest/v1/ingest.proto, src/api/grpc_codegen.py,
                    src/api/grpc_ingest.py, docs/contracts/ingest-grpc.md, tests/security/
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          One name per contract item, generated code kept out of the repository
Tools:             Python 3.12, importlib, Protobuf

Supplied, not student-editable. The names below are the ones ``docs/contracts/ingest-grpc.md``
gives; the server scaffold, the test harness, ``poe grpc-call`` and ``poe payload-size`` all
read them from here.

``poe proto-gen`` writes the generated modules under ``.generated/grpc/`` at the repository
root, a Git-ignored directory, together with the SHA-256 digest of the ``.proto`` file it
compiled. ``load`` imports them from there and refuses generated code whose digest is not the
current ``.proto``'s, so an edited schema is never served or measured through code generated
from an older one. In the API image the same directory is generated when the image is built,
from the ``.proto`` the image was built from.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

TASK_ROOT = Path(__file__).resolve().parents[2]
PROTO_DIRECTORY = "proto"
PROTO_FILE = "coldline/ingest/v1/ingest.proto"
GENERATED_DIRECTORY = ".generated/grpc"
# The digest of the .proto the generated code came from, written beside the code.
STAMP_NAME = "ingest.proto.sha256"
PACKAGE = "coldline.ingest.v1"
SERVICE = "ReadingIngest"
METHOD = "SubmitReading"
REQUEST = "GatewayReading"
RESPONSE = "ReadingAccepted"
FULL_METHOD = f"/{PACKAGE}.{SERVICE}/{METHOD}"
MESSAGES_MODULE = "coldline.ingest.v1.ingest_pb2"
SERVICES_MODULE = "coldline.ingest.v1.ingest_pb2_grpc"


class ContractUnavailable(RuntimeError):
    """Report why the generated contract code cannot be used yet, in words a student can act on."""


def proto_path(root: Path = TASK_ROOT) -> Path:
    """Return the .proto file the contract is written in."""
    return root / PROTO_DIRECTORY / PROTO_FILE


def generated_root(root: Path = TASK_ROOT) -> Path:
    """Return the ignored directory the generated modules live under."""
    return root / GENERATED_DIRECTORY


def proto_digest(root: Path = TASK_ROOT) -> str:
    """Return the SHA-256 hex digest of the current .proto file's bytes."""
    return hashlib.sha256(proto_path(root).read_bytes()).hexdigest()


@dataclass(frozen=True)
class GeneratedContract:
    """Hold the two generated modules: the messages and the service stubs."""

    messages: ModuleType
    services: ModuleType

    def message_class(self, name: str) -> Any | None:
        """Return the generated message class called ``name``, or None when it is not defined."""
        return getattr(self.messages, name, None)

    def missing(self) -> list[str]:
        """Return the contract items the generated code does not define, in contract order.

        An empty list means the request message, the response message, the service and the
        method all exist under the contract's names. Whether their fields match the contract
        is ``poe proto-check``'s question, not this one.
        """
        missing: list[str] = []
        for name in (REQUEST, RESPONSE):
            if self.message_class(name) is None:
                missing.append(f"message {name}")
        file_descriptor = getattr(self.messages, "DESCRIPTOR", None)
        services = getattr(file_descriptor, "services_by_name", {})
        service = services.get(SERVICE)
        if service is None:
            missing.append(f"service {SERVICE}")
        elif service.methods_by_name.get(METHOD) is None:
            missing.append(f"method {SERVICE}.{METHOD}")
        return missing


_LOADED: dict[Path, GeneratedContract] = {}


def load(root: Path = TASK_ROOT) -> GeneratedContract:
    """Import the generated modules for ``root``, or raise ``ContractUnavailable`` saying why.

    A process imports one generated contract: Protobuf registers every generated file in one
    process-wide pool, so code generated twice cannot be imported side by side.
    """
    generated = generated_root(root)
    stamp = generated / STAMP_NAME
    module_file = generated / "coldline/ingest/v1/ingest_pb2.py"
    if not module_file.is_file() or not stamp.is_file():
        raise ContractUnavailable(
            f"no generated code under {GENERATED_DIRECTORY}/: run `poe proto-gen` after "
            f"editing {PROTO_DIRECTORY}/{PROTO_FILE}"
        )
    if stamp.read_text(encoding="utf-8").strip() != proto_digest(root):
        raise ContractUnavailable(
            f"the generated code under {GENERATED_DIRECTORY}/ was generated from an earlier "
            f"version of {PROTO_DIRECTORY}/{PROTO_FILE}: run `poe proto-gen` again"
        )
    key = generated.resolve()
    if key in _LOADED:
        return _LOADED[key]
    if _LOADED:
        raise ContractUnavailable("another generated contract is already loaded in this process")
    location = str(generated)
    if location not in sys.path:
        sys.path.insert(0, location)
    try:
        messages = importlib.import_module(MESSAGES_MODULE)
        services = importlib.import_module(SERVICES_MODULE)
    except Exception as exc:  # generated code that does not import is reported, not raised raw
        raise ContractUnavailable(f"the generated code could not be imported: {exc}") from exc
    contract = GeneratedContract(messages=messages, services=services)
    _LOADED[key] = contract
    return contract
