# Using Weld

Weld builds, installs and manages Python CLI apps from `.wld` archives
(zip files with a `config.json`). This page covers the command line, the state
Weld keeps, and the update/registry behaviour.

- Creating a package: [project.md](project.md)
- Build/install/remove hooks: [plugins.md](plugins.md)
- Templates: [templates.md](templates.md)
- Option reference: [config.md](config.md)

---

## Install Weld

```bash
git clone https://github.com/MrNotInk/Weld.git
cd Weld
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python weld.py --doctor
```

`python weld.py` and `weld` are equivalent; the self-updater installs a
`weld` command into `~/.local/bin`. Requirements: Python 3.11+ and Linux/macOS.
Nuitka, Cython, setuptools and a C toolchain are **not** needed.

---

## Command overview

Everything is a flag on one command; there are no subcommands.

| Command | What it does |
|---|---|
| `weld --new <folder>` | Create a project skeleton (`config.json`, `bin/main.py`, `Readme`, `Changelog`, `plugins/`, `bin/libs/`). |
| `weld --template <name>` | Apply a built-in template to the current project (see templates.md). |
| `weld --templates` | List built-in and user templates. |
| `weld --build <folder>` | Validate with ruff and create `<folder>.wld` next to the project. |
| `weld --install <file\|dir\|package>` | Install a local archive, a directory containing one, or a registry package name. |
| `weld --update <file\|dir\|package>` | Update one package (registry update skipped when not newer). |
| `weld --update-all` | Update every installed package from the registry. |
| `weld --run <package> [args...]` | Replace the process with the installed command, forwarding arguments. |
| `weld --list` | Table of installed packages. |
| `weld --list-sort <key>` | Sort by `name`, `version`, `installed_at`, `updated_at`. |
| `weld --list-filter <field=glob>` | Filter, e.g. `--list-filter version=1.*`. Fields: `name version desc note deps files libs pinned installed_at updated_at`. |
| `weld --info <package>` | Details, requirements, env, entry, aliases and the Readme. |
| `weld --search <query>` | Match installed packages by name/description. |
| `weld --verify` | Check every tracked file and library exists (and the command is executable). |
| `weld --clean` | Remove orphaned files, caches and build leftovers from `~/.weld` (asks first). |
| `weld --addc <package>` | Copy an installed package's `libs/` files into the current project's `bin/libs/`. |
| `weld --pin` / `--unpin <package>` | Block/unblock updates and removal. |
| `weld --note <package> <text...>` | Store a free-form note. |
| `weld --rename <old> <new>` | Rename a package's command and record. |
| `weld --duplicate <src> <dst>` | Copy a package under a new name (shares libs). |
| `weld --edit <package>` | Open the Readme in `$EDITOR` (or details if none exists). |
| `weld --history [package]` | Show the action log. |
| `weld --stats` | Counts, settings and paths. |
| `weld --doctor` | Tool availability, venv check, and tools required by installed packages. |
| `weld --completions zsh` | Print a zsh completion script. |
| `weld --check-update` | Compare the local version with `main`. |
| `weld --self-upgrade [--yes] [--force]` | Update Weld itself from GitHub (git clone, release fallback). |
| `weld --version` | Print the version. |
| `weld --help` / no arguments | Full usage. |

---

## Build → install → run

```bash
weld --new myapp
cd myapp
# edit bin/main.py, config.json ...
weld --build .
weld --install ../myapp.wld
weld --run myapp --flag value
```

- `--build` needs `ruff`; it lints and byte-compiles `bin/*.py` and
  `bin/libs/*.py`, then writes `<folder>.wld`. Symlinks and caches are skipped.
- `--install` copies scripts, data and `bin/libs/` into `~/.weld`, tracks every
  file, and installs `deps` with `uv pip install` (fallback `pip`).
- Installation is staged: a failure while preparing or checking leaves the
  previous version untouched.
- `--run` must be the **last** Weld flag; everything after the package name is
  forwarded unchanged. It uses `execv`, so it replaces Weld (no wrapper
  process, no post-run handling).

Installed scripts are Python scripts, not native binaries: the interpreter in
the shebang and the app's dependencies must be available at run time. Scripts
keep their original shebang when present; otherwise `#!/usr/bin/env python3` is
added. Nuitka flags are accepted but ignored.

---

