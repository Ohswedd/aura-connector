# CLI

The `aura` command (console-script entry point in `pyproject.toml`,
implemented in `src/aura/cli.py`) exposes the connector's **offline,
server-independent** operations. Every command is backed by the same tested
library functions used at runtime. The CLI has no import-time side effects and
never opens a network connection.

```bash
aura --help
```

## Commands

### `aura version`
Print the installed package version.

### `aura doctor`
Report environment and optional-dependency status, including native acceleration:

```
aura_version: 0.1.0
python_version: 3.13.1
opentelemetry_installed: False
native_acceleration: unavailable (pure-python fallback active)
native_backend: pure-python
```

`native_acceleration` is one of:
- `available (backend: aura_native, version: …)`
- `unavailable (pure-python fallback active)`
- `disabled by AURA_DISABLE_NATIVE (pure-python fallback active)`

You can also query this programmatically:

```bash
python -c "import aura; print(aura.native_status())"
```

### `aura schema compile --app <module> [--out <file>]`
Compile an application's `AuraModel` subclasses into a deterministic schema
document (`aura.schema.schema_document`). Prints JSON to stdout or writes a file.

### `aura migrate diff --from <a> --to <b> [--require-safe]`
Diff two schemas (each a `.json` schema file or an importable models module) into
a reviewable `MigrationPlan`. `--require-safe` exits non-zero on destructive
changes (a CI gate).

### `aura explain file <ir.json>`
Render a deterministic **client-side static plan** for a Query IR JSON document , 
the same IR the query builder emits. It reports the operation, target model,
filter/projection/sort shape, pagination, retrieval modality (scan/index, vector,
text, or hybrid), and consistency. It deliberately makes **no** cardinality or
cost claim: server-side EXPLAIN cost (row counts, index selection, execution cost)
requires a live AuraDB cluster and is not synthesised here.

```bash
aura explain file query-ir.json
```

### `aura bench local [--iterations N]`
Run real, in-process micro-benchmarks of the connector's hot paths (protocol
encode/decode round-trip and vector pack/unpack round-trip) and print measured
timings. Uses the active native backend if available. No numbers are fabricated.

```bash
aura bench local --iterations 20000
```

### `aura generate stubs --models <module|file.py> [--out <file>]`
Generate a faithful `.pyi` type stub from an application's models by reflecting
their declared annotations (`Vector[N]`, `list[T]`, `T | None`, nested models).
The generated stub is validated to be syntactically valid Python before output.

```bash
aura generate stubs --models app/models.py --out app/models.pyi
```

## Connector / server boundary

These belong to the **AuraDB server**, not the connector, and are intentionally
not implemented or faked in the CLI:

| Command | Why it is server-dependent |
|---|---|
| `migrate apply` | Applies a plan against a live cluster (locks, online DDL, transactional DDL). The connector produces and gates the plan (`migrate diff`); execution is the server's. |
| `explain` cost | Cardinality estimates, index selection, and execution cost are computed by the server's planner against live statistics. The connector renders only the client-side static plan (`explain file`). |
| distributed transactions | Cross-shard isolation and coordination are server responsibilities; the client drives begin/commit/rollback over the protocol. |

If a future command genuinely needs a cluster, it will fail with a structured
`AuraError` explaining the missing dependency rather than returning empty output.

See [`AURADB_BOUNDARY.md`](AURADB_BOUNDARY.md) for the
full connector and server responsibility tables.
