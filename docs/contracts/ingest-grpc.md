<!--
Coldline - Task 4.8
Supplied material: the gRPC reading intake contract. Not student-editable.
`poe proto-check` reads the tables under "Names", "Request message fields" and "Response
message fields" by their first column; keep their headings and their shape if this file is
revised.
-->
# gRPC reading intake contract

A sensor gateway submits one reading per call to one unary gRPC method. The method takes the
reading as a Protobuf message, converts it to the `SensorReading` model that
`POST /api/v1/readings` accepts (`src/domain/contracts.py`), and hands it to the same
application call the JSON endpoint uses, `ReadingApplication.accept`
(`src/api/use_cases.py`). The Protobuf schema decides how each field is encoded. This
document decides what each field means, whether its presence is checked, and how it maps to
`SensorReading`. Application validation still decides whether a reading is acceptable.

## Names

| Item | Name |
|---|---|
| Protobuf file | `proto/coldline/ingest/v1/ingest.proto` |
| Package | `coldline.ingest.v1` |
| Service | `ReadingIngest` |
| Method | `SubmitReading` |
| Request message | `GatewayReading` |
| Response message | `ReadingAccepted` |
| Full method name | `/coldline.ingest.v1.ReadingIngest/SubmitReading` |

The method is unary: one `GatewayReading` in, one `ReadingAccepted` out, no streaming on
either side. The service declares this one method. `src/api/grpc_ingest.py`, the test
template and `poe grpc-call` use these names, so a different name breaks them.

## Request message fields

`GatewayReading` carries exactly these eight fields, none of them `repeated`.

| Field | Number | Type | Presence | Maps to `SensorReading` |
|---|---|---|---|---|
| `reading_id` | 1 | `string` | implicit | `reading_id`, as sent. The exception id is derived from it, so the same `reading_id` names the same exception on both intakes |
| `shipment_id` | 2 | `string` | implicit | `shipment_id`, as sent |
| `temperature_c` | 3 | `double` | optional | `temperature_c` |
| `allowed_min_c` | 4 | `double` | optional | `allowed_min_c` |
| `allowed_max_c` | 5 | `double` | optional | `allowed_max_c` |
| `recorded_at_unix_ms` | 6 | `int64` | optional | `recorded_at`: the instant that many milliseconds after 1970-01-01T00:00:00Z, as a UTC `datetime` |
| `context` | 7 | `string` | implicit | `context`; an unset field reads as `""`, the model's own default |
| `handling_note` | 8 | `string` | optional | `handling_note`: the text when the field is set (an empty string included), `None` when it is not |

The two values of the **Presence** column:

- **optional**: declare the field with the `optional` keyword, which gives it explicit
  presence, and have the method check it with `request.HasField("<field>")`. In proto3 a
  scalar field the sender leaves out reads as its zero value: an unset `temperature_c` reads
  `0.0`, which is a temperature, and an unset `recorded_at_unix_ms` reads `0`, which is
  1970. For the four numeric fields an unset field is a malformed reading, refused before a
  `SensorReading` is built. For `handling_note` presence is what tells "no note" (`None`)
  from "an empty note" (`""`), so it is checked to choose the value, never to refuse.
- **implicit**: declare the field without a label. An unset string reads as `""`;
  `SensorReading` refuses an empty `reading_id` or `shipment_id` itself, and an empty
  `context` is the model's default.

`SensorReading` has one more field, the development-only `emulator_response` selector. This
contract does not carry it: the method leaves it unset (`None`), so the model emulator gives
its default answer for every reading that arrives through gRPC.

## Response message fields

`ReadingAccepted` carries exactly these three fields, the same three the JSON endpoint
returns.

| Field | Number | Type | Presence | Value |
|---|---|---|---|---|
| `exception_id` | 1 | `string` | implicit | The accepted record's `exception_id` |
| `state` | 2 | `string` | implicit | The record's state name as the acceptance call returned it, for example `QUEUED` |
| `status_url` | 3 | `string` | implicit | `/api/v1/exceptions/{exception_id}`, the protected summary endpoint for that record |

## Status per outcome

Every call ends with one gRPC status. The first row is the only one that carries a
`ReadingAccepted`; every other row ends the call with its status and a short reason in the
status details, and no response message.

| Outcome | Raised by | gRPC status | The JSON intake's counterpart |
|---|---|---|---|
| The reading is accepted, or it is the same reading again and the existing record is returned | `ReadingApplication.accept` returns | `OK` | `202` |
| A field whose presence the contract checks to refuse (`temperature_c`, `allowed_min_c`, `allowed_max_c`, `recorded_at_unix_ms`) is unset | The method's presence check | `INVALID_ARGUMENT` | `422` |
| The fields do not make a valid `SensorReading` (an empty `reading_id`, a minimum not below the maximum, a value too long, a timestamp out of range) | `SensorReading(...)` raises `pydantic.ValidationError` | `INVALID_ARGUMENT` | `422` |
| The reading is inside its handling range, so there is no exception to raise | `InRangeReading` | `FAILED_PRECONDITION` | `422` |
| The record was stored but the job could not be published to the queue | `QueueUnavailable` | `UNAVAILABLE` | `503` |
| The reading's identity already has a terminal `FAILED` record | `TerminalExceptionConflict` | `ABORTED` | `409` |
| No token, a token that is not a bearer token, or a token the verifier refuses | The supplied token interceptor, before the method runs | `UNAUTHENTICATED` | `401` |
| A verified token whose role or scope is not the one the access policy grants for submitting a reading | The supplied token interceptor, before the method runs | `PERMISSION_DENIED` | `403` |

The acceptance path is idempotent by reading identity: sending the same `reading_id` again,
through either intake, returns the existing record and its exception id with `OK` (or `202`),
not a second exception. A malformed or in-range reading creates no record.

## Metadata

The caller's bearer token travels in the call's `authorization` metadata entry, in the same
form as the HTTP header: `Bearer <token>`. gRPC metadata keys are lowercase. The supplied
interceptor (`src/api/security/grpc_auth.py`) reads that entry and nothing else from the
metadata.
