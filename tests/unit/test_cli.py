"""Tests for the Aura CLI: version, doctor, schema compile, migrate diff."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aura import __version__
from aura.cli import main

_MODELS_SRC = """
from aura import AuraModel, Field


class Widget(AuraModel):
    id: int = Field(primary_key=True)
    name: str


class Gadget(AuraModel):
    id: int = Field(primary_key=True)
    label: str
"""

_MODELS_V2_SRC = """
from aura import AuraModel, Field


class Widget(AuraModel):
    id: int = Field(primary_key=True)
    name: str
    note: str | None = Field(default=None)
"""


@pytest.fixture
def models_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    module_name = "cli_test_models_v1"
    (tmp_path / f"{module_name}.py").write_text(_MODELS_SRC)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(module_name, None)
    return module_name


@pytest.fixture
def models_module_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    module_name = "cli_test_models_v2"
    (tmp_path / f"{module_name}.py").write_text(_MODELS_V2_SRC)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(module_name, None)
    return module_name


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_doctor_reports_environment(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert f"aura_version: {__version__}" in out
    assert "python_version:" in out
    assert "native_acceleration:" in out
    assert "native_backend:" in out


def test_doctor_reports_disabled_native(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AURA_DISABLE_NATIVE", "1")
    # Reload so the adapter recomputes its env-driven flags under the patched env.
    import importlib

    import aura._native as native_mod

    importlib.reload(native_mod)
    import aura.cli as cli_mod

    importlib.reload(cli_mod)
    try:
        assert cli_mod.main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "native_backend: pure-python" in out
        assert "disabled by AURA_DISABLE_NATIVE" in out
    finally:
        monkeypatch.delenv("AURA_DISABLE_NATIVE", raising=False)
        importlib.reload(native_mod)
        importlib.reload(cli_mod)


def test_explain_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ir = {
        "operation": "select",
        "model": "User",
        "filters": [{"op": "eq", "field": "id"}],
        "projection": ["id", "name"],
        "include": [{"link": "orders"}],
        "limit": 10,
        "vector": {"field": "embedding", "metric": "cosine", "query": [0.1, 0.2]},
        "consistency": "strong",
    }
    ir_file = tmp_path / "ir.json"
    ir_file.write_text(json.dumps(ir))
    assert main(["explain", "file", str(ir_file)]) == 0
    out = capsys.readouterr().out
    assert "operation: select" in out
    assert "model: User" in out
    assert "filters: 1 predicate(s)" in out
    assert "vector search:" in out
    assert "server" in out  # the cost-disclaimer note


def test_explain_file_missing_operation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ir_file = tmp_path / "bad.json"
    ir_file.write_text(json.dumps({"model": "User"}))
    assert main(["explain", "file", str(ir_file)]) == 1
    assert "error:" in capsys.readouterr().err


def test_bench_local(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bench", "local", "--iterations", "200"]) == 0
    out = capsys.readouterr().out
    assert "protocol_roundtrip" in out
    assert "vector_pack_roundtrip" in out
    assert "per_op=" in out


def test_generate_stubs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = (
        "from __future__ import annotations\n"
        "from aura import AuraModel, Field, Vector\n\n"
        "class Item(AuraModel):\n"
        "    id: int = Field(primary_key=True)\n"
        "    name: str\n"
        "    note: str | None = Field(default=None)\n"
        "    tags: list[str]\n"
        "    embedding: Vector[4]\n"
    )
    models_file = tmp_path / "stub_models.py"
    models_file.write_text(src)
    out_file = tmp_path / "out.pyi"
    assert main(["generate", "stubs", "--models", str(models_file), "--out", str(out_file)]) == 0
    text = out_file.read_text()
    assert "class Item(AuraModel):" in text
    assert "note: str | None" in text
    assert "tags: list[str]" in text
    assert "embedding: Vector[4]" in text
    # The generated stub must be syntactically valid Python.
    compile(text, str(out_file), "exec")


def test_schema_compile_to_stdout(capsys: pytest.CaptureFixture[str], models_module: str) -> None:
    assert main(["schema", "compile", "--app", models_module]) == 0
    document = json.loads(capsys.readouterr().out)
    names = {m["name"] for m in document["models"]}
    assert names == {"Widget", "Gadget"}


def test_schema_compile_to_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], models_module: str
) -> None:
    out_file = tmp_path / "schema.json"
    assert main(["schema", "compile", "--app", models_module, "--out", str(out_file)]) == 0
    document = json.loads(out_file.read_text())
    assert {m["name"] for m in document["models"]} == {"Widget", "Gadget"}
    assert "Wrote schema" in capsys.readouterr().out


def test_schema_compile_unknown_module_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "compile", "--app", "no.such.module.here"]) == 1
    assert "error:" in capsys.readouterr().err


def test_migrate_diff_between_modules(
    capsys: pytest.CaptureFixture[str], models_module: str, models_module_v2: str
) -> None:
    assert main(["migrate", "diff", "--from", models_module, "--to", models_module_v2]) == 0
    out = capsys.readouterr().out
    assert "Aura migration" in out
    # v2 drops Gadget and changes Widget -> destructive
    assert "DESTRUCTIVE" in out
    assert "Lock/impact estimate: local_only" in out


def test_migrate_diff_from_json_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], models_module: str
) -> None:
    # Compile a schema to JSON, then diff it against itself -> no changes.
    schema_file = tmp_path / "s.json"
    main(["schema", "compile", "--app", models_module, "--out", str(schema_file)])
    capsys.readouterr()
    assert main(["migrate", "diff", "--from", str(schema_file), "--to", str(schema_file)]) == 0
    assert "No schema changes detected" in capsys.readouterr().out


def test_migrate_diff_require_safe_gate(
    capsys: pytest.CaptureFixture[str], models_module: str, models_module_v2: str
) -> None:
    # Destructive diff with --require-safe must exit non-zero.
    code = main(
        ["migrate", "diff", "--from", models_module, "--to", models_module_v2, "--require-safe"]
    )
    assert code == 1
    assert "error:" in capsys.readouterr().err
