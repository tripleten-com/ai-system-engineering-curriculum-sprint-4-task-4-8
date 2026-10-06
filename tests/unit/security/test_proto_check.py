"""Coldline.

===================

File:              tests/unit/security/test_proto_check.py
Component:         Unit tests — Protobuf contract check and code generation
Purpose:           Prove that `poe proto-check` names every kind of mismatch, that `poe proto-gen`
                    writes the generated code and its stamp, and that the loader refuses missing
                    or stale code.
Interacts With:    tests/security/proto_check.py, src/api/grpc_codegen.py,
                    src/api/grpc_contract.py, docs/contracts/ingest-grpc.md
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          A schema checked against its written contract, generated code kept current
Tools:             Python 3.12, pytest, grpcio-tools

Every compiled schema here is an invented one, in a temporary Task root: a probe message
with three fields, a one-field acknowledgement and one method, described by its own small
contract document. Nothing here compiles, reads or states the reading intake's own schema,
so these tests neither depend on nor reveal the student's .proto.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from api import grpc_codegen, grpc_contract
from tests.security import proto_check

TASK_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = """# Probe contract

## Names

| Item | Name |
|---|---|
| Package | `example.probe.v1` |
| Service | `ProbeIngest` |
| Method | `Send` |
| Request message | `ProbeReading` |
| Response message | `ProbeAck` |

## Request message fields

| Field | Number | Type | Presence | Meaning |
|---|---|---|---|---|
| `label` | 1 | `string` | implicit | a name |
| `level` | 2 | `double` | optional | a level |
| `seen_at_ms` | 3 | `int64` | optional | an instant |

## Response message fields

| Field | Number | Type | Presence | Value |
|---|---|---|---|---|
| `ack_id` | 1 | `string` | implicit | an id |
"""
MATCHING = """syntax = "proto3";

package example.probe.v1;

message ProbeReading {
  string label = 1;
  optional double level = 2;
  optional int64 seen_at_ms = 3;
}

message ProbeAck {
  string ack_id = 1;
}

service ProbeIngest {
  rpc Send(ProbeReading) returns (ProbeAck);
}
"""


def _root(tmp_path: Path, proto: str = MATCHING) -> Path:
    """Write the invented contract and one .proto into a temporary Task root."""
    (tmp_path / "proto/coldline/ingest/v1").mkdir(parents=True)
    (tmp_path / grpc_contract.PROTO_DIRECTORY / grpc_contract.PROTO_FILE).write_text(
        proto, encoding="utf-8"
    )
    (tmp_path / "contract.md").write_text(CONTRACT, encoding="utf-8")
    return tmp_path


def _failures(root: Path) -> list[str]:
    """Return the rendered failures of one check against the invented contract."""
    report = proto_check.check(root, contract_path=root / "contract.md")
    return [item.render() for item in report.failures]


def test_the_shipped_contract_document_names_what_the_code_names() -> None:
    """The contract document's names are the constants the scaffold and the harness use."""
    contract = proto_check.read_contract(TASK_ROOT / "docs/contracts/ingest-grpc.md")

    assert contract.package == grpc_contract.PACKAGE
    assert contract.service == grpc_contract.SERVICE
    assert contract.method == grpc_contract.METHOD
    assert contract.request == grpc_contract.REQUEST
    assert contract.response == grpc_contract.RESPONSE
    for fields in (contract.request_fields, contract.response_fields):
        numbers = [field.number for field in fields]
        assert numbers == list(range(1, len(fields) + 1))
        assert {field.presence for field in fields} <= set(proto_check.PRESENCE_RULES)
        assert all(field.type in proto_check.TYPE_NAMES.values() for field in fields)


def test_a_matching_proto_passes_every_item(tmp_path: Path) -> None:
    """A schema with every name, number, type and presence rule of the contract passes."""
    root = _root(tmp_path)

    report = proto_check.check(root, contract_path=root / "contract.md")

    assert report.failures == []
    labels = [item.label for item in report.items]
    assert "field ProbeReading.level = 2, double, optional presence" in labels
    assert "method ProbeIngest.Send(ProbeReading) returns (ProbeAck), unary" in labels


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("optional double level = 2;", "optional double level = 7;", "its number is 7"),
        ("optional double level = 2;", "optional float level = 2;", "its type is float"),
        ("optional double level = 2;", "optional string level = 2;", "its type is string"),
        ("optional int64 seen_at_ms = 3;", "int64 seen_at_ms = 3;", "its presence is implicit"),
        ("string label = 1;", "optional string label = 1;", "its presence is optional"),
        ("string label = 1;", "repeated string label = 1;", "it is repeated"),
        ("string ack_id = 1;", "string ack_id = 1;\n  string extra = 9;", "not in the contract"),
        ("optional double level = 2;", "", "field ProbeReading.level"),
        ("rpc Send(ProbeReading)", "rpc Send(stream ProbeReading)", "it streams"),
        ("returns (ProbeAck);", "returns (stream ProbeAck);", "it streams"),
        (
            "service ProbeIngest {",
            "service ProbeIngest {\n  rpc Other(ProbeAck) returns (ProbeAck);",
            "it also declares Other",
        ),
        ("rpc Send(ProbeReading) returns (ProbeAck);", "", "not declared"),
        ("package example.probe.v1;", "package example.other.v1;", "package example.probe.v1"),
    ],
    ids=[
        "number",
        "float-for-double",
        "string-for-double",
        "optional-dropped",
        "optional-added",
        "repeated",
        "extra-field",
        "missing-field",
        "client-streaming",
        "server-streaming",
        "second-method",
        "missing-method",
        "package",
    ],
)
def test_each_kind_of_mismatch_is_named(tmp_path: Path, old: str, new: str, expected: str) -> None:
    """Every difference from the contract is a failed item whose line says what differs."""
    assert old in MATCHING
    root = _root(tmp_path, MATCHING.replace(old, new, 1))

    failures = _failures(root)

    assert any(expected in failure for failure in failures), failures


