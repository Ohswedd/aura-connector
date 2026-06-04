"""Generate CREATE/DROP DDL for a model schema in a given SQL dialect.

The builder turns a :class:`~aura.schema.model_schema.ModelSchema` into idempotent DDL:
one ``CREATE TABLE IF NOT EXISTS`` with column definitions, primary key, uniqueness and
NOT NULL constraints, plus a ``CREATE INDEX`` per indexed field. To-one relationships
(``link``) become foreign-key scalar columns typed from the target's primary key; to-many
relationships are not columns (they are derived from the reverse link) and are skipped.

DDL identifiers are emitted through the dialect's quoting, and the builder never
interpolates user-supplied values — DDL carries structure (names, types), not row data.
"""

from __future__ import annotations

from ...models import get_model
from ...schema.model_schema import FieldSchema, ModelSchema, RelationshipSchema
from .dialects import Dialect

__all__ = ["SchemaBuilder"]


class SchemaBuilder:
    """Build dialect-specific DDL statements from a model schema."""

    def __init__(self, dialect: Dialect) -> None:
        self._d = dialect

    def create_table(self, schema: ModelSchema) -> list[str]:
        """Return the DDL statements that create ``schema``'s table and indexes."""
        d = self._d
        table = d.quote(schema.name)
        columns: list[str] = []
        single_pk = schema.primary_key

        for field in schema.fields:
            columns.append(self._column_def(field, single_pk))
        for rel in schema.relationships:
            if rel.kind == "link":
                columns.append(self._fk_column_def(rel))

        body = ",\n  ".join(columns)
        statements = [f"CREATE TABLE IF NOT EXISTS {table} (\n  {body}\n)"]

        for field in schema.fields:
            if field.index and not field.primary_key and not field.unique:
                index_name = d.quote(f"idx_{schema.name}_{field.name}")
                statements.append(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({d.quote(field.name)})"
                )
        return statements

    def drop_table(self, schema: ModelSchema) -> list[str]:
        """Return the DDL statements that drop ``schema``'s table."""
        return [f"DROP TABLE IF EXISTS {self._d.quote(schema.name)}"]

    # -- column rendering --------------------------------------------------------
    def _column_def(self, field: FieldSchema, single_pk: str | None) -> str:
        d = self._d
        parts = [d.quote(field.name), d.column_type(field)]
        if field.primary_key and field.name == single_pk:
            parts.append("PRIMARY KEY")
        else:
            if field.unique:
                parts.append("UNIQUE")
            if not field.nullable and not field.has_default:
                parts.append("NOT NULL")
        return " ".join(parts)

    def _fk_column_def(self, rel: RelationshipSchema) -> str:
        d = self._d
        return f"{d.quote(rel.name)} {self._fk_type(rel)}"

    def _fk_type(self, rel: RelationshipSchema) -> str:
        """Resolve the column type of a foreign-key column from the target's primary key."""
        target = get_model(rel.target)
        if target is not None:
            schema = target.__aura_schema__
            if schema.primary_key is not None:
                pk = schema.field(schema.primary_key)
                if pk is not None:
                    return self._d.column_type(pk)
        return self._d.type_text
