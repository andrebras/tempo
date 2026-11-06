"""The Tempo page and its small JSON API, on 127.0.0.1 only.

Any website open in the browser could send requests to a localhost port, so
every request must name this server as its Host, and writes must be JSON from
this page's own origin.
"""

import contextlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from tempo.store import QuestionError, Store, connect

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")


def make_handler(port):
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    allowed_origins = {"http://" + host for host in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "tempo"

        def do_GET(self):
            if not self._trusted(write=False):
                return
            with Store(connect()) as store:
                self._get(store)

        def do_POST(self):
            if not self._trusted(write=True):
                return
            with Store(connect()) as store:
                self._post(store)

        def _get(self, store):
            url = urlparse(self.path)
            parts = _parts(url.path)
            if parts == []:
                with open(PAGE, "rb") as page:
                    self._send(200, page.read(), "text/html; charset=utf-8")
            elif parts == ["api", "questions"]:
                query = parse_qs(url.query)
                status = query.get("status", ["open"])[0]
                project = query.get("project", [None])[0]
                self._json(
                    200, {"questions": store.list(project, status), "projects": store.projects()}
                )
            elif len(parts) == 4 and parts[:2] == ["api", "questions"]:
                self._handle(lambda: store.get(parts[2], _int(parts[3])))
            elif parts == ["api", "settings"]:
                self._json(200, _settings(store))
            else:
                self._json(404, {"error": "Not found"})

        def _post(self, store):
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
                self._save_setting(store, lambda: store.rename_project(parts[2], body.get("name")))
            elif len(parts) == 3 and parts[:2] == ["api", "folders"]:
                self._save_setting(
                    store, lambda: store.set_folder_project(parts[2], body.get("project"))
                )
            else:
                self._json(404, {"error": "Not found"})

        def _question_action(self, store, project, number, action, body):
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

        def _save_setting(self, store, change):
            """Make a change from Settings and answer with the new Settings state."""

            def run():
                change()
                return _settings(store)

            self._handle(run)

        def log_message(self, format, *args):
            pass

        def _trusted(self, write):
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

        def _handle(self, action):
            try:
                self._json(200, action())
            except QuestionError as error:
                self._json(400, {"error": str(error)})

        def _json(self, status, data):
            self._send(status, json.dumps(data).encode(), "application/json")

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(port):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(port))
    print("Tempo on http://127.0.0.1:%d/" % port, flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


def _settings(store):
    """What the Settings screen shows: projects with their folders, and every folder seen."""
    return {"projects": store.project_summaries(), "folders": store.folders()}


def _parts(path):
    return [unquote(part) for part in path.split("/") if part]


def _int(value):
    try:
        return int(value)
    except ValueError:
        raise QuestionError("%s is not a question number" % value) from None
