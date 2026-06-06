"""Native AuraDB backend: speaks the Aura Wire Protocol version 1 to a real
AuraDB single-node server over TCP or TLS.

This backend is selected by the ``auradb://`` (plaintext) and ``auradbs://`` (TLS)
DSN schemes. It owns its socket, performs the AWP 1 handshake (with optional
static-token authentication), and translates the connector's canonical Query IR
into the AuraDB server's Query IR and back. It is distinct from the legacy
``aura://`` AuraDB backend, which targets the in-process reference protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from ..config import ClientConfig, TokenAuth
from ..errors import (
    AuraAuthenticationError,
    AuraBackendCapabilityError,
    AuraConnectionError,
    AuraConstraintError,
    AuraNotFoundError,
    AuraNotLeaderError,
    AuraProtocolError,
    AuraQueryError,
    AuraSchemaError,
    AuraServerError,
)
from ..protocol.awp1 import (
    HEADER_LEN,
    AWPFrame,
    Opcode,
    decode_frame,
    decode_header,
    encode_frame,
)
from .base import Backend, BackendResult
from .capabilities import BackendCapabilities

if TYPE_CHECKING:
    from ..models import AuraModel
    from ..observability import Metrics, _TelemetryBridge
    from ..schema.migrations import MigrationPlan

__all__ = ["AuraDBNativeBackend"]

# Server ErrorCode (snake_case) to connector exception class.
_SERVER_ERROR_MAP = {
    "unauthenticated": AuraAuthenticationError,
    "invalid_credentials": AuraAuthenticationError,
    "not_found": AuraNotFoundError,
    "unique_violation": AuraConstraintError,
    "conflict": AuraConstraintError,
    "schema_violation": AuraConstraintError,
    "invalid_request": AuraQueryError,
    "not_leader": AuraNotLeaderError,
    "protocol": AuraProtocolError,
    "corruption": AuraServerError,
    "storage": AuraServerError,
    "unsupported": AuraBackendCapabilityError,
    "config": AuraServerError,
    "io": AuraServerError,
    "limit_exceeded": AuraQueryError,
    "internal": AuraServerError,
}

# Connector field-type label to AuraDB FieldType kind.
_TYPE_KIND = {
    "int": "int",
    "float": "float",
    "str": "string",
    "bool": "bool",
    "bytes": "bytes",
    "datetime": "timestamp",
    "date": "timestamp",
}

# Connector comparison operator to AuraDB compare operator.
_COMPARE_OP = {
    "eq": "eq",
    "ne": "ne",
    "lt": "lt",
    "le": "lte",
    "lte": "lte",
    "gt": "gt",
    "ge": "gte",
    "gte": "gte",
    "in": "in",
}

_VECTOR_METRIC = {"cosine": "cosine", "euclidean": "euclidean", "dot": "dot_product"}


def _error_from_payload(payload: dict[str, Any]) -> Exception:
    code = str(payload.get("code", "internal"))
    message = str(payload.get("message", "server error"))
    if code == "not_leader":
        # The multi-node preview enriches the error with structured leader-routing
        # fields (top-level and/or nested under ``not_leader``); preserve them so
        # callers can redirect without parsing the message.
        retryable = payload.get("retryable")
        return AuraNotLeaderError.from_server_payload(
            message,
            code=code,
            retryable=None if retryable is None else bool(retryable),
            payload=payload,
        )
    cls = _SERVER_ERROR_MAP.get(code, AuraServerError)
    return cls(message, code=code)


def _field_type(fs: dict[str, Any]) -> dict[str, Any]:
    if fs.get("type") == "vector" or fs.get("vector_dim") is not None:
        return {"kind": "vector", "dim": int(fs["vector_dim"])}
    if fs.get("container") == "dict" or fs.get("type") == "document":
        return {"kind": "document"}
    kind = _TYPE_KIND.get(str(fs.get("type")))
    if kind is None:
        raise AuraSchemaError(
            f"AuraDB backend cannot map field type {fs.get('type')!r} for field {fs.get('name')!r}"
        )
    return {"kind": kind}


def _collection_schema(model: dict[str, Any]) -> dict[str, Any]:
    """Translate a connector ModelSchema dict into an AuraDB CollectionSchema dict."""
    fields = [
        {
            "name": fs["name"],
            "field_type": _field_type(fs),
            "primary_key": bool(fs.get("primary_key")),
            "unique": bool(fs.get("unique")),
            "nullable": bool(fs.get("nullable")),
            "indexed": bool(fs.get("index")),
        }
        for fs in model.get("fields", [])
    ]
    relationships = []
    for rel in model.get("relationships", []):
        on_delete = rel.get("on_delete")
        on_delete = "set_null" if on_delete in {"set_null", "setnull"} else "restrict"
        relationships.append(
            {
                "name": rel["name"],
                "target": rel["target"],
                "cardinality": "to_many" if rel.get("kind") == "collection" else "to_one",
                "on_delete": on_delete,
            }
        )
    return {"name": model["name"], "fields": fields, "relationships": relationships}


def _field_path(node: dict[str, Any]) -> str:
    field = node.get("field", "")
    path = node.get("path") or []
    return ".".join([field, *path]) if path else field


def _literal(node: dict[str, Any]) -> Any:
    if node.get("kind") == "literal":
        return node.get("value")
    if node.get("kind") == "param":
        raise AuraQueryError("AuraDB backend does not support parameter references in filters")
    return node.get("value")


def _translate_predicate(pred: dict[str, Any]) -> dict[str, Any]:
    kind = pred.get("kind")
    if kind == "bool":
        return {
            "type": pred["op"],  # "and" / "or"
            "filters": [_translate_predicate(p) for p in pred.get("operands", [])],
        }
    if kind == "not":
        return {"type": "not", "filter": _translate_predicate(pred["operand"])}
    if kind == "compare":
        op = str(pred.get("op", ""))
        left = pred.get("left", {})
        field = _field_path(left)
        if op in {"is_null"}:
            return {"type": "not", "filter": {"type": "exists", "field": field}}
        if op in {"is_not_null", "exists"}:
            return {"type": "exists", "field": field}
        if op == "contains":
            return {"type": "contains", "field": field, "substring": str(_literal(pred["right"]))}
        if op == "not_in":
            inner = {
                "type": "compare",
                "field": field,
                "op": "in",
                "value": _literal(pred["right"]),
            }
            return {"type": "not", "filter": inner}
        server_op = _COMPARE_OP.get(op)
        if server_op is None:
            raise AuraBackendCapabilityError(
                f"AuraDB backend does not support the {op!r} filter operator"
            )
        return {
            "type": "compare",
            "field": field,
            "op": server_op,
            "value": _literal(pred["right"]),
        }
    raise AuraQueryError(f"unsupported filter node {kind!r}")


def _combine(filters: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return {"type": "and", "filters": filters}


def _translate_filters(ir: dict[str, Any]) -> dict[str, Any] | None:
    return _combine([_translate_predicate(p) for p in ir.get("filters", [])])


def _translate_select(ir: dict[str, Any]) -> dict[str, Any]:
    server: dict[str, Any] = {"collection": ir["model"]}
    filters = [_translate_predicate(p) for p in ir.get("filters", [])]

    text = ir.get("text")
    if text:
        text_filters = [
            {"type": "contains_text", "field": f, "query": text["query"]}
            for f in text.get("fields", [])
        ]
        combined_text = _combine(text_filters)
        if combined_text is not None:
            filters.append(combined_text)

    combined = _combine(filters)
    if combined is not None:
        server["filter"] = combined

    vector = ir.get("vector")
    if vector:
        metric = _VECTOR_METRIC.get(vector.get("metric", "cosine"), "cosine")
        server["vector"] = {
            "field": vector["field"],
            "query": list(vector.get("query", [])),
            "k": int(ir.get("limit") or 10),
            "metric": metric,
        }

    sort = ir.get("sort")
    if sort:
        server["order_by"] = [
            {"field": s["field"], "desc": s.get("direction") == "desc"} for s in sort
        ]
    if ir.get("limit") is not None:
        server["limit"] = int(ir["limit"])
    if ir.get("offset") is not None:
        server["offset"] = int(ir["offset"])
    if ir.get("projection"):
        server["projection"] = list(ir["projection"])
    if ir.get("include"):
        server["includes"] = [inc["link"] for inc in ir["include"]]
    return server


def _decode_value(value: Any) -> Any:
    """Convert AuraDB extension-encoded JSON values back into plain Python."""
    if isinstance(value, dict):
        if len(value) == 1:
            if "$vector" in value:
                return list(value["$vector"])
            if "$timestamp" in value:
                return value["$timestamp"]
            if "$bytes" in value:
                return bytes(value["$bytes"])
        return {k: _decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_value(v) for v in value]
    return value


def _decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: _decode_value(v) for k, v in fields.items()}


class AuraDBNativeBackend(Backend):
    """A backend that speaks AWP 1 to an AuraDB single-node server."""

    name = "auradb"

    def __init__(
        self,
        config: ClientConfig,
        metrics: Metrics,
        telemetry: _TelemetryBridge,
    ) -> None:
        self._config = config
        self._metrics = metrics
        self._telemetry = telemetry
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._vector_fields: dict[str, set[str]] = {}
        # Maps the client-side transaction id to the server-assigned one.
        self._txn_map: dict[int, int] = {}

    # -- lifecycle ---------------------------------------------------------------
    def _token(self) -> str | None:
        auth = self._config.auth
        if auth is None:
            return None
        if isinstance(auth, TokenAuth):
            return auth.token
        raise AuraAuthenticationError(
            "AuraDB supports static-token authentication; use TokenAuth(token=...)"
        )

    def _ssl_context(self) -> ssl.SSLContext | None:
        tls = self._config.tls
        if not tls.enabled:
            return None
        ctx = ssl.create_default_context(cafile=tls.ca_cert_path)
        ctx.check_hostname = tls.verify_hostname
        if not tls.verify_hostname:
            ctx.verify_mode = ssl.CERT_NONE
        if tls.client_cert_path and tls.client_key_path:
            ctx.load_cert_chain(tls.client_cert_path, tls.client_key_path)
        return ctx

    async def connect(self) -> None:
        if self._writer is not None:
            return
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._config.host,
                self._config.port,
                ssl=self._ssl_context(),
                server_hostname=self._config.host if self._config.tls.enabled else None,
            )
        except OSError as exc:
            raise AuraConnectionError(
                f"could not connect to {self._config.address}: {exc}"
            ) from exc
        await self._handshake()

    async def _handshake(self) -> None:
        token = self._token()
        payload: dict[str, Any] = {"client_version": "aura-connector", "protocol_version": 1}
        if token is not None:
            payload["auth_token"] = token
        opcode, ack = await self._request(Opcode.HELLO, payload)
        if opcode != Opcode.HELLO_ACK:
            raise AuraProtocolError("server did not acknowledge the handshake")
        if ack.get("auth_required") and not ack.get("authenticated"):
            if token is None:
                raise AuraAuthenticationError("server requires authentication; provide a token")
            op2, _ = await self._request(Opcode.AUTH, {"token": token})
            if op2 != Opcode.AUTH_RESULT:
                raise AuraAuthenticationError("authentication failed")

    async def close(self) -> None:
        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, ssl.SSLError):
                await writer.wait_closed()

    async def ping(self) -> bool:
        opcode, _ = await self._request(Opcode.PING, None)
        return opcode == Opcode.PONG

    # -- wire --------------------------------------------------------------------
    async def _request(self, opcode: int, payload_obj: Any, txn_id: int = 0) -> tuple[int, Any]:
        if self._writer is None or self._reader is None:
            raise AuraConnectionError("backend is not connected")
        body = b"" if payload_obj is None else json.dumps(payload_obj).encode("utf-8")
        self._req_id += 1
        frame = AWPFrame(
            opcode=int(opcode), payload=body, request_id=self._req_id, transaction_id=txn_id
        )
        async with self._lock:
            self._writer.write(encode_frame(frame))
            await self._writer.drain()
            header = await self._reader.readexactly(HEADER_LEN)
            _, payload_len, trailer = decode_header(header)
            rest = await self._reader.readexactly(payload_len + trailer)
        response = decode_frame(header + rest)
        decoded = json.loads(response.payload) if response.payload else None
        if response.opcode == Opcode.ERROR:
            raise _error_from_payload(decoded or {})
        return response.opcode, decoded

    # -- schema ------------------------------------------------------------------
    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for model in models:
            model_dict = model.__aura_schema__.to_dict()
            self._vector_fields[model_dict["name"]] = {
                fs["name"]
                for fs in model_dict["fields"]
                if fs.get("type") == "vector" or fs.get("vector_dim") is not None
            }
            await self._request(Opcode.SCHEMA_CREATE, _collection_schema(model_dict))

    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for model in models:
            await self._request(Opcode.SCHEMA_DROP, {"name": model.__name__})

    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> MigrationPlan:
        # Live migration application is not supported; the server exposes a
        # migration-impact estimate but not in-place apply.
        self._require("schema_migrations")
        raise AuraBackendCapabilityError("auradb backend does not apply live migrations")

    async def server_schema(self) -> list[dict[str, Any]]:
        _, schemas = await self._request(Opcode.SCHEMA_LIST, None)
        return list(schemas or [])

    # -- execution ---------------------------------------------------------------
    def _encode_fields(self, model: str, fields: dict[str, Any]) -> dict[str, Any]:
        vectors = self._vector_fields.get(model, set())
        out: dict[str, Any] = {}
        for key, value in fields.items():
            if key in vectors and isinstance(value, list):
                out[key] = {"$vector": value}
            else:
                out[key] = value
        return out

    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        op = ir["operation"]
        # AuraDB v0.2.0 honors the frame transaction id on reads, returning the
        # transaction view (committed state plus the transaction's own staged
        # writes, minus its staged deletes). Route reads through the server-side
        # transaction id so reads inside a transaction see its uncommitted work.
        stxid = self._txn_map.get(txid, 0) if txid else 0
        if op == "select":
            _, page = await self._request(
                Opcode.QUERY, {"query": "find", **_translate_select(ir)}, stxid
            )
            rows = [_decode_fields(r["fields"]) for r in page.get("rows", [])]
            metadata: dict[str, Any] = {}
            if page.get("cursor_id") is not None:
                metadata["cursor"] = page["cursor_id"]
                metadata["has_more"] = True
            return BackendResult(rows=rows, count=len(rows), metadata=metadata)
        if op == "count":
            payload = {
                "query": "count",
                "collection": ir["model"],
                "filter": _translate_filters(ir),
            }
            _, result = await self._request(Opcode.QUERY, payload, stxid)
            return BackendResult(count=int(result["count"]))
        if op == "exists":
            payload = {
                "query": "exists",
                "collection": ir["model"],
                "filter": _translate_filters(ir),
            }
            _, result = await self._request(Opcode.QUERY, payload, stxid)
            return BackendResult(count=1 if result["exists"] else 0)
        raise AuraBackendCapabilityError(f"auradb backend does not support read operation {op!r}")

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        op = ir["operation"]
        model = ir["model"]
        stxid = self._txn_map.get(txid, 0) if txid else 0
        if op == "insert":
            rows = ir.get("rows", [])
            on_conflict = ir.get("on_conflict")
            if on_conflict == "replace" and len(rows) == 1:
                mutation = {
                    "mutation": "upsert",
                    "collection": model,
                    "fields": self._encode_fields(model, rows[0]),
                }
            elif len(rows) == 1:
                mutation = {
                    "mutation": "insert",
                    "collection": model,
                    "fields": self._encode_fields(model, rows[0]),
                }
            else:
                mutation = {
                    "mutation": "bulk_insert",
                    "collection": model,
                    "records": [self._encode_fields(model, r) for r in rows],
                }
            _, result = await self._request(Opcode.MUTATE, mutation, stxid)
            return self._mutation_result(result)
        if op == "update":
            mutation = {
                "mutation": "update",
                "collection": model,
                "filter": _translate_filters(ir),
                "set": self._encode_fields(model, ir.get("set", {})),
            }
            _, result = await self._request(Opcode.MUTATE, mutation, stxid)
            return self._mutation_result(result)
        if op == "delete":
            mutation = {
                "mutation": "delete",
                "collection": model,
                "filter": _translate_filters(ir),
            }
            _, result = await self._request(Opcode.MUTATE, mutation, stxid)
            return self._mutation_result(result)
        if op == "upsert":
            fields = {**ir.get("key", {}), **ir.get("values", {})}
            mutation = {
                "mutation": "upsert",
                "collection": model,
                "fields": self._encode_fields(model, fields),
            }
            _, result = await self._request(Opcode.MUTATE, mutation, stxid)
            res = self._mutation_result(result)
            # The upsert path in the client expects the resulting row.
            res.rows = [_decode_fields(fields)]
            return res
        raise AuraBackendCapabilityError(f"auradb backend does not support mutation {op!r}")

    def _mutation_result(self, result: dict[str, Any]) -> BackendResult:
        affected = (
            int(result.get("inserted", 0))
            + int(result.get("updated", 0))
            + int(result.get("deleted", 0))
        )
        return BackendResult(affected=affected)

    async def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        # Use the server-side transaction id (not the client-side one) so a
        # cursor opened inside a transaction is planned and paged against that
        # transaction's view on the server.
        stxid = self._txn_map.get(txid, 0) if txid else 0
        _, page = await self._request(
            Opcode.QUERY, {"query": "find", **_translate_select(ir)}, stxid
        )
        while True:
            for row in page.get("rows", []):
                yield _decode_fields(row["fields"])
            cursor = page.get("cursor_id")
            if cursor is None:
                break
            _, page = await self._request(
                Opcode.CURSOR_FETCH, {"cursor_id": cursor, "limit": batch_size}, stxid
            )

    # -- transactions ------------------------------------------------------------
    async def begin(self, txid: int, isolation: str) -> None:
        _, result = await self._request(Opcode.TXN_BEGIN, None)
        # The server assigns its own transaction id; map the client id to it so
        # subsequent staged mutations reference the right server-side transaction.
        self._txn_map[txid] = int((result or {}).get("txn_id", 0))

    async def commit(self, txid: int, isolation: str) -> None:
        stxid = self._txn_map.pop(txid, 0)
        await self._request(Opcode.TXN_COMMIT, None, stxid)

    async def rollback(self, txid: int, isolation: str) -> None:
        stxid = self._txn_map.pop(txid, 0)
        await self._request(Opcode.TXN_ROLLBACK, None, stxid)

    # -- introspection -----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name="auradb",
            transactions=True,
            schema_create=True,
            schema_migrations=False,
            json_fields=True,
            relationships=True,
            graph_traversal=False,
            vector_search=True,
            hybrid_search=False,
            full_text_search=True,
            raw_queries=False,
            explain=True,
            server_side_cursors=True,
            native_protocol=True,
            document_queries=True,
            key_value=False,
        )
