"""Generate a human-reviewable migration plan from two schema versions.

Compares an old and a new set of models, classifies each change as safe or destructive,
prints a reviewable plan with rollback steps, and shows the CI gate that refuses to apply
destructive changes without explicit approval.

Run: ``python examples/migrations.py``
"""

from __future__ import annotations

from aura import Field, Model, Vector, diff_schemas, generate_migration, schema_document
from aura.errors import AuraMigrationError


def _v2_models() -> list[type[Model]]:
    # A second revision of the same logical "User" model, defined in isolation so both
    # versions can be compiled independently.
    class User(Model):
        id: int = Field(primary_key=True)
        email: str = Field(unique=True, index=True)  # added unique constraint
        name: str
        nickname: str | None = None  # additive, safe
        embedding: Vector[256] | None = None  # additive vector, safe

    return [User]


def _v1_models() -> list[type[Model]]:
    class User(Model):
        id: int = Field(primary_key=True)
        email: str = Field(index=True)
        name: str

    return [User]


def main() -> None:
    old = _v1_models()
    new = _v2_models()

    plan = generate_migration(old, new)
    print(plan.format())
    print("Destructive:", plan.is_destructive)
    plan.require_safe()  # additive migration passes the CI safety gate

    # Now demonstrate a destructive migration: dropping a column.
    reverse = diff_schemas(schema_document(new), schema_document(old))
    print("\n--- Reverse (destructive) migration ---")
    print(reverse.format())
    try:
        reverse.require_safe()
    except AuraMigrationError as exc:
        print("CI gate correctly blocked destructive migration:")
        print(" ", exc.message)


if __name__ == "__main__":
    main()
