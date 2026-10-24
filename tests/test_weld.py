"""End-to-end tests for weld.py.

Every test runs weld in an isolated HOME so the real ~/.weld is never touched.
Fake ``nuitka``/``pyinstaller`` shims are placed on PATH; they only produce a
tiny shell script instead of a real binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WELD_PY = REPO_ROOT / "weld.py"
VENV_BIN = Path(sys.executable).parent

TEMPLATE_NAMES = [
    "hello", "cli", "flask", "fastapi", "daemon", "scraper", "tui",
    "bot", "cron", "tcp_server", "grpc", "data_pipeline", "rest_client",
    "config_app", "plugin",
]

_FAKE_NUITKA = """#!/usr/bin/env python3
import os, sys

if "--version" in sys.argv:
    print("fake-nuitka 1.0")
    sys.exit(0)

src = sys.argv[-1]
stem = os.path.splitext(os.path.basename(src))[0]
out = os.path.join(os.path.dirname(os.path.abspath(src)), stem + ".bin")
with open(out, "w") as f:
    f.write('#!/bin/sh\\necho compiled-' + stem + ' "$@"\\n')
os.chmod(out, 0o755)
"""

_FAKE_PYINSTALLER = """#!/usr/bin/env python3
import os, sys

if "--version" in sys.argv:
    print("fake-pyinstaller 1.0")
    sys.exit(0)

args = sys.argv[1:]
dist = "."
src = None
i = 0
while i < len(args):
    if args[i] == "--distpath" and i + 1 < len(args):
        dist = args[i + 1]
        i += 2
        continue
    if args[i].endswith(".py"):
        src = args[i]
    i += 1
if src is None:
    sys.exit("no source file given")

stem = os.path.splitext(os.path.basename(src))[0]
os.makedirs(dist, exist_ok=True)
out = os.path.join(dist, stem)
with open(out, "w") as f:
    f.write('#!/bin/sh\\necho pyi-' + stem + ' "$@"\\n')
