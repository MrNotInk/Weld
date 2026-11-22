# Templates

Templates replace your project's entry script and can add pip dependencies to
`config.json`. They are a starting point, not a framework.

```bash
weld --templates             # list built-in + user templates
weld --template cli          # run inside a project directory
```

`--template` must run in a directory containing `config.json` and `bin/`. It:

1. backs up the current entry script to `bin/<entry>.<timestamp>.bak`
   (`.bak` files are excluded from archives),
2. writes the template code to the entry script (`bin/main.py`, or the file
   named by `entry` in `config.json`),
3. merges the template's `deps` into `config.json` (existing deps are kept).

---

## Built-in templates

| Name | Description | pip deps |
|---|---|---|
| `hello` | Minimal hello-world | – |
| `cli` | argparse CLI application | – |
| `flask` | Flask REST API server | `flask` |
| `fastapi` | FastAPI + uvicorn async API | `fastapi`, `uvicorn` |
| `daemon` | Background daemon with SIGTERM handling | – |
| `scraper` | Web scraper with requests + BeautifulSoup | `requests`, `beautifulsoup4` |
| `tui` | Rich terminal UI with tables and live display | `rich` |
| `bot` | Telegram bot with command handlers | `python-telegram-bot` |
| `cron` | Scheduled recurring tasks with `schedule` | `schedule` |
| `tcp_server` | TCP socket server with socketserver | – |
| `grpc` | gRPC server stub (requires generated proto code) | `grpcio`, `grpcio-tools` |
| `data_pipeline` | ETL extract / transform / load pipeline | – |
| `rest_client` | HTTP REST API client with session | `requests` |
| `config_app` | Config-file driven app reading `config.toml` | `tomli-w` |
| `plugin` | weld plugin hook stubs (used for `plugins/__init__.py`) | – |

`weld --templates` prints the authoritative list with the current deps.

---

## User templates

Drop a `.py` file in `~/.weld/templates/<name>.py`; it appears in
`--templates` and can be applied with `--template <name>`.

```python
# ~/.weld/templates/quickstart.py
import sys

from rich import print

print("[bold green]quickstart[/bold green]")
sys.exit(0)
```

- The file's **code becomes the entry script verbatim** — it is the script, not
  a template definition.
- User templates are listed as `User-defined template` with no pip deps; add
  dependencies by editing `config.json` yourself (built-in templates merge
  their own deps automatically).
- Template names are validated (`A-Z, a-z, 0-9, _, -` only) and path traversal
  such as `--template ../../evil` is rejected.
- Removing the file removes the template; nothing is cached.

## Choosing a template vs writing your own

- Use `--new` for the smallest valid project, then paste real code into
  `bin/main.py`.
- Use a built-in template when you want a working example of a specific shape
  (CLI, server, TUI, ...).
- Use a user template when several of your apps should share the same
  boilerplate.
- Apply a template **before** writing real code: it overwrites the entry script
  (the `.bak` copy is your safety net).
