"""SQL dialects: identifier quoting, type mapping, and value adaptation.

A :class:`Dialect` captures the per-database differences the compiler needs: the parameter
placeholder style, how identifiers are quoted, how model field types map to column types,
and whether the database supports ``RETURNING`` and ``INSERT … ON CONFLICT``/``ON DUPLICATE
KEY`` upserts. Three instances are provided: :data:`SQLITE`, :data:`POSTGRES`, :data:`MYSQL`.

Type strategy. Scalars that the model layer already normalizes to canonical strings
(``datetime``, ``date``, ``uuid``, ``Decimal``) are stored as text. Containers (lists and
documents), vectors, and binary values are stored as JSON text — the portable *JSON field
fallback* and *vector storage fallback* the connector documents for backends without a
native column type for them. Identifier quoting doubles the dialect quote character and
rejects embedded NULs, so identifiers can never break out of their quotes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import AuraDialectError
from ...schema.model_schema import FieldSchema
from .parameters import ParameterStyle

__all__ = ["Dialect", "SQLITE", "POSTGRES", "MYSQL"]

# Field type labels (from ``ModelSchema``) that are stored as JSON text on SQL backends.
_JSON_TYPES = frozenset({"vector", "list", "document", "bytes"})
# Field type labels stored as canonical text (the model layer emits ISO/str forms).
_TEXT_SCALARS = frozenset({"str", "datetime", "date", "time", "UUID", "Decimal", "any"})


@dataclass(frozen=True)
class Dialect:
    """Per-database SQL generation rules."""

    name: str
    paramstyle: ParameterStyle
    quote_char: str
    supports_returning: bool
    supports_upsert: bool
    autoincrement_int: str
    type_int: str
    type_float: str
    type_bool: str
    type_text: str
    type_json: str
    type_str_key: str  # text type usable as a key/index (MySQL needs a length)

    def quote(self, identifier: str) -> str:
        """Quote an identifier safely for this dialect."""
        if "\x00" in identifier:
            raise AuraDialectError("Identifier contains a NUL byte", context={"dialect": self.name})
        q = self.quote_char
        return f"{q}{identifier.replace(q, q + q)}{q}"

    def is_json_type(self, field: FieldSchema) -> bool:
        """Whether ``field`` is stored as JSON text (containers, vectors, binary)."""
        return (
            field.container in {"list", "dict"}
            or field.type in _JSON_TYPES
            or field.vector_dim is not None
        )

    def column_type(self, field: FieldSchema) -> str:
        """Return the SQL column type for ``field``."""
        if self.is_json_type(field):
            return self.type_json
        type_label = field.type
        if type_label == "int":
            return self.type_int
        if type_label == "float":
            return self.type_float
        if type_label == "bool":
            return self.type_bool
        if type_label in _TEXT_SCALARS:
            # A key/indexed string needs a bounded type on some dialects (MySQL).
            if type_label == "str" and (field.primary_key or field.unique or field.index):
                return self.type_str_key
            return self.type_text
        # Unknown/opaque types (including model-name FK labels) fall back to text.
        return self.type_text


SQLITE = Dialect(
    name="sqlite",
    paramstyle=ParameterStyle.QMARK,
    quote_char='"',
    supports_returning=True,  # SQLite >= 3.35
    supports_upsert=True,
    autoincrement_int="INTEGER",
    type_int="INTEGER",
    type_float="REAL",
    type_bool="INTEGER",
    type_text="TEXT",
    type_json="TEXT",
    type_str_key="TEXT",
)

POSTGRES = Dialect(
    name="postgres",
    paramstyle=ParameterStyle.NUMERIC,
    quote_char='"',
    supports_returning=True,
    supports_upsert=True,
    autoincrement_int="BIGINT",
    type_int="BIGINT",
    type_float="DOUBLE PRECISION",
    type_bool="BOOLEAN",
    type_text="TEXT",
    type_json="TEXT",
    type_str_key="TEXT",
)

MYSQL = Dialect(
    name="mysql",
    paramstyle=ParameterStyle.FORMAT,
    quote_char="`",
    supports_returning=False,  # MySQL/MariaDB lack RETURNING in the portable subset
    supports_upsert=True,
    autoincrement_int="BIGINT",
    type_int="BIGINT",
    type_float="DOUBLE",
    type_bool="TINYINT(1)",
    type_text="LONGTEXT",
    type_json="LONGTEXT",
    type_str_key="VARCHAR(255)",
)
