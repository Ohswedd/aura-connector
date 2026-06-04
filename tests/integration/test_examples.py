"""Smoke tests that every example runs end-to-end without error."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
SCRIPTS = sorted(p.name for p in EXAMPLES.glob("*.py"))


def _load(name: str):
    path = EXAMPLES / name
    spec = importlib.util.spec_from_file_location(f"example_{name[:-3]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", SCRIPTS)
def test_example_runs(name: str) -> None:
    module = _load(name)
    main = module.main
    if asyncio.iscoroutinefunction(main):
        asyncio.run(main())
    else:
        main()
