# ⚡ Weld (`weld`)

A fast, lightweight Python application packaging and distribution tool designed to simplify building, bundling, installing, and managing Python CLI applications and standalone executables.

---

## 🚀 Overview

**Weld** simplifies Python app distribution by handling validation, compilation, dependency management, and local packaging into portable `.wld` archives. It includes built-in application templates, hooks for lifecycle plugins, concurrent package locking, and automatic compilation via tools like Nuitka, PyInstaller, or Cython.

## ✨ Features

* **Project Scaffolding**: Quickly create new project structures with ready-to-use templates (Flask, FastAPI, CLI, Daemon, TUI, Scraper, etc.).
* **Code Validation**: Runs `ruff` checks and bytecode compilation (`py_compile`) before building.
* **Compilation Engines**: Compiles executables using **Nuitka** or **PyInstaller**, and shared library modules (`.so`) using **Cython**.
* **Custom Tool Options**: Add or remove saved Nuitka/Ruff options with `-Wnuitka` / `-Enuitka` / `-Wruff` / `-Eruff` (e.g. `weld -Wruff "--ignore=F401"`).
* **Package Archives (`.wld`)**: Archives your Python binaries, libraries, configurations, Readmes, and Changelogs into portable `.wld` files.
* **Dependency Management**: Automatically manages and installs PyPI dependencies via `uv` or `pip` (can be disabled with `auto_pip_install = false`).
* **Package Lifecycle**: Supports installing (from local `.wld` archives or
  the `Weld_tools` registry), updating (single package or all at once with
  `--update-all`), pinning/unpinning, running, searching, duplicating,
  renaming, and removing packages.
* **Health & History Tracking**: Includes a diagnostic doctor (`--doctor`), environment verification (`--verify`), orphaned file cleaner (`--clean`), and action history log (`--history`).
* **Shell Completions**: Generates Zsh auto-completion scripts out of the box.

## 📋 Prerequisites

* **Python 3.11+** — 3.11 through 3.13 are recommended, because Nuitka and
  PyInstaller do not always support the newest Python release on day one.
  On Python 3.10 and older the `tomli` backport is used automatically.
* **Linux or macOS** (Weld uses POSIX file locking and `.so` shared libraries).
* **At least one compiler**: [Nuitka](https://nuitka.net/) or
  [PyInstaller](https://pyinstaller.org/). Cython is only needed for projects
  that ship modules in `bin/libs/`.
* A C toolchain and Python development headers for Cython builds
  (e.g. `python3-dev` on Debian/Ubuntu).

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
the validator (`ruff`), and the compilation engines (`cython`, `setuptools`,
`nuitka`, `pyinstaller`).

### Configuration

Weld stores its state in `~/.weld/` and reads `~/.weld/config.toml`:

```toml
[weld]
compiler = "nuitka"        # "nuitka" or "pyinstaller"
libs_compiler = "cython"   # only "cython" is currently supported
default_version = "1.0.0"
auto_pip_install = true    # install a package's deps on `weld --install`
nuitka_opts = []           # extra options passed to Nuitka (see below)
ruff_opts = ["--no-cache", "--output-format=concise"]  # `ruff check` options
```

Run `weld --doctor` at any time to check which tools are available. Tools that
are not required for the configured compiler are reported as optional.

#### Custom Nuitka / Ruff options (`-W` / `-E`)

Extra options for Nuitka (used by `--install`/`--update`) and Ruff (used by
`--build`) are saved in `config.toml` and managed with four flags:

```bash
weld -Wnuitka "--standalone"     # add a Nuitka option (saved)
weld -Wnuitka "--jobs=4 --quiet" # several options in one string also work
weld -Enuitka "--standalone"     # remove it again (warns if not found)

weld -Wruff "--ignore=F401"      # add a Ruff option (saved)
weld -Eruff "--no-cache"         # even the defaults can be removed
```

`-W...` adds and `-E...` removes; both are repeatable and also accept the
`-Wruff=--ignore=F401` spelling. Removing an option that is not saved (or
adding one twice) prints a warning and changes nothing. The flags can be
combined with a command and then apply to that run immediately, e.g.
`weld --build myapp -Wruff "--ignore=F401"`. Options are matched exactly,
one flag at a time (remove `--output-format=concise`, not just
`--output-format`). `--doctor` and `--stats` show the current lists.

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

The test-suite runs Weld end-to-end in an isolated `HOME` using fake
Nuitka/PyInstaller shims, so it never touches your real `~/.weld` and does not
need a C toolchain.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## 📄 License

See the repository for license information.
