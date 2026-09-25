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
* **Dependency Management**: Automatically manages and installs PyPI dependencies via `uv` or `pip`.
* **Package Lifecycle**: Supports installing, updating, pinning/unpinning, running, searching, duplicating, renaming, and removing packages.
* **Health & History Tracking**: Includes a diagnostic doctor (`--doctor`), environment verification (`--verify`), orphaned file cleaner (`--clean`), and action history log (`--history`).
* **Shell Completions**: Generates Zsh auto-completion scripts out of the box.

## 📋 Prerequisites

Weld requires **Python 3.11+** (or standard Python with `tomli`).

### Python Dependencies
Python Should Be 3.14< becuase nuitka support only this right now check via```
python --version```
Install the required packages using pip:
```bash
pip install rich tomli-w cython nuitka pyinstaller ruff
```
