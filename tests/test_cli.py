import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tempo import cli
from tests.helpers import DatabaseTest


def run(*argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    with (
        redirect_stdout(out),
        redirect_stderr(err),
        mock.patch.object(sys, "stdin", io.StringIO(stdin)),
    ):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(DatabaseTest):
    def test_add_prints_the_number_and_link(self):
        code, out, _ = run(
            "add", "--title", "T", "--context", "C", "--option", "x", "--option", "y"
        )
        self.assertEqual(code, 0)
        self.assertIn("Added Q1 in demo: http://127.0.0.1:2204/#/demo/1", out)

    def test_add_explains_what_is_missing(self):
        code, _, err = run("add", "--title", "T", "--context", "C", "--option", "x")
        self.assertEqual(code, 2)
        self.assertIn("at least two options", err)

    def test_accepts_q_prefixed_numbers(self):
        self.add()
        code, out, _ = run("show", "q1")
        self.assertEqual(code, 0)
        self.assertIn("(a) Left", out)

    def test_user_prompt_hook_delivers_answers_once(self):
        self.add(title="Left or right?")
        self.store.answer("demo", 1, "b", "Nicer view")
        with mock.patch.object(cli, "project_name", return_value="demo"):
            _, out, _ = run("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x/demo"}))
            context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("demo Q1 (Left or right?) answered (b) Right", context)
            self.assertIn("Note: Nicer view", context)
            _, again, _ = run("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x/demo"}))
        self.assertEqual(again, "")

    def test_a_changed_answer_says_what_it_was_before(self):
        self.add()
        self.store.answer("demo", 1, "a")
        run("inbox")
        self.store.reopen("demo", 1)
        self.store.answer("demo", 1, None, "Neither, go up")
        _, out, _ = run("inbox")
        self.assertIn("changed the answer to Neither, go up", out)
        self.assertIn("Before: (a) Left", out)

    def test_user_prompt_hook_ignores_other_projects(self):
        self.add(project="other")
        self.store.follow_up("other", 1, "Why?")
        with mock.patch.object(cli, "project_name", return_value="demo"):
            _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        self.assertEqual(out, "")

    def test_user_prompt_hook_uses_the_project_override(self):
        self.add(project="ONZE HQ")
        self.store.follow_up("ONZE HQ", 1, "Why?")
        with mock.patch.dict("os.environ", {"OQ_PROJECT": "ONZE HQ"}):
            _, out, _ = run("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x/onzehq"}))
        self.assertIn("ONZE HQ Q1", out)

    def test_follow_ups_come_with_instructions(self):
        self.add()
        self.store.follow_up("demo", 1, "What is left?")
        _, out, _ = run("inbox")
        self.assertIn("asks for more: What is left?", out)
        self.assertIn("tempo reword", out)

    def test_wait_returns_as_soon_as_something_arrives(self):
        self.add()
        self.store.follow_up("demo", 1, "Hm?")
        code, out, _ = run("wait", "--timeout", "5", "--interval", "0.1")
        self.assertEqual(code, 0)
        self.assertIn("asks for more: Hm?", out)

    def test_wait_gives_up_after_the_timeout(self):
        _, out, _ = run("wait", "--timeout", "1", "--interval", "0.2")
        self.assertIn("Nothing new", out)
