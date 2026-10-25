#!/usr/bin/env python3
import argparse
import copy
import fnmatch
import glob
import importlib.util
import json
import os
import py_compile
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime

from rich.box import ROUNDED
from rich.console import Console
from rich.markup import escape as _esc
from rich.table import Table

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        sys.exit("Error: 'tomllib' (Python 3.11+) or 'tomli' package required.")

try:
    import tomli_w
except ImportError:
    sys.exit("Error: 'tomli-w' package required.  pip install tomli-w")

console = Console()

VERSION = "0.1.0"

WELD_DIR       = os.path.expanduser("~/.weld")
WELD_BIN       = os.path.join(WELD_DIR, "bin")
WELD_LIBS      = os.path.join(WELD_BIN, "libs")
WELD_PACKAGES  = os.path.join(WELD_DIR, "packages.json")
WELD_LOCK      = WELD_PACKAGES + ".lock"
WELD_CONFIG    = os.path.join(WELD_DIR, "config.toml")
WELD_README    = os.path.join(WELD_DIR, "Readme")
WELD_HISTORY   = os.path.join(WELD_DIR, "history.log")
WELD_PLUGINS   = os.path.join(WELD_DIR, "plugins")
WELD_TEMPLATES = os.path.join(WELD_DIR, "templates")

# Self-upgrade locations
WELD_REPO           = "MrNotInk/Weld"
WELD_GIT_URL        = f"https://github.com/{WELD_REPO}.git"
WELD_RELEASE_TAG    = "Python3"  # fallback tag; latest tag is resolved via API first
RELEASE_VERSION_URL = f"https://github.com/{WELD_REPO}/releases/download/{WELD_RELEASE_TAG}/version"
RELEASE_WELD_URL    = f"https://github.com/{WELD_REPO}/releases/download/{WELD_RELEASE_TAG}/weld"
RELEASE_API_URL     = f"https://api.github.com/repos/{WELD_REPO}/releases/latest"
LOCAL_BIN_DIR       = os.path.expanduser("~/.local/bin")
LOCAL_BIN_WELD      = os.path.join(LOCAL_BIN_DIR, "weld")

WLD_EXT = ".wld"

_WELD_DIR_KNOWN = {
    "bin", "Readme", "packages.json", "packages.json.lock",
    "config.toml", "_complete_packages.py", "history.log",
    "plugins", "templates",
}

DEFAULT_CONFIG = {
    "weld": {
        "compiler":         "nuitka",
        "libs_compiler":    "cython",
        "default_version":  "1.0.0",
        "auto_pip_install": True,
    }
}

# Directory names that are never archived (VCS data, caches, virtualenvs, ...)
_ARCHIVE_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    ".venv", "venv", "env", "ENV",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "node_modules", "build", "dist", "_pyibuild",
}

# File suffixes that are never archived (build leftovers, backups, ...)
_ARCHIVE_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".bak", ".spec", ".wld")

# Files created by tools that must never be copied into ~/.weld/bin
_INSTALL_JUNK_SUFFIXES = (".pyc", ".pyo", ".bak", ".spec", ".c", ".build", ".dist")
_INSTALL_JUNK_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db"}

# Package names that would collide with weld's own layout inside ~/.weld/bin
_RESERVED_PKG_NAMES = {"libs"}

_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_ARCHIVE_BYTES = 1 << 30  # 1 GiB uncompressed

_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


# ---------------------------------------------------------------------------
# Package-name validation
# ---------------------------------------------------------------------------

def _validate_pkg_name(name: str) -> None:
    """Raise SystemExit if *name* is empty or contains unsafe characters."""
    if not isinstance(name, str) or not _PKG_NAME_RE.match(name):
        console.print(
            f"[red]Error:[/red] Invalid package name '{_esc(name)}'. "
            "Only letters, digits, hyphens and underscores are allowed, "
            "and the name must start with a letter or digit."
        )
        raise SystemExit(1)
    if name.lower() in _RESERVED_PKG_NAMES:
        console.print(
            f"[red]Error:[/red] The package name '{_esc(name)}' is reserved by weld."
        )
        raise SystemExit(1)


def _validate_template_name(name: str) -> None:
    if not isinstance(name, str) or not _TEMPLATE_NAME_RE.match(name):
        console.print(
            f"[red]Error:[/red] Invalid template name '{_esc(name)}'. "
            "Only letters, digits, hyphens and underscores are allowed."
        )
        raise SystemExit(1)


def _is_junk_name(name: str) -> bool:
    return name in _INSTALL_JUNK_NAMES or name.endswith(_INSTALL_JUNK_SUFFIXES)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, dict] = {}

TEMPLATES["hello"] = {
    "desc": "Minimal hello-world",
    "deps": [],
    "code": '''\
"""
Minimal hello-world template.
"""
import sys


def main() -> int:
    print("Hello, world!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["cli"] = {
    "desc": "argparse CLI application",
    "deps": [],
    "code": '''\
"""
CLI application template.
Uses argparse for argument parsing.
"""
import argparse
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="myapp",
        description="A CLI application",
    )
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("input", nargs="?", help="Input value")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.version:
        print("myapp 1.0.0")
        return 0

    if args.input:
        print(f"Processing: {args.input}")
    else:
        print("No input provided. Use --help for usage.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["flask"] = {
    "desc": "Flask REST API server",
    "deps": ["flask"],
    "code": '''\
"""
Flask web application template.
"""
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/")
def index():
    return jsonify({"message": "Hello from Flask!", "status": "ok"})


@app.route("/echo", methods=["POST"])
def echo():
    data = request.get_json(force=True)
    return jsonify({"echo": data})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
'''}

TEMPLATES["fastapi"] = {
    "desc": "FastAPI + uvicorn async API",
    "deps": ["fastapi", "uvicorn"],
    "code": '''\
"""
FastAPI application template.
"""
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="My API", version="1.0.0")


class Item(BaseModel):
    name: str
    value: str


@app.get("/")
def root():
    return {"message": "Hello from FastAPI!"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/items")
def create_item(item: Item):
    return {"created": item.model_dump()}


@app.get("/items/{item_id}")
def get_item(item_id: int):
    return {"item_id": item_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
'''}

