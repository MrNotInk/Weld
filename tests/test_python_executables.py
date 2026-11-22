"""Focused coverage of the legacy Nuitka backend's script preparation."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def weld_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "weld.py"
    spec = importlib.util.spec_from_file_location("weld_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("source", "expected_shebang", "expected_stdout"),
    [
        pytest.param(
            b"print('hello')\n", b"#!/usr/bin/env python3\n", "hello\n",
            id="missing-shebang",
        ),
        pytest.param(
            f"#!{sys.executable} -u\nprint('hello')\n".encode(),
            f"#!{sys.executable} -u\n".encode(), "hello\n",
            id="preserve-interpreter-and-flags",
        ),
        pytest.param(
            b"#!/usr/bin/env python3\r\nprint('hello')\r\n",
            b"#!/usr/bin/env python3\n", "hello\n",
            id="crlf-shebang",
        ),
        pytest.param(
            b"print('hello')\r\n", b"#!/usr/bin/env python3\n", "hello\n",
            id="crlf-body-without-shebang",
        ),
        pytest.param(
            "\ufeffprint('café')\n".encode(),
            b"#!/usr/bin/env python3\n", "café\n",
            id="utf8-bom",
        ),
        pytest.param(
            b"\xef\xbb\xbf#!/usr/bin/env python3\nprint('hello')\n",
            b"#!/usr/bin/env python3\n", "hello\n",
            id="utf8-bom-before-shebang",
        ),
        pytest.param(
            "# coding: latin-1\nprint('café')\n".encode("latin-1"),
            b"#!/usr/bin/env python3\n", "café\n",
            id="non-utf8-encoding",
        ),
        pytest.param(
            "# a comment\n# coding: latin-1\nprint('café')\n".encode("latin-1"),
            b"#!/usr/bin/env python3\n", "café\n",
            id="encoding-cookie-on-second-line",
        ),
        pytest.param(b"", b"#!/usr/bin/env python3\n", "", id="empty-file"),
        pytest.param(
            b"#!/usr/bin/env python3", b"#!/usr/bin/env python3\n", "",
            id="shebang-without-newline",
        ),
    ],
)
def test_prepares_runnable_scripts(
    weld_module: ModuleType, tmp_path: Path,
    source: bytes, expected_shebang: bytes, expected_stdout: str,
) -> None:
    path = tmp_path / "some app.py"
    path.write_bytes(source)
    path.chmod(0o640)
    messages: list[str] = []

    result = weld_module.compile_py_nuitka(
        str(path), str(tmp_path), line_cb=messages.append,
        opts=["--standalone", "--jobs=4"],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert messages == ["Prepared some app"]
    assert not path.exists()
    output = tmp_path / "some app"
    assert output.read_bytes().startswith(expected_shebang)
    assert output.read_bytes().count(b"#!") == 1
    assert stat.S_IMODE(output.stat().st_mode) == 0o751
    run = subprocess.run(
        [str(output)], capture_output=True, text=True, check=True, timeout=10,
    )
    assert run.stdout == expected_stdout


@pytest.mark.parametrize("relative", [False, True])
def test_resolves_paths_without_invoking_tools(
    weld_module: ModuleType, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, relative: bool,
) -> None:
    directory = tmp_path / "working directory"
    directory.mkdir()
    source = directory / "my.tool.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def unexpected_call(*args, **kwargs):
        pytest.fail("Script preparation must not invoke tools or read tool options")

    monkeypatch.setattr(weld_module.subprocess, "Popen", unexpected_call)
    monkeypatch.setattr(weld_module.subprocess, "run", unexpected_call)
    monkeypatch.setattr(weld_module, "get_nuitka_opts", unexpected_call)
    result = weld_module.compile_py_nuitka(
        source.name if relative else str(source), "working directory",
    )
    assert result.returncode == 0, result.stderr
    assert not source.exists()
    assert (directory / "my.tool").is_file()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_never_overwrites_existing_output(
    weld_module: ModuleType, tmp_path: Path, kind: str,
) -> None:
    source = tmp_path / "app.py"
    original = b"print('hello')\n"
    source.write_bytes(original)
    output = tmp_path / "app"
    if kind == "file":
        output.write_bytes(b"existing data")
    elif kind == "directory":
        output.mkdir()
    else:
        output.symlink_to(tmp_path / "missing-target")

    result = weld_module.compile_py_nuitka(str(source), str(tmp_path))
    assert result.returncode != 0
    assert "File exists" in result.stderr
    assert source.read_bytes() == original
    assert os.path.lexists(output)
    if kind == "file":
        assert output.read_bytes() == b"existing data"
    elif kind == "directory":
        assert output.is_dir()
    else:
        assert output.is_symlink()


@pytest.mark.parametrize("failure", ["chmod", "remove-source"])
def test_preparation_failure_keeps_source_and_cleans_output(
    weld_module: ModuleType, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    source = tmp_path / "app.py"
    original = b"print('hello')\n"
    source.write_bytes(original)
    real_remove = os.remove

    def fail_chmod(*args, **kwargs):
        raise PermissionError("chmod denied")

    def fail_source_remove(path):
        if path == str(source):
            raise PermissionError("source removal denied")
        return real_remove(path)

    if failure == "chmod":
        monkeypatch.setattr(weld_module.os, "chmod", fail_chmod)
    else:
        monkeypatch.setattr(weld_module.os, "remove", fail_source_remove)
    result = weld_module.compile_py_nuitka(str(source), str(tmp_path))
    assert result.returncode != 0
    assert "denied" in result.stderr
    assert source.read_bytes() == original
    assert not (tmp_path / "app").exists()


@pytest.mark.parametrize(
    ("filename", "content", "error"),
    [
        ("missing.py", None, "No such file"),
        ("app.txt", b"print('hello')\n", "File must be a .py file"),
        ("app.py", b"# coding: not-an-encoding\n", "unknown encoding"),
    ],
)
def test_invalid_input_fails_cleanly(
    weld_module: ModuleType, tmp_path: Path,
    filename: str, content: bytes | None, error: str,
) -> None:
    source = tmp_path / filename
    if content is not None:
        source.write_bytes(content)
    messages: list[str] = []
    result = weld_module.compile_py_nuitka(
        str(source), str(tmp_path), line_cb=messages.append,
    )
    assert result.returncode != 0
    assert error in result.stderr
    assert messages == [result.stderr]
    if content is not None:
        assert source.read_bytes() == content
    assert not (tmp_path / "app").exists()


@pytest.mark.parametrize("returncode", [0, 1])
def test_display_wrapper_delegates_to_script_preparation(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], returncode: int,
) -> None:
    expected = subprocess.CompletedProcess(["app.py"], returncode, stdout="", stderr="")
    calls = []

    def prepare(full_path, cwd, *, opts):
        calls.append((full_path, cwd, opts))
        return expected

    monkeypatch.setattr(weld_module, "compile_py_nuitka", prepare)
    assert weld_module._compile_nuitka_live("app.py", ".", "app.py", opts=[]) is expected
    assert calls == [("app.py", ".", [])]
    assert ("✔" if returncode == 0 else "✘") in capsys.readouterr().out
