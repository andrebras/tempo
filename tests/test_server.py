import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from tempo.server import make_handler
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
        status, data = self.request("GET", "/api/questions?status=open")
        self.assertEqual(json.loads(data)["questions"][0]["label"], "Q1")

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

    def test_refuses_other_hosts(self):
        status, _ = self.request("GET", "/api/questions", host="evil.example:%d" % self.port)
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
