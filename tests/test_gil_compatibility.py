"""Startup GIL compatibility without hiding warnings or claiming thread safety."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GIL_SHEBANG = b"#!/usr/bin/env -S python3 -X gil=1\n"
PROBE = (
    b"from __future__ import annotations\n"
    b"import json, sys\n"
    b"print(json.dumps({'gil': sys._xoptions.get('gil'), 'args': sys.argv[1:], "
    b"'enabled': getattr(sys, '_is_gil_enabled', lambda: True)(), "
    b"'optimize': sys.flags.optimize}))\n"
)


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
    return env


@pytest.mark.parametrize(
    ("shebang", "expected"),
    [
        pytest.param(b"", DEFAULT_GIL_SHEBANG, id="default"),
        pytest.param(
            b"#!/usr/bin/env python3\n", DEFAULT_GIL_SHEBANG, id="existing-env",
        ),
        pytest.param(
            f"#!{sys.executable} -u\n".encode(),
            f"#!/usr/bin/env -S {sys.executable} -u -X gil=1\n".encode(),
            id="absolute-interpreter-and-flags",
        ),
        pytest.param(
            b"#!/usr/bin/env -S python3 -u -O\n",
            b"#!/usr/bin/env -S python3 -u -O -X gil=1\n",
            id="preserve-env-s-flags",
        ),
        pytest.param(
            b"#!/usr/bin/env --split-string=python3 -u\n",
            b"#!/usr/bin/env -S python3 -u -X gil=1\n",
            id="long-split-string-option",
        ),
        pytest.param(
            b"#!/usr/bin/env --split-string\tpython3 -u\n",
            b"#!/usr/bin/env -S python3 -u -X gil=1\n",
            id="tab-separated-split-string-option",
        ),
        pytest.param(
            b"#!/usr/bin/env -Spython3 -u\n",
            b"#!/usr/bin/env -S python3 -u -X gil=1\n",
            id="compact-env-s-option",
        ),
        pytest.param(
            b"#!/usr/bin/env -S python3 --\n",
            b"#!/usr/bin/env -S python3 -X gil=1 --\n",
            id="end-of-options-marker",
        ),
        pytest.param(
            b"#!/usr/bin/env -S python3\t--\n",
            b"#!/usr/bin/env -S python3 -X gil=1 --\n",
            id="tab-separated-end-of-options-marker",
        ),
        pytest.param(
            b"#!/usr/bin/env -S python3 -X gil=0\n",
            b"#!/usr/bin/env -S python3 -X gil=0 -X gil=1\n",
            id="gil-requirement-overrides-unsafe-shebang",
        ),
        pytest.param(
            b"#!/usr/bin/env -S PYTHON_GIL=0 python3\n",
            b"#!/usr/bin/env -S PYTHON_GIL=0 python3 -X gil=1\n",
            id="gil-requirement-overrides-environment",
        ),
        pytest.param(
            b"#!/usr/bin/env -S python3 -W 'ignore:some warning:RuntimeWarning'\n",
            b"#!/usr/bin/env -S python3 -W 'ignore:some warning:RuntimeWarning' -X gil=1\n",
            id="preserve-quoted-arguments",
        ),
    ],
)
def test_gil_startup_preserves_script_and_interpreter_options(
    weld_module: ModuleType, tmp_path: Path, shebang: bytes, expected: bytes,
) -> None:
    source = tmp_path / "clock.py"
    source.write_bytes(shebang + PROBE)
    source.chmod(0o640)

    result = weld_module.compile_py_nuitka(str(source), str(tmp_path), enable_gil=True)
    assert result.returncode == 0, result.stderr
    executable = tmp_path / "clock"
    assert executable.read_bytes() == expected + PROBE
    assert executable.stat().st_mode & 0o777 == 0o751
    assert not source.exists()

    args = ["two words", "--flag", ""]
    run = subprocess.run(
        [str(executable), *args], env=_runtime_env(),
        capture_output=True, text=True, check=True, timeout=10,
    )
    probe = json.loads(run.stdout)
    assert probe["gil"] == "1"
    assert probe["enabled"] is True
    assert probe["args"] == args
    assert probe["optimize"] == (1 if b" -O" in shebang else 0)
    assert run.stderr == ""


@pytest.mark.parametrize("shebang", [b"#!\n", b"#!/usr/bin/env\n", b"#!/usr/bin/env -S\n"])
def test_invalid_gil_shebang_does_not_remove_source(
    weld_module: ModuleType, tmp_path: Path, shebang: bytes,
) -> None:
    source = tmp_path / "clock.py"
    original = shebang + PROBE
    source.write_bytes(original)
    result = weld_module.compile_py_nuitka(str(source), str(tmp_path), enable_gil=True)
    assert result.returncode != 0
    assert "Missing" in result.stderr
    assert source.read_bytes() == original
    assert not (tmp_path / "clock").exists()


def test_gil_shebang_keeps_non_utf8_source_readable(
    weld_module: ModuleType, tmp_path: Path,
) -> None:
    source = tmp_path / "clock.py"
    source.write_bytes("# comment\n# coding: latin-1\nprint('café')\n".encode("latin-1"))
    result = weld_module.compile_py_nuitka(str(source), str(tmp_path), enable_gil=True)
    assert result.returncode == 0, result.stderr
    run = subprocess.run(
        [str(tmp_path / "clock")], env=_runtime_env(),
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert run.stdout == "café\n"


def test_unrelated_runtime_warnings_are_not_suppressed(
    weld_module: ModuleType, tmp_path: Path,
) -> None:
    source = tmp_path / "clock.py"
    source.write_text(
        "import warnings\nwarnings.warn('unrelated warning', RuntimeWarning)\n",
        encoding="utf-8",
    )
    result = weld_module.compile_py_nuitka(str(source), str(tmp_path), enable_gil=True)
    assert result.returncode == 0, result.stderr
    run = subprocess.run(
        [str(tmp_path / "clock")], env=_runtime_env(),
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert "RuntimeWarning: unrelated warning" in run.stderr


@pytest.mark.parametrize("free_threaded", [False, True])
@pytest.mark.parametrize("has_libs", [False, True])
def test_install_applies_gil_compatibility_only_to_free_threaded_native_apps(
    weld_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    free_threaded: bool, has_libs: bool,
) -> None:
    # Mock the build capability, not the current runtime GIL state: the GIL
    # may already have been enabled in the process running Weld itself.
    original_get_config_var = sysconfig.get_config_var

    def get_config_var(name):
        if name == "Py_GIL_DISABLED":
            return 1 if free_threaded else 0
        return original_get_config_var(name)

    monkeypatch.setattr(sysconfig, "get_config_var", get_config_var)
    archive = tmp_path / "clock.wld"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("config.json", json.dumps({"name": "clock", "version": "1.1.1"}))
        zf.writestr("bin/main.py", PROBE)
        zf.writestr("bin/helper.py", PROBE)
        if has_libs:
            zf.writestr("bin/libs/_clock.py", "def now(): return '9:44:22'\n")

    weld_module.bootstrap()
    weld_module._cmd_install(str(archive))
    # Only the package's own command needs the startup flags; helper scripts
    # keep their own shebang.
    needs_gil = free_threaded and has_libs
    expected = {
        "clock": DEFAULT_GIL_SHEBANG if needs_gil else b"#!/usr/bin/env python3\n",
        "helper": b"#!/usr/bin/env python3\n",
    }
    for name, shebang in expected.items():
        executable = Path(weld_module.WELD_BIN) / name
        assert executable.read_bytes() == shebang + PROBE
        direct = subprocess.run(
            [str(executable), "two words"], env=_runtime_env(),
            capture_output=True, text=True, check=True, timeout=10,
        )
        wants_gil = needs_gil and name == "clock"
        assert json.loads(direct.stdout)["gil"] == ("1" if wants_gil else None)
        assert json.loads(direct.stdout)["args"] == ["two words"]

    # --run must also honor the installed executable's startup flags.
    through_weld = subprocess.run(
        [sys.executable, str(REPO_ROOT / "weld.py"), "--run", "clock", "two words"],
        env=_runtime_env(), capture_output=True, text=True, check=True, timeout=10,
    )
    assert json.loads(through_weld.stdout)["gil"] == ("1" if needs_gil else None)
    assert json.loads(through_weld.stdout)["args"] == ["two words"]
    info = weld_module.load_packages()["clock"]
    assert info["_lib_files"] == (["_clock.py"] if has_libs else [])
    if has_libs:
        copied = Path(weld_module.WELD_LIBS) / "_clock.py"
        assert copied.read_text(encoding="utf-8") == "def now(): return '9:44:22'\n"
        assert not (Path(weld_module.WELD_LIBS) / "_clock.so").exists()
