"""Coldline.

===================

File:              tests/security/exception_read.py
Component:         Security tooling — Summary endpoint read
Purpose:           Request GET /api/v1/exceptions/{exception_id} with a token fixture as a bearer
                    token and print the status and the body (`poe read-exception`).
Interacts With:    tests/fixtures/tokens/fixtures.yaml, the running API, README.md
Sprint/Task:       Sprint 4 — Project 4 / Task 4.8
Concepts:          Bearer tokens, the protected summary endpoint
Tools:             Python 3.12, httpx

Supplied, not student-editable. ``poe read-exception <exception_id>`` sends the
``dispatcher-valid`` fixture, the one token the summary endpoint admits; ``--token <fixture>``
sends another. It is the same request as the ``curl`` example in ``README.md``, without
copying the token by hand. It exits 0 whatever the status was, and 2 when the API could not
be reached.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from tests.runtime_config import host_port
from tests.security.fixtures import FIXTURE_NAMES, bearer_headers


def main(argv: list[str] | None = None) -> int:
    """Read one exception through the summary endpoint and print what came back."""
    parser = argparse.ArgumentParser(description="Read one exception as a token fixture.")
    parser.add_argument("exception_id", help="the id a call or a reading returned")
    parser.add_argument("--token", choices=FIXTURE_NAMES, default="dispatcher-valid")
    arguments = parser.parse_args(argv)
    url = (
        f"http://localhost:{host_port('COLDLINE_API_HOST_PORT', 8000)}"
        f"/api/v1/exceptions/{arguments.exception_id}"
    )
    try:
        response = httpx.get(url, headers=bearer_headers(arguments.token), timeout=10.0)
    except httpx.HTTPError as exc:
        print(f"read-exception: the API could not be reached: {exc}", file=sys.stderr)
        return 2
    print(f"GET /api/v1/exceptions/{arguments.exception_id} as {arguments.token}")
    print(f"status: {response.status_code}")
    print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
