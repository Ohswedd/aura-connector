"""In-memory reference server and transport.

The :class:`ReferenceServer` is a real, in-process implementation of the server side
of the protocol: it decodes frames, executes Query IR against an in-memory store
(with filtering, ordering, pagination, vector nearest-neighbour, hybrid scoring,
includes, mutations, and transactions), and encodes genuine response frames. The
:class:`MemoryTransport` drives it through the actual codec, so tests and examples
exercise the same protocol + hydration path as a network transport.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from ..errors import (
    AuraConnectionError,
    AuraConstraintError,
    AuraError,
    AuraQueryError,
)
from ..protocol.codec import decode_frame, encode_frame
from ..protocol.frames import Frame
from ..protocol.messages import (
    CursorAck,
    CursorClose,
    CursorFetch,
    ErrorBody,
    HandshakeAck,
    MutationRequest,
    MutationResultBody,
    PingRequest,
    PongResponse,
    QueryRequest,
    QueryResultBody,
    SchemaResponse,
    TxAck,
    decode_body,
    encode_body,
)
from ..protocol.opcodes import PROTOCOL_VERSION, Opcode
from .base import Transport

__all__ = ["MemoryTransport", "ReferenceServer"]

Row = dict[str, Any]
Table = dict[Any, Row]
Store = dict[str, Table]


class ReferenceServer:
    """A deterministic in-memory Aura server."""

    def __init__(self) -> None:
        self._tables: Store = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._tx_overlays: dict[int, Store] = {}
        # Open server cursors: token -> remaining materialized rows + page size.
        self._cursors: dict[str, dict[str, Any]] = {}
        self._cursor_seq = 0

    # -- schema registration -----------------------------------------------------
    def load_schema(self, models: list[dict[str, Any]]) -> None:
        for model in models:
            self._schemas[model["name"]] = model
            self._tables.setdefault(model["name"], {})

    def primary_key_field(self, model: str) -> str:
        schema = self._schemas.get(model)
        if schema and schema.get("primary_key"):
            return str(schema["primary_key"])
        return "id"

    def _relationships(self, model: str) -> list[dict[str, Any]]:
        schema = self._schemas.get(model)
        return list(schema.get("relationships", [])) if schema else []

    # -- frame entrypoint --------------------------------------------------------
    def feed(self, data: bytes, *, max_payload_bytes: int = 64 * 1024 * 1024) -> bytes:
        """Decode one request frame, process it, and return an encoded response."""
        frame, _ = decode_frame(data, max_payload_bytes=max_payload_bytes)
        response = self.handle(frame)
        return encode_frame(response, max_payload_bytes=max_payload_bytes, payload_checksum=True)

    def handle(self, frame: Frame) -> Frame:
        try:
            return self._dispatch(frame)
        except AuraError as exc:
            body = ErrorBody(
                code=exc.code, message=exc.message, retryable=exc.retryable, context=exc.context
            )
            return Frame(
                opcode=Opcode.ERROR,
                payload=encode_body(body.to_payload()),
                request_id=frame.request_id,
                transaction_id=frame.transaction_id,
            )

    def _dispatch(self, frame: Frame) -> Frame:
        payload = decode_body(frame.payload)
        opcode = frame.opcode

        if opcode is Opcode.HANDSHAKE:
            ack = HandshakeAck(
                server_version=PROTOCOL_VERSION,
                features=["query", "vector", "tx"],
                compression=["zlib"],
            )
            return self._reply(frame, Opcode.HANDSHAKE_ACK, ack.to_payload())

        if opcode is Opcode.PING:
            ping = PingRequest.from_payload(payload)
            pong = PongResponse(nonce=ping.nonce, server_version=PROTOCOL_VERSION)
            return self._reply(frame, Opcode.PONG, pong.to_payload())

        if opcode is Opcode.SCHEMA:
            models = payload.get("models")
            if models:
                self.load_schema(list(models))
            response = SchemaResponse(models=list(self._schemas.values()))
            return self._reply(frame, Opcode.SCHEMA_RESULT, response.to_payload())

        if opcode is Opcode.BEGIN_TX:
            self._tx_overlays[frame.transaction_id] = copy.deepcopy(self._tables)
            return self._reply(
                frame, Opcode.TX_ACK, TxAck(frame.transaction_id, "begun").to_payload()
            )

        if opcode is Opcode.COMMIT_TX:
            overlay = self._tx_overlays.pop(frame.transaction_id, None)
            if overlay is not None:
                self._tables = overlay
            return self._reply(
                frame, Opcode.TX_ACK, TxAck(frame.transaction_id, "committed").to_payload()
            )

        if opcode is Opcode.ROLLBACK_TX:
            self._tx_overlays.pop(frame.transaction_id, None)
            return self._reply(
                frame, Opcode.TX_ACK, TxAck(frame.transaction_id, "rolled_back").to_payload()
            )

        if opcode is Opcode.QUERY:
            query_request = QueryRequest.from_payload(payload)
            query_result = self._execute_query(query_request.ir, frame.transaction_id)
            return self._reply(frame, Opcode.QUERY_RESULT, query_result.to_payload())

        if opcode is Opcode.CURSOR_FETCH:
            fetch = CursorFetch.from_payload(payload)
            body = self._cursor_fetch(fetch.cursor, fetch.batch_size)
            return self._reply(frame, Opcode.QUERY_RESULT, body.to_payload())

        if opcode is Opcode.CURSOR_CLOSE:
            close = CursorClose.from_payload(payload)
            existed = self._cursors.pop(close.cursor, None) is not None
            cursor_ack = CursorAck(cursor=close.cursor, status="closed" if existed else "absent")
            return self._reply(frame, Opcode.CURSOR_ACK, cursor_ack.to_payload())

        if opcode is Opcode.MUTATION:
            mutation_request = MutationRequest.from_payload(payload)
            mutation_result = self._execute_mutation(mutation_request.ir, frame.transaction_id)
            return self._reply(frame, Opcode.MUTATION_RESULT, mutation_result.to_payload())

        raise AuraQueryError(f"Unsupported opcode {opcode!r}")

    def _reply(self, frame: Frame, opcode: Opcode, payload: dict[str, Any]) -> Frame:
        return Frame(
            opcode=opcode,
            payload=encode_body(payload),
            request_id=frame.request_id,
            transaction_id=frame.transaction_id,
        )

    # -- store access ------------------------------------------------------------
    def _store(self, txid: int) -> Store:
        if txid and txid in self._tx_overlays:
            return self._tx_overlays[txid]
        return self._tables

    def _table(self, store: Store, model: str) -> Table:
        return store.setdefault(model, {})

    # -- query execution ---------------------------------------------------------
    def _execute_query(self, ir: dict[str, Any], txid: int) -> QueryResultBody:
        operation = ir.get("operation", "select")
        store = self._store(txid)

        if operation == "raw":
            return self._execute_raw(ir, store)
        if operation == "traverse":
            return self._execute_traverse(ir, store)
        if operation not in {"select", "count", "exists"}:
            raise AuraQueryError(f"Unsupported query operation {operation!r}")

        model = ir.get("model")
        if not model:
            raise AuraQueryError("Query is missing a target model")
        table = self._table(store, model)
        rows = list(table.values())

        filters = ir.get("filters", [])
        rows = [r for r in rows if self._matches_all(r, filters)]

        if operation == "count":
            return QueryResultBody(rows=[], count=len(rows))
        if operation == "exists":
            return QueryResultBody(rows=[], count=1 if rows else 0)

        scored = self._apply_search(ir, rows)

        if not _is_ranked(ir):
            scored = self._apply_order(ir.get("sort", []), scored)

        offset = ir.get("offset")
        limit = ir.get("limit")
        if offset:
            scored = scored[offset:]
        if limit is not None:
            scored = scored[:limit]

        ranked = _is_ranked(ir)
        result_rows = [
            self._project(model, store, ir, row, bundle, (idx + (offset or 0)) if ranked else None)
            for idx, (row, bundle) in enumerate(scored)
        ]

        cursor_request = ir.get("cursor")
        if cursor_request:
            batch_size = int(cursor_request.get("batch_size", 1000)) or 1000
            return self._open_cursor(model, result_rows, batch_size)
        return QueryResultBody(rows=result_rows, count=len(result_rows))

    # -- server cursors (real page-by-page streaming) ----------------------------
    def _open_cursor(self, model: str, rows: list[Row], batch_size: int) -> QueryResultBody:
        """Return the first page and, if more rows remain, register a server cursor.

        The reference backend materializes the result set in memory, but it only ever
        hands the client one bounded page at a time and holds the remainder server-side
        behind an opaque cursor token — the same contract a real AuraDB server cursor
        exposes. The client therefore holds at most ``batch_size`` rows at once.
        """
        page = rows[:batch_size]
        remainder = rows[batch_size:]
        if remainder:
            self._cursor_seq += 1
            token = f"cur-{self._cursor_seq}"
            self._cursors[token] = {
                "rows": remainder,
                "model": model,
                "batch_size": batch_size,
            }
            metadata = {"cursor": token, "has_more": True}
        else:
            metadata = {"cursor": None, "has_more": False}
        return QueryResultBody(rows=page, count=len(page), metadata=metadata)

    def _cursor_fetch(self, token: str, batch_size: int) -> QueryResultBody:
        state = self._cursors.get(token)
        if state is None:
            raise AuraQueryError(
                f"Unknown or expired cursor {token!r}",
                context={"cursor": token},
            )
        size = batch_size or int(state["batch_size"]) or 1000
        remaining: list[Row] = state["rows"]
        page = remaining[:size]
        state["rows"] = remaining[size:]
        if state["rows"]:
            metadata = {"cursor": token, "has_more": True}
        else:
            del self._cursors[token]
            metadata = {"cursor": None, "has_more": False}
        return QueryResultBody(rows=page, count=len(page), metadata=metadata)

    @property
    def open_cursor_count(self) -> int:
        """Number of server cursors currently held open (for tests/diagnostics)."""
        return len(self._cursors)

    def _execute_raw(self, ir: dict[str, Any], store: Store) -> QueryResultBody:
        """Execute the bounded raw-statement subset.

        Supported form: ``SELECT * FROM <Model> [WHERE <field> = $<param>]`` with
        parameters bound (never string-concatenated).
        """
        statement = str(ir.get("statement", "")).strip()
        params = ir.get("params", {})
        tokens = statement.replace(";", "").split()
        upper = [t.upper() for t in tokens]
        if not (
            len(tokens) >= 4 and upper[0] == "SELECT" and tokens[1] == "*" and upper[2] == "FROM"
        ):
            raise AuraQueryError(
                "Reference server supports raw statements of the form "
                "'SELECT * FROM <Model> [WHERE <field> = $<param>]'"
            )
        model = tokens[3]
        rows = list(self._table(store, model).values())
        if len(tokens) >= 8 and upper[4] == "WHERE" and tokens[6] == "=":
            field = tokens[5]
            param_token = tokens[7]
            if not param_token.startswith("$"):
                raise AuraQueryError("Raw WHERE value must be a bound parameter ($name)")
            value = params.get(param_token[1:])
            rows = [r for r in rows if r.get(field) == value]
        return QueryResultBody(rows=[dict(r) for r in rows], count=len(rows))

    def _execute_traverse(self, ir: dict[str, Any], store: Store) -> QueryResultBody:
        """Walk relationships from a start set with a depth bound."""
        current_model = ir["model"]
        start_filters = ir.get("start", [])
        current_rows = [
            r
            for r in self._table(store, current_model).values()
            if self._matches_all(r, start_filters)
        ]
        steps = ir.get("steps", [])
        if len(steps) > ir.get("max_depth", 5):
            raise AuraQueryError("traversal exceeds max_depth")

        for step in steps:
            relationship = next(
                (r for r in self._relationships(current_model) if r["name"] == step), None
            )
            if relationship is None:
                raise AuraQueryError(f"{current_model} has no relationship {step!r} to traverse")
            target = relationship["target"]
            target_table = self._table(store, target)
            seen: dict[Any, Row] = {}
            if relationship["kind"] == "link":
                for row in current_rows:
                    ref = row.get(step)
                    related = target_table.get(ref)
                    if related is not None:
                        seen[ref] = related
            else:
                back = next(
                    (
                        r
                        for r in self._relationships(target)
                        if r["kind"] == "link" and r["target"] == current_model
                    ),
                    None,
                )
                if back is not None:
                    pk_field = self.primary_key_field(current_model)
                    target_pk = self.primary_key_field(target)
                    starts = {row.get(pk_field) for row in current_rows}
                    for row in target_table.values():
                        if row.get(back["name"]) in starts:
                            seen[row.get(target_pk)] = row
            current_rows = list(seen.values())
            current_model = target

        filters = ir.get("filters", [])
        current_rows = [r for r in current_rows if self._matches_all(r, filters)]
        limit = ir.get("limit")
        if limit is not None:
            current_rows = current_rows[:limit]
        return QueryResultBody(
            rows=[dict(r) for r in current_rows],
            count=len(current_rows),
            metadata={"model": current_model},
        )

    def _apply_search(
        self, ir: dict[str, Any], rows: list[Row]
    ) -> list[tuple[Row, dict[str, float] | None]]:
        if ir.get("hybrid") is not None:
            return self._apply_hybrid(ir["hybrid"], rows)
        if ir.get("text_search") is not None:
            return self._apply_text_ranked(ir["text_search"], rows)

        vector = ir.get("vector")
        text = ir.get("text")
        if vector is None and text is None:
            return [(r, None) for r in rows]

        fusion = ir.get("fusion", {})
        alpha = fusion.get("alpha", 1.0 if vector else 0.0)
        scored: list[tuple[Row, dict[str, float] | None]] = []
        for row in rows:
            vscore = self._vector_score(vector, row) if vector else None
            tscore = self._text_score(text, row) if text else None
            if vscore is None and tscore is None:
                continue
            if vscore is not None and tscore is not None:
                combined = alpha * vscore + (1 - alpha) * tscore
            else:
                combined = vscore if vscore is not None else tscore
            scored.append((row, {"score": float(combined)}))
        scored.sort(key=lambda pair: (-(pair[1] or {})["score"], self._stable_key(pair[0])))
        return scored

    def _bm25_scores(
        self, field: str, query: str, operator: str, rows: list[Row], k1: float, b: float
    ) -> dict[Any, float]:
        """Compute BM25 scores over ``rows`` for ``query`` on ``field``.

        Mirrors the AuraDB server's BM25+ formulation closely enough for the
        reference engine: deterministic ordering and the same relative ranking
        behaviour (term-frequency saturation, document-length normalization, and
        non-negative IDF). Exact scores need not byte-match the server.
        """
        terms = _tokenize(query)
        terms = sorted(set(terms))
        if not terms:
            return {}
        docs: list[tuple[Any, list[str]]] = []
        for row in rows:
            value = row.get(field)
            if not isinstance(value, str):
                continue
            docs.append((self._stable_key(row), _tokenize(value)))
        if not docs:
            return {}
        n = len(docs)
        avgdl = sum(len(toks) for _, toks in docs) / n if n else 1.0
        avgdl = max(avgdl, 1.0)
        df = {t: sum(1 for _, toks in docs if t in toks) for t in terms}
        out: dict[Any, float] = {}
        for key, toks in docs:
            dl = len(toks)
            matched = 0
            score = 0.0
            for t in terms:
                tf = toks.count(t)
                if tf == 0:
                    continue
                matched += 1
                idf = math.log((n - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
                denom = tf + k1 * (1 - b + b * dl / avgdl)
                score += idf * (tf * (k1 + 1.0)) / denom
            if operator == "and" and matched < len(terms):
                continue
            if matched > 0:
                out[key] = score
        return out

    def _apply_text_ranked(
        self, ts: dict[str, Any], rows: list[Row]
    ) -> list[tuple[Row, dict[str, float] | None]]:
        k1 = float(ts.get("k1") or 1.2)
        b = float(ts.get("b") or 0.75)
        operator = ts.get("operator", "or")
        if ts.get("rank") == "term_frequency":
            query_terms = _tokenize(ts["query"])

            def _tf(r: Row, terms: list[str] = query_terms) -> float:
                doc = _tokenize(str(r.get(ts["field"], "")))
                return float(sum(doc.count(t) for t in terms))

            tscores = {self._stable_key(r): _tf(r) for r in rows}
            tscores = {k: v for k, v in tscores.items() if v > 0}
        else:
            tscores = self._bm25_scores(ts["field"], ts["query"], operator, rows, k1, b)
        scored: list[tuple[Row, dict[str, float] | None]] = [
            (r, {"score": tscores[self._stable_key(r)]})
            for r in rows
            if self._stable_key(r) in tscores
        ]
        scored.sort(key=lambda pair: (-(pair[1] or {})["score"], self._stable_key(pair[0])))
        return scored

    def _apply_hybrid(
        self, hs: dict[str, Any], rows: list[Row]
    ) -> list[tuple[Row, dict[str, float] | None]]:
        k1 = float(hs.get("k1") or 1.2)
        b = float(hs.get("b") or 0.75)
        weights = hs.get("weights", {"text": 0.5, "vector": 0.5})
        wt = float(weights.get("text", 0.5))
        wv = float(weights.get("vector", 0.5))
        fusion = hs.get("fusion", "weighted_sum")
        text = self._bm25_scores(
            hs["text_field"], hs["text_query"], hs.get("operator", "or"), rows, k1, b
        )
        vec: dict[Any, float] = {}
        vparams = {
            "field": hs["vector_field"],
            "query": hs["vector"],
            "metric": hs.get("metric", "cosine"),
        }
        for r in rows:
            vs = self._vector_score(vparams, r)
            if vs is not None:
                vec[self._stable_key(r)] = vs
        keys = set(text) | set(vec)
        by_key = {self._stable_key(r): r for r in rows}
        scored: list[tuple[Row, dict[str, float] | None]] = []
        if fusion == "reciprocal_rank_fusion":
            text_rank = _rank_map(text)
            vec_rank = _rank_map(vec)
            for key in keys:
                fused = 0.0
                if key in text_rank:
                    fused += wt / (60.0 + text_rank[key])
                if key in vec_rank:
                    fused += wv / (60.0 + vec_rank[key])
                bundle = {"score": fused}
                if key in text:
                    bundle["text_score"] = text[key]
                if key in vec:
                    bundle["vector_score"] = vec[key]
                scored.append((by_key[key], bundle))
        else:
            tmin, tmax = _min_max(text.values())
            vmin, vmax = _min_max(vec.values())
            for key in keys:
                tn = _normalize(text[key], tmin, tmax) if key in text else 0.0
                vn = _normalize(vec[key], vmin, vmax) if key in vec else 0.0
                bundle = {"score": wt * tn + wv * vn}
                if key in text:
                    bundle["text_score"] = text[key]
                if key in vec:
                    bundle["vector_score"] = vec[key]
                scored.append((by_key[key], bundle))
        scored.sort(key=lambda pair: (-(pair[1] or {})["score"], self._stable_key(pair[0])))
        return scored

    def _vector_score(self, vector: dict[str, Any], row: Row) -> float | None:
        field = vector["field"]
        stored = row.get(field)
        if stored is None:
            return None
        query = [float(x) for x in vector["query"]]
        candidate = [float(x) for x in stored]
        if len(candidate) != len(query):
            return None
        metric = vector.get("metric", "cosine")
        if metric == "dot":
            return _dot(query, candidate)
        if metric == "euclidean":
            return -_euclidean(query, candidate)
        return _cosine_similarity(query, candidate)

    def _text_score(self, text: dict[str, Any], row: Row) -> float | None:
        query = str(text.get("query", "")).lower()
        terms = [t for t in query.split() if t]
        if not terms:
            return None
        haystack = " ".join(str(row.get(f, "")) for f in text.get("fields", [])).lower()
        hits = sum(haystack.count(term) for term in terms)
        return float(hits) / len(terms)

    def _apply_order(
        self, sort: list[dict[str, Any]], scored: list[tuple[Row, dict[str, float] | None]]
    ) -> list[tuple[Row, dict[str, float] | None]]:
        if not sort:
            return scored
        ordered = list(scored)
        for term in reversed(sort):
            field = term["field"]
            reverse = term.get("direction") == "desc"
            ordered.sort(key=lambda pair, f=field: _sort_key(pair[0].get(f)), reverse=reverse)
        return ordered

    def _project(
        self,
        model: str,
        store: Store,
        ir: dict[str, Any],
        row: Row,
        bundle: dict[str, float] | None,
        rank: int | None,
    ) -> Row:
        projection = ir.get("projection")
        if projection:
            pk = self.primary_key_field(model)
            keep = set(projection) | {pk}
            out = {k: v for k, v in row.items() if k in keep}
        else:
            out = dict(row)

        for include in ir.get("include", []):
            self._resolve_include(model, store, row, include, out)

        if bundle is not None:
            if bundle.get("score") is not None:
                out["__score__"] = bundle["score"]
            if "text_score" in bundle:
                out["__text_score__"] = bundle["text_score"]
            if "vector_score" in bundle:
                out["__vector_score__"] = bundle["vector_score"]
        if rank is not None:
            out["__rank__"] = rank + 1
        return out

    def _resolve_include(
        self, model: str, store: Store, row: Row, include: dict[str, Any], out: Row
    ) -> None:
        rel_name = include["link"]
        relationship = next((r for r in self._relationships(model) if r["name"] == rel_name), None)
        if relationship is None:
            raise AuraQueryError(
                f"{model} has no relationship {rel_name!r} to include",
                context={"model": model, "relationship": rel_name},
            )
        target = relationship["target"]
        target_table = self._table(store, target)

        if relationship["kind"] == "link":
            ref = row.get(rel_name)
            related = target_table.get(ref)
            out[rel_name] = dict(related) if related is not None else None
            return

        # collection: find the reverse link on the target pointing back to this model.
        back = next(
            (
                r
                for r in self._relationships(target)
                if r["kind"] == "link" and r["target"] == model
            ),
            None,
        )
        pk_value = row.get(self.primary_key_field(model))
        matches: list[Row] = []
        if back is not None:
            field = back["name"]
            matches = [dict(r) for r in target_table.values() if r.get(field) == pk_value]
        sort = include.get("sort", [])
        if sort:
            scored = self._apply_order(sort, [(m, None) for m in matches])
            matches = [m for m, _ in scored]
        limit = include.get("limit")
        if limit is not None:
            matches = matches[:limit]
        out[rel_name] = matches

    # -- mutation execution ------------------------------------------------------
    def _execute_mutation(self, ir: dict[str, Any], txid: int) -> MutationResultBody:
        operation = ir["operation"]
        model = ir["model"]
        store = self._store(txid)
        table = self._table(store, model)
        pk_field = self.primary_key_field(model)

        if operation == "insert":
            return self._insert(model, table, pk_field, ir)
        if operation == "update":
            return self._update(table, ir)
        if operation == "delete":
            return self._delete(table, ir)
        if operation == "upsert":
            return self._upsert(model, table, pk_field, ir)
        raise AuraQueryError(f"Unsupported mutation {operation!r}")

    def _insert(
        self, model: str, table: Table, pk_field: str, ir: dict[str, Any]
    ) -> MutationResultBody:
        on_conflict = ir.get("on_conflict")
        inserted: list[Row] = []
        for raw in ir["rows"]:
            row = self._normalize_row(model, raw)
            pk = row.get(pk_field)
            if pk is None:
                raise AuraConstraintError(
                    f"{model} insert is missing primary key {pk_field!r}",
                    context={"model": model},
                )
            if pk in table:
                if on_conflict == "ignore":
                    continue
                if on_conflict != "replace":
                    raise AuraConstraintError(
                        f"{model} with {pk_field}={pk!r} already exists",
                        context={"model": model, "pk": pk},
                    )
            table[pk] = row
            inserted.append(dict(row))
        return MutationResultBody(affected=len(inserted), returning=inserted)

    def _update(self, table: Table, ir: dict[str, Any]) -> MutationResultBody:
        filters = ir.get("filters", [])
        assignments = ir.get("set", {})
        affected = 0
        for row in table.values():
            if self._matches_all(row, filters):
                row.update(assignments)
                affected += 1
        return MutationResultBody(affected=affected)

    def _delete(self, table: Table, ir: dict[str, Any]) -> MutationResultBody:
        filters = ir.get("filters", [])
        to_delete = [pk for pk, row in table.items() if self._matches_all(row, filters)]
        for pk in to_delete:
            del table[pk]
        return MutationResultBody(affected=len(to_delete))

    def _upsert(
        self, model: str, table: Table, pk_field: str, ir: dict[str, Any]
    ) -> MutationResultBody:
        key = ir["key"]
        values = ir["values"]
        match_pk = None
        for pk, row in table.items():
            if all(row.get(k) == v for k, v in key.items()):
                match_pk = pk
                break
        if match_pk is not None:
            table[match_pk].update(values)
            return MutationResultBody(affected=1, returning=[dict(table[match_pk])])
        merged = self._normalize_row(model, {**key, **values})
        pk = merged.get(pk_field)
        if pk is None:
            raise AuraConstraintError(f"upsert for {model} is missing {pk_field!r}")
        table[pk] = merged
        return MutationResultBody(affected=1, returning=[dict(merged)])

    def _normalize_row(self, model: str, raw: dict[str, Any]) -> Row:
        """Store link fields as the target primary key; drop collection fields."""
        relationships = {r["name"]: r for r in self._relationships(model)}
        row: Row = {}
        for key, value in raw.items():
            rel = relationships.get(key)
            if rel is None:
                row[key] = value
                continue
            if rel["kind"] == "collection":
                continue
            if isinstance(value, dict):
                target_pk = self.primary_key_field(rel["target"])
                row[key] = value.get(target_pk)
            else:
                row[key] = value
        return row

    # -- predicate evaluation ----------------------------------------------------
    def _matches_all(self, row: Row, filters: list[dict[str, Any]]) -> bool:
        return all(self._matches(row, f) for f in filters)

    def _matches(self, row: Row, predicate: dict[str, Any]) -> bool:
        kind = predicate.get("kind")
        if kind == "bool":
            results = [self._matches(row, op) for op in predicate["operands"]]
            return all(results) if predicate["op"] == "and" else any(results)
        if kind == "not":
            return not self._matches(row, predicate["operand"])
        if kind == "compare":
            return self._compare(row, predicate)
        raise AuraQueryError(f"Unsupported predicate kind {kind!r}")

    def _compare(self, row: Row, predicate: dict[str, Any]) -> bool:
        left = predicate["left"]
        right = predicate["right"]
        op = predicate["op"]
        value = self._field_value(row, left)
        rhs = self._literal(right)

        if op == "eq":
            return bool(value == rhs)
        if op == "ne":
            return bool(value != rhs)
        if op == "lt":
            return value is not None and value < rhs
        if op == "le":
            return value is not None and value <= rhs
        if op == "gt":
            return value is not None and value > rhs
        if op == "ge":
            return value is not None and value >= rhs
        if op == "in":
            return value in rhs
        if op == "not_in":
            return value not in rhs
        if op == "contains":
            return rhs in value if value is not None else False
        if op == "startswith":
            return isinstance(value, str) and value.startswith(rhs)
        if op == "endswith":
            return isinstance(value, str) and value.endswith(rhs)
        if op == "like":
            return _like(value, rhs)
        if op == "is_null":
            return value is None
        if op == "is_not_null":
            return value is not None
        if op == "exists":
            return value is not None
        raise AuraQueryError(f"Unsupported comparison op {op!r}")

    def _field_value(self, row: Row, field_ir: dict[str, Any]) -> Any:
        value: Any = row.get(field_ir["field"])
        for key in field_ir.get("path", []):
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value

    def _literal(self, expr: dict[str, Any]) -> Any:
        if expr.get("kind") == "literal":
            return expr["value"]
        if expr.get("kind") == "param":
            raise AuraQueryError("Unbound query parameter encountered")
        return expr

    def _stable_key(self, row: Row) -> Any:
        for value in row.values():
            if isinstance(value, (int, float, str)):
                return value
        return id(row)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return _dot(a, b) / (na * nb)


def _like(value: Any, pattern: str) -> bool:
    if not isinstance(value, str):
        return False
    import re

    regex = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    return re.match(regex, value) is not None


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, 0)
    return (1, value)


def _is_ranked(ir: dict[str, Any]) -> bool:
    """Whether the query orders by a relevance/similarity score."""
    return any(k in ir for k in ("vector", "text", "text_search", "hybrid"))


def _tokenize(text: str) -> list[str]:
    """Case-fold and split on non-alphanumeric boundaries (matches the server)."""
    out: list[str] = []
    token: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            token.append(ch)
        elif token:
            out.append("".join(token))
            token = []
    if token:
        out.append("".join(token))
    return out


def _rank_map(scores: dict[Any, float]) -> dict[Any, int]:
    """1-based rank of each key by descending score (ties broken by key)."""
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], _key_sort(kv[0])))
    return {key: i + 1 for i, (key, _) in enumerate(ordered)}


def _key_sort(key: Any) -> tuple[int, Any]:
    if isinstance(key, (int, float)):
        return (0, key)
    return (1, str(key))


def _min_max(values: Any) -> tuple[float, float]:
    vals = [float(v) for v in values]
    if not vals:
        return (0.0, 0.0)
    return (min(vals), max(vals))


def _normalize(value: float, lo: float, hi: float) -> float:
    if abs(hi - lo) < 1e-12:
        return 1.0
    return (value - lo) / (hi - lo)


class MemoryTransport(Transport):
    """Transport backed by an in-process :class:`ReferenceServer`.

    Requests are encoded to wire bytes, decoded by the server, executed, and the
    response re-encoded and decoded — the full protocol path runs in-process.
    """

    def __init__(
        self,
        server: ReferenceServer | None = None,
        *,
        max_payload_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        super().__init__()
        self._server = server if server is not None else ReferenceServer()
        self._connected = False
        self._max_payload_bytes = max_payload_bytes

    @property
    def server(self) -> ReferenceServer:
        return self._server

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def request(self, frame: Frame) -> Frame:
        if not self._connected:
            raise AuraConnectionError("MemoryTransport is not connected")
        data = encode_frame(frame, max_payload_bytes=self._max_payload_bytes, payload_checksum=True)
        self.stats.frames_sent += 1
        self.stats.bytes_sent += len(data)
        response = self._server.feed(data, max_payload_bytes=self._max_payload_bytes)
        self.stats.frames_received += 1
        self.stats.bytes_received += len(response)
        out, _ = decode_frame(response, max_payload_bytes=self._max_payload_bytes)
        return out
