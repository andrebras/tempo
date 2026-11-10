"""The Tempo page and its small JSON API, on 127.0.0.1 only.

Any website open in the browser could send requests to a localhost port, so
every request must name this server as its Host, and writes must be JSON from
this page's own origin.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import traceback
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar
from urllib.parse import parse_qs, unquote, urlparse

from tempo import log
from tempo.store import QuestionError, Store, connect, db_path

HERE = os.path.dirname(os.path.abspath(__file__))
# The plugin folder this code runs from; /health names it so a stale server can be spotted.
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(HERE, "page.html")
SCRIPT = os.path.join(HERE, "page.js")
# The largest request body the page sends is a note; a megabyte leaves plenty of room.
MAX_BODY = 1_000_000


def make_handler(port: int) -> type[BaseHTTPRequestHandler]:
    hosts = frozenset({"127.0.0.1:%d" % port, "localhost:%d" % port})
    # Read once, when the server starts. A checkout updated under a running server must
    # not make it claim the new version, or serve the new page against its old code.
    started = health()
    with open(PAGE, "rb") as file:
        page_html = file.read()
    with open(SCRIPT, "rb") as file:
        page_script = file.read()

    class PortHandler(Handler):
        allowed_hosts = hosts
        allowed_origins = frozenset("http://" + host for host in hosts)
        about = started
        page = page_html
        script = page_script

    return PortHandler


class Handler(BaseHTTPRequestHandler):
    """Answers one request; make_handler fills in the port's hosts and the files read at
    start. Each path is looked up in ROUTES."""

    server_version = "tempo"
    # A client that stops sending mid-request frees its thread after this many seconds.
    timeout = 30
    allowed_hosts: ClassVar[frozenset[str]] = frozenset()
    allowed_origins: ClassVar[frozenset[str]] = frozenset()
    about: ClassVar[dict[str, Any]]
    page: ClassVar[bytes] = b""
    script: ClassVar[bytes] = b""

    def do_GET(self) -> None:
        if not self._trusted(write=False):
            return
        # Answered without the database, so a broken database cannot hide the server.
        if _parts(urlparse(self.path).path) == ["health"]:
            self._json(200, self.about)
            return
        self._safely(lambda store: self._route(store, "GET", {}))

    def do_POST(self) -> None:
        if not self._trusted(write=True):
            return
        self._safely(self._post)

    def _safely(self, run: Callable[[Store], None]) -> None:
        """Run a request with the database; an unexpected error, such as a locked
        database, answers 500 with JSON instead of dropping the connection. The
        traceback goes to stderr, which is the server log."""
        try:
            with Store(connect()) as store:
                run(store)
        except QuestionError as error:
            # Only connect() lets one through (each route answers its own with 400): the
            # database was changed by a newer Tempo, and the message says what to do.
            with contextlib.suppress(OSError):
                self._json(503, {"error": str(error)})
        except Exception:
            traceback.print_exc()
            with contextlib.suppress(OSError):
                self._json(500, {"error": "Something went wrong in Tempo"})

    def _post(self, store: Store) -> None:
        """Check the body first, so a bad body is refused whatever the path."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._json(400, {"error": "Invalid Content-Length"})
            return
        if length > MAX_BODY:
            self._json(413, {"error": "The request is too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "Invalid JSON"})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "The body must be a JSON object"})
            return
        self._route(store, "POST", body)

    def _route(self, store: Store, method: str, body: dict[str, Any]) -> None:
        parts = _parts(urlparse(self.path).path)
        for route_method, pattern, run in ROUTES:
            args = _match(pattern, parts)
            if route_method == method and args is not None:
                run(self, store, args, body)
                return
        self._json(404, {"error": "Not found"})

    def _save_setting(self, store: Store, change: Callable[[], dict[str, Any]]) -> None:
        """Make a change from Settings and answer with the new Settings state, plus
        whatever the change reports."""

        def run() -> dict[str, Any]:
            return {**change(), **_settings(store)}

        self._handle(run)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """One line in server.log for each request that is not a plain GET: method,
        path and status code. Never the body or the query, which may quote answers."""
        if self.command == "GET":
            return
        if isinstance(code, HTTPStatus):
            code = code.value
        path = urlparse(getattr(self, "path", "") or "").path
        log.write(self.command or "-", path or "-", code)

    def _trusted(self, write: bool) -> bool:
        if self.headers.get("Host") not in self.allowed_hosts:
            self._json(403, {"error": "Unknown host"})
            return False
        if write:
            origin = self.headers.get("Origin")
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if origin not in self.allowed_origins or content_type != "application/json":
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


# What a route runs: it gets the request, the database, the path's ANY parts in order,
# and the JSON body (empty for a GET), and sends the answer.
Run = Callable[[Handler, Store, list[str], dict[str, Any]], None]


def _page(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    request._send(200, request.page, "text/html; charset=utf-8")


def _script(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    request._send(200, request.script, "text/javascript; charset=utf-8")


def _questions(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    query = parse_qs(urlparse(request.path).query)
    status = query.get("status", ["open"])[0]
    project = query.get("project", [None])[0]
    # Projects whose folders have no questions yet are listed too, while a folder is left.
    store.forget_gone_folders()
    projects = [summary["name"] for summary in store.project_summaries()]
    request._json(
        200,
        {
            "questions": store.list(project, status),
            "projects": projects,
            # When each project last had news, for the header's "new" dot.
            "activity": store.project_activity(),
        },
    )


def _question(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    project, number = args
    request._handle(lambda: store.get(project, _int(number)))


def _get_settings(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    request._json(200, _settings(store))


def _answer(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    project, number = args
    request._handle(
        lambda: store.answer(
            project,
            _int(number),
            _text(body, "choice"),
            _text(body, "body", ""),
            # The wording the page showed, so an answer to an older one is refused.
            version=_number(body, "version"),
        )
    )


def _follow_up(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    project, number = args
    request._handle(lambda: store.follow_up(project, _int(number), _text(body, "body", "")))


def _reopen(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    project, number = args
    request._handle(lambda: store.reopen(project, _int(number)))


def _rename(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    (old,) = args
    request._save_setting(store, lambda: {"moved": store.rename_project(old, _text(body, "name"))})


def _start_page(request: Handler, store: Store, args: list[str], body: dict[str, Any]) -> None:
    request._save_setting(store, lambda: _set_start_page(store, body))


# A part of a route's path that matches any one part, which is passed to the route.
ANY = None

# Every path the server answers, besides /health: method, the path's parts, and what runs.
# A path that matches no route gets 404.
ROUTES: list[tuple[str, tuple[str | None, ...], Run]] = [
    ("GET", (), _page),
    ("GET", ("page.js",), _script),
    ("GET", ("api", "questions"), _questions),
    ("GET", ("api", "questions", ANY, ANY), _question),
    ("GET", ("api", "settings"), _get_settings),
    ("POST", ("api", "questions", ANY, ANY, "answer"), _answer),
    ("POST", ("api", "questions", ANY, ANY, "followup"), _follow_up),
    ("POST", ("api", "questions", ANY, ANY, "reopen"), _reopen),
    ("POST", ("api", "projects", ANY, "rename"), _rename),
    ("POST", ("api", "settings", "page"), _start_page),
]


def _match(pattern: tuple[str | None, ...], parts: list[str]) -> list[str] | None:
    """The parts that fill the pattern's ANY places, or None when the path does not fit."""
    if len(pattern) != len(parts):
        return None
    if any(want is not ANY and want != part for want, part in zip(pattern, parts)):
        return None
    return [part for want, part in zip(pattern, parts) if want is ANY]


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


def health() -> dict[str, Any]:
    """Who this server is: its code, and the database it shows, so a session on another
    database leaves it alone."""
    return {
        "app": "tempo",
        "version": version(),
        "commit": _commit(),
        "root": ROOT,
        "db": db_path(),
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
    """What the Settings screen shows: projects with their folders, and whether each
    session starts the page. Only folders still on disk are listed; a folder that no
    longer exists, or whose path isn't known yet, stays in the database but is hidden.
    An empty project whose folders are all gone is forgotten (forget_gone_folders)."""
    store.forget_gone_folders()
    projects = [
        dict(project, folders=[folder for folder in project["folders"] if _on_disk(folder)])
        for project in store.project_summaries()
    ]
    return {
        "projects": projects,
        "start_page": store.start_page(),
    }


def _on_disk(folder: dict[str, Any]) -> bool:
    return bool(folder["path"]) and os.path.exists(str(folder["path"]))


def _set_start_page(store: Store, body: Any) -> dict[str, Any]:
    """Settings' "Start the page with each session" switch, the same as `tempo config page`."""
    on = body.get("on")
    if not isinstance(on, bool):
        raise QuestionError("on must be true or false")
    store.set_start_page(on)
    return {}


def _text(body: dict[str, Any], key: str, default: str | None = None) -> Any:
    """A text field of a request body, or default when it is missing or null."""
    value = body.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise QuestionError("%s must be text" % key)
    return value


def _number(body: dict[str, Any], key: str) -> int | None:
    """A whole-number field of a request body, or None when it is missing or null."""
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuestionError("%s must be a number" % key)
    return int(value)


def _parts(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


def _int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = None
    # SQLite holds whole numbers of 64 bits; a bigger one could not even be looked up.
    if number is None or not -(2**63) <= number < 2**63:
        raise QuestionError("%s is not a question number" % value)
    return number
