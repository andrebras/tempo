"""The Tempo page and its small JSON API, on 127.0.0.1 only.

Any website open in the browser could send requests to a localhost port, so
every request must name this server as its Host, and writes must be JSON from
this page's own origin.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import subprocess
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from tempo import threads
from tempo.store import QuestionError, Store, connect

HERE = os.path.dirname(os.path.abspath(__file__))
# The plugin folder this code runs from; /health names it so a stale server can be spotted.
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(HERE, "page.html")
SCRIPT = os.path.join(HERE, "page.js")
THREADS_PAGE = os.path.join(HERE, "threads.html")


def make_handler(port: int) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    allowed_origins = {"http://" + host for host in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "tempo"

        def do_GET(self) -> None:
            if not self._trusted(write=False):
                return
            # Answered without the database, so a broken database cannot hide the server.
            if _parts(urlparse(self.path).path) == ["health"]:
                self._json(200, health())
                return
            with Store(connect()) as store:
                self._get(store)

        def do_POST(self) -> None:
            if not self._trusted(write=True):
                return
            with Store(connect()) as store:
                self._post(store)

        def _get(self, store: Store) -> None:
            url = urlparse(self.path)
            parts = _parts(url.path)
            if parts == []:
                with open(PAGE, "rb") as page:
                    self._send(200, page.read(), "text/html; charset=utf-8")
            elif parts == ["page.js"]:
                with open(SCRIPT, "rb") as script:
                    self._send(200, script.read(), "text/javascript; charset=utf-8")
            elif parts == ["api", "questions"]:
                query = parse_qs(url.query)
                status = query.get("status", ["open"])[0]
                project = query.get("project", [None])[0]
                # Projects whose folders have no questions yet are listed too.
                projects = [summary["name"] for summary in store.project_summaries()]
                # New folders that may be renamed ones: the banner shows on every screen.
                self._json(
                    200,
                    {
                        "questions": store.list(project, status),
                        "projects": projects,
                        "renames": store.renames(),
                    },
                )
            elif len(parts) == 4 and parts[:2] == ["api", "questions"]:
                self._handle(lambda: store.get(parts[2], _int(parts[3])))
            elif parts == ["api", "settings"]:
                self._json(200, _settings(store))
            elif not threads.enabled() or not _threads_path(parts):
                self._json(404, {"error": "Not found"})
            elif parts == ["threads"]:
                with open(THREADS_PAGE, "rb") as page:
                    self._send(200, page.read(), "text/html; charset=utf-8")
            elif parts == ["api", "threads"]:
                self._json(200, {"threads": threads.threads()})
            elif len(parts) == 3:
                self._handle(lambda: threads.thread(_int(parts[2])))
            else:
                self._json(404, {"error": "Not found"})

        def _post(self, store: Store) -> None:
            parts = _parts(urlparse(self.path).path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": "Invalid JSON"})
                return
            if len(parts) == 5 and parts[:2] == ["api", "questions"]:
                self._question_action(store, parts[2], parts[3], parts[4], body)
            elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "rename":
                self._save_setting(
                    store, lambda: {"moved": store.rename_project(parts[2], body.get("name"))}
                )
            elif parts == ["api", "folders", "link"]:
                self._save_setting(store, lambda: _link(store, body))
            elif parts == ["api", "folders", "keep"]:
                self._save_setting(store, lambda: _keep(store, body))
            elif not threads.enabled() or not _threads_path(parts):
                self._json(404, {"error": "Not found"})
            elif parts == ["api", "threads"]:
                self._handle(lambda: threads.start(body.get("folder"), body.get("body")))
            elif len(parts) == 3:
                self._handle(lambda: threads.reply(_int(parts[2]), body.get("body")))
            else:
                self._json(404, {"error": "Not found"})

        def _question_action(
            self, store: Store, project: str, number: str, action: str, body: Any
        ) -> None:
            if action == "answer":
                self._handle(
                    lambda: store.answer(
                        project, _int(number), body.get("choice"), body.get("body", "")
                    )
                )
            elif action == "followup":
                self._handle(lambda: store.follow_up(project, _int(number), body.get("body", "")))
            elif action == "reopen":
                self._handle(lambda: store.reopen(project, _int(number)))
            else:
                self._json(404, {"error": "Not found"})

        def _save_setting(self, store: Store, change: Callable[[], dict[str, Any]]) -> None:
            """Make a change from Settings and answer with the new Settings state, plus
            whatever the change reports."""

            def run() -> dict[str, Any]:
                return {**change(), **_settings(store)}

            self._handle(run)

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _trusted(self, write: bool) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(403, {"error": "Unknown host"})
                return False
            if write:
                origin = self.headers.get("Origin")
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if origin not in allowed_origins or content_type != "application/json":
                    self._json(403, {"error": "Writes must come from this page"})
                    return False
            return True

        def _handle(self, action: Callable[[], Any]) -> None:
            try:
                self._json(200, action())
            except QuestionError as error:
                self._json(400, {"error": str(error)})

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(port))
    print("Tempo on http://127.0.0.1:%d/" % port, flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


def version() -> str | None:
    """The plugin's version, from the same plugin.json that bin/check-version reads."""
    try:
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as file:
            return str(json.load(file)["version"])
    except (OSError, ValueError, KeyError):
        return None


_STARTED = time.gmtime()


@functools.cache
def health() -> dict[str, Any]:
    """Who this server is, worked out once on the first /health request."""
    return {
        "app": "tempo",
        "version": version(),
        "commit": _commit(),
        "root": ROOT,
        "pid": os.getpid(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", _STARTED),
    }


def _commit() -> str | None:
    """The short git commit, or None when the code is not a git checkout (the plugin cache)."""
    if not os.path.exists(os.path.join(ROOT, ".git")):
        return None
    try:
        # git is looked up on PATH on purpose; the arguments are fixed.
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _settings(store: Store) -> dict[str, Any]:
    """What the Settings screen shows: projects with their folders, and new folders that
    may be renamed ones."""
    return {
        "projects": store.project_summaries(),
        "renames": store.renames(),
        "threads": threads.enabled(),
    }


def _link(store: Store, body: Any) -> dict[str, Any]:
    """File a folder under a project, from the rename banner or Settings."""
    store.link_folder(str(body.get("path") or ""), body.get("project"))
    return {}


def _keep(store: Store, body: Any) -> dict[str, Any]:
    """The banner's "keep it apart": the folder stays its own project."""
    store.keep_folder(str(body.get("path") or ""))
    return {}


def _threads_path(parts: list[str]) -> bool:
    """The threads page and its API, which answer only while TEMPO_THREADS=1 is set."""
    return parts == ["threads"] or parts[:2] == ["api", "threads"]


def _parts(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise QuestionError("%s is not a question number" % value) from None
