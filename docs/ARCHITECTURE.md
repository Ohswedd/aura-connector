# Aura Architecture

Aura is a layered, async-native Python data client. Each layer has a single
responsibility and depends only on the layers beneath it.

```
+-------------------------------------------------------------+
| Public API   aura.Client / aura.Aura / Model / Field / Vector|
+-------------------------------------------------------------+
| Model & Schema   models.py, fields.py, vectors.py, schema/   |
+-------------------------------------------------------------+
| Query AST        query/expressions.py, ast.py, builder.py    |
+-------------------------------------------------------------+
| Protocol         protocol/opcodes.py, frames.py, codec.py    |
+-------------------------------------------------------------+
| Transport        transport/base.py, memory.py, tcp.py        |
+-------------------------------------------------------------+
| Hydration        hydration/hydrator.py                       |
+-------------------------------------------------------------+
```

## Public API (`aura.client`)

`Client` owns the async connection lifecycle and a `ModelRegistry`. It is an async
context manager. Dynamic attribute access (`client.User`) and `client.query(User)`
return a `QueryBuilder` bound to a model and an executor coroutine. The executor
compiles the builder's AST into a Query IR, wraps it in a protocol message, sends it
through the transport, and hydrates the response rows.

`Client` performs **no** network work at import time and none in `__init__`; all IO
happens in `connect()` / `close()` / request methods.

## Model & Schema layer

`AuraModelMeta` scans class annotations and `Field()` markers to build an immutable
`ModelSchema` (`schema/model_schema.py`). It installs a `FieldDescriptor` for each
field so that:

- `User.id` (class access) returns a `FieldReference`, an expression-tree leaf used
  by the query builder.
- `instance.id` (instance access) returns the stored Python value.

Schema generation is deterministic: field order follows declaration order and the
serialized schema dict has a stable key ordering.

## Query AST layer

`expressions.py` defines the typed expression tree (`FieldReference`, `Comparison`,
`BooleanOp`, `Not`, `FunctionCall`, `Literal`, `Param`, `VectorDistance`). Operators
on `FieldReference` (`==`, `!=`, `<`, `>`, `.in_()`, `.contains()`, `.asc()`,
`.desc()`) build nodes, they never produce strings.

`ast.py` defines query nodes (`SelectQuery`, `InsertQuery`, `UpdateQuery`,
`DeleteQuery`, `UpsertQuery`, `CountQuery`, `ExistsQuery`) each with `to_ir()`
producing the canonical Query IR dict.

`builder.py` is the fluent, immutable builder. Each chained call returns a new
builder with an updated AST. Terminal coroutines (`all`, `one`, `first`, `count`,
`exists`, `execute`) invoke the bound executor.

## Protocol layer

A frame has a fixed 32-byte header followed by an optional payload:

```
magic(4) version(1) opcode(1) flags(1) compression(1)
payload_length(4) transaction_id(8) request_id(8) header_crc32(4)
```

`codec.py` encodes/decodes frames deterministically, validates the magic bytes and
version, recomputes the header checksum, enforces the configured max payload length,
optionally zlib-compresses the payload, and optionally appends a CRC32 payload
checksum. Payload bodies are canonical (sorted-key) JSON, a deterministic,
debuggable representation wrapped inside the binary frame.

`messages.py` defines typed request/response bodies (ping, schema, query, mutation,
error) that serialize to/from payload dicts.

## Transport layer

`base.Transport` is the abstract async interface: `connect`, `close`, `request`
(send one frame, await the correlated response), and `is_connected`.

`memory.MemoryTransport` wraps a `ReferenceServer`, an in-process engine that
decodes incoming frames, executes them against an in-memory store (per-model tables
with primary-key indexes, filtering, ordering, pagination, vector nearest-neighbour,
and mutations), and encodes real response frames. It is the deterministic backend
for tests and examples.

`tcp.TCPTransport` speaks the same protocol over `asyncio` streams with a read loop
that correlates responses by request ID. It is structurally ready for a real AuraDB
server.

Streaming uses cursor/page opcodes (`CURSOR_FETCH`/`CURSOR_CLOSE`): the client opens a
server cursor and pulls one bounded page at a time, holding at most one page in memory.
The reference server materializes the result internally but hands out pages behind an
opaque cursor token, the same contract a real server cursor exposes (see
`docs/TRANSPORTS.md`).

## Hydration layer

`Hydrator` converts protocol result rows into model instances without invoking user
`__init__`, instead using a dedicated construction path and the optional
`__aura_post_load__` hook. It handles scalars, optionals, lists, document/dict
fields, nested models, relationship includes, vectors, partial-select rows, and
raises `AuraValidationError` on missing required fields.

## Cross-cutting

- **Errors** (`errors.py`): a structured taxonomy rooted at `AuraError` carrying a
  stable code, message, retry classification, and optional request ID.
- **Config** (`config.py`): DSN parsing and immutable config dataclasses for
  timeouts, retries, TLS, auth, pooling, and payload limits.
- **Observability** (`observability.py`): in-process metrics (incl. error counts) and a
  soft OpenTelemetry span bridge; see `docs/OBSERVABILITY.md`.
- **CLI** (`cli.py`): offline `aura version/doctor/schema compile/migrate diff`, exposed
  as the `aura` console script.

## Native acceleration boundary

Aura ships an optional compiled Rust/PyO3 fast path as an **optional
in-repository extra** (see [`NATIVE_ACCELERATION.md`](NATIVE_ACCELERATION.md)), not a separate package. A single adapter,
`aura._native`, sits in front of two hot, byte-exact boundaries and dispatches to the
compiled `aura_native` extension (`crates/aura_native`) when installed, or to an identical
pure-Python reference otherwise:

- `protocol/frames.py` + `protocol/codec.py`, frame header/payload CRC32.
- `vectors.py`, fixed-dimension vector validation and `Vector.pack()`/`from_bytes()`
  (little-endian f32).

The native path is a transparent speed optimisation: the public API and all observable
behaviour are identical with or without it, parity is enforced by tests, and
`AURA_DISABLE_NATIVE=1` forces the pure-Python path. The pure-Python implementation is the
complete, benchmarked baseline; no zero-copy behaviour is claimed, and the native path is not always faster.
