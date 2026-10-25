# ⚡ Weld (`weld`)

A fast, lightweight Python application packaging and distribution tool designed to simplify building, bundling, installing, and managing Python CLI applications and standalone executables.

---

## 🚀 Overview

**Weld** simplifies Python app distribution by handling validation, compilation, dependency management, and local packaging into portable `.wld` archives. It includes built-in application templates, hooks for lifecycle plugins, concurrent package locking, and automatic compilation via tools like Nuitka, PyInstaller, or Cython.

## ✨ Features

* **Project Scaffolding**: Quickly create new project structures with ready-to-use templates (Flask, FastAPI, CLI, Daemon, TUI, Scraper, etc.).
* **Code Validation**: Runs `ruff` checks and bytecode compilation (`py_compile`) before building.
* **Compilation Engines**: Compiles executables using **Nuitka** or **PyInstaller**, and shared library modules (`.so`) using **Cython**.
* **Package Archives (`.wld`)**: Archives your Python binaries, libraries, configurations, Readmes, and Changelogs into portable `.wld` files.
* **Dependency Management**: Automatically manages and installs PyPI dependencies via `uv` or `pip` (can be disabled with `auto_pip_install = false`).
* **Package Lifecycle**: Supports installing, updating, pinning/unpinning, running, searching, duplicating, renaming, and removing packages.
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
```

Run `weld --doctor` at any time to check which tools are available. Tools that
are not required for the configured compiler are reported as optional.

## 🏁 Quick start

```bash
# Create a project and apply a template
python weld.py --new myapp
python weld.py --template cli          # run inside myapp/

# Validate and package it
python weld.py --build myapp           # -> myapp.wld

# Install, run, inspect and remove
python weld.py --install myapp.wld
python weld.py --run myapp --help
python weld.py --list
python weld.py --info myapp
python weld.py --verify
python weld.py --remove myapp
```

`--run` must be the last weld option: everything after the package name is
forwarded to the installed binary unchanged.

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