## Installing by name (registry)

A bare name that is not a path is resolved through
[`MrNotInk/Weld_tools`](https://github.com/MrNotInk/Weld_tools):

```bash
weld --install clock      # downloads the archive from list.json
weld --update clock
weld --update-all
```

- Weld clones the registry, reads `list.json` (`{"clock": {"version": "1.1.1",
  "link": "https://..."}}`) and downloads the `link`.
- `--update` skips the download when the registry version is not newer;
  `--update-all` skips pinned packages and packages missing from the registry
  and prints a summary.
- Local paths always work the same way: `--install ./myapp.wld`,
  `--install ./myapp` (finds `myapp.wld` inside), `--install myapp.wld`.

---

## Libraries are copied, not compiled

`bin/libs/` from an archive is copied **verbatim** to `~/.weld/bin/libs/`
(subdirectories included, caches and symlinks skipped). Nothing is cythonized
and no compiler is used, so this works on any Python that can import the
modules — including new pre-releases where Cython builds may crash.

- Prebuilt `.so` files are copied as they are. A binary built for a different
  Python ABI cannot be fixed by copying; ship sources (`libs/_clock.py`) or a
  wheel for the right interpreter.
- Copied files are tracked in `packages.json` under `_lib_files`; `--verify`,
  `--remove`, `--clean`, `--addc` and updates all use that list.
- On a free-threaded CPython build, the package's own command starts with
  `-X gil=1` when the archive ships libraries, so native modules do not enable
  the GIL noisily. Helper scripts keep their own shebang. This does not make
  arbitrary extensions thread-safe.
- `libs_compiler = "cython"` is still accepted in the config file and ignored.

---

## Weld state (`~/.weld`)

```
~/.weld/
├── bin/                 # installed commands, data and libs/
├── Readme/              # <package>-Readme and <package>-Changelog
├── packages.json        # database: files, _lib_files, entry, aliases, env, ...
├── packages.json.lock   # flock target for concurrent runs
├── config.toml          # settings
├── history.log          # tab-separated action log
├── logs/                # error reports (never removed by --clean)
├── plugins/__init__.py  # global lifecycle hooks
└── templates/           # user templates (one .py per template)
```

`config.toml`:

```toml
[weld]
compiler = "nuitka"        # legacy name: executable Python scripts; or "pyinstaller"
default_version = "1.0.0"  # used by --new
auto_pip_install = true    # install deps on --install (packages may override)
nuitka_opts = []           # legacy, saved and ignored
ruff_opts = ["--no-cache", "--output-format=concise"]
```

Manage Ruff options with `-Wruff "<opt>"` / `-Eruff "<opt>"` (repeatable;
`--stats` and `--doctor` show the current list). `-Wnuitka` / `-Enuitka` still
save and remove the legacy list.

---

## Error logs

When a command fails, Weld writes a private report to
`~/.weld/logs/weld-error-<timestamp>-<pid>-<id>.log` and prints
`Full error log: <path>` on stderr. Reports include the command, working
directory, full traceback, console output, untruncated subprocess stdout/stderr,
Python/ABI/GIL details, toolchain paths, Weld settings and an allowlisted set of
build environment variables. Directories are `0700`, files `0600`.

Successful commands and `--help` leave no logs; `--clean` preserves the whole
logs directory. Reports may contain private data from tool output — review
before sharing. Failures inside an installed app, after `--run` hands over the
process, are outside Weld's control.

---

## Updating Weld itself

```bash
weld --check-update
weld --self-upgrade            # confirm when prompted
weld --self-upgrade --yes      # no prompt
```

The updater clones `main`, reads the repository `version` file and installs
`weld.py` as `~/.local/bin/weld` (release assets are only a fallback). Make sure
`~/.local/bin` comes before any older Weld on `PATH`.

Self-upgrade does **not** rewrite installed apps. Reinstall a package to pick up
new install behaviour, even when its version has not changed:

```bash
weld --install myapp
```

---

## Exit codes and scripting

- `0` success; `1` on errors (handled or unexpected); argparse errors exit `2`.
- Errors are printed to stdout/stderr; `Full error log:` goes to stderr.
- `--run` forwards the app's own exit status, because it replaces Weld.
- `--list-filter`, `--list-sort` and `--verify` are handy in CI; `--verify`
  exits non-zero when anything is missing.
