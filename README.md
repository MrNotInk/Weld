================================================================================
                                WELD (.weld.py)
================================================================================

A fast, lightweight Python application packaging and distribution tool designed
to simplify building, bundling, installing, and managing Python CLI applications
and standalone executables[cite: 1].

--------------------------------------------------------------------------------
1. OVERVIEW
--------------------------------------------------------------------------------

Weld simplifies Python app distribution by handling validation, compilation,
dependency management, and local packaging into portable .wld archives[cite: 1]. It
includes built-in application templates, hooks for lifecycle plugins, concurrent
package locking, and automatic compilation via tools like Nuitka, PyInstaller,
or Cython[cite: 1].


--------------------------------------------------------------------------------
2. FEATURES
--------------------------------------------------------------------------------

* Project Scaffolding
  Quickly create new project structures with ready-to-use templates (Flask,
  FastAPI, CLI, Daemon, TUI, Scraper, etc.)[cite: 1].

* Code Validation
  Runs 'ruff' checks and bytecode compilation ('py_compile') before building[cite: 1].

* Compilation Engines
  Compiles executables using Nuitka or PyInstaller, and shared library
  modules (.so) using Cython[cite: 1].

* Package Archives (.wld)
  Archives your Python binaries, libraries, configurations, Readmes, and
  Changelogs into portable .wld files[cite: 1].

* Dependency Management
  Automatically manages and installs PyPI dependencies via 'uv' or 'pip'[cite: 1].

* Package Lifecycle
  Supports installing, updating, pinning/unpinning, running, searching,
  duplicating, renaming, and removing packages[cite: 1].

* Health & History Tracking
  Includes a diagnostic doctor (--doctor), environment verification (--verify),
  orphaned file cleaner (--clean), and action history log (--history)[cite: 1].

* Shell Completions
  Generates Zsh auto-completion scripts out of the box[cite: 1].


--------------------------------------------------------------------------------
3. PREREQUISITES
--------------------------------------------------------------------------------

Weld requires Python 3.11+ (or standard Python with 'tomli')[cite: 1].

Python Dependencies:
-------------------
pip install rich tomli-w

(Optional dependencies: 'tomli' if Python < 3.11, 'ruff', 'cython', 'nuitka',
'pyinstaller', 'uv')[cite: 1]


--------------------------------------------------------------------------------
4. DIRECTORY STRUCTURE
--------------------------------------------------------------------------------

By default, Weld manages projects in the home directory (~/.weld)[cite: 1]:

~/.weld/
├── bin/                   # Compiled binaries and executables
│   └── libs/              # Compiled C-extension libraries (.so)
├── Readme/                # Stored package Readme and Changelog documentation
├── plugins/               # Global plugin hooks
├── templates/             # Starter project templates (.py)
├── packages.json          # Package manifest database
├── packages.json.lock     # Concurrent lockfile
├── config.toml            # Global configuration
└── history.log            # Activity audit trail[cite: 1]


--------------------------------------------------------------------------------
5. QUICK START
--------------------------------------------------------------------------------

[Step 1] Create a New Project
    python .weld.py --new my_app[cite: 1]

[Step 2] Apply a Template (Optional)
    cd my_app
    python path/to/.weld.py --template cli[cite: 1]

    Available built-in templates: hello, cli, flask, fastapi, daemon, scraper,
    tui, bot, cron, tcp_server, grpc, data_pipeline, rest_client, config_app, plugin[cite: 1].

[Step 3] Build a Weld Archive (.wld)
    python .weld.py --build ./my_app[cite: 1]

[Step 4] Install the Package
    python .weld.py --install my_app.wld[cite: 1]

[Step 5] Run the Binary
    python .weld.py --run my_app -- --arg1 value[cite: 1]
    (Note: '--run' must be the final flag; all subsequent arguments are passed
    directly to your binary)[cite: 1]


--------------------------------------------------------------------------------
6. COMMAND REFERENCE
--------------------------------------------------------------------------------

COMMAND       USAGE                         DESCRIPTION
--------------------------------------------------------------------------------
List          weld --list                   List installed packages/capsules[cite: 1]
New           weld --new <folder>           Create a new project skeleton[cite: 1]
Build         weld --build <folder>         Validate & bundle into .wld archive[cite: 1]
Install       weld --install <file.wld>     Extract, install deps, compile[cite: 1]
Update        weld --update <file.wld>      Update an existing package[cite: 1]
Remove        weld --remove <package>       Uninstall package & clean files[cite: 1]
Run           weld --run <package> [args]   Execute binary with forwarded args[cite: 1]
Info          weld --info <package>         Show metadata, deps, and Readme[cite: 1]
Search        weld --search <query>         Search installed packages[cite: 1]
Pin/Unpin     weld --pin / --unpin <pkg>    Prevent/allow package updates[cite: 1]
Rename        weld --rename <old> <new>     Rename an installed package[cite: 1]
Duplicate     weld --duplicate <src> <dst>  Duplicate an installed package[cite: 1]
Doctor        weld --doctor                 Verify system env & tools[cite: 1]
Verify        weld --verify                 Check integrity of binaries/libs[cite: 1]
Clean         weld --clean                  Sweep orphaned files in ~/.weld[cite: 1]
Stats         weld --stats                  Print stats & env settings[cite: 1]
History       weld --history [package]      View action history logs[cite: 1]
Templates     weld --templates              List all available templates[cite: 1]
Completions   weld --completions zsh        Output Zsh auto-completion script[cite: 1]


--------------------------------------------------------------------------------
7. CONFIGURATION (~/.weld/config.toml)
--------------------------------------------------------------------------------

[weld]
compiler = "nuitka"         # Options: "nuitka", "pyinstaller"[cite: 1]
libs_compiler = "cython"    # Options: "cython"[cite: 1]
default_version = "1.0.0"[cite: 1]
auto_pip_install = true[cite: 1]


--------------------------------------------------------------------------------
8. PROJECT CONFIG STRUCTURE (config.json)
--------------------------------------------------------------------------------

{
    "name": "my_app",
    "version": "1.0.0",
    "desc": "A brief description of the package",
    "deps": [
        "requests",
        "rich"
    ]
}[cite: 1]
