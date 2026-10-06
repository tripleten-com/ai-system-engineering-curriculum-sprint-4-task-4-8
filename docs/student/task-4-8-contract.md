# Task 4.8 — gRPC and Protobuf ingest contract

Readings enter Coldline through `POST /api/v1/readings`. This optional Task adds a second way
in: one unary gRPC method, `SubmitReading`, that takes a reading as a Protobuf message, checks
the caller's bearer token from the call's metadata with the verifier the API already uses, and
hands the reading to the same application call the JSON endpoint uses. You write the message,
the response and the method in the supplied `.proto`, implement the method, register the
supplied token interceptor, prove one accepted and one refused call with your own tests,
measure one reading in both encodings, and record six answers in `submission.yaml`. This Task
is optional and gates nothing else in Project 4.

## What is assessed, and by whom

| Assessed | By |
|---|---|
| The pull request changes only the four permitted paths | Automated, in this repository (`poe submission`, inside `poe verify`, and `test_submission_change_stays_within_the_permitted_diff`) |
| `submission.yaml` has the published shape: two gRPC status names, two positive sizes, a listed recommendation, a non-empty list of distinct listed gaps, and not the sample | Automated (`poe answers`, repeated by `poe submission` and `poe verify`) |
| The `.proto` against the contract document; the four calls; the shared acceptance path; the two recorded statuses against this run's calls; your two tests as written and under four interceptor mutations | Automated, in this repository, against the running stack (`poe grpc-contract`, inside `poe verify`) |
| The Project 4 controls still pass | Automated (`poe verify`: the running platform, the end-to-end workflow and every test under `tests/student/`); the hosted `security-gate` job runs the settled Task 5 gate |
| `answers.json_bytes`, `answers.protobuf_bytes` and `answers.open_gaps` | Protected automated check, on the platform after you submit |
| `answers.recommendation` | Your call: only its format is checked |
| The `poe grpc-call` and `poe payload-size` outputs in your pull request description | Nothing grades them; they are the record behind your answers |

## What is already supplied

| Supplied | Where | What it does |
|---|---|---|
| The contract | `docs/contracts/ingest-grpc.md` | The names; every field with its type, number and presence rule; the mapping to `SensorReading`; the status for each outcome |
| The Protobuf file | `proto/coldline/ingest/v1/ingest.proto` | The package and the service declared; the message, the response and the method are yours to write (Step 1) |
| The server scaffold | `src/api/grpc_ingest.py` | Starts and stops with the API on the gRPC port; registers your service once the generated code defines the contract's names; holds the method stub (Step 2) and the interceptor registration slot (Step 3); its docstring names the JSON endpoint's application call |
| The token interceptor | `src/api/security/grpc_auth.py` | `TokenInterceptor(verifier, method=..., role=..., scope=...)`: reads the `authorization` metadata and applies the same verification and role-and-scope rule as the protected summary endpoint (`check_access`, the decision `require_access` applies), refusing with `UNAUTHENTICATED` or `PERMISSION_DENIED` before your method runs |
| The generated-code loader and generator | `src/api/grpc_contract.py`, `src/api/grpc_codegen.py` | The contract's names in one place; `poe proto-gen` writes the generated code under the Git-ignored `.generated/grpc/` |
| The test template | `tests/student/test_grpc_ingest.py` | The `ingest` fixture: a gRPC client, an HTTP client that sends a token fixture as a bearer token, the token fixture loader, and a helper that sends a fresh reading and reports whether an exception was created for it (`tests/security/grpc_harness.py`) |
| The supplied reading | `tests/fixtures/grpc/readings.json` | The reading `poe grpc-call` sends (with a fresh `reading_id` each run) and `poe payload-size` measures (as written) |
| The tools | `poe proto-gen`, `poe proto-check`, `poe grpc-call`, `poe payload-size`, `poe read-exception`, `poe restart-api` | See `README.md` |
| The token fixtures and the policy | `tests/fixtures/tokens/fixtures.yaml`, `docs/security/access-policy.md` | The same fixtures and grants the summary endpoint uses; this Task uses `gateway-valid`, `dispatcher-valid` and `expired` |

