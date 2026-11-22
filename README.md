# ⚡ Weld (`weld`)

A fast, lightweight Python application packaging and distribution tool designed to simplify building, bundling, installing, and managing Python CLI applications and standalone executables.

---

## 🚀 Overview

**Weld** simplifies Python app distribution by handling validation, executable script preparation, optional native compilation, dependency management, and local packaging into portable `.wld` archives. It includes built-in application templates, hooks for lifecycle plugins, and concurrent package locking.

## ✨ Features

* **Project Scaffolding**: Quickly create new project structures with ready-to-use templates (Flask, FastAPI, CLI, Daemon, TUI, Scraper, etc.).
* **Code Validation**: Runs `ruff` checks and bytecode compilation (`py_compile`) before building.
* **Executable Python Scripts**: The default (legacy `nuitka`) mode removes `.py`, ensures a shebang, and adds execute permissions without invoking Nuitka.
* **Optional Compilation Engines**: Builds native executables using **PyInstaller**, and shared library modules (`.so`) using **Cython**.
* **Custom Tool Options**: Add or remove saved Ruff options with `-Wruff` / `-Eruff` (e.g. `weld -Wruff "--ignore=F401"`). Legacy Nuitka option flags remain accepted but are ignored.
* **Package Archives (`.wld`)**: Archives your Python binaries, libraries, configurations, Readmes, and Changelogs into portable `.wld` files.
* **Dependency Management**: Automatically manages and installs PyPI dependencies via `uv` or `pip` (can be disabled with `auto_pip_install = false`).
* **Package Lifecycle**: Supports installing (from local `.wld` archives or
  the `Weld_tools` registry), updating (single package or all at once with
  `--update-all`), pinning/unpinning, running, searching, duplicating,
  renaming, and removing packages.
* **Health & History Tracking**: Includes a diagnostic doctor (`--doctor`), environment verification (`--verify`), orphaned file cleaner (`--clean`), and action history log (`--history`).
* **Shell Completions**: Generates Zsh auto-completion scripts out of the box.

## 📋 Prerequisites

* **Python 3.11+** — required both for Weld and for installed Python scripts.
  On Python 3.10 and older the `tomli` backport is used automatically.
