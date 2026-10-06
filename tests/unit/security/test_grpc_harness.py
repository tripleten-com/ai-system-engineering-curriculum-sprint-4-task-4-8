"""Coldline.

===================

File:              tests/unit/security/test_grpc_harness.py
Component:         Unit tests — gRPC harness and payload size helpers
Purpose:           Prove the JSON-to-Protobuf mapping the harness applies, the fixed JSON
                    serializer `poe payload-size` uses, and the fresh and malformed readings.
Interacts With:    tests/security/grpc_harness.py, tests/security/payload_size.py,
                    tests/fixtures/grpc/readings.json
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          One mapping for every caller, a serializer fixed by its separators and order
Tools:             Python 3.12, pytest

Every reading measured here is an invented mapping, and the message is a stand-in class
that records what was set, so nothing here encodes or sizes the supplied reading.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

from api.grpc_contract import REQUEST, GeneratedContract
from tests.security import grpc_harness, payload_size


class StandInMessage:
    """Record the fields set on it, and serialize to a fixed marker."""

    def __init__(self) -> None:
        """Start with no field set."""
        self.__dict__["fields"] = {}

    def __setattr__(self, name: str, value: Any) -> None:
        """Record one field, refusing a name outside the stand-in contract."""
        if name not in {"reading_id", "level", "recorded_at_unix_ms", "note"}:
            raise AttributeError(name)
        self.__dict__["fields"][name] = value

    def SerializeToString(self) -> bytes:
        """Return a fixed five-byte marker."""
        return b"probe"


def _contract() -> GeneratedContract:
    """Return a stand-in generated contract whose request message is the stand-in."""
    messages = ModuleType("stand_in_messages")
    setattr(messages, REQUEST, StandInMessage)
    return GeneratedContract(messages=messages, services=ModuleType("stand_in_services"))


def test_the_json_size_is_compact_utf_8_in_the_given_key_order() -> None:
    """No whitespace, the keys as given, and multi-byte characters counted in bytes."""
    assert payload_size.json_bytes({"b": 1, "a": "x"}) == len('{"b":1,"a":"x"}')
    assert payload_size.json_bytes({"a": 2.0}) == len('{"a":2.0}')
    assert payload_size.json_bytes({"a": "é"}) == len('{"a":""}') + 2


def test_unix_ms_counts_whole_milliseconds_since_the_epoch() -> None:
    """An ISO instant with a zone becomes milliseconds; one without a zone is refused."""
    assert grpc_harness.unix_ms("1970-01-01T00:00:01Z") == 1000
    assert grpc_harness.unix_ms("1970-01-01T00:00:00.250+00:00") == 250
    assert grpc_harness.unix_ms("2026-01-01T00:00:00Z") == 1_767_225_600_000
    with pytest.raises(grpc_harness.HarnessError, match="no time zone"):
        grpc_harness.unix_ms("2026-01-01T00:00:00")


def test_the_message_carries_the_mapped_fields_and_leaves_none_unset() -> None:
    """recorded_at becomes recorded_at_unix_ms; a None value is left unset; others keep names."""
    reading = {
        "reading_id": "reading-example",
        "level": 1.5,
        "recorded_at": "1970-01-01T00:00:02Z",
        "note": None,
    }

    message = grpc_harness.to_message(_contract(), reading)

    assert message.fields == {
        "reading_id": "reading-example",
        "level": 1.5,
        "recorded_at_unix_ms": 2000,
    }
    assert payload_size.protobuf_bytes(_contract(), reading) == len(b"probe")


def test_a_field_the_message_does_not_have_is_a_harness_error() -> None:
    """A key with no field of that name is reported, not silently dropped."""
    with pytest.raises(grpc_harness.HarnessError, match="cannot hold"):
        grpc_harness.to_message(_contract(), {"unknown_field": "x"})


def test_fresh_readings_get_new_identities_and_the_malformed_one_loses_its_temperature() -> None:
    """Each fresh reading is a new exception; the malformed reading omits temperature_c."""
    first, second = grpc_harness.fresh_reading(), grpc_harness.fresh_reading()
    malformed = grpc_harness.malformed_reading()

    assert first["reading_id"] != second["reading_id"]
    assert grpc_harness.reading_exception_id(first) != grpc_harness.reading_exception_id(second)
    assert "temperature_c" in first
    assert "temperature_c" not in malformed
    assert set(first) - set(malformed) == {"temperature_c"}