Of the supplied pieces above, you edit `proto/coldline/ingest/v1/ingest.proto`,
`src/api/grpc_ingest.py` and `tests/student/test_grpc_ingest.py`, and you complete
`submission.yaml`. Every other listed file stays as supplied: the public check reports a change
to any of them as a boundary violation.

## Commands

```shell
poe proto-gen                                    # generate the code from your .proto
poe proto-check                                  # your .proto against the contract, item by item
poe restart-api                                  # rebuild the API after an edit
poe grpc-call --token <fixture> [--reading malformed]
poe read-exception <exception_id>                # read it back as dispatcher-valid
poe student-tests                                # your tests and the carried ones
poe payload-size                                 # one reading as JSON and as Protobuf
poe answers                                      # the answer sheet's format
poe submission                                   # the format plus the permitted files
poe verify                                       # the full public path
```

`poe answers` and `poe submission` are static and need no stack. `poe verify` starts the stack
itself. `poe grpc-call`, `poe read-exception`, `poe student-tests` and `poe grpc-contract`
need the stack running and the API rebuilt since your last edit.

## Check-list rows and the checks that read them

| Check-list row | Check |
|---|---|
| `ingest.proto` defines the reading message, the response, and one method under the names in `docs/contracts/ingest-grpc.md`, with every field at its assigned number and with its presence rule | `test_the_proto_declares_the_contract_names_fields_numbers_and_presence` (`poe proto-check`'s comparison) |
| `poe proto-gen` and `poe proto-check` pass | The `proto-gen` and `proto-check` steps of `poe verify`, and `test_proto_gen_and_proto_check_pass` |
| The method converts the message to `SensorReading` and uses the same acceptance path as `POST /api/v1/readings` | `test_the_method_converts_the_reading_and_uses_the_json_intakes_acceptance_path`: a fresh reading sent as `gateway-valid` ends `OK` with the exception id the reading's identity gives; the JSON intake, sent the same reading afterwards, answers with that same exception rather than a second one; the stored reading carries every mapped field (an unset `context` as `""`, no development selector); and the record reaches a finished state within a minute |
| A malformed reading is refused with `INVALID_ARGUMENT` | `test_a_malformed_reading_is_refused_with_invalid_argument`: the supplied reading without `temperature_c`, sent as `gateway-valid`, and one more fresh reading without each other field the contract checks for presence |
| `gateway-valid` is accepted with `OK` | `test_gateway_valid_is_accepted_with_ok` |
| `dispatcher-valid` is refused with `PERMISSION_DENIED` | `test_dispatcher_valid_is_refused_with_permission_denied`, with no response message |
| `expired` is refused with `UNAUTHENTICATED` | `test_expired_is_refused_with_unauthenticated`, with no response message |
| A reading accepted through gRPC can be read through the protected summary endpoint | `test_a_reading_accepted_through_grpc_is_readable_through_the_summary_endpoint`: the `gateway-valid` call's exception id, read as `dispatcher-valid`, answers `200` with that id |
| The accepted-call test verifies the returned exception id through the protected endpoint | `test_your_accepted_and_refused_call_tests_pass_with_the_interceptor_in_place` (your file has such a test and it passes), then `test_the_accepted_call_test_verifies_the_returned_id_through_the_summary_endpoint`: with the interceptor replaced by one that answers an accepted call with another exception id, the test fails |
| The refused-call test verifies `PERMISSION_DENIED` and that no exception was created for its reading | The same in-place row, then `test_the_refused_call_test_verifies_permission_denied_and_that_no_exception_was_created`: with the two refusal statuses swapped, and with an interceptor that refuses only after your method has run (so the exception exists), the test fails both times |
| The refused-call test fails when the interceptor is removed | `test_the_refused_call_test_fails_when_the_interceptor_is_removed`: with an interceptor that admits every call, which is what removing your registration does, the test fails |
| `answers.recommendation` holds one of the allowed values | `poe answers`, and `test_recommendation_is_one_of_the_allowed_values` |
| `answers.accepted_status` matches the `gateway-valid` call and `answers.refused_status` matches the `dispatcher-valid` call that `poe verify` makes | `test_answers_record_the_statuses_of_the_calls_this_run_made` |
| `answers.json_bytes`, `answers.protobuf_bytes`, and `answers.open_gaps` use the allowed format, and `answers.open_gaps` lists only options from `submission.yaml` | `poe answers`, and `test_sizes_and_open_gaps_use_the_allowed_format` |
| Your two payload sizes and your open gaps pass the protected answer check | Protected (CMS): the protected answer check runs on the platform after you submit and reports its result there |
| The Project 4 controls still pass their checks | `poe smoke`, `poe e2e-tests` and `poe student-tests` inside `poe verify`, and the hosted `security-gate` job |
| The pull request modifies only the four permitted paths | `test_submission_change_stays_within_the_permitted_diff` in `tests/contract/test_authoring_contract.py`, and `poe submission` inside `poe verify` |

Every `test_...` name above is a row of `tests/contract/test_grpc_ingest_contract.py`, which
`poe grpc-contract` runs inside `poe verify` after `poe proto-gen` and `poe proto-check`.

## How your tests are judged

The rows about your tests run `tests/student/test_grpc_ingest.py` against the running API and
record, through the harness, what each test actually did. A test counts as the **accepted-call
test** when it made a call with `gateway-valid` that ended `OK` and then read the returned
exception id through `GET /api/v1/exceptions/{exception_id}` as `dispatcher-valid` (through
`ingest.api_client` or `ingest.exception_exists`). A test counts as the **refused-call test**
when it sent a fresh reading with `dispatcher-valid` through `ingest.send_fresh_reading`. A
name, a comment or a constant counts for nothing; only the calls a test made do.

Both must pass as written. Then the supplied interceptor is replaced **inside the API
container**, never in your repository, four times, and the API restarted each time:

| Mutation | The interceptor in the container | Must fail |
|---|---|---|
| interceptor-removed | admits every call | your refused-call test |
| status-swapped | answers `UNAUTHENTICATED` for `PERMISSION_DENIED` and the reverse | your refused-call test |
| refusal-after-acceptance | lets the refused call run your method, then ends it `PERMISSION_DENIED` | your refused-call test |
| identity-swapped | answers an accepted call with another exception id | your accepted-call test |

Only a failing assertion counts: a test that still passes, is skipped, or errors does not
prove the mutation. After the last mutation the API container is recreated from its image.
`poe grpc-mutation` prints the same inventory and verdicts on their own.

## What the checks verify

| Check | What it looks at |
|---|---|
| `tests/contract/submission_validation.py` (`poe answers`, `poe submission`) | `submission.yaml` is one plain YAML mapping (no duplicate keys, aliases, anchors, merge keys or non-JSON tags) whose `answers` carries the six fields in the published shape (`docs/contracts/submission.schema.json`) and is not the sample. With `poe submission`, the diff from the merge base with `main` touches only the four permitted paths |
| `tests/security/proto_check.py` (`poe proto-check`) | The `.proto` as it is now, compiled into a descriptor set in a temporary directory, against the three tables of `docs/contracts/ingest-grpc.md` |
| `tests/contract/test_grpc_ingest_contract.py` (`poe grpc-contract`) | The rows above |
| `tests/security/integrity.py` (`poe integrity-record`, `poe integrity-check`) | SHA-256 digests of the four student files and every trusted file the checks rely on, recorded when `poe verify` starts and compared when it ends. The generated code is not covered, and the mutations never touch the repository |
| `poe smoke`, `poe e2e-tests`, `poe student-tests` inside `poe verify` | The platform you inherited, the end-to-end workflow, and every test under `tests/student/` |

## Student-editable paths

- `proto/coldline/ingest/v1/ingest.proto`
- `src/api/grpc_ingest.py`
- `tests/student/test_grpc_ingest.py`
- `submission.yaml`

That is the whole list. Do not commit the generated code: `.generated/` is Git-ignored, and
`poe verify` and the API image generate it again from your `.proto`. Before you push, run
`git status` and `git diff --stat`: if anything besides these four paths changed, the public
check reports the boundary violation rather than your work.
