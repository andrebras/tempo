import base64
import http.client
import io
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest import mock

from tempo import terminal
from tempo.server import ROOT, make_handler
from tempo.store import Store
from tests.helpers import DatabaseTest


class ServerBase(DatabaseTest):
    """A page server on a free port and the test's database, and requests to it."""

    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = make_handler(self.port)
        # shutdown() waits for the serving loop to check in, which it does once per poll
        # interval (half a second by default), so a short one keeps each test fast.
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.add()

    def request(
        self,
        method,
        path,
        body=None,
        host=None,
        origin="same",
        content_type="application/json",
        headers=None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {"Host": host or "127.0.0.1:%d" % self.port, **(headers or {})}
        if origin == "same":
            origin = "http://127.0.0.1:%d" % self.port
        if origin:
            headers["Origin"] = origin
        if content_type:
            headers["Content-Type"] = content_type
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def post_raw(self, path, data, length=None):
        """POST bytes as they are, with any Content-Length, and give up after two seconds."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.putrequest("POST", path, skip_host=True)
        connection.putheader("Host", "127.0.0.1:%d" % self.port)
        connection.putheader("Origin", "http://127.0.0.1:%d" % self.port)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(data) if length is None else length))
        connection.endheaders(data)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, json.loads(body)


class ServerTest(ServerBase):
    def test_serves_the_page_and_the_list(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Tempo</title>", page)
        self.assertIn(b'<script src="/page.js"></script>', page)
        status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(json.loads(data)["questions"][0]["label"], "Q1")

    def test_serves_the_script(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/page.js", headers={"Host": "127.0.0.1:%d" % self.port})
        response = connection.getresponse()
        script = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/javascript; charset=utf-8")
        self.assertIn(b"function rich(", script)

    def test_health_says_which_tempo_this_is(self):
        status, data = self.request("GET", "/health")
        self.assertEqual(status, 200)
        health = json.loads(data)
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as plugin:
            self.assertEqual(health["version"], json.load(plugin)["version"])
        self.assertEqual(health["app"], "tempo")
        self.assertEqual(health["root"], ROOT)
        self.assertEqual(health["pid"], os.getpid())
        self.assertRegex(health["started"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(health["db"], self.env["TEMPO_DB"])

    def test_health_and_page_files_are_read_when_the_server_starts(self):
        # A checkout updated under a running server must not make it claim the new
        # version, which would stop a newer session from replacing it, nor serve the
        # new page script against its old code.
        # The server in setUp has started but not been asked anything yet.
        newer = os.path.join(self.tmp.name, "page.js")
        with open(newer, "w") as file:
            file.write("// a newer script")
        with (
            mock.patch("tempo.server.version", return_value="9.9.9"),
            mock.patch("tempo.server.SCRIPT", newer),
        ):
            self.assertNotEqual(json.loads(self.request("GET", "/health")[1])["version"], "9.9.9")
            self.assertIn(b"function rich(", self.request("GET", "/page.js")[1])

    def test_a_body_that_is_not_an_object_is_refused(self):
        for data in (b"null", b"[]", b"5"):
            with self.subTest(data=data):
                status, reply = self.post_raw("/api/questions/demo/1/answer", data)
                self.assertEqual(status, 400)
                self.assertIn("error", reply)

    def test_fields_of_the_wrong_type_are_refused(self):
        for body in ({"choice": 1}, {"body": 5}, {"choice": "a", "body": ["x"]}):
            with self.subTest(body=body):
                status, reply = self.post_raw(
                    "/api/questions/demo/1/answer", json.dumps(body).encode()
                )
                self.assertEqual(status, 400)
                self.assertIn("must be text", reply["error"])
        for path, data in (
            ("/api/questions/demo/1/followup", b'{"body": 5}'),
            ("/api/projects/demo/rename", b'{"name": 5}'),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.post_raw(path, data)[0], 400)
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_a_database_changed_by_a_newer_tempo_says_so(self):
        log = mock.patch("sys.stderr", io.StringIO())
        log.start()
        self.addCleanup(log.stop)
        self.store.db.execute("PRAGMA user_version = 999")
        status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(status, 503)
        self.assertIn("last changed by a newer Tempo", json.loads(data)["error"])
        self.assertIn("Update Tempo", json.loads(data)["error"])

    def test_a_negative_or_huge_length_is_refused_without_waiting(self):
        status, _ = self.post_raw("/api/questions/demo/1/answer", b"", length=-1)
        self.assertEqual(status, 400)
        status, _ = self.post_raw("/api/questions/demo/1/answer", b"", length=10**9)
        self.assertEqual(status, 413)

    def test_a_length_that_is_not_a_number_or_a_body_that_is_not_json_is_refused(self):
        status, reply = self.post_raw("/api/questions/demo/1/answer", b"", length="ten")
        self.assertEqual((status, reply["error"]), (400, "Invalid Content-Length"))
        status, reply = self.post_raw("/api/questions/demo/1/answer", b'{"choice": "a"')
        self.assertEqual((status, reply["error"]), (400, "Invalid JSON"))
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_an_unexpected_error_answers_with_a_json_500(self):
        error = RuntimeError("database is locked")
        log = mock.patch("sys.stderr", io.StringIO())
        log.start()
        self.addCleanup(log.stop)
        with mock.patch.object(Store, "answer", side_effect=error):
            status, reply = self.post_raw("/api/questions/demo/1/answer", b'{"choice": "a"}')
        self.assertEqual((status, reply), (500, {"error": "Something went wrong in Tempo"}))
        with mock.patch.object(Store, "list", side_effect=error):
            status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(data), {"error": "Something went wrong in Tempo"})

    def test_a_number_too_big_for_sqlite_is_not_a_question_number(self):
        huge = "99999999999999999999"
        with mock.patch("sys.stderr", io.StringIO()) as log:
            status, reply = self.post_raw(
                "/api/questions/demo/%s/followup" % huge, b'{"body": "?"}'
            )
            self.assertEqual(self.request("GET", "/api/questions/demo/%s" % huge)[0], 400)
        self.assertEqual((status, reply), (400, {"error": "%s is not a question number" % huge}))
        # No traceback in the server log: it is the request that is wrong, not Tempo.
        self.assertEqual(log.getvalue(), "")

    def test_health_needs_this_server_as_its_host(self):
        self.assertEqual(self.request("GET", "/health", host="evil.example")[0], 403)

    def test_the_list_names_projects_without_questions_yet(self):
        self.store.see_folder("onzehq")
        status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["projects"], ["demo", "onzehq"])

    def test_the_list_says_when_each_project_last_had_news(self):
        status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(status, 200)
        activity = json.loads(data)["activity"]
        self.assertEqual(activity, self.store.project_activity())
        self.assertEqual(list(activity), ["demo"])

    def test_answers_a_question(self):
        status, data = self.request(
            "POST", "/api/questions/demo/1/answer", {"choice": "a", "body": ""}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["status"], "answered")
        self.assertEqual(len(self.store.undelivered("demo")), 1)

    def log(self):
        """server.log's lines, without their times."""
        path = os.path.join(self.tmp.name, "server.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as file:
            return [line.split(" ", 1)[1] for line in file.read().splitlines()]

    def test_logs_each_change_request_without_its_body(self):
        self.request("GET", "/api/questions")
        self.request("POST", "/api/questions/demo/1/answer", {"choice": "a", "body": "Secret note"})
        self.request("POST", "/api/questions/demo/1/answer?x=1", {"choice": "b"})
        self.request("POST", "/api/questions/demo/1/answer", {}, origin="http://evil.example")
        self.request("DELETE", "/api/questions/demo/1")
        self.assertEqual(
            self.log(),
            [
                "POST /api/questions/demo/1/answer 200",
                "POST /api/questions/demo/1/answer 400",
                "POST /api/questions/demo/1/answer 403",
                "DELETE /api/questions/demo/1 501",
            ],
        )

    def test_an_answer_to_the_wording_the_page_showed_is_checked(self):
        path = "/api/questions/demo/1/answer"
        self.store.reword("demo", 1, "Swapped the options", options=["Right", "Left"])
        status, data = self.request("POST", path, {"choice": "b", "version": 1})
        self.assertEqual(status, 400)
        self.assertIn("changed since you looked", json.loads(data)["error"])
        status, data = self.request("POST", path, {"choice": "b", "version": "2"})
        self.assertEqual((status, json.loads(data)["error"]), (400, "version must be a number"))
        self.assertEqual(self.store.get("demo", 1)["status"], "open")
        self.assertEqual(self.request("POST", path, {"choice": "b", "version": 2})[0], 200)

    def test_answering_twice_needs_a_reopen(self):
        path = "/api/questions/demo/1/"
        self.assertEqual(self.request("POST", path + "answer", {"choice": "a"})[0], 200)
        self.assertEqual(self.request("POST", path + "answer", {"choice": "b"})[0], 400)
        self.assertEqual(self.request("POST", path + "reopen", {})[0], 200)
        self.assertEqual(self.request("POST", path + "answer", {"choice": "b"})[0], 200)

    def test_reports_rule_errors(self):
        status, data = self.request("POST", "/api/questions/demo/1/followup", {"body": ""})
        self.assertEqual(status, 400)
        self.assertIn("follow-up", json.loads(data)["error"])
        status, _ = self.request("POST", "/api/questions/demo/abc/answer", {"choice": "a"})
        self.assertEqual(status, 400)

    def test_reads_and_saves_settings(self):
        self.store.see_folder("onzehq")
        status, data = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(data)["projects"],
            [
                {"name": "demo", "questions": 1, "folders": []},
                {
                    "name": "onzehq",
                    "questions": 0,
                    "folders": [],
                },
            ],
        )
        status, data = self.request("POST", "/api/projects/onzehq/rename", {"name": "demo"})
        self.assertEqual(status, 200)
        status, data = self.request("POST", "/api/projects/demo/rename", {"name": "ONZE HQ"})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(data)["projects"],
            [
                {
                    "name": "ONZE HQ",
                    "questions": 1,
                    "folders": [],
                }
            ],
        )
        self.assertEqual(self.store.get("ONZE HQ", 1)["label"], "Q1")
        self.assertEqual(self.store.project_for("onzehq"), "ONZE HQ")

    def test_settings_mark_folders_that_are_gone_and_hide_those_with_no_path(self):
        here = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, here)
        self.store.see_folder("tempo", here)
        self.store.see_folder("old", "/nowhere/old")
        self.store.see_folder("open-questions")
        self.store.rename_project("old", "tempo")
        self.store.rename_project("open-questions", "tempo")
        _, data = self.request("GET", "/api/settings")
        [tempo] = [p for p in json.loads(data)["projects"] if p["name"] == "tempo"]
        self.assertEqual(
            [(folder["path"], folder["gone"]) for folder in tempo["folders"]],
            [("/nowhere/old", True), (here, False)],
        )
        # Hidden, not deleted.
        self.assertEqual(len(self.store.folders()), 3)

    def test_empty_projects_whose_folders_are_gone_are_no_longer_listed(self):
        here = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, here)
        self.store.see_folder("fork", "/nowhere/scratchpad/fork")
        self.store.see_folder("tempo", here)
        _, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(json.loads(data)["projects"], ["demo", "tempo"])
        _, data = self.request("GET", "/api/settings")
        self.assertEqual([p["name"] for p in json.loads(data)["projects"]], ["demo", "tempo"])
        self.assertEqual([folder["path"] for folder in self.store.folders()], [here])

    def test_turns_the_page_off_and_on_for_new_sessions(self):
        _, data = self.request("GET", "/api/settings")
        self.assertTrue(json.loads(data)["start_page"])
        status, data = self.request("POST", "/api/settings/page", {"on": False})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["start_page"])
        self.assertFalse(self.store.start_page())
        status, data = self.request("POST", "/api/settings/page", {"on": True})
        self.assertTrue(json.loads(data)["start_page"])
        self.assertTrue(self.store.start_page())
        for body in ({}, {"on": "off"}, {"on": 0}):
            with self.subTest(body=body):
                status, data = self.request("POST", "/api/settings/page", body)
                self.assertEqual(status, 400)
                self.assertIn("true or false", json.loads(data)["error"])
        self.assertTrue(self.store.start_page())

    def test_turns_the_terminal_on_and_off(self):
        _, data = self.request("GET", "/api/settings")
        self.assertFalse(json.loads(data)["terminal"])
        status, data = self.request("POST", "/api/settings/terminal", {"on": True})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["terminal"])
        self.assertTrue(self.store.terminal())
        self.assertEqual(self.request("POST", "/api/settings/terminal", {"on": "on"})[0], 400)
        self.request("POST", "/api/settings/terminal", {"on": False})
        self.assertFalse(self.store.terminal())

    def test_reports_settings_errors(self):
        self.add(project="other")
        status, data = self.request("POST", "/api/projects/demo/rename", {"name": " "})
        self.assertEqual(status, 400)
        self.assertIn("needs a name", json.loads(data)["error"])

    def test_joining_projects_reports_the_moved_questions(self):
        self.add(project="other")
        status, data = self.request("POST", "/api/projects/demo/rename", {"name": "other"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["moved"], [{"from": 1, "to": 2}])
        self.assertEqual(json.loads(data)["projects"][0]["questions"], 2)

    def test_refuses_other_hosts(self):
        for path in ["/", "/page.js", "/api/questions"]:
            with self.subTest(path=path):
                status, _ = self.request("GET", path, host="evil.example:%d" % self.port)
                self.assertEqual(status, 403)

    def test_refuses_writes_from_other_sites(self):
        for origin, content_type in [
            ("https://evil.example", "application/json"),
            (None, "application/json"),
            ("same", "text/plain"),
        ]:
            with self.subTest(origin=origin, content_type=content_type):
                status, _ = self.request(
                    "POST",
                    "/api/questions/demo/1/answer",
                    {"choice": "a"},
                    origin=origin,
                    content_type=content_type,
                )
                self.assertEqual(status, 403)
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_refuses_settings_writes_from_other_sites(self):
        self.store.see_folder("onzehq")
        for path, body in [
            ("/api/projects/demo/rename", {"name": "Hijacked"}),
            ("/api/settings/page", {"on": False}),
        ]:
            with self.subTest(path=path):
                status, _ = self.request("POST", path, body, origin="https://evil.example")
                self.assertEqual(status, 403)
        self.assertEqual(self.store.projects(), ["demo"])
        self.assertEqual(self.store.project_for("onzehq"), "onzehq")
        self.assertTrue(self.store.start_page())

    def test_threads_are_gone(self):
        for method, path in [
            ("GET", "/threads"),
            ("GET", "/api/threads"),
            ("POST", "/api/threads"),
        ]:
            with self.subTest(path=path):
                status, _ = self.request(method, path, {} if method == "POST" else None)
                self.assertEqual(status, 404)
        self.assertNotIn("threads", json.loads(self.request("GET", "/api/settings")[1]))

    def test_renamed_folders_are_no_longer_offered(self):
        for path in ("/api/folders/link", "/api/folders/keep"):
            with self.subTest(path=path):
                status, _ = self.request("POST", path, {"path": "/x", "project": "x"})
                self.assertEqual(status, 404)
        for path in ("/api/questions", "/api/settings"):
            with self.subTest(path=path):
                self.assertNotIn("renames", json.loads(self.request("GET", path)[1]))


class TerminalTest(ServerBase):
    """The page's terminal: a shell in the project's folder, behind a setting and a secret."""

    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, self.folder)
        self.store.see_folder("demo", self.folder)
        self.store.set_terminal(True)
        # /bin/sh starts fast and the same everywhere, unlike the user's own shell.
        shell = mock.patch.dict(os.environ, {"SHELL": "/bin/sh", "PS1": "$ "})
        shell.start()
        self.addCleanup(shell.stop)
        self.addCleanup(terminal.SHELLS.close_all)
        self.secret = self.server.RequestHandlerClass.terminal_secret

    log = ServerTest.log

    def terminal(self, path, body=None, secret=None, **options):
        headers = {"X-Tempo-Secret": self.secret if secret is None else secret}
        method = "GET" if body is None else "POST"
        status, data = self.request(method, path, body, headers=headers, **options)
        return status, json.loads(data)

    def open(self):
        status, data = self.terminal("/api/terminal", {"project": "demo"})
        self.assertEqual(status, 200, data)
        return data["id"]

    def read(self, key):
        """Start reading a shell's output: the connection and its response."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": "127.0.0.1:%d" % self.port, "X-Tempo-Secret": self.secret}
        connection.request("GET", "/api/terminal/%s/output" % key, headers=headers)
        self.addCleanup(connection.close)
        return connection, connection.getresponse()

    def until(self, response, text):
        """The decoded output and the event lines, read until text appears or it ends."""
        seen, events = b"", []
        while text.encode() not in seen:
            line = response.readline()
            if not line:
                break
            events.append(line)
            if line.startswith(b"data: "):
                seen += base64.b64decode(line[6:].strip())
        return seen, events

    def ended(self, key, shell):
        """Whether the shell's process ended and it is no longer listed, within 5 seconds."""
        deadline = time.monotonic() + 5
        while shell.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return shell.process.poll() is not None and terminal.SHELLS.get(key) is None

    def test_the_page_carries_a_new_secret_each_start(self):
        _, page = self.request("GET", "/")
        self.assertIn(b'<meta name="tempo-secret" content="%s">' % self.secret.encode(), page)
        self.assertGreaterEqual(len(self.secret), 40)
        self.assertNotEqual(make_handler(self.port).terminal_secret, self.secret)

    def test_a_shell_runs_in_the_projects_folder_and_runs_what_is_typed(self):
        key = self.open()
        _, response = self.read(key)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        resize = self.terminal("/api/terminal/%s/resize" % key, {"cols": 90, "rows": 20})
        self.assertEqual(resize, (200, {}))
        typed = {"data": "echo hi$((1+1)); pwd -P; stty size\n"}
        self.assertEqual(self.terminal("/api/terminal/%s/input" % key, typed), (200, {}))
        seen, _ = self.until(response, "20 90")
        # The sum shows the shell ran the line, not only echoed the keys.
        self.assertIn(b"hi2", seen)
        self.assertIn(os.path.realpath(self.folder).encode(), seen)
        self.assertIn(b"20 90", seen)

    def test_each_request_needs_the_secret(self):
        key = self.open()
        for secret in ("", "wrong", self.secret[:-1]):
            for path, body in [
                ("/api/terminal", {"project": "demo"}),
                ("/api/terminal/%s/input" % key, {"data": "echo x > made\n"}),
                ("/api/terminal/%s/close" % key, {}),
                ("/api/terminal/%s/output" % key, None),
            ]:
                with self.subTest(secret=secret, path=path):
                    status, data = self.terminal(path, body, secret=secret)
                    self.assertEqual(status, 403)
                    self.assertIn("Only this page", data["error"])
        self.assertEqual(list(terminal.SHELLS.shells), [key])
        time.sleep(0.2)
        self.assertEqual(os.listdir(self.folder), [])

    def test_writes_need_this_pages_origin_and_host_even_with_the_secret(self):
        body = {"project": "demo"}
        self.assertEqual(
            self.terminal("/api/terminal", body, origin="https://evil.example")[0], 403
        )
        self.assertEqual(self.terminal("/api/terminal", body, host="evil.example")[0], 403)
        self.assertEqual(terminal.SHELLS.shells, {})

    def test_with_the_setting_off_no_shell_starts(self):
        self.store.set_terminal(False)
        status, data = self.terminal("/api/terminal", {"project": "demo"})
        self.assertEqual(status, 400)
        self.assertIn("tempo config terminal on", data["error"])
        self.assertEqual(terminal.SHELLS.shells, {})

    def test_a_project_with_no_folder_on_disk_gets_no_shell(self):
        self.store.see_folder("gone", "/nowhere/gone")
        status, data = self.terminal("/api/terminal", {"project": "gone"})
        self.assertEqual((status, data["error"]), (400, "No folder of gone is on this computer"))

    def test_the_log_has_opening_and_ending_but_not_each_key(self):
        key = self.open()
        self.terminal("/api/terminal/%s/input" % key, {"data": "l"})
        self.terminal("/api/terminal/%s/resize" % key, {"cols": 80, "rows": 24})
        self.terminal("/api/terminal/%s/input" % key, {"data": "s"}, secret="wrong")
        self.terminal("/api/terminal/%s/close" % key, {})
        self.assertEqual(
            self.log(),
            [
                "POST /api/terminal 200",
                "POST /api/terminal/%s/input 403" % key,
                "POST /api/terminal/%s/close 200" % key,
            ],
        )

    def test_bad_sizes_and_unknown_shells_are_refused(self):
        key = self.open()
        for body in ({"cols": 0, "rows": 20}, {"cols": 80}, {"cols": 80, "rows": 5000}):
            with self.subTest(body=body):
                self.assertEqual(self.terminal("/api/terminal/%s/resize" % key, body)[0], 400)
        status, data = self.terminal("/api/terminal/nope/input", {"data": "x"})
        self.assertEqual((status, data["error"]), (404, "That terminal has ended"))

    def test_only_one_page_reads_a_shell(self):
        key = self.open()
        self.read(key)
        status, data = self.terminal("/api/terminal/%s/output" % key)
        self.assertEqual(status, 409)
        self.assertIn("another tab", data["error"])

    def test_the_shell_stops_when_the_page_stops_reading(self):
        key = self.open()
        shell = terminal.SHELLS.get(key)
        connection, response = self.read(key)
        self.terminal("/api/terminal/%s/input" % key, {"data": "echo ready\n"})
        self.until(response, "ready")
        response.close()
        connection.close()
        # Noticed at the next write: new output, or the next "still here".
        self.terminal("/api/terminal/%s/input" % key, {"data": "echo more\n"})
        self.assertTrue(self.ended(key, shell))

    def test_end_shell_stops_it_and_ends_the_output(self):
        key = self.open()
        shell = terminal.SHELLS.get(key)
        _, response = self.read(key)
        self.assertEqual(self.terminal("/api/terminal/%s/close" % key, {}), (200, {}))
        _, events = self.until(response, "never printed")
        self.assertEqual(events[-3:], [b"event: end\n", b"data:\n", b"\n"])
        self.assertTrue(self.ended(key, shell))

    def test_typing_exit_ends_the_output_and_the_shell(self):
        key = self.open()
        shell = terminal.SHELLS.get(key)
        _, response = self.read(key)
        self.terminal("/api/terminal/%s/input" % key, {"data": "exit\n"})
        _, events = self.until(response, "never printed")
        self.assertIn(b"event: end\n", events)
        self.assertTrue(self.ended(key, shell))

    def test_a_shell_nobody_reads_is_stopped(self):
        with mock.patch.object(terminal, "ATTACH", 0.1):
            key = self.open()
            shell = terminal.SHELLS.get(key)
        self.assertTrue(self.ended(key, shell))

    def test_turning_the_terminal_off_in_settings_stops_every_shell(self):
        shells = [terminal.SHELLS.get(self.open()) for _ in range(2)]
        self.request("POST", "/api/settings/terminal", {"on": False})
        self.assertEqual(terminal.SHELLS.shells, {})
        self.assertTrue(all(shell.process.poll() is not None for shell in shells))
