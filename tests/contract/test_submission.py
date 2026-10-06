"""Coldline.

===================

File:              tests/contract/test_submission.py
Component:         Contract tests — Test Submission
Purpose:           Tests for the public answer and path checks for this Task's submission.
Interacts With:    Published interfaces and repository boundaries
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Compatibility, ownership, export safety
Tools:             Python 3.12, pytest
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.contract.submission_validation import (
    ALLOWED_PATHS,
    ANSWER_FIELDS,
    GRPC_STATUSES,
    OPEN_GAP_OPTIONS,
    RECOMMENDATIONS,
    SubmissionError,
    _load_one_document,
    main,
    validate_changed_paths,
    validate_submission,
)

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "docs/contracts/submission.schema.json"
TEMPLATE = ROOT / "tests/fixtures/submission-template.yaml"
PERMITTED = [
    "proto/coldline/ingest/v1/ingest.proto",
    "src/api/grpc_ingest.py",
    "tests/student/test_grpc_ingest.py",
    "submission.yaml",
]


def valid_answers(**overrides: Any) -> dict[str, object]:
    """Return a complete answer sheet in the published shape.

    Fictional format example: these values show the shape and state no result. The two
    statuses are names no call in this Task ends with, the two sizes are round numbers no
    encoding of the supplied reading has, the recommendation is the first allowed value (the
    tests below use all three), and the gap list names every option, which no sheet could
    defend.
    """
    answers: dict[str, Any] = {
        "accepted_status": "DATA_LOSS",
        "refused_status": "OUT_OF_RANGE",
        "json_bytes": 1000,
        "protobuf_bytes": 400,
        "recommendation": RECOMMENDATIONS[0],
        "open_gaps": list(OPEN_GAP_OPTIONS),
    }
    answers.update(overrides)
    return {"answers": answers}


def _task_root(tmp_path: Path, submission_text: str) -> Path:
    """Stage a minimal Task root the public verifier can validate."""
    (tmp_path / "docs/contracts").mkdir(parents=True)
    (tmp_path / "submission.yaml").write_text(submission_text, encoding="utf-8")
    (tmp_path / "submission-sample.yaml").write_text(
        (ROOT / "submission-sample.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "docs/contracts/submission.schema.json").write_text(
        SCHEMA.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def test_a_complete_sheet_is_well_formed(tmp_path: Path) -> None:
    """The public schema accepts a complete sheet without judging its correctness."""
    root = _task_root(tmp_path, yaml.safe_dump(valid_answers()))

    validate_submission(root / "submission.yaml", SCHEMA)


@pytest.mark.parametrize("recommendation", RECOMMENDATIONS)
def test_every_allowed_recommendation_is_well_formed(tmp_path: Path, recommendation: str) -> None:
    """Each of the three recommendations passes; the schema prefers none of them."""
    root = _task_root(tmp_path, yaml.safe_dump(valid_answers(recommendation=recommendation)))

    validate_submission(root / "submission.yaml", SCHEMA)


@pytest.mark.parametrize("option", OPEN_GAP_OPTIONS)
def test_every_open_gap_option_is_well_formed_on_its_own(tmp_path: Path, option: str) -> None:
    """A one-option list of any listed option passes the format check; none is preferred."""
    root = _task_root(tmp_path, yaml.safe_dump(valid_answers(open_gaps=[option])))

    validate_submission(root / "submission.yaml", SCHEMA)


def test_blank_template_fails_with_field_address(tmp_path: Path) -> None:
    """An untouched answer sheet must identify the first incomplete field."""
    root = _task_root(tmp_path, TEMPLATE.read_text(encoding="utf-8"))

    with pytest.raises(SubmissionError, match="answers.accepted_status is incomplete"):
        validate_submission(root / "submission.yaml", SCHEMA)


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"accepted_status": "ok"}, "accepted_status"),
        ({"accepted_status": "200"}, "accepted_status"),
        ({"refused_status": "PERMISSION DENIED"}, "refused_status"),
        ({"refused_status": 7}, "refused_status"),
        ({"json_bytes": "300 bytes"}, "json_bytes"),
        ({"json_bytes": -3}, "json_bytes"),
        ({"protobuf_bytes": 12.5}, "protobuf_bytes"),
        ({"protobuf_bytes": True}, "protobuf_bytes"),
        ({"recommendation": "maybe"}, "recommendation"),
        ({"recommendation": "Move_Now"}, "recommendation"),
        ({"open_gaps": ["not_an_option"]}, "open_gaps"),
        ({"open_gaps": "json_intake_open"}, "open_gaps"),
        ({"open_gaps": ["plaintext_token", "plaintext_token"]}, "open_gaps"),
    ],
    ids=[
        "status-lowercase",
        "status-as-http-code",
        "status-with-space",
        "status-as-number",
        "size-as-text",
        "size-negative",
        "size-fractional",
        "size-boolean",
        "unlisted-recommendation",
        "capitalised-recommendation",
        "unlisted-gap",
        "gaps-not-a-list",
        "gap-repeated",
    ],
)
def test_values_outside_the_published_contract_are_rejected(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    """The public schema must name the field it rejected, and reject the right ones."""
    sheet = valid_answers()
    answers = sheet["answers"]
    assert isinstance(answers, dict)
    answers.update(overrides)
    root = _task_root(tmp_path, yaml.safe_dump(sheet))

    with pytest.raises(SubmissionError, match=message):
        validate_submission(root / "submission.yaml", SCHEMA)


@pytest.mark.parametrize("field", ANSWER_FIELDS)
def test_a_missing_or_blank_field_is_named(tmp_path: Path, field: str) -> None:
    """Each of the six answers is required, and a blank one is named as incomplete."""
    answers = dict(valid_answers()["answers"])  # type: ignore[arg-type]
    del answers[field]
    root = _task_root(tmp_path, yaml.safe_dump({"answers": answers}))
    with pytest.raises(SubmissionError, match=field):
        validate_submission(root / "submission.yaml", SCHEMA)

    blank = dict(valid_answers()["answers"])  # type: ignore[arg-type]
    blank[field] = {"json_bytes": 0, "protobuf_bytes": 0, "open_gaps": []}.get(field, "")
    root = _task_root(tmp_path / "blank", yaml.safe_dump({"answers": blank}))
    with pytest.raises(SubmissionError, match=f"answers.{field} is incomplete"):
        validate_submission(root / "submission.yaml", SCHEMA)


@pytest.mark.parametrize(
    "field",
    ["calls_passed", "instructor_approved", "note_for_dana", "expired_status"],
)
def test_no_self_attestation_or_free_text_field_is_accepted(tmp_path: Path, field: str) -> None:
    """Reject a self-approval, a pass boolean, a free-text note, or an unrecorded status."""
    answers = valid_answers()
    mapping = answers["answers"]
    assert isinstance(mapping, dict)
    mapping[field] = True
    root = _task_root(tmp_path, yaml.safe_dump(answers))

    with pytest.raises(SubmissionError, match="Additional properties"):
        validate_submission(root / "submission.yaml", SCHEMA)


def test_missing_answers_mapping_is_rejected(tmp_path: Path) -> None:
    """The answers mapping is required, not merely tolerated."""
    root = _task_root(tmp_path, "task: 4.8\n")

    with pytest.raises(SubmissionError, match="answers must be one mapping"):
        validate_submission(root / "submission.yaml", SCHEMA)


def test_exact_sample_copy_is_rejected(tmp_path: Path) -> None:
    """The published sample must not be accepted as a student submission."""
    root = _task_root(tmp_path, (ROOT / "submission-sample.yaml").read_text(encoding="utf-8"))

    with pytest.raises(SubmissionError, match="fictional sample"):
        validate_submission(
            root / "submission.yaml",
            SCHEMA,
            sample_path=root / "submission-sample.yaml",
        )


def test_public_entrypoint_reports_an_incomplete_answer_sheet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Catch a verifier entrypoint that skips the real submission contract."""
    root = _task_root(tmp_path, TEMPLATE.read_text(encoding="utf-8"))

    assert main(root, changed_paths=[], format_only=True) == 1
    assert "answers.accepted_status is incomplete" in capsys.readouterr().err


