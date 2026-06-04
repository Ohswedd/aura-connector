# Aura Wire Protocol

The client speaks a deterministic binary frame protocol. A frame is a fixed 32-byte
header, an optional payload, and an optional trailing payload checksum.

## Frame header (big-endian)

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | magic (`AURA`) |
| 4 | 1 | version |
| 5 | 1 | opcode |
| 6 | 1 | flags |
| 7 | 1 | compression marker |
| 8 | 4 | payload length |
| 12 | 8 | transaction id |
| 20 | 8 | request id |
| 28 | 4 | header checksum (CRC32 of bytes 0..28) |

If the `PAYLOAD_CHECKSUM` flag is set, a 4-byte CRC32 of the (possibly compressed) body
follows the payload.

The header and payload CRC32 (ISO-HDLC, identical to `zlib.crc32`) are computed through
the `aura._native` adapter: the optional in-repository `aura_native` extension when it is
installed, or an identical pure-Python implementation otherwise. The choice of backend
never changes the bytes on the wire. See `docs/NATIVE_ACCELERATION.md`.

## Opcodes

`HANDSHAKE`/`HANDSHAKE_ACK`, `PING`/`PONG`, `QUERY`/`QUERY_RESULT`,
`MUTATION`/`MUTATION_RESULT`, `SCHEMA`/`SCHEMA_RESULT`, `ERROR`,
`STREAM_DATA`/`STREAM_END`, and the transaction control opcodes
`BEGIN_TX`/`COMMIT_TX`/`ROLLBACK_TX`/`TX_ACK`.

## Flags and compression

- Flags: `COMPRESSED`, `PAYLOAD_CHECKSUM`, `END_OF_STREAM`, `MORE_FRAMES`.
- Compression markers: `NONE`, `ZLIB`.

## Payload bodies

Bodies are canonical (sorted-key) JSON wrapped inside the binary frame, deterministic
and debuggable (allowed by the development contract). The body shape is defined by typed
message classes in `aura.protocol.messages` (`QueryRequest`, `QueryResultBody`,
`MutationRequest`, `ErrorBody`, …). The frame body could be swapped for another encoding
later without changing the frame contract; the version field governs evolution.

## Determinism and safety

`encode_frame` is byte-for-byte deterministic for a given frame and options, golden
tests pin the format (`tests/unit/test_protocol_golden.py`). The decoder validates,
and fails closed on:

- wrong magic bytes (`AuraProtocolError`),
- incompatible version (`AuraProtocolVersionError`),
- header checksum mismatch,
- payload length exceeding the configured limit,
- payload checksum mismatch,
- unknown opcode or compression marker.

## Usage

```python
from aura.protocol import Frame, Opcode, encode_frame, decode_frame, encode_body
from aura.protocol.messages import QueryRequest

frame = Frame(
    opcode=Opcode.QUERY,
    payload=encode_body(QueryRequest(ir={"operation": "select", "model": "User"}).to_payload()),
    request_id=1,
)
raw = encode_frame(frame, payload_checksum=True)
decoded, consumed = decode_frame(raw)
```

`try_decode_frame` returns `(None, 0)` when the buffer does not yet contain a complete
frame, which the TCP transport uses for incremental stream parsing.
