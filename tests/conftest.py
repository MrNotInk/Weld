"""Shared fixtures for tests that call Weld helpers in-process."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def weld_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # Import after setting HOME so every Weld path stays inside the test sandbox.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = Path(__file__).resolve().parents[1] / "weld.py"
    spec = importlib.util.spec_from_file_location("weld_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
