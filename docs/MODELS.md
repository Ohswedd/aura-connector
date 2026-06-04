# Models

A model is a Python class deriving from `AuraModel` (exported as `Model`). The
metaclass compiles annotations and `Field(...)` markers into an immutable schema and
installs typed descriptors.

```python
from datetime import datetime
from aura import Model, Field, Vector

class Workspace(Model):
    id: int = Field(primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str

class User(Model):
    id: int = Field(primary_key=True)
    workspace: Workspace = Field(link=True)      # to-one relationship
    email: str = Field(unique=True, index=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    embedding: Vector[768] | None = None
```

## Field options

`Field(...)` accepts:

| Option | Meaning |
|---|---|
| `primary_key` | Marks the primary key (one per model). |
| `unique` | Unique constraint hint. |
| `index` | Index hint. |
| `nullable` | Allows `None` (implied by `T | None`). |
| `default` | Static default value. |
| `default_factory` | Callable producing a fresh default. |
| `alias` | Serialized/storage name. |
| `description` | Human description, surfaced in the schema. |
| `metadata` | Arbitrary non-sensitive metadata dict. |
| `link` | Declares a relationship field (with a model-typed annotation). |
| `on_delete` | `cascade` / `restrict` / `set_null`. |
| `vector_index` | Vector index hint, e.g. `"hnsw"`. |

Invalid configurations raise `AuraSchemaError` at class-definition time (e.g. setting
both `default` and `default_factory`, or `primary_key=True` with `nullable=True`).

## Class vs instance access

Class-level access returns a query expression; instance-level access returns the value:

```python
User.email                 # FieldReference  -> use in queries
User.email == "a@b.com"    # Comparison      -> a filter predicate

user = User(id=1, workspace=ws, email="a@b.com", name="Ann")
user.email                 # "a@b.com"
```

## Relationships and explicit loading

Aura never performs hidden lazy network IO. Accessing a relationship that
was not loaded raises `RelationshipNotLoadedError`:

```python
user = await client.User.find(id=1)
user.workspace             # RelationshipNotLoadedError

user = await client.query(User).where(User.id == 1).include("workspace").one()
user.workspace.name        # "Acme" (loaded because it was included)
```

## Construction, validation, serialization

- Required fields (no default, not nullable, not a relationship) must be supplied;
  otherwise `AuraValidationError` is raised.
- Vector fields validate dimensionality; nested-model fields accept an instance or a
  mapping.
- `model.to_dict()` returns a JSON-able dict keyed by storage name (alias-aware).
- `model.primary_key_value()` returns the primary key.

## Hydration hook

Database hydration uses a dedicated path (`_aura_construct`) that bypasses `__init__`
and invokes the optional `__aura_post_load__()` hook:

```python
class Order(Model):
    id: int = Field(primary_key=True)
    amount_cents: int

    def __aura_post_load__(self) -> None:
        # Runs after rows are hydrated from the database, not on manual construction.
        ...
```

## Schema generation

```python
from aura import schema_document, schema_json

schema_document([Workspace, User])   # deterministic dict
schema_json([Workspace, User])       # canonical (sorted-key) JSON string
```

Output is deterministic and order-independent, so it can be committed and diffed in CI. See [`examples/schema_generation.py`](../examples/schema_generation.py).
