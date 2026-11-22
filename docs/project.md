# Building a Weld project

A Weld package is a directory with `config.json` plus a `bin/` folder.
Everything else is optional. You can build it with `weld --new`, by hand, or
by zipping it yourself.

- Shortest possible layout: [Minimum layout](#minimum-layout)
- Every optional part: [Full layout](#full-layout)
- All `config.json` keys: [`config.json` reference](#configjson-reference)
- Turning it into a `.wld`: [Building the archive](#building-the-archive)
- Checklist and pitfalls: [Common mistakes](#common-mistakes)

---

## Minimum layout

```
myapp/
├── config.json          # required
└── bin/
    └── main.py          # required: becomes the command "myapp"
```

`config.json` needs only `name` and `version`:

```json
{
  "name": "myapp",
  "version": "0.1.0",
  "desc": "what it does",
  "deps": []
}
```

`bin/main.py` is a plain script; no shebang is needed:

```python
print("hi from myapp")
```

On install Weld adds `#!/usr/bin/env python3`, removes the `.py` suffix, makes
the file executable and puts it in `~/.weld/bin/myapp`.

> `--build` (see below) additionally requires `ruff` on `PATH`, because it
> lints and byte-compiles every `.py` file. Zipping by hand skips that check.

---

## Full layout

```
myapp/
├── config.json           # required
├── Readme                # optional, exact name, no extension
├── Changelog             # optional, exact name, no extension
├── plugins/
│   └── __init__.py       # optional build hooks (see plugins.md)
└── bin/
    ├── main.py           # entry script -> command "myapp"
    ├── extra.py          # optional: becomes another command "extra"
    ├── data.txt          # optional: copied as a data file
    ├── assets/           # optional: copied as a data directory
    │   └── logo.png
    └── libs/             # optional: copied as-is into ~/.weld/bin/libs/
        ├── _clock.py
        └── native.so
```

Rules that matter:

| Item | Behaviour |
|---|---|
| `bin/main.py` | The package's own command (`~/.weld/bin/<name>`), unless `entry` selects another script. |
| other `bin/*.py` | Each top-level `.py` becomes an executable command named after the file stem (`extra.py` → `extra`). |
| `bin/libs/**` | Copied **verbatim** into `~/.weld/bin/libs/`. No compilation, no Cython, no C compiler, no headers. Subdirectories are preserved. |
| `bin/` data | Any other file or directory is copied into `~/.weld/bin/` and tracked, so `--remove` deletes it. |
| `Readme` / `Changelog` | Stored as `~/.weld/Readme/myapp-Readme` and `myapp-Changelog`. `--info` shows the Readme. |
| `plugins/` | Build hooks (`before_build`, `after_build`). The folder is packaged into the `.wld` like any other file, but the archived copy is never executed on install. |

`bin/libs/` is for modules your app imports as `libs.something`
(e.g. `from libs import _clock`). They land in `~/.weld/bin/libs/`, which is
on `sys.path` because the command lives in `~/.weld/bin/`.

---

## `config.json` reference

Required: `name`, `version`. Optional with defaults: `deps` (`[]`), `desc` (`""`).
Everything below is optional too.

```json
{
  "name": "clock",
  "version": "1.1.1",
  "desc": "Terminal clock",
  "deps": ["rich"],

  "entry": "cli.py",
  "aliases": ["clk", "time-now"],
  "env": {"CLOCK_MODE": "fast"},
  "auto_pip_install": false,

  "min_weld_version": "0.6.4",
  "python_requires": ">=3.11,<3.16",
  "requires": ["git", "ffmpeg"]
}
```

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Package/command name. `^[A-Za-z0-9][A-Za-z0-9_-]*$`; `libs` is reserved. |
| `version` | string/number | Must be non-empty. Used by `--update` / `--update-all`. |
| `desc` | string | Shown by `--list`, `--search`, `--info`. |
| `deps` | list of strings | pip packages installed on `--install` (via `uv pip install`, falling back to `pip`). |
| `entry` | string | Script in `bin/` that becomes the command. Default `main.py`. `.py` may be omitted; subdirectories are rejected. Used by `--install`, `--build` and `--template`. |
| `aliases` | list of strings | Extra command names installed as small `#!/bin/sh` launchers next to the real command, so they inherit its shebang, GIL flag and env. Never overwrite files owned by another package. |
| `env` | object | `NAME: "value"` pairs written as `NAME='value'` words into the command's `env -S` shebang. Applies only to that command. Quotes, newlines and NUL bytes are rejected. |
| `auto_pip_install` | bool | Per-package override of `auto_pip_install` from `~/.weld/config.toml`. |
| `min_weld_version` | string | Installation is refused on an older Weld (message points at `weld --self-upgrade`). |
| `python_requires` | string | Specifier check: `>=3.11`, `>=3.11,<3.16`, `==3.12.*`, `!=3.13`, `~=3.11`. The error quotes the running interpreter. |
| `requires` | list of strings | External tools that must be on `PATH`. Each is reported as `found` / `missing`; if anything is missing **nothing is installed**. `--doctor` lists them per package. |

Behaviour notes:

- `min_weld_version`, `python_requires` and `requires` are checked **before**
  dependencies are installed or any file in `~/.weld` is touched. A failed
  check leaves the previous installation exactly as it was.
- Applied values are stored in `~/.weld/packages.json` and shown by
  `--info` (`Entry`, `Aliases`, `Requires`, `Python`, `Weld`, `Env`).
- Unknown keys print `Warning: Unknown key '...' (ignored)`, so typos such as
  `"dependancies"` do not pass silently.
- `--template` rewrites the `entry` script (default `bin/main.py`); extra
  `bin/*.py` files are left alone.

---

## Building the archive

### With `weld --new` + `--build`

```bash
weld --new myapp          # skeleton with hello template
cd myapp
# ... edit bin/main.py, config.json, add your files ...
weld --build .            # or: cd .. && weld --build myapp
```

`weld --new myapp` creates:

```
myapp/
├── config.json           # name/version/deps/desc
├── Readme
├── Changelog
├── plugins/__init__.py   # empty hook stubs
└── bin/
    ├── main.py           # hello template
    └── libs/             # empty
```

`--build` then:

1. warns when `Readme` / `Changelog` is missing (optional),
2. requires `ruff` and runs `ruff check` + byte-compilation on every `.py` in
   `bin/` and `bin/libs/`,
3. writes `<folder>.wld` **next to** the project directory,
4. runs the project's `before_build` / `after_build` plugin hooks.

A `.wld` file is a zip. Existing archives with the same name are replaced.

### Without Weld

```bash
cd myapp && zip -qr ../myapp.wld .
```

`config.json` **must be at the archive root**. `zip -r myapp.wld myapp/` puts it
one level down and installation fails with `config.json missing from archive`.

Never included when Weld creates the archive: `.git`, `.hg`, `.svn`, `.idea`,
`.vscode`, `.venv`, `venv`, `env`, `ENV`, `__pycache__`, `.mypy_cache`,
`.ruff_cache`, `.pytest_cache`, `node_modules`, `build`, `dist`, `_pyibuild`,
files ending in `.pyc .pyo .bak .spec .wld`, and symlinks. If you zip by hand
you should exclude these yourself.

Limits when installing an archive: 10 000 entries and 1 GiB uncompressed.

---

## What installation produces

```
~/.weld/
├── bin/
│   ├── myapp                     # entry script (+execute bits)
│   ├── extra                     # other bin/*.py scripts
│   ├── clk                       # alias launchers, if configured
│   ├── data.txt                  # bin data
│   ├── assets/logo.png           # bin data directory
│   └── libs/                     # copied from bin/libs
│       ├── _clock.py
│       └── native.so
├── Readme/myapp-Readme
├── packages.json                 # files, _lib_files, entry, aliases, env, ...
├── history.log
└── config.toml
```

Useful checks after installing:

```bash
weld --list
weld --info myapp
weld --verify
weld --run myapp
weld --remove myapp
```

---

## Common mistakes

| Message | Cause / fix |
|---|---|
| `config.json missing from archive` | Archive has a top-level folder. Zip the project *contents*. |
| `bin/ missing from archive` | Scripts are not under `bin/`. |
| `bin/main.py not found in archive` | No `main.py` and `entry` is unset. Add one or set `entry`. |
| `'entry' file bin/x.py ... was not found` | `entry` points at a missing file or a subdirectory. |
| `Invalid package name` | `name` violates `^[A-Za-z0-9][A-Za-z0-9_-]*$`, or is `libs`. |
| `Invalid alias '...'` | Alias contains path separators or is a reserved name. |
| `Cannot create alias '...': ... already exists` | The file exists and is not owned by this package. |
| `Required tool 'ruff' not found` | `--build` needs ruff installed. |
| `Unable to resolve ...` / pip errors | `deps` could not be installed; fix the package index or your environment. |
| `needs <tool> to run ... was not installed` | A `requires` entry is missing from `PATH`. Install it, retry. |
| `needs Python >=x` | `python_requires` does not match the interpreter used to run Weld. |
| `needs Weld x` | Upgrade Weld with `weld --self-upgrade`. |

Platform: Weld targets Linux and macOS. It relies on POSIX file locking,
executable bits, `env -S` shebangs and `.so`/`sh` files.
