# Getting Started

## Install

```bash
python -m pip install -e ".[dev]"
```

Aura requires Python 3.11+.

## Your first query

The `aura+memory://` scheme runs a complete in-process reference server, so you can
build and test a full application without a live AuraDB cluster.

```python
import asyncio
from aura import Aura, Model, Field

class Task(Model):
    id: int = Field(primary_key=True)
    title: str
    done: bool = Field(default=False)

async def main() -> None:
    async with Aura.connect("aura+memory://localhost/todo", models=[Task]) as client:
        await client.insert(Task(id=1, title="Write docs"))
        await client.insert(Task(id=2, title="Ship release"))

        open_tasks = await client.query(Task).where(Task.done == False).all()  # noqa: E712
        print([t.title for t in open_tasks])

        await client.update(Task).where(Task.id == 1).set(done=True).execute()
        print("remaining:", await client.query(Task).where(Task.done == False).count())

asyncio.run(main())
```

## Connection lifecycle

`Aura.connect(...)` / `Client.connect(...)` returns a connector that is **both**
awaitable and an async context manager:

```python
# Context manager (recommended): closes automatically.
async with Aura.connect(dsn, models=[Task]) as client:
    ...

# Or await it and close explicitly.
client = await Aura.connect(dsn, models=[Task])
try:
    ...
finally:
    await client.close()
```

Using a closed client raises `AuraClientClosedError`; using an unopened one raises
`AuraConnectionError`.

## Where to go next

- [Models](MODELS.md), declaring typed models, fields, relationships, vectors.
- [Query Builder](QUERY_BUILDER.md), filtering, search, mutations, transactions.
- [Client](CLIENT.md), lifecycle, health, transactions, raw escape hatch.
- [Vectors](VECTORS.md), fixed-dimension vectors and similarity search.