* **Linux or macOS** (Weld uses POSIX file locking and `.so` shared libraries).
* **No executable compiler is needed by default.**
  [PyInstaller](https://pyinstaller.org/) is only needed when
  `compiler = "pyinstaller"` is selected. Its support for new Python releases
  may lag; Python 3.11–3.13 are recommended for that mode.
* Cython, a C toolchain, and Python development headers (e.g. `python3-dev`
  on Debian/Ubuntu) are only needed for projects shipping `bin/libs/*.py`.

### Installation

```bash
git clone https://github.com/MrNotInk/Weld.git
cd Weld
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python weld.py --doctor
```

`requirements.txt` installs the runtime dependencies (`rich`, `tomli-w`),
the validator (`ruff`), and optional native build support (`cython`,
`setuptools`, `pyinstaller`). Nuitka is not needed or installed.

### Updating Weld

With Git installed and your Python environment active, existing users can run:

```bash
weld --check-update                 # compare with the version on main
weld --self-upgrade                 # confirm the upgrade when prompted
~/.local/bin/weld --version         # verify the newly installed copy
```

Use `weld --self-upgrade --yes` to skip the confirmation prompt. If you run
Weld from a source checkout rather than a `weld` command, use
`python weld.py --self-upgrade` instead.

The updater clones `main`, reads the repository's `version` file, and installs
`weld.py` as `~/.local/bin/weld`. Each release updates both version values,
so new versions are detectable once merged; no separate release download is
needed.
Make sure `~/.local/bin` is ahead of any older Weld executable on your `PATH`.

Self-upgrade updates Weld itself, not installed applications. Existing apps
remain unchanged; reinstall an unpinned package with `weld --install <package>`
(or a local `.wld` archive) to use the new script mode even when its package
version has not changed. `--update` / `--update-all` are for application
packages, not for updating Weld.

### Configuration

Weld stores its state in `~/.weld/` and reads `~/.weld/config.toml`:

```toml
[weld]
compiler = "nuitka"        # legacy name: Python scripts; or "pyinstaller"
libs_compiler = "cython"   # only "cython" is currently supported
default_version = "1.0.0"
auto_pip_install = true    # install a package's deps on `weld --install`
nuitka_opts = []           # legacy setting, saved but ignored
ruff_opts = ["--no-cache", "--output-format=concise"]  # `ruff check` options
```

Run `weld --doctor` at any time to check which tools are available. Optional
tools are labelled accordingly. PyInstaller is not probed or listed by doctor;
its availability is still checked during installation when
`compiler = "pyinstaller"` is selected.

#### Detailed error logs

When a Weld command fails, Weld saves a uniquely named report in
`~/.weld/logs/weld-error-<timestamp>-<pid>-<id>.log` and prints
`Full error log: <path>` to stderr. This also covers argument/configuration
errors, unexpected exceptions, and individual failures during `--update-all`.
Successful commands and `--help` do not leave error logs.

Reports include the command and working directory, full chained traceback,
console output, and **untruncated stdout and stderr from captured subprocesses**
(including successful steps and fallback attempts). Python's executable,
version, virtual environment, ABI/GIL settings, C compiler configuration,
tool paths, build-package versions, Weld settings, and relevant build
environment variables are included. Cython reports also retain the generated
setup script before temporary build files are removed; errors printed only
to stdout are no longer lost.

Logs are created with owner-only file permissions. The environment is limited
to a build-related allowlist, not dumped wholesale, but commands and tool output
may still contain private data: **review a log before sharing it**. `--clean`
preserves the entire logs directory; delete old reports manually when no longer
needed. If a report cannot be written, Weld warns without replacing the original
error. These reports cover Weld's own operations, not errors from installed
applications run directly (or after `--run` hands control to the application).

#### Executable script mode (legacy `nuitka` setting)

`--install` and `--update` no longer invoke Nuitka. For each top-level `.py`
entry in `bin/`, Weld removes only the final `.py` suffix, preserves its
existing shebang (or adds `#!/usr/bin/env python3`), and adds execute bits
like `chmod +x`. `main.py` becomes the package name, for example
`~/.weld/bin/myapp`. Script contents and other permission bits are preserved;
UTF-8 BOMs and CRLF shebangs are handled so the result runs on POSIX.

These are Python scripts, **not native binaries**: Python and the application's
dependencies must be available when they run. Keep the intended virtual
environment active when using the default `env python3` shebang. Source
archives and project files are not changed; preparation happens in a temporary
directory. Existing extensionless files are not overwritten by preparation.
Choose `compiler = "pyinstaller"` if you need native executable builds instead.

#### Free-threaded Python and Cython libraries

When Weld runs on a free-threaded CPython build and an application ships
`bin/libs/*.py`, its executable scripts start Python with `-X gil=1`. The GIL
is enabled before Cython extensions are imported, avoiding the warning about
modules such as `libs._clock` enabling it automatically. This does **not**
declare arbitrary extensions safe to run without the GIL or suppress other
runtime warnings. Pure-Python apps and normal GIL builds keep their existing
startup behavior.

These shebangs use `env -S` (available on modern Linux and macOS) to preserve
the interpreter command and options. After updating Weld, reinstall an affected
application to regenerate its executable, even if its package version is unchanged:

```bash
weld --install clock
```

For an existing installation before applying the fix, safely enable the GIL
for one invocation instead:

```bash
PYTHON_GIL=1 clock
```

Do not use `PYTHON_GIL=0` merely to silence the warning: that removes the
extension's GIL protection rather than addressing compatibility.

#### Custom Ruff options (`-W` / `-E`)

Extra options for Ruff (used by `--build`) are saved in `config.toml`:

```bash
weld -Wruff "--ignore=F401"       # add a Ruff option (saved)
weld -Wruff "--ignore=E501 --quiet" # several options in one string also work
weld -Eruff "--no-cache"          # even the defaults can be removed
```

`-W...` adds and `-E...` removes; both are repeatable and also accept the
`-Wruff=--ignore=F401` spelling. Removing an option that is not saved (or
adding one twice) prints a warning and changes nothing. The flags can be
combined with a command and then apply to that run immediately, e.g.
`weld --build myapp -Wruff "--ignore=F401"`. Options are matched exactly,
one flag at a time (remove `--output-format=concise`, not just
`--output-format`). `--doctor` and `--stats` show the current lists.

The legacy `-Wnuitka` / `-Enuitka` flags still save and remove `nuitka_opts`
for compatibility with existing configurations, but those options are ignored
and explicitly labelled as such. They do not enable native compilation.

## 🏁 Quick start

```bash
# Create a project and apply a template
python weld.py --new myapp
python weld.py --template cli          # run inside myapp/

# Validate and package it
python weld.py --build myapp           # -> myapp.wld

# Install, run, inspect and remove
python weld.py --install myapp.wld     # local archive
python weld.py --install ./myapp       # finds myapp.wld in the current dir
python weld.py --install clock         # registry name -> downloads via list.json
python weld.py --run myapp --help
python weld.py --list
python weld.py --info myapp
python weld.py --update clock         # update one package (registry or local)
python weld.py --update-all           # update every installed package
python weld.py --verify
python weld.py --remove myapp
```

`--run` must be the last weld option: everything after the package name is
forwarded to the installed binary unchanged.

## 🌐 Remote packages (Weld_tools registry)

`--install <name>` and `--update <name>` accept a bare package name. When
the argument is not a local path (it does not start with `./` and is not an
existing file/directory), weld clones
[`MrNotInk/Weld_tools`](https://github.com/MrNotInk/Weld_tools), reads
`list.json`, and downloads the archive from the `link` of the matching
entry:

```json
{
  "clock": {
    "version": "1.1.1",
    "link": "https://github.com/MrNotInk/clock/releases/download/v1.1.1/clock.wld"
  }
}
```

- `weld --install clock` — downloads the archive from the `link` and
  installs it. A name that is not in `list.json` prints
  `package not found`.
- `weld --update clock` — reinstalls the package from the registry only
  when the registry `version` is newer than the installed one; otherwise it
  prints `Already up to date`.
- `weld --update-all` — updates every installed package from the registry.
  Pinned packages, packages missing from the registry, and packages already
  on the registry version are skipped; a summary is printed at the end.
- Local paths keep working exactly as before: `--install ./clock` looks for
  `clock.wld` in the current directory, and `--install <dir>` looks for
  `<name>.wld` inside the directory.
- The Readme and Changelog shipped inside the archive are stored per
  package in `~/.weld/Readme/` (and are removed when an archive no longer
  contains them).

## 🧪 Tests

The test-suite runs Weld end-to-end in an isolated `HOME`, executing the
installed Python scripts directly and using a fake PyInstaller shim for
native build tests. It never touches your real `~/.weld` and does not need
Nuitka or a C toolchain.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## 📄 License

See the repository for license information.
