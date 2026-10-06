"""Coldline.

===================

File:              src/api/grpc_ingest.py
Component:         API — gRPC reading intake
Purpose:           Serve the gateways' reading intake as one token-checked gRPC method beside the
                    JSON endpoint, on the same acceptance path.
Interacts With:    proto/coldline/ingest/v1/ingest.proto (through the generated code),
                    src/api/grpc_contract.py, src/api/security/grpc_auth.py,
                    src/api/use_cases.py, src/api/bootstrap.py, docs/contracts/ingest-grpc.md
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Convert at the edge, one acceptance path, interceptors, gRPC status codes
Tools:             Python 3.12, grpcio (asyncio server), Pydantic

This file is yours to complete in two places: the method stub ``SubmitReading`` (Step 2) and
the interceptor registration slot ``interceptors`` (Step 3). The rest is the supplied server
scaffold; keep it as it is.

The JSON endpoint's application call. ``POST /api/v1/readings`` (``accept_reading`` in
``src/api/routes.py``) hands the validated ``SensorReading`` to
``await application.accept(reading)``, where ``application`` is the ``ReadingApplication`` of
``src/api/use_cases.py``. That call redacts the reading's context, decides whether the
reading needs exception work, derives the exception id from the reading's identity, stores
the record and publishes one job to the queue the worker reads; the worker then runs the
guardrail, the redactor and the audit events. Sending the same reading again returns the
same record. The call raises ``InRangeReading``, ``QueueUnavailable`` and
``TerminalExceptionConflict`` (all three in ``api.use_cases``). The scaffold hands your
service the same ``ReadingApplication`` object the JSON endpoint uses.

The server starts and stops with the API (``src/api/bootstrap.py``), on the gRPC port inside
the Compose network; ``README.md`` lists the host port. It loads the Python code
``poe proto-gen`` generates from your ``.proto`` (the API image generates it again when it is
built) and registers your service only once that code defines the request message, the
response message and the method under the contract's names. Until then the server runs with
no method and answers every call ``UNIMPLEMENTED``. An edit to this file or to the ``.proto``
reaches the running API only after ``poe restart-api``, which rebuilds the API image.
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

import grpc

from api.grpc_contract import FULL_METHOD, SERVICE, ContractUnavailable, GeneratedContract, load
from api.security.tokens import TokenVerifier
from api.use_cases import ReadingApplication

LOGGER = logging.getLogger("coldline.api.grpc")
STOP_GRACE_SECONDS = 5.0


def interceptors(verifier: TokenVerifier) -> list[Any]:
    """Return the interceptors the server runs before your method: the registration slot.

    Step 3: register the supplied token interceptor for your method's full name, with the one
    role and the one scope ``docs/security/access-policy.md`` grants for submitting a
    reading::

        from api.security.grpc_auth import TokenInterceptor

        return [TokenInterceptor(verifier, method=FULL_METHOD, role="<role>", scope="<scope>")]

    The interceptor reads the bearer token from the call's ``authorization`` metadata and runs
    the same verification and role-and-scope rule as the protected summary endpoint. Until it
    is registered here, every call reaches your method, with a token or without one.
    """
    return []


async def refuse(context: Any, code: Any, details: str) -> NoReturn:
    """End the call with ``code`` (a ``grpc.StatusCode``) and ``details``, and no response.

    Supplied. ``context.abort`` raises inside the gRPC runtime, so nothing after an
    ``await refuse(...)`` runs.
    """
    await context.abort(code, details)
    raise AssertionError("context.abort returned instead of ending the call")


class ReadingIngestService:
    """The service the server registers: one method, ``SubmitReading``."""

    def __init__(self, application: ReadingApplication, contract: GeneratedContract) -> None:
        """Keep the JSON endpoint's ``ReadingApplication`` and the generated contract code.

        ``contract.message_class("ReadingAccepted")`` is the generated response class.
        """
        self._application = application
        self._contract = contract

    async def SubmitReading(self, request: Any, context: Any) -> Any:
        """Accept one ``GatewayReading`` on the JSON endpoint's acceptance path (Step 2).

        ``request`` is the decoded ``GatewayReading``; ``context`` is the call's context.
        Follow ``docs/contracts/ingest-grpc.md``:

        1. check the presence of every field the contract marks for it
           (``request.HasField("<field>")``);
        2. build a ``SensorReading`` (``domain.contracts``) from the fields, by the contract's
           mapping, and pass it to ``await self._application.accept(reading)``;
        3. end each error the contract lists with its status, through
           ``await refuse(context, grpc.StatusCode.<NAME>, "<reason>")``;
        4. return a ``ReadingAccepted`` built from the record the call returned.
        """
        await refuse(context, grpc.StatusCode.UNIMPLEMENTED, "SubmitReading is not implemented")


async def start_server(
    application: ReadingApplication, verifier: TokenVerifier, *, port: int
) -> Any:
    """Start the gRPC server on ``port`` with your interceptors and service; return it.

    Supplied: keep it as it is. The service is registered only when the generated code
    defines every contract item; otherwise the server runs with no method and logs why.
    """
    server = grpc.aio.server(interceptors=interceptors(verifier))
    try:
        contract = load()
    except ContractUnavailable as exc:
        LOGGER.warning("gRPC reading intake serves no method: %s", exc)
    else:
        missing = contract.missing()
        if missing:
            LOGGER.warning(
                "gRPC reading intake serves no method: the generated code does not define %s",
                ", ".join(missing),
            )
        else:
            register = getattr(contract.services, f"add_{SERVICE}Servicer_to_server")
            register(ReadingIngestService(application, contract), server)
            LOGGER.info("gRPC reading intake serves %s on port %s", FULL_METHOD, port)
    server.add_insecure_port(f"0.0.0.0:{port}")
    await server.start()
    return server


async def stop_server(server: Any) -> None:
    """Stop the server, letting calls in flight finish for a few seconds. Supplied."""
    await server.stop(STOP_GRACE_SECONDS)
