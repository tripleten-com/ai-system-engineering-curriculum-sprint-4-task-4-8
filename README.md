# Coldline Task 4.8 — Optional Task 8: gRPC and Protobuf ingest contract

This checkpoint is the finished Project 4 system from Task 4.7: a verified token decides who
may read an exception summary, and readings enter through `POST /api/v1/readings`. This Task
adds a second way in for sensor readings: one gRPC method that takes the reading as a
Protobuf message, checks the caller's token from the call's metadata with the verifier the
API already uses, and hands the reading to the same application call the JSON endpoint uses.
You define the message and the method in a supplied `.proto` file, implement the method,
register the supplied token interceptor, prove one accepted and one refused call with your
own tests, compare the size of one reading in both encodings, and record your answers in
`submission.yaml`. This Task is optional.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/tripleten-com/ai-system-engineering-curriculum-sprint-4-task-4-8/tree/main)

## Start the system

Prerequisites are Python 3.12 and Docker with Compose v2. The supplied bootstrap supports macOS
arm64/x86-64, Windows x86-64, and Linux x86-64/aarch64, and installs pinned uv 0.11.8 under
`.tools/bin`. If your computer cannot run the stack locally, use the Codespaces button above.

On macOS and most Linux distributions the interpreter is `python3`; substitute it wherever these
commands say `python`.

```shell
python infra/scripts/bootstrap.py
./.tools/bin/uv sync --frozen
./.tools/bin/uv run --frozen poe preflight
./.tools/bin/uv run --frozen poe start
./.tools/bin/uv run --frozen poe ready
```

PowerShell and POSIX wrappers are available under `infra/scripts/`. After uv is on `PATH`, the
shorter `uv run --frozen poe <task>` form works; in PowerShell on Windows the pinned binary is
`.tools/bin/uv.exe`.

| Service | Local address | Purpose |
|---|---|---|
| API | `http://localhost:8000` | Submit readings as JSON, read exception summaries with a token, search procedures |
| API: gRPC reading intake | `localhost:50051` | The `SubmitReading` method of `coldline.ingest.v1.ReadingIngest`, once you define it; `poe grpc-call` is the supplied client |
| Token issuer: discovery document | `http://localhost:8180/.well-known/openid-configuration` | The development issuer's OIDC discovery document: its `issuer` and `jwks_uri` |
| Token issuer: key set | `http://localhost:8180/.well-known/jwks.json` | The published key set (JWKS) the settled `config/auth.yaml` names |
| Jaeger | `http://localhost:16686` | Open traces |
| Grafana | `http://localhost:3000` | Use the focused diagnostics dashboard |
| Prometheus | `http://localhost:9090` | Query bounded metrics and inspect the deployed alert rule |
| Alertmanager | `http://localhost:9093` | Inspect firing and resolved alerts |
| LocalStack S3/SQS/Secrets Manager | `http://localhost:4566` | The emulated object-storage, queue and secret-store endpoint |

Each of these ports can be overridden by setting the matching `COLDLINE_API_HOST_PORT`,
`COLDLINE_GRPC_HOST_PORT`, `COLDLINE_ISSUER_HOST_PORT`, `COLDLINE_JAEGER_HOST_PORT`,
`COLDLINE_GRAFANA_HOST_PORT`, `COLDLINE_PROMETHEUS_HOST_PORT`,
`COLDLINE_ALERTMANAGER_HOST_PORT`, or `COLDLINE_LOCALSTACK_HOST_PORT` environment variable in
your shell environment or a local `.env` file (copy `.env.example`) if a default collides with
something already running on your machine. Keep the override in place for every `poe` command.
If you remap the issuer port, keep `config/auth.yaml` unchanged: it is supplied in this Task and
names the default port. Host-side tools (`poe grpc-call`, `poe read-exception`, `poe verify`,
the harness behind `tests/student/test_grpc_ingest.py`, the carried access and audit tests)
resolve the API's HTTP and gRPC ports and the issuer's origin from these variables (the
environment, then `.env`, then the default), and the API and the worker use the Compose-network
origins.

