"""Unit tests for the native AuraDB backend: AWP 1 codec, IR translation, and
DSN routing. These tests need no server."""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, Vector
from aura.backends.auradb_native import (
    AuraDBNativeBackend,
    _collection_schema,
    _decode_fields,
    _translate_predicate,
    _translate_select,
)
from aura.backends.registry import backend_family, resolve_backend
from aura.config import TLSConfig, parse_dsn
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge
from aura.protocol.awp1 import AWPFrame, Opcode, decode_frame, decode_header, encode_frame


class Article(AuraModel):
    id: str = Field(primary_key=True)
    status: str = Field(index=True)
    title: str
    embedding: Vector[3]


# -- codec -------------------------------------------------------------------
def test_frame_roundtrips():
    frame = AWPFrame(opcode=int(Opcode.QUERY), payload=b'{"q":1}', request_id=42, transaction_id=7)
    decoded = decode_frame(encode_frame(frame))
    assert decoded.opcode == int(Opcode.QUERY)
    assert decoded.payload == b'{"q":1}'
    assert decoded.request_id == 42
    assert decoded.transaction_id == 7


def test_decode_header_rejects_bad_magic():
    raw = bytearray(encode_frame(AWPFrame(opcode=int(Opcode.PING))))
    raw[0] = ord("X")
    with pytest.raises(ValueError):
        decode_header(bytes(raw[:44]))


def test_decode_header_rejects_corrupt_checksum():
    raw = bytearray(encode_frame(AWPFrame(opcode=int(Opcode.PING))))
    raw[42] ^= 0xFF
    with pytest.raises(ValueError):
        decode_header(bytes(raw[:44]))


# -- schema translation ------------------------------------------------------
def test_collection_schema_translation():
    server = _collection_schema(Article.__aura_schema__.to_dict())
    assert server["name"] == "Article"
    by_name = {f["name"]: f for f in server["fields"]}
    assert by_name["id"]["primary_key"] is True
    assert by_name["id"]["field_type"] == {"kind": "string"}
    assert by_name["status"]["indexed"] is True
    assert by_name["embedding"]["field_type"] == {"kind": "vector", "dim": 3}


# -- predicate translation ---------------------------------------------------
def test_translate_compare_and_path():
    pred = (Article.status == "published").to_ir()
    assert _translate_predicate(pred) == {
        "type": "compare",
        "field": "status",
        "op": "eq",
        "value": "published",
    }


def test_translate_boolean_and():
    pred = ((Article.status == "a") & (Article.title == "b")).to_ir()
    out = _translate_predicate(pred)
    assert out["type"] == "and"
    assert len(out["filters"]) == 2


def test_translate_select_with_vector_and_limit():
    ir = {
        "operation": "select",
        "model": "Article",
        "filters": [(Article.status == "published").to_ir()],
        "vector": {"field": "embedding", "metric": "dot", "query": [1.0, 0.0, 0.0]},
        "limit": 5,
    }
    server = _translate_select(ir)
    assert server["collection"] == "Article"
    assert server["filter"]["op"] == "eq"
    assert server["vector"] == {
        "field": "embedding",
        "query": [1.0, 0.0, 0.0],
        "k": 5,
        "metric": "dot_product",
    }
    assert server["limit"] == 5


def test_translate_select_text_becomes_contains_text():
    ir = {"operation": "select", "model": "Article", "text": {"fields": ["body"], "query": "alpha"}}
    server = _translate_select(ir)
    assert server["filter"] == {"type": "contains_text", "field": "body", "query": "alpha"}


def test_decode_fields_unwraps_vector():
    assert _decode_fields({"embedding": {"$vector": [1.0, 2.0]}, "n": 3}) == {
        "embedding": [1.0, 2.0],
        "n": 3,
    }


# -- DSN routing -------------------------------------------------------------
def test_auradb_schemes_route_to_native_backend():
    assert backend_family("auradb://localhost:7171/db") == "auradb_native"
    assert backend_family("auradbs://localhost:7171/db") == "auradb_native"


def test_auradbs_enables_tls():
    config = parse_dsn("auradbs://localhost:7171/db")
    assert config.tls.enabled is True


def test_resolve_backend_builds_native_backend():
    metrics = Metrics()
    telemetry = _TelemetryBridge(TelemetryConfig.from_value(None))
    _config, backend = resolve_backend(
        "auradb://localhost:7171/db", metrics=metrics, telemetry=telemetry
    )
    assert isinstance(backend, AuraDBNativeBackend)
    assert backend.capabilities().native_protocol is True
    assert backend.capabilities().vector_search is True


def test_native_backend_tls_context_from_config():
    config = parse_dsn("auradbs://localhost:7171/db", tls=TLSConfig(enabled=True))
    backend = AuraDBNativeBackend(
        config, Metrics(), _TelemetryBridge(TelemetryConfig.from_value(None))
    )
    ctx = backend._ssl_context()
    assert ctx is not None
