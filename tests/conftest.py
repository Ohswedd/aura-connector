"""Shared test fixtures and models."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura import AuraModel, Field, Vector
from aura.client import Client
from aura.config import parse_dsn
from aura.transport.memory import MemoryTransport, ReferenceServer


class Workspace(AuraModel):
    id: int = Field(primary_key=True)
    name: str


class User(AuraModel):
    id: int = Field(primary_key=True)
    workspace: Workspace = Field(link=True)
    name: str
    email: str = Field(unique=True, index=True)
    embedding: Vector[4] | None = None


class Order(AuraModel):
    id: int = Field(primary_key=True)
    user: User = Field(link=True)
    amount_cents: int
    note: str | None = Field(default=None)


class Document(AuraModel):
    id: int = Field(primary_key=True)
    workspace: Workspace = Field(link=True)
    title: str
    body: str
    metadata: dict[str, str] = Field(default_factory=dict)
    embedding: Vector[3]


ALL_MODELS = [Workspace, User, Order, Document]


@pytest.fixture
def models() -> SimpleNamespace:
    """Shared model classes, provided as a fixture to avoid cross-package imports."""
    return SimpleNamespace(Workspace=Workspace, User=User, Order=Order, Document=Document)


@pytest.fixture
def server() -> ReferenceServer:
    return ReferenceServer()


@pytest.fixture
async def client(server: ReferenceServer):
    config = parse_dsn("aura+memory://localhost/test")
    transport = MemoryTransport(server)
    client = Client(config, transport, ALL_MODELS)
    await client._open()
    try:
        yield client
    finally:
        await client.close()
