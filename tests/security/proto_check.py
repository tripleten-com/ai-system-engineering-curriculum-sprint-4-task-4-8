"""Coldline.

===================

File:              tests/security/proto_check.py
Component:         Security tooling — Protobuf contract check
Purpose:           Compare the student's .proto with docs/contracts/ingest-grpc.md: the package,
                    the two message names, every field's number, type and presence rule, and the
                    one unary method (`poe proto-check`).
Interacts With:    proto/coldline/ingest/v1/ingest.proto, docs/contracts/ingest-grpc.md,
                    src/api/grpc_codegen.py, tests/security/payload_size.py,
                    tests/contract/test_grpc_ingest_contract.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          A schema checked against its written contract, explicit presence
Tools:             Python 3.12, grpcio-tools (protoc), Protobuf descriptors

Supplied, not student-editable. ``poe proto-check`` compiles the current ``.proto`` with the
bundled ``protoc`` into a descriptor set in a temporary directory (it reads the file you have
now, whether or not ``poe proto-gen`` has run since you edited it, and writes nothing in the
repository), reads the three tables of the contract document, and prints one line per item:
``ok`` or ``FAIL`` with the reason. It exits 0 when every item matches, 1 when one does not,
and 2 when the contract document itself cannot be read.

A field matches when it exists under the contract's name with the contract's number and type,
is not ``repeated``, and has the contract's presence rule: ``optional`` means the field is
declared with the ``optional`` keyword (explicit presence); ``implicit`` means it is declared
without a label and outside any ``oneof``. A message must carry exactly the contract's fields.
The service must declare exactly one method, under the contract's name, taking the request
message and returning the response message, with no streaming on either side.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf import descriptor_pb2

from api.grpc_codegen import CodegenError, descriptor_set
from api.grpc_contract import PROTO_DIRECTORY, PROTO_FILE

TASK_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DOCUMENT = Path("docs/contracts/ingest-grpc.md")
NAMES_HEADING = "Names"
REQUEST_HEADING = "Request message fields"
RESPONSE_HEADING = "Response message fields"
PRESENCE_RULES = ("optional", "implicit")
_FieldProto = descriptor_pb2.FieldDescriptorProto
TYPE_NAMES: dict[int, str] = {
    _FieldProto.TYPE_DOUBLE: "double",
    _FieldProto.TYPE_FLOAT: "float",
    _FieldProto.TYPE_INT64: "int64",
    _FieldProto.TYPE_UINT64: "uint64",
    _FieldProto.TYPE_INT32: "int32",
    _FieldProto.TYPE_FIXED64: "fixed64",
    _FieldProto.TYPE_FIXED32: "fixed32",
    _FieldProto.TYPE_BOOL: "bool",
    _FieldProto.TYPE_STRING: "string",
    _FieldProto.TYPE_GROUP: "group",
    _FieldProto.TYPE_MESSAGE: "message",
    _FieldProto.TYPE_BYTES: "bytes",
    _FieldProto.TYPE_UINT32: "uint32",
    _FieldProto.TYPE_ENUM: "enum",
    _FieldProto.TYPE_SFIXED32: "sfixed32",
    _FieldProto.TYPE_SFIXED64: "sfixed64",
    _FieldProto.TYPE_SINT32: "sint32",
    _FieldProto.TYPE_SINT64: "sint64",
}


class ContractDocumentError(ValueError):
    """Report that the contract document does not have the tables this check reads."""


@dataclass(frozen=True)
class ContractField:
    """One row of a message table: the field's name, number, type and presence rule."""

    name: str
    number: int
    type: str
    presence: str


@dataclass(frozen=True)
class Contract:
    """What the contract document names and lists."""

    package: str
    service: str
    method: str
    request: str
    response: str
    request_fields: tuple[ContractField, ...]
    response_fields: tuple[ContractField, ...]


