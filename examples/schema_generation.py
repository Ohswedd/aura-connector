"""Generate a deterministic schema document from models.

Run: ``python examples/schema_generation.py``
"""

from __future__ import annotations

import json

from aura import Field, Model, Vector, schema_document


class Workspace(Model):
    id: int = Field(primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str


class Document(Model):
    id: int = Field(primary_key=True)
    workspace: Workspace = Field(link=True, on_delete="cascade")
    title: str = Field(index=True)
    body: str
    metadata: dict[str, str] = Field(default_factory=dict)
    embedding: Vector[1536] = Field(vector_index="hnsw")


def main() -> None:
    document = schema_document([Workspace, Document])
    print(json.dumps(document, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
