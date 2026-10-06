"""Coldline.

===================

File:              tests/unit/api/test_grpc_auth.py
Component:         Unit tests — gRPC token interceptor
Purpose:           Prove the supplied interceptor's UNAUTHENTICATED and PERMISSION_DENIED
                    decisions, and that it passes other methods and admitted calls on.
Interacts With:    src/api/security/grpc_auth.py, src/api/security/access.py,
                    src/api/security/tokens.py, tests/fixtures/tokens/fixtures.yaml
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Interceptors, metadata, authentication versus authorization
Tools:             Python 3.12, pytest, grpcio

The interceptor is exercised on its own, with an invented method name and the summary-read
row of the access policy, so this file neither registers it on the reading method nor names
the row that grants submitting a reading. No server runs: the continuation is a stand-in
and a refusal handler is called directly with a context that records the abort. The key
set is the committed file.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import grpc
import pytest

from api.security.grpc_auth import STATUS_BY_KIND, TokenInterceptor, metadata_value
from api.security.tokens import AuthSettings, TokenVerifier
from tests.security.fixtures import token

TASK_ROOT = Path(__file__).resolve().parents[3]
KEY_SET = TASK_ROOT / "infra/issuer/jwks.json"
PROTECTED = "/example.probe.v1.ProbeService/Read"
CONTINUED = object()


class Aborted(Exception):
    """Stand in for the gRPC runtime's abort: carry the code and the details."""

    def __init__(self, code: Any, details: str) -> None:
        """Keep both parts."""
        super().__init__(details)
        self.code = code
        self.details = details


class RecordingContext:
    """A servicer context whose abort raises, as the runtime's does."""

    async def abort(self, code: Any, details: str) -> None:
        """Raise the abort as an exception."""
        raise Aborted(code, details)


def _verifier() -> TokenVerifier:
    """Return a verifier over the committed key set."""
    settings = AuthSettings(
        issuer="https://issuer.coldline.test",
        audience="coldline-api",
        jwks_url="http://localhost:8180/.well-known/jwks.json",
        algorithms=["RS256"],
        leeway_seconds=30,
    )
    return TokenVerifier(settings, fetch=lambda url: KEY_SET.read_bytes())


def _interceptor() -> TokenInterceptor:
    """Return the interceptor guarding the invented method with the summary-read row."""
    return TokenInterceptor(
        _verifier(), method=PROTECTED, role="dispatcher", scope="exceptions:read"
    )


async def _continuation(details: Any) -> Any:
    """Stand in for the server's next step: return a marker."""
    return CONTINUED


async def _intercept(method: str, metadata: list[tuple[str, str]]) -> Any:
    """Run the interceptor for one call."""
    details = SimpleNamespace(method=method, invocation_metadata=metadata)
    return await _interceptor().intercept_service(_continuation, details)


async def _refusal_of(handler: Any) -> Aborted:
    """Call a refusal handler and return the abort it raised."""
    with pytest.raises(Aborted) as raised:
        await handler.unary_unary(b"", RecordingContext())
    return raised.value


def _bearer(name: str) -> list[tuple[str, str]]:
    """Return the metadata that sends one fixture as a bearer token."""
    return [("authorization", f"Bearer {token(name)}")]


async def test_a_call_to_another_method_passes_on_unchecked() -> None:
    """Interceptors are server-wide: a method the interceptor does not guard is not checked."""
    handler = await _intercept("/example.probe.v1.ProbeService/Other", [])

    assert handler is CONTINUED


async def test_the_granted_role_and_scope_pass_on_to_the_method() -> None:
    """A verified token with the row's role and scope reaches the method."""
    handler = await _intercept(PROTECTED, _bearer("dispatcher-valid"))

    assert handler is CONTINUED


@pytest.mark.parametrize("fixture", ["gateway-valid", "wrong-role", "missing-scope"])
async def test_a_valid_token_without_the_grant_is_permission_denied(fixture: str) -> None:
    """A verified caller the row does not grant is refused PERMISSION_DENIED."""
    handler = await _intercept(PROTECTED, _bearer(fixture))

    refusal = await _refusal_of(handler)
    assert refusal.code == grpc.StatusCode.PERMISSION_DENIED


@pytest.mark.parametrize("fixture", ["expired", "bad-signature", "wrong-issuer", "wrong-audience"])
async def test_a_token_the_verifier_refuses_is_unauthenticated(fixture: str) -> None:
    """A token the verifier refuses is UNAUTHENTICATED, with the verifier's reason."""
    handler = await _intercept(PROTECTED, _bearer(fixture))

    refusal = await _refusal_of(handler)
    assert refusal.code == grpc.StatusCode.UNAUTHENTICATED
    assert refusal.details.startswith("token rejected:")


@pytest.mark.parametrize(
    "metadata",
    [[], [("authorization", "Basic abc")], [("x-other", "Bearer abc")]],
    ids=["no-metadata", "not-bearer", "other-key"],
)
async def test_a_call_without_a_bearer_token_is_unauthenticated(
    metadata: list[tuple[str, str]],
) -> None:
    """No authorization entry, or one that is not a bearer token, is UNAUTHENTICATED."""
    handler = await _intercept(PROTECTED, metadata)

    refusal = await _refusal_of(handler)
    assert refusal.code == grpc.StatusCode.UNAUTHENTICATED


def test_the_two_refusal_statuses_are_the_counterparts_of_401_and_403() -> None:
    """The status table maps each refusal kind to its gRPC status."""
    assert STATUS_BY_KIND == {
        "unauthenticated": grpc.StatusCode.UNAUTHENTICATED,
        "permission_denied": grpc.StatusCode.PERMISSION_DENIED,
    }


def test_metadata_values_are_read_by_lowercase_key_and_decoded() -> None:
    """The first matching entry wins; a bytes value is decoded; a missing key is None."""
    metadata = [("Authorization", "Bearer first"), ("authorization", "Bearer second")]

    assert metadata_value(metadata, "authorization") == "Bearer first"
    assert metadata_value([("authorization", b"Bearer raw")], "authorization") == "Bearer raw"
    assert metadata_value(None, "authorization") is None
    assert metadata_value([("other", "value")], "authorization") is None


@pytest.mark.parametrize(
    "method,role,scope",
    [
        ("Read", "dispatcher", "exceptions:read"),
        ("/example.probe.v1.ProbeService", "dispatcher", "exceptions:read"),
        (PROTECTED, " ", "exceptions:read"),
        (PROTECTED, "dispatcher", ""),
    ],
    ids=["bare-name", "service-only", "blank-role", "blank-scope"],
)
def test_a_registration_needs_a_full_method_name_a_role_and_a_scope(
    method: str, role: str, scope: str
) -> None:
    """The interceptor refuses to be built for a method it could never match, or for no rule."""
    with pytest.raises(ValueError):
        TokenInterceptor(_verifier(), method=method, role=role, scope=scope)
