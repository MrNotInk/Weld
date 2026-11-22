# Plugins and hooks

Weld runs optional hooks around build, install, update and remove. A hook is
just a callable in a plugin module. There are two plugin locations:

| Location | Hooks that run | Purpose |
|---|---|---|
| `<project>/plugins/__init__.py` | `before_build`, `after_build` only | Project-specific build steps, loaded from the project directory when you run `--build`. |
| `~/.weld/plugins/__init__.py` | all eight hooks | Global lifecycle automation for everything you install on this machine. |

`weld --new <folder>` creates the project plugin with empty stubs, and
bootstrap creates an empty global plugin if it is missing.

---

## Hook reference

| Hook | Called with | When |
|---|---|---|
| `before_build` | `config: dict` | After config validation, before ruff/bytecode checks. Project plugin first, then global. |
| `after_build` | `config: dict`, `archive_path: str` | After the `.wld` is written. Project plugin first, then global. |
| `before_install` | `config: dict` | After the archive config is read, pin check done, before dependencies and file changes. |
| `after_install` | `config: dict` | After files and `packages.json` are written, before the success line. |
| `before_update` | `config: dict` | At the start of `--update` / registry update; the new archive config is passed. |
| `after_update` | `config: dict` | After the update completes. |
| `before_remove` | `pkg_name: str` | Before files are deleted (inside the package lock). |
| `after_remove` | `pkg_name: str` | After the record is deleted. |

Notes:

- Hooks are optional; define only the ones you need. Weld looks the name up with
  `getattr` and calls it if it is callable.
- `config` is the normalized archive/project config (all optional keys filled
  in: `entry`, `aliases`, `env`, `requires`, ...).
- `before_update` / `after_update` also run for a fresh install triggered by
  `--update` on a package that is not installed yet.
- Install/update/remove hooks are global-only. A project's `plugins/` folder is
  packaged into the `.wld` like any other file, but the archived copy is never
  imported: at install time Weld looks only at `~/.weld/plugins/__init__.py`,
  so a downloaded archive cannot run install-time code.
- `--build` warns `Loading plugin from untrusted path` when it loads a
  project plugin.

---

## Failures are never fatal

A plugin that raises is reported as a warning and the command continues:

```text
Warning: Plugin hook 'after_build' raised: ...
```

A plugin that fails to import at all is skipped with
`Warning: Plugin failed to load: ...`. Weld never lets plugin code change the
exit status of a successful build/install/remove.

Because hooks run inside Weld's process, they inherit Weld's diagnostics: any
output a hook prints is captured in an error report if a later step fails.
Do not print secrets.

---

## Minimal examples

### Global: log every install to a file

`~/.weld/plugins/__init__.py`:

```python
from datetime import datetime
from pathlib import Path


def _append(line: str) -> None:
    with Path("~/.weld/hooks.log").expanduser().open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")


def after_install(config: dict) -> None:
    _append(f"installed {config['name']} {config['version']}")


def after_remove(pkg_name: str) -> None:
    _append(f"removed {pkg_name}")
```

### Global: refuse to remove anything named `clock`

```python
def before_remove(pkg_name: str) -> None:
    if pkg_name == "clock":
        raise RuntimeError("clock is protected")
```

This does **not** abort the removal (hooks only warn). Use `weld --pin clock`
when you want removal blocked for real.

### Project: regenerate a version file before building

`myapp/plugins/__init__.py`:

```python
from pathlib import Path
import json


def before_build(config: dict) -> None:
    Path("bin/_version.py").write_text(
        f"__version__ = {json.dumps(config['version'])}\n", encoding="utf-8"
    )


def after_build(config: dict, archive_path: str) -> None:
    print(f"built {archive_path}")
```

`before_build` runs before ruff validates `bin/*.py`, so a generated file is
linted and shipped. Keep generated files out of `.gitignore` if you want them
archived.

### Project: copy extra assets into `bin/` before packaging

```python
from pathlib import Path
import shutil


def before_build(config: dict) -> None:
    target = Path("bin/assets")
    target.mkdir(parents=True, exist_ok=True)
    for source in Path("design").glob("*.png"):
        shutil.copy2(source, target / source.name)
```

---

## Where plugins live, in short

```text
myproject/
├── config.json
├── bin/...
└── plugins/__init__.py     # before_build / after_build

~/.weld/
└── plugins/__init__.py     # all hooks, applied to every command
```

Both are plain Python modules loaded with `importlib` under the name
`weld_plugin`. Reload happens per command, so edits take effect on the next
run without restarting anything.