This Task runs as its own Compose project, `coldline-task-4-8`. If an earlier Task's stack is
still running, run `poe stop` in that Task's repository first; otherwise `poe start` here fails
because the published ports are already taken.

PostgreSQL, Redis, worker metrics, and OTLP remain inside the Compose network. Codespaces uses the
same `compose.yaml` and keeps every forwarded port private, the gRPC port included.

## Restart the API after an edit

The API image carries `src/api/grpc_ingest.py` and the Python code generated from your `.proto`
as they were when the image was built: the image runs the same generator as `poe proto-gen`
while it builds. After you edit either file, rebuild the API image and recreate the API
container alone:

```shell
./.tools/bin/uv run --frozen poe restart-api
```

`poe restart` restarts the existing API and worker containers **without rebuilding**, so it
keeps serving the code the image was built with; use it only when nothing in the checkout
changed. `poe start` on a running stack also rebuilds what changed. If the API logs say the
gRPC reading intake serves no method, the image's generated code does not define the contract's
message, response and method yet: run `poe proto-gen` and `poe proto-check` on the host to see
why. `docker compose --profile observability --profile localstack logs api` shows those lines.

## Read an exception with a bearer token

The summary endpoint admits one token fixture, `dispatcher-valid`. To read the exception a call
returned, send that fixture as a bearer token:

```shell
./.tools/bin/uv run --frozen poe read-exception <exception_id>
```

`poe read-exception` reads the token from `tests/fixtures/tokens/fixtures.yaml` and prints the
status and the body; `--token <fixture>` sends another fixture. The same request by hand, with
the `dispatcher-valid` value copied from that file:

```shell
curl -H "Authorization: Bearer <dispatcher-valid token>" \
  http://localhost:8000/api/v1/exceptions/<exception_id>
```

In PowerShell, use `curl.exe` and put the command on one line.

## Command path

For this Task, run the supplied commands in this order:

```text
poe proto-gen          # Step 1: generate the Python code from your .proto
poe proto-check        # Step 1: compare your .proto with docs/contracts/ingest-grpc.md
poe restart-api        # Steps 2 and 3: after each edit to the method, the registration or the .proto
poe grpc-call --token gateway-valid
poe grpc-call --token gateway-valid --reading malformed
poe grpc-call --token dispatcher-valid
poe grpc-call --token expired
poe student-tests      # Step 4: your tests, beside the carried ones
poe payload-size       # Step 4: one supplied reading as JSON and as Protobuf
poe answers            # as you fill submission.yaml
poe verify
```

The exact public command is `./.tools/bin/uv run --frozen poe verify`, run from the repository
root. Where a Task page shortens a command to `poe <task>`, that is the form it means.