TEMPLATES["daemon"] = {
    "desc": "Background daemon with SIGTERM handling",
    "deps": [],
    "code": '''\
"""
Background daemon / service template.
"""
import logging
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

_running = True


def _stop(signum, frame):
    global _running
    log.info("Received signal %s, shutting down...", signum)
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT,  _stop)


def tick() -> None:
    log.info("tick")


def main() -> int:
    log.info("Daemon started")
    while _running:
        tick()
        time.sleep(5)
    log.info("Daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["scraper"] = {
    "desc": "Web scraper with requests + BeautifulSoup",
    "deps": ["requests", "beautifulsoup4"],
    "code": '''\
"""
Web scraper template.
"""
import sys

import requests
from bs4 import BeautifulSoup

TARGET_URL = "https://example.com"


def fetch(url: str) -> str:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text


def parse(html: str) -> list[dict]:
    soup   = BeautifulSoup(html, "html.parser")
    result = []
    for tag in soup.find_all("a", href=True):
        result.append({"text": tag.get_text(strip=True), "href": tag["href"]})
    return result


def main() -> int:
    url   = sys.argv[1] if len(sys.argv) > 1 else TARGET_URL
    html  = fetch(url)
    items = parse(html)
    for item in items:
        print(f"{item['text']!r:40}  {item['href']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["tui"] = {
    "desc": "Rich terminal UI with tables and live display",
    "deps": ["rich"],
    "code": '''\
"""
Terminal UI template.
"""
import sys
import time

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()


def build_table(data: list[dict]) -> Table:
    table = Table(title="Data", show_header=True, header_style="bold cyan")
    if not data:
        return table
    for key in data[0]:
        table.add_column(key.capitalize())
    for row in data:
        table.add_row(*[str(v) for v in row.values()])
    return table


def main() -> int:
    console.print(Panel("[bold cyan]TUI Application[/bold cyan]", expand=False))

    sample_data = [
        {"name": "Alice", "role": "admin",  "active": "yes"},
        {"name": "Bob",   "role": "user",   "active": "yes"},
        {"name": "Carol", "role": "viewer", "active": "no"},
    ]
    console.print(build_table(sample_data))

    choice = Prompt.ask("\\nChoose action", choices=["run", "quit"], default="quit")
    if choice == "run":
        with Live(console=console, refresh_per_second=10) as live:
            for i in range(20):
                live.update(f"[cyan]Running step {i+1}/20...[/cyan]")
                time.sleep(0.1)
        console.print("[green]Done![/green]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["bot"] = {
    "desc": "Telegram bot with command handlers",
    "deps": ["python-telegram-bot"],
    "code": '''\
"""
Telegram bot template.
"""
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = "YOUR_BOT_TOKEN_HERE"


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hello! I am your bot.")


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("/start - Start\\n/help - Help")


async def echo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(update.message.text)


def main() -> int:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    app.run_polling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["cron"] = {
    "desc": "Scheduled recurring tasks with schedule",
    "deps": ["schedule"],
    "code": '''\
"""
Scheduled task / cron template.
"""
import logging
import sys
import time

import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)


def every_minute() -> None:
    log.info("Running every-minute task")


def every_hour() -> None:
    log.info("Running every-hour task")


def daily_at_midnight() -> None:
    log.info("Running daily midnight task")


def main() -> int:
    schedule.every(1).minutes.do(every_minute)
    schedule.every(1).hours.do(every_hour)
    schedule.every().day.at("00:00").do(daily_at_midnight)
    log.info("Scheduler started")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["tcp_server"] = {
    "desc": "TCP socket server with socketserver",
    "deps": [],
    "code": '''\
"""
TCP socket server template.
"""
import logging
import socketserver
import sys

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 9999


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        log.info("Connection from %s:%s", *self.client_address)
        data = self.request.recv(1024).strip()
        log.info("Received: %r", data)
        self.request.sendall(b"ACK: " + data + b"\\n")


def main() -> int:
    with socketserver.TCPServer((HOST, PORT), Handler) as server:
        log.info("Listening on %s:%s", HOST, PORT)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["grpc"] = {
    "desc": "gRPC server stub (requires generated proto code)",
    "deps": ["grpcio", "grpcio-tools"],
    "code": '''\
"""
gRPC server template.
"""
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import grpc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def serve(port: int = 50051) -> None:
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("gRPC server started on port %s", port)
    server.wait_for_termination()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["data_pipeline"] = {
    "desc": "ETL extract / transform / load pipeline",
    "deps": [],
    "code": '''\
"""
Data pipeline template.
"""
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def extract(source: str) -> list[dict]:
    log.info("Extracting from %s", source)
    return [{"id": 1, "value": "foo"}, {"id": 2, "value": "bar"}]


def transform(records: list[dict]) -> list[dict]:
    log.info("Transforming %d records", len(records))
    return [{**r, "value": r["value"].upper(), "processed": True} for r in records]


def load(records: list[dict], dest: str) -> None:
    log.info("Loading %d records to %s", len(records), dest)
    Path(dest).write_text(json.dumps(records, indent=2))


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else "input.json"
    dest   = sys.argv[2] if len(sys.argv) > 2 else "output.json"
    load(transform(extract(source)), dest)
    log.info("Pipeline complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["rest_client"] = {
    "desc": "HTTP REST API client with session",
    "deps": ["requests"],
    "code": '''\
"""
REST API client template.
"""
import json
import sys

import requests

BASE_URL = "https://jsonplaceholder.typicode.com"


class APIClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session  = requests.Session()

    def get(self, path: str, **params) -> dict:
        r = self.session.get(
            f"{self.base_url}/{path.lstrip('/')}",
            params=params, timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def post(self, path: str, data: dict) -> dict:
        r = self.session.post(
            f"{self.base_url}/{path.lstrip('/')}",
            json=data, timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def delete(self, path: str) -> int:
        r = self.session.delete(
            f"{self.base_url}/{path.lstrip('/')}",
            timeout=10,
        )
        r.raise_for_status()
        return r.status_code


def main() -> int:
    client = APIClient(BASE_URL)
    posts  = client.get("/posts", _limit=3)
    print(json.dumps(posts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["config_app"] = {
    "desc": "Config-file driven app reading config.toml",
    "deps": ["tomli-w"],
    "code": '''\
"""
Config-file driven application template.
"""
import os
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path

DEFAULT_CONFIG = {
    "app": {"name": "myapp", "debug": False, "workers": 4}
}

CONFIG_PATH = Path(os.path.dirname(os.path.abspath(__file__))) / "config.toml"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as fp:
            return tomllib.load(fp)
    return DEFAULT_CONFIG


def main() -> int:
    cfg = load_config()
    app = cfg.get("app", {})
    print(f"App:     {app.get('name', 'unknown')}")
    print(f"Debug:   {app.get('debug', False)}")
    print(f"Workers: {app.get('workers', 1)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''}

TEMPLATES["plugin"] = {
    "desc": "weld plugin hook stubs",
    "deps": [],
    "code": '''\
"""
weld plugin hook template.
"""


def before_build(config: dict) -> None:
    pass


def after_build(config: dict, archive_path: str) -> None:
    pass


def before_install(config: dict) -> None:
    pass


def after_install(config: dict) -> None:
    pass


def before_remove(pkg_name: str) -> None:
    pass


def after_remove(pkg_name: str) -> None:
    pass


def before_update(config: dict) -> None:
    pass


def after_update(config: dict) -> None:
    pass
'''}

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_GREEN       = "\033[32m"
_RED         = "\033[31m"
_CYAN        = "\033[36m"
_YELLOW      = "\033[33m"
_BOLD        = "\033[1m"
_DIM         = "\033[2m"
_RESET       = "\033[0m"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_CLEAR_LINE  = "\033[2K"


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _progress(current: int, total: int, label: str) -> None:
    bar_width = 20
    filled    = int(bar_width * current / total) if total else 0
    bar       = "█" * filled + "░" * (bar_width - filled)
    _write(f"\r{_CLEAR_LINE}{_DIM}[{current}/{total}]{_RESET} {_CYAN}{bar}{_RESET}  {label}")


def _progress_done() -> None:
    _write(f"\r{_CLEAR_LINE}")


# ---------------------------------------------------------------------------
# Filesystem / archive helpers
# ---------------------------------------------------------------------------

def _atomic_write(path: str, data: str | bytes) -> None:
    """Write *data* to *path* atomically (temp file + rename)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        if isinstance(data, bytes):
            with open(tmp, "wb") as fp:
                fp.write(data)
                fp.flush()
                os.fsync(fp.fileno())
        else:
            with open(tmp, "w", encoding="utf-8") as fp:
                fp.write(data)
                fp.flush()
                os.fsync(fp.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_json(path: str, data: dict) -> None:
    _atomic_write(path, json.dumps(data, indent=4) + "\n")


def _safe_join(base: str, relative: str) -> str | None:
    """Join *relative* onto *base*, returning None if it escapes *base*."""
    if not isinstance(relative, str) or not relative:
        return None
    base_abs = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base_abs, relative))
    try:
        if os.path.commonpath([base_abs, target]) != base_abs:
            return None
    except ValueError:  # different drives on Windows
        return None
    return target


def _prune_empty_dirs(base: str, relative: str) -> None:
    """Remove now-empty parent directories of *relative* below *base*."""
    base_abs = os.path.abspath(base)
    parent = os.path.dirname(os.path.abspath(os.path.join(base_abs, relative)))
    while parent != base_abs and parent.startswith(base_abs + os.sep):
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)


def _require_tool(tool: str, hint: str = "") -> str:
    path = shutil.which(tool)
    if not path:
        console.print(f"[red]Error:[/red] Required tool '{_esc(tool)}' not found on PATH.")
        if hint:
            _print(f"{_DIM}{hint}{_RESET}")
        raise SystemExit(1)
    return path


def _normalize_config(raw: object, source: str) -> dict:
    """Validate a project/archive config dict and fill in defaults."""
    if not isinstance(raw, dict):
        console.print(f"[red]Error:[/red] {_esc(source)} must contain a JSON object")
        raise SystemExit(1)

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        console.print(f"[red]Error:[/red] Missing or invalid 'name' in {_esc(source)}")
        raise SystemExit(1)

    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, (str, int, float)) or str(version).strip() == "":
        console.print(
            f"[red]Error:[/red] Missing or invalid 'version' in {_esc(source)} "
            "(expected a non-empty string)"
        )
        raise SystemExit(1)

    deps = raw.get("deps", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) and d.strip() for d in deps):
        console.print(
            f"[red]Error:[/red] 'deps' in {_esc(source)} must be a list of package names"
        )
        raise SystemExit(1)

    desc = raw.get("desc", "")
    if not isinstance(desc, str):
        console.print(f"[red]Error:[/red] 'desc' in {_esc(source)} must be a string")
        raise SystemExit(1)

    config = dict(raw)
    config.update(name=name.strip(), version=str(version).strip(), deps=list(deps), desc=desc)
    return config


def _read_project_config(directory: str) -> dict:
    path = os.path.join(directory, "config.json")
    if not os.path.isfile(path):
        console.print("[red]Error:[/red] config.json not found")
        raise SystemExit(1)
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Error:[/red] Invalid JSON in config.json: {_esc(str(exc))}")
        raise SystemExit(1)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not read config.json: {_esc(str(exc))}")
        raise SystemExit(1)
    return _normalize_config(raw, "config.json")


def _read_archive_config(wld_file: str) -> dict:
    """Read and validate config.json from a .wld archive."""
    try:
        with zipfile.ZipFile(wld_file) as zf:
            try:
                raw_bytes = zf.read("config.json")
            except KeyError:
                console.print("[red]Error:[/red] config.json missing from archive")
                raise SystemExit(1)
    except zipfile.BadZipFile:
        console.print(
            f"[red]Error:[/red] '{_esc(os.path.basename(wld_file))}' is not a valid "
            f"{WLD_EXT} archive"
        )
        raise SystemExit(1)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not read archive: {_esc(str(exc))}")
        raise SystemExit(1)

    try:
        raw = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        console.print(f"[red]Error:[/red] Invalid config.json in archive: {_esc(str(exc))}")
        raise SystemExit(1)
    return _normalize_config(raw, "archive config.json")


def _create_wld_archive(directory: str, archive_path: str) -> None:
    """Create *archive_path* from *directory*, skipping junk and cache files."""
    parent = os.path.dirname(os.path.abspath(archive_path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".weld-build-", suffix=WLD_EXT, dir=parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(directory):
                dirs[:] = sorted(
                    d for d in dirs
                    if d not in _ARCHIVE_EXCLUDE_DIRS
                    and not os.path.islink(os.path.join(root, d))
                )
                for filename in sorted(files):
                    if filename.endswith(_ARCHIVE_EXCLUDE_SUFFIXES):
                        continue
                    full = os.path.join(root, filename)
                    if os.path.islink(full) or not os.path.isfile(full):
                        continue
                    rel = os.path.relpath(full, directory).replace(os.sep, "/")
                    zf.write(full, rel)
        os.replace(tmp_path, archive_path)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not create archive: {_esc(str(exc))}")
        raise SystemExit(1)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _extract_wld_archive(wld_file: str, dest: str) -> None:
    """Safely extract a .wld archive into *dest*."""
    dest_abs = os.path.abspath(dest)
    os.makedirs(dest_abs, exist_ok=True)
    try:
        with zipfile.ZipFile(wld_file) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ARCHIVE_ENTRIES:
                console.print(
                    f"[red]Error:[/red] Archive contains too many entries "
                    f"({len(infos)} > {_MAX_ARCHIVE_ENTRIES})"
                )
                raise SystemExit(1)

            total_size = 0
            for info in infos:
                raw_name = info.filename
                name = raw_name.replace("\\", "/")
                norm = os.path.normpath(name)
                unsafe = (
                    not name
                    or name.startswith("/")
                    or re.match(r"^[A-Za-z]:", name) is not None
                    or norm == ".."
                    or norm.startswith("../")
                    or os.path.isabs(norm)
                )
                if unsafe:
                    console.print(
                        f"[red]Error:[/red] Unsafe path in archive: '{_esc(raw_name)}'"
                    )
                    raise SystemExit(1)

                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    console.print(
                        f"[red]Error:[/red] Archive contains a symbolic link: "
                        f"'{_esc(raw_name)}'"
                    )
                    raise SystemExit(1)

                total_size += info.file_size
                if total_size > _MAX_ARCHIVE_BYTES:
                    console.print(
                        "[red]Error:[/red] Archive expands to more than "
                        f"{_MAX_ARCHIVE_BYTES // (1 << 20)} MiB; refusing to extract"
                    )
                    raise SystemExit(1)

                target = os.path.join(dest_abs, norm)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if mode & 0o777:
                    os.chmod(target, mode & 0o777)
    except zipfile.BadZipFile:
        console.print(f"[red]Error:[/red] '{_esc(os.path.basename(wld_file))}' is not a valid archive")
        raise SystemExit(1)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Failed to extract archive: {_esc(str(exc))}")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


@contextmanager
def spinner(message: str):
    stop_event = threading.Event()

    def _spin():
        idx = 0
        while not stop_event.is_set():
            frame = SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]
            _write(f"\r{_CLEAR_LINE}{_CYAN}{frame}{_RESET} {message}")
            idx += 1
            time.sleep(0.08)

    thread = threading.Thread(target=_spin, daemon=True)
    _write(_HIDE_CURSOR)
    thread.start()

    ok = True
    try:
        yield
    except BaseException:
        ok = False
        raise
    finally:
        stop_event.set()
        thread.join()
        _write(_SHOW_CURSOR)
        if ok:
            _write(f"\r{_CLEAR_LINE}{_GREEN}✔{_RESET} {message}\n")
        else:
            _write(f"\r{_CLEAR_LINE}{_RED}✘{_RESET} {message}\n")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def _log(action: str, pkg_name: str, extra: str = "") -> None:
    ts = now_iso()

    # Tab-separated; strip separators so user-provided text cannot corrupt it.
    def clean(value: str) -> str:
        return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")

    line = f"{ts}\t{clean(action)}\t{clean(pkg_name)}"
    if extra:
        line += f"\t{clean(extra)}"
    try:
        os.makedirs(WELD_DIR, exist_ok=True)
        with open(WELD_HISTORY, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except OSError:
        # History is best-effort; never fail an operation because of it.
        pass


# ---------------------------------------------------------------------------
# Plugin system
# ---------------------------------------------------------------------------

def _load_plugin(pkg_dir: str) -> object | None:
    init_py = os.path.join(pkg_dir, "plugins", "__init__.py")
    if not os.path.isfile(init_py):
        return None

    # Warn when loading a plugin that is NOT the global ~/.weld plugin
    if os.path.abspath(pkg_dir) != os.path.abspath(WELD_DIR):
        _print(
            f"{_YELLOW}Warning:{_RESET} Loading plugin from untrusted path: "
            f"{init_py}"
        )

    spec   = importlib.util.spec_from_file_location("weld_plugin", init_py)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - user plugin code
        _print(f"{_YELLOW}Warning:{_RESET} Plugin failed to load: {exc}")
        return None
    return module


def _load_global_plugin() -> object | None:
    return _load_plugin(WELD_DIR)


def _call_hook(module: object | None, hook: str, *args) -> None:
    if module is None:
        return
    fn = getattr(module, hook, None)
    if callable(fn):
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - user plugin code
            _print(f"{_YELLOW}Warning:{_RESET} Plugin hook '{hook}' raised: {exc}")


# ---------------------------------------------------------------------------
# Bootstrap & config
# ---------------------------------------------------------------------------

def bootstrap() -> None:
    for d in (WELD_DIR, WELD_BIN, WELD_LIBS,
              WELD_README, WELD_PLUGINS, WELD_TEMPLATES):
        os.makedirs(d, exist_ok=True)

    plugin_init = os.path.join(WELD_PLUGINS, "__init__.py")
    if not os.path.isfile(plugin_init):
        _atomic_write(plugin_init, TEMPLATES["plugin"]["code"])

    for name, tpl in TEMPLATES.items():
        path = os.path.join(WELD_TEMPLATES, f"{name}.py")
        if not os.path.isfile(path):
            _atomic_write(path, tpl["code"])

    if not os.path.isfile(WELD_PACKAGES):
        _write_json(WELD_PACKAGES, {})

    if not os.path.isfile(WELD_CONFIG):
        _atomic_write(WELD_CONFIG, tomli_w.dumps(DEFAULT_CONFIG))


def load_config() -> dict:
    if not os.path.isfile(WELD_CONFIG):
        try:
            _atomic_write(WELD_CONFIG, tomli_w.dumps(DEFAULT_CONFIG))
        except OSError as exc:
            console.print(f"[red]Error:[/red] Could not create {_esc(WELD_CONFIG)}: {_esc(str(exc))}")
            raise SystemExit(1)
    try:
        with open(WELD_CONFIG, "rb") as fp:
            config = tomllib.load(fp)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        console.print(f"[red]Error:[/red] Invalid config file {_esc(WELD_CONFIG)}: {_esc(str(exc))}")
        raise SystemExit(1)
    if not isinstance(config, dict):
        console.print(f"[red]Error:[/red] Invalid config file {_esc(WELD_CONFIG)}")
        raise SystemExit(1)
    return config


def _weld_settings() -> dict:
    settings = load_config().get("weld", {})
    return settings if isinstance(settings, dict) else {}


def get_compiler() -> str:
    val = str(_weld_settings().get("compiler", "nuitka")).lower()
    if val not in ("nuitka", "pyinstaller"):
        _print(f"{_YELLOW}Warning:{_RESET} Unknown compiler '{val}', falling back to nuitka")
        return "nuitka"
    return val


def get_libs_compiler() -> str:
    val = str(_weld_settings().get("libs_compiler", "cython")).lower()
    if val != "cython":
        _print(f"{_YELLOW}Warning:{_RESET} Unknown libs_compiler '{val}', falling back to cython")
        return "cython"
    return val


def get_auto_pip_install() -> bool:
    return bool(_weld_settings().get("auto_pip_install", True))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def filter_files(directory: str, extension: str) -> list[str]:
    """Return sorted list of filenames in *directory* ending with *extension*."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        e for e in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, e)) and e.endswith(extension)
    )


def filter_dirs(directory: str, extension: str) -> list[str]:
    """Return sorted list of directory names in *directory* ending with *extension*."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        e for e in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, e)) and e.endswith(extension)
    )


# ---------------------------------------------------------------------------
# packages.json locking
# ---------------------------------------------------------------------------

@contextmanager
def _packages_lock():
    """Exclusive file lock around packages.json so concurrent runs are safe."""
    os.makedirs(WELD_DIR, exist_ok=True)
    with open(WELD_LOCK, "a+", encoding="utf-8") as lf:
        if fcntl is not None:
            fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lf, fcntl.LOCK_UN)


def load_packages() -> dict:
    if not os.path.isfile(WELD_PACKAGES):
        return {}
    try:
        with open(WELD_PACKAGES, encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        console.print(
            f"[red]Error:[/red] Corrupt package database {_esc(WELD_PACKAGES)}: {_esc(str(exc))}\n"
            f"[dim]Fix the JSON or delete the file to start fresh.[/dim]"
        )
        raise SystemExit(1)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not read {_esc(WELD_PACKAGES)}: {_esc(str(exc))}")
        raise SystemExit(1)
    if not isinstance(data, dict):
        console.print(
            f"[red]Error:[/red] Corrupt package database {_esc(WELD_PACKAGES)}: "
            "expected a JSON object"
        )
        raise SystemExit(1)
    return data


def save_packages(data: dict) -> None:
    try:
        _write_json(WELD_PACKAGES, data)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not write {_esc(WELD_PACKAGES)}: {_esc(str(exc))}")
        raise SystemExit(1)


def ruff_check(path: str, cwd: str | None = None) -> tuple[bool, str]:
    """Run ruff on *path*; returns (ok, combined output)."""
    result = subprocess.run(
        ["ruff", "check", "--no-cache", "--output-format=concise", path],
        cwd=cwd, capture_output=True, text=True, check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output


def _has_uv() -> bool:
    return shutil.which("uv") is not None


def pip_install(package: str) -> str:
    """Install *package* via pip; returns 'uv' or 'pip' to indicate which was used.
    Raises RuntimeError with tool output on failure."""
    if _has_uv():
        result = subprocess.run(
            ["uv", "pip", "install", package],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            return "uv"
        # uv can fail outside a virtualenv; let pip have a try before giving up.
        uv_error = (result.stderr or result.stdout or "").strip()
    else:
        uv_error = ""

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if uv_error:
            details = f"uv:\n{uv_error}\n\npip:\n{details}"
        raise RuntimeError(f"pip install {package} failed:\n{details}")
    return "pip"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def readme_path_for(pkg_name: str) -> str:
    return os.path.join(WELD_README, f"{pkg_name}-Readme")


def changelog_path_for(pkg_name: str) -> str:
    return os.path.join(WELD_README, f"{pkg_name}-Changelog")


# ---------------------------------------------------------------------------
# Readme / Changelog helpers
# ---------------------------------------------------------------------------

def store_readme(pkg_name: str, extracted_dir: str) -> bool:
    src = os.path.join(extracted_dir, "Readme")
    if not os.path.isfile(src):
        return False
    shutil.copy2(src, readme_path_for(pkg_name))
    _print(f"{_DIM}Readme stored  ->  {readme_path_for(pkg_name)}{_RESET}")
    return True


def store_changelog(pkg_name: str, extracted_dir: str) -> bool:
    src = os.path.join(extracted_dir, "Changelog")
    if not os.path.isfile(src):
        return False
    shutil.copy2(src, changelog_path_for(pkg_name))
    _print(f"{_DIM}Changelog stored  ->  {changelog_path_for(pkg_name)}{_RESET}")
    return True


def read_readme(pkg_name: str) -> str | None:
    path = readme_path_for(pkg_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fp:
            return fp.read().strip()
    except OSError:
        return None


def remove_readme(pkg_name: str, silent: bool = False) -> None:
    path = readme_path_for(pkg_name)
    if os.path.isfile(path):
        os.remove(path)
        if not silent:
            _print(f"{_YELLOW}Removed:{_RESET} {path}")


def remove_changelog(pkg_name: str, silent: bool = False) -> None:
    path = changelog_path_for(pkg_name)
    if os.path.isfile(path):
        os.remove(path)
        if not silent:
            _print(f"{_YELLOW}Removed:{_RESET} {path}")


# ---------------------------------------------------------------------------
# Compilers
# ---------------------------------------------------------------------------

def compile_py_nuitka(
    full_path: str, cwd: str,
    line_cb = None,
) -> subprocess.CompletedProcess:
    """
    Compile *full_path* with Nuitka.
    *full_path* must be an absolute path to the .py file.
    If *line_cb* is provided, each stderr line (stripped) is passed to it live.
    """
    if not os.path.isabs(full_path):
        full_path = os.path.join(cwd, full_path)

    proc = subprocess.Popen(
        ["nuitka", full_path],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    stderr_chunks: list[str] = []
    for line in proc.stderr:
        stderr_chunks.append(line)
        if line_cb:
            line_cb(line.rstrip("\n\r"))

    proc.wait()
    return subprocess.CompletedProcess(
        proc.args, proc.returncode,
        stdout="", stderr="".join(stderr_chunks),
    )


def compile_py_pyinstaller(full_path: str, cwd: str) -> subprocess.CompletedProcess:
    """
    Compile *full_path* with PyInstaller (--onefile).
    *full_path* must be an absolute path to the .py file.
    """
    if not os.path.isabs(full_path):
        full_path = os.path.join(cwd, full_path)

    result = subprocess.run(
        [
            "pyinstaller", "--onefile",
            "--distpath", cwd,
            "--workpath", os.path.join(cwd, "_pyibuild"),
            "--specpath", cwd,
            full_path,
        ],
        cwd=cwd, capture_output=True, text=True, check=False,
    )

    # Clean up PyInstaller leftovers
    base = os.path.splitext(os.path.basename(full_path))[0]
    for leftover in [
        os.path.join(cwd, base + ".spec"),
        os.path.join(cwd, "_pyibuild"),
    ]:
        if os.path.exists(leftover):
            (shutil.rmtree if os.path.isdir(leftover) else os.remove)(leftover)

    return result


def _compile_nuitka_live(full_path: str, cwd: str, label: str) -> subprocess.CompletedProcess:
    """Compile with Nuitka showing a live spinner + last stderr line, truncated to term width - 2."""
    term_w = shutil.get_terminal_size().columns
    max_vis = term_w - 2

    proc = subprocess.Popen(
        ["nuitka", full_path],
        cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )

    stderr_chunks: list[str] = []
    last_output = ""
    idx = 0
    _write(_HIDE_CURSOR)

    try:
        for line in proc.stderr:
            stderr_chunks.append(line)
            stripped = line.rstrip("\n\r")
            if stripped:
                last_output = stripped

            frame = SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]
            prefix_vis = 2 + len(label)  # frame + space + label
            avail = max(max_vis - prefix_vis - 1, 0)
            truncated = last_output[:avail]

            out = f"\r{_CLEAR_LINE}{_CYAN}{frame}{_RESET} {label}"
            if truncated:
                out += f" {_DIM}{truncated}{_RESET}"
            _write(out)
            idx += 1

        proc.wait()
    except BaseException:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        _write(_SHOW_CURSOR)

    ok = proc.returncode == 0
    icon = f"{_GREEN}✔{_RESET}" if ok else f"{_RED}✘{_RESET}"
    _write(f"\r{_CLEAR_LINE}{icon} {label}\n")

    return subprocess.CompletedProcess(
        proc.args, proc.returncode,
        stdout="", stderr="".join(stderr_chunks),
    )


def compile_to_so(python_file: str, inplace: bool = True) -> str:
    if not os.path.exists(python_file):
        raise FileNotFoundError(python_file)
    if not python_file.endswith(".py"):
        raise ValueError("File must be a .py file")

    base_name  = os.path.splitext(os.path.basename(python_file))[0]
    script_dir = os.path.dirname(os.path.abspath(python_file)) or "."

    setup_content = (
        "from setuptools import setup\n"
        "from Cython.Build import cythonize\n\n"
        "setup(\n"
        f"    ext_modules=cythonize({os.path.basename(python_file)!r},\n"
        "    compiler_directives={\"language_level\": 3})\n"
        ")\n"
    )

    # Unique file name so a user module called _setup_compile_tmp.py is
    # never overwritten.
    fd, setup_path = tempfile.mkstemp(
        prefix="_weld_setup_", suffix=".py", dir=script_dir
    )
    os.close(fd)
    with open(setup_path, "w", encoding="utf-8") as fp:
        fp.write(setup_content)

    try:
        cmd = [sys.executable, setup_path, "build_ext"]
        if inplace:
            cmd.append("--inplace")

        with spinner(f"Cython  {os.path.basename(python_file)}"):
            result = subprocess.run(
                cmd, cwd=script_dir, capture_output=True, text=True, check=False
            )

        if result.returncode != 0:
            raise RuntimeError(f"Cython build failed:\n{result.stderr}")

        pattern = os.path.join(glob.escape(script_dir), glob.escape(base_name) + ".cpython-*.so")
        so_files = sorted(glob.glob(pattern))
        if not so_files:
            raise RuntimeError("Cython .so was not generated")

        generated_so = so_files[0]
        final_so     = os.path.join(script_dir, f"{base_name}.so")

        if os.path.abspath(generated_so) != os.path.abspath(final_so):
            if os.path.exists(final_so):
                os.remove(final_so)
            os.rename(generated_so, final_so)

        c_file = os.path.join(script_dir, f"{base_name}.c")
        if os.path.exists(c_file):
            os.remove(c_file)

        return final_so

    finally:
        if os.path.exists(setup_path):
            os.remove(setup_path)


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _get_template(name: str) -> dict | None:
    if name in TEMPLATES:
        return TEMPLATES[name]
    _validate_template_name(name)
    path = os.path.join(WELD_TEMPLATES, f"{name}.py")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fp:
            code = fp.read()
        return {"code": code, "deps": [], "desc": "User-defined template"}
    return None


def _merge_deps(config_deps: list[str], new_deps: list[str]) -> tuple[list[str], list[str]]:
    added  = [d for d in new_deps if d not in config_deps]
    merged = config_deps + added
    return merged, added


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_template(template_name: str) -> None:
    if not os.path.isfile("config.json"):
        console.print("[red]Error:[/red] Not in a weld project directory (config.json not found)")
        raise SystemExit(1)

    if not os.path.isdir("bin"):
        console.print("[red]Error:[/red] bin/ directory not found in project")
        raise SystemExit(1)

    # Validate the project config *before* touching any files.
    project_config = _read_project_config(".")

    tpl = _get_template(template_name)
    if tpl is None:
        available = sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(WELD_TEMPLATES)
            if f.endswith(".py")
        )
        _print(f"{_RED}Error:{_RESET} Template '{template_name}' not found.")
        _print(f"\n{_DIM}Available templates:{_RESET}")
        for t in available:
            _print(f"  {_CYAN}•{_RESET} {t}")
        raise SystemExit(1)

    main_py = os.path.join("bin", "main.py")
    if os.path.isfile(main_py):
        # Use a timestamped backup so repeated calls don't overwrite each other.
        # Backups are excluded from .wld archives (*.bak suffix).
        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{main_py}.{ts_tag}.bak"
        shutil.copy2(main_py, backup)
        _print(f"{_DIM}Backed up bin/main.py -> {backup}{_RESET}")

    _atomic_write(main_py, tpl["code"])

    _print(f"{_GREEN}{_BOLD}✔ Template applied:{_RESET} '{template_name}' -> bin/main.py")

    existing_deps = project_config.get("deps", [])
    template_deps = [d for d in tpl.get("deps", []) if isinstance(d, str)]
    merged, added = _merge_deps(existing_deps, template_deps)

    if added:
        project_config["deps"] = merged
        _write_json("config.json", project_config)
        _print(f"{_GREEN}✔ config.json updated{_RESET}  deps added:")
        for dep in added:
            _print(f"  {_CYAN}+{_RESET} {dep}")
    else:
        if template_deps:
            _print(f"{_DIM}config.json deps already up to date ({', '.join(template_deps)}){_RESET}")
        else:
            _print(f"{_DIM}Template has no pip dependencies.{_RESET}")


def cmd_template_list() -> None:
    names: set[str] = set(TEMPLATES.keys())
    for f in os.listdir(WELD_TEMPLATES):
        if f.endswith(".py"):
            names.add(os.path.splitext(f)[0])

    table = Table(
        title="📄 Available Templates",
        box=ROUNDED, header_style="bold cyan", padding=(0, 1),
    )
    table.add_column("Template",    style="bold green", max_width=18)
    table.add_column("Description", style="white",      max_width=38)
    table.add_column("pip deps",    style="yellow",     max_width=30)

    for name in sorted(names):
        tpl  = _get_template(name)
        desc = tpl.get("desc", "-")            if tpl else "-"
        deps = ", ".join(tpl.get("deps", [])) if tpl else "-"
        table.add_row(_esc(name), _esc(desc), _esc(deps) or "[dim]-[/dim]")

    console.print(table)
    _print(f"\n{_DIM}Usage:    weld --template <name>   (run inside a weld project){_RESET}")
    _print(f"{_DIM}Add own:  {WELD_TEMPLATES}/<name>.py{_RESET}")


def cmd_build(directory: str) -> None:
    directory = os.path.abspath(directory)

    if not os.path.isdir(directory):
        console.print(f"[red]Error:[/red] Directory '{_esc(directory)}' not found")
        raise SystemExit(1)

    config = _read_project_config(directory)
    _validate_pkg_name(config["name"])

    bin_dir  = os.path.join(directory, "bin")
    main_py  = os.path.join(bin_dir, "main.py")
    libs_dir = os.path.join(bin_dir, "libs")

    if not os.path.isdir(bin_dir):
        console.print("[red]Error:[/red] bin/ directory not found")
        raise SystemExit(1)
    if not os.path.isfile(main_py):
        console.print("[red]Error:[/red] main.py not found in bin/")
        raise SystemExit(1)

    local_plugin  = _load_plugin(directory)
    global_plugin = _load_global_plugin()
    _call_hook(local_plugin,  "before_build", config)
    _call_hook(global_plugin, "before_build", config)

    if not os.path.isfile(os.path.join(directory, "Readme")):
        _print(f"{_YELLOW}Warning:{_RESET} No Readme found (optional but recommended)")
    if not os.path.isfile(os.path.join(directory, "Changelog")):
        _print(f"{_DIM}No Changelog found (optional).{_RESET}")

    _require_tool("ruff", "Install it with: pip install ruff")

    _print(f"\n{_BOLD}{_CYAN}Running validators...{_RESET}")

    bin_files  = filter_files(bin_dir,  ".py")
    libs_files = filter_files(libs_dir, ".py")
    targets = (
        [(f, f, os.path.join(bin_dir, f)) for f in bin_files]
        + [(f"libs/{f}", f"libs/{f}", os.path.join(libs_dir, f)) for f in libs_files]
    )
    total = len(targets)

    summary_rows: list[tuple[str, str, str]] = []
    try:
        for idx, (display, label, full) in enumerate(targets, 1):
            _progress(idx, total, f"validating  {label}")
            ruff_ok, output = ruff_check(full, cwd=directory)
            if not ruff_ok:
                _progress_done()
                console.print(f"[red]Error:[/red] Ruff check failed for {_esc(display)}")
                if output.strip():
                    _print(output.rstrip())
                raise SystemExit(1)

            try:
                py_compile.compile(full, doraise=True)
            except py_compile.PyCompileError as exc:
                _progress_done()
                console.print(f"[red]Error:[/red] Syntax error in {_esc(display)}")
                _print(str(exc).replace(directory + os.sep, ""))
                raise SystemExit(1)
            except OSError as exc:
                _progress_done()
                console.print(f"[red]Error:[/red] Could not compile {_esc(display)}: {_esc(str(exc))}")
                raise SystemExit(1)

            summary_rows.append((display, "✔", "✔"))
    finally:
        _progress_done()
        for d in (bin_dir, libs_dir):
            for cache in ("__pycache__", ".mypy_cache", ".ruff_cache"):
                shutil.rmtree(os.path.join(d, cache), ignore_errors=True)

    table = Table(title="Validation Summary", box=ROUNDED, header_style="bold cyan", padding=(0, 1))
    table.add_column("File",     style="bold white", max_width=30)
    table.add_column("Ruff",     style="green",      max_width=6)
    table.add_column("Bytecode", style="green",      max_width=10)
    for row in summary_rows:
        table.add_row(*(_esc(value) for value in row))
    console.print(table)

    parent       = os.path.dirname(directory)
    archive_name = os.path.basename(directory) + WLD_EXT
    archive_path = os.path.join(parent, archive_name)

    # Warn if an existing archive will be overwritten
    if os.path.exists(archive_path):
        _print(f"{_YELLOW}Warning:{_RESET} Overwriting existing archive: {archive_name}")

    with spinner(f"Wlding  {os.path.basename(directory)} -> {archive_name}"):
        _create_wld_archive(directory, archive_path)

    _call_hook(local_plugin,  "after_build", config, archive_path)
    _call_hook(global_plugin, "after_build", config, archive_path)
    _log("BUILD", config["name"], config["version"])
    console.print(f"[bold green]Wlded:[/bold green] {archive_path}")


def _copy_tree_tracked(
    src_dir: str, dst_dir: str, prefix: str, created_abs: list[str],
) -> list[str]:
    """Copy a directory tree, returning tracked relative paths and recording
    the absolute paths of newly copied files in *created_abs*."""
    tracked: list[str] = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = sorted(d for d in dirs if not _is_junk_name(d))
        rel_root = os.path.relpath(root, src_dir)
        target_root = dst_dir if rel_root == "." else os.path.join(dst_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for filename in sorted(files):
            if _is_junk_name(filename):
                continue
            target = os.path.join(target_root, filename)
            existed = os.path.exists(target)
            shutil.copy2(os.path.join(root, filename), target)
            if not existed:
                created_abs.append(target)
            rel = filename if rel_root == "." else os.path.join(rel_root, filename)
            tracked.append(os.path.join(prefix, rel) if prefix else rel)
    return tracked


def cmd_install(wld_file: str, log_action: bool = True) -> None:
    wld_file = os.path.abspath(wld_file)

    if not os.path.isfile(wld_file):
        console.print(f"[red]Error:[/red] File '{_esc(wld_file)}' not found")
        raise SystemExit(1)

    if not wld_file.endswith(WLD_EXT):
        console.print(
            f"[red]Error:[/red] File does not have a '{WLD_EXT}' extension. "
            "Please provide a valid .wld archive."
        )
        raise SystemExit(1)

    config = _read_archive_config(wld_file)
    pkg_name = config["name"]
    _validate_pkg_name(pkg_name)

    version = config["version"]
    deps    = config["deps"]
    desc    = config["desc"]

    global_plugin = _load_global_plugin()
    os.makedirs(WELD_BIN, exist_ok=True)
    os.makedirs(WELD_LIBS, exist_ok=True)

    with _packages_lock():
        datac = load_packages()
        if pkg_name in datac and datac[pkg_name].get("pinned", False):
            console.print(
                f"[red]Error:[/red] '{_esc(pkg_name)}' is pinned. "
                "Unpin with --unpin first."
            )
            raise SystemExit(1)
        old_info = copy.deepcopy(datac.get(pkg_name))

        # Files / libs owned by other packages must never be deleted or
        # considered stale when this package is upgraded.
        other_files: set[str] = set()
        other_libs: set[str] = set()
        for other_name, other_info in datac.items():
            if other_name == pkg_name:
                continue
            other_files.update(str(f) for f in other_info.get("files", []))
            other_libs.update(str(lib) for lib in other_info.get("libs", []))

    _call_hook(global_plugin, "before_install", config)

    if deps:
        if get_auto_pip_install():
            _print(f"\n{_BOLD}{_CYAN}Installing dependencies...{_RESET}")
            for idx, dep in enumerate(deps, 1):
                _progress(idx, len(deps), dep)
                try:
                    tool = "uv" if _has_uv() else "pip"
                    with spinner(f"{tool} pip install {dep}"):
                        pip_install(dep)
                except RuntimeError as exc:
                    _progress_done()
                    console.print(f"[red]Error:[/red] {_esc(str(exc))}")
                    raise SystemExit(1)
            _progress_done()
        else:
            _print(
                f"{_DIM}Skipping installation of {len(deps)} dependency(ies) "
                f"(auto_pip_install = false){_RESET}"
            )

    compiler      = get_compiler()
    libs_compiler = get_libs_compiler()

    with tempfile.TemporaryDirectory(prefix="weld-install-") as tmp:
        with spinner("Extracting wld archive"):
            _extract_wld_archive(wld_file, tmp)

        tmp_bin = os.path.join(tmp, "bin")
        if not os.path.isdir(tmp_bin):
            console.print("[red]Error:[/red] bin/ missing from archive")
            raise SystemExit(1)
        tmp_libs = os.path.join(tmp_bin, "libs")

        # Rename main.py -> <pkg>.py so compilers name the binary after the
        # package. Everything is compiled inside the temporary directory so a
        # failure can never leave half-installed files in ~/.weld.
        main_src = os.path.join(tmp_bin, "main.py")
        main_dst = os.path.join(tmp_bin, f"{pkg_name}.py")
        if os.path.isfile(main_src) and os.path.abspath(main_src) != os.path.abspath(main_dst):
            os.replace(main_src, main_dst)

        py_files   = filter_files(tmp_bin, ".py")
        libs_files = filter_files(tmp_libs, ".py") if os.path.isdir(tmp_libs) else []

        if py_files:
            if compiler == "nuitka":
                _require_tool("nuitka", "Install it with: pip install nuitka")
            else:
                _require_tool("pyinstaller", "Install it with: pip install pyinstaller")
            _print(f"\n{_BOLD}{_CYAN}Compiling with {compiler}...{_RESET}")
            try:
                for idx, py_file in enumerate(py_files, 1):
                    full = os.path.join(tmp_bin, py_file)
                    _progress(idx, len(py_files), py_file)
                    if compiler == "nuitka":
                        result = _compile_nuitka_live(full, tmp_bin, py_file)
                    else:
                        with spinner(f"{compiler}  {py_file}"):
                            result = compile_py_pyinstaller(full, tmp_bin)
                    if result.returncode != 0:
                        console.print(
                            f"[red]Error:[/red] {_esc(compiler)} failed for {_esc(py_file)}"
                        )
                        stderr = (result.stderr or "").strip()
                        if stderr:
                            _print(stderr[-4000:])
                        raise SystemExit(1)
            finally:
                _progress_done()

        # Normalise compiler output names and remove build leftovers.
        for bin_file in filter_files(tmp_bin, ".bin"):
            os.replace(
                os.path.join(tmp_bin, bin_file),
                os.path.join(tmp_bin, bin_file.removesuffix(".bin")),
            )
        for build_dir in filter_dirs(tmp_bin, ".build"):
            shutil.rmtree(os.path.join(tmp_bin, build_dir), ignore_errors=True)

        binary_names = {os.path.splitext(py_file)[0] for py_file in py_files}
        for py_file in py_files:
            path = os.path.join(tmp_bin, py_file)
            if os.path.exists(path):
                os.remove(path)
        for cache in ("__pycache__", ".mypy_cache", ".ruff_cache"):
            shutil.rmtree(os.path.join(tmp_bin, cache), ignore_errors=True)

        compiled_libs: list[str] = []
        if libs_files:
            _print(f"\n{_BOLD}{_CYAN}Compiling libs with {libs_compiler}...{_RESET}")
            if importlib.util.find_spec("Cython") is None:
                console.print(
                    "[red]Error:[/red] Cython is not installed. "
                    "Install it with: pip install cython"
                )
                raise SystemExit(1)
            if importlib.util.find_spec("setuptools") is None:
                console.print(
                    "[red]Error:[/red] setuptools is not installed. "
                    "Install it with: pip install setuptools"
                )
                raise SystemExit(1)
            try:
                for idx, py_file in enumerate(libs_files, 1):
                    full = os.path.join(tmp_libs, py_file)
                    _progress(idx, len(libs_files), py_file)
                    try:
                        so_path = compile_to_so(full)
                    except (RuntimeError, OSError, ValueError) as exc:
                        console.print(
                            f"[red]Error:[/red] Cython failed for libs/{_esc(py_file)}"
                        )
                        _print(str(exc))
                        raise SystemExit(1)
                    compiled_libs.append(os.path.basename(so_path).removesuffix(".so"))
                    if os.path.exists(full):
                        os.remove(full)
            finally:
                _progress_done()
            for cache in ("__pycache__", "build", ".mypy_cache", ".ruff_cache"):
                shutil.rmtree(os.path.join(tmp_libs, cache), ignore_errors=True)

        # Everything left in bin/ (except libs/ and junk) is an installable
        # artifact: either a compiled binary or a data file/directory.
        artifacts: list[str] = []
        for entry in sorted(os.listdir(tmp_bin)):
            if entry == "libs" or _is_junk_name(entry):
                continue
            src = os.path.join(tmp_bin, entry)
            if os.path.islink(src) or not (os.path.isfile(src) or os.path.isdir(src)):
                continue
            artifacts.append(entry)

        created_files: list[str] = []
        created_libs: list[str] = []
        installed_files: list[str] = []

        try:
            for entry in artifacts:
                src = os.path.join(tmp_bin, entry)
                dst = os.path.join(WELD_BIN, entry)
                if os.path.isdir(src):
                    installed_files.extend(
                        _copy_tree_tracked(src, dst, entry, created_files)
                    )
                else:
                    existed = os.path.exists(dst)
                    shutil.copy2(src, dst)
                    if not existed:
                        created_files.append(dst)
                    installed_files.append(entry)
                    if entry in binary_names:
                        os.chmod(dst, 0o755)

            for lib in compiled_libs:
                src = os.path.join(tmp_libs, f"{lib}.so")
                dst = os.path.join(WELD_LIBS, f"{lib}.so")
                existed = os.path.exists(dst)
                shutil.copy2(src, dst)
                if not existed:
                    created_libs.append(dst)

            has_readme    = store_readme(pkg_name, tmp)
            has_changelog = store_changelog(pkg_name, tmp)
            if not has_readme:
                remove_readme(pkg_name, silent=True)
                _print(f"{_DIM}No Readme in archive for '{pkg_name}'{_RESET}")
            if not has_changelog:
                remove_changelog(pkg_name, silent=True)

            with _packages_lock():
                datac   = load_packages()
                already = pkg_name in datac
                datac[pkg_name] = {
                    "deps":          deps,
                    "version":       version,
                    "desc":          desc,
                    "files":         sorted(installed_files),
                    "libs":          sorted(compiled_libs),
                    "has_readme":    has_readme,
                    "has_changelog": has_changelog,
                    "pinned":        datac[pkg_name].get("pinned", False) if already else False,
                    "note":          datac[pkg_name].get("note", "")    if already else "",
                    "installed_at":  datac[pkg_name].get("installed_at", now_iso()) if already else now_iso(),
                    "updated_at":    now_iso(),
                }
                save_packages(datac)
        except BaseException:
            # Roll back files that did not exist before this install. Files
            # belonging to a previously installed version are left in place.
            old_files = set(old_info.get("files", [])) if old_info else set()
            old_libs  = set(old_info.get("libs",  [])) if old_info else set()
            for path in created_files:
                rel = os.path.relpath(path, WELD_BIN)
                if rel in old_files:
                    continue
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                        _prune_empty_dirs(WELD_BIN, rel)
                except OSError:
                    pass
            for path in created_libs:
                lib = os.path.basename(path).removesuffix(".so")
                if lib in old_libs:
                    continue
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass
            raise

        # Remove artifacts that belonged to the previous version and are not
        # part of this one (unless another installed package also needs them).
        if old_info:
            for rel in old_info.get("files", []):
                if rel in other_files or rel in installed_files:
                    continue
                target = _safe_join(WELD_BIN, str(rel))
                if target and os.path.isfile(target):
                    try:
                        os.remove(target)
                        _prune_empty_dirs(WELD_BIN, str(rel))
                    except OSError:
                        pass
            for lib in old_info.get("libs", []):
                if lib in other_libs or lib in compiled_libs:
                    continue
                target = _safe_join(WELD_LIBS, f"{lib}.so")
                if target and os.path.isfile(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass

    _call_hook(global_plugin, "after_install", config)
    if log_action:
        _log("INSTALL", pkg_name, version)
    _print(f"\n{_GREEN}{_BOLD}✔ Installed:{_RESET} {pkg_name} {version}")


def cmd_update(wld_file: str) -> None:
    wld_file = os.path.abspath(wld_file)
    if not os.path.isfile(wld_file):
        console.print(f"[red]Error:[/red] File '{_esc(wld_file)}' not found")
        raise SystemExit(1)

    config   = _read_archive_config(wld_file)
    pkg_name = config["name"]
    _validate_pkg_name(pkg_name)

    with _packages_lock():
        datac = load_packages()
        old_info = copy.deepcopy(datac.get(pkg_name))
        if old_info is not None and old_info.get("pinned", False):
            _print(
                f"{_YELLOW}Skipped:{_RESET} '{pkg_name}' is pinned. "
                "Unpin with --unpin first."
            )
            return

    global_plugin = _load_global_plugin()
    _call_hook(global_plugin, "before_update", config)

    if old_info is None:
        _print(
            f"{_YELLOW}Warning:{_RESET} '{pkg_name}' not installed, "
            "running a fresh install"
        )
    else:
        _print(
            f"{_CYAN}Updating:{_RESET} {pkg_name}  "
            f"{old_info.get('version', '?')} -> {config['version']}"
        )

    # cmd_install() compiles everything in a temporary directory and only
    # touches the installed files once compilation succeeded, so a failed
    # update leaves the previous version intact and installed.
    cmd_install(wld_file, log_action=False)

    _call_hook(global_plugin, "after_update", config)
    _log("UPDATE" if old_info is not None else "INSTALL", pkg_name, config["version"])


def cmd_new(folder_name: str) -> None:
    target = os.path.abspath(folder_name)
    if os.path.exists(target):
        console.print("[red]Error:[/red] Folder already exists")
        raise SystemExit(1)

    # Use only the final path component as the package name; an absolute path
    # such as /tmp/myapp must not end up in config.json.
    pkg_name = os.path.basename(os.path.normpath(target))
    _validate_pkg_name(pkg_name)

    default_version = str(_weld_settings().get("default_version", "1.0.0"))

    try:
        os.makedirs(os.path.join(target, "bin", "libs"))
        os.makedirs(os.path.join(target, "plugins"))

        _write_json(
            os.path.join(target, "config.json"),
            {"name": pkg_name, "deps": [], "version": default_version, "desc": ""},
        )

        _atomic_write(os.path.join(target, "bin", "main.py"), TEMPLATES["hello"]["code"])

        _atomic_write(
            os.path.join(target, "Readme"),
            f"# {pkg_name}\n\n"
            "## Description\n\nDescribe your package here.\n\n"
            f"## Usage\n\n```\nweld --run {pkg_name}\n```\n",
        )

        _atomic_write(
            os.path.join(target, "Changelog"),
            f"# {pkg_name} Changelog\n\n"
            f"## {default_version}  {datetime.now().strftime('%Y-%m-%d')}\n\n"
            "- Initial release\n",
        )

        _atomic_write(
            os.path.join(target, "plugins", "__init__.py"),
            TEMPLATES["plugin"]["code"],
        )

        _print(f"{_GREEN}{_BOLD}Created:{_RESET} {folder_name}/")
        _print(f"  {_DIM}{folder_name}/config.json{_RESET}")
        _print(f"  {_DIM}{folder_name}/Readme{_RESET}")
        _print(f"  {_DIM}{folder_name}/Changelog{_RESET}")
        _print(f"  {_DIM}{folder_name}/plugins/__init__.py{_RESET}")
        _print(f"  {_DIM}{folder_name}/bin/main.py{_RESET}  (hello template)")
        _print(f"  {_DIM}{folder_name}/bin/libs/{_RESET}")
        _print(f"\n{_DIM}Tip: weld --template <name>  to apply a different template{_RESET}")

    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        console.print(f"[red]Error:[/red] {_esc(str(exc))}")
        raise SystemExit(1)


def cmd_info(pkg_name: str) -> None:
    datac = load_packages()
    if pkg_name not in datac:
        console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
        raise SystemExit(1)

    info  = datac[pkg_name]
    deps  = info.get("deps",  [])
    files = info.get("files", [])
    libs  = info.get("libs",  [])
    pin   = f"  {_YELLOW}[PINNED]{_RESET}" if info.get("pinned") else ""

    _print(f"\n{_BOLD}{_CYAN}📦 {pkg_name}{_RESET}{pin}")
    _print(f"  {_BOLD}Version:{_RESET}      {info.get('version', 'N/A')}")
    _print(f"  {_BOLD}Description:{_RESET}  {info.get('desc', 'No description')}")
    _print(f"  {_BOLD}Installed:{_RESET}    {info.get('installed_at', 'N/A')}")
    _print(f"  {_BOLD}Updated:{_RESET}      {info.get('updated_at', 'N/A')}")
    _print(f"  {_BOLD}Pinned:{_RESET}       {'yes' if info.get('pinned') else 'no'}")
    _print(f"  {_BOLD}Dependencies:{_RESET} {', '.join(deps)  if deps  else '-'}")
    _print(f"  {_BOLD}Files:{_RESET}        {', '.join(files) if files else '-'}")
    _print(f"  {_BOLD}Libs:{_RESET}         {', '.join(libs)  if libs  else '-'}")

    note = info.get("note", "").strip()
    if note:
        _print(f"  {_BOLD}Note:{_RESET}         {note}")

    readme_text = read_readme(pkg_name)
    if readme_text:
        _print(f"\n  {_BOLD}Readme{_RESET}  {_DIM}({pkg_name}-Readme){_RESET}")
        _print("  " + "-" * 50)
        for line in readme_text.splitlines():
            _print(f"  {line}")
        _print("  " + "-" * 50)
    else:
        _print(f"\n  {_BOLD}Readme:{_RESET}  {_DIM}No readme available{_RESET}")
    _print()


def cmd_run(pkg_name: str, extra_args: list[str]) -> None:
    """
    Replace the current process with the installed binary.

    NOTE: --run must be the last weld flag on the command line; any arguments
    after the package name are forwarded verbatim to the binary.
    Example:  weld --run mypkg --verbose input.txt
    """
    _validate_pkg_name(pkg_name)
    binary = _safe_join(WELD_BIN, pkg_name)
    if binary is None or not os.path.isfile(binary):
        console.print(f"[red]Error:[/red] Binary '{_esc(pkg_name)}' not found in {_esc(WELD_BIN)}")
        raise SystemExit(1)
    if not os.access(binary, os.X_OK):
        console.print(
            f"[red]Error:[/red] '{_esc(binary)}' is not executable.\n"
            f"[dim]Hint: chmod +x {_esc(binary)}[/dim]"
        )
        raise SystemExit(1)
    os.execv(binary, [binary] + extra_args)


def _field_as_text(name: str, info: dict, key: str) -> str:
    if key == "name":
        return name
    value = info.get(key, "")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "" if value is None else str(value)


_LIST_FIELDS = (
    "name", "version", "desc", "note", "deps", "files", "libs",
    "pinned", "installed_at", "updated_at",
)


def cmd_list(sort_by: str = "name", filter_pat: str = "") -> None:
    packages = load_packages()

    if filter_pat:
        key, sep, pattern = filter_pat.partition("=")
        key     = key.strip()
        pattern = pattern.strip()
        if not sep or not key or not pattern:
            console.print(
                "[red]Error:[/red] --list-filter expects <field>=<glob>, "
                "e.g. --list-filter version=1.*"
            )
            raise SystemExit(1)
        if key not in _LIST_FIELDS:
            console.print(
                f"[red]Error:[/red] Unknown filter field '{_esc(key)}'. "
                f"Choose from: {', '.join(_LIST_FIELDS)}"
            )
            raise SystemExit(1)
        packages = {
            name: info for name, info in packages.items()
            if fnmatch.fnmatch(_field_as_text(name, info, key), pattern)
        }

    valid_sorts = ("name", "version", "installed_at", "updated_at")
    if sort_by not in valid_sorts:
        _print(f"{_YELLOW}Warning:{_RESET} Unknown sort key '{sort_by}', using 'name'")
        sort_by = "name"

    packages = dict(sorted(
        packages.items(),
        key=lambda item: item[0] if sort_by == "name"
        else _field_as_text(item[0], item[1], sort_by),
    ))

    table = Table(
        title="📦 Installed Capsules", show_header=True,
        header_style="bold cyan", box=ROUNDED,
        padding=(0, 1), row_styles=["", "dim"],
    )
    table.add_column("#",            justify="right", max_width=4,  style="bold")
    table.add_column("Package",      style="bold green",        max_width=18)
    table.add_column("Version",      style="yellow",            max_width=10)
    table.add_column("Description",  style="white",             max_width=22)
    table.add_column("Dependencies", style="blue",              max_width=16)
    table.add_column("Files",        style="magenta",           max_width=12)
    table.add_column("Libs",         style="cyan",              max_width=12)
    table.add_column("Readme",       style="dim",               max_width=7)
    table.add_column("Pinned",       style="yellow",            max_width=7)
    table.add_column("Installed",    style="dim",               max_width=20)

    for idx, (name, info) in enumerate(packages.items(), 1):
        deps  = info.get("deps",  [])
        files = info.get("files", [])
        libs  = info.get("libs",  [])
        table.add_row(
            str(idx), _esc(name),
            _esc(str(info.get("version", "N/A"))),
            _esc(str(info.get("desc", "-"))),
            _esc(", ".join(deps))  if deps  else "[italic]-[/italic]",
            _esc(", ".join(files)) if files else "[italic]-[/italic]",
            _esc(", ".join(libs))  if libs  else "[italic]-[/italic]",
            "✔" if os.path.isfile(readme_path_for(name)) else "-",
            "🔒" if info.get("pinned") else "-",
            _esc(str(info.get("installed_at", "-"))),
        )

    console.print(table)
    total       = len(packages)
    with_deps   = sum(1 for p in packages.values() if p.get("deps"))
    with_readme = sum(1 for n in packages if os.path.isfile(readme_path_for(n)))
    pinned      = sum(1 for p in packages.values() if p.get("pinned"))
    console.print(
        f"\n[bold]Summary:[/bold] {total} welds, "
        f"{with_deps} with dependencies, "
        f"{with_readme} with readme, "
        f"{pinned} pinned"
    )


def cmd_remove(pkg_name: str, silent: bool = False) -> None:
    with _packages_lock():
        datac = load_packages()
        if pkg_name not in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
            raise SystemExit(1)
        if datac[pkg_name].get("pinned", False):
            console.print(
                f"[red]Error:[/red] '{_esc(pkg_name)}' is pinned. "
                "Unpin with --unpin first."
            )
            raise SystemExit(1)

        global_plugin = _load_global_plugin()
        _call_hook(global_plugin, "before_remove", pkg_name)

        # Never delete files or libs that another package still references
        # (duplicated packages share binaries/data/libs).
        other_files: set[str] = set()
        other_libs: set[str] = set()
        for other_name, other_info in datac.items():
            if other_name == pkg_name:
                continue
            other_files.update(str(f) for f in other_info.get("files", []))
            other_libs.update(str(lib) for lib in other_info.get("libs", []))

        for rel in datac[pkg_name].get("files", []):
            rel = str(rel)
            if rel in other_files:
                continue
            path = _safe_join(WELD_BIN, rel)
            if path is None:
                _print(f"{_YELLOW}Warning:{_RESET} Skipping unsafe path '{rel}'")
                continue
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
                _prune_empty_dirs(WELD_BIN, rel)
                if not silent:
                    _print(f"{_YELLOW}Removed:{_RESET} {path}")

        for lib in datac[pkg_name].get("libs", []):
            lib = str(lib)
            if lib in other_libs:
                continue
            path = _safe_join(WELD_LIBS, f"{lib}.so")
            if path is None:
                _print(f"{_YELLOW}Warning:{_RESET} Skipping unsafe lib path '{lib}'")
                continue
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
                _prune_empty_dirs(WELD_BIN, rel)
                if not silent:
                    _print(f"{_YELLOW}Removed:{_RESET} {path}")

        remove_readme(pkg_name, silent=silent)
        remove_changelog(pkg_name, silent=silent)
        del datac[pkg_name]
        save_packages(datac)

    _call_hook(global_plugin, "after_remove", pkg_name)
    _log("REMOVE", pkg_name)
    if not silent:
        _print(f"{_GREEN}{_BOLD}Removed:{_RESET} '{pkg_name}'")


def cmd_pin(pkg_name: str) -> None:
    with _packages_lock():
        datac = load_packages()
        if pkg_name not in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
            raise SystemExit(1)
        datac[pkg_name]["pinned"] = True
        save_packages(datac)
    _log("PIN", pkg_name)
    _print(f"{_GREEN}Pinned:{_RESET} '{pkg_name}'")


def cmd_unpin(pkg_name: str) -> None:
    with _packages_lock():
        datac = load_packages()
        if pkg_name not in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
            raise SystemExit(1)
        datac[pkg_name]["pinned"] = False
        save_packages(datac)
    _log("UNPIN", pkg_name)
    _print(f"{_GREEN}Unpinned:{_RESET} '{pkg_name}'")


def cmd_note(pkg_name: str, note: str) -> None:
    with _packages_lock():
        datac = load_packages()
        if pkg_name not in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
            raise SystemExit(1)
        datac[pkg_name]["note"] = note
        save_packages(datac)
    _log("NOTE", pkg_name, note)
    _print(f"{_GREEN}Note set:{_RESET} {pkg_name}  ->  {note}")


def cmd_rename(old_name: str, new_name: str) -> None:
    _validate_pkg_name(new_name)
    with _packages_lock():
        datac = load_packages()
        if old_name not in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(old_name)}' not found")
            raise SystemExit(1)
        if new_name in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(new_name)}' already exists")
            raise SystemExit(1)
        if datac[old_name].get("pinned", False):
            console.print(f"[red]Error:[/red] '{_esc(old_name)}' is pinned, unpin before renaming.")
            raise SystemExit(1)

        pkg_info = datac[old_name]

        old_bin = os.path.join(WELD_BIN, old_name)
        new_bin = os.path.join(WELD_BIN, new_name)
        if os.path.exists(new_bin):
            console.print(
                f"[red]Error:[/red] '{_esc(new_bin)}' already exists on disk; "
                "remove it or choose another name."
            )
            raise SystemExit(1)

        if os.path.isfile(old_bin):
            os.rename(old_bin, new_bin)
            pkg_info["files"] = [
                new_name if f == old_name else f
                for f in pkg_info.get("files", [])
            ]
        else:
            _print(
                f"{_YELLOW}Warning:{_RESET} Binary for '{old_name}' is missing; "
                "the package record was renamed only."
            )

        if os.path.isfile(readme_path_for(old_name)):
            os.replace(readme_path_for(old_name), readme_path_for(new_name))

        if os.path.isfile(changelog_path_for(old_name)):
            os.replace(changelog_path_for(old_name), changelog_path_for(new_name))

        datac[new_name] = pkg_info
        del datac[old_name]
        save_packages(datac)

    _log("RENAME", old_name, f"-> {new_name}")
    _print(f"{_GREEN}{_BOLD}Renamed:{_RESET} '{old_name}' -> '{new_name}'")


def _editor_command() -> list[str]:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    try:
        parts = shlex.split(editor)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] Could not parse EDITOR '{_esc(editor)}': {_esc(str(exc))}")
        raise SystemExit(1)
    if not parts:
        console.print("[red]Error:[/red] EDITOR is empty")
        raise SystemExit(1)
    return parts


def cmd_edit(pkg_name: str) -> None:
    datac = load_packages()
    if pkg_name not in datac:
        console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
        raise SystemExit(1)

    editor = _editor_command()
    readme = readme_path_for(pkg_name)
    info   = datac[pkg_name]

    if os.path.isfile(readme):
        _print(f"{_DIM}Opening Readme for '{pkg_name}' in {' '.join(editor)}...{_RESET}")
        try:
            subprocess.call([*editor, readme])
        except OSError as exc:
            console.print(f"[red]Error:[/red] Could not launch editor: {_esc(str(exc))}")
            raise SystemExit(1)
        return

    fd, tmp_path = tempfile.mkstemp(suffix=f"_{pkg_name}.txt", prefix="weld-edit-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(
                f"Package: {pkg_name}\n"
                f"Version: {info.get('version', 'N/A')}\n"
                f"Desc:    {info.get('desc', '')}\n"
                f"Note:    {info.get('note', '')}\n"
            )
        _print(f"{_DIM}Opening details for '{pkg_name}' in {' '.join(editor)}...{_RESET}")
        try:
            subprocess.call([*editor, tmp_path])
        except OSError as exc:
            console.print(f"[red]Error:[/red] Could not launch editor: {_esc(str(exc))}")
            raise SystemExit(1)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def cmd_duplicate(src_name: str, dst_name: str) -> None:
    _validate_pkg_name(dst_name)
    with _packages_lock():
        datac = load_packages()
        if src_name not in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(src_name)}' not found")
            raise SystemExit(1)
        if dst_name in datac:
            console.print(f"[red]Error:[/red] Package '{_esc(dst_name)}' already exists")
            raise SystemExit(1)

        pkg_info = copy.deepcopy(datac[src_name])

        src_bin = os.path.join(WELD_BIN, src_name)
        dst_bin = os.path.join(WELD_BIN, dst_name)
        if not os.path.isfile(src_bin):
            console.print(
                f"[red]Error:[/red] Cannot duplicate '{_esc(src_name)}': "
                "its binary is missing. Reinstall it first."
            )
            raise SystemExit(1)
        if os.path.exists(dst_bin):
            console.print(
                f"[red]Error:[/red] '{_esc(dst_bin)}' already exists on disk; "
                "remove it or choose another name."
            )
            raise SystemExit(1)

        shutil.copy2(src_bin, dst_bin)
        pkg_info["files"] = [
            dst_name if f == src_name else f
            for f in pkg_info.get("files", [])
        ]

        if os.path.isfile(readme_path_for(src_name)):
            shutil.copy2(readme_path_for(src_name), readme_path_for(dst_name))

        if os.path.isfile(changelog_path_for(src_name)):
            shutil.copy2(changelog_path_for(src_name), changelog_path_for(dst_name))

        pkg_info.update({
            "pinned":       False,
            "note":         f"duplicated from {src_name}",
            "installed_at": now_iso(),
            "updated_at":   now_iso(),
        })
        datac[dst_name] = pkg_info
        save_packages(datac)

    _log("DUPLICATE", src_name, f"-> {dst_name}")
    _print(f"{_GREEN}{_BOLD}Duplicated:{_RESET} '{src_name}' -> '{dst_name}'")


def cmd_history(pkg_name: str | None = None) -> None:
    if not os.path.isfile(WELD_HISTORY):
        _print(f"{_DIM}No history yet.{_RESET}")
        return

    with open(WELD_HISTORY, encoding="utf-8", errors="replace") as fp:
        lines = [line.rstrip() for line in fp if line.strip()]

    if not lines:
        _print(f"{_DIM}No history yet.{_RESET}")
        return

    records: list[dict] = []
    for line in lines:
        # FIX #8: history is now tab-separated so pkg names with spaces are safe
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        records.append({
            "ts":     parts[0],
            "action": parts[1],
            "pkg":    parts[2],
            "extra":  parts[3] if len(parts) > 3 else "",
        })

    ACTION_COLOR = {
        "INSTALL":   _GREEN,  "REMOVE":    _RED,
        "UPDATE":    _CYAN,   "BUILD":     _CYAN,
        "PIN":       _YELLOW, "UNPIN":     _YELLOW,
        "RENAME":    _YELLOW, "DUPLICATE": _CYAN,
        "NOTE":      _YELLOW,
    }

    if pkg_name:
        records = [r for r in records if r["pkg"] == pkg_name]
        if not records:
            _print(f"{_YELLOW}No history for:{_RESET} '{pkg_name}'")
            return
        _print(f"\n{_BOLD}{_CYAN}History for {pkg_name}{_RESET}\n")
        table = Table(box=ROUNDED, header_style="bold cyan", padding=(0, 1))
        table.add_column("Date",    style="dim",    max_width=22)
        table.add_column("Action",  style="yellow", max_width=12)
        table.add_column("Details", style="white",  max_width=30)
        for r in records:
            table.add_row(_esc(r["ts"]), _esc(r["action"]), _esc(r["extra"] or "-"))
        console.print(table)
    else:
        grouped: dict[str, list] = defaultdict(list)
        for r in records:
            grouped[r["pkg"]].append(r)
        _print(f"\n{_BOLD}{_CYAN}Full History{_RESET}\n")
        for pname, recs in grouped.items():
            _print(f"  {_BOLD}{pname}{_RESET}")
            for r in recs:
                color = ACTION_COLOR.get(r["action"], _RESET)
                extra = f"  {_DIM}{r['extra']}{_RESET}" if r["extra"] else ""
                _print(f"    {_DIM}{r['ts']}{_RESET}  {color}{r['action']:<10}{_RESET}{extra}")
            _print()


def cmd_stats() -> None:
    packages = load_packages()

    total        = len(packages)
    with_deps    = sum(1 for p in packages.values() if p.get("deps"))
    with_readme  = sum(1 for n in packages if os.path.isfile(readme_path_for(n)))
    pinned       = sum(1 for p in packages.values() if p.get("pinned"))
    with_notes   = sum(1 for p in packages.values() if p.get("note", "").strip())
    total_files  = sum(len(p.get("files", [])) for p in packages.values())
    total_libs   = sum(len(p.get("libs",  [])) for p in packages.values())
    total_deps   = sum(len(p.get("deps",  [])) for p in packages.values())
    last_install = max(
        (p.get("updated_at") or p.get("installed_at") or "" for p in packages.values()),
        default="-",
    ) or "-"
    n_templates  = len([f for f in os.listdir(WELD_TEMPLATES) if f.endswith(".py")])
    cfg          = load_config().get("weld", {})

    _print(f"\n{_BOLD}{_CYAN}weld stats{_RESET}\n")

    table = Table(box=ROUNDED, show_header=False, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(style="cyan")

    table.add_row("Capsules installed",  str(total))
    table.add_row("Total files",         str(total_files))
    table.add_row("Total libs",          str(total_libs))
    table.add_row("Total pip deps",      str(total_deps))
    table.add_row("With deps",           str(with_deps))
    table.add_row("With readme",         str(with_readme))
    table.add_row("With notes",          str(with_notes))
    table.add_row("Pinned",              str(pinned))
    table.add_row("Last activity",       last_install)
    table.add_row("Templates available", str(n_templates))
    table.add_row("Compiler",            cfg.get("compiler",      "nuitka"))
    table.add_row("Libs compiler",       cfg.get("libs_compiler", "cython"))
    table.add_row("weld version",         VERSION)
    table.add_row("Python",              sys.version.split()[0])
    table.add_row("Platform",            sys.platform)
    table.add_row("Config",              WELD_CONFIG)
    table.add_row("Templates dir",       WELD_TEMPLATES)
    table.add_row("Plugin",              os.path.join(WELD_PLUGINS, "__init__.py"))

    console.print(table)
    _print()


def cmd_doctor() -> None:
    compiler      = get_compiler()
    libs_compiler = get_libs_compiler()

    # (tool, version command, required?)
    tools = [
        ("python",      [sys.executable,  "--version"], True),
        ("ruff",        ["ruff",          "--version"], True),
        ("nuitka",      ["nuitka",        "--version"], compiler == "nuitka"),
        ("pyinstaller", ["pyinstaller",   "--version"], compiler == "pyinstaller"),
        ("cython",      ["cython",        "--version"], False),
        ("setuptools",  [sys.executable,  "-c", "import setuptools"], False),
        ("mypy",        ["mypy",          "--version"], False),
        ("uv",          ["uv",            "--version"], False),
        ("zip",         ["zip",           "--version"], False),
        ("unzip",       ["unzip",         "--version"], False),
    ]

    _print(f"\n{_BOLD}{_CYAN}weld doctor{_RESET}\n")
    missing_required: list[str] = []
    rows: list[tuple[str, str, str, str]] = []

    for name, cmd, required in tools:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, check=False
            )
            if result.returncode == 0:
                first   = (result.stdout or result.stderr or "").splitlines()
                version = first[0].strip() if first else "installed"
                rows.append((name, "✔", version, "yes" if required else "no"))
            elif required:
                rows.append((name, "✘", "NOT WORKING", "yes"))
                missing_required.append(name)
            else:
                rows.append((name, "-", "not available (optional)", "no"))
        except FileNotFoundError:
            if required:
                rows.append((name, "✘", "NOT FOUND", "yes"))
                missing_required.append(name)
            else:
                rows.append((name, "-", "not found (optional)", "no"))
        except Exception as exc:  # noqa: BLE001 - any tool failure is reported
            if required:
                rows.append((name, "✘", str(exc), "yes"))
                missing_required.append(name)
            else:
                rows.append((name, "-", f"{exc} (optional)", "no"))

    table = Table(box=ROUNDED, header_style="bold cyan", padding=(0, 1))
    table.add_column("Tool",     style="bold white", max_width=14)
    table.add_column("Status",   max_width=8)
    table.add_column("Version",  style="dim",        max_width=32)
    table.add_column("Required", style="dim",        max_width=10)
    for name, status, version, required in rows:
        color = {"✔": "green", "✘": "red"}.get(status, "dim")
        table.add_row(
            _esc(name), f"[{color}]{status}[/{color}]",
            _esc(str(version)), required,
        )
    console.print(table)
    _print()

    # ------------------------------------------------------------------ #
    # Venv check — warn on Linux if weld is not running inside a venv      #
    # ------------------------------------------------------------------ #
    if sys.platform.startswith("linux"):
        in_venv = (
            sys.prefix != sys.base_prefix
            or os.environ.get("VIRTUAL_ENV") is not None
            or os.environ.get("CONDA_DEFAULT_ENV") is not None
        )
        if not in_venv:
            _print(
                f"{_YELLOW}Warning:{_RESET} You are not running inside a virtual "
                f"environment.\n"
                f"  weld installs pip dependencies into the system Python, which\n"
                f"  can conflict with system packages or other projects.\n"
                f"  Consider creating and activating a venv before using weld:\n"
                f"\n"
                f"    {_DIM}python -m venv ~/.weld-env{_RESET}\n"
                f"    {_DIM}source ~/.weld-env/bin/activate{_RESET}\n"
            )
            all_ok = False
        else:
            venv_path = (
                os.environ.get("VIRTUAL_ENV")
                or os.environ.get("CONDA_DEFAULT_ENV")
                or sys.prefix
            )
            _print(f"{_GREEN}✔{_RESET} Running inside a virtual environment: "
                   f"{_DIM}{venv_path}{_RESET}\n")
            all_ok = True
    else:
        all_ok = True

    if missing_required:
        all_ok = False
        _print(
            f"{_RED}Missing required tool(s):{_RESET} "
            f"{', '.join(missing_required)}"
        )
        _print(f"{_DIM}Install everything with: pip install -r requirements.txt{_RESET}")
    elif libs_compiler == "cython" and not any(
        row[0] == "cython" and row[1] == "✔" for row in rows
    ):
        _print(
            f"{_YELLOW}Warning:{_RESET} 'cython' is not available; packages that "
            "ship bin/libs/*.py cannot be installed."
        )

    if all_ok:
        _print(f"{_GREEN}{_BOLD}✔ All required tools are available.{_RESET}")
    else:
        _print(f"{_RED}{_BOLD}✘ Some required tools are missing or warnings exist.{_RESET}")
        raise SystemExit(1)


def cmd_clean() -> None:
    datac = load_packages()

    known_bin_entries: set[str] = set()
    known_lib_names: set[str] = set()
    known_pkg_names: set[str] = set(datac.keys())

    for info in datac.values():
        for tracked in info.get("files", []):
            # Tracked files may be nested (data directories); the first path
            # component is what lives directly inside ~/.weld/bin.
            known_bin_entries.add(str(tracked).replace("\\", "/").split("/")[0])
        for lib in info.get("libs", []):
            known_lib_names.add(f"{lib}.so")

    _JUNK_DIRS = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "build"}
    _JUNK_EXTS = {".c", ".spec"}

    orphaned_root = [
        os.path.join(WELD_DIR, entry) for entry in os.listdir(WELD_DIR)
        if entry not in _WELD_DIR_KNOWN
        and not entry.startswith(("packages.json.tmp.", "config.toml.tmp."))
    ] if os.path.isdir(WELD_DIR) else []

    orphaned_bin: list[str] = []
    if os.path.isdir(WELD_BIN):
        for entry in os.listdir(WELD_BIN):
            if entry == "libs" or entry in _JUNK_DIRS:
                continue
            if entry in known_bin_entries:
                continue
            orphaned_bin.append(os.path.join(WELD_BIN, entry))

    orphaned_libs: list[str] = []
    if os.path.isdir(WELD_LIBS):
        for entry in os.listdir(WELD_LIBS):
            if entry in _JUNK_DIRS or any(entry.endswith(ext) for ext in _JUNK_EXTS):
                continue
            if entry not in known_lib_names:
                orphaned_libs.append(os.path.join(WELD_LIBS, entry))

    orphaned_readme: list[str] = []
    if os.path.isdir(WELD_README):
        for entry in os.listdir(WELD_README):
            if entry.endswith("-Readme"):
                pkg = entry[: -len("-Readme")]
            elif entry.endswith("-Changelog"):
                pkg = entry[: -len("-Changelog")]
            else:
                # Unexpected file in Readme dir — mark as orphaned
                orphaned_readme.append(os.path.join(WELD_README, entry))
                continue
            if pkg not in known_pkg_names:
                orphaned_readme.append(os.path.join(WELD_README, entry))

    junk: list[str] = []
    for root, dirs, files in os.walk(WELD_DIR):
        for directory in list(dirs):
            if directory in _JUNK_DIRS:
                junk.append(os.path.join(root, directory))
                dirs.remove(directory)
        for filename in files:
            if any(filename.endswith(ext) for ext in _JUNK_EXTS):
                junk.append(os.path.join(root, filename))

    candidates: list[tuple[str, str]] = (
        [(p, "orphaned (root)")     for p in orphaned_root]
        + [(p, "orphaned (bin)")    for p in orphaned_bin]
        + [(p, "orphaned (libs)")   for p in orphaned_libs]
        + [(p, "orphaned (readme)") for p in orphaned_readme]
        + [(p, "junk/cache")        for p in junk]
    )

    # De-duplicate (a path can match several rules) and drop anything that is
    # nested inside another directory that will be removed anyway.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for path, reason in candidates:
        abs_path = os.path.abspath(path)
        if abs_path in seen:
            continue
        seen.add(abs_path)
        unique.append((path, reason))

    dir_targets = {
        os.path.abspath(path) for path, _ in unique
        if os.path.isdir(path) and not os.path.islink(path)
    }
    all_targets: list[tuple[str, str]] = []
    for path, reason in unique:
        abs_path = os.path.abspath(path)
        if any(
            abs_path != directory
            and abs_path.startswith(directory + os.sep)
            for directory in dir_targets
        ):
            continue
        all_targets.append((path, reason))

    if not all_targets:
        _print(f"{_GREEN}✔{_RESET} {_BOLD}~/.weld is clean — nothing to remove.{_RESET}")
        return

    def display(path: str) -> str:
        try:
            rel = os.path.relpath(path, WELD_DIR)
        except ValueError:
            return path
        return path if rel == "." else f"~/.weld/{rel}"

    _print(f"\n{_BOLD}{_CYAN}Clean report for ~/.weld{_RESET}\n")
    table = Table(title="Items to remove", box=ROUNDED, header_style="bold cyan", padding=(0, 1))
    table.add_column("Path",   style="white",  max_width=60)
    table.add_column("Reason", style="yellow", max_width=20)
    for path, reason in all_targets:
        table.add_row(display(path), reason)
    console.print(table)
    _print(f"\n{_YELLOW}Total:{_RESET} {len(all_targets)} item(s) will be deleted.")
    _write(f"\n{_BOLD}Proceed? [y/N]:{_RESET} ")

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        _print(f"\n{_YELLOW}Aborted.{_RESET}")
        return

    if answer != "y":
        _print(f"{_YELLOW}Aborted.{_RESET}")
        return

    removed = 0
    for path, _ in all_targets:
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            _print(f"  {_YELLOW}Removed:{_RESET} {display(path)}")
            removed += 1
        except OSError as exc:
            _print(f"  {_RED}Failed:{_RESET} {path}  ({exc})")

    _print(f"\n{_GREEN}{_BOLD}Clean complete.{_RESET} {removed}/{len(all_targets)} item(s) removed.")


def cmd_search(query: str) -> None:
    packages    = load_packages()
    query_lower = query.lower()
    matches = {
        name: info for name, info in packages.items()
        if query_lower in name.lower() or query_lower in info.get("desc", "").lower()
    }
    if not matches:
        _print(f"{_YELLOW}No packages matched:{_RESET} '{query}'")
        return
    table = Table(
        title=f"🔍 Search: '{query}'",
        box=ROUNDED, header_style="bold cyan", padding=(0, 1),
    )
    table.add_column("Package",     style="bold green", max_width=20)
    table.add_column("Version",     style="yellow",     max_width=10)
    table.add_column("Description", style="white",      max_width=40)
    table.add_column("Readme",      style="dim",        max_width=8)
    for name, info in matches.items():
        table.add_row(
            _esc(name), _esc(str(info.get("version", "N/A"))),
            _esc(str(info.get("desc", "-"))),
            "✔" if os.path.isfile(readme_path_for(name)) else "-",
        )
    console.print(table)
    console.print(f"\n[bold]{len(matches)}[/bold] match(es) found")


def cmd_verify() -> None:
    datac = load_packages()
    if not datac:
        _print(f"{_DIM}No welds installed.{_RESET}")
        return

    results: dict[str, dict] = {}
    for pkg_name, info in datac.items():
        missing = []
        for f in info.get("files", []):
            path = _safe_join(WELD_BIN, str(f))
            if path is None or not os.path.exists(path):
                missing.append(f"bin/{f}")
            elif str(f) == pkg_name and not os.access(path, os.X_OK):
                missing.append(f"bin/{f} (not executable)")
        for lib in info.get("libs", []):
            path = _safe_join(WELD_LIBS, f"{lib}.so")
            if path is None or not os.path.exists(path):
                missing.append(f"bin/libs/{lib}.so")
        results[pkg_name] = {
            "ok":      len(missing) == 0,
            "missing": missing,
            "readme":  os.path.isfile(readme_path_for(pkg_name)),
        }

    _print(f"\n{_BOLD}{_CYAN}Verifying all welds...{_RESET}\n")
    for pkg_name, res in results.items():
        status = f"{_GREEN}✔{_RESET}" if res["ok"] else f"{_RED}✘{_RESET}"
        _print(f"  {status} {_BOLD}{pkg_name}{_RESET}")
        for m in res["missing"]:
            _print(f"      {_RED}✘{_RESET} {m}  {_RED}<- MISSING{_RESET}")
        _print(f"      {'✔' if res['readme'] else '-'} Readme/{pkg_name}-Readme")

    _print()
    table = Table(title="Verify Summary", box=ROUNDED, header_style="bold cyan", padding=(0, 1))
    table.add_column("Package", style="bold white", max_width=20)
    table.add_column("Status",  max_width=10)
    table.add_column("Missing", style="red",  max_width=30)
    table.add_column("Readme",  style="dim",  max_width=8)

    all_ok = True
    for pkg_name, res in results.items():
        if not res["ok"]:
            all_ok = False
        table.add_row(
            _esc(pkg_name),
            "[green]✔ OK[/green]" if res["ok"] else "[red]✘ BROKEN[/red]",
            _esc(", ".join(res["missing"])) if res["missing"] else "-",
            "✔" if res["readme"] else "-",
        )
    console.print(table)

    total  = len(results)
    broken = sum(1 for r in results.values() if not r["ok"])
    _print(
        f"\n{_BOLD}Summary:{_RESET} {total} welds  "
        f"{_GREEN}{total - broken} healthy{_RESET}  "
        f"{(_RED if broken else _DIM)}{broken} broken{_RESET}"
    )
    if not all_ok:
        raise SystemExit(1)


def cmd_addc(pkg_name: str) -> None:
    datac = load_packages()
    if pkg_name not in datac:
        console.print(f"[red]Error:[/red] Package '{_esc(pkg_name)}' not found")
        raise SystemExit(1)
    libs = datac[pkg_name].get("libs", [])
    if not libs:
        console.print(f"[yellow]Warning:[/yellow] Package '{_esc(pkg_name)}' has no libs")
        raise SystemExit(1)
    if not os.path.isfile("config.json"):
        console.print("[red]Error:[/red] Not in a weld project directory")
        raise SystemExit(1)
    os.makedirs(os.path.join("bin", "libs"), exist_ok=True)
    copied = 0
    for lib in libs:
        src_path = _safe_join(WELD_LIBS, f"{lib}.so")
        dst_path = _safe_join(os.path.join("bin", "libs"), f"{lib}.so")
        if src_path is None or dst_path is None:
            console.print(f"[red]Error:[/red] Invalid library name '{_esc(lib)}'")
            raise SystemExit(1)
        if not os.path.isfile(src_path):
            console.print(f"[red]Error:[/red] {_esc(lib)}.so not found in installed package")
            raise SystemExit(1)
        with spinner(f"Copying {lib}.so -> bin/libs/"):
            shutil.copy2(src_path, dst_path)
        copied += 1
    _print(f"{_GREEN}✔{_RESET} Copied {copied} lib(s) into bin/libs/")


def cmd_completions_zsh() -> None:
    helper_path = os.path.join(WELD_DIR, "_complete_packages.py")
    _atomic_write(
        helper_path,
        "import json\n"
        f"with open({WELD_PACKAGES!r}) as f:\n"
        "    d = json.load(f)\n"
        "print('\\n'.join(d.keys()))\n",
    )

    template_names = " ".join(
        os.path.splitext(f)[0]
        for f in sorted(os.listdir(WELD_TEMPLATES))
        if f.endswith(".py")
    )

    script = rf"""#compdef weld

_weld() {{
    local -a opts
    opts=(
        '(-l --list){{-l,--list}}[List installed welds]'
        '--list-sort[Sort key]:key:(name version installed_at updated_at)'
        '--list-filter[Filter welds]:filter:'
        '--build[Build a folder into a .wld archive]:folder:_files -/'
        '--install[Install a .wld package]:file:_files -g "*.wld"'
        '--update[Update an installed package]:file:_files -g "*.wld"'
        '--new[Create a new weld project skeleton]:folder:_files -/'
        '--remove[Remove an installed weld]:package:_weld_packages'
        '--info[Show info about a weld]:package:_weld_packages'
        '--run[Run an installed binary (must be last flag)]:package:_weld_packages'
        '--search[Search welds]:query:'
        '--verify[Verify all installed welds]'
        '--clean[Clean orphaned files from ~/.weld]'
        '--addc[Copy libs into current project]:package:_weld_packages'
        '--pin[Pin a weld]:package:_weld_packages'
        '--unpin[Unpin a weld]:package:_weld_packages'
        '--note[Set a note on a weld]:package:_weld_packages'
        '--rename[Rename a weld]:package:_weld_packages'
        '--duplicate[Duplicate a weld]:package:_weld_packages'
        '--edit[Edit weld readme in $EDITOR]:package:_weld_packages'
        '--history[Show history]:package:_weld_packages'
        '--stats[Show weld stats]'
        '--doctor[Check required tools]'
        '--template[Apply a template to current project]:template:({template_names})'
        '--templates[List available templates]'
        '--completions[Print completions]:shell:(zsh)'
        '--version[Show version]'
        '(-h --help){{-h,--help}}[Show help]'
    )
    _arguments -s $opts
}}

_weld_packages() {{
    local packages
    packages=(${{(f)"$({shlex.quote(sys.executable)} {shlex.quote(helper_path)} 2>/dev/null)"}})
    _describe 'packages' packages
}}

_weld
"""
    print(script)
    _print(f"\n{_DIM}# Add to ~/.zshrc:{_RESET}")
    _print(f"{_DIM}# source <(weld --completions zsh){_RESET}")


# ---------------------------------------------------------------------------
# Self-upgrade helpers
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints."""
    v = v.strip().lstrip("vV")
    if not v:
        return (0,)
    # split on common separators
    parts = re.split(r"[.\-+_]", v)
    nums: list[int] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^(\d+)", p)
        if m:
            try:
                nums.append(int(m.group(1)))
            except ValueError:
                nums.append(0)
        else:
            # pre-release tags like alpha/beta/rc without leading digits
            # treat as 0 but keep structure
            nums.append(0)
    return tuple(nums) if nums else (0,)


def _is_newer_version(remote: str, local: str) -> bool:
    """Return True if *remote* is newer than *local*."""
    try:
        return _parse_version(remote) > _parse_version(local)
    except Exception:
        # fallback: string compare
        return remote.strip() != local.strip() and remote.strip() > local.strip()


def _fetch_url_text(url: str, timeout: int = 15) -> str:
    """Fetch a URL as text. Tries urllib, then curl/wget fallback."""
    import urllib.request
    import urllib.error

    last_exc: Exception | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"weld/{VERSION}", "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # GitHub may return json for API, text for version file
            try:
                return data.decode("utf-8").strip()
            except UnicodeDecodeError:
                return data.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        last_exc = exc

    # fallback to curl / wget if available (helps in minimal containers where SSL CA may fail)
    for tool, extra_args in (("curl", ["-fsSL"]), ("wget", ["-qO-"])):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            result = subprocess.run(
                [exe, *extra_args, url],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            continue

    raise RuntimeError(f"Failed to fetch {url}: {last_exc}") from last_exc


def _fetch_url_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch a URL as bytes. Tries urllib, then curl/wget fallback."""
    import urllib.request

    last_exc: Exception | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"weld/{VERSION}", "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        last_exc = exc

    # curl fallback (binary safe)
    curl = shutil.which("curl")
    if curl:
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                [curl, "-fsSL", "-o", tmp_path, url],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if result.returncode == 0 and os.path.isfile(tmp_path):
                with open(tmp_path, "rb") as fp:
                    data = fp.read()
                os.remove(tmp_path)
                if data:
                    return data
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    wget = shutil.which("wget")
    if wget:
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                [wget, "-qO", tmp_path, url],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if result.returncode == 0 and os.path.isfile(tmp_path):
                with open(tmp_path, "rb") as fp:
                    data = fp.read()
                os.remove(tmp_path)
                if data:
                    return data
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    raise RuntimeError(f"Failed to fetch {url}: {last_exc}") from last_exc


def _get_release_urls() -> tuple[str, str]:
    """
    Try to resolve the latest release tag via GitHub API, falling back to
    the hardcoded RELEASE_VERSION_URL / RELEASE_WELD_URL.
    Returns (version_url, weld_url).

    Env overrides (for testing):
      WELD_VERSION_URL, WELD_WELD_URL, WELD_RELEASE_API_URL
    """
    env_version = os.environ.get("WELD_VERSION_URL")
    env_weld = os.environ.get("WELD_WELD_URL")
    if env_version or env_weld:
        return env_version or RELEASE_VERSION_URL, env_weld or RELEASE_WELD_URL

    # Allow API URL override for testing
    api_url = os.environ.get("WELD_RELEASE_API_URL") or RELEASE_API_URL
    try:
        api_text = _fetch_url_text(api_url, timeout=10)
        info = json.loads(api_text)
        tag = info.get("tag_name")
        if isinstance(tag, str) and tag.strip():
            tag = tag.strip()
            version_url = f"https://github.com/{WELD_REPO}/releases/download/{tag}/version"
            weld_url = f"https://github.com/{WELD_REPO}/releases/download/{tag}/weld"
            return version_url, weld_url
    except Exception:
        pass
    return RELEASE_VERSION_URL, RELEASE_WELD_URL


def _clone_repo_and_get_weld(version_only: bool = False) -> tuple[str, bytes | None]:
    """
    Clone the weld git repo shallowly and extract version + weld bytes.
    Returns (version, data) where data is the raw weld.py bytes (or None if version_only).
    Env overrides:
      WELD_GIT_URL     – repo URL (default https://github.com/MrNotInk/Weld.git)
      WELD_GIT_BRANCH  – branch/tag to clone (default main)
    Raises RuntimeError if git not found or clone fails.
    """
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git not found on PATH — cannot clone repo")

    repo_url = os.environ.get("WELD_GIT_URL") or WELD_GIT_URL
    branch = os.environ.get("WELD_GIT_BRANCH") or "main"

    tmp_parent = tempfile.mkdtemp(prefix="weld-git-")
    clone_dir = os.path.join(tmp_parent, "repo")

    # file:// URLs for testing: if repo_url is file://, strip prefix
    try:
        # Try branch-specific shallow clone first, then fallback to default branch
        for attempt_branch in ([branch] if branch else []) + [None]:
            # cleanup previous attempt
            if os.path.exists(clone_dir):
                shutil.rmtree(clone_dir, ignore_errors=True)

            if attempt_branch:
                cmd = [git, "clone", "--depth", "1", "--branch", attempt_branch, repo_url, clone_dir]
            else:
                cmd = [git, "clone", "--depth", "1", repo_url, clone_dir]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            if result.returncode == 0 and os.path.isfile(os.path.join(clone_dir, "weld.py")):
                break
        else:
            raise RuntimeError(f"git clone failed: {(result.stderr or result.stdout).strip()[:500]}")

        weld_py = os.path.join(clone_dir, "weld.py")
        with open(weld_py, "rb") as fp:
            data = fp.read()

        # Extract VERSION = "x.y.z"
        m = re.search(br'VERSION\s*=\s*["\']([^"\']+)["\']', data)
        if not m:
            raise RuntimeError("Could not find VERSION in cloned weld.py")
        version = m.group(1).decode("utf-8", errors="replace").strip()

        if version_only:
            return version, None
        return version, data

    finally:
        # cleanup clone
        try:
            shutil.rmtree(tmp_parent, ignore_errors=True)
        except Exception:
            pass


def _get_remote_version_and_data_via_git_or_release(prefer_git: bool = True) -> tuple[str, bytes | None, str]:
    """
    Try to get remote version (and optionally weld bytes) via git clone first,
    falling back to release download URLs.
    Returns (version, data_or_None, method) where method is 'git' or 'release'.
    If version_only check, data may be None and will be fetched later via the
    appropriate method.
    """
    # Env override to force method
    method_pref = os.environ.get("WELD_UPGRADE_METHOD", "").lower()
    if method_pref == "release":
        prefer_git = False
    elif method_pref == "git":
        prefer_git = True

    if prefer_git and shutil.which("git"):
        try:
            # For check-update we only need version, but we fetch both for upgrade
            # to avoid double-clone. Here we try to get both.
            version, data = _clone_repo_and_get_weld(version_only=False)
            if version and data:
                return version, data, "git"
        except Exception as exc:
            _print(f"{_DIM}Git clone failed ({exc}), falling back to releases...{_RESET}")

    # Fallback to releases (HTTP)
    version_url, weld_url = _get_release_urls()
    # version fetch handled by caller; here we just indicate method
    # For upgrade path, caller will fetch weld bytes via HTTP separately
    # So for git fallback we return version=None to signal HTTP path
    # Actually we fetch version here for consistency
    try:
        with spinner("Fetching remote version (release)"):
            remote_version = _fetch_url_text(version_url).strip().split()[0]
        return remote_version, None, "release"
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch version via releases: {exc}") from exc


def _ensure_shebang(file_path: str) -> None:
    """Ensure *file_path* starts with #!/usr/bin/env python3."""
    expected = b"#!/usr/bin/env python3\n"
    try:
        with open(file_path, "rb") as fp:
            content = fp.read()
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not read {_esc(file_path)}: {_esc(str(exc))}")
        raise SystemExit(1)

    if not content:
        # empty file, just write shebang
        try:
            with open(file_path, "wb") as out:
                out.write(expected)
        except OSError as exc:
            console.print(f"[red]Error:[/red] Could not write shebang: {_esc(str(exc))}")
            raise SystemExit(1)
        return

    lines = content.splitlines(keepends=True)
    first = lines[0] if lines else b""

    if first.startswith(b"#!"):
        if first.strip() != expected.strip():
            # replace shebang
            lines[0] = expected
            new_content = b"".join(lines)
            try:
                _atomic_write(file_path, new_content)
            except OSError as exc:
                console.print(f"[red]Error:[/red] Could not update shebang: {_esc(str(exc))}")
                raise SystemExit(1)
        return
    else:
        # prepend shebang
        try:
            _atomic_write(file_path, expected + content)
        except OSError as exc:
            console.print(f"[red]Error:[/red] Could not prepend shebang: {_esc(str(exc))}")
            raise SystemExit(1)


def _is_in_path(directory: str) -> bool:
    """Return True if *directory* is already in $PATH."""
    path_env = os.environ.get("PATH", "")
    # normalize both sides
    needle = os.path.abspath(os.path.expanduser(directory))
    for entry in path_env.split(os.pathsep):
        if not entry.strip():
            continue
        try:
            if os.path.abspath(os.path.expanduser(entry.strip())) == needle:
                return True
        except Exception:
            if entry.strip() == directory:
                return True
    # also check literal $HOME form
    home_bin_literal = "$HOME/.local/bin"
    if directory == os.path.expanduser("~/.local/bin") and home_bin_literal in path_env:
        return True
    return False


def _add_local_bin_to_path(bin_dir: str) -> None:
    """
    Try to add *bin_dir* to the user's shell RC files.
    Creates ~/.local/bin if needed is handled elsewhere; this only edits RC.
    """
    home = os.path.expanduser("~")
    rc_candidates = [
        os.path.join(home, ".bashrc"),
        os.path.join(home, ".zshrc"),
        os.path.join(home, ".profile"),
        os.path.join(home, ".bash_profile"),
    ]
    # Prefer the literal $HOME form for portability across machines
    export_line = 'export PATH="$HOME/.local/bin:$PATH"'

    # If any RC already configures .local/bin, don't duplicate — just inform
    for rc in rc_candidates:
        if not os.path.isfile(rc):
            continue
        try:
            with open(rc, encoding="utf-8", errors="replace") as fp:
                if ".local/bin" in fp.read():
                    _print(f"{_DIM}Found {_RESET}{rc}{_DIM} already configures .local/bin — not adding duplicate.{_RESET}")
                    _print(f"{_DIM}Restart your shell or run: source {rc}{_RESET}")
                    return
        except OSError:
            continue

    updated: list[str] = []
    for rc in rc_candidates:
        if not os.path.isfile(rc):
            continue
        try:
            with open(rc, encoding="utf-8", errors="replace") as fp:
                content = fp.read()
        except OSError:
            continue
        # already contains .local/bin in some form?
        if ".local/bin" in content:
            continue
        try:
            with open(rc, "a", encoding="utf-8") as fp:
                fp.write(f"\n# Added by weld self-upgrade — make `weld` available\n{export_line}\n")
            updated.append(rc)
        except OSError:
            continue

    if not updated:
        # No existing RC contained it; create/update ~/.profile as fallback
        profile = os.path.join(home, ".profile")
        try:
            content = ""
            if os.path.isfile(profile):
                with open(profile, encoding="utf-8", errors="replace") as fp:
                    content = fp.read()
            if ".local/bin" not in content:
                with open(profile, "a", encoding="utf-8") as fp:
                    if content and not content.endswith("\n"):
                        fp.write("\n")
                    if not content:
                        fp.write(f"{export_line}\n")
                    else:
                        fp.write(f"\n# Added by weld self-upgrade — make `weld` available\n{export_line}\n")
                updated.append(profile)
        except OSError:
            pass

    if updated:
        _print(f"{_GREEN}✔ Added {_RESET}{bin_dir} to PATH in: {', '.join(updated)}")
        _print(f"{_DIM}Restart your shell or run: source {updated[0]}{_RESET}")
    else:
        # Could not auto-edit; at least tell the user what to do
        _print(f"{_YELLOW}Warning:{_RESET} {bin_dir} is not in your PATH.")
        _print(f"{_DIM}Add this to your shell RC (~/.bashrc / ~/.zshrc):{_RESET} {export_line}")
        _print(f"{_DIM}Then restart your shell or run: export PATH=\"$HOME/.local/bin:$PATH\"{_RESET}")


def _prompt_upgrade(current: str, remote: str) -> bool:
    """Prompt user to confirm upgrade; return True if yes."""
    _print(f"\n{_BOLD}{_CYAN}Update available:{_RESET} {current} → {_GREEN}{remote}{_RESET}")
    # Non-interactive without TTY: require --yes
    if not sys.stdin.isatty():
        _print(f"{_YELLOW}Non-interactive shell detected. Use --yes to auto-confirm.{_RESET}")
        return False
    try:
        answer = input(f"Upgrade now? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        _print(f"\n{_YELLOW}Aborted.{_RESET}")
        return False
    # default yes on empty, yes on y/yes
    if not answer or answer in ("y", "yes"):
        return True
    return False


def cmd_check_update() -> None:
    """Check if a newer weld version is available (via git clone or releases)."""
    _print(f"{_BOLD}{_CYAN}Checking for updates...{_RESET}  current {_BOLD}{VERSION}{_RESET}")

    method_pref = os.environ.get("WELD_UPGRADE_METHOD", "").lower()
    prefer_git = method_pref != "release" and shutil.which("git") is not None

    remote_version: str | None = None
    source = ""
    if prefer_git:
        git_url = os.environ.get("WELD_GIT_URL") or WELD_GIT_URL
        _print(f"{_DIM}Trying git clone from {git_url} ...{_RESET}")
        try:
            with spinner(f"Cloning {WELD_REPO}"):
                remote_version, _ = _clone_repo_and_get_weld(version_only=True)
            source = f"git:{git_url}"
            _print(f"{_DIM}Got version via git clone: {remote_version}{_RESET}")
        except Exception as exc:
            _print(f"{_DIM}Git check failed ({exc}), falling back to releases...{_RESET}")

    if remote_version is None:
        version_url, _ = _get_release_urls()
        _print(f"{_DIM}Fetching {version_url}{_RESET}")
        try:
            with spinner(f"Fetching version from {WELD_REPO}"):
                remote_version = _fetch_url_text(version_url).strip()
        except Exception as exc:
            console.print(f"[red]Error:[/red] Could not fetch remote version: {_esc(str(exc))}")
            raise SystemExit(1)
        source = version_url

    if not remote_version:
        console.print("[red]Error:[/red] Remote version file is empty")
        raise SystemExit(1)

    # Sanitize: version file may contain whitespace/newlines, take first token
    remote_version = remote_version.split()[0].strip()
    _print(f"{_BOLD}Current:{_RESET} {VERSION}")
    _print(f"{_BOLD}Latest:{_RESET}  {remote_version}  {_DIM}({source}){_RESET}")

    if _is_newer_version(remote_version, VERSION):
        _print(f"\n{_GREEN}{_BOLD}Update available:{_RESET} {VERSION} → {remote_version}")
        _print(f"{_DIM}Run: weld --self-upgrade  (or weld --self-upgrade --yes for non-interactive){_RESET}")
    elif _parse_version(remote_version) == _parse_version(VERSION):
        _print(f"\n{_GREEN}✔ You are on the latest version.{_RESET}")
    else:
        _print(f"\n{_YELLOW}You are ahead of the release ({VERSION} > {remote_version}).{_RESET}")


def cmd_self_upgrade(force: bool = False, yes: bool = False) -> None:
    """
    Self-upgrade weld.

    Two methods (git clone preferred):
      - Git: clone https://github.com/MrNotInk/Weld.git, copy weld.py, chmod +x + shebang
      - Release: download version + weld asset from GitHub Releases

    Flow:
      1. Fetch remote version (git or release)
      2. Compare with local VERSION; prompt unless --yes/--force
      3. Get ``weld`` bytes (from clone or download), ensure shebang + chmod +x
      4. Install to ~/.local/bin/weld (create dir if needed)
      5. Ensure ~/.local/bin is in $PATH (create + edit RC if not)
    """
    _print(f"{_BOLD}{_CYAN}weld self-upgrade{_RESET}  current {_BOLD}{VERSION}{_RESET}")

    method_pref = os.environ.get("WELD_UPGRADE_METHOD", "").lower()
    prefer_git = method_pref != "release" and shutil.which("git") is not None
    use_git = prefer_git
    git_url = os.environ.get("WELD_GIT_URL") or WELD_GIT_URL

    remote_version: str | None = None
    data: bytes | None = None
    source_desc = ""
    git_temp_to_clean: str | None = None

    if use_git:
        _print(f"{_DIM}Method: git clone {git_url}{_RESET}")
        try:
            with spinner(f"Cloning {WELD_REPO}"):
                remote_version, data = _clone_repo_and_get_weld(version_only=False)
            source_desc = f"git clone {git_url}"
            _print(f"{_DIM}Cloned version {remote_version} ({len(data) if data else 0} bytes){_RESET}")
        except Exception as exc:
            _print(f"{_YELLOW}Git clone failed:{_RESET} {exc}")
            _print(f"{_DIM}Falling back to releases...{_RESET}")
            use_git = False

    if not use_git:
        version_url, weld_url = _get_release_urls()
        _print(f"{_DIM}Release: {WELD_REPO}  (version file: {version_url}){_RESET}")

        # 1. Fetch remote version
        try:
            with spinner(f"Fetching remote version"):
                remote_version = _fetch_url_text(version_url).strip().split()[0]
        except Exception as exc:
            console.print(f"[red]Error:[/red] Could not fetch remote version: {_esc(str(exc))}")
            _print(f"{_DIM}Tried: {version_url}{_RESET}")
            _print(f"{_DIM}Check your internet or try: curl -fsSL {version_url}{_RESET}")
            raise SystemExit(1)

        if not remote_version:
            console.print("[red]Error:[/red] Remote version file is empty")
            raise SystemExit(1)

    # Common version handling
    assert remote_version is not None

    _print(f"{_BOLD}Current:{_RESET} {VERSION}")
    _print(f"{_BOLD}Latest:{_RESET}  {remote_version}")

    is_newer = _is_newer_version(remote_version, VERSION)
    is_same = _parse_version(remote_version) == _parse_version(VERSION)

    if not is_newer and not force:
        if is_same:
            _print(f"\n{_GREEN}✔ Already on the latest version ({VERSION}).{_RESET}")
            _print(f"{_DIM}Use --force to reinstall anyway.{_RESET}")
            return
        else:
            _print(f"\n{_YELLOW}You are ahead of the release ({VERSION} > {remote_version}).{_RESET}")
            _print(f"{_DIM}Use --force to downgrade/reinstall.{_RESET}")
            return

    if is_newer:
        _print(f"\n{_GREEN}{_BOLD}New version available:{_RESET} {VERSION} → {remote_version}")
    else:
        _print(f"\n{_YELLOW}Force reinstall:{_RESET} {VERSION} → {remote_version}")

    # 2. Prompt unless auto-confirm
    if not yes and not force:
        # --force without --yes still prompts? No, --force should skip prompt as explicit intent
        # Keep behavior: force implies yes for automation, but we ask if not --yes
        # Spec says \"if version is exceeded then it will ask if you to upgrade if yes then download\"
        # So we need to ask. Only skip if --yes is passed.
        if not _prompt_upgrade(VERSION, remote_version):
            _print(f"{_YELLOW}Upgrade cancelled.{_RESET}")
            return
    elif not yes and force:
        # --force is explicit, don't prompt
        pass
    else:
        # yes=True, auto-confirm
        _print(f"{_DIM}Auto-confirm (--yes) — proceeding.{_RESET}")

    # 3. Get weld bytes (already have if git, else download)
    if use_git:
        # data already from clone
        assert data is not None
        _print(f"\n{_BOLD}{_CYAN}Got weld via git clone...{_RESET}  {_DIM}({len(data)} bytes from {source_desc}){_RESET}")
    else:
        # Download weld binary via releases
        version_url, weld_url = _get_release_urls()  # re-resolve for correct weld_url
        _print(f"\n{_BOLD}{_CYAN}Downloading weld binary...{_RESET}")
        _print(f"{_DIM}From: {weld_url}{_RESET}")

        try:
            with spinner(f"Downloading weld {remote_version}"):
                data = _fetch_url_bytes(weld_url)
        except Exception as exc:
            console.print(f"[red]Error:[/red] Could not download weld binary: {_esc(str(exc))}")
            _print(f"{_DIM}Tried: {weld_url}{_RESET}")
            raise SystemExit(1)

    if not data or len(data) < 1024:
        console.print(f"[red]Error:[/red] Weld file is too small ({len(data) if data else 0} bytes) — corrupted download/clone")
        raise SystemExit(1)

    # Basic sanity: should look like a Python script (contains weld marker or VERSION)
    if b"weld" not in data.lower() and b"VERSION" not in data and b"import argparse" not in data:
        console.print("[yellow]Warning:[/yellow] File does not look like weld (missing expected markers) — continuing anyway")

    # 4. Install to ~/.local/bin/weld
    bin_dir = os.path.expanduser(LOCAL_BIN_DIR)
    dest = os.path.expanduser(LOCAL_BIN_WELD)

    try:
        os.makedirs(bin_dir, exist_ok=True)
    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not create {bin_dir}: {_esc(str(exc))}")
        raise SystemExit(1)

    # Backup existing if present
    if os.path.isfile(dest):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{dest}.bak.{ts}"
        try:
            shutil.copy2(dest, backup)
            _print(f"{_DIM}Backed up existing weld → {backup}{_RESET}")
        except OSError as exc:
            _print(f"{_YELLOW}Warning:{_RESET} Could not backup existing {dest}: {exc}")

    # Atomic write to dest via temp file in same directory
    tmp_fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".weld-upgrade-", dir=bin_dir)
        tmp_fd = fd
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
            fp.flush()
            try:
                os.fsync(fp.fileno())
            except OSError:
                pass
        tmp_fd = None  # fd closed

        # Ensure shebang
        _ensure_shebang(tmp_path)

        # chmod +x
        try:
            os.chmod(tmp_path, 0o755)
        except OSError as exc:
            _print(f"{_YELLOW}Warning:{_RESET} Could not chmod +x: {exc}")

        # Replace dest atomically
        os.replace(tmp_path, dest)
        tmp_path = None

        # Ensure final chmod (in case replace lost it on some filesystems)
        try:
            os.chmod(dest, 0o755)
        except OSError:
            pass

    except OSError as exc:
        console.print(f"[red]Error:[/red] Could not install to {dest}: {_esc(str(exc))}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise SystemExit(1)
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    _print(f"\n{_GREEN}{_BOLD}✔ Installed:{_RESET} {dest}  {_DIM}({len(data)} bytes, {remote_version}){_RESET}")

    # Verify new binary
    try:
        result = subprocess.run(
            [dest, "--version"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        out = (result.stdout or "") + (result.stderr or "")
        if remote_version in out or VERSION in out or "weld" in out.lower():
            _print(f"{_DIM}Verified: {out.strip().splitlines()[0][:120]}{_RESET}")
        else:
            _print(f"{_DIM}Installed binary output: {out.strip()[:120]}{_RESET}")
    except Exception:
        pass

    # 5. Ensure ~/.local/bin is in PATH
    if not _is_in_path(bin_dir):
        _print(f"\n{_YELLOW}Note:{_RESET} {bin_dir} is not in your PATH.")
        try:
            _add_local_bin_to_path(bin_dir)
        except Exception as exc:
            _print(f"{_YELLOW}Warning:{_RESET} Could not update PATH automatically: {exc}")
            _print(f"{_DIM}Manually add: export PATH=\"$HOME/.local/bin:$PATH\"{_RESET}")
    else:
        _print(f"{_DIM}{bin_dir} is already in PATH.{_RESET}")

    # History
    try:
        _log("SELF-UPGRADE", "weld", f"{VERSION} -> {remote_version}")
    except Exception:
        pass

    _print(f"\n{_GREEN}{_BOLD}Upgrade complete!{_RESET}  {VERSION} → {remote_version}")
    _print(f"{_DIM}Run: {dest} --version  to confirm. If 'weld' command not found, restart your shell.{_RESET}")
    # Also mention direct python fallback
    _print(f"{_DIM}Fallback: python {dest} --version{_RESET}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    bootstrap()

    parser = argparse.ArgumentParser(
        prog="weld",
        description=f"weld — fixes the mess of Python app distribution  v{VERSION}",
        epilog=(
            "NOTE: --run must be the last flag; everything after the package name "
            "is forwarded to the binary.  Example: weld --run mypkg --verbose"
        ),
    )

    parser.add_argument("--version",      action="store_true")
    parser.add_argument("-l", "--list",   action="store_true")
    parser.add_argument("--list-sort",    metavar="<key>",       default="name")
    parser.add_argument("--list-filter",  metavar="<key=glob>",  default="")
    parser.add_argument("--build",        metavar="<folder>")
    parser.add_argument("--install",      metavar="<file>")
    parser.add_argument("--update",       metavar="<file>")
    parser.add_argument("--new",          metavar="<folder>")
    parser.add_argument("--remove",       metavar="<package>")
    parser.add_argument("--info",         metavar="<package>")
    parser.add_argument("--run",          metavar="<package>",   nargs=argparse.REMAINDER)
    parser.add_argument("--search",       metavar="<query>")
    parser.add_argument("--verify",       action="store_true")
    parser.add_argument("--clean",        action="store_true")
    parser.add_argument("--addc",         metavar="<package>")
    parser.add_argument("--pin",          metavar="<package>")
    parser.add_argument("--unpin",        metavar="<package>")
    parser.add_argument("--note",         metavar="<package>",   nargs="+")
    parser.add_argument("--rename",       metavar="<package>",   nargs=2)
    parser.add_argument("--duplicate",    metavar="<package>",   nargs=2)
    parser.add_argument("--edit",         metavar="<package>")
    parser.add_argument("--history",      metavar="<package>",   nargs="?", const="")
    parser.add_argument("--stats",        action="store_true")
    parser.add_argument("--doctor",       action="store_true")
    parser.add_argument("--template",     metavar="<name>")
    parser.add_argument("--templates",    action="store_true")
    parser.add_argument("--completions",  metavar="<shell>")
    parser.add_argument("--self-upgrade", action="store_true", help="Upgrade weld itself to the latest release from GitHub")
    parser.add_argument("--check-update", action="store_true", help="Check if a newer weld version is available")
    parser.add_argument("--yes",          action="store_true", help="Auto-confirm prompts (for --self-upgrade)")
    parser.add_argument("--force",        action="store_true", help="Force reinstall even if already on latest (for --self-upgrade)")

    args = parser.parse_args()

    try:
        if args.version:
            _print(f"weld {_CYAN}{_BOLD}{VERSION}{_RESET}")
        elif args.list or args.list_filter or args.list_sort != "name":
            cmd_list(sort_by=args.list_sort, filter_pat=args.list_filter)
        elif args.build is not None:
            cmd_build(args.build)
        elif args.install is not None:
            cmd_install(args.install)
        elif args.update is not None:
            cmd_update(args.update)
        elif args.new is not None:
            cmd_new(args.new)
        elif args.remove is not None:
            cmd_remove(args.remove)
        elif args.info is not None:
            cmd_info(args.info)
        elif args.run is not None:
            if not args.run:
                console.print(
                    "[red]Error:[/red] --run requires a package name.\n"
                    "[dim]NOTE: --run must be placed last so arguments are "
                    "forwarded correctly.  Example: weld --run mypkg --flag[/dim]"
                )
                raise SystemExit(1)
            cmd_run(args.run[0], args.run[1:])
        elif args.search is not None:
            cmd_search(args.search)
        elif args.verify:
            cmd_verify()
        elif args.clean:
            cmd_clean()
        elif args.addc is not None:
            cmd_addc(args.addc)
        elif args.pin is not None:
            cmd_pin(args.pin)
        elif args.unpin is not None:
            cmd_unpin(args.unpin)
        elif args.note is not None:
            if len(args.note) < 2:
                console.print(
                    "[red]Error:[/red] --note requires a package name and a note. "
                    "Example: weld --note mypkg 'my note'"
                )
                raise SystemExit(1)
            cmd_note(args.note[0], " ".join(args.note[1:]))
        elif args.rename is not None:
            cmd_rename(args.rename[0], args.rename[1])
        elif args.duplicate is not None:
            cmd_duplicate(args.duplicate[0], args.duplicate[1])
        elif args.edit is not None:
            cmd_edit(args.edit)
        elif args.history is not None:
            cmd_history(args.history or None)
        elif args.stats:
            cmd_stats()
        elif args.doctor:
            cmd_doctor()
        elif args.template is not None:
            cmd_template(args.template)
        elif args.templates:
            cmd_template_list()
        elif args.completions is not None:
            if args.completions == "zsh":
                cmd_completions_zsh()
            else:
                console.print(
                    f"[red]Error:[/red] Unknown shell '{_esc(args.completions)}' "
                    "(supported: zsh)"
                )
                raise SystemExit(1)
        elif args.self_upgrade:
            cmd_self_upgrade(force=args.force, yes=args.yes)
        elif args.check_update:
            cmd_check_update()
        else:
            parser.print_help()

    except KeyboardInterrupt:
        _write(_SHOW_CURSOR)
        _print(f"\n{_YELLOW}Aborted.{_RESET}")
        raise SystemExit(1)
    except BrokenPipeError:
        # Output was piped into e.g. `head`; exit quietly.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        raise SystemExit(0)
    except OSError as exc:
        console.print(f"[red]Error:[/red] {_esc(str(exc))}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