def test_a_missing_message_is_named(tmp_path: Path) -> None:
    """A schema without the response message fails that item and the method's return type."""
    proto = MATCHING.replace("message ProbeAck {\n  string ack_id = 1;\n}\n", "").replace(
        "returns (ProbeAck)", "returns (ProbeReading)"
    )
    root = _root(tmp_path, proto)

    failures = _failures(root)

    assert any("message ProbeAck: not defined" in failure for failure in failures), failures
    assert any("it returns example.probe.v1.ProbeReading" in failure for failure in failures)


def test_a_proto_that_does_not_compile_is_one_failed_item(tmp_path: Path) -> None:
    """A schema protoc rejects is reported as not compiling, not as a traceback."""
    root = _root(tmp_path, MATCHING.replace("string label = 1;", "string label = ;"))

    failures = _failures(root)

    assert len(failures) == 1
    assert "compiles" in failures[0]


def test_a_contract_document_without_its_tables_is_a_tooling_error(tmp_path: Path) -> None:
    """A contract document missing a table is a tooling error, not a failed item."""
    root = _root(tmp_path)
    (root / "contract.md").write_text("# Probe contract\n\n## Names\n", encoding="utf-8")

    with pytest.raises(proto_check.ContractDocumentError):
        proto_check.check(root, contract_path=root / "contract.md")


def test_proto_gen_writes_both_modules_and_the_stamp(tmp_path: Path) -> None:
    """The generated message and service modules land under the output, beside the digest."""
    root = _root(tmp_path)
    output = tmp_path / "out"

    grpc_codegen.generate(root, output=output)

    assert (output / "coldline/ingest/v1/ingest_pb2.py").is_file()
    assert (output / "coldline/ingest/v1/ingest_pb2_grpc.py").is_file()
    source = (root / grpc_contract.PROTO_DIRECTORY / grpc_contract.PROTO_FILE).read_bytes()
    stamp = (output / grpc_contract.STAMP_NAME).read_text(encoding="utf-8").strip()
    assert stamp == hashlib.sha256(source).hexdigest()


def test_a_failed_generation_keeps_the_earlier_code(tmp_path: Path) -> None:
    """Protoc's failure is a CodegenError and the code generated before stays in place."""
    root = _root(tmp_path)
    output = tmp_path / "out"
    grpc_codegen.generate(root, output=output)
    before = (output / grpc_contract.STAMP_NAME).read_text(encoding="utf-8")
    (root / grpc_contract.PROTO_DIRECTORY / grpc_contract.PROTO_FILE).write_text(
        'syntax = "proto3";\nmessage {\n', encoding="utf-8"
    )

    with pytest.raises(grpc_codegen.CodegenError):
        grpc_codegen.generate(root, output=output)
    assert (output / grpc_contract.STAMP_NAME).read_text(encoding="utf-8") == before


def test_the_loader_refuses_missing_and_stale_generated_code(tmp_path: Path) -> None:
    """No generated code, or code generated from an earlier .proto, is never imported."""
    root = _root(tmp_path)

    with pytest.raises(grpc_contract.ContractUnavailable, match="poe proto-gen"):
        grpc_contract.load(root)

    grpc_codegen.generate(root)
    (root / grpc_contract.PROTO_DIRECTORY / grpc_contract.PROTO_FILE).write_text(
        MATCHING + "\n// edited\n", encoding="utf-8"
    )
    with pytest.raises(grpc_contract.ContractUnavailable, match="earlier version"):
        grpc_contract.load(root)


def _module(**attributes: object) -> ModuleType:
    """Return a stand-in generated module carrying ``attributes``."""
    module = ModuleType("stand_in")
    for name, value in attributes.items():
        setattr(module, name, value)
    return module


def test_the_missing_contract_items_are_named_in_contract_order() -> None:
    """The absent messages, service or method are listed; a complete module lists nothing."""
    method = {grpc_contract.METHOD: object()}
    service = SimpleNamespace(methods_by_name=method)
    descriptor = SimpleNamespace(services_by_name={grpc_contract.SERVICE: service})
    complete = grpc_contract.GeneratedContract(
        messages=_module(
            DESCRIPTOR=descriptor,
            **{grpc_contract.REQUEST: object, grpc_contract.RESPONSE: object},
        ),
        services=_module(),
    )
    empty = grpc_contract.GeneratedContract(
        messages=_module(DESCRIPTOR=SimpleNamespace(services_by_name={})), services=_module()
    )

    assert complete.missing() == []
    assert empty.missing() == [
        f"message {grpc_contract.REQUEST}",
        f"message {grpc_contract.RESPONSE}",
        f"service {grpc_contract.SERVICE}",
    ]