| Command | Use |
|---|---|
| `poe verify` | The public student verification path: the answer format and the permitted files, the unit tests, the stack, `poe proto-gen` and `poe proto-check`, the four calls and the comparison of your recorded statuses, your tests with the interceptor in place and under four mutations, then the inherited control checks |
| `poe proto-gen` | Generate the Python code for `proto/coldline/ingest/v1/ingest.proto` into the Git-ignored `.generated/grpc/` directory. Never commit it; `poe verify` and the API image generate it again |
| `poe proto-check` | Compile the `.proto` as it is now and compare it with `docs/contracts/ingest-grpc.md`: the package, the two messages, every field's number, type and presence rule, and the one unary method. One `ok` or `FAIL` line per item |
| `poe grpc-call --token <fixture> [--reading malformed]` | Send the supplied reading (`tests/fixtures/grpc/readings.json`, with a fresh `reading_id` each run) to `SubmitReading` with that token fixture in the `authorization` metadata, or the same reading without `temperature_c`, and print the status and the response or the status details |
| `poe payload-size` | Print the supplied reading's size in bytes as JSON and as Protobuf, once your `.proto` passes `poe proto-check` and `poe proto-gen` has run since your last edit |
| `poe read-exception <exception_id> [--token <fixture>]` | Read one exception through the protected summary endpoint, as `dispatcher-valid` unless you name another fixture |
| `poe restart-api` | Rebuild the API image from this checkout and recreate the API container alone |
| `poe student-tests` | Run every test under `tests/student/`: yours, `test_grpc_ingest.py`, and the carried Task 4.2, 4.3 and 4.4 tests. Start the stack first |
| `poe answers` | The answer sheet's format on its own: the two status names, the two sizes, a listed recommendation and a list of listed gaps, and not a copy of the sample |
| `poe submission` | The same check plus the permitted-files boundary: the diff from your merge base touches only the four student files |
| `poe grpc-contract` | The assessed module `poe verify` runs, on its own; needs the running stack |
| `poe grpc-mutation` | Print the inventory of your tests and the four mutation verdicts on their own, then restore the API container |
| `poe integrity-record`, `poe integrity-check` | The first and last steps of `poe verify`: hash the student files and the checks' own files into a snapshot outside the repository, then compare the tree with it |
| `poe auth-checks`, `poe token-check <fixture>`, `poe auth-config` | Task 2's tools over the settled `config/auth.yaml` and the access rule, still runnable |
| `poe scenario [--response <name>] [--note <id>]`, `poe audit-trail <exception_id>`, `poe pii-scan <exception_id>`, `poe redaction-report`, `poe model-request <exception_id>` | Task 3's and Task 4's tools, still runnable |
| `poe secret-status`, `poe secret-replace`, `poe provider-auth-check`, `poe secret-check-old` | Task 5's secret tools, still runnable; none prints a value |
| `poe security-setup`, `poe security-scan`, `poe seed-secret`, `poe seed-vulnerable` | Task 5's gate tools, kept as supplied; the `security-gate` job runs `poe security-scan` on every pull request with the settled thresholds and suppression |
| `poe queue-contract`, `poe slo-contract`, `poe gate-contract`, `poe runbook-contract`, `poe e2e` | Project 3's checks and the inherited platform checks, runnable as supplied |
| `poe contract` | Check interfaces, boundaries, submissions, and repository structure |
| `poe migrate`, `poe migrate-down`, `poe migrate-current` | Step the schema by hand; the initializer brings it to head on every start |
| `poe restart` | Restart the existing API and worker containers **without rebuilding** |
| `poe stop` | Remove containers and the network, keeping named volumes |
| `poe reset` | Remove containers, the network, and local named volumes |

`poe verify` records an integrity snapshot, runs `poe answers` and `poe submission`, runs the
unit tests, starts the stack (the API image generates the gRPC code from your `.proto` and
serves your method), ingests the supplied corpus, runs `poe proto-gen` and `poe proto-check`,
and then `poe grpc-contract`: it sends the `gateway-valid`, `dispatcher-valid` and `expired`
calls with the supplied reading and the malformed reading with `gateway-valid`, compares your
`answers.accepted_status` and `answers.refused_status` with the `gateway-valid` and
`dispatcher-valid` results, and runs your tests with the interceptor in place and with it
replaced inside the API container four ways, restoring the container afterwards. Last it reruns
the inherited control checks (`poe smoke`, `poe e2e-tests`, `poe student-tests`) and compares
the tree with the snapshot.

## Folder map