def test_public_entrypoint_rejects_the_sample_and_accepts_a_complete_sheet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`poe answers` applies the sample-copy check; a complete sheet of its own passes."""
    copied = _task_root(tmp_path / "copied", (ROOT / "submission-sample.yaml").read_text("utf-8"))
    assert main(copied, changed_paths=[], format_only=True) == 1
    assert "fictional sample" in capsys.readouterr().err

    own = _task_root(tmp_path / "own", yaml.safe_dump(valid_answers()))
    assert main(own, changed_paths=[], format_only=True) == 0
    assert main(own, changed_paths=list(PERMITTED)) == 0


def test_public_entrypoint_reports_a_protected_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A change to the supplied interceptor is named, not silently accepted."""
    root = _task_root(tmp_path, yaml.safe_dump(valid_answers()))

    assert main(root, changed_paths=["src/api/security/grpc_auth.py"]) == 1
    assert "protected path changed: src/api/security/grpc_auth.py" in capsys.readouterr().err


def test_only_the_four_student_files_are_permitted() -> None:
    """The four student files pass; every supplied file is a protected path."""
    assert ALLOWED_PATHS == frozenset(PERMITTED)
    validate_changed_paths(list(PERMITTED))

    for protected in (
        "docs/contracts/ingest-grpc.md",
        "docs/security/access-policy.md",
        "config/auth.yaml",
        "src/api/security/grpc_auth.py",
        "src/api/security/access.py",
        "src/api/security/tokens.py",
        "src/api/grpc_contract.py",
        "src/api/grpc_codegen.py",
        "src/api/bootstrap.py",
        "src/api/routes.py",
        "tests/fixtures/tokens/fixtures.yaml",
        "tests/fixtures/grpc/readings.json",
        "tests/security/grpc_harness.py",
        "tests/security/grpc_mutation.py",
        "tests/contract/test_grpc_ingest_contract.py",
        "tests/student/test_exception_access.py",
        "proto/coldline/ingest/v1/other.proto",
        ".github/workflows/task.yml",
        "security/gate.yaml",
        "compose.yaml",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "submission-sample.yaml",
    ):
        with pytest.raises(SubmissionError, match="protected path changed"):
            validate_changed_paths([protected])


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "answers: {value: first, value: second}\n",
        "answers: &answer {value: fictional}\n",
        "answers: *missing\n",
        "answers: {<<: {value: fictional}}\n",
        "answers: {value: 2026-09-04}\n",
        "answers: {value: !custom fictional}\n",
        "answers: {1: fictional}\n",
    ],
    ids=["duplicate-key", "anchor", "alias", "merge-key", "date", "custom-tag", "non-string-key"],
)
def test_non_json_yaml_constructs_are_rejected(tmp_path: Path, unsafe_text: str) -> None:
    """Reject restricted syntax before schema validation can mask a parser defect."""
    submission = tmp_path / "submission.yaml"
    submission.write_text(unsafe_text, encoding="utf-8")

    with pytest.raises(SubmissionError, match="restricted YAML"):
        _load_one_document(submission)


