# Weld documentation

Weld builds, installs and manages Python CLI apps from `.wld` archives
(a `.wld` is a zip containing `config.json` and a `bin/` folder).

| Document | Contents |
|---|---|
| [project.md](project.md) | How to lay out a project by hand: `config.json`, `bin/`, `bin/libs/`, Readme/Changelog, building the archive, common mistakes. |
| [weld.md](weld.md) | How to use Weld: every command, build/install/run flow, registry installs, `~/.weld` state, error logs, self-upgrade. |
| [config.md](config.md) | Complete option reference for `config.json`, `~/.weld/config.toml`, the registry `list.json`, and the `packages.json` database. |
| [plugins.md](plugins.md) | Hook reference (`before_build` … `after_remove`), project vs global plugins, examples. |
| [templates.md](templates.md) | Built-in templates, writing your own, what `--template` changes. |

Start here if you are new:

```bash
weld --new myapp
weld --template cli
weld --build .
weld --install ../myapp.wld
weld --run myapp --help
```

The main [README](../README.md) is the project overview; each release's changes
are in [CHANGELOG.md](../CHANGELOG.md).
