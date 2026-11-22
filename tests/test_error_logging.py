"""Detailed failure reports, isolated from the real ~/.weld directory."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _invoke(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
    *args: str, code: int | None = 1,
) -> None:
    monkeypatch.setattr(sys, "argv", ["weld", *args])
    if code is None:
        weld_module.main()
    else:
        with pytest.raises(SystemExit) as caught:
            weld_module.main()
        assert caught.value.code == code


def _reports(weld_module: ModuleType) -> list[Path]:
    return sorted(Path(weld_module.WELD_LOGS).glob("*.log"))


def _report(weld_module: ModuleType) -> tuple[Path, str]:
    reports = _reports(weld_module)
    assert len(reports) == 1
    return reports[0], reports[0].read_text(encoding="utf-8")


def test_cli_failure_saves_private_report_and_prints_its_path(
    weld_module: ModuleType, tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.wld"
    result = subprocess.run(
        [sys.executable, weld_module.__file__, "--install", str(missing)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 1
    assert "wld archive not found" in result.stdout
    assert "Traceback (most recent call last)" not in result.stdout + result.stderr
    path, report = _report(weld_module)
    assert f"Full error log: {path}" in result.stderr
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    for text in (
        "Weld error report", f"Weld version: {weld_module.VERSION}", "Started (UTC):",
        "Failed (UTC):", "PID:", "--install", str(missing), str(tmp_path),
        sys.executable, sys.version, "sys.prefix:", "sys.base_prefix:", "sys.flags:",
        "SOABI:", "EXT_SUFFIX:", "Py_GIL_DISABLED:", "GIL enabled:", "CC:", "CFLAGS:",
        "Python paths:", "Cython:", "setuptools:", "Tool paths", "Build environment",
        "Weld settings", "wld archive not found", "Traceback (most recent call last)",
        "_find_local_wld", "SystemExit: 1",
    ):
        assert text in report
    assert "\x1b[" not in report


@pytest.mark.parametrize(("args", "code", "message"), [
    (["--unknown-option"], 2, "unrecognized arguments"),
    (["--run"], 1, "--run requires a package name"),
    (["--list"], 1, "Corrupt package database"),
    (["--stats"], 1, "Invalid config file"),
])
def test_handled_cli_and_argument_errors_are_logged(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
    args: list[str], code: int, message: str,
) -> None:
    weld_module.bootstrap()
    if args == ["--list"]:
        Path(weld_module.WELD_PACKAGES).write_text("{bad json", encoding="utf-8")
    elif args == ["--stats"]:
        Path(weld_module.WELD_CONFIG).write_text("not [[[ toml", encoding="utf-8")
    _invoke(weld_module, monkeypatch, *args, code=code)
    _, report = _report(weld_module)
    assert message in report
    if args == ["--list"]:
        assert "JSONDecodeError" in report
    elif args == ["--stats"]:
        assert "TOMLDecodeError" in report


@pytest.mark.parametrize("error", [PermissionError("bootstrap denied"), ValueError("unexpected failure")])
def test_bootstrap_and_unexpected_errors_include_traceback_and_console_output(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], error: Exception,
) -> None:
    stdout, stderr = sys.stdout, sys.stderr

    def fail():
        weld_module._print("plain output")
        weld_module.console.print("[bold red]Rich output[/bold red]")
        sys.stderr.write("stderr output\n")
        raise error

    monkeypatch.setattr(weld_module, "bootstrap", fail)
    _invoke(weld_module, monkeypatch, "--version")
    _, report = _report(weld_module)
    terminal = capsys.readouterr()
    assert str(error) in terminal.out
    assert "Traceback (most recent call last)" not in terminal.out + terminal.err
    for text in ("plain output", "Rich output", "stderr output", type(error).__name__, str(error)):
        assert text in report
    assert sys.stdout is stdout and sys.stderr is stderr
    assert weld_module._diagnostics is None


@pytest.mark.parametrize(("args", "code"), [
    (["--version"], None), (["--help"], 0), ([], None),
])
def test_success_and_help_do_not_leave_error_logs(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], args: list[str], code: int | None,
) -> None:
    _invoke(weld_module, monkeypatch, *args, code=code)
    assert _reports(weld_module) == []
    assert "Full error log" not in capsys.readouterr().err
    assert weld_module._diagnostics is None


@pytest.mark.parametrize("mode", ["stdout-only", "both-streams", "silent", "missing-so"])
def test_cython_failure_keeps_all_build_details_after_temporary_files_are_removed(
    weld_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    archive = tmp_path / "clock.wld"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("config.json", json.dumps({"name": "clock", "version": "1.1.1"}))
        zf.writestr("bin/main.py", "print('clock')\n")
        zf.writestr("bin/libs/_clock.py", "def now(): return '12:34'\n")
    weld_module.bootstrap()
    old_script = Path(weld_module.WELD_BIN) / "clock"
    old_library = Path(weld_module.WELD_LIBS) / "_clock.so"
    old_script.write_text("old executable", encoding="utf-8")
    old_library.write_bytes(b"old library")
    old_packages = {"clock": {"version": "1.0.0", "files": ["clock"], "libs": ["_clock"]}}
    weld_module.save_packages(old_packages)
    stdout = "stdout START\n" + "x" * 20_000 + "\nstdout END\n" if mode != "silent" else ""
    stderr = "stderr START\n" + "y" * 20_000 + "\nstderr END\n" if mode == "both-streams" else ""
    exit_code = 0 if mode == "missing-so" else 7
    calls = []

    def build(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert cmd[0] == sys.executable
        assert "build_ext" in cmd and "--inplace" in cmd
        assert "from Cython.Build import cythonize" in Path(cmd[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, exit_code, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(weld_module.subprocess, "run", build)
    monkeypatch.setattr(weld_module, "_find_c_compiler", lambda: "fake-cc")
    _invoke(weld_module, monkeypatch, "--install", str(archive))
    _, report = _report(weld_module)
    assert len(calls) == 1  # Collecting diagnostics must not run additional tools.
    cmd, kwargs = calls[0]
    assert f"Command: {sys.executable}" in report
    assert f"Working directory: {kwargs['cwd']}" in report
    assert f"Return code: {exit_code}" in report
    assert "Generated setup script:" in report
    assert "compiler_directives={\"language_level\": 3}" in report
    assert "Package configuration:" in report and '"version": "1.1.1"' in report
    assert not Path(cmd[1]).exists()
    assert not Path(kwargs["cwd"]).exists()
    if stdout:
        assert stdout in report
    if stderr:
        assert stderr in report
    output = capsys.readouterr()
    plain_output = weld_module._ANSI_ESCAPE_RE.sub("", output.out).replace("\r", "\n")
    assert "✘ Cython  _clock.py" in plain_output
    assert "✔ Cython  _clock.py" not in plain_output
    assert "{_RESET}" not in plain_output
    assert "Cython .so was not generated" in report if mode == "missing-so" else "exit code 7" in report
    if mode == "silent":
        assert "produced no stdout or stderr" in plain_output
    elif mode == "stdout-only":
        assert "stdout START" in plain_output and "stdout END" in plain_output
    assert old_script.read_text(encoding="utf-8") == "old executable"
    assert old_library.read_bytes() == b"old library"
    assert weld_module.load_packages() == old_packages


def test_real_cython_parser_failure_is_logged_without_invoking_a_c_compiler(
    weld_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "broken.wld"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("config.json", json.dumps({"name": "broken", "version": "1.0.0"}))
        zf.writestr("bin/main.py", "print('hello')\n")
        # Cython must reject this before invoking any C compiler.
        zf.writestr("bin/libs/_broken.py", "def invalid(:\n")
    monkeypatch.setattr(weld_module, "_find_c_compiler", lambda: "not-needed-for-parsing")
    _invoke(weld_module, monkeypatch, "--install", str(archive))
    _, report = _report(weld_module)
    assert "Error compiling Cython file:" in report
    assert "CompileError" in report and "_broken.py" in report
    assert "Return code: 1" in report
    assert "Generated setup script:" in report
    assert weld_module.load_packages() == {}


def test_successful_cython_build_discards_its_journal_and_cleans_setup_files(
    weld_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "_native.py"
    source.write_text("def answer(): return 42\n", encoding="utf-8")

    def build(cmd, **kwargs):
        source.with_suffix(".cpython-test.so").write_bytes(b"compiled library")
        source.with_suffix(".c").write_text("generated C", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="successful build", stderr="")

    monkeypatch.setattr(weld_module.subprocess, "run", build)
    monkeypatch.setattr(weld_module, "_main", lambda: weld_module.compile_to_so(str(source)))
    _invoke(weld_module, monkeypatch, code=None)
    assert source.with_suffix(".so").read_bytes() == b"compiled library"
    assert not source.with_suffix(".c").exists()
    assert not list(tmp_path.glob("_weld_setup_*.py"))
    assert _reports(weld_module) == []
    assert "✔" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["launch", "timeout", "check"])
def test_subprocess_exceptions_retain_partial_output_and_command_context(
    weld_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    cmd = ["fake-compiler", "two words"]
    if failure == "launch":
        error = FileNotFoundError(2, "compiler missing", cmd[0])
    elif failure == "timeout":
        error = subprocess.TimeoutExpired(cmd, 1, output=b"partial stdout\xff", stderr=b"partial stderr\xfe")
    else:
        error = subprocess.CalledProcessError(3, cmd, output=b"partial stdout\xff", stderr=b"partial stderr\xfe")

    def run(*args, **kwargs):
        raise error

    def fail():
        weld_module._run_logged(cmd, cwd=str(tmp_path), capture_output=True, text=True, timeout=1)

    monkeypatch.setattr(weld_module.subprocess, "run", run)
    monkeypatch.setattr(weld_module, "_main", fail)
    _invoke(weld_module, monkeypatch)
    _, report = _report(weld_module)
    assert "Command: fake-compiler 'two words'" in report
    assert f"Working directory: {tmp_path}" in report
    assert "Timeout: 1" in report
    assert type(error).__name__ in report
    if failure != "launch":
        assert "partial stdout\\xff" in report and "partial stderr\\xfe" in report


def test_previous_successful_steps_and_non_utf8_output_are_retained(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail():
        weld_module._run_logged(
            [sys.executable, "-c", "import os; os.write(1, b'first stdout\\xff'); os.write(2, b'first stderr')"],
            capture_output=True, text=True, check=True,
        )
        weld_module._run_logged(
            [sys.executable, "-c", "import sys; print('second stdout'); sys.stderr.write('second stderr'); sys.exit(9)"],
            capture_output=True, text=True, check=True,
        )

    monkeypatch.setattr(weld_module, "_main", fail)
    _invoke(weld_module, monkeypatch)
    _, report = _report(weld_module)
    assert "Return code: 0" in report and "Return code: 9" in report
    assert "first stdout\\xff" in report and "first stderr" in report
    assert "second stdout" in report and "second stderr" in report
    assert "CalledProcessError" in report


def test_large_reports_spool_without_truncation_and_do_not_dump_environment_secrets(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = "BEGIN LARGE OUTPUT\n" + "z" * (2 << 20) + "\nEND LARGE OUTPUT\n"
    monkeypatch.setenv("WELD_TEST_PRIVATE_TOKEN", "never-dump-this-secret")
    monkeypatch.setenv("CFLAGS", "-O0 -g")
    monkeypatch.setenv("PYTHON_GIL", "1")
    sessions = []

    def fail():
        sessions.append(weld_module._diagnostics)
        weld_module._record_diagnostic(payload)
        assert weld_module._diagnostics.journal._rolled
        raise RuntimeError("failed after large output")

    monkeypatch.setattr(weld_module, "_main", fail)
    _invoke(weld_module, monkeypatch)
    _, report = _report(weld_module)
    assert payload in report
    assert "CFLAGS=-O0 -g" in report and "PYTHON_GIL=1" in report
    assert "WELD_TEST_PRIVATE_TOKEN" not in report and "never-dump-this-secret" not in report
    assert sessions[0].journal.closed


@pytest.mark.parametrize("failure", ["directory", "write"])
def test_logging_failures_never_replace_the_original_error(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], tmp_path: Path, failure: str,
) -> None:
    weld_module.bootstrap()
    if failure == "directory":
        Path(weld_module.WELD_LOGS).write_text("not a directory", encoding="utf-8")
    else:
        def fail_write(*args):
            raise OSError("no space for report")
        monkeypatch.setattr(weld_module, "_write_error_report", fail_write)
    _invoke(weld_module, monkeypatch, "--install", str(tmp_path / "missing.wld"))
    terminal = capsys.readouterr()
    assert "wld archive not found" in terminal.out
    assert "could not write error log" in terminal.err
    assert weld_module.WELD_LOGS in terminal.err
    assert "Traceback (most recent call last)" not in terminal.out + terminal.err
    assert _reports(weld_module) == []


@pytest.mark.parametrize("failure", ["record", "read"])
def test_journal_io_failures_still_save_the_original_error(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    class BrokenJournal:
        def __init__(self, journal):
            self.journal = journal

        def write(self, text):
            if failure == "record":
                raise OSError("journal write failed")
            return self.journal.write(text)

        def read(self, size):
            if failure == "read":
                raise OSError("journal read failed")
            return self.journal.read(size)

        def __getattr__(self, name):
            return getattr(self.journal, name)

    def fail():
        session = weld_module._diagnostics
        session.journal = BrokenJournal(session.journal)
        weld_module._record_diagnostic("some captured output\n")
        raise RuntimeError("primary failure")

    monkeypatch.setattr(weld_module, "_main", fail)
    _invoke(weld_module, monkeypatch)
    _, report = _report(weld_module)
    assert "RuntimeError: primary failure" in report
    assert "Warning:" in report and f"journal {'write' if failure == 'record' else 'read'} failed" in report


def test_unavailable_optional_metadata_does_not_prevent_an_error_report(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_metadata(package):
        raise RuntimeError("metadata unavailable")

    def fail():
        raise ValueError("original failure")

    monkeypatch.setattr(weld_module.metadata, "version", no_metadata)
    monkeypatch.setattr(weld_module, "_main", fail)
    _invoke(weld_module, monkeypatch)
    _, report = _report(weld_module)
    assert "ValueError: original failure" in report
    assert "Cython: <unavailable: RuntimeError: metadata unavailable>" in report


def test_repeated_failures_have_distinct_logs_and_clean_preserves_the_entire_log_tree(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _invoke(weld_module, monkeypatch, "--install", str(tmp_path / "missing.wld"))
    first, before = _report(weld_module)
    _invoke(weld_module, monkeypatch, "--install", str(tmp_path / "missing-again.wld"))
    reports = _reports(weld_module)
    assert len(reports) == 2
    nested = Path(weld_module.WELD_LOGS) / "build"
    nested.mkdir()
    evidence = nested / "evidence.c"
    evidence.write_text("retain diagnostics", encoding="utf-8")
    orphan = Path(weld_module.WELD_DIR) / "orphan"
    orphan.write_text("remove me", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda: "y")
    _invoke(weld_module, monkeypatch, "--clean", code=None)
    assert not orphan.exists()
    assert _reports(weld_module) == reports
    assert first.read_text(encoding="utf-8") == before
    assert evidence.read_text(encoding="utf-8") == "retain diagnostics"


def test_update_all_logs_failures_even_when_it_continues_to_other_packages(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    weld_module.bootstrap()
    weld_module.save_packages({name: {"version": "1.0.0"} for name in ("first", "second")})
    registry = {name: {"version": "2.0.0", "link": name} for name in ("first", "second")}

    def download(name, path):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("config.json", json.dumps({"name": name, "version": "2.0.0"}))

    def install(path, log_action):
        config = weld_module._read_archive_config(path)
        if config["name"] == "first":
            weld_module._print("first handled failure")
            raise SystemExit(1)
        raise RuntimeError("second unexpected failure")

    monkeypatch.setattr(weld_module, "_fetch_registry", lambda: registry)
    monkeypatch.setattr(weld_module, "_download_wld", download)
    monkeypatch.setattr(weld_module, "_cmd_install", install)
    _invoke(weld_module, monkeypatch, "--update-all", code=None)
    reports = _reports(weld_module)
    assert len(reports) == 2
    contents = [path.read_text(encoding="utf-8") for path in reports]
    assert "first handled failure" in contents[0] and "SystemExit: 1" in contents[0]
    assert "second unexpected failure" in contents[1] and "RuntimeError" in contents[1]
    assert "Updating:" in contents[1] and "second" in contents[1]


def test_keyboard_interrupt_is_logged_but_broken_pipe_is_not(
    weld_module: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(weld_module, "_main", interrupt)
    _invoke(weld_module, monkeypatch)
    _, report = _report(weld_module)
    assert "KeyboardInterrupt" in report and "Aborted." in report

    def broken_pipe():
        raise BrokenPipeError

    monkeypatch.setattr(weld_module, "_main", broken_pipe)
    monkeypatch.setattr(weld_module.os, "dup2", lambda *args: None)
    _invoke(weld_module, monkeypatch, code=0)
    assert len(_reports(weld_module)) == 1