```text
repository root/
├── .generated/          Git-ignored: the Python code `poe proto-gen` generates from proto/
├── config/              Retrieval configuration, settled since Sprint 2, and the settled auth.yaml
├── docs/                Student guidance, public contracts, fidelity notes, the security and governance material
│   ├── contracts/       This Task's gRPC contract (ingest-grpc.md) and answer schema
│   ├── fidelity/        Local-runtime boundary notes for each active adapter and the token issuer
│   ├── governance/      The Task 6 material, supplied
│   ├── security/        The supplied workflow, threat catalog, control matrix, access policy, output policy, audit events, redactor, and gate policy
│   ├── architecture/    Supplied vector engine technical profiles, in prose
│   ├── retrieval/       Supplied retrieval pipeline reference
│   └── student/         This Task's contract, the settled threat model, the Task 6 corrections, and the Project 3 runbook
├── evidence/            Git-ignored: the evidence file `poe attack-dev` writes, if you run it
├── infra/               Local setup and runtime configuration
│   ├── containers/      The API and worker Dockerfiles; the API image generates the gRPC code
│   ├── issuer/          The development token issuer: its server script and the published key set
│   ├── observability/   Prometheus, Alertmanager, and Grafana configuration
│   ├── release/         The supplied release manifest, unchanged
│   ├── corpus/          Supplied synthetic corpus, query set, and investigation
│   ├── judge/           Supplied cached judge evidence and its provenance record
│   ├── profiles/        Supplied engine and emulator profiles, and their provenance record
│   └── postgres/        Database initialization and the migration baseline stamp
├── loadtest/            Supplied traffic profile and provider-latency harness
├── migrations/          Alembic environment, revision template, and revisions
├── proto/               The gRPC reading intake's Protobuf file: coldline/ingest/v1/ingest.proto
├── reports/             Git-ignored: the scan report and the bill of materials `poe security-scan` writes
├── schemas/             The supplied output schema the guardrail enforces
├── security/            The settled gate thresholds, the scanner pins, the supplied Semgrep rules
├── src/
│   ├── api/             HTTP application code, composition, the gRPC reading intake (grpc_ingest.py) and its generated-code loader
│   │   └── security/    The settled TokenVerifier and require_access rule, and the supplied gRPC token interceptor
│   ├── worker/          Background application code, the guardrail, the redaction calls
│   ├── common/          The supplied audit sink and the supplied redactor both services use
│   ├── domain/          Shared domain code, contracts (SensorReading among them), service and repository contracts
│   ├── ports/           Application interfaces
│   └── adapters/        Technology-specific implementations
└── tests/
    ├── unit/            Isolated behavior checks
    ├── contract/        Interface and repository checks, this Task's gRPC contract module and answer-sheet checks
    ├── fixtures/        Supplied fixtures: the token fixtures, the supplied gRPC reading under grpc/, and the earlier Tasks' fixtures
    ├── security/        Supplied tooling: the gRPC harness, proto-check, grpc-call, payload-size, the mutation runner, the integrity bookends, and the earlier Tasks' tools
    ├── student/         Your test_grpc_ingest.py, and the carried Task 4.2, 4.3 and 4.4 test files
    ├── smoke/           Running-platform checks
    └── e2e/             Supplied workflow tools and checks, including `poe scenario`
```

## Overview

Use the Optional Task 8 lesson (Task 4.8 in this repository) to decide what to do. This README
covers local setup and repository orientation.

1. `README.md` — local setup, commands, and permitted changes.
2. [`docs/student/task-4-8-contract.md`](docs/student/task-4-8-contract.md) — what this Task
   assesses and who assesses it, the supplied pieces, the Check-list rows and the checks that
   read them, and the four permitted paths.
3. [`docs/contracts/ingest-grpc.md`](docs/contracts/ingest-grpc.md) — the names, the fields with
   their types, numbers and presence rules, the mapping to `SensorReading`, and the status for
   each outcome.
4. [`docs/security/access-policy.md`](docs/security/access-policy.md) — the roles, the scopes,
   and the row that grants submitting a reading.
5. [`src/api/grpc_ingest.py`](src/api/grpc_ingest.py) — the server scaffold, its method stub
   and its interceptor registration slot; its docstring names the JSON endpoint's application
   call.

## Test levels

| Level | Requires Compose | Main question |
|---|---|---|
| Unit | No | Does one responsibility behave correctly, including failures? |
| Contract | Some | Do interfaces, schemas, paths, and dependency rules stay compatible? |
| Smoke | Yes | Did the complete local platform initialize and become observable? |
| E2E | Yes | Can an external client complete the supplied workflow, in one trace? |
| Student | Yes | Do your gRPC tests and the carried Task 4.2, 4.3 and 4.4 tests hold? |

Contract checks marked `runtime` need the running stack. `poe contract` skips them and this
Task's assessed module; `poe grpc-contract` runs the assessed module. A fresh checkout fails
`poe answers`, so `poe verify` stops there until `submission.yaml` is filled.

## Submission checks

