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

# How a `tempo wait` process shows in ps.
WAIT = "/usr/bin/python3 -m tempo wait --timeout 1800"


def run(*argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    with (
        redirect_stdout(out),
        redirect_stderr(err),
        mock.patch.object(sys, "stdin", io.StringIO(stdin)),
    ):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeClock:
    """Stands in for time.time and time.sleep: sleeping moves the clock on at once."""

    def __init__(self):
        self.now = 1_000_000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class CliTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        # The `tempo wait` processes running, by pid, with their commands: none unless a
        # test adds one, so waits running on this computer for real never count.
        self.waits = {}
        waits = mock.patch.object(cli, "_wait_processes", side_effect=lambda: dict(self.waits))
        waits.start()
        self.addCleanup(waits.stop)

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

    def test_show_takes_the_short_code_of_an_option(self):
        # The user names an answer as "1b"; show gives that question.
        self.add()
        for code in ("1b", "Q1B"):
            with self.subTest(code=code):
                status, out, _ = run("show", code)
                self.assertEqual(status, 0)
                self.assertIn("(b) Right", out)

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

    def test_two_inbox_reads_at_once_deliver_an_answer_once(self):
        # A hook in a second session (or tempo wait) reads the inbox while this one does.
        self.add()
        self.store.answer("demo", 1, "b")
        read = cli.Store.undelivered
        other = []

        def read_then_the_other(store, project=None):
            entries = read(store, project)
            if not other:
                other.append(None)
                other[0] = cli.take_inbox("demo")
            return entries

        with mock.patch.object(cli.Store, "undelivered", read_then_the_other):
            texts = [cli.take_inbox("demo"), *other]
        self.assertEqual(sum("answered (b) Right" in (text or "") for text in texts), 1)

    def test_an_answer_whose_text_cannot_be_built_is_not_lost(self):
        self.add(title="Which DB?", options=["Postgres", "SQLite"])
        self.store.answer("demo", 1, "a")
        with mock.patch.object(cli, "_describe_inbox", side_effect=RuntimeError("broken")):
            _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        self.assertEqual(out, "")
        self.assertEqual(len(self.store.undelivered("demo")), 1)
        self.assertIn("answered (a) Postgres", run("inbox")[1])

    def test_an_answer_naming_a_letter_its_version_lacks_is_still_delivered(self):
        # An older bug could save an answer whose letter its question version does not have.
        self.add()
        self.store.answer("demo", 1, "a")
        run("inbox")
        self.store.db.execute("UPDATE entries SET choice = 'c' WHERE kind = 'answer'")
        self.store.reopen("demo", 1)
        self.store.answer("demo", 1, "b", "Changed my mind")
        _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("changed the answer to (b) Right / Note: Changed my mind", context)
        self.assertIn("Before: (c)", context)
        self.assertEqual(self.store.undelivered("demo"), [])

    def test_a_session_in_the_root_folder_takes_no_news(self):
        # "/" has no folder name, so its project name is empty, which is no project.
        del os.environ["TEMPO_PROJECT"]
        self.add(project="shop")
        self.store.answer("shop", 1, "a")
        payload = json.dumps({"cwd": "/", "stop_hook_active": False})
        for event in ("user-prompt-submit", "stop"):
            self.assertEqual(run("hook", event, stdin=payload)[1], "", event)
        self.assertEqual(len(self.store.undelivered("shop")), 1)

    def test_session_start_hook_stays_quiet_in_a_thread(self):
        with (
            mock.patch.dict("os.environ", {"TEMPO_IN_THREAD": "1"}),
            mock.patch.object(cli, "start_server") as start,
        ):
            code, out, _ = run("hook", "session-start", stdin="{}")
        self.assertEqual((code, out), (0, ""))
        start.assert_not_called()

    def test_user_prompt_hook_stays_quiet_in_a_thread(self):
        self.add()
        self.store.answer("demo", 1, "b")
        with mock.patch.dict("os.environ", {"TEMPO_IN_THREAD": "1"}):
            code, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("answered (b) Right", run("inbox")[1])

    def test_stop_hook_stays_quiet_in_a_thread(self):
        with (
            mock.patch.dict("os.environ", {"TEMPO_IN_THREAD": "1"}),
            mock.patch.object(cli, "stop_outcome", return_value=("Carry on", "demo", "")) as reason,
        ):
            code, out, _ = run("hook", "stop", stdin="{}")
        self.assertEqual((code, out), (0, ""))
        reason.assert_not_called()

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

    def test_hooks_never_block_the_session_on_a_tempo_error(self):
        # A session started in / has no folder name, so finding its project fails.
        del os.environ["TEMPO_PROJECT"]
        with mock.patch.object(cli, "start_server") as start:
            for event in ("user-prompt-submit", "session-start"):
                code, out, _ = run("hook", event, stdin=json.dumps({"cwd": "/"}))
                self.assertEqual((code, out), (0, ""), event)
        start.assert_called_once()

    def hook_log(self):
        """The hook lines of server.log, without their times."""
        with open(os.path.join(self.tmp.name, "server.log"), encoding="utf-8") as file:
            lines = [line.split(" ", 1)[1] for line in file.read().splitlines()]
        return [line for line in lines if line.startswith("hook ")]

    def test_each_hook_run_writes_one_log_line(self):
        reply = [("user", "Go"), ("assistant", [{"type": "text", "text": "Done."}])]
        options = [("user", "Go"), ("assistant", [{"type": "text", "text": "(a) Up (b) Down"}])]
        self.add()
        with mock.patch.object(cli, "start_server", return_value=True):
            run("hook", "session-start", stdin="{}")
        run("hook", "user-prompt-submit", stdin="{}")
        self.store.answer("demo", 1, "b", "A private note")
        run("hook", "user-prompt-submit", stdin="{}")
        self.add()
        self.stop(*options)
        self.stop(*reply)
        self.stop(*reply, active=True)
        with mock.patch.dict("os.environ", {"TEMPO_IN_THREAD": "1"}):
            run("hook", "stop", stdin="{}")
        with mock.patch.object(cli, "session_note", side_effect=RuntimeError("broken")):
            run("hook", "session-start", stdin="{}")
        self.assertEqual(
            self.hook_log(),
            [
                "hook session-start demo note sent, page started",
                "hook user-prompt-submit demo nothing new",
                "hook user-prompt-submit demo delivered 1",
                "hook stop demo blocked: unrecorded question",
                "hook stop demo blocked: no wait",
                "hook stop - let through: already sent back once",
                "hook stop - quiet: TEMPO_IN_THREAD",
                "hook session-start demo error: broken",
            ],
        )

    def test_a_log_problem_never_breaks_a_hook(self):
        self.add()
        self.store.answer("demo", 1, "b")
        with mock.patch.object(cli.log, "open_log", side_effect=OSError("disk full")):
            code, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        self.assertEqual(code, 0)
        self.assertIn("answered (b) Right", out)

    def test_hooks_ignore_input_that_is_not_an_object(self):
        for event in ("user-prompt-submit", "stop"):
            code, out, _ = run("hook", event, stdin="[]")
            self.assertEqual((code, out), (0, ""), event)

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
        self.assertEqual(self.store.folders()[0]["path"], os.path.realpath("/x/onzehq"))
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
        with mock.patch.object(cli, "repo_path", return_value="/x/tempo-settings"):
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
        clock = FakeClock()
        with (
            mock.patch.object(cli.time, "time", clock.time),
            mock.patch.object(cli.time, "sleep", clock.sleep),
        ):
            _, out, _ = run("wait", "--timeout", "1", "--interval", "0.25")
        self.assertIn("Nothing new", out)
        # It checked the inbox every 0.25 seconds until the second was up, without waiting.
        self.assertEqual(clock.slept, [0.25] * 4)

    def marker(self, pid, project="demo", wait=True):
        """Leave a wait marker for the pid; with wait, the pid also runs `tempo wait`."""
        path = os.path.join(self.tmp.name, "wait-%d.json" % pid)
        with open(path, "w") as file:
            json.dump({"pid": pid, "project": project}, file)
        if wait:
            self.waits[pid] = WAIT
        return path

    def test_wait_leaves_a_marker_while_it_runs(self):
        seen = []

        def inbox(project):
            seen.append(cli.wait_running("demo"))
            return "Answered"

        self.waits[os.getpid()] = WAIT  # The wait runs in this process.
        with mock.patch.object(cli, "take_inbox", side_effect=inbox):
            run("wait", "--timeout", "5")
        self.waits.clear()
        self.assertEqual(seen, [True])
        self.assertFalse(cli.wait_running("demo"))
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith("wait-")], [])

    def test_inbox_for_every_project_shows_news_without_taking_it(self):
        self.add(project="blog")
        self.store.answer("blog", 1, "a")
        _, out, _ = run("inbox", "--all-projects")
        self.assertIn("blog Q1 (Which way?) answered (a) Left", out)
        self.assertIn("only shown", out)
        self.assertEqual(len(self.store.undelivered("blog")), 1)

    def test_wait_no_longer_waits_for_every_project(self):
        # One session's wait took other sessions' news; the option stays for one release.
        self.add(project="blog")
        self.store.answer("blog", 1, "a")
        self.add()
        self.store.follow_up("demo", 1, "Hm?")
        code, out, err = run("wait", "--all-projects", "--timeout", "5", "--interval", "0.1")
        self.assertEqual(code, 0)
        self.assertIn("demo Q1 (Which way?) asks for more: Hm?", out)
        self.assertNotIn("blog", out)
        self.assertIn("--all-projects is no longer used", err)
        self.assertEqual(len(self.store.undelivered("blog")), 1)

    def test_wait_ends_when_its_session_is_gone(self):
        # Once the session's shell is gone, the wait belongs to the system (pid 1), and news
        # it took would reach nobody.
        self.add()
        parents = iter([4242, 4242, 1])

        def meanwhile(seconds):
            self.store.follow_up("demo", 1, "Hm?")

        with (
            mock.patch.object(cli.os, "getppid", lambda: next(parents)),
            mock.patch.object(cli.time, "sleep", meanwhile),
        ):
            code, out, _ = run("wait", "--timeout", "5", "--interval", "0.1")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(len(self.store.undelivered("demo")), 1)

    def test_wait_follows_its_folder_when_the_project_is_renamed(self):
        del os.environ["TEMPO_PROJECT"]
        self.store.see_folder("shop", "/x/shop")
        self.add(project="shop")
        self.waits[os.getpid()] = WAIT  # The wait runs in this process.
        seen = []

        def meanwhile(seconds):
            # The user renames the project in Settings, then follows up under the new name.
            if not seen:
                self.store.rename_project("shop", "Shop")
            else:
                self.store.follow_up("Shop", 1, "Why?")
            seen.append(cli.wait_running("Shop"))

        with (
            mock.patch.object(cli, "folder_path", return_value="/x/shop"),
            mock.patch.object(cli.time, "sleep", meanwhile),
        ):
            _, out, _ = run("wait", "--timeout", "5", "--interval", "0.1")
        self.assertIn("Shop Q1 (Which way?) asks for more: Why?", out)
        # Its marker named the new project from the first check after the rename.
        self.assertEqual(seen, [False, True])

    def test_wait_running_matches_the_project_and_drops_dead_markers(self):
        self.marker(os.getpid(), project="other")
        self.assertFalse(cli.wait_running("demo"))
        self.marker(os.getpid(), project=None)
        self.assertTrue(cli.wait_running("demo"))
        ended = subprocess.Popen(["true"])
        ended.wait()
        dead = self.marker(ended.pid, wait=False)
        self.assertTrue(cli.wait_running("demo"))
        self.assertFalse(os.path.exists(dead))

    def test_a_marker_whose_pid_now_runs_something_else_is_dropped(self):
        # A killed wait's pid can be given to another process, which is alive but no wait.
        reused = self.marker(os.getpid(), wait=False)
        self.assertFalse(cli.wait_running("demo"))
        self.assertFalse(os.path.exists(reused))

    def test_a_wait_that_left_no_marker_counts_for_its_folder_or_named_project(self):
        # An install from before markers, or one using another folder, runs `tempo wait`
        # without leaving a marker here.
        del os.environ["TEMPO_PROJECT"]
        self.marker(4242, project="other")
        self.assertFalse(cli.wait_running("demo"))
        folders = {4343: "/x/demo", 4444: "/x/other", 4545: None, 4747: "/x/other"}
        overrides = {4747: "ONZE HQ"}
        with (
            mock.patch.object(cli, "_process_cwd", side_effect=folders.get),
            mock.patch.object(cli, "_process_project_setting", side_effect=overrides.get),
        ):
            self.waits[4444] = WAIT
            self.assertFalse(cli.wait_running("demo"))
            self.waits[4343] = WAIT
            self.assertTrue(cli.wait_running("demo"))
            self.assertFalse(cli.wait_running("shop"))
            self.waits[4646] = WAIT + " --project shop"
            self.assertTrue(cli.wait_running("shop"))
            self.assertFalse(cli.wait_running("ONZE HQ"))
            self.waits[4747] = WAIT  # Run with TEMPO_PROJECT="ONZE HQ".
            self.assertTrue(cli.wait_running("ONZE HQ"))
            # A wait whose folder cannot be found counts for every project.
            self.waits[4545] = WAIT
            self.assertTrue(cli.wait_running("blog"))

    def test_without_ps_a_marker_counts_while_its_process_lives(self):
        self.marker(os.getpid(), wait=False)
        with mock.patch.object(cli, "_wait_processes", return_value=None):
            self.assertTrue(cli.wait_running("demo"))


