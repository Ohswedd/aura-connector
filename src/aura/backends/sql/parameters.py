"""Bound-parameter accumulation across the supported SQL parameter styles.

A :class:`ParameterCollector` records each user value in order and returns the dialect's
placeholder for it. The compiler never writes a value into the SQL text; it only ever emits
placeholders, so the statement string is fixed by the query *shape* and the values travel
separately to the driver. This is the mechanism that makes the compiler injection-safe.

Supported styles:

* ``ParameterStyle.QMARK``   — ``?`` positional (SQLite / aiosqlite)
* ``ParameterStyle.NUMERIC`` — ``$1, $2, …`` (PostgreSQL / asyncpg)
* ``ParameterStyle.FORMAT``  — ``%s`` positional (MySQL / aiomysql)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

__all__ = ["ParameterCollector", "ParameterStyle"]


class ParameterStyle(StrEnum):
    """The placeholder syntaxes the SQL backends emit."""

    QMARK = "qmark"
    NUMERIC = "numeric"
    FORMAT = "format"


class ParameterCollector:
    """Collects bound values in order and renders the right placeholder for each."""

    __slots__ = ("_style", "values")

    def __init__(self, style: ParameterStyle) -> None:
        self._style = style
        self.values: list[Any] = []

    @property
    def style(self) -> ParameterStyle:
        return self._style

    def add(self, value: Any) -> str:
        """Bind ``value`` and return its placeholder string."""
        self.values.append(value)
        if self._style is ParameterStyle.NUMERIC:
            return f"${len(self.values)}"
        if self._style is ParameterStyle.FORMAT:
            return "%s"
        return "?"

    def add_many(self, values: list[Any]) -> list[str]:
        """Bind several values, returning their placeholders in order."""
        return [self.add(value) for value in values]
