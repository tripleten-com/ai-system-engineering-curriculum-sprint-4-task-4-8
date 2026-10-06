"""Coldline.

===================

File:              src/api/grpc_codegen.py
Component:         API — gRPC code generation
Purpose:           Generate the Python code for the reading intake's .proto file into the ignored
                    .generated/grpc/ directory (`poe proto-gen`), and compile the .proto to a
                    descriptor set for `poe proto-check`.
Interacts With:    proto/coldline/ingest/v1/ingest.proto, src/api/grpc_contract.py,
                    infra/containers/api.Dockerfile, tests/security/proto_check.py
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Generated code is built, not committed; one generator on the host and in
                    the image
Tools:             Python 3.12, grpcio-tools (protoc)

Supplied, not student-editable. ``poe proto-gen`` runs ``python -m api.grpc_codegen``: it
compiles ``proto/coldline/ingest/v1/ingest.proto`` with the ``protoc`` that ``grpcio-tools``
bundles, writes the message module and the service module under ``.generated/grpc/`` (the
whole directory is replaced on every run) and records the digest of the ``.proto`` it
compiled beside them. protoc's own error message names the line of a ``.proto`` that does not
compile, and the command then exits 1 and leaves the earlier generated code untouched.

The API image runs the same module when it is built, with ``--tolerant``: a ``.proto`` that
does not compile then leaves the image without generated code, the API starts anyway and its
gRPC server serves no method, so the inherited checks still run; ``poe proto-gen`` on the host
names the error.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from importlib import resources
from pathlib import Path

from api.grpc_contract import (
    GENERATED_DIRECTORY,
    PROTO_DIRECTORY,
    PROTO_FILE,
    STAMP_NAME,
    TASK_ROOT,
    generated_root,
    proto_digest,
    proto_path,
)


class CodegenError(RuntimeError):
    """Report that the .proto could not be compiled; protoc has printed why."""


def _protoc(root: Path, outputs: list[str]) -> int:
    """Run the bundled protoc over the contract's .proto with the given output flags."""
    # Imported here: only the API image and the host carry the tools, the worker does not.
    from grpc_tools import protoc

    well_known = resources.files("grpc_tools") / "_proto"
    arguments = [
        "grpc_tools.protoc",
        f"-I{root / PROTO_DIRECTORY}",
        f"-I{well_known}",
        *outputs,
        PROTO_FILE,
    ]
    return int(protoc.main(arguments))


def generate(root: Path = TASK_ROOT, *, output: Path | None = None) -> Path:
    """Generate the message and service modules for ``root``'s .proto; return where they went.

    The code is generated into a temporary directory first and moved into place only when
    protoc succeeds, so a failed run keeps the previous generated code.
    """
    if not proto_path(root).is_file():
        raise CodegenError(f"{PROTO_DIRECTORY}/{PROTO_FILE} is missing")
    target = generated_root(root) if output is None else output
    with tempfile.TemporaryDirectory(prefix="coldline-proto-gen-") as temporary:
        staging = Path(temporary) / "grpc"
        staging.mkdir()
        code = _protoc(root, [f"--python_out={staging}", f"--grpc_python_out={staging}"])
        if code != 0:
            raise CodegenError(
                f"protoc could not compile {PROTO_DIRECTORY}/{PROTO_FILE} (exit {code}); "
                "its message above names the line"
            )
        (staging / STAMP_NAME).write_text(proto_digest(root) + "\n", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, target)
    return target


def descriptor_set(root: Path = TASK_ROOT) -> bytes:
    """Compile ``root``'s .proto to a serialized ``FileDescriptorSet``, writing nothing in it."""
    if not proto_path(root).is_file():
        raise CodegenError(f"{PROTO_DIRECTORY}/{PROTO_FILE} is missing")
    with tempfile.TemporaryDirectory(prefix="coldline-proto-check-") as temporary:
        output = Path(temporary) / "ingest.pb"
        code = _protoc(root, [f"--descriptor_set_out={output}"])
        if code != 0 or not output.is_file():
            raise CodegenError(
                f"protoc could not compile {PROTO_DIRECTORY}/{PROTO_FILE} (exit {code}); "
                "its message above names the line"
            )
        return output.read_bytes()


def main(argv: list[str] | None = None) -> int:
    """Generate the code; with ``--tolerant`` a failure is reported and the exit code is 0."""
    parser = argparse.ArgumentParser(description="Generate the gRPC reading intake's Python code.")
    parser.add_argument(
        "--tolerant",
        action="store_true",
        help="report a .proto that does not compile and exit 0 (the API image build uses this)",
    )
    arguments = parser.parse_args(argv)
    try:
        target = generate()
    except CodegenError as exc:
        print(f"proto-gen: {exc}", file=sys.stderr)
        if arguments.tolerant:
            print(
                "proto-gen: continuing without generated code; the API's gRPC server will serve "
                "no method until the .proto compiles",
                file=sys.stderr,
            )
            return 0
        return 1
    print(
        f"proto-gen: generated the Python code for {PROTO_DIRECTORY}/{PROTO_FILE} under "
        f"{GENERATED_DIRECTORY}/ ({', '.join(sorted(path.name for path in _modules(target)))})"
    )
    return 0


def _modules(target: Path) -> list[Path]:
    """Return the generated Python modules under ``target``."""
    return [path for path in target.rglob("*.py") if path.is_file()]


if __name__ == "__main__":
    raise SystemExit(main())