def test_multiple_yaml_documents_are_rejected(tmp_path: Path) -> None:
    """A second document cannot supply or replace the answer mapping."""
    submission = tmp_path / "submission.yaml"
    submission.write_text("answers: {}\n---\nanswers: {}\n", encoding="utf-8")

    with pytest.raises(SubmissionError, match="exactly one YAML mapping"):
        _load_one_document(submission)


def test_a_sheet_that_is_not_utf_8_is_a_submission_error(tmp_path: Path) -> None:
    """A sheet saved in another encoding gets the public error, not a Python traceback."""
    submission = tmp_path / "submission.yaml"
    submission.write_bytes("answers: {recommendation: do_not_move}\n".encode("utf-16"))

    with pytest.raises(SubmissionError, match="restricted YAML"):
        _load_one_document(submission)


def test_the_schema_names_the_allowed_values() -> None:
    """The schema's enums are the validator's constants, in the same order."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    answers = schema["properties"]["answers"]

    assert tuple(answers["required"]) == ANSWER_FIELDS
    assert tuple(schema["$defs"]["grpc_status"]["enum"]) == GRPC_STATUSES
    assert tuple(answers["properties"]["recommendation"]["enum"]) == RECOMMENDATIONS
    gaps = answers["properties"]["open_gaps"]
    assert tuple(gaps["items"]["enum"]) == OPEN_GAP_OPTIONS
    assert gaps["uniqueItems"] is True
    assert gaps["minItems"] == 1
    assert gaps["maxItems"] == len(OPEN_GAP_OPTIONS)
    assert schema["$defs"]["byte_count"]["type"] == "integer"
    assert schema["$defs"]["byte_count"]["minimum"] == 1


def test_the_options_are_listed_in_alphabetical_order() -> None:
    """The open-gap options are ordered by id alone, so their order says nothing about them."""
    assert list(OPEN_GAP_OPTIONS) == sorted(OPEN_GAP_OPTIONS)
    assert len(set(OPEN_GAP_OPTIONS)) == len(OPEN_GAP_OPTIONS)


def test_the_template_fixture_is_a_blank_sheet_with_the_six_fields() -> None:
    """The fixture is the blank shape: two empty strings, two zeros, an empty string, a list."""
    document = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))

    assert document == {
        "answers": {
            "accepted_status": "",
            "refused_status": "",
            "json_bytes": 0,
            "protobuf_bytes": 0,
            "recommendation": "",
            "open_gaps": [],
        }
    }


def test_the_sample_uses_the_published_shape() -> None:
    """The sample is schema-valid and names only listed values."""
    document = _load_one_document(ROOT / "submission-sample.yaml")
    answers = document["answers"]

    validate_submission(ROOT / "submission-sample.yaml", SCHEMA)
    assert answers["accepted_status"] in GRPC_STATUSES
    assert answers["refused_status"] in GRPC_STATUSES
    assert answers["recommendation"] in RECOMMENDATIONS
    assert set(answers["open_gaps"]) <= set(OPEN_GAP_OPTIONS)
