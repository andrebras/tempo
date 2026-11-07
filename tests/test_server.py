import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer

from tempo.server import ROOT, make_handler
from tests.helpers import DatabaseTest


class ServerTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = make_handler(self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
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

    def test_health_needs_this_server_as_its_host(self):
        self.assertEqual(self.request("GET", "/health", host="evil.example")[0], 403)

    def test_the_list_names_projects_without_questions_yet(self):
        self.store.see_folder("onzehq")
        status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["projects"], ["demo", "onzehq"])

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
                {"name": "onzehq", "questions": 0, "folders": ["onzehq"]},
            ],
        )
        status, data = self.request("POST", "/api/projects/onzehq/rename", {"name": "demo"})
        self.assertEqual(status, 200)
        status, data = self.request("POST", "/api/projects/demo/rename", {"name": "ONZE HQ"})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(data)["projects"],
            [{"name": "ONZE HQ", "questions": 1, "folders": ["onzehq"]}],
        )
        self.assertEqual(self.store.get("ONZE HQ", 1)["label"], "Q1")
        self.assertEqual(self.store.project_for("onzehq"), "ONZE HQ")

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
        ]:
            with self.subTest(path=path):
                status, _ = self.request("POST", path, body, origin="https://evil.example")
                self.assertEqual(status, 403)
        self.assertEqual(self.store.projects(), ["demo"])
        self.assertEqual(self.store.project_for("onzehq"), "onzehq")
