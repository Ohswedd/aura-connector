"""v0.9.0 search snippets/highlights: the request builder and IR, live wire
forwarding and response decoding, the typed result models, and capability gating.

These pin the honest behaviors:

* ``.snippets(fields=…)`` builds the IR and requires a prior ranked-text clause;
* the native backend forwards the request and decodes the response;
* ``search_snippets(instance)`` returns typed, plain-text models (empty when none);
* a snippet request requires the backend ``search_snippets`` capability and
  otherwise raises ``AuraCapabilityError`` — never silently dropped.
"""

from __future__ import annotations

import pytest

from aura import (
    AuraModel,
    Field,
    HighlightRange,
    SearchSnippet,
    SearchSnippetFragment,
    Vector,
    search_snippets,
)
from aura.backends.capabilities import CAPABILITY_FLAGS, BackendCapabilities
from aura.config import parse_dsn
from aura.errors import AuraCapabilityError, AuraQueryError


class Article(AuraModel):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


def _builder(model: type[AuraModel]):
    from aura.query.builder import QueryBuilder

    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


# --- request model / builder -------------------------------------------------


def test_snippet_request_model() -> None:
    from aura.query.ast import SnippetRequest

    req = SnippetRequest(fields=("body",), max_fragments=2, fragment_chars=50)
    ir = req.to_ir()
    assert ir == {"fields": ["body"], "max_fragments": 2, "fragment_chars": 50}
    # Optional caps are omitted when unset.
    assert SnippetRequest(fields=("body",)).to_ir() == {"fields": ["body"]}


def test_snippet_builder_fields() -> None:
    ir = (
        _builder(Article)
        .search_text("body", "restore backup")
        .snippets(fields=["body"], max_fragments=2)
        .query.to_ir()
    )
    assert ir["snippet"] == {"fields": ["body"], "max_fragments": 2}


def test_snippet_builder_requires_prior_search() -> None:
    # A snippet request needs a ranked-text clause to highlight against.
    with pytest.raises(AuraQueryError):
        _builder(Article).snippets(fields=["body"])
    # Empty field list and non-positive caps are rejected client-side.
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "x").snippets(fields=[])
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "x").snippets(fields=["body"], max_fragments=0)


# --- live wire forwarding / decoding ----------------------------------------


def test_snippet_forwarded_and_decoded() -> None:
    from aura.backends.auradb_native import _decode_row, _translate_select

    ir = (
        _builder(Article)
        .search_text("body", "restore")
        .snippets(fields=["body"], fragment_chars=80)
        .query.to_ir()
    )
    server = _translate_select(ir)
    assert server["snippet"] == {"fields": ["body"], "fragment_chars": 80}
    # A response row's snippets surface on the decoded row for the hydrator.
    row = {
        "fields": {"id": 1, "body": "restore the backup"},
        "snippets": [
            {
                "field": "body",
                "fragments": [{"text": "restore the backup", "ranges": [{"start": 0, "end": 7}]}],
            }
        ],
    }
    decoded = _decode_row(row)
    assert decoded["__snippets__"] == row["snippets"]
    # A row without snippets leaves the key unset.
    assert "__snippets__" not in _decode_row({"fields": {"id": 2, "body": "x"}})


# --- result models -----------------------------------------------------------


class _Inst:
    """A stand-in for a hydrated instance carrying the raw snippet payload."""

    def __init__(self, snippets: object) -> None:
        if snippets is not None:
            self.__dict__["__snippets__"] = snippets


def test_snippet_result_model_parse() -> None:
    inst = _Inst(
        [
            {
                "field": "body",
                "fragments": [{"text": "restore the backup", "ranges": [{"start": 0, "end": 7}]}],
            }
        ]
    )
    snips = search_snippets(inst)
    assert len(snips) == 1
    assert isinstance(snips[0], SearchSnippet)
    assert snips[0].field == "body"
    frag = snips[0].fragments[0]
    assert isinstance(frag, SearchSnippetFragment)
    assert frag.text == "restore the backup"
    assert frag.ranges == (HighlightRange(start=0, end=7),)
    # The highlighted range slices the matched token out of the fragment text.
    r = frag.ranges[0]
    assert frag.text[r.start : r.end] == "restore"


def test_snippet_missing_field_safe() -> None:
    # No snippets attached -> empty tuple, never a crash or key error.
    assert search_snippets(_Inst(None)) == ()
    assert search_snippets(_Inst([])) == ()
    # Malformed entries are skipped, not fatal.
    assert search_snippets(_Inst(["not-a-dict"])) == ()


# --- capability gating -------------------------------------------------------


def test_snippet_capability_required() -> None:
    assert "search_snippets" in CAPABILITY_FLAGS
    caps = BackendCapabilities(name="auradb", full_text_search=True)
    assert not caps.supports("search_snippets")
    with pytest.raises(AuraCapabilityError):
        caps.require("search_snippets")


def test_snippet_unsupported_backend_raises() -> None:
    caps = BackendCapabilities(name="sqlite")
    with pytest.raises(AuraCapabilityError):
        caps.require("search_snippets")


def test_snippet_live_success_with_v15_capability() -> None:
    from aura.backends.auradb_native import AuraDBNativeBackend
    from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge

    backend = AuraDBNativeBackend(
        parse_dsn("auradb://localhost:7171/x"),
        Metrics(),
        _TelemetryBridge(TelemetryConfig.from_value(None)),
    )
    backend._server_caps = {"full_text_bm25_ranking", "search_snippets"}
    backend.capabilities().require("search_snippets")  # does not raise


def test_snippet_plain_text_docs() -> None:
    # The connector documents snippet text as plain text (no HTML claim).
    import aura.snippets as snippets_mod

    doc = (snippets_mod.__doc__ or "").lower()
    assert "plain text" in doc
    assert "no html" in doc or "makes no html" in doc or "html/markup" in doc
