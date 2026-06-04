"""MongoDB backend (motor) — document-native and honest.

Queries compile to structured MongoDB filter/update documents — never to generated query
strings — so user values are always data, never code. Lists, documents, and vectors are
stored natively; to-one relationships are stored as the target's primary key. Features
MongoDB does not provide in a given deployment are reported truthfully: transactions require
a replica set (declared ``False`` here for the standalone default), and vector search
requires a configured vector index — both raise
:class:`~aura.errors.AuraBackendCapabilityError` rather than being emulated.

Integration tests are gated on ``AURA_TEST_MONGODB_DSN``. The driver (``motor``) is imported
lazily; a missing driver raises :class:`~aura.errors.AuraDriverNotInstalledError`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from ..errors import AuraBackendCapabilityError, AuraConnectionError, AuraQueryError
from ..observability import Metrics
from ..schema.migrations import MigrationPlan, diff_schemas
from ..schema.model_schema import ModelSchema
from .base import Backend, BackendResult
from .capabilities import BackendCapabilities
from .errors import import_driver

if TYPE_CHECKING:
    from ..models import AuraModel
    from .registry import BackendURL

__all__ = ["MongoDBBackend"]

_MONGODB_CAPABILITIES = BackendCapabilities(
    name="mongodb",
    transactions=False,  # requires a replica set; declared per-deployment, not assumed
    schema_create=True,
    schema_migrations=False,
    json_fields=True,
    relationships=True,
    graph_traversal=False,
    vector_search=False,  # requires an Atlas/`$vectorSearch` index; not assumed
    hybrid_search=False,
    full_text_search=False,
    raw_queries=False,
    explain=True,
    server_side_cursors=True,
    native_protocol=False,
    document_queries=True,
    key_value=False,
)

_COMPARE = {"lt": "$lt", "le": "$lte", "gt": "$gt", "ge": "$gte"}


class MongoDBBackend(Backend):
    """Async MongoDB backend over ``motor``."""

    name = "mongodb"

    def __init__(self, url: BackendURL, metrics: Metrics) -> None:
        self._url = url
        self._metrics = metrics
        self._schemas: dict[str, ModelSchema] = {}
        self._client: Any = None
        self._db: Any = None

    def capabilities(self) -> BackendCapabilities:
        return _MONGODB_CAPABILITIES

    # -- lifecycle ---------------------------------------------------------------
    async def connect(self) -> None:
        motor_asyncio = import_driver("motor.motor_asyncio", extra="mongodb", backend="mongodb")
        self._client = motor_asyncio.AsyncIOMotorClient(self._url.raw)
        database = self._url.database or "aura"
        self._db = self._client[database]

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None

    async def ping(self) -> bool:
        if self._db is None:
            raise AuraConnectionError("MongoDB backend is not connected")
        result = await self._db.command("ping")
        return bool(result.get("ok"))

    def _collection(self, model: str) -> Any:
        if self._db is None:
            raise AuraConnectionError("MongoDB backend is not connected")
        return self._db[model]

    def _schema_for(self, model: str) -> ModelSchema:
        schema = self._schemas.get(model)
        if schema is not None:
            return schema
        from ..models import get_model

        registered = get_model(model)
        if registered is not None:
            return registered.__aura_schema__
        raise AuraQueryError(f"Model {model!r} is not registered with this backend")

    # -- schema ------------------------------------------------------------------
    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for model in models:
            schema = model.__aura_schema__
            self._schemas[schema.name] = schema
            collection = self._collection(schema.name)
            for field in schema.fields:
                if field.unique:
                    await collection.create_index(field.name, unique=True)
                elif field.index:
                    await collection.create_index(field.name)

    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for model in models:
            await self._collection(model.__aura_schema__.name).drop()

    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> MigrationPlan:
        old = {"models": [s.to_dict() for s in self._schemas.values()]}
        new = {"models": [m.__aura_schema__.to_dict() for m in models]}
        return diff_schemas(old, new)

    # -- execution ---------------------------------------------------------------
    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        operation = ir.get("operation", "select")
        if operation == "traverse":
            raise AuraBackendCapabilityError(
                "Graph traversal is not supported by the MongoDB backend",
                context={"backend": self.name, "capability": "graph_traversal"},
            )
        self._reject_search(ir)
        model = str(ir["model"])
        collection = self._collection(model)
        query = self._filter(ir.get("filters", []))

        if operation == "count":
            return BackendResult(count=await collection.count_documents(query))
        if operation == "exists":
            found = await collection.count_documents(query, limit=1)
            return BackendResult(count=1 if found else 0)

        cursor = collection.find(query, self._projection(ir))
        sort = self._sort(ir.get("sort", []))
        if sort:
            cursor = cursor.sort(sort)
        if ir.get("offset"):
            cursor = cursor.skip(int(ir["offset"]))
        if ir.get("limit") is not None:
            cursor = cursor.limit(int(ir["limit"]))
        rows = [self._clean(doc) for doc in await cursor.to_list(length=None)]
        return BackendResult(rows=rows, count=len(rows))

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        operation = ir["operation"]
        model = str(ir["model"])
        collection = self._collection(model)
        schema = self._schema_for(model)

        if operation == "insert":
            docs = [self._normalize(schema, dict(r)) for r in ir.get("rows", [])]
            on_conflict = ir.get("on_conflict")
            return await self._insert(collection, schema, docs, on_conflict)
        if operation == "update":
            query = self._filter(ir.get("filters", []))
            assignments = self._normalize(schema, dict(ir.get("set", {})))
            result = await collection.update_many(query, {"$set": assignments})
            return BackendResult(affected=result.modified_count)
        if operation == "delete":
            query = self._filter(ir.get("filters", []))
            result = await collection.delete_many(query)
            return BackendResult(affected=result.deleted_count)
        if operation == "upsert":
            return await self._upsert(collection, schema, ir)
        raise AuraQueryError(f"Unsupported mutation {operation!r}")

    async def _insert(
        self,
        collection: Any,
        schema: ModelSchema,
        docs: list[dict[str, Any]],
        on_conflict: str | None,
    ) -> BackendResult:
        pk = schema.primary_key
        inserted: list[dict[str, Any]] = []
        for doc in docs:
            if pk is not None and pk in doc:
                doc.setdefault("_id", doc[pk])
            try:
                await collection.insert_one(doc)
                inserted.append(self._clean(doc))
            except Exception as exc:  # pragma: no cover - duplicate-key path is server-specific
                if self._is_duplicate(exc):
                    if on_conflict == "ignore":
                        continue
                    if on_conflict == "replace" and pk is not None:
                        await collection.replace_one({"_id": doc.get("_id")}, doc, upsert=True)
                        inserted.append(self._clean(doc))
                        continue
                    from ..errors import AuraConstraintError

                    raise AuraConstraintError(
                        f"Duplicate key inserting into {schema.name}",
                        context={"model": schema.name},
                    ) from exc
                raise
        return BackendResult(rows=inserted, affected=len(inserted))

    async def _upsert(
        self, collection: Any, schema: ModelSchema, ir: dict[str, Any]
    ) -> BackendResult:
        key = self._normalize(schema, dict(ir.get("key", {})))
        values = self._normalize(schema, dict(ir.get("values", {})))
        await collection.update_one(key, {"$set": {**key, **values}}, upsert=True)
        doc = await collection.find_one(key)
        return BackendResult(rows=[self._clean(doc)] if doc else [], affected=1)

    async def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        self._reject_search(ir)
        collection = self._collection(str(ir["model"]))
        cursor = collection.find(self._filter(ir.get("filters", [])), self._projection(ir))
        sort = self._sort(ir.get("sort", []))
        if sort:
            cursor = cursor.sort(sort)
        if ir.get("offset"):
            cursor = cursor.skip(int(ir["offset"]))
        if ir.get("limit") is not None:
            cursor = cursor.limit(int(ir["limit"]))
        cursor = cursor.batch_size(batch_size)
        async for doc in cursor:
            yield self._clean(doc)

    # -- IR -> Mongo translation -------------------------------------------------
    def _filter(self, filters: list[dict[str, Any]]) -> dict[str, Any]:
        if not filters:
            return {}
        clauses = [self._predicate(f) for f in filters]
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def _predicate(self, pred: dict[str, Any]) -> dict[str, Any]:
        kind = pred.get("kind")
        if kind == "bool":
            operands = [self._predicate(op) for op in pred["operands"]]
            return {"$and" if pred["op"] == "and" else "$or": operands}
        if kind == "not":
            return {"$nor": [self._predicate(pred["operand"])]}
        if kind == "compare":
            return self._comparison(pred)
        raise AuraQueryError(f"Unsupported predicate kind {kind!r}")

    def _comparison(self, pred: dict[str, Any]) -> dict[str, Any]:
        op = pred["op"]
        field = self._field(pred["left"])
        value = self._value(pred["right"])
        if op == "eq":
            return {field: value}
        if op == "ne":
            return {field: {"$ne": value}}
        if op in _COMPARE:
            return {field: {_COMPARE[op]: value}}
        if op == "in":
            return {field: {"$in": list(value) if value is not None else []}}
        if op == "not_in":
            return {field: {"$nin": list(value) if value is not None else []}}
        if op == "contains":
            return {field: {"$regex": re.escape(str(value))}}
        if op == "startswith":
            return {field: {"$regex": "^" + re.escape(str(value))}}
        if op == "endswith":
            return {field: {"$regex": re.escape(str(value)) + "$"}}
        if op == "like":
            pattern = re.escape(str(value)).replace("%", ".*").replace("_", ".")
            return {field: {"$regex": f"^{pattern}$"}}
        if op == "is_null":
            return {field: None}
        if op in {"is_not_null", "exists"}:
            return {field: {"$ne": None}}
        raise AuraQueryError(f"Unsupported comparison op {op!r}")

    def _field(self, field_ir: dict[str, Any]) -> str:
        name = str(field_ir["field"])
        path = field_ir.get("path") or []
        return ".".join([name, *[str(p) for p in path]])

    def _value(self, expr: dict[str, Any]) -> Any:
        if expr.get("kind") == "literal":
            return expr["value"]
        if expr.get("kind") == "param":
            raise AuraQueryError("Unbound query parameter encountered")
        raise AuraQueryError("Unsupported value expression in MongoDB filter")

    def _sort(self, sort: list[dict[str, Any]]) -> list[tuple[str, int]]:
        return [(str(term["field"]), -1 if term.get("direction") == "desc" else 1) for term in sort]

    def _projection(self, ir: dict[str, Any]) -> dict[str, int] | None:
        projection = ir.get("projection")
        if not projection:
            return {"_id": 0}
        fields = {str(name): 1 for name in projection}
        fields["_id"] = 0
        return fields

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
            out[key] = self._link_value(rel.target, value)
        return out

    def _link_value(self, target: str, value: Any) -> Any:
        if isinstance(value, dict):
            from ..models import get_model

            model = get_model(target)
            pk = model.__aura_schema__.primary_key if model is not None else None
            return value.get(pk) if pk else None
        return value

    @staticmethod
    def _clean(doc: dict[str, Any] | None) -> dict[str, Any]:
        if not doc:
            return {}
        out = dict(doc)
        out.pop("_id", None)
        return out

    @staticmethod
    def _is_duplicate(exc: Exception) -> bool:
        name = exc.__class__.__name__
        return name == "DuplicateKeyError" or "duplicate key" in str(exc).lower()

    def _reject_search(self, ir: dict[str, Any]) -> None:
        if "vector" in ir:
            raise AuraBackendCapabilityError(
                "Vector search requires a configured vector index and is not enabled",
                context={"backend": self.name, "capability": "vector_search"},
            )
        if "text" in ir:
            raise AuraBackendCapabilityError(
                "Full-text/hybrid search is not enabled on the MongoDB backend",
                context={"backend": self.name, "capability": "full_text_search"},
            )