Run `poe verify` locally before opening your student pull request. Public GitHub CI repeats
the student checks, running `poe submission` first so a boundary violation fails fast, and the
`security-gate` job runs `poe security-scan` with the settled thresholds; the gRPC packages
this Task adds are in `uv.lock` and the gate scans them like every other dependency. After
you submit on the platform, a protected answer check compares `answers.json_bytes`,
`answers.protobuf_bytes` and `answers.open_gaps` with the expected answers and reports its
result there. The recommendation is yours: only its format is checked. This Task is optional
and gates nothing else in Project 4.

## Task boundary

Task 4.8 asks you to define the reading message, the response and the method in the `.proto`,
implement the method and register the supplied interceptor, write the accepted-call and the
refused-call tests, and record your answers.

The only student-editable paths are:

- `proto/coldline/ingest/v1/ingest.proto`
- `src/api/grpc_ingest.py`
- `tests/student/test_grpc_ingest.py`
- `submission.yaml`

The contract document, the interceptor and the rest of `src/api/security/`, `config/auth.yaml`,
the token fixtures and the supplied reading, the supplied tests and tools, `POST
/api/v1/readings` and every other route, the guardrail, the redactor, the audit events, the
secret, the security gate and the workflows stay as supplied. The public check compares the
diff from your merge base with these four paths and reports any other change as a boundary
violation. The generated code under `.generated/` is Git-ignored and never part of the diff.

### Student walkthrough

See **Optional Task 8: gRPC and Protobuf ingest contract** in your course platform for the
full walkthrough. In outline: read `docs/contracts/ingest-grpc.md`, define the message, the
response and the method, run `poe proto-gen` and `poe proto-check`; implement the method in
`src/api/grpc_ingest.py`, run `poe restart-api` and the `gateway-valid` and malformed calls,
and read the returned exception with `poe read-exception`; register the interceptor, restart
the API and run the three token calls; write your two tests and run `poe student-tests`,
then remove the registration, restart the API, confirm the refused-call test fails, restore
the registration and restart again; run `poe payload-size`; fill `submission.yaml`; run
`poe verify`; open your pull request with the `poe grpc-call` and `poe payload-size` outputs
in its description.

## Operational limits

This is a local development stack. The token issuer is a development service: it publishes
one fixed key set and issues no tokens; the eight fixtures were signed once and committed.
See [TokenIssuer fidelity](docs/fidelity/TokenIssuer.md). The Compose PostgreSQL password and
the LocalStack access keys are development values, listed once more in
`tests/fixtures/credentials/test-values.yaml` so the audit checks can search for them. Never
place real credentials, personal data, or production records in this repository. Every
reading, note and token in this repository is synthetic.

The sizes `poe payload-size` prints measure one encoded reading and nothing else around it;
they are a measurement of this reading, not a traffic or cost estimate.

Alertmanager here is configured with a "default" receiver that has no notification integration:
alerts are queryable through its own API but never sent anywhere real. Never add a webhook, email,
Slack, or paid integration; Sprints 1-4 are emulator-only and never call a hosted endpoint.

Named volumes preserve local PostgreSQL, Redis, Prometheus, Alertmanager, Grafana, and Jaeger state
across `poe stop`; the audit table is in the PostgreSQL volume. LocalStack object, queue and
secret contents are deliberately not persisted; the initializer re-uploads the supplied corpus
artifacts, re-provisions the queue and re-creates the secret's first version on every start. The
`poe reset` command deletes the named volumes. This topology makes no backup, replication,
high-availability, disaster-recovery, capacity, latency-SLO, or availability claim beyond what
Project 3 settled.

See [TokenIssuer fidelity](docs/fidelity/TokenIssuer.md),
[JobQueue fidelity](docs/fidelity/JobQueue.md),
[ModelProvider fidelity](docs/fidelity/ModelProvider.md),
[SecretProvider fidelity](docs/fidelity/SecretProvider.md),
[ObjectStore fidelity](docs/fidelity/ObjectStore.md), and
[Retriever fidelity](docs/fidelity/Retriever.md) for the active adapter boundaries. The
[local runtime evidence](docs/fidelity/local-runtime.md) records the current measurement and its
qualification limits.
