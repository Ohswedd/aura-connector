"""Redis backend (redis.asyncio) — intentionally limited key-value/cache use.

Redis is a key-value store, not a relational or document database, and this backend says so.
It supports primary-key document access: insert/upsert as ``<Model>:<pk>`` JSON values, fetch
or delete by primary key, existence checks, prefix-scan listing, and a prefix count. It does
**not** support relational filtering, joins, graph traversal, or vector search; attempting
them raises :class:`~aura.errors.AuraBackendCapabilityError`. Schema creation is a no-op
metadata validation step. Use this backend for caching and key-value access patterns.

Integration tests are gated on ``AURA_TEST_REDIS_DSN``. The driver (``redis``) is imported
lazily; a missing driver raises :class:`~aura.errors.AuraDriverNotInstalledError`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from ..errors import (
    AuraBackendCapabilityError,
    AuraConnectionError,
    AuraConstraintError,
    AuraQueryError,
)
from ..observability import Metrics
from ..schema.migrations import MigrationPlan, diff_schemas
from ..schema.model_schema import ModelSchema
from .base import Backend, BackendResult
from .capabilities import BackendCapabilities
from .errors import import_driver

if TYPE_CHECKING:
    from ..models import AuraModel
    from .registry import BackendURL

__all__ = ["RedisBackend"]

_REDIS_CAPABILITIES = BackendCapabilities(
    name="redis",
    transactions=False,
    schema_create=True,  # no-op metadata validation
    schema_migrations=False,
    json_fields=True,
    relationships=False,
    graph_traversal=False,
    vector_search=False,
    hybrid_search=False,
    full_text_search=False,
    raw_queries=False,
    explain=False,
    server_side_cursors=False,
    native_protocol=False,
    document_queries=False,
    key_value=True,
)


class RedisBackend(Backend):
    """Async, limited Redis key-value backend over ``redis.asyncio``."""

    name = "redis"

    def __init__(self, url: BackendURL, metrics: Metrics) -> None:
        self._url = url
        self._metrics = metrics
        self._schemas: dict[str, ModelSchema] = {}
        self._client: Any = None

    def capabilities(self) -> BackendCapabilities:
        return _REDIS_CAPABILITIES

    # -- lifecycle ---------------------------------------------------------------
    async def connect(self) -> None:
        redis_asyncio = import_driver("redis.asyncio", extra="redis", backend="redis")
        self._client = redis_asyncio.from_url(self._url.raw, decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        return bool(await self._require_client().ping())

    def _require_client(self) -> Any:
        if self._client is None:
            raise AuraConnectionError("Redis backend is not connected")
        return self._client

    def _schema_for(self, model: str) -> ModelSchema:
        schema = self._schemas.get(model)
        if schema is not None:
            return schema
        from ..models import get_model

        registered = get_model(model)
        if registered is not None:
            return registered.__aura_schema__
        raise AuraQueryError(f"Model {model!r} is not registered with this backend")

    # -- schema (no-op metadata validation) --------------------------------------
    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for model in models:
            schema = model.__aura_schema__
            if schema.primary_key is None:
                raise AuraQueryError(
                    f"Redis backend requires a primary key on {schema.name!r}",
                    context={"model": schema.name},
                )
            self._schemas[schema.name] = schema

    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        client = self._require_client()
        for model in models:
            await self._delete_prefix(client, f"{model.__aura_schema__.name}:")

    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> MigrationPlan:
        old = {"models": [s.to_dict() for s in self._schemas.values()]}
        new = {"models": [m.__aura_schema__.to_dict() for m in models]}
        return diff_schemas(old, new)

    # -- execution ---------------------------------------------------------------
    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        operation = ir.get("operation", "select")
        model = str(ir["model"])
        schema = self._schema_for(model)
        pk_field = self._pk_field(schema)
        filters = ir.get("filters", [])
        pk_value = self._pk_filter(filters, pk_field)

        if operation in {"select", "count", "exists"}:
            if pk_value is not None:
                doc = await self._get(model, pk_value)
                docs = [doc] if doc is not None else []
            elif not filters:
                docs = await self._scan(model)
            else:
                raise AuraBackendCapabilityError(
                    "Redis backend supports only primary-key lookups, not relational filters",
                    context={"backend": self.name, "capability": "relational_filter"},
                )
            if operation == "count":
                return BackendResult(count=len(docs))
            if operation == "exists":
                return BackendResult(count=1 if docs else 0)
            docs = self._paginate(docs, ir)
            return BackendResult(rows=docs, count=len(docs))

        raise AuraBackendCapabilityError(
            f"Redis backend does not support the {operation!r} query",
            context={"backend": self.name, "operation": operation},
        )

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        operation = ir["operation"]
        model = str(ir["model"])
        schema = self._schema_for(model)
        pk_field = self._pk_field(schema)

        if operation == "insert":
            return await self._insert(model, schema, pk_field, ir)
        if operation == "upsert":
            return await self._upsert(model, schema, pk_field, ir)
        if operation in {"update", "delete"}:
            pk_value = self._pk_filter(ir.get("filters", []), pk_field)
            if pk_value is None:
                raise AuraBackendCapabilityError(
                    f"Redis {operation} requires a primary-key filter",
                    context={"backend": self.name, "capability": "relational_filter"},
                )
            if operation == "delete":
                deleted = await self._require_client().delete(self._key(model, pk_value))
                return BackendResult(affected=int(deleted))
            current = await self._get(model, pk_value)
            if current is None:
                return BackendResult(affected=0)
            current.update(self._normalize(schema, dict(ir.get("set", {}))))
            await self._set(model, pk_field, current)
            return BackendResult(affected=1)
        raise AuraQueryError(f"Unsupported mutation {operation!r}")

    async def _insert(
        self, model: str, schema: ModelSchema, pk_field: str, ir: dict[str, Any]
    ) -> BackendResult:
        on_conflict = ir.get("on_conflict")
        client = self._require_client()
        inserted: list[dict[str, Any]] = []
        for raw in ir.get("rows", []):
            doc = self._normalize(schema, dict(raw))
            pk_value = doc.get(pk_field)
            if pk_value is None:
                raise AuraConstraintError(
                    f"{model} insert is missing primary key {pk_field!r}",
                    context={"model": model},
                )
            key = self._key(model, pk_value)
            if await client.exists(key):
                if on_conflict == "ignore":
                    continue
                if on_conflict != "replace":
                    raise AuraConstraintError(
                        f"{model} with {pk_field}={pk_value!r} already exists",
                        context={"model": model},
                    )
            await client.set(key, json.dumps(doc))
            inserted.append(doc)
        return BackendResult(rows=inserted, affected=len(inserted))

    async def _upsert(
        self, model: str, schema: ModelSchema, pk_field: str, ir: dict[str, Any]
    ) -> BackendResult:
        key = self._normalize(schema, dict(ir.get("key", {})))
        values = self._normalize(schema, dict(ir.get("values", {})))
        merged = {**key, **values}
        pk_value = merged.get(pk_field)
        if pk_value is None:
            raise AuraConstraintError(
                f"upsert for {model} is missing primary key {pk_field!r}",
                context={"model": model},
            )
        existing = await self._get(model, pk_value)
        if existing is not None:
            existing.update(values)
            merged = existing
        await self._set(model, pk_field, merged)
        return BackendResult(rows=[merged], affected=1)

    async def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        model = str(ir["model"])
        schema = self._schema_for(model)
        pk_field = self._pk_field(schema)
        if self._pk_filter(ir.get("filters", []), pk_field) is not None or ir.get("filters"):
            for row in (await self.execute_query(ir)).rows:
                yield row
            return
        client = self._require_client()
        pattern = f"{model}:*"
        async for key in client.scan_iter(match=pattern, count=batch_size):
            value = await client.get(key)
            if value is not None:
                yield json.loads(value)

    # -- helpers -----------------------------------------------------------------
    def _pk_field(self, schema: ModelSchema) -> str:
        if schema.primary_key is None:  # pragma: no cover - guarded at create_schema
            raise AuraQueryError(f"{schema.name} has no primary key")
        return schema.primary_key

    def _pk_filter(self, filters: list[dict[str, Any]], pk_field: str) -> Any:
        """Return the primary-key value if ``filters`` is a single ``pk == value``, else None."""
        if len(filters) != 1:
            return None
        pred = filters[0]
        if pred.get("kind") != "compare" or pred.get("op") != "eq":
            return None
        left = pred.get("left", {})
        if left.get("kind") != "field" or left.get("field") != pk_field or left.get("path"):
            return None
        right = pred.get("right", {})
        return right.get("value") if right.get("kind") == "literal" else None

    def _key(self, model: str, pk_value: Any) -> str:
        return f"{model}:{pk_value}"

    async def _get(self, model: str, pk_value: Any) -> dict[str, Any] | None:
        value = await self._require_client().get(self._key(model, pk_value))
        if value is None:
            return None
        loaded: dict[str, Any] = json.loads(value)
        return loaded

    async def _set(self, model: str, pk_field: str, doc: dict[str, Any]) -> None:
        await self._require_client().set(self._key(model, doc[pk_field]), json.dumps(doc))

    async def _scan(self, model: str) -> list[dict[str, Any]]:
        client = self._require_client()
        docs: list[dict[str, Any]] = []
        async for key in client.scan_iter(match=f"{model}:*"):
            value = await client.get(key)
            if value is not None:
                docs.append(json.loads(value))
        return docs

    async def _delete_prefix(self, client: Any, prefix: str) -> None:
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)

    def _paginate(self, docs: list[dict[str, Any]], ir: dict[str, Any]) -> list[dict[str, Any]]:
        offset = int(ir.get("offset") or 0)
        if offset:
            docs = docs[offset:]
        limit = ir.get("limit")
        if limit is not None:
            docs = docs[: int(limit)]
        return docs

    def _normalize(self, schema: ModelSchema, doc: dict[str, Any]) -> dict[str, Any]:
        relationships = {r.name: r for r in schema.relationships}
        out: dict[str, Any] = {}
        for key, value in doc.items():
            rel = relationships.get(key)
            if rel is None:
                out[key] = value
                continue
            if rel.kind == "collection":
                continue
            if isinstance(value, dict):
                from ..models import get_model

                target = get_model(rel.target)
                pk = target.__aura_schema__.primary_key if target is not None else None
                out[key] = value.get(pk) if pk else None
            else:
                out[key] = value
        return out
