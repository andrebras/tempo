import io
import json
import threading
import time
from unittest import mock

from tempo import permissions, threads
from tempo.store import QuestionError, now
from tests.helpers import DatabaseTest


class PermissionsTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(permissions, "POLL", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)
        with threads.open_db() as db:
            self.thread_id = db.execute(
                "INSERT INTO threads (folder, title, created_at, updated_at)"
                " VALUES ('/x', 'Deploy', ?, ?)",
                (now(), now()),
            ).lastrowid

    def answer_when_asked(self, allow):
        """Click Allow or Deny on the page as soon as a request shows in the thread."""

        def click():
            for _ in range(500):
                pending = threads.thread(self.thread_id)["permissions"]
                if pending:
                    threads.decide(self.thread_id, pending[0]["id"], allow)
                    return
                time.sleep(0.01)

        clicker = threading.Thread(target=click)
        clicker.start()
        self.addCleanup(clicker.join)

    def test_allow_lets_the_call_run_as_asked(self):
        self.answer_when_asked(True)
        tool_input = {"command": "curl -sI https://example.com"}
        answer = permissions.ask(self.thread_id, "Bash", tool_input)
        self.assertEqual(answer, {"behavior": "allow", "updatedInput": tool_input})
        self.assertEqual(threads.thread(self.thread_id)["permissions"], [])

    def test_deny_tells_claude_the_user_said_no(self):
        self.answer_when_asked(False)
        answer = permissions.ask(self.thread_id, "Bash", {"command": "rm -rf build"})
        self.assertEqual(answer["behavior"], "deny")
        self.assertIn("denied", answer["message"])

    def test_no_answer_in_time_denies(self):
        answer = permissions.ask(self.thread_id, "Bash", {"command": "ls"}, timeout=0)
        self.assertEqual(answer["behavior"], "deny")
        self.assertIn("in time", answer["message"])
        with self.assertRaises(QuestionError):
            threads.decide(self.thread_id, 1, True)

    def test_the_page_sees_the_request(self):
        with threads.open_db() as db:
            db.execute(
                "INSERT INTO thread_permissions (thread_id, tool, summary, created_at)"
                " VALUES (?, 'Bash', 'npm publish', ?)",
                (self.thread_id, now()),
            )
        (request,) = threads.thread(self.thread_id)["permissions"]
        self.assertEqual((request["tool"], request["summary"]), ("Bash", "npm publish"))
        self.assertEqual(threads.threads()[0]["asking"], 1)

    def test_speaks_mcp_over_stdin_and_stdout(self):
        messages = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "v"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "approve",
                    "arguments": {"tool_name": "Bash", "input": {"command": "ls"}},
                },
            },
            {"jsonrpc": "2.0", "id": 3, "method": "server/discover"},
        ]
        stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
        stdout = io.StringIO()
        deny = {"behavior": "deny", "message": "No"}
        with (
            mock.patch.object(permissions, "ask", return_value=deny) as ask,
            mock.patch.object(permissions.threading, "Thread", inline_thread),
        ):
            permissions.serve(self.thread_id, stdin, stdout)
        ask.assert_called_once_with(self.thread_id, "Bash", {"command": "ls"})
        replies = {r["id"]: r for r in map(json.loads, stdout.getvalue().splitlines())}
        self.assertEqual(sorted(replies), [0, 1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "v")
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "approve")
        self.assertEqual(json.loads(replies[2]["result"]["content"][0]["text"]), deny)
        self.assertIn("error", replies[3])

    def test_a_thread_run_uses_the_prompt_tool_and_ends_leftover_requests(self):
        with threads.open_db() as db:
            db.execute(
                "INSERT INTO thread_permissions (thread_id, tool, summary, created_at)"
                " VALUES (?, 'Bash', 'ls', ?)",
                (self.thread_id, now()),
            )
        result = json.dumps({"type": "result", "result": "Done", "session_id": "s"})
        done = mock.Mock(stdout=result + "\n", stderr="")
        with mock.patch.object(threads.subprocess, "run", return_value=done) as run:
            threads._run(self.thread_id, self.tmp.name, None, "Go")
        command = run.call_args.args[0]
        self.assertEqual(
            command[command.index("--permission-prompt-tool") + 1], "mcp__tempo_threads__approve"
        )
        config = json.loads(command[command.index("--mcp-config") + 1])
        server = config["mcpServers"]["tempo_threads"]
        self.assertEqual(server["args"][:2], ["-m", "tempo.permissions"])
        self.assertEqual(server["env"]["TEMPO_DB"], self.env["TEMPO_DB"])
        self.assertEqual(threads.thread(self.thread_id)["permissions"], [])


def inline_thread(target, args, daemon):
    run = mock.Mock()
    run.start = lambda: target(*args)
    return run