@dataclass(frozen=True)
class Item:
    """One compared item: whether it matched, what it is, and why not."""

    passed: bool
    label: str
    reason: str = ""

    def render(self) -> str:
        """Return the line ``poe proto-check`` prints for this item."""
        if self.passed:
            return f"ok    {self.label}"
        return f"FAIL  {self.label}: {self.reason}"


@dataclass
class Report:
    """Every compared item, in the order they were compared."""

    items: list[Item] = field(default_factory=list)

    def add(self, passed: bool, label: str, reason: str = "") -> bool:
        """Record one item and return whether it passed."""
        self.items.append(Item(passed, label, reason))
        return passed

    @property
    def failures(self) -> list[Item]:
        """Return the items that did not match."""
        return [item for item in self.items if not item.passed]


def _cell(text: str) -> str:
    """Return one table cell without its padding and its code backticks."""
    return text.strip().strip("`").strip()


def _tables(text: str) -> dict[str, list[list[str]]]:
    """Return every Markdown table's body rows, keyed by the ``##`` heading above it."""
    tables: dict[str, list[list[str]]] = {}
    heading: str | None = None
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            heading = match.group(1)
            continue
        if heading is None or not line.startswith("|"):
            continue
        cells = [_cell(cell) for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        tables.setdefault(heading, []).append(cells)
    # The first row under each heading is the table's header row.
    return {name: body[1:] for name, body in tables.items() if body}


def _fields(rows: list[list[str]], heading: str) -> tuple[ContractField, ...]:
    """Return the fields one message table lists."""
    fields: list[ContractField] = []
    for row in rows:
        if len(row) < 4 or not row[1].isdigit() or row[3] not in PRESENCE_RULES:
            raise ContractDocumentError(f"a row under '{heading}' is not a field row: {row}")
        fields.append(ContractField(row[0], int(row[1]), row[2], row[3]))
    if not fields:
        raise ContractDocumentError(f"the table under '{heading}' lists no field")
    return tuple(fields)


def read_contract(path: Path) -> Contract:
    """Read the names and the two field tables from the contract document."""
    try:
        tables = _tables(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractDocumentError(f"{path} could not be read: {exc}") from exc
    for heading in (NAMES_HEADING, REQUEST_HEADING, RESPONSE_HEADING):
        if heading not in tables:
            raise ContractDocumentError(f"{path.name} has no table under '## {heading}'")
    names = {row[0]: row[1] for row in tables[NAMES_HEADING] if len(row) >= 2}
    try:
        return Contract(
            package=names["Package"],
            service=names["Service"],
            method=names["Method"],
            request=names["Request message"],
            response=names["Response message"],
            request_fields=_fields(tables[REQUEST_HEADING], REQUEST_HEADING),
            response_fields=_fields(tables[RESPONSE_HEADING], RESPONSE_HEADING),
        )
    except KeyError as exc:
        raise ContractDocumentError(f"the '{NAMES_HEADING}' table has no {exc} row") from exc


def _presence(field_proto: descriptor_pb2.FieldDescriptorProto) -> str:
    """Return a compiled field's presence rule in the contract's words."""
    if field_proto.proto3_optional:
        return "optional"
    if field_proto.HasField("oneof_index"):
        return "oneof"
    return "implicit"


def _check_message(
    report: Report,
    file_proto: descriptor_pb2.FileDescriptorProto,
    name: str,
    expected: tuple[ContractField, ...],
) -> None:
    """Compare one compiled message with the fields its table lists."""
    message = next((item for item in file_proto.message_type if item.name == name), None)
    if not report.add(message is not None, f"message {name}", "not defined"):
        return
    assert message is not None
    compiled = {item.name: item for item in message.field}
    for wanted in expected:
        label = (
            f"field {name}.{wanted.name} = {wanted.number}, {wanted.type}, "
            f"{wanted.presence} presence"
        )
        found = compiled.get(wanted.name)
        if found is None:
            report.add(False, label, "not declared")
            continue
        problems: list[str] = []
        if found.number != wanted.number:
            problems.append(f"its number is {found.number}")
        found_type = TYPE_NAMES.get(found.type, str(found.type))
        if found_type != wanted.type:
            problems.append(f"its type is {found_type}")
        if found.label == _FieldProto.LABEL_REPEATED:
            problems.append("it is repeated")
        found_presence = _presence(found)
        if found_presence != wanted.presence:
            problems.append(f"its presence is {found_presence}")
        report.add(not problems, label, "; ".join(problems))
    extra = sorted(set(compiled) - {wanted.name for wanted in expected})
    report.add(
        not extra,
        f"message {name} carries only the contract's fields",
        f"not in the contract: {', '.join(extra)}",
    )


def compare(contract: Contract, file_proto: descriptor_pb2.FileDescriptorProto) -> Report:
    """Compare one compiled .proto file with the contract, item by item."""
    report = Report()
    report.add(
        file_proto.syntax == "proto3",
        "syntax proto3",
        f"the file declares syntax {file_proto.syntax or 'proto2'!r}",
    )
    report.add(
        file_proto.package == contract.package,
        f"package {contract.package}",
        f"the file declares package {file_proto.package!r}",
    )
    _check_message(report, file_proto, contract.request, contract.request_fields)
    _check_message(report, file_proto, contract.response, contract.response_fields)
    service = next((item for item in file_proto.service if item.name == contract.service), None)
    if not report.add(service is not None, f"service {contract.service}", "not declared"):
        return report
    assert service is not None
    label = (
        f"method {contract.service}.{contract.method}({contract.request}) "
        f"returns ({contract.response}), unary"
    )
    method = next((item for item in service.method if item.name == contract.method), None)
    if method is None:
        report.add(False, label, "not declared")
    else:
        problems: list[str] = []
        if method.input_type != f".{contract.package}.{contract.request}":
            problems.append(f"it takes {method.input_type.lstrip('.')}")
        if method.output_type != f".{contract.package}.{contract.response}":
            problems.append(f"it returns {method.output_type.lstrip('.')}")
        if method.client_streaming or method.server_streaming:
            problems.append("it streams")
        report.add(not problems, label, "; ".join(problems))
    others = sorted(item.name for item in service.method if item.name != contract.method)
    report.add(
        not others,
        f"service {contract.service} declares one method",
        f"it also declares {', '.join(others)}",
    )
    return report


def check(root: Path = TASK_ROOT, *, contract_path: Path | None = None) -> Report:
    """Compile ``root``'s .proto and compare it with the contract document.

    A .proto that does not compile is one failed item; protoc prints the reason itself.
    ``ContractDocumentError`` propagates: a broken contract document is a tooling error.
    """
    contract = read_contract(root / CONTRACT_DOCUMENT if contract_path is None else contract_path)
    report = Report()
    try:
        compiled = descriptor_pb2.FileDescriptorSet.FromString(descriptor_set(root))
    except CodegenError as exc:
        report.add(False, f"{PROTO_DIRECTORY}/{PROTO_FILE} compiles", str(exc))
        return report
    report.add(True, f"{PROTO_DIRECTORY}/{PROTO_FILE} compiles")
    file_proto = next((item for item in compiled.file if item.name == PROTO_FILE), None)
    if file_proto is None:
        report.add(False, f"{PROTO_FILE} in the descriptor set", "protoc produced no such file")
        return report
    report.items.extend(compare(contract, file_proto).items)
    return report


def main(argv: list[str] | None = None) -> int:
    """Print every compared item; exit 0 if all match, 1 if one does not, 2 on a tooling error."""
    del argv
    try:
        report = check()
    except ContractDocumentError as exc:
        print(f"proto-check: {exc}", file=sys.stderr)
        return 2
    print(f"proto-check: {PROTO_DIRECTORY}/{PROTO_FILE} against {CONTRACT_DOCUMENT.as_posix()}")
    for item in report.items:
        print(item.render())
    if report.failures:
        print(
            f"proto-check failed: {len(report.failures)} of {len(report.items)} items do not "
            "match the contract",
            file=sys.stderr,
        )
        return 1
    print(f"proto-check passed: all {len(report.items)} items match the contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
