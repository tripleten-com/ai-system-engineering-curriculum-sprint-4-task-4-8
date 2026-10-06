"""Coldline.

===================

File:              src/api/security/grpc_auth.py
Component:         API — gRPC token interceptor
Purpose:           Verify the bearer token in a gRPC call's metadata and apply one role-and-scope
                    rule to one method before its handler runs.
Interacts With:    api.security.access (check_access), api.security.tokens (TokenVerifier),
                    src/api/grpc_ingest.py, docs/security/access-policy.md
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Interceptors, metadata as the gRPC counterpart of headers, UNAUTHENTICATED
                    versus PERMISSION_DENIED
Tools:             Python 3.12, grpcio (asyncio server)

Supplied and settled: register it, do not edit it. ``TokenInterceptor`` is the gRPC
counterpart of ``require_access``. It reads the call's ``authorization`` metadata entry
(``Bearer <token>``), and runs the same decision ``require_access`` applies to an HTTP route,
``check_access``: the same ``TokenVerifier`` (the one ``config/auth.yaml`` configures), then
the same role-and-scope rule. A refusal ends the call before the method's handler runs:

- no token, a value that is not a bearer token, or a token the verifier refuses:
  ``UNAUTHENTICATED``, with the verifier's reason in the status details (HTTP's 401);
- a verified token whose role is not the one granted, or whose scopes do not include the one
  granted: ``PERMISSION_DENIED`` (HTTP's 403);
- otherwise the call reaches the method.

Python gRPC interceptors are server-wide: the server runs every registered interceptor for
every call. This one therefore takes the full name of the one method it protects
(``/<package>.<Service>/<Method>``) and lets a call to any other method through unchecked.
Registration form, in ``src/api/grpc_ingest.py``::

    from api.security.grpc_auth import TokenInterceptor

    TokenInterceptor(verifier, method=FULL_METHOD, role="<role>", scope="<scope>")

``role`` and ``scope`` are the one row of ``docs/security/access-policy.md`` that grants the
action the method performs. ``tests/security/grpc_mutation.py`` runs your tests against
temporary copies of this module inside the API container (one that admits every call, one
with the two statuses swapped, one that refuses only after the method has run, and one that
returns another exception id) to prove they can fail; the file in your repository never
changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import grpc

from api.security.access import AccessDenied, check_access
from api.security.tokens import TokenVerifier

METADATA_KEY = "authorization"
# The gRPC status for each kind of refusal: the counterparts of 401 and 403.
STATUS_BY_KIND: dict[str, Any] = {
    "unauthenticated": grpc.StatusCode.UNAUTHENTICATED,
    "permission_denied": grpc.StatusCode.PERMISSION_DENIED,
}
Continuation = Callable[[Any], Awaitable[Any]]


def metadata_value(metadata: Sequence[Any] | None, key: str) -> str | None:
    """Return the first value of one metadata entry, by its lowercase key, or None.

    gRPC delivers metadata as ``(key, value)`` pairs with lowercase keys; a binary value (a
    key ending ``-bin``) arrives as bytes and is decoded here only for the comparison's sake.
    """
    for entry in metadata or ():
        name, value = entry[0], entry[1]
        if str(name).lower() != key:
            continue
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    return None


def refusal_handler(code: Any, details: str) -> Any:
    """Return a unary handler that ends the call with ``code`` and ``details``, and no response."""

    async def refuse(request: object, context: Any) -> None:
        await context.abort(code, details)

    return grpc.unary_unary_rpc_method_handler(refuse)


class TokenInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    """Admit a call to one method only when its bearer token holds one role and one scope."""

    def __init__(self, verifier: TokenVerifier, *, method: str, role: str, scope: str) -> None:
        """Bind the verifier, the protected method's full name, and the granted role and scope."""
        if not method.startswith("/") or method.count("/") != 2:
            raise ValueError("method must be the full name /<package>.<Service>/<Method>")
        if not role.strip() or not scope.strip():
            raise ValueError("TokenInterceptor needs a non-empty role and scope")
        self._verifier = verifier
        self.method = method
        self.role = role
        self.scope = scope

    async def intercept_service(self, continuation: Continuation, call_details: Any) -> Any:
        """Refuse the protected method's caller unless the token passes; pass other calls on."""
        if call_details.method != self.method:
            return await continuation(call_details)
        authorization = metadata_value(call_details.invocation_metadata, METADATA_KEY)
        try:
            check_access(self._verifier, authorization, role=self.role, scope=self.scope)
        except AccessDenied as exc:
            return refusal_handler(STATUS_BY_KIND[exc.kind], exc.reason)
        handler = await continuation(call_details)
        return handler