class GitTest(DatabaseTest):
    """Runs real git commands in temporary folders. Git's own variables, set when the
    tests run from a git hook or `git rebase --exec`, would point those commands at the
    repo running the tests instead, so they are cleared."""

    def setUp(self):
        super().setUp()
        clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        patcher = mock.patch.dict(os.environ, clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class RenameNoteTest(GitTest):
    """A session starts in a folder Tempo has never seen while a known one is gone."""

    def setUp(self):
        super().setUp()
        del os.environ["TEMPO_PROJECT"]
        self.lab = os.path.realpath(self.tmp.name)
        self.old = os.path.join(self.lab, "open-questions")
        self.new = os.path.join(self.lab, "tempo")
        subprocess.run(["git", "init", "-q", self.old], check=True)
        self.store.see_folder("open-questions", self.old)
        self.store.rename_project("open-questions", "Tempo")

    def start(self, cwd):
        with mock.patch.object(cli, "start_server"):
            _, out, _ = run("hook", "session-start", stdin=json.dumps({"cwd": cwd}))
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def test_a_renamed_folder_is_offered_and_claude_is_told(self):
        os.rename(self.old, self.new)
        note = self.start(self.new)
        self.assertIn("This folder, `tempo`", note)
        self.assertIn("filed under Tempo, no longer exists, so it looks renamed or moved", note)
        self.assertIn('"Link to Tempo" button', note)
        # Not linked until the user says so.
        self.assertEqual(self.store.project_for("tempo", self.new), "tempo")
        self.assertEqual(len(self.store.renames()), 1)

    def test_several_missing_folders_offer_a_link_for_each(self):
        shop = os.path.join(self.lab, "shop")
        self.store.see_folder("shop", shop)
        os.rename(self.old, self.new)
        note = self.start(self.new)
        self.assertIn("several known folders no longer exist", note)
        self.assertIn('"Link to <project>" button for each missing folder', note)
        self.assertNotIn("Settings", note)
        self.assertEqual(self.store.project_for("tempo", self.new), "tempo")

    def test_a_new_folder_outside_git_is_not_offered(self):
        # A quick session in a scratch folder must not take the renamed repo's banner.
        os.rename(self.old, self.new)
        scratch = os.path.join(self.lab, "scratch")
        os.makedirs(scratch)
        self.assertNotIn("is new to Tempo", self.start(scratch))
        self.assertIn("looks renamed or moved", self.start(self.new))
        self.assertEqual([r["path"] for r in self.store.renames()], [self.new])

    def test_no_missing_folder_starts_a_new_project_as_before(self):
        subprocess.run(["git", "init", "-q", self.new], check=True)
        note = self.start(self.new)
        self.assertNotIn("is new to Tempo", note)
        self.assertEqual(self.store.renames(), [])
        self.assertEqual(self.store.project_for("tempo", self.new), "tempo")


class ProjectNameTest(GitTest):
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
                self.assertEqual(cli.folder_path(folder), os.path.realpath(repo))
                self.assertEqual(cli.project_name(folder), "shop")

    def test_worktrees_of_a_bare_repo_share_the_repo_project(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        bare = os.path.join(tmp.name, "shop.git")
        subprocess.run([*git, "init", "-q", "--bare", bare], check=True)
        # A first commit, so the bare repo has a branch to check out.
        seed = os.path.join(tmp.name, "seed")
        subprocess.run([*git, "clone", "-q", bare, seed], check=True, capture_output=True)
        subprocess.run([*git, "-C", seed, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run([*git, "-C", bare, "fetch", "-q", seed, "HEAD:main"], check=True)
        main = os.path.join(tmp.name, "shop-main")
        subprocess.run([*git, "-C", bare, "worktree", "add", "-q", main, "main"], check=True)
        self.assertEqual(cli.folder_path(main), os.path.realpath(bare))
        self.assertEqual(cli.project_name(main), "shop")

    def test_a_submodule_is_a_project_of_its_own(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        app, lib = os.path.join(tmp.name, "app"), os.path.join(tmp.name, "lib")
        for repo in (app, lib):
            subprocess.run([*git, "init", "-q", repo], check=True)
            subprocess.run(
                [*git, "-C", repo, "commit", "-q", "--allow-empty", "-m", "x"], check=True
            )
        add = ["-c", "protocol.file.allow=always", "submodule", "add", "-q", lib, "vendor/lib"]
        subprocess.run([*git, "-C", app, *add], check=True, capture_output=True)
        submodule = os.path.join(app, "vendor", "lib")
        self.assertEqual(cli.folder_path(submodule), os.path.realpath(submodule))
        self.assertEqual(cli.project_name(submodule), "lib")

    def test_uses_the_folder_itself_outside_git(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(cli.folder_path(tmp.name), os.path.realpath(tmp.name))


class StartServerTest(unittest.TestCase):
    """What the SessionStart hook does with whatever already listens on the port."""

    def setUp(self):
        from tempo import server

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.listening = True
        self.folder = tmp.name
        database = mock.patch.dict(os.environ, {"TEMPO_DB": os.path.join(tmp.name, "tempo.db")})
        database.start()
        self.addCleanup(database.stop)
        self.current = {
            "app": "tempo",
            "version": server.version(),
            "root": server.ROOT,
            "pid": 42,
            "db": cli.db_path(),
        }
        self.patch("_listening", side_effect=lambda port: self.listening)
        self.fetch = self.patch("_fetch")
        self.tempo_pid = self.patch("_tempo_pid", return_value=None)
        self.kill = self.patch("kill", cli.os, side_effect=self.stopped)
        self.popen = self.patch("Popen", cli.subprocess)
        self.patch("sleep", cli.time)
        self.serve()

    def patch(self, name, target=cli, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def stopped(self, pid, sig):
        self.listening = False

    def serve(self, health=None, page=None):
        """What the server on the port answers; None stands for an error or no answer."""
        body = json.dumps(health).encode() if health else None
        self.fetch.side_effect = lambda port, path: {"/health": body, "/": page}[path]

    def test_starts_when_the_port_is_free(self):
        self.listening = False
        self.assertTrue(cli.start_server(2204))
        self.popen.assert_called_once()

    def test_the_log_is_private(self):
        self.listening = False
        cli.start_server(2204)
        mode = os.stat(os.path.join(self.folder, "server.log")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_keeps_a_current_tempo(self):
        self.serve(health=self.current)
        self.assertFalse(cli.start_server(2204))
        self.kill.assert_not_called()
        self.popen.assert_not_called()

    def test_replaces_an_older_tempo(self):
        for change in [{"version": "0.0.1"}, {"version": None}, {"version": "dev"}]:
            with self.subTest(change=change):
                self.listening = True
                self.kill.reset_mock()
                self.serve(health={**self.current, **change})
                self.assertTrue(cli.start_server(2204))
                self.kill.assert_called_once_with(42, cli.signal.SIGTERM)

    def test_keeps_a_tempo_of_the_same_or_a_newer_version_from_another_folder(self):
        # With the installed plugin and a checkout both in use, neither keeps replacing
        # the other's page.
        for change in [{"root": "/Users/me/Lab/tempo"}, {"version": "99.0.0", "root": "/x"}]:
            with self.subTest(change=change):
                self.serve(health={**self.current, **change})
                self.assertFalse(cli.start_server(2204))
                self.kill.assert_not_called()
                self.popen.assert_not_called()

    def test_keeps_a_tempo_on_another_database_even_an_older_one(self):
        # A worktree's server on the real page's port, or the real one on a worktree's:
        # replacing it would switch the page to another set of questions.
        self.serve(health={**self.current, "version": "0.0.1", "db": "/branch/questions.db"})
        self.assertFalse(cli.start_server(2204))
        self.kill.assert_not_called()
        self.popen.assert_not_called()

    def test_keeps_an_old_server_whose_page_works(self):
        self.serve(page=b"<title>Tempo</title>")
        self.tempo_pid.return_value = 42
        self.assertFalse(cli.start_server(2204))
        self.kill.assert_not_called()

    def test_replaces_a_broken_old_tempo(self):
        self.tempo_pid.return_value = 42
        self.assertTrue(cli.start_server(2204))
        self.kill.assert_called_once_with(42, cli.signal.SIGTERM)
        self.popen.assert_called_once()

    def test_leaves_anything_it_cannot_show_is_tempo(self):
        for health in [None, {"app": "other", "pid": 42}]:
            with self.subTest(health=health):
                self.serve(health=health)
                self.assertFalse(cli.start_server(2204))
                self.kill.assert_not_called()
                self.popen.assert_not_called()

    def test_does_not_start_while_the_old_server_holds_the_port(self):
        self.serve(health={**self.current, "version": "0.0.1"})
        self.kill.side_effect = None
        self.assertFalse(cli.start_server(2204))
        self.popen.assert_not_called()


class PortTest(unittest.TestCase):
    """Code on a branch database serves its page on a port of its own."""

    def setUp(self):
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for key in ("TEMPO_PORT", "OQ_PORT", "TEMPO_DB", "OQ_DB"):
            os.environ.pop(key, None)

    def branch(self, path):
        return mock.patch("tempo.cli.branch_db", return_value=path)

    def test_the_real_database_uses_the_default_port(self):
        with self.branch(None):
            self.assertEqual(cli._port(), 2204)

    def test_a_branch_database_gets_its_own_steady_port(self):
        ports = set()
        for path in ("/a/.tempo-dev/questions.db", "/b/.tempo-dev/questions.db"):
            with self.branch(path):
                port = cli._port()
                self.assertEqual(cli._port(), port)
                self.assertNotIn(port, (2204, 2205))
                ports.add(port)
        self.assertEqual(len(ports), 2)

    def test_an_explicit_database_or_port_wins(self):
        with self.branch("/a/.tempo-dev/questions.db"):
            with mock.patch.dict(os.environ, {"TEMPO_DB": "/elsewhere/questions.db"}):
                self.assertEqual(cli._port(), 2204)
            with mock.patch.dict(os.environ, {"TEMPO_PORT": "3000"}):
                self.assertEqual(cli._port(), 3000)


class OlderTest(unittest.TestCase):
    def test_compares_versions_by_number(self):
        self.assertTrue(cli._older("0.4.0", "0.5.0"))
        self.assertTrue(cli._older("0.9.0", "0.10.0"))
        self.assertFalse(cli._older("0.5.0", "0.5.0"))
        self.assertFalse(cli._older("0.10.0", "0.9.0"))

    def test_an_unknown_running_version_is_older_but_an_unknown_own_one_replaces_nothing(self):
        self.assertTrue(cli._older(None, "0.5.0"))
        self.assertTrue(cli._older("dev", "0.5.0"))
        self.assertFalse(cli._older("0.4.0", None))


class RestartNoteTest(DatabaseTest):
    """The hooks of a session older than the installed plugin ask for a restart, once."""

    def setUp(self):
        super().setUp()
        self.installed = os.path.join(self.tmp.name, "installed_plugins.json")
        self.root = os.path.join(self.tmp.name, "plugin")  # An installed copy: no .git.
        os.mkdir(self.root)
        for patch in (
            mock.patch.object(cli, "INSTALLED_PLUGINS", self.installed),
            mock.patch.object(cli, "CODE_ROOT", self.root),
            mock.patch("tempo.server.version", return_value="0.4.0"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def install(self, *versions):
        entries = [{"scope": "user", "version": v, "installPath": "/x/" + v} for v in versions]
        with open(self.installed, "w") as file:
            json.dump({"version": 2, "plugins": {"tempo@tempo": entries}}, file)

    def hook(self, event, session="s1"):
        _, out, _ = run("hook", event, stdin=json.dumps({"cwd": "/x", "session_id": session}))
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_an_older_session_is_told_once(self):
        self.install("0.5.4", "0.5.1")
        note = cli.restart_note("s1")
        self.assertEqual(
            note,
            "Tempo in this session is 0.4.0 but 0.5.4 is installed: tell the user to restart"
            " this session (`claude --continue`) to use it.",
        )
        self.assertEqual(cli.restart_note("s1"), "")
        self.assertEqual(cli.restart_note("s2"), note)

    def test_the_prompt_hook_puts_the_line_first_and_says_it_once(self):
        self.install("0.5.4")
        self.add()
        self.store.answer("demo", 1, "b")
        context = self.hook("user-prompt-submit")
        self.assertTrue(context.startswith("Tempo in this session is 0.4.0 but 0.5.4"))
        self.assertIn("answered (b) Right", context)
        self.assertEqual(self.hook("user-prompt-submit"), "")

    def test_the_session_start_hook_says_it(self):
        self.install("0.5.4")
        with mock.patch.object(cli, "start_server"):
            self.assertIn("restart this session", self.hook("session-start"))

    def test_the_same_or_a_newer_session_is_not_told(self):
        for version in ("0.4.0", "0.3.9"):
            with self.subTest(version=version):
                self.install(version)
                self.assertEqual(cli.restart_note("s1"), "")

    def test_an_unreadable_list_says_nothing(self):
        self.assertEqual(cli.restart_note("s1"), "")  # No file at all.
        for content in ("not json", "[]", '{"plugins": {"tempo@tempo": "0.5.4"}}', "{}"):
            with self.subTest(content=content):
                with open(self.installed, "w") as file:
                    file.write(content)
                self.assertEqual(cli.restart_note("s1"), "")

    def test_a_checkout_or_worktree_says_nothing(self):
        self.install("0.5.4")
        for make in (os.mkdir, lambda path: open(path, "w").close()):
            with self.subTest(make=make):
                git = os.path.join(self.root, ".git")
                make(git)
                self.assertEqual(cli.restart_note("s1"), "")
                (os.rmdir if os.path.isdir(git) else os.remove)(git)

    def test_no_session_id_or_own_version_says_nothing(self):
        self.install("0.5.4")
        self.assertEqual(cli.restart_note(None), "")
        with mock.patch("tempo.server.version", return_value=None):
            self.assertEqual(cli.restart_note("s1"), "")

    def test_a_marker_that_cannot_be_used_says_nothing(self):
        self.install("0.5.4")
        os.mkdir(os.path.join(self.tmp.name, "restart-noted"))  # A folder, not a file.
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.restart_note("s1"), "")
        self.assertIn("version check skipped", err.getvalue())


class TempoPidTest(unittest.TestCase):
    def test_only_names_a_process_running_tempo_serve(self):
        def ran(stdout):
            return subprocess.CompletedProcess([], 0, stdout=stdout)

        for command, pid in [
            ("/usr/bin/python3 -m tempo serve --port 2204\n", 4242),
            ("/usr/bin/python3 -m http.server 2204\n", None),
        ]:
            with self.subTest(command=command):
                runs = [ran("4242\n"), ran(command)]
                with mock.patch.object(cli.subprocess, "run", side_effect=runs):
                    self.assertEqual(cli._tempo_pid(2204), pid)


class WaitProcessesTest(unittest.TestCase):
    def test_lists_the_processes_running_tempo_wait(self):
        sleep = [sys.executable, "-c", "import time; time.sleep(30)"]
        wait = subprocess.Popen([*sleep, "-m", "tempo", "wait", "--timeout", "1800"])
        other = subprocess.Popen([*sleep, "-m", "tempo", "serve"])
        for process in (wait, other):
            self.addCleanup(process.wait)
            self.addCleanup(process.kill)
        waits = cli._wait_processes()
        self.assertIn("-m tempo wait --timeout 1800", waits[wait.pid])
        self.assertNotIn(other.pid, waits)
        self.assertNotIn(os.getpid(), waits)

    def test_none_when_ps_cannot_run(self):
        with mock.patch.object(cli.subprocess, "run", side_effect=OSError):
            self.assertIsNone(cli._wait_processes())

    def test_finds_a_process_folder(self):
        folder = cli._process_cwd(os.getpid())
        self.assertEqual(os.path.realpath(folder), os.path.realpath(os.getcwd()))
        self.assertIsNone(cli._process_cwd(-1))

    def test_finds_a_process_project_setting(self):
        env = dict(os.environ, TEMPO_PROJECT="ONZE HQ")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=env)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.assertEqual(cli._process_project_setting(process.pid), "ONZE HQ")
        plain = {k: v for k, v in os.environ.items() if not k.endswith("_PROJECT")}
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=plain)
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        self.assertIsNone(cli._process_project_setting(other.pid))
