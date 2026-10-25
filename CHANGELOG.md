# 📜 Changelog

All notable changes to **Weld** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.4.0] - 2026-10-25

### Added

- **Saved tool options for Nuitka and Ruff**, managed from the command line:
  - `-Wnuitka "<opt>"` — add option(s) that Weld passes to Nuitka on every
    `--install` / `--update`, e.g. `weld -Wnuitka "--standalone"`.
  - `-Enuitka "<opt>"` — remove a saved Nuitka option. Removing an option
    that is not saved prints a **warning** (`... not found, nothing to
    remove`) and changes nothing.
  - `-Wruff "<opt>"` — add option(s) that Weld passes to `ruff check` on
    every `--build`, e.g. `weld -Wruff "--ignore=F401"`.
  - `-Eruff "<opt>"` — remove a saved Ruff option (defaults such as
    `--no-cache` can be removed too); warns when the option is not found.
  - All four flags are **repeatable**, accept several space-separated
    options in one quoted string (split shell-style), and support both
    `-Wruff "--ignore=F401"` and `-Wruff=--ignore=F401` spellings.
  - The flags can be used on their own (they just update the config and
    show the resulting option list) **or combined** with `--build`,
    `--install` or `--update`, in which case they apply to that run
    immediately, e.g. `weld --build myapp -Wruff "--ignore=F401"`.
  - Adding an option that is already saved prints a warning instead of
    storing a duplicate.
- New `~/.weld/config.toml` keys backing the feature (also editable by
  hand; a single shell-style string is accepted instead of a list):
  ```toml
  [weld]
  nuitka_opts = []
  ruff_opts = ["--no-cache", "--output-format=concise"]
  ```
- `--build` and `--install` now print the effective Ruff / Nuitka options
  they are about to use; `--doctor` and `--stats` also list them.
- Zsh completions (`--completions zsh`) now include the four new flags.

### Changed

- Version bumped from `0.3.0` to **`0.4.0`** (`VERSION` in `weld.py` and
  the repo `version` file are kept in sync).
- Ruff is still invoked as `ruff check --no-cache --output-format=concise`
  by default, but those defaults now come from the saved `ruff_opts`
  setting instead of being hardcoded, so they can be changed or removed.

---

## [0.3.0] and earlier

Releases up to `0.3.0` predate this changelog; see the Git history and
GitHub releases for details.
