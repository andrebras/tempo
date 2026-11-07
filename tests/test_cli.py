import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
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

    def test_add_takes_a_goal_and_a_start_here_reason(self):
        add = ("add", "--title", "T", "--context", "C", "--option", "x", "--option", "y")
        run(*add, "--goal", "Release 0.4")
        code, _, _ = run(*add, "--goal", "Release 0.4", "--start-here", "unlocks Q1")
        self.assertEqual(code, 0)
        self.assertEqual(self.store.get("demo", 2)["start_here"], "unlocks Q1")
        _, out, _ = run("list")
        self.assertEqual(
            out.splitlines(),
            [
                "Q2 [open, v1, goal: Release 0.4, start here, open 1 minute] T",
                "Q1 [open, v1, goal: Release 0.4, open 1 minute] T",
            ],
        )

    def test_add_ignores_the_old_priority_with_a_note(self):
        add = ("add", "--title", "T", "--context", "C", "--option", "x", "--option", "y")
        code, out, err = run(*add, "--priority", "now")
        self.assertEqual(code, 0)
        self.assertIn("Added Q1", out)
        self.assertIn("priorities are no longer used", err)
        code, _, err = run("priority", "1", "later")
        self.assertEqual(code, 0)
        self.assertIn("--goal", err)

    def test_goal_sets_and_clears_without_rewording(self):
        self.add(goal="Release")
        self.add(goal="Release", start_here="unlocks Q1")
        code, out, _ = run("goal", "1", "Release", "--start-here", "unlocks Q2")
        self.assertEqual(code, 0)
        self.assertEqual(out, "Q1 goal: Release; start here: unlocks Q2.\n")
        self.assertIsNone(self.store.get("demo", 2)["start_here"])
        _, out, _ = run("goal", "1", "--no-start-here")
        self.assertEqual(out, "Q1 goal: Release.\n")
        _, out, _ = run("goal", "1", "")
        self.assertEqual(out, "Q1 no goal.\n")
        self.assertEqual(self.store.get("demo", 1)["version"], 1)
        code, _, err = run("goal", "1", "--start-here", "unlocks Q2")
        self.assertEqual(code, 2)
        self.assertIn("goal first", err)

    def test_reword_can_move_the_start_here_mark(self):
        self.add(goal="Release", start_here="unlocks Q2")
        self.add(goal="Release")
        run("reword", "2", "--note", "Clearer", "--start-here", "unlocks Q1")
        self.assertIsNone(self.store.get("demo", 1)["start_here"])
        self.assertEqual(self.store.get("demo", 2)["start_here"], "unlocks Q1")
        run("reword", "2", "--note", "No longer first", "--no-start-here")
        self.assertIsNone(self.store.get("demo", 2)["start_here"])

    def test_show_gives_the_goal_and_the_plain_facts(self):
        self.add(goal="Release", start_here="unlocks the rest", recommended="a")
        _, out, _ = run("show", "1")
        self.assertIn(
            "demo Q1 (open, version 1, goal: Release; start here: unlocks the rest, quick,"
            " open 1 minute)",
            out,
        )

    def test_open_for_uses_the_largest_whole_unit(self):
        def ago(**delta):
            stamp = datetime.now(timezone.utc) - timedelta(**delta)
            return cli.open_for(stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))

        self.assertEqual(ago(seconds=5), "open 1 minute")
        self.assertEqual(ago(minutes=59), "open 59 minutes")
        self.assertEqual(ago(hours=2, minutes=5), "open 2 hours")
        self.assertEqual(ago(days=3, hours=23), "open 3 days")
        self.assertEqual(ago(days=1), "open 1 day")

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

    def test_session_start_tells_claude_to_use_tempo_and_lists_open_questions(self):
        self.add()
        self.add(title="Ship it?", goal="Release 0.4", start_here="unlocks the rest")
        with mock.patch.object(cli, "start_server") as start:
            _, out, _ = run("hook", "session-start", stdin="{}")
        start.assert_called_once()
        context = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "SessionStart")
        self.assertIn("goes in Tempo with `tempo add`", context["additionalContext"])
        self.assertIn("Q1 Which way? http://127.0.0.1:2204/#/demo/1", context["additionalContext"])
        self.assertIn(
            "Q2 Ship it? [Release 0.4, start here] http://127.0.0.1:2204/#/demo/2",
            context["additionalContext"],
        )
        self.assertIn("Give every question a goal", context["additionalContext"])
        self.assertIn("only when its answer really unlocks", context["additionalContext"])

    def stop(self, *replies, active=False):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as transcript:
            self.addCleanup(os.remove, transcript.name)
            for kind, content in replies:
                transcript.write(json.dumps({"type": kind, "message": {"content": content}}))
                transcript.write("\n")
        payload = {"transcript_path": transcript.name, "stop_hook_active": active}
        return run("hook", "stop", stdin=json.dumps(payload))[1]

    def test_stop_sends_back_options_asked_only_in_chat(self):
        out = self.stop(
            ("user", "Which one?"),
            ("assistant", [{"type": "text", "text": "Pick one:\n- (a) Left\n- (b) Right"}]),
        )
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_sends_back_questions_the_closing_list_does_not_record(self):
        closing = "\n\nOpen questions in demo:\n- Q1: Which way? http://127.0.0.1:2204/#/demo/1"
        cases = {
            "options": "Pick one:\n- (a) Left\n- (b) Right",
            "options and the closing list": "Pick one:\n- (a) Left\n- (b) Right" + closing,
            "yes or no": "May I clean up the database? Reply yes or no.",
            "permission over steps": "May I do this?\n1. Back up\n2. Clean up" + closing,
            "should I": "Done.\n\nShould I also update the docs?",
            "do you want": "- Do you want me to push it?",
            "bold closing list": "May I push?\n\n- **Q1** Which way? [Release] http://127.0.0.1:2204/#/demo/1",
            "tempo add named, not run": "(a) Left (b) Right. I will run tempo add next.",
        }
        for name, text in cases.items():
            with self.subTest(name):
                out = self.stop(("user", "Go"), ("assistant", [{"type": "text", "text": text}]))
                self.assertEqual(json.loads(out)["decision"], "block")
                self.assertIn("tempo add", json.loads(out)["reason"])

    def test_stop_ignores_code_quotes_and_statements(self):
        cases = {
            "code block": "Fixed:\n```\nif ask:\n    print('May I go? Reply yes')\n```",
            "inline code": "The hook now matches `Should I …?`.",
            "quoted line": "You wrote:\n> May I clean up the database? Reply yes or no.",
            "quoted phrase": 'The text "May I clean up? Reply yes or no." was missed before.',
            "statement": "I can update the docs next. Steps:\n1. Back up\n2. Clean up",
            "how can I": "How can I tell? The log shows it.",
        }
        for name, text in cases.items():
            with self.subTest(name):
                reply = [("user", "Go"), ("assistant", [{"type": "text", "text": text}])]
                self.assertEqual(self.stop(*reply), "")

    def test_stop_lets_tempo_questions_and_old_replies_through(self):
        options = [{"type": "text", "text": "(a) Left (b) Right"}]
        linked = [{"type": "text", "text": "(a) Left (b) Right http://127.0.0.1:2204/#/demo/1"}]
        added = [{"type": "tool_use", "input": {"command": "tempo add --title T"}}]
        reworded = [{"type": "tool_use", "input": {"command": "tempo reword 3 --note N"}}]
        yes_no = [{"type": "text", "text": "May I clean up? Reply yes or no.\n- Q1: T http://x"}]
        heading = [{"type": "text", "text": "**Q5: Push?** http://127.0.0.1:2204/#/demo/5\nMay I?"}]
        cases = {
            "linked": [("user", "Q?"), ("assistant", linked)],
            "linked in a heading": [("user", "Q?"), ("assistant", heading)],
            "yes or no added": [("user", "Q?"), ("assistant", added), ("assistant", yes_no)],
            "reworded": [("user", "Q?"), ("assistant", reworded), ("assistant", options)],
            "added": [("user", "Q?"), ("assistant", added), ("assistant", options)],
            "earlier reply": [("assistant", options), ("user", "Thanks"), ("assistant", [])],
            "after a tool result": [
                ("user", "Q?"),
                ("assistant", added),
                ("user", [{"type": "tool_result", "content": "Added Q1"}]),
                ("assistant", options),
            ],
        }
        for name, replies in cases.items():
            with self.subTest(name):
                self.assertEqual(self.stop(*replies), "")
        self.assertEqual(self.stop(("user", "Q?"), ("assistant", options), active=True), "")
        self.assertEqual(run("hook", "stop", stdin="{}")[1], "")

    def test_stop_hands_over_new_answers_once(self):
        self.add(title="Left or right?")
        self.store.answer("demo", 1, "b")
        reply = [("user", "Go"), ("assistant", [{"type": "text", "text": "Done."}])]
        self.assertEqual(self.stop(*reply, active=True), "")
        out = json.loads(self.stop(*reply))
        self.assertEqual(out["decision"], "block")
        self.assertIn("demo Q1 (Left or right?) answered (b) Right", out["reason"])
        self.assertEqual(self.stop(*reply), "")

    def test_stop_hands_over_answers_before_anything_else(self):
        self.add()
        self.add()
        self.store.answer("demo", 1, "a")
        options = [("user", "Go"), ("assistant", [{"type": "text", "text": "(a) Up (b) Down"}])]
        self.assertIn("answered (a) Left", json.loads(self.stop(*options))["reason"])
        self.assertIn("tempo add", json.loads(self.stop(*options))["reason"])

    def test_stop_asks_for_a_wait_while_questions_are_open(self):
        reply = [("user", "Go"), ("assistant", [{"type": "text", "text": "Done."}])]
        self.assertEqual(self.stop(*reply), "")
        self.add()
        out = json.loads(self.stop(*reply))
        self.assertEqual(out["decision"], "block")
        self.assertIn("tempo wait --timeout 1800", out["reason"])
        self.assertIn("run_in_background", out["reason"])
        self.assertEqual(self.stop(*reply, active=True), "")
        self.marker(os.getpid())
        self.assertEqual(self.stop(*reply), "")

    def test_stop_lets_the_reply_end_on_any_error(self):
        self.add()
        with mock.patch.object(cli, "wait_running", side_effect=RuntimeError):
            self.assertEqual(run("hook", "stop", stdin="{}")[1], "")

    def test_user_prompt_hook_ignores_other_projects(self):
        self.add(project="other")
        self.store.follow_up("other", 1, "Why?")
        with mock.patch.object(cli, "project_name", return_value="demo"):
            _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        self.assertEqual(out, "")

    def test_user_prompt_hook_uses_the_project_override(self):
        self.add(project="ONZE HQ")
        self.store.follow_up("ONZE HQ", 1, "Why?")
        with mock.patch.dict("os.environ", {"TEMPO_PROJECT": "ONZE HQ"}):
            _, out, _ = run("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x/onzehq"}))
        self.assertIn("ONZE HQ Q1", out)

    def test_user_prompt_hook_uses_the_folder_project_and_remembers_the_folder(self):
        del os.environ["TEMPO_PROJECT"]
        self.add(project="onzehq")
        hook = json.dumps({"cwd": "/x/onzehq"})
        _, out, _ = run("hook", "user-prompt-submit", stdin=hook)
        self.assertEqual(out, "")
        self.assertEqual(self.store.folders()[0]["project"], "onzehq")
        self.store.rename_project("onzehq", "ONZE HQ")
        self.store.follow_up("ONZE HQ", 1, "Why?")
        _, out, _ = run("hook", "user-prompt-submit", stdin=hook)
        self.assertIn("ONZE HQ Q1", out)

    def test_the_project_override_skips_the_folder(self):
        run("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x/onzehq"}))
        self.assertEqual(self.store.folders(), [])

    def test_add_remembers_the_folder_and_uses_its_project(self):
        del os.environ["TEMPO_PROJECT"]
        add = ("add", "--title", "T", "--context", "C", "--option", "x", "--option", "y")
        with mock.patch.object(cli, "folder_name", return_value="tempo-settings"):
            _, out, _ = run(*add)
            self.assertIn("Added Q1 in tempo-settings", out)
            self.store.rename_project("tempo-settings", "Tempo")
            _, out, _ = run(*add)
        self.assertIn("Added Q2 in Tempo", out)

    def test_still_reads_the_old_oq_settings(self):
        self.add(project="ONZE HQ")
        self.store.follow_up("ONZE HQ", 1, "Why?")
        with mock.patch.dict("os.environ", {"OQ_PROJECT": "ONZE HQ", "OQ_PORT": "2299"}):
            del os.environ["TEMPO_PROJECT"]
            _, out, _ = run("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x/onzehq"}))
        self.assertIn("ONZE HQ Q1", out)
        self.assertIn("http://127.0.0.1:2299/", out)

    def test_the_new_settings_win_over_the_old_ones(self):
        with mock.patch.dict("os.environ", {"OQ_PROJECT": "old", "TEMPO_PROJECT": "new"}):
            self.assertEqual(cli._project(None), "new")

    def test_follow_ups_come_with_instructions(self):
        self.add()
        self.store.follow_up("demo", 1, "What is left?")
        _, out, _ = run("inbox")
        self.assertIn("asks for more: What is left?", out)
        self.assertIn("tempo reword", out)

    def test_a_note_that_asks_something_needs_a_reply(self):
        self.add()
        self.add()
        self.store.answer("demo", 1, "a", "Fine. Do we still need the skill?")
        self.store.answer("demo", 2, "b", "Nicer view")
        _, out, _ = run("inbox")
        self.assertIn("answer it in the question with `tempo context 1`", out)
        self.assertEqual(out.count("The note asks something"), 1)
        self.assertNotIn("reword", out)

    def test_wait_returns_as_soon_as_something_arrives(self):
        self.add()
        self.store.follow_up("demo", 1, "Hm?")
        code, out, _ = run("wait", "--timeout", "5", "--interval", "0.1")
        self.assertEqual(code, 0)
        self.assertIn("asks for more: Hm?", out)

    def test_wait_gives_up_after_the_timeout(self):
        _, out, _ = run("wait", "--timeout", "1", "--interval", "0.2")
        self.assertIn("Nothing new", out)

    def marker(self, pid, project="demo"):
        path = os.path.join(self.tmp.name, "wait-%d.json" % pid)
        with open(path, "w") as file:
            json.dump({"pid": pid, "project": project}, file)
        return path

    def test_wait_leaves_a_marker_while_it_runs(self):
        seen = []

        def inbox(project):
            seen.append(cli.wait_running("demo"))
            return "Answered"

        with mock.patch.object(cli, "take_inbox", side_effect=inbox):
            run("wait", "--timeout", "5")
        self.assertEqual(seen, [True])
        self.assertFalse(cli.wait_running("demo"))
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith("wait-")], [])

    def test_wait_running_matches_the_project_and_drops_dead_markers(self):
        self.marker(os.getpid(), project="other")
        self.assertFalse(cli.wait_running("demo"))
        self.marker(os.getpid(), project=None)
        self.assertTrue(cli.wait_running("demo"))
        ended = subprocess.Popen(["true"])
        ended.wait()
        dead = self.marker(ended.pid)
        self.assertTrue(cli.wait_running("demo"))
        self.assertFalse(os.path.exists(dead))


class ProjectNameTest(unittest.TestCase):
    def test_uses_the_main_checkout_folder_from_subfolders_and_worktrees(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = os.path.join(tmp.name, "shop")
        # No signing: the test must not depend on how the developer signs commits.
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q", repo], check=True)
        subprocess.run([*git, "-C", repo, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        os.makedirs(os.path.join(repo, "sub"))
        worktree = os.path.join(tmp.name, "shop-feature")
        subprocess.run([*git, "-C", repo, "worktree", "add", "-q", worktree], check=True)
        for folder in [repo, os.path.join(repo, "sub"), worktree]:
            with self.subTest(folder=folder):
                self.assertEqual(cli.project_name(folder), "shop")
