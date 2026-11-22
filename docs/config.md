# Configuration reference

Weld has two configuration files:

| File | Scope |
|---|---|
| `config.json` | One package/project: name, version, deps, entry, aliases, env, requirements. |
| `~/.weld/config.toml` | Weld itself: compiler mode, defaults, saved tool options. |

Package layout and `config.json` in context: [project.md](project.md).

---

## `config.json` (per package)

Only `name` and `version` are required.

| Key | Type | Default | Notes |
|---|---|---|---|
| `name` | string | — | `^[A-Za-z0-9][A-Za-z0-9_-]*$`, not `libs`, must start with a letter or digit. |
| `version` | string or number | — | Non-empty after string conversion. Drives `--update` / `--update-all`. |
| `desc` | string | `""` | Shown in `--list`, `--search`, `--info`. |
| `deps` | list of non-empty strings | `[]` | pip packages installed on `--install` (`uv pip install`, fallback `pip`). |
| `entry` | string | `main.py` | File in `bin/` that becomes the command. `.py` optional; a directory or missing file is an error. Honoured by `--install`, `--build`, `--template`. |
| `aliases` | list of names | `[]` | Extra command names, each a `#!/bin/sh` launcher that `exec`s the real command. Duplicates and the package's own name are dropped. Path separators, reserved names and files owned by other packages are refused. |
| `env` | object | `{}` | `NAME: value` pairs (values stringified, `true`/`false` rejected). Emitted as `NAME='value'` words in the command's `env -S` shebang. No quotes, newlines or NUL. |
| `auto_pip_install` | `true`/`false` | unset | Overrides `[weld] auto_pip_install` for this package. Unset = use the global setting. |
| `min_weld_version` | string | `""` | Refuse to install when `weld --version` is older. |
| `python_requires` | string | `""` | Comma-free specifier list: `>=3.11`, `>=3.11,<3.16`, `==3.12.*`, `!=3.13`, `~=3.11`, `<3.15`. |
| `requires` | list of tool names | `[]` | Must exist on `PATH`. `[A-Za-z0-9][A-Za-z0-9_.+-]*`. |

Anything else prints `Warning: Unknown key '<key>' in <source> (ignored)`.

Example:

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

### How the values are applied

| Value | Effect on disk / runtime |
|---|---|
| `entry` | Renamed to `<name>.py` while installing, so the command is `~/.weld/bin/<name>`. Build and template use the same file. |
| `aliases` | `~/.weld/bin/<alias>` created and tracked in `files`; `--remove` deletes them. |
| `env` | Only in the command's shebang, e.g. `#!/usr/bin/env -S CLOCK_MODE='fast' python3`. Helper scripts and your shell are untouched. Combined with the free-threading flag when needed: `... python3 -X gil=1`. |
| `auto_pip_install` | Skips/forces the dependency step for that package only. |
| `min_weld_version`, `python_requires`, `requires` | Checked before dependencies and before anything in `~/.weld` is modified, so a failure leaves the installed version intact. |
| all of the above | Stored in `packages.json` and displayed by `--info`. |

### Requirement checks in detail

`python_requires` is evaluated against the interpreter running **Weld**
(`sys.version_info`), because that is the interpreter used at install time and
in the default `env python3` shebang. Supported forms:

```
>=3.11        >=3.11,<3.16        ==3.12.*        !=3.13
<3.15         ~=3.11              3.12.*          (bare wildcard == )
```

`~=` means "compatible with": `~=3.11` accepts 3.11.x but not 4.0.
Anything that does not parse produces `invalid python_requires entry '...'`.

`requires` prints every declared tool, marking it `found` or `missing`, for
example:

```text
Error: 'clock' needs ffmpeg to run, but it was not found on PATH.
  found    git
  missing  ffmpeg
Install the missing tool(s) and run the command again; 'clock' was not installed.
```

`--doctor` additionally shows a **Tool requirements from installed packages**
section listing `requires` entries and which installed package needs them. The
global tool table is unchanged — nothing is probed there because of this field.

---

## `~/.weld/config.toml`

```toml
[weld]
compiler = "nuitka"        # "nuitka" (legacy name) = executable Python scripts
                           # "pyinstaller" = native onefile build
default_version = "1.0.0"  # version used by --new
auto_pip_install = true    # global dependency switch
nuitka_opts = []           # legacy, saved but ignored
ruff_opts = ["--no-cache", "--output-format=concise"]
```

- `compiler = "nuitka"` is the legacy name for script mode; no Nuitka is
  invoked.
- `pyinstaller` requires PyInstaller at install time and does not support every
  new Python release immediately.
- `libs_compiler` from older versions is accepted and ignored — `bin/libs/` is
  copied as-is, never compiled.
- Manage Ruff options with `-Wruff "<opt>"` (add) and `-Eruff "<opt>"` (remove);
  both are repeatable and are applied to that run as well. `-Wnuitka` /
  `-Enuitka` still edit the legacy list. `--stats` and `--doctor` print the
  current values.
- An invalid TOML file is a hard error with the file path and parser message.

---

## Registry `list.json` (Weld_tools)

Not a Weld config file, but read during `--install <name>`:

```json
{
  "clock": {
    "version": "1.1.1",
    "link": "https://github.com/MrNotInk/clock/releases/download/v1.1.1/clock.wld"
  }
}
```

`link` is downloaded to a temporary `.wld`, then installed normally (so the
archive's own `config.json` rules still apply). `version` is compared with the
installed version for `--update` / `--update-all`.

---

## `packages.json` (managed by Weld)

`~/.weld/packages.json` is the database; edit it only when you know what you
are doing (`--list`/`--info` are safer). A record contains:

| Field | Meaning |
|---|---|
| `deps`, `version`, `desc` | Copied from the archive config. |
| `files` | Tracked paths inside `~/.weld/bin` (commands, aliases, data, nested files). |
| `_lib_files` | Tracked paths inside `~/.weld/bin/libs` copied from `bin/libs/`. |
| `libs` | Legacy `.so` module names from older Cython installs; still honoured for removal. |
| `entry`, `aliases`, `env` | Applied package options. |
| `requires`, `python_requires`, `min_weld_version` | Declared requirements (informational after install). |
| `pinned`, `note` | Managed by `--pin`/`--note`. |
| `installed_at`, `updated_at` | Timestamps. |
| `has_readme`, `has_changelog` | Whether `Readme/<pkg>-Readme` etc. exist. |

Writes are atomic and guarded by `packages.json.lock` (`flock`), so concurrent
Weld runs do not corrupt the file.
