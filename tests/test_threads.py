import json
import subprocess
from unittest import mock

from tempo import threads
from tests.helpers import DatabaseTest


def stream(*events):
    """What `claude -p --output-format stream-json --verbose` prints: one event per line."""
    return "\n".join(json.dumps(event) for event in events) + "\n"


def tool_use(id, name, tool_input, parent=None):
    content = [{"type": "tool_use", "id": id, "name": name, "input": tool_input}]
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"content": content}}


def tool_result(id, content, is_error=False, parent=None):
    block = {"type": "tool_result", "tool_use_id": id, "content": content, "is_error": is_error}
    return {"type": "user", "parent_tool_use_id": parent, "message": {"content": [block]}}


RESULT = {"type": "result", "result": "Done: the tests pass.", "session_id": "s-1"}


class ThreadsTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        # Threads run Claude in the background; here each run happens on the spot.
        patcher = mock.patch.object(threads.threading, "Thread", self.inline_thread)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = stream(RESULT)

    def inline_thread(self, target, args, daemon):
        run = mock.Mock()
        run.start = lambda: target(*args)
        return run

    def run_claude(self, command, **kwargs):
        self.command = command
        return subprocess.CompletedProcess(command, 0, self.output, "")

    def start(self, text="Run the tests"):
        with mock.patch.object(threads.subprocess, "run", self.run_claude):
            started = threads.start(self.tmp.name, text)
        return threads.thread(started["id"])

    def test_off_unless_set(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(threads.enabled())
        with mock.patch.dict("os.environ", {"TEMPO_THREADS": "1"}):
            self.assertTrue(threads.enabled())

    def test_keeps_the_reply_and_the_steps(self):
        self.output = stream(
            {"type": "system", "subtype": "init"},
            tool_use("t1", "Bash", {"command": "bin/ci", "description": "Run CI"}),
            tool_result("t1", "PASS"),
            tool_use("t2", "Read", {"file_path": "/x/a.py"}),
            tool_result("t2", [{"type": "text", "text": "line one"}], is_error=True),
            RESULT,
        )
        thread = self.start()
        self.assertIn("stream-json", self.command)
        self.assertIn("--verbose", self.command)
        self.assertEqual(thread["status"], "waiting")
        self.assertEqual(thread["session_id"], "s-1")
        reply = thread["messages"][-1]
        self.assertEqual(reply["body"], "Done: the tests pass.")
        self.assertEqual(
            reply["steps"],
            [
                {"tool": "Bash", "input": "bin/ci", "result": "PASS", "error": False},
                {"tool": "Read", "input": "/x/a.py", "result": "line one", "error": True},
            ],
        )
        self.assertEqual(thread["messages"][0]["steps"], [])

    def test_leaves_out_subagent_steps_and_trims_long_ones(self):
        self.output = stream(
            tool_use("t1", "Agent", {"prompt": "p" * 500}),
            tool_use("s1", "Bash", {"command": "ls"}, parent="t1"),
            tool_result("s1", "a.py", parent="t1"),
            tool_result("t1", "x" * 2000),
            RESULT,
        )
        (step,) = self.start()["messages"][-1]["steps"]
        self.assertEqual(step["tool"], "Agent")
        self.assertEqual(len(step["input"]), threads.SUMMARY_LENGTH)
        self.assertEqual(len(step["result"]), threads.RESULT_LENGTH)
        self.assertTrue(step["result"].endswith("…"))

    def test_resumes_the_session_for_a_follow_up(self):
        first = self.start()
        with mock.patch.object(threads.subprocess, "run", self.run_claude):
            threads.reply(first["id"], "And now?")
        self.assertEqual(self.command[self.command.index("--resume") + 1], "s-1")
        self.assertEqual(len(threads.thread(first["id"])["messages"]), 4)

    def test_fails_when_claude_gives_no_result(self):
        self.output = "not json\n"
        thread = self.start()
        self.assertEqual(thread["status"], "failed")
        self.assertIn("Claude could not run", thread["messages"][-1]["body"])
