"""Aura command-line interface.

The CLI exposes the *offline*, server-independent operations that the connector can
perform without a live AuraDB cluster, each backed by the same implemented and tested
library functions used at runtime:

* ``aura version``          — print the installed package version.
* ``aura doctor``           — report environment, optional-dependency, and native
  acceleration status.
* ``aura schema compile``   — compile an application's models into a deterministic
  schema document (:func:`aura.schema.schema_document`).
* ``aura migrate diff``     — diff two schemas into a reviewable
  :class:`~aura.schema.MigrationPlan` (:func:`aura.schema.diff_schemas`), with an
  optional ``--require-safe`` CI gate.
* ``aura explain file``     — render a deterministic *client-side* static plan for a
  Query IR JSON document (the same IR the query builder emits). This is the
  connector's local plan view; server-side cost (cardinality, index choice) requires
  a live cluster and is intentionally not synthesised here.
* ``aura bench local``      — run real, in-process micro-benchmarks of the connector's
  hot paths (protocol round-trip, vector packing) and print measured timings.
* ``aura generate stubs``   — generate a faithful ``.pyi`` type stub from an
  application's models by reflecting their declared annotations.

Commands that require a live cluster (``migrate apply``, server-side ``explain`` cost
estimation, distributed transactions) belong to the AuraDB server and are not
synthesised here; see ``docs/CLI.md`` and ``docs/AURADB_BOUNDARY.md`` for
the connector/server boundary. This module has no import-time side effects and never
opens a network connection.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import time
import types
import typing
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from ._native import native_status
from .errors import AuraError, AuraQueryError, AuraSchemaError
from .models import AuraModel
from .schema import diff_schemas, schema_document
from .vectors import is_vector_type

__all__ = ["build_parser", "main"]


def _load_models(module_path: str) -> list[type[AuraModel]]:
    """Import ``module_path`` and return the :class:`AuraModel` subclasses it defines."""
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise AuraSchemaError(
            f"Could not import models module {module_path!r}: {exc}",
            context={"module": module_path},
        ) from exc
    models: list[type[AuraModel]] = []
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and issubclass(value, AuraModel)
            and value is not AuraModel
            and value.__module__ == module.__name__
        ):
            models.append(value)
    if not models:
        raise AuraSchemaError(
            f"No AuraModel subclasses found in {module_path!r}",
            context={"module": module_path},
        )
    models.sort(key=lambda m: m.__name__)
    return models


def _read_schema_source(source: str) -> Any:
    """Resolve a schema source: a ``.json`` file path, or an importable models module."""
    if source.endswith(".json"):
        with open(source, encoding="utf-8") as handle:
            return json.load(handle)
    return schema_document(_load_models(source))


def _cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    def _present(module: str) -> bool:
        try:
            importlib.import_module(module)
        except ImportError:
            return False
        return True

    status = native_status()
    if status["available"]:
        native_line = f"available (backend: {status['backend']}, version: {status['version']})"
    elif status["disabled_by_env"]:
        native_line = "disabled by AURA_DISABLE_NATIVE (pure-python fallback active)"
    else:
        native_line = "unavailable (pure-python fallback active)"

    # Backend driver availability. The base install is dependency-free; each driver is an
    # optional extra. "available" means the matching backend can connect in this environment.
    backend_drivers = {
        "sqlite": "aiosqlite",
        "postgres": "asyncpg",
        "mysql": "aiomysql",
        "mongodb": "motor",
        "redis": "redis",
    }
    backends = {
        extra: "available" if _present(module) else f"install: aura-connector[{extra}]"
        for extra, module in backend_drivers.items()
    }

    report = {
        "aura_version": __version__,
        "python_version": ".".join(str(p) for p in sys.version_info[:3]),
        "opentelemetry_installed": _present("opentelemetry"),
        # Native acceleration is an optional in-repository extra (crates/aura_native),
        # installed via `pip install aura-connector[native]`. The connector runs
        # complete in pure Python whether or not it is present.
        "native_acceleration": native_line,
        "native_backend": status["backend"],
    }
    for key, value in report.items():
        print(f"{key}: {value}")
    print("backends:")
    for extra, state in backends.items():
        print(f"  {extra}: {state}")
    return 0


def _load_module_from_source(source: str) -> types.ModuleType:
    """Import a models module given either a dotted path or a filesystem path."""
    path = Path(source)
    if path.suffix == ".py" or path.exists():
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise AuraSchemaError(
                f"Could not load models from file {source!r}", context={"path": source}
            )
        module = importlib.util.module_from_spec(spec)
        # Register before executing so model metaclasses can resolve string
        # annotations against the module's own globals (PEP 563 deferred evaluation).
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except (ImportError, FileNotFoundError, SyntaxError, AuraError) as exc:
            sys.modules.pop(spec.name, None)
            raise AuraSchemaError(
                f"Could not load models from file {source!r}: {exc}",
                context={"path": source},
            ) from exc
        return module
    try:
        return importlib.import_module(source)
    except ImportError as exc:
        raise AuraSchemaError(
            f"Could not import models module {source!r}: {exc}", context={"module": source}
        ) from exc


def _models_in_module(module: types.ModuleType) -> list[type[AuraModel]]:
    models = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, AuraModel)
        and value is not AuraModel
        and value.__module__ == module.__name__
    ]
    if not models:
        raise AuraSchemaError(
            f"No AuraModel subclasses found in {module.__name__!r}",
            context={"module": module.__name__},
        )
    models.sort(key=lambda m: m.__name__)
    return models


def _render_annotation(tp: Any) -> str:
    """Render a type annotation back into faithful source text (deterministic)."""
    if tp is type(None):
        return "None"
    if is_vector_type(tp):
        dim = getattr(tp, "dim", None)
        return f"Vector[{dim}]" if dim is not None else "Vector"
    origin = typing.get_origin(tp)
    if origin is None:
        if isinstance(tp, type):
            return tp.__name__
        return str(tp)
    args = typing.get_args(tp)
    if origin in (types.UnionType, typing.Union):
        return " | ".join(_render_annotation(a) for a in args)
    name = getattr(origin, "__name__", None) or str(origin)
    if args:
        return f"{name}[{', '.join(_render_annotation(a) for a in args)}]"
    return name


def _generate_stub(models: list[type[AuraModel]]) -> str:
    """Build a ``.pyi`` stub reflecting each model's declared field annotations."""
    lines = [
        "# Auto-generated by `aura generate stubs`. Reflects declared model annotations.",
        "from aura import AuraModel, Vector",
        "",
    ]
    for model in models:
        hints = typing.get_type_hints(model)
        lines.append(f"class {model.__name__}(AuraModel):")
        field_names = list(model.__aura_fields__)
        if not field_names:
            lines.append("    ...")
            lines.append("")
            continue
        for fname in field_names:
            annotation = hints.get(fname)
            rendered = _render_annotation(annotation) if annotation is not None else "Any"
            lines.append(f"    {fname}: {rendered}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cmd_schema_compile(args: argparse.Namespace) -> int:
    document = schema_document(_load_models(args.app))
    text = json.dumps(document, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"Wrote schema for {len(document['models'])} model(s) to {args.out}")
    else:
        print(text)
    return 0


def _cmd_migrate_diff(args: argparse.Namespace) -> int:
    old = _read_schema_source(args.from_)
    new = _read_schema_source(args.to)
    plan = diff_schemas(old, new)
    print(plan.format())
    if args.require_safe:
        plan.require_safe()
    return 0


def _explain_ir(ir: Any) -> str:
    """Render a deterministic, client-side static plan for a Query IR document.

    This describes only what the connector knows locally: the operation, target,
    filter/projection/sort shape, pagination, and retrieval modality. It deliberately
    makes no cardinality or cost claim — server-side EXPLAIN cost requires a cluster.
    """
    if not isinstance(ir, dict):
        raise AuraQueryError("Query IR must be a JSON object")
    operation = ir.get("operation")
    if not operation:
        raise AuraQueryError("Query IR is missing the required 'operation' field")

    lines = ["Aura client-side static plan (no server cost estimate)"]
    lines.append(f"  operation: {operation}")
    if ir.get("model"):
        lines.append(f"  model: {ir['model']}")
    if ir.get("statement"):
        lines.append(f"  statement: {ir['statement']}")

    filters = ir.get("filters")
    if isinstance(filters, list):
        lines.append(f"  filters: {len(filters)} predicate(s)")
    projection = ir.get("projection")
    if isinstance(projection, list):
        lines.append(f"  projection: {', '.join(map(str, projection)) or '(all)'}")
    includes = ir.get("include")
    if isinstance(includes, list) and includes:
        rels = ", ".join(str(i.get("link", i)) for i in includes)
        lines.append(f"  include: {rels}")
    sort = ir.get("sort")
    if isinstance(sort, list) and sort:
        lines.append(f"  sort: {len(sort)} term(s)")
    if "limit" in ir:
        lines.append(f"  limit: {ir['limit']}")
    if "offset" in ir:
        lines.append(f"  offset: {ir['offset']}")
    vector = ir.get("vector")
    if isinstance(vector, dict):
        dims = len(vector.get("query", []))
        lines.append(
            f"  vector search: field={vector.get('field')} "
            f"metric={vector.get('metric')} dims={dims}"
        )
    text = ir.get("text")
    if isinstance(text, dict):
        lines.append(f"  text search: fields={text.get('fields')}")
    if "fusion" in ir:
        lines.append(f"  hybrid fusion: {ir['fusion']}")
    if ir.get("consistency"):
        lines.append(f"  consistency: {ir['consistency']}")
    if ir.get("timeout_ms") is not None:
        lines.append(f"  timeout_ms: {ir['timeout_ms']}")

    retrieval = "vector" if "vector" in ir else "text" if "text" in ir else "scan/index"
    if "vector" in ir and "text" in ir:
        retrieval = "hybrid (vector + text)"
    lines.append(f"  retrieval modality (client view): {retrieval}")
    lines.append(
        "  note: row counts, index selection, and execution cost are determined by the "
        "AuraDB server at run time."
    )
    return "\n".join(lines)


def _cmd_explain_file(args: argparse.Namespace) -> int:
    with open(args.path, encoding="utf-8") as handle:
        ir = json.load(handle)
    print(_explain_ir(ir))
    return 0


def _cmd_bench_local(args: argparse.Namespace) -> int:
    from .protocol.codec import decode_frame, encode_frame
    from .protocol.frames import Frame
    from .protocol.opcodes import Opcode
    from .vectors import Vector

    iters = args.iterations
    if iters <= 0:
        raise AuraError("--iterations must be a positive integer")

    payload = b'{"id":1,"name":"aura"}' * 16
    frame = Frame(opcode=Opcode.QUERY, payload=payload, request_id=1, transaction_id=1)
    vec = Vector([float(i) / 256 for i in range(256)], dim=256)

    def protocol_roundtrip() -> None:
        data = encode_frame(frame, payload_checksum=True)
        decode_frame(data)

    def vector_pack_roundtrip() -> None:
        Vector.from_bytes(vec.pack(), 256)

    status = native_status()
    print(f"aura bench local — {iters} iterations (native backend: {status['backend']})")
    for name, fn in (
        ("protocol_roundtrip", protocol_roundtrip),
        ("vector_pack_roundtrip", vector_pack_roundtrip),
    ):
        for _ in range(min(100, iters)):  # warm up
            fn()
        start = time.perf_counter()
        for _ in range(iters):
            fn()
        elapsed = time.perf_counter() - start
        per_op_us = (elapsed / iters) * 1_000_000
        print(f"  {name:<24} total={elapsed * 1000:8.2f}ms  per_op={per_op_us:8.3f}us")
    return 0


def _cmd_generate_stubs(args: argparse.Namespace) -> int:
    module = _load_module_from_source(args.models)
    models = _models_in_module(module)
    text = _generate_stub(models)
    # Fail loudly rather than emit a stub that does not parse.
    compile(text, args.out or "<aura-stub>", "exec")
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote stubs for {len(models)} model(s) to {args.out}")
    else:
        print(text, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``aura`` argument parser (also used by tests)."""
    parser = argparse.ArgumentParser(prog="aura", description="Aura connector CLI")
    parser.add_argument("--version", action="version", version=f"aura {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print the installed Aura version").set_defaults(
        func=_cmd_version
    )
    sub.add_parser("doctor", help="Report environment and dependency status").set_defaults(
        func=_cmd_doctor
    )

    schema = sub.add_parser("schema", help="Schema tools")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)
    compile_p = schema_sub.add_parser("compile", help="Compile models into a schema document")
    compile_p.add_argument("--app", required=True, help="Importable models module, e.g. app.models")
    compile_p.add_argument("--out", help="Write JSON to this path instead of stdout")
    compile_p.set_defaults(func=_cmd_schema_compile)

    migrate = sub.add_parser("migrate", help="Migration tools")
    migrate_sub = migrate.add_subparsers(dest="migrate_command", required=True)
    diff_p = migrate_sub.add_parser("diff", help="Diff two schemas into a migration plan")
    diff_p.add_argument(
        "--from", dest="from_", required=True, help="Old schema: a .json file or models module"
    )
    diff_p.add_argument("--to", required=True, help="New schema: a .json file or models module")
    diff_p.add_argument(
        "--require-safe",
        action="store_true",
        help="Exit non-zero if the plan contains destructive changes (CI gate)",
    )
    diff_p.set_defaults(func=_cmd_migrate_diff)

    explain = sub.add_parser("explain", help="Explain a query (client-side static plan)")
    explain_sub = explain.add_subparsers(dest="explain_command", required=True)
    explain_file = explain_sub.add_parser(
        "file", help="Render a client-side static plan for a Query IR JSON file"
    )
    explain_file.add_argument("path", help="Path to a Query IR JSON document")
    explain_file.set_defaults(func=_cmd_explain_file)

    bench = sub.add_parser("bench", help="Local benchmarks")
    bench_sub = bench.add_subparsers(dest="bench_command", required=True)
    bench_local = bench_sub.add_parser(
        "local", help="Run in-process micro-benchmarks of the connector hot paths"
    )
    bench_local.add_argument(
        "--iterations", type=int, default=20000, help="Iterations per benchmark (default 20000)"
    )
    bench_local.set_defaults(func=_cmd_bench_local)

    generate = sub.add_parser("generate", help="Code generation from models")
    generate_sub = generate.add_subparsers(dest="generate_command", required=True)
    stubs_p = generate_sub.add_parser(
        "stubs", help="Generate a .pyi type stub reflecting model annotations"
    )
    stubs_p.add_argument(
        "--models", required=True, help="Models module (dotted path) or a .py file path"
    )
    stubs_p.add_argument("--out", help="Write the stub to this path instead of stdout")
    stubs_p.set_defaults(func=_cmd_generate_stubs)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except AuraError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
