"""SQLite backend: a complete local CRUD session with no external service.

SQLite is a first-class Aura backend. This example creates a temporary database, defines a
model, and runs inserts, a filtered/ordered query, an update, an upsert, and a count — all
through the same typed client API used for every backend.

Run: ``python examples/sqlite_backend.py`` (requires ``pip install aura-connector[sqlite]``)
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from aura import Aura, Field, Model


class Task(Model):
    id: int = Field(primary_key=True)
    title: str = Field(index=True)
    done: bool = Field(default=False)
    tags: list[str] = Field(default_factory=list)


async def main() -> None:
    path = Path(tempfile.mkdtemp()) / "aura_tasks.db"
    async with Aura.connect(f"sqlite:///{path}", models=[Task]) as db:
        print("backend:", db.backend.name)

        await db.insert(Task(id=1, title="write docs", tags=["docs"]))
        await db.bulk_insert(
            Task,
            [Task(id=2, title="ship release"), Task(id=3, title="review PR", done=True)],
        )

        pending = await db.query(Task).where(Task.done == False).order_by(Task.id.asc()).all()  # noqa: E712
        print("pending:", [t.title for t in pending])

        await db.update(Task).where(Task.id == 1).set(done=True).execute()
        await db.upsert(Task, key={Task.id: 2}, values={Task.done: True})

        done_count = await db.query(Task).where(Task.done == True).count()  # noqa: E712
        print("completed:", done_count, "of", await db.query(Task).count())

    path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
