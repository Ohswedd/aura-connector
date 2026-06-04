"""Schema diffing and migration planning.

``diff_schemas`` compares two compiled schema documents (as produced by
:func:`aura.schema.compiler.schema_document`) and returns a deterministic, human
reviewable :class:`MigrationPlan`. The plan classifies each change as additive or
*destructive* (dropping a model/field/index/relationship, or narrowing a type or vector
dimension), carries rollback metadata for every change, and renders to readable text.

What is intentionally local-only: the plan does **not** include AuraDB lock/impact
estimates, which require a connected server. Destructive-change detection,
human-reviewable output, and rollback-strategy metadata are all computed here
without any external dependency.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..errors import AuraMigrationError
from .compiler import SCHEMA_FORMAT_VERSION, schema_document

if TYPE_CHECKING:
    from ..models import AuraModel

__all__ = [
    "Change",
    "LockImpactEstimate",
    "MigrationPlan",
    "diff_schemas",
    "generate_migration",
]

#: The three possible states of a plan's live lock/impact estimate.
#: ``local_only`` — generated offline, no server consulted (the default for a local diff).
#: ``server_estimated`` — a connected AuraDB returned a real estimate.
#: ``unavailable`` — an estimate was requested but the server could not provide one.
_ESTIMATE_STATES = frozenset({"local_only", "server_estimated", "unavailable"})

# Change kinds, ordered for deterministic rendering (creates before drops within a model
# group is handled by the per-model ordering below).
_KIND_ORDER = {
    "add_model": 0,
    "drop_model": 1,
    "add_field": 2,
    "alter_field": 3,
    "drop_field": 4,
    "add_index": 5,
    "drop_index": 6,
    "add_relationship": 7,
    "alter_relationship": 8,
    "drop_relationship": 9,
}

# Field attributes whose change is meaningful for a migration (and whether a given
# transition is destructive is decided in ``_field_changes``).
_INDEX_ATTRS = ("index", "unique", "primary_key")


@dataclass(frozen=True)
class Change:
    """A single schema change with destructiveness and rollback metadata."""

    kind: str
    model: str
    target: str  # field/index/relationship name, or the model name for model-level
    detail: str
    destructive: bool
    rollback: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": self.model,
            "target": self.target,
            "detail": self.detail,
            "destructive": self.destructive,
            "rollback": self.rollback,
        }


@dataclass(frozen=True)
class LockImpactEstimate:
    """A live-cluster lock/impact estimate for a migration.

    This is produced only by a connected AuraDB server. The local diff cannot compute
    it; it carries the structured shape so a caller can attach a server-provided estimate
    via :meth:`MigrationPlan.with_server_estimate` without changing the plan contract.
    """

    lock_level: str  # e.g. "none" | "shared" | "exclusive"
    blocking: bool
    estimated_duration_ms: int | None = None
    affected_rows: int | None = None
    source: str = "server"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_level": self.lock_level,
            "blocking": self.blocking,
            "estimated_duration_ms": self.estimated_duration_ms,
            "affected_rows": self.affected_rows,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class MigrationPlan:
    """An ordered, reviewable set of schema changes from one schema to another."""

    changes: tuple[Change, ...] = ()
    from_version: int = SCHEMA_FORMAT_VERSION
    to_version: int = SCHEMA_FORMAT_VERSION
    #: One of ``local_only`` / ``server_estimated`` / ``unavailable``. A plan built by
    #: the local diff is always ``local_only`` until a server estimate is attached.
    impact_estimate_status: str = "local_only"
    #: A live lock/impact estimate, present only when a server provided one.
    lock_impact_estimate: LockImpactEstimate | None = None
    _notes: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.impact_estimate_status not in _ESTIMATE_STATES:
            raise AuraMigrationError(
                f"Invalid impact_estimate_status {self.impact_estimate_status!r}; "
                f"expected one of {sorted(_ESTIMATE_STATES)}"
            )
        if (
            self.lock_impact_estimate is not None
            and self.impact_estimate_status != "server_estimated"
        ):
            raise AuraMigrationError(
                "lock_impact_estimate may only be set when impact_estimate_status is "
                "'server_estimated'"
            )

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def requires_live_cluster_estimate(self) -> bool:
        """True when this plan has changes whose lock impact only a live server can size.

        It is ``False`` for an empty plan (nothing to estimate) or once a real
        server estimate has been attached.
        """
        return not (self.is_empty or self.impact_estimate_status == "server_estimated")

    @property
    def is_destructive(self) -> bool:
        return any(c.destructive for c in self.changes)

    @property
    def destructive_changes(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.destructive)

    def require_safe(self) -> None:
        """Raise if the plan contains destructive changes (CI gate helper)."""
        if self.is_destructive:
            names = ", ".join(f"{c.model}.{c.target}" for c in self.destructive_changes)
            raise AuraMigrationError(
                f"Migration contains destructive changes requiring explicit approval: {names}",
                context={"destructive": [c.to_dict() for c in self.destructive_changes]},
            )

    def rollback_plan(self) -> list[str]:
        """Return the rollback steps, in reverse application order."""
        return [c.rollback for c in reversed(self.changes)]

    def with_server_estimate(self, estimate: LockImpactEstimate) -> MigrationPlan:
        """Return a copy carrying a live server lock/impact estimate.

        Call this with the estimate a connected AuraDB returns; the result has
        ``impact_estimate_status == 'server_estimated'`` and a populated
        :attr:`lock_impact_estimate`.
        """
        from dataclasses import replace

        return replace(
            self,
            impact_estimate_status="server_estimated",
            lock_impact_estimate=estimate,
        )

    def mark_estimate_unavailable(self) -> MigrationPlan:
        """Return a copy explicitly marking the live estimate as unavailable.

        Use this when an estimate was requested from a server but could not be obtained,
        so callers can distinguish "never asked" (``local_only``) from "asked, failed".
        """
        from dataclasses import replace

        return replace(self, impact_estimate_status="unavailable", lock_impact_estimate=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "destructive": self.is_destructive,
            "changes": [c.to_dict() for c in self.changes],
            "rollback": self.rollback_plan(),
            "impact_estimate_status": self.impact_estimate_status,
            "requires_live_cluster_estimate": self.requires_live_cluster_estimate,
            "lock_impact_estimate": (
                self.lock_impact_estimate.to_dict()
                if self.lock_impact_estimate is not None
                else None
            ),
        }

    def format(self) -> str:
        """Render a deterministic, human-reviewable migration document."""
        if self.is_empty:
            return "# Aura migration\n\nNo schema changes detected.\n"
        lines = ["# Aura migration", ""]
        if self.is_destructive:
            lines.append("> WARNING: this migration contains DESTRUCTIVE changes.")
            lines.append("> They require explicit approval before being applied.")
            lines.append("")
        lines.append(f"Changes ({len(self.changes)}):")
        for change in self.changes:
            marker = "DESTRUCTIVE" if change.destructive else "safe"
            lines.append(
                f"  - [{marker}] {change.kind} {change.model}.{change.target}: {change.detail}"
            )
        lines.append("")
        lines.append("Rollback (reverse order):")
        for step in self.rollback_plan():
            lines.append(f"  - {step}")
        lines.append("")
        lines.append(f"Lock/impact estimate: {self.impact_estimate_status}")
        if self.impact_estimate_status == "server_estimated" and self.lock_impact_estimate:
            est = self.lock_impact_estimate
            lines.append(
                f"  - lock={est.lock_level} blocking={est.blocking} "
                f"duration_ms={est.estimated_duration_ms} affected_rows={est.affected_rows}"
            )
        else:
            lines.append(
                "  - AuraDB lock/impact estimates require a live connection and are not "
                "computed by this local diff."
            )
        lines.append("")
        return "\n".join(lines)


def _models_of(document: Any) -> dict[str, dict[str, Any]]:
    """Accept a schema document, a list of model dicts, or compiled models."""
    if isinstance(document, dict) and "models" in document:
        models = document["models"]
    elif isinstance(document, (list, tuple)):
        models = document
    else:
        raise AuraMigrationError(
            "Schema must be a schema document, a list of model dicts, or models",
            context={"type": type(document).__name__},
        )
    out: dict[str, dict[str, Any]] = {}
    for model in models:
        if not isinstance(model, dict) or "name" not in model:
            raise AuraMigrationError("Each model entry must be a dict with a 'name'")
        out[str(model["name"])] = model
    return out


def _fields_of(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(f["name"]): f for f in model.get("fields", [])}


def _rels_of(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(r["name"]): r for r in model.get("relationships", [])}


def _field_changes(model: str, old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    name = str(new["name"])

    # Type change: a different type is destructive (data may not coerce losslessly).
    if old.get("type") != new.get("type"):
        changes.append(
            Change(
                kind="alter_field",
                model=model,
                target=name,
                detail=f"type {old.get('type')!r} -> {new.get('type')!r}",
                destructive=True,
                rollback=f"restore {model}.{name} type to {old.get('type')!r}",
            )
        )

    # Vector dimension change is destructive (stored vectors no longer match).
    if old.get("vector_dim") != new.get("vector_dim"):
        changes.append(
            Change(
                kind="alter_field",
                model=model,
                target=name,
                detail=f"vector_dim {old.get('vector_dim')} -> {new.get('vector_dim')}",
                destructive=True,
                rollback=f"restore {model}.{name} vector_dim to {old.get('vector_dim')}",
            )
        )

    # Nullable -> not nullable is destructive (existing nulls become invalid).
    if old.get("nullable") and not new.get("nullable"):
        changes.append(
            Change(
                kind="alter_field",
                model=model,
                target=name,
                detail="nullable -> not null",
                destructive=True,
                rollback=f"make {model}.{name} nullable again",
            )
        )
    elif not old.get("nullable") and new.get("nullable"):
        changes.append(
            Change(
                kind="alter_field",
                model=model,
                target=name,
                detail="not null -> nullable",
                destructive=False,
                rollback=f"make {model}.{name} not null again",
            )
        )

    # Index/uniqueness transitions.
    for attr in _INDEX_ATTRS:
        before, after = bool(old.get(attr)), bool(new.get(attr))
        if before == after:
            continue
        if after:
            changes.append(
                Change(
                    kind="add_index",
                    model=model,
                    target=name,
                    detail=f"add {attr}",
                    destructive=False,
                    rollback=f"drop {attr} on {model}.{name}",
                )
            )
        else:
            changes.append(
                Change(
                    kind="drop_index",
                    model=model,
                    target=name,
                    detail=f"drop {attr}",
                    destructive=True,
                    rollback=f"recreate {attr} on {model}.{name}",
                )
            )
    return changes


def _model_changes(model: str, old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    old_fields, new_fields = _fields_of(old), _fields_of(new)

    for fname in sorted(set(new_fields) - set(old_fields)):
        f = new_fields[fname]
        nullable_or_default = f.get("nullable") or f.get("has_default")
        changes.append(
            Change(
                kind="add_field",
                model=model,
                target=fname,
                detail=f"add {f.get('type')} field"
                + ("" if nullable_or_default else " (NOT NULL without default)"),
                # Adding a required column with no default to existing rows is destructive.
                destructive=not nullable_or_default,
                rollback=f"drop {model}.{fname}",
            )
        )
    for fname in sorted(set(old_fields) - set(new_fields)):
        changes.append(
            Change(
                kind="drop_field",
                model=model,
                target=fname,
                detail="drop field",
                destructive=True,
                rollback=f"recreate {model}.{fname} as {old_fields[fname].get('type')}",
            )
        )
    for fname in sorted(set(old_fields) & set(new_fields)):
        changes.extend(_field_changes(model, old_fields[fname], new_fields[fname]))

    old_rels, new_rels = _rels_of(old), _rels_of(new)
    for rname in sorted(set(new_rels) - set(old_rels)):
        r = new_rels[rname]
        changes.append(
            Change(
                kind="add_relationship",
                model=model,
                target=rname,
                detail=f"-> {r.get('target')} ({r.get('kind')})",
                destructive=False,
                rollback=f"drop relationship {model}.{rname}",
            )
        )
    for rname in sorted(set(old_rels) - set(new_rels)):
        changes.append(
            Change(
                kind="drop_relationship",
                model=model,
                target=rname,
                detail=f"drop relationship -> {old_rels[rname].get('target')}",
                destructive=True,
                rollback=f"recreate relationship {model}.{rname}",
            )
        )
    for rname in sorted(set(old_rels) & set(new_rels)):
        before, after = old_rels[rname], new_rels[rname]
        if before.get("target") != after.get("target") or before.get("kind") != after.get("kind"):
            changes.append(
                Change(
                    kind="alter_relationship",
                    model=model,
                    target=rname,
                    detail=f"{before.get('target')}/{before.get('kind')} -> "
                    f"{after.get('target')}/{after.get('kind')}",
                    destructive=True,
                    rollback=f"restore relationship {model}.{rname}",
                )
            )
    return changes


def diff_schemas(old: Any, new: Any) -> MigrationPlan:
    """Diff two schema documents and return a deterministic :class:`MigrationPlan`.

    ``old`` and ``new`` may each be a schema document (``{"models": [...]}``), a list of
    model dicts, or anything :func:`_models_of` understands. Changes are emitted in a
    stable order (by model name, then by change kind) so the same diff always produces
    byte-identical output for CI review.
    """
    old_models = _models_of(old)
    new_models = _models_of(new)
    changes: list[Change] = []

    for mname in sorted(set(new_models) - set(old_models)):
        model = new_models[mname]
        field_count = len(model.get("fields", []))
        changes.append(
            Change(
                kind="add_model",
                model=mname,
                target=mname,
                detail=f"create model with {field_count} field(s)",
                destructive=False,
                rollback=f"drop model {mname}",
            )
        )
        # Field-level adds for a brand-new model are implied by the create; do not also
        # emit per-field adds (keeps the plan readable).
    for mname in sorted(set(old_models) - set(new_models)):
        changes.append(
            Change(
                kind="drop_model",
                model=mname,
                target=mname,
                detail="drop model and all its data",
                destructive=True,
                rollback=f"recreate model {mname}",
            )
        )
    for mname in sorted(set(old_models) & set(new_models)):
        changes.extend(_model_changes(mname, old_models[mname], new_models[mname]))

    changes.sort(key=lambda c: (c.model, _KIND_ORDER.get(c.kind, 99), c.target))
    return MigrationPlan(
        changes=tuple(changes),
        from_version=_format_version(old),
        to_version=_format_version(new),
    )


def _format_version(document: Any) -> int:
    if isinstance(document, dict):
        return int(document.get("format_version", SCHEMA_FORMAT_VERSION))
    return SCHEMA_FORMAT_VERSION


def generate_migration(
    old_models: Iterable[type[AuraModel]], new_models: Iterable[type[AuraModel]]
) -> MigrationPlan:
    """Compile two model sets and diff them into a :class:`MigrationPlan`."""
    return diff_schemas(schema_document(old_models), schema_document(new_models))
