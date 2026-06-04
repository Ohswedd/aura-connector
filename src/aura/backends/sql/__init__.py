"""Safe Query IR → SQL compilation shared by the SQL backends.

The SQL backends (SQLite, PostgreSQL, MySQL/MariaDB) translate the canonical Query IR into
parameterized SQL. Every user value is a *bound parameter* — never interpolated into the
statement text — and every identifier is quoted per the active dialect, so SQL injection is
impossible by construction. The compiler, dialects, parameter binder, and schema builder
live here and are reused across all three SQL backends; each backend only supplies its
driver glue and a :class:`~aura.backends.sql.dialects.Dialect`.
"""

from __future__ import annotations

from .compiler import SQLCompiler, SQLStatement
from .dialects import MYSQL, POSTGRES, SQLITE, Dialect
from .parameters import ParameterCollector, ParameterStyle
from .schema import SchemaBuilder

__all__ = [
    "SQLCompiler",
    "SQLStatement",
    "Dialect",
    "SQLITE",
    "POSTGRES",
    "MYSQL",
    "ParameterCollector",
    "ParameterStyle",
    "SchemaBuilder",
]
