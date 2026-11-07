import http.client
import io
import json
import os
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest import mock

from tempo.server import ROOT, make_handler
from tempo.store import Store
from tests.helpers import DatabaseTest


class ServerTest(DatabaseTest):
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
        self, method, path, body=None, host=None, origin="same", content_type="application/json"
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {"Host": host or "127.0.0.1:%d" % self.port}
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
            ("/api/folders/link", b'{"path": "/x", "project": 5}'),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.post_raw(path, data)[0], 400)
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_a_negative_or_huge_length_is_refused_without_waiting(self):
        status, _ = self.post_raw("/api/questions/demo/1/answer", b"", length=-1)
        self.assertEqual(status, 400)
        status, _ = self.post_raw("/api/questions/demo/1/answer", b"", length=10**9)
        self.assertEqual(status, 413)

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

    def test_settings_hide_folders_that_are_gone_or_have_no_path(self):
        here = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, here)
        self.store.see_folder("tempo", here)
        self.store.see_folder("old", "/nowhere/old")
        self.store.see_folder("open-questions")
        self.store.rename_project("old", "tempo")
        self.store.rename_project("open-questions", "tempo")
        _, data = self.request("GET", "/api/settings")
        [tempo] = [p for p in json.loads(data)["projects"] if p["name"] == "tempo"]
        self.assertEqual([folder["path"] for folder in tempo["folders"]], [here])
        # Hidden, not deleted.
        self.assertEqual(len(self.store.folders()), 3)

    def test_offers_a_renamed_folder_on_every_screen_and_links_it(self):
        self.store.see_folder("open-questions", "/lab/open-questions")
        self.store.see_folder("tempo", "/lab/tempo")
        self.store.spot_rename("/lab/tempo", gone=lambda path: path == "/lab/open-questions")
        _, data = self.request("GET", "/api/questions")
        [rename] = json.loads(data)["renames"]
        self.assertEqual(rename["candidates"][0]["project"], "open-questions")
        status, data = self.request(
            "POST", "/api/folders/link", {"path": "/lab/tempo", "project": "open-questions"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["renames"], [])
        self.assertEqual(self.store.project_for("tempo", "/lab/tempo"), "open-questions")

    def test_keeps_a_new_folder_apart(self):
        self.store.see_folder("open-questions", "/lab/open-questions")
        self.store.see_folder("tempo", "/lab/tempo")
        self.store.spot_rename("/lab/tempo", gone=lambda path: True)
        status, data = self.request("POST", "/api/folders/keep", {"path": "/lab/tempo"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["renames"], [])
        _, data = self.request("GET", "/api/questions")
        self.assertEqual(json.loads(data)["renames"], [])
        status, data = self.request(
            "POST", "/api/folders/link", {"path": "/nowhere", "project": "x"}
        )
        self.assertEqual(status, 400)

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
            ("/api/folders/link", {"path": "/x/onzehq", "project": "Hijacked"}),
        ]:
            with self.subTest(path=path):
                status, _ = self.request("POST", path, body, origin="https://evil.example")
                self.assertEqual(status, 403)
        self.assertEqual(self.store.projects(), ["demo"])
        self.assertEqual(self.store.project_for("onzehq"), "onzehq")

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
