"""Unit tests for SQL DDL generation."""

from __future__ import annotations

from aura import AuraModel, Field, Vector
from aura.backends.sql.dialects import MYSQL, POSTGRES, SQLITE
from aura.backends.sql.schema import SchemaBuilder


class Account(AuraModel):
    id: int = Field(primary_key=True)
    email: str = Field(unique=True)
    handle: str = Field(index=True)
    bio: str | None = None
    balance: float = Field(default=0.0)
    tags: list[str] = Field(default_factory=list)
    embedding: Vector[4] | None = None


class Post(AuraModel):
    id: int = Field(primary_key=True)
    author: Account = Field(link=True)
    title: str


def test_create_table_has_primary_key_and_columns() -> None:
    ddl = SchemaBuilder(SQLITE).create_table(Account.__aura_schema__)
    create = ddl[0]
    assert create.startswith('CREATE TABLE IF NOT EXISTS "Account"')
    assert '"id" INTEGER PRIMARY KEY' in create
    assert '"email" TEXT UNIQUE' in create


def test_not_null_for_required_columns() -> None:
    create = SchemaBuilder(SQLITE).create_table(Account.__aura_schema__)[0]
    assert '"handle" TEXT NOT NULL' in create
    # Nullable and defaulted columns are not NOT NULL.
    assert '"bio" TEXT' in create and '"bio" TEXT NOT NULL' not in create


def test_index_statements_emitted_for_indexed_fields() -> None:
    ddl = SchemaBuilder(SQLITE).create_table(Account.__aura_schema__)
    index_statements = [s for s in ddl if s.startswith("CREATE INDEX")]
    assert any('"idx_Account_handle"' in s for s in index_statements)
    # Unique/primary-key columns are not given a redundant secondary index.
    assert not any("email" in s for s in index_statements)


def test_json_and_vector_columns_use_json_type() -> None:
    create = SchemaBuilder(SQLITE).create_table(Account.__aura_schema__)[0]
    assert '"tags" TEXT' in create
    assert '"embedding" TEXT' in create


def test_link_relationship_becomes_fk_column() -> None:
    create = SchemaBuilder(SQLITE).create_table(Post.__aura_schema__)[0]
    # The to-one link 'author' is a scalar FK column typed from Account's int primary key.
    assert '"author" INTEGER' in create


def test_drop_table() -> None:
    ddl = SchemaBuilder(POSTGRES).drop_table(Account.__aura_schema__)
    assert ddl == ['DROP TABLE IF EXISTS "Account"']


def test_mysql_uses_backticks_and_bounded_strings() -> None:
    create = SchemaBuilder(MYSQL).create_table(Account.__aura_schema__)[0]
    assert "`Account`" in create
    assert "`email` VARCHAR(255) UNIQUE" in create