os.chmod(out, 0o755)
"""

_FAILING_COMPILER = """#!/bin/sh
echo "compiler exploded" >&2
exit 1
"""


def _write_exe(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class WeldRunner:
    def __init__(self, root: Path, home: Path, env: dict[str, str]):
        self.root = root
        self.home = home
        self.env = env

    # -- low level ---------------------------------------------------------
    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        input: str | None = None,
        path: str | None = None,
        extra_env: dict[str, str] | None = None,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if path is not None:
            env["PATH"] = path
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(WELD_PY), *args],
            cwd=str(cwd or self.root),
            env=env,
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def ok(self, *args: str, **kwargs) -> subprocess.CompletedProcess[str]:
        result = self.run(*args, **kwargs)
        assert result.returncode == 0, (
            f"weld {' '.join(args)} failed ({result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert_no_traceback(result)
        return result

    def fail(self, *args: str, **kwargs) -> subprocess.CompletedProcess[str]:
        result = self.run(*args, **kwargs)
        assert result.returncode != 0, (
            f"weld {' '.join(args)} unexpectedly succeeded:\n{result.stdout}"
        )
        assert_no_traceback(result)
        return result

    # -- project helpers ---------------------------------------------------
    def new(self, name: str) -> Path:
        result = self.ok("--new", name)
        assert "Created" in result.stdout
        return self.root / name

    def template(self, project: Path, template: str) -> subprocess.CompletedProcess[str]:
        return self.ok("--template", template, cwd=project)

    def build(self, name: str) -> Path:
        result = self.ok("--build", name)
        archive = self.root / f"{name}.wld"
        assert archive.is_file(), result.stdout
        return archive

    def install(self, archive: Path, **kwargs) -> subprocess.CompletedProcess[str]:
        return self.ok("--install", str(archive), **kwargs)

    def packages(self) -> dict:
        path = self.home / ".weld" / "packages.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def bin_dir(self) -> Path:
        return self.home / ".weld" / "bin"

    @property
    def libs_dir(self) -> Path:
        return self.bin_dir / "libs"

    def set_config(self, **settings) -> None:
        defaults = {
            "compiler": "nuitka",
            "libs_compiler": "cython",
            "default_version": "1.0.0",
            "auto_pip_install": True,
        }
        defaults.update(settings)
        lines = ["[weld]"]
        for key, value in defaults.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                rendered = f'"{value}"'
            lines.append(f"{key} = {rendered}")
        (self.home / ".weld" / "config.toml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def assert_no_traceback(result: subprocess.CompletedProcess[str]) -> None:
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Traceback (most recent call last)" not in combined, combined


@pytest.fixture
def weld(tmp_path: Path) -> WeldRunner:
    return _make_runner(tmp_path)


def _make_runner(
    tmp_path: Path, *, failing_compiler: bool = False, suffix: str = "",
) -> WeldRunner:
    home = tmp_path / f"home{suffix}"
    home.mkdir()
    fakebin = tmp_path / f"fakebin{suffix}"
    fakebin.mkdir()
    _write_exe(fakebin / "nuitka", _FAKE_NUITKA)
    _write_exe(fakebin / "pyinstaller", _FAKE_PYINSTALLER)
    if failing_compiler:
        _write_exe(fakebin / "nuitka", _FAILING_COMPILER)
        _write_exe(fakebin / "pyinstaller", _FAILING_COMPILER)

    tmpdir = tmp_path / f"tmp{suffix}"
    tmpdir.mkdir()

    env = {
        "HOME": str(home),
        "PATH": f"{fakebin}:{VENV_BIN}:/usr/local/bin:/usr/bin:/bin",
        "TMPDIR": str(tmpdir),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    return WeldRunner(tmp_path, home, env)


def make_archive(path: Path, config: dict, files: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.json", json.dumps(config))
        for name, content in (files or {}).items():
            zf.writestr(name, content)
    return path


# ---------------------------------------------------------------------------
# CLI basics
# ---------------------------------------------------------------------------

def test_version(weld: WeldRunner) -> None:
    result = weld.ok("--version")
    assert "weld" in result.stdout
    assert "0.1.0" in result.stdout


def test_no_arguments_prints_help(weld: WeldRunner) -> None:
    result = weld.ok()
    assert "--build" in result.stdout
    assert "--install" in result.stdout


def test_templates_lists_all_builtins(weld: WeldRunner) -> None:
    result = weld.ok("--templates")
    for name in TEMPLATE_NAMES:
        assert name in result.stdout


def test_stats_and_history_on_fresh_install(weld: WeldRunner) -> None:
    assert "weld stats" in weld.ok("--stats").stdout
    assert "No history yet" in weld.ok("--history").stdout
    assert "No welds installed" in weld.ok("--verify").stdout


def test_doctor_with_fake_tools(weld: WeldRunner) -> None:
    result = weld.ok("--doctor")
    assert "All required tools are available" in result.stdout


# ---------------------------------------------------------------------------
# new / template
# ---------------------------------------------------------------------------

def test_new_creates_project_skeleton(weld: WeldRunner) -> None:
    project = weld.new("myapp")
    assert (project / "config.json").is_file()
    assert (project / "bin" / "main.py").is_file()
    assert (project / "Readme").is_file()
    assert (project / "Changelog").is_file()
    assert (project / "plugins" / "__init__.py").is_file()
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    assert config["name"] == "myapp"
    assert config["version"] == "1.0.0"


def test_new_absolute_path_uses_basename(weld: WeldRunner) -> None:
    target = weld.root / "nested" / "absapp"
    weld.ok("--new", str(target))
    config = json.loads((target / "config.json").read_text(encoding="utf-8"))
    assert config["name"] == "absapp"


@pytest.mark.parametrize("name", ["bad name", "with.dot", "libs", ""])
def test_new_rejects_invalid_names(weld: WeldRunner, name: str) -> None:
    result = weld.fail("--new", name)
    assert "Error" in result.stdout


def test_template_applies_and_merges_deps(weld: WeldRunner) -> None:
    project = weld.new("myapp")
    weld.template(project, "flask")
    assert "Flask" in (project / "bin" / "main.py").read_text(encoding="utf-8")
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    assert config["deps"] == ["flask"]
    backups = list((project / "bin").glob("main.py.*.bak"))
    assert len(backups) == 1


def test_template_unknown_name(weld: WeldRunner) -> None:
    project = weld.new("myapp")
    result = weld.fail("--template", "does-not-exist", cwd=project)
    assert "not found" in result.stdout
    assert "hello" in result.stdout


def test_template_rejects_path_traversal(weld: WeldRunner) -> None:
    project = weld.new("myapp")
    outside = weld.root / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text('print("owned")\n', encoding="utf-8")
    before = (project / "bin" / "main.py").read_text(encoding="utf-8")
    result = weld.fail("--template", "../../outside/evil", cwd=project)
    assert "Invalid template name" in result.stdout
    assert (project / "bin" / "main.py").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_every_builtin_template_builds(weld: WeldRunner, template: str) -> None:
    project = weld.new("tplapp")
    weld.template(project, template)
    weld.build("tplapp")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def test_build_archive_contents_and_junk_exclusion(weld: WeldRunner) -> None:
    project = weld.new("app")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("x", encoding="utf-8")
    (project / "bin" / "__pycache__").mkdir()
    (project / "bin" / "__pycache__" / "main.cpython-311.pyc").write_text("x", encoding="utf-8")
    (project / "bin" / "main.py.20200101_000000.bak").write_text("x", encoding="utf-8")
    (project / ".venv").mkdir()
    archive = weld.build("app")

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "config.json" in names
    assert "bin/main.py" in names
    assert not any(".git/" in n for n in names)
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith(".bak") for n in names)
    assert not any(".venv/" in n for n in names)


def test_rebuild_replaces_stale_archive(weld: WeldRunner) -> None:
    project = weld.new("app")
    (project / "bin" / "data.txt").write_text("hello", encoding="utf-8")
    weld.build("app")
    (project / "bin" / "data.txt").unlink()
    archive = weld.build("app")
    with zipfile.ZipFile(archive) as zf:
        assert "bin/data.txt" not in zf.namelist()


def test_build_reports_ruff_violations(weld: WeldRunner) -> None:
    project = weld.new("app")
    (project / "bin" / "main.py").write_text(
        "import os\n\n\nprint('hi')\n", encoding="utf-8"
    )
    result = weld.fail("--build", "app")
    assert "Ruff check failed" in result.stdout


def test_build_reports_syntax_errors(weld: WeldRunner) -> None:
    project = weld.new("app")
    (project / "bin" / "main.py").write_text("def broken(:\n", encoding="utf-8")
    result = weld.fail("--build", "app")
    assert "Ruff check failed" in result.stdout or "Syntax error" in result.stdout


def test_build_reports_missing_ruff(weld: WeldRunner) -> None:
    weld.new("app")
    result = weld.fail(
        "--build", "app", path=f"{weld.root / 'fakebin'}:/usr/bin:/bin"
    )
    assert "Required tool 'ruff' not found" in result.stdout


def test_build_rejects_invalid_config(weld: WeldRunner) -> None:
    project = weld.new("app")
    (project / "config.json").write_text('{"name": "app"}', encoding="utf-8")
    assert "version" in weld.fail("--build", "app").stdout

    (project / "config.json").write_text("{not json", encoding="utf-8")
    assert "Invalid JSON" in weld.fail("--build", "app").stdout


def test_build_rejects_reserved_config_name(weld: WeldRunner) -> None:
    project = weld.new("app")
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    config["name"] = "libs"
    (project / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert "reserved" in weld.fail("--build", "app").stdout


def test_plugin_hooks_run(weld: WeldRunner) -> None:
    project = weld.new("app")
    marker = project / "hook.log"
    (project / "plugins" / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"MARK = Path({str(marker)!r})\n"
        "def before_build(config):\n"
        "    MARK.write_text(MARK.read_text() + 'before\\n' if MARK.exists() else 'before\\n')\n"
        "def after_build(config, archive_path):\n"
        "    MARK.write_text(MARK.read_text() + 'after\\n')\n",
        encoding="utf-8",
    )
    weld.build("app")
    assert marker.read_text(encoding="utf-8").splitlines() == ["before", "after"]


# ---------------------------------------------------------------------------
# install / run
# ---------------------------------------------------------------------------

def test_install_and_run(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    result = weld.install(archive)
    assert "Installed" in result.stdout

    info = weld.packages()["app"]
    assert info["files"] == ["app"]
    assert info["version"] == "1.0.0"
    assert os.access(weld.bin_dir / "app", os.X_OK)

    run = weld.ok("--run", "app", "--flag", "value")
    assert "compiled-app --flag value" in run.stdout

    assert (weld.home / ".weld" / "Readme" / "app-Readme").is_file()


def test_install_uses_pyinstaller_when_configured(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.set_config(compiler="pyinstaller")
    weld.install(archive)
    run = weld.ok("--run", "app")
    assert "pyi-app" in run.stdout


def test_reinstall_keeps_file_tracking(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    weld.install(archive)
    assert weld.packages()["app"]["files"] == ["app"]

    weld.ok("--remove", "app")
    assert not (weld.bin_dir / "app").exists()


def test_install_missing_config_fields(weld: WeldRunner) -> None:
    archive = make_archive(
        weld.root / "broken.wld",
        {"name": "brokenpkg"},
        {"bin/main.py": "print('hi')\n"},
    )
    result = weld.fail("--install", str(archive))
    assert "version" in result.stdout
    assert not (weld.bin_dir / "brokenpkg").exists()


def test_install_rejects_reserved_name(weld: WeldRunner) -> None:
    archive = make_archive(
        weld.root / "libs.wld",
        {"name": "libs", "version": "1.0.0", "deps": [], "desc": ""},
        {"bin/main.py": "print('hi')\n"},
    )
    result = weld.fail("--install", str(archive))
    assert "reserved" in result.stdout
    assert not (weld.bin_dir / "libs.bin").exists()


def test_install_rejects_path_traversal_archive(weld: WeldRunner) -> None:
    archive = weld.root / "evil.wld"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "config.json",
            json.dumps({"name": "evil", "version": "1.0.0", "deps": [], "desc": ""}),
        )
        zf.writestr("bin/main.py", "print('hi')\n")
        zf.writestr("bin/../../escaped.txt", "owned")

    result = weld.fail("--install", str(archive))
    assert "Unsafe path" in result.stdout
    assert not (weld.root / "escaped.txt").exists()


def test_install_rejects_invalid_archive(weld: WeldRunner) -> None:
    bad = weld.root / "bad.wld"
    bad.write_text("not a zip", encoding="utf-8")
    assert "not a valid" in weld.fail("--install", str(bad)).stdout


def test_install_failure_leaves_no_files(weld: WeldRunner) -> None:
    failbin = weld.root / "failbin"
    failbin.mkdir()
    _write_exe(failbin / "nuitka", _FAILING_COMPILER)
    weld.new("app")
    archive = weld.build("app")
    result = weld.fail(
        "--install", str(archive), path=f"{failbin}:{weld.env['PATH']}"
    )
    assert "compiler exploded" in result.stdout
    assert weld.packages() == {}
    assert list(weld.bin_dir.iterdir()) == [weld.libs_dir]


def test_install_skips_deps_when_disabled(weld: WeldRunner) -> None:
    weld.new("app")
    config_path = weld.root / "app" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["deps"] = ["definitely-not-a-real-package-xyz"]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    archive = weld.build("app")
    weld.set_config(auto_pip_install=False)
    result = weld.install(archive)
    assert "Skipping installation" in result.stdout


def test_install_installs_dependencies_without_uv(weld: WeldRunner) -> None:
    weld.new("app")
    config_path = weld.root / "app" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["deps"] = ["rich"]  # already present in the test environment
    config_path.write_text(json.dumps(config), encoding="utf-8")
    archive = weld.build("app")

    result = weld.install(archive, extra_env={"PIP_NO_INDEX": "1"})
    assert "Installing dependencies" in result.stdout
    assert "Installed" in result.stdout
    assert weld.packages()["app"]["deps"] == ["rich"]


def test_dependency_failure_is_clean(weld: WeldRunner) -> None:
    weld.new("app")
    config_path = weld.root / "app" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["deps"] = ["definitely-not-a-real-package-xyz"]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    archive = weld.build("app")

    result = weld.fail("--install", str(archive), extra_env={"PIP_NO_INDEX": "1"})
    assert "pip install" in result.stdout
    assert weld.packages() == {}
    assert not (weld.bin_dir / "app").exists()


def test_install_package_data_directories(weld: WeldRunner) -> None:
    project = weld.new("dataapp")
    assets = project / "bin" / "assets" / "sub"
    assets.mkdir(parents=True)
    (assets / "f.txt").write_text("hello", encoding="utf-8")
    archive = weld.build("dataapp")
    weld.install(archive)

    assert (weld.bin_dir / "assets" / "sub" / "f.txt").read_text(
        encoding="utf-8"
    ) == "hello"
    info = weld.packages()["dataapp"]
    assert "assets/sub/f.txt" in info["files"]
    weld.ok("--verify")

    weld.ok("--remove", "dataapp")
    assert not (weld.bin_dir / "assets").exists()


def test_run_rejects_unknown_and_unsafe_packages(weld: WeldRunner) -> None:
    assert "not found" in weld.fail("--run", "nope").stdout
    assert "Invalid package name" in weld.fail("--run", "../../bin/sh").stdout


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_update_changes_version(weld: WeldRunner) -> None:
    project = weld.new("app")
    archive = weld.build("app")
    weld.install(archive)

    config_path = project / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["version"] = "2.0.0"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    new_archive = weld.build("app")

    result = weld.ok("--update", str(new_archive))
    assert "Updating" in result.stdout
    assert weld.packages()["app"]["version"] == "2.0.0"
    assert weld.packages()["app"]["files"] == ["app"]
    assert "compiled-app" in weld.ok("--run", "app").stdout


def test_failed_update_keeps_previous_version(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)

    failbin = weld.root / "failbin"
    failbin.mkdir()
    _write_exe(failbin / "nuitka", _FAILING_COMPILER)
    result = weld.fail(
        "--update", str(archive),
        path=f"{failbin}:{weld.env['PATH']}",
    )
    assert "compiler exploded" in result.stdout

    assert weld.packages()["app"]["version"] == "1.0.0"
    assert "compiled-app" in weld.ok("--run", "app").stdout


def test_update_installs_if_not_present(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    result = weld.ok("--update", str(archive))
    assert "fresh install" in result.stdout
    assert "app" in weld.packages()


# ---------------------------------------------------------------------------
# pin / rename / duplicate / note / remove
# ---------------------------------------------------------------------------

def test_pin_blocks_destructive_operations(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    weld.ok("--pin", "app")

    assert "pinned" in weld.fail("--remove", "app").stdout
    assert "pinned" in weld.fail("--install", str(archive)).stdout
    assert "pinned" in weld.fail("--rename", "app", "other").stdout
    skipped = weld.ok("--update", str(archive))
    assert "Skipped" in skipped.stdout

    weld.ok("--unpin", "app")
    assert weld.packages()["app"]["pinned"] is False


def test_duplicate_and_rename(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)

    weld.ok("--duplicate", "app", "app2")
    assert (weld.bin_dir / "app2").is_file()
    assert "compiled-app" in weld.ok("--run", "app2").stdout

    weld.ok("--rename", "app2", "app3")
    assert not (weld.bin_dir / "app2").exists()
    assert (weld.bin_dir / "app3").is_file()
    assert "app3" in weld.packages()
    assert "compiled-app" in weld.ok("--run", "app3").stdout
    weld.ok("--verify")


def test_duplicate_requires_binary(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    (weld.bin_dir / "app").unlink()
    result = weld.fail("--duplicate", "app", "copy")
    assert "binary is missing" in result.stdout


def test_note_accepts_multiple_words(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    weld.ok("--note", "app", "this", "is", "a", "note")
    assert weld.packages()["app"]["note"] == "this is a note"
    assert "this is a note" in weld.ok("--info", "app").stdout
    assert "NOTE" in weld.ok("--history", "app").stdout


def test_remove_keeps_shared_libs(tmp_path: Path) -> None:
    weld = _make_runner(tmp_path)
    weld.ok("--version")  # bootstrap ~/.weld
    (weld.bin_dir / "a").write_text("#!/bin/sh\n", encoding="utf-8")
    (weld.bin_dir / "b").write_text("#!/bin/sh\n", encoding="utf-8")
    weld.libs_dir.mkdir(parents=True, exist_ok=True)
    shared = weld.libs_dir / "shared.so"
    shared.write_text("so", encoding="utf-8")

    def info(name: str, libs: list[str]) -> dict:
        return {
            "deps": [], "version": "1", "desc": "", "files": [name],
            "libs": libs, "has_readme": False, "has_changelog": False,
            "pinned": False, "note": "", "installed_at": "x", "updated_at": "x",
        }

    (weld.home / ".weld" / "packages.json").write_text(
        json.dumps({"a": info("a", ["shared"]), "b": info("b", ["shared"])}),
        encoding="utf-8",
    )

    weld.ok("--remove", "a")
    assert shared.is_file(), "shared lib was removed while still in use"
    weld.ok("--remove", "b")
    assert not shared.exists()


# ---------------------------------------------------------------------------
# list / search / verify / stats
# ---------------------------------------------------------------------------

def test_list_filtering_and_sorting(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)

    assert "app" in weld.ok("--list").stdout
    assert "app" in weld.ok("--list-filter", "version=1.*").stdout
    assert "app" in weld.ok("--list-sort", "version").stdout

    assert "Unknown filter field" in weld.fail("--list-filter", "nope=x").stdout
    assert "--list-filter expects" in weld.fail("--list-filter", "nomatch").stdout


def test_search_and_info(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)

    assert "app" in weld.ok("--search", "app").stdout
    assert "No packages matched" in weld.ok("--search", "zzz").stdout
    assert "Version" in weld.ok("--info", "app").stdout
    assert "not found" in weld.fail("--info", "missing").stdout


def test_verify_detects_non_executable_binary(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    weld.ok("--verify")

    (weld.bin_dir / "app").chmod(0o644)
    result = weld.fail("--verify")
    assert "not executable" in result.stdout


def test_history_and_clean(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)

    history = weld.ok("--history")
    assert "BUILD" in history.stdout
    assert "INSTALL" in history.stdout

    orphan = weld.bin_dir / "orphan_binary"
    orphan.write_text("x", encoding="utf-8")
    (weld.libs_dir / "orphan.c").write_text("x", encoding="utf-8")
    readme_orphan = weld.home / ".weld" / "Readme" / "ghost-Readme"
    readme_orphan.write_text("x", encoding="utf-8")

    result = weld.ok("--clean", input="y\n")
    assert "Clean complete" in result.stdout
    assert not orphan.exists()
    assert not (weld.libs_dir / "orphan.c").exists()
    assert not readme_orphan.exists()
    # Installed package must survive
    assert (weld.bin_dir / "app").is_file()


def test_clean_aborts_on_no(weld: WeldRunner) -> None:
    weld.ok("--version")
    orphan = weld.bin_dir / "orphan_binary"
    orphan.write_text("x", encoding="utf-8")
    result = weld.ok("--clean", input="n\n")
    assert "Aborted" in result.stdout
    assert orphan.is_file()


# ---------------------------------------------------------------------------
# config / packages database
# ---------------------------------------------------------------------------

def test_corrupt_config_is_reported(weld: WeldRunner) -> None:
    weld.ok("--version")
    (weld.home / ".weld" / "config.toml").write_text("not [[[ toml", encoding="utf-8")
    result = weld.fail("--stats")
    assert "Invalid config file" in result.stdout


def test_corrupt_packages_json_is_reported(weld: WeldRunner) -> None:
    weld.ok("--version")
    (weld.home / ".weld" / "packages.json").write_text("{bad json", encoding="utf-8")
    result = weld.fail("--list")
    assert "Corrupt package database" in result.stdout


# ---------------------------------------------------------------------------
# edit / completions
# ---------------------------------------------------------------------------

def test_edit_removes_temporary_file(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    (weld.home / ".weld" / "Readme" / "app-Readme").unlink()

    tmpdir = Path(weld.env["TMPDIR"])
    weld.ok("--edit", "app", extra_env={"EDITOR": "true -x"})
    assert list(tmpdir.iterdir()) == []


def test_edit_launches_editor_for_readme(weld: WeldRunner) -> None:
    weld.new("app")
    archive = weld.build("app")
    weld.install(archive)
    log = weld.root / "editor.log"
    editor = weld.root / "editor.sh"
    _write_exe(editor, f'#!/bin/sh\nprintf "%s" "$1" > {log}\n')
    weld.ok("--edit", "app", extra_env={"EDITOR": str(editor)})
    assert log.read_text(encoding="utf-8").endswith("app-Readme")


def test_completions_uses_current_interpreter(weld: WeldRunner) -> None:
    result = weld.ok("--completions", "zsh")
    assert sys.executable in result.stdout
    helper = weld.home / ".weld" / "_complete_packages.py"
    assert helper.is_file()
    run = subprocess.run(
        [sys.executable, str(helper)],
        env={"HOME": str(weld.home), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0
    assert run.stdout.strip() == ""
    assert "-l" in result.stdout


def test_unknown_shell_is_rejected(weld: WeldRunner) -> None:
    assert "Unknown shell" in weld.fail("--completions", "fish").stdout
