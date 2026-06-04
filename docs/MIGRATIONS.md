# Migrations

Aura compiles models into a deterministic schema document (see
[`MODELS.md`](MODELS.md)) and can **diff two schema versions** into a
human-reviewable migration plan. The diff is computed entirely locally, no server is
required, and classifies every change as safe or destructive, with rollback metadata.

## Diffing schemas

```python
from aura import diff_schemas, generate_migration, schema_document

# From compiled model sets:
plan = generate_migration(old_models=[UserV1], new_models=[UserV2])

# Or from schema documents / JSON you committed to your repo:
plan = diff_schemas(schema_document([UserV1]), schema_document([UserV2]))

print(plan.format())
```

`generate_migration` and `diff_schemas` both return a `MigrationPlan`.

## MigrationPlan

| Member | Description |
|---|---|
| `changes` | ordered tuple of `Change` objects (deterministic order) |
| `is_empty` | `True` when the schemas are identical |
| `is_destructive` | `True` if any change is destructive |
| `destructive_changes` | just the destructive subset |
| `require_safe()` | raises `AuraMigrationError` if the plan is destructive (CI gate) |
| `rollback_plan()` | rollback steps in reverse application order |
| `to_dict()` | JSON-ready representation (includes the estimate fields below) |
| `format()` | human-reviewable text document |
| `impact_estimate_status` | `"local_only"` \| `"server_estimated"` \| `"unavailable"` |
| `requires_live_cluster_estimate` | `True` for a non-empty local plan; `False` once a server estimate is attached or for an empty plan |
| `lock_impact_estimate` | `LockImpactEstimate \| None`, populated only by a server |
| `with_server_estimate(est)` | returns a copy with a server-provided `LockImpactEstimate` |
| `mark_estimate_unavailable()` | returns a copy marking the estimate explicitly unavailable |

Each `Change` carries `kind`, `model`, `target`, `detail`, `destructive`, and a
`rollback` string.

## Live lock/impact estimate boundary

Migration plans can include AuraDB lock/impact
estimates *when connected*. A local diff cannot compute these, so the plan represents the
estimate **structurally and explicitly** instead of silently omitting it:

```python
plan = generate_migration(old_models=[UserV1], new_models=[UserV2])

plan.impact_estimate_status          # "local_only"  (a local diff never invents an estimate)
plan.requires_live_cluster_estimate  # True          (a connected server is needed to size lock impact)
plan.lock_impact_estimate            # None          (no server consulted)

# A connected client would attach the server's estimate:
from aura import LockImpactEstimate
enriched = plan.with_server_estimate(
    LockImpactEstimate(lock_level="exclusive", blocking=True,
                       estimated_duration_ms=1500, affected_rows=10_000)
)
enriched.impact_estimate_status          # "server_estimated"
enriched.requires_live_cluster_estimate  # False
```

Local mode therefore never claims to compute live lock impact; it tells you, in a typed
field, that a live estimate is required and not yet available.

## What counts as destructive

Destructive changes require explicit approval:

- Dropping a model, field, index/uniqueness, or relationship.
- Changing a field's type (data may not coerce losslessly).
- Changing a vector field's dimension (stored vectors no longer match).
- Adding a `NOT NULL` field without a default (existing rows would violate it).
- Narrowing a nullable field to `NOT NULL`.

Additive changes (new nullable/defaulted fields, new indexes, new relationships, new
models) are safe.

## CI usage

```python
plan = generate_migration(committed_models, current_models)
if plan.is_empty:
    print("schema in sync")
else:
    print(plan.format())
    plan.require_safe()   # non-zero exit / raises on destructive drift
```

## CLI

The same diff is available offline via the CLI:

```bash
aura migrate diff --from old.schema.json --to myapp.models   # prints plan.format()
aura migrate diff --from old.schema.json --to new.schema.json --require-safe  # CI gate
```

`--from`/`--to` accept either a `.json` schema document or an importable models module.

## Honest limitation

AuraDB **lock/impact estimates** require a live server
and are **not** computed by the local diff. Rather than omit them, the plan exposes
`impact_estimate_status` (`local_only` by default), `requires_live_cluster_estimate`, and
`lock_impact_estimate` (see above) so local mode is explicit about what only a connected
cluster can provide. Destructive-change detection, human-reviewable output, and
rollback-strategy metadata are all implemented locally and
tested in `tests/unit/test_migrations.py`.
