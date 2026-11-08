import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

from tempo import cli
from tests import REAL_START_SERVER, TEST_PORT, RealServerStart
from tests.helpers import DatabaseTest


def bash(command, tool_id):
    """A Bash tool call as a Claude Code transcript records it."""
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def result(tool_id, output, error=False):
    """A tool call's result as a Claude Code transcript records it."""
    return {"type": "tool_result", "tool_use_id": tool_id, "content": output, "is_error": error}


# How a `tempo wait` process started by bin/tempo shows in ps.
WAIT = "/usr/bin/python3 /plugin/tempo/__main__.py wait --timeout 1800"


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
        self.assertIn("Added Q1 in demo: http://127.0.0.1:%d/#/demo/1" % TEST_PORT, out)

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

    def prompt_context(self, payload="{}"):
        """What the UserPromptSubmit hook hands Claude, or "" when it says nothing."""
        out = run("hook", "user-prompt-submit", stdin=payload)[1]
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_hooks_keep_the_session_project_after_claude_cds_into_another(self):
        # A session started in shop has cd'd into blog: the hook input's cwd follows the cd,
        # CLAUDE_PROJECT_DIR stays where the session started.
        del os.environ["TEMPO_PROJECT"]
        for project in ("shop", "blog"):
            self.add(project=project)
            self.store.answer(project, 1, "a")
        payload = json.dumps({"cwd": "/x/blog"})
        with mock.patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/x/shop"}):
            context = self.prompt_context(payload)
            self.add(project="shop")
            self.store.answer("shop", 2, "b")
            stop = run("hook", "stop", stdin=payload)[1]
        self.assertIn("shop Q1", context)
        self.assertNotIn("blog", context)
        self.assertIn("shop Q2", json.loads(stop)["reason"])
        self.assertEqual(len(self.store.undelivered("blog")), 1)
        # Without CLAUDE_PROJECT_DIR (an older Claude Code), the cwd still names it.
        self.assertIn("blog Q1", self.prompt_context(payload))

    def test_wait_uses_the_session_project_folder_when_claude_code_names_it(self):
        del os.environ["TEMPO_PROJECT"]
        self.add(project="shop")
        self.store.follow_up("shop", 1, "Hm?")
        with mock.patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/x/shop"}):
            _, out, _ = run("wait", "--timeout", "5", "--interval", "0.1")
        self.assertIn("shop Q1 (Which way?) asks for more: Hm?", out)

    def test_a_hook_hands_over_only_what_fits_and_leaves_the_rest(self):
        # Claude Code shows a hook's text past 10,000 characters only as a short preview.
        for number in range(1, 61):
            self.add(title="Question %d" % number)
            self.store.answer("demo", number, "a", "A long note. " * 20)
        context = self.prompt_context()
        self.assertLessEqual(len(context), cli.HOOK_TEXT_LIMIT)
        left = len(self.store.undelivered("demo"))
        self.assertGreater(left, 0)
        self.assertIn(cli.MORE_NEWS % left, context)
        self.assertNotIn("demo Q%d " % (61 - left), context)
        # The next prompt brings the next batch, in order.
        self.assertIn("demo Q%d " % (61 - left), self.prompt_context())

    def test_a_note_too_long_alone_is_cut_short_but_delivered(self):
        self.add()
        self.store.answer("demo", 1, "a", "x" * 20000)
        self.add()
        self.store.follow_up("demo", 2, "y" * 20000)
        context = self.prompt_context()
        self.assertLessEqual(len(context), cli.HOOK_TEXT_LIMIT)
        self.assertIn(cli.CUT_SHORT, context)
        self.assertIn(cli.MORE_NEWS % 1, context)
        self.assertEqual([e["number"] for e in self.store.undelivered("demo")], [2])

    def test_the_restart_line_counts_toward_the_hook_limit(self):
        for number in range(1, 61):
            self.add()
            self.store.answer("demo", number, "a", "A long note. " * 20)
        with mock.patch.object(cli, "restart_note", return_value="r" * 3000):
            context = self.prompt_context()
        self.assertLessEqual(len(context), cli.HOOK_TEXT_LIMIT)

    def test_the_stop_hook_hands_over_only_what_fits(self):
        for number in range(1, 61):
            self.add()
            self.store.answer("demo", number, "a", "A long note. " * 20)
        reason = json.loads(run("hook", "stop", stdin="{}")[1])["reason"]
        self.assertLessEqual(len(reason), cli.HOOK_TEXT_LIMIT)
        self.assertIn(cli.MORE_NEWS % len(self.store.undelivered("demo")), reason)

    def test_the_session_note_lists_only_the_open_questions_that_fit(self):
        for number in range(1, 201):
            self.add(title="An open question with a fairly long title, number %d" % number)
        with mock.patch.object(cli, "start_server", return_value=True):
            out = run("hook", "session-start", stdin="{}")[1]
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), cli.HOOK_TEXT_LIMIT)
        listed = context.count("- Q")
        self.assertGreater(listed, 10)
        self.assertIn(cli.MORE_QUESTIONS % (200 - listed), context)

    def test_inbox_hands_over_everything_at_once(self):
        # `tempo inbox` and `tempo wait` print to a Bash tool, which has a larger limit.
        for number in range(1, 61):
            self.add()
            self.store.answer("demo", number, "a", "A long note. " * 20)
        out = run("inbox")[1]
        self.assertIn("demo Q60 ", out)
        self.assertEqual(self.store.undelivered("demo"), [])

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

    def test_answer_records_the_answer_as_the_page_does(self):
        self.add(title="Left or right?")
        code, out, _ = run("answer", "Q1", "B", "--note", "Nicer view")
        self.assertEqual((code, out), (0, "Answered Q1: (b) Right / Note: Nicer view\n"))
        question = self.store.get("demo", 1)
        self.assertEqual(question["status"], "answered")
        self.assertEqual(
            (question["answer"]["author"], question["answer"]["choice"]), ("user", "b")
        )

    def test_answer_names_the_version_it_answered_once_reworded(self):
        self.add()
        self.store.reword("demo", 1, "Swapped", options=["Right", "Left"])
        code, out, _ = run("answer", "1", "b")
        self.assertEqual((code, out), (0, "Answered Q1 (version 2): (b) Left\n"))

    def test_answer_rejects_an_unknown_option_or_question(self):
        self.add()
        code, _, err = run("answer", "1", "z")
        self.assertEqual((code, err), (2, "tempo: Option (z) does not exist\n"))
        code, _, err = run("answer", "9", "a")
        self.assertEqual((code, err), (2, "tempo: Q9 does not exist in demo\n"))
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_answer_takes_an_options_text_in_any_case(self):
        self.add(title="Ship it?", options=["Yes", "No."])
        code, out, _ = run("answer", "Q1", "yes")
        self.assertEqual((code, out), (0, "Answered Q1: (a) Yes\n"))
        self.add(title="Ship it?", options=["Yes", "No."])
        self.assertEqual(run("answer", "2", "NO")[1], "Answered Q2: (b) No.\n")

    def test_answer_and_reply_take_another_project(self):
        self.add(project="shop")
        self.assertEqual(run("reply", "1", "Why?", "--project", "shop")[0], 0)
        self.assertEqual(run("answer", "1", "a", "--project", "shop")[0], 0)
        self.assertEqual(self.store.get("shop", 1)["status"], "answered")

    def test_an_answer_from_the_terminal_reaches_claude_like_one_from_the_page(self):
        self.add(title="Left or right?")
        run("answer", "1", "b", "--note", "Nicer view")
        terminal = run("inbox")[1]
        self.assertEqual(run("inbox")[1], "Nothing new from the user.\n")
        self.store.reopen("demo", 1)
        self.store.db.execute("DELETE FROM entries")
        self.store.answer("demo", 1, "b", "Nicer view")
        self.assertEqual(terminal, run("inbox")[1])
        self.assertIn("demo Q1 (Left or right?) answered (b) Right / Note: Nicer view", terminal)

    def test_reply_records_a_follow_up_that_reaches_claude(self):
        self.add(title="Left or right?")
        code, out, _ = run("reply", "1", "What is on the left?")
        self.assertEqual((code, out), (0, "Added your follow-up to Q1.\n"))
        _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("demo Q1 (Left or right?) asks for more: What is on the left?", context)
        self.assertEqual(run("inbox")[1], "Nothing new from the user.\n")

    def test_reply_needs_some_text(self):
        self.add()
        code, _, err = run("reply", "1", "  ")
        self.assertEqual(code, 2)
        self.assertIn("follow-up", err)
        self.assertEqual(self.store.get("demo", 1)["entries"], [])

    def test_status_counts_this_project_or_every_project(self):
        self.add()
        self.add()
        self.add()
        self.add(project="shop")
        self.store.answer("demo", 2, "a")
        self.assertEqual(run("status"), (0, "? 2\n", ""))
        self.assertEqual(run("status", "--all"), (0, "? 3\n", ""))
        self.assertEqual(run("status", "--project", "shop"), (0, "? 1\n", ""))
        self.assertEqual(run("status", "--project", "none"), (0, "", ""))

    def test_status_counts_the_project_the_folder_is_filed_under(self):
        repo = os.path.realpath(self.tmp.name)
        self.add(project="shop")
        self.add(project="other")
        self.store.see_folder(os.path.basename(repo), repo)
        self.store.db.execute("UPDATE folders SET project = 'shop' WHERE path = ?", (repo,))
        self.store.db.commit()
        env = {key: value for key, value in os.environ.items() if key != "TEMPO_PROJECT"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(cli, "folder_path", return_value=repo),
        ):
            self.assertEqual(run("status"), (0, "? 1\n", ""))
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(cli, "folder_path", return_value=os.path.join(repo, "unknown")),
        ):
            self.assertEqual(run("status"), (0, "", ""))

    def test_status_prints_nothing_when_nothing_is_open(self):
        self.add()
        self.store.answer("demo", 1, "a")
        self.assertEqual(run("status"), (0, "", ""))

    def test_status_never_creates_a_database(self):
        missing = os.path.join(self.tmp.name, "nowhere", "questions.db")
        with mock.patch.dict("os.environ", {"TEMPO_DB": missing}):
            self.assertEqual(run("status"), (0, "", ""))
        self.assertFalse(os.path.exists(os.path.dirname(missing)))

    def test_status_never_migrates_and_reads_what_it_can(self):
        path = os.path.join(self.tmp.name, "old.db")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE questions (project TEXT, number INTEGER, status TEXT)")
        old.execute("INSERT INTO questions VALUES ('demo', 1, 'open')")
        old.commit()
        old.close()
        empty = os.path.join(self.tmp.name, "empty.db")
        open(empty, "w").close()
        with mock.patch.dict("os.environ", {"TEMPO_DB": path}):
            self.assertEqual(run("status"), (0, "? 1\n", ""))
        with mock.patch.dict("os.environ", {"TEMPO_DB": empty}):
            self.assertEqual(run("status"), (0, "", ""))
        old = sqlite3.connect(path)
        self.addCleanup(old.close)
        tables = [row[0] for row in old.execute("SELECT name FROM sqlite_master")]
        self.assertEqual(
            (tables, old.execute("PRAGMA user_version").fetchone()[0]), (["questions"], 0)
        )

    def test_status_reads_a_database_no_one_has_open(self):
        # With no connection open, a WAL database has no -shm file; status must still read it.
        self.add()
        self.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.store.db.close()
        for suffix in ("-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.env["TEMPO_DB"] + suffix)
        self.assertEqual(run("status"), (0, "? 1\n", ""))

    def test_status_never_writes_a_log_left_by_a_killed_process(self):
        # A process killed with writes still in the write-ahead log leaves it behind. A
        # read-write connection would copy the log into the file when it closes.
        self.add()
        self.store.db.close()
        path = self.env["TEMPO_DB"]
        script = (
            "import os, sqlite3, sys\n"
            "db = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
            "db.execute('PRAGMA wal_autocheckpoint = 0')\n"
            "db.execute(\"UPDATE questions SET project = 'shop'\")\n"
            "os._exit(0)\n"
        )
        subprocess.run([sys.executable, "-c", script, path], check=True)

        def state():
            with open(path, "rb") as file:
                return file.read(), os.path.getsize(path + "-wal")

        before = state()
        self.assertGreater(before[1], 0)
        # The count comes from the log: the question moved to shop only there.
        self.assertEqual(run("status", "--project", "shop"), (0, "? 1\n", ""))
        self.assertEqual(state(), before)

    def test_session_start_tells_claude_to_use_tempo_and_lists_open_questions(self):
        self.add()
        self.add(title="Ship it?", goal="Release 0.4", start_here="unlocks the rest")
        with mock.patch.object(cli, "start_server") as start:
            _, out, _ = run("hook", "session-start", stdin="{}")
        start.assert_called_once()
        context = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "SessionStart")
        self.assertIn("goes in Tempo with `tempo add`", context["additionalContext"])
        self.assertIn(
            "Q1 Which way? http://127.0.0.1:%d/#/demo/1" % TEST_PORT, context["additionalContext"]
        )
        self.assertIn(
            "Q2 Ship it? [Release 0.4, start here] http://127.0.0.1:%d/#/demo/2" % TEST_PORT,
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

    def test_stop_catches_questions_in_their_many_forms(self):
        # From the second bug hunt's replies (stop_detect.py, stop_detect2.py).
        cases = [
            "Should I go ahead and delete the old branch?",
            "Which do you prefer:\n(a) Postgres\n(b) SQLite",
            "Which do you prefer:\n- a) Postgres\n- b) SQLite",
            "Which do you prefer?\n1. Postgres\n2. SQLite",
            "Which do you prefer:\n**A.** Postgres\n**B.** SQLite",
            "Shall we merge it now?",
            "Let me know if you want me to push the branch.",
            "Want me to open the PR?",
            "I'll push it once you confirm: should I push to origin?",
            "Next steps for you:\n- Say whether I should delete the branch.",
            'Should I delete "old-branch" now?',
            "Should I run `git push --force` now?",
            "Is it OK if I delete the branch?",
            "Would you prefer Postgres or SQLite?",
            "Ready to merge — go ahead?",
            "Should I merge? I recommend yes, the tests pass.",
            # A dot inside the question: versions, file names, paths, abbreviations.
            "Should I tag v0.5.4 now?",
            "Should I edit README.md as well?",
            "May I delete ~/.claude/tempo/server.log?",
            "Do you want me to bump it to 0.6.0?",
            "Should I open the PR, e.g. against main?",
            "Should I tag v0.5.4 now? (y/n)",
        ]
        for text in cases:
            with self.subTest(text):
                out = self.stop(("user", "Go"), ("assistant", [{"type": "text", "text": text}]))
                self.assertEqual(json.loads(out or "{}").get("decision"), "block")

    def test_stop_lets_statements_and_questions_it_answers_itself_through(self):
        cases = [
            "I asked myself: should I refactor this? No, it's fine as is.",
            'The docs say: "Can I use WAL mode?" Yes.',
            "Fixed the bug where (a) the hook crashed and (b) the server hung.",
            "Done. The test asks `Should I retry?` and returns.",
            "Do you want to know why? Because the lock was missing.",
            "Two bugs: (a) the race and (b) the leak. Both fixed.",
            "Let me know if anything looks off.",
            "Tell me if I missed something.",
            "Let me know whether the page loads now.",
        ]
        for text in cases:
            with self.subTest(text):
                reply = [("user", "Go"), ("assistant", [{"type": "text", "text": text}])]
                self.assertEqual(self.stop(*reply), "")

    def test_stop_lets_tempo_questions_and_old_replies_through(self):
        options = [{"type": "text", "text": "(a) Left (b) Right"}]
        linked = [{"type": "text", "text": "(a) Left (b) Right http://127.0.0.1:2204/#/demo/1"}]
        added = [bash("tempo add --title T", "toolu_1")]
        reworded = [bash("tempo reword 3 --note N", "toolu_2")]
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
                ("user", [result("toolu_1", "Added Q1")]),
                ("assistant", options),
            ],
            "added from a path, after cd and a setting": [
                ("user", "Q?"),
                ("assistant", [bash("cd ~/x && TEMPO_PROJECT=x bin/tempo add --title T", "t3")]),
                ("user", [result("t3", "Added Q1")]),
                ("assistant", options),
            ],
            "linked in the same paragraph": [
                ("user", "Q?"),
                (
                    "assistant",
                    [{"type": "text", "text": "Added Q4 http://127.0.0.1:2204/#/demo/4. May I?"}],
                ),
            ],
        }
        for name, replies in cases.items():
            with self.subTest(name):
                self.assertEqual(self.stop(*replies), "")
        self.assertEqual(self.stop(("user", "Q?"), ("assistant", options), active=True), "")
        self.assertEqual(run("hook", "stop", stdin="{}")[1], "")

    def test_stop_is_not_fooled_by_a_failed_or_unrelated_tempo_add_or_a_far_link(self):
        ask = "Should I delete the old branch?"
        closing = "http://127.0.0.1:2204/#/demo/3"
        cases = {
            "grep for tempo add": [bash("grep -rn 'tempo add' skills/", "t1"), result("t1", "")],
            "failed tempo add": [
                bash("tempo add --title 'Delete it?'", "t1"),
                result("t1", "tempo: the following arguments are required: --context", True),
            ],
            "tempo add named in another tool": [
                {"type": "tool_use", "id": "t1", "name": "Write", "input": {"content": "tempo add"}}
            ],
            "numbered closing list": "\n\nOpen questions:\n1. Q3 Pick a DB " + closing,
            "closing list as a table": "\n\n| Q3 | Pick a DB | %s |" % closing,
            "closing list after a blank line": "\n\nOpen questions:\n- Q3 Pick a DB " + closing,
        }
        for name, extra in cases.items():
            with self.subTest(name):
                if isinstance(extra, str):
                    reply = [("assistant", [{"type": "text", "text": ask + extra}])]
                else:
                    uses = [block for block in extra if block["type"] == "tool_use"]
                    results = [block for block in extra if block["type"] == "tool_result"]
                    reply = [("assistant", uses)]
                    reply += [("user", results)] if results else []
                    reply += [("assistant", [{"type": "text", "text": ask}])]
                out = self.stop(("user", "Go"), *reply)
                self.assertEqual(json.loads(out or "{}").get("decision"), "block")

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

    def test_stop_raises_follow_ups_left_unanswered(self):
        self.add(title="Ship it?")
        self.add()
        self.store.follow_up("demo", 1, "What is left?")
        self.marker(os.getpid())  # A wait is running, so only the follow-up can block.
        reply = [("user", "Go"), ("assistant", [{"type": "text", "text": "Done."}])]
        # New news is handed over first; the follow-up then gets a few minutes.
        self.assertIn("asks for more: What is left?", json.loads(self.stop(*reply))["reason"])
        self.assertEqual(self.stop(*reply), "")
        self.store.db.execute("UPDATE entries SET delivered_at = '2026-01-01T10:00:00Z'")
        out = json.loads(self.stop(*reply))
        self.assertEqual(out["decision"], "block")
        self.assertIn("demo Q1 (Ship it?) asked: What is left?", out["reason"])
        self.assertIn("`tempo reword 1 --context`", out["reason"])
        self.assertEqual(self.stop(*reply, active=True), "")
        self.store.reword("demo", 1, "Answered the follow-up", context="Only the docs.")
        self.assertEqual(self.stop(*reply), "")
        # On an answered question, the reply goes in the thread: a reword would reopen it.
        self.store.answer("demo", 2, "a")
        self.store.follow_up("demo", 2, "Sure?")
        self.store.take_undelivered("demo")
        self.store.db.execute("UPDATE entries SET delivered_at = '2026-01-01T10:00:00Z'")
        self.assertIn("`tempo context 2`", json.loads(self.stop(*reply))["reason"])
        self.assertIn("hook stop demo blocked: unanswered follow-ups 1", self.hook_log())

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
        with (
            mock.patch.object(cli, "start_server", return_value=True),
            mock.patch.object(cli, "session_note", side_effect=RuntimeError("broken")),
        ):
            run("hook", "session-start", stdin="{}")
        self.assertEqual(
            self.hook_log(),
            [
                "hook session-start demo note sent, page start requested",
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
            del os.environ["TEMPO_PROJECT"], os.environ["TEMPO_PORT"]
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

    # The page turned off with `tempo config page off`

    def test_config_shows_and_changes_the_page_setting(self):
        self.assertEqual(
            run("config"),
            (0, "page on: each session starts the page at http://127.0.0.1:%d/\n" % TEST_PORT, ""),
        )
        code, out, _ = run("config", "page", "off")
        self.assertEqual(code, 0)
        self.assertIn("page off", out)
        self.assertIn("tempo serve --background", out)
        self.assertFalse(self.store.start_page())
        self.assertIn("page off", run("config", "page")[1])
        run("config", "page", "on")
        self.assertTrue(self.store.start_page())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["config", "page", "maybe"])

    def test_with_the_page_off_the_session_start_hook_leaves_it_alone(self):
        self.add()
        self.store.set_start_page(False)
        with mock.patch.object(cli, "start_server") as start:
            _, out, _ = run("hook", "session-start", stdin="{}")
        start.assert_not_called()
        note = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("127.0.0.1", note)
        self.assertIn("never give page links", note)
        self.assertIn("- Q1 Which way? `tempo show 1`, answer with `tempo answer 1 <letter>`", note)
        self.assertIn("the number, then the title, as in `- Q3 Pick a colour`", note)
        self.assertIn("hook session-start demo note sent, page off", self.hook_log())

    def test_a_page_that_fails_to_start_does_not_stop_the_session_note(self):
        self.add()
        with mock.patch.object(cli, "start_server", side_effect=OSError("no room")):
            code, out, err = run("hook", "session-start", stdin="{}")
        self.assertEqual(code, 0)
        self.assertIn("Q1", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("the page did not start: no room", err)
        self.assertIn("hook session-start demo note sent, page not started", self.hook_log())

    def test_an_unreadable_page_setting_counts_as_on(self):
        with mock.patch.object(cli, "Store", side_effect=OSError("locked")):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertTrue(cli.page_on())
        self.assertIn("page setting not read: locked", err.getvalue())

    def test_the_session_note_asks_for_linked_question_numbers(self):
        with mock.patch.object(cli, "start_server"):
            _, out, _ = run("hook", "session-start", stdin="{}")
        note = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("the number as a link to the question, then the title", note)
        self.assertIn("`- [Q3](http://127.0.0.1:2204/#/shop/3) Pick a colour`", note)
        self.assertIn("Never print a bare link.", note)
        # Answers from the terminal, the tui and slash commands don't approve merging either.
        self.assertIn("An answer in Tempo never approves merging", note)
        self.assertNotIn("on the Tempo page", note)

    def test_with_the_page_off_serve_still_starts_it(self):
        self.store.set_start_page(False)
        with mock.patch.object(cli, "start_server", return_value=True) as start:
            self.assertEqual(run("serve", "--background")[0], 0)
        start.assert_called_once()

    def test_answers_arrive_with_one_wording_wherever_they_were_given(self):
        self.add()
        run("answer", "1", "a")
        _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        heading = "New from the user in Tempo (http://127.0.0.1:"
        self.assertTrue(context.startswith(heading), context)
        self.assertNotIn("on the Tempo page", context)

    def test_with_the_page_off_texts_point_to_the_terminal(self):
        self.store.set_start_page(False)
        _, out, _ = run("add", "--title", "T", "--context", "C", "--option", "x", "--option", "y")
        self.assertEqual(
            out, "Added Q1 in demo: `tempo show 1`, answer with `tempo answer 1 <letter>`\n"
        )
        self.store.answer("demo", 1, "a")
        _, out, _ = run("hook", "user-prompt-submit", stdin="{}")
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("New from the user in Tempo:"), context)
        self.add()
        reply = [("user", "Go"), ("assistant", [{"type": "text", "text": "Done."}])]
        reason = json.loads(self.stop(*reply))["reason"]
        self.assertIn("so answers would not reach you", reason)

    def test_with_the_page_off_a_question_number_counts_as_its_link(self):
        self.add()
        self.store.set_start_page(False)
        self.marker(os.getpid())

        def reply(text):
            return self.stop(("user", "Go"), ("assistant", [{"type": "text", "text": text}]))

        self.assertEqual(reply("Q1 asks which way. (a) Left (b) Right"), "")
        reason = json.loads(reply("May I push?"))["reason"]
        self.assertIn("give its number (such as Q5)", reason)
        self.assertNotIn("link", reason)
        # The closing list names Q1, but that doesn't record the question asked above it.
        self.assertIn("block", reply("May I push?\n\n- Q1 Which way? `tempo show 1`"))


class GitTest(DatabaseTest):
    """Runs real git commands in temporary folders. Git's own variables, set when the
    tests run from a git hook or `git rebase --exec`, would point those commands at the
    repo running the tests instead, so they are cleared. The developer's own git settings
    (hooks through core.hooksPath, a template folder for git init) are left out too."""

    def setUp(self):
        super().setUp()
        clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        clean.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
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


class RepoIdentityNoteTest(GitTest):
    """Real repos with commits and remotes, first seen by a session: a missing one is
    offered only to a copy of it."""

    # No signing: the test must not depend on how the developer signs commits.
    git = ("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false")
    start = RenameNoteTest.start

    def setUp(self):
        super().setUp()
        del os.environ["TEMPO_PROJECT"]
        self.lab = os.path.realpath(self.tmp.name)
        self.old = os.path.join(self.lab, "open-questions")
        self.new = os.path.join(self.lab, "tempo")
        self.repo(self.old, "git@github.com:me/open-questions.git")
        self.start(self.old)
        self.store.rename_project("open-questions", "Tempo")

    def repo(self, path, remote=None):
        subprocess.run([*self.git, "init", "-q", path], check=True)
        # Its own message: two empty commits made in the same second would be the same.
        message = os.path.basename(path)
        commit = [*self.git, "-C", path, "commit", "-q", "--allow-empty", "-m", message]
        subprocess.run(commit, check=True)
        if remote:
            subprocess.run(["git", "-C", path, "remote", "add", "origin", remote], check=True)

    def test_the_renamed_repo_is_offered(self):
        os.rename(self.old, self.new)
        self.assertIn("looks renamed or moved", self.start(self.new))

    def test_a_repo_renamed_on_github_too_is_offered(self):
        os.rename(self.old, self.new)
        url = "git@github.com:me/tempo.git"
        subprocess.run(["git", "-C", self.new, "remote", "set-url", "origin", url], check=True)
        self.assertIn("looks renamed or moved", self.start(self.new))

    def test_an_unrelated_repo_is_not_offered_a_deleted_one(self):
        shutil.rmtree(self.old)
        self.repo(self.new, "git@github.com:me/tempo.git")
        self.assertNotIn("is new to Tempo", self.start(self.new))
        self.assertEqual(self.store.renames(), [])

    def test_without_git_on_path_sessions_still_start(self):
        shutil.rmtree(self.old)
        self.repo(self.new)
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            note = self.start(self.new)
            self.assertEqual(cli.repo_identity(self.new), (None, None))
        self.assertNotIn("is new to Tempo", note)
        self.assertEqual(self.store.project_for("tempo", self.new), "tempo")

    def forget_identity(self, path):
        """Make path look remembered before identities were stored."""
        self.store.db.execute(
            "UPDATE folders SET first_commit = NULL, remote = NULL WHERE path = ?", (path,)
        )
        self.store.db.commit()

    def identity_of(self, path):
        row = self.store.db.execute(
            "SELECT first_commit, remote FROM folders WHERE path = ?", (path,)
        ).fetchone()
        return tuple(row)

    def test_a_known_repo_without_identity_gets_it_once(self):
        self.forget_identity(self.old)
        self.start(self.old)
        first_commit, remote = self.identity_of(self.old)
        self.assertTrue(first_commit)
        self.assertEqual(remote, "git@github.com:me/open-questions.git")
        with mock.patch.object(cli, "repo_identity") as identity:
            self.start(self.old)
        identity.assert_not_called()
        # Now a copy of it is matched when it goes missing.
        os.rename(self.old, self.new)
        self.assertIn("looks renamed or moved", self.start(self.new))

    def test_a_known_repo_fills_in_a_stripped_remote(self):
        url = "https://me:secret@github.com/me/open-questions.git"
        subprocess.run(["git", "-C", self.old, "remote", "set-url", "origin", url], check=True)
        self.forget_identity(self.old)
        self.start(self.old)
        self.assertEqual(self.identity_of(self.old)[1], "https://github.com/me/open-questions.git")

    def test_a_known_folder_outside_git_makes_no_git_call(self):
        scratch = os.path.join(self.lab, "scratch")
        os.makedirs(scratch)
        self.start(scratch)
        with mock.patch.object(cli, "repo_identity") as identity:
            self.start(scratch)
        identity.assert_not_called()
        self.assertEqual(self.identity_of(scratch), (None, None))

    def test_without_git_on_path_a_known_repo_still_starts_and_fills_in_later(self):
        self.forget_identity(self.old)
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertIn("Tempo", self.start(self.old))
        self.assertEqual(self.identity_of(self.old), (None, None))
        self.start(self.old)
        self.assertTrue(self.identity_of(self.old)[0])

    def test_a_token_in_the_remote_address_is_not_kept(self):
        url = "https://me:secret@github.com/me/open-questions.git"
        subprocess.run(["git", "-C", self.old, "remote", "set-url", "origin", url], check=True)
        _, remote = cli.repo_identity(self.old)
        self.assertEqual(remote, "https://github.com/me/open-questions.git")


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
        # The real start_server; the suite swaps in one that fails (see tests/__init__.py).
        self.patch("start_server", new=REAL_START_SERVER)
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

    def test_keeps_a_tempo_that_does_not_name_its_database_unless_this_is_the_real_one(self):
        # 0.5 and older leave the database out of /health. The 0.6.0 release's tests, on a
        # test database, once replaced the user's 0.5.4 page this way.
        old = {key: value for key, value in self.current.items() if key != "db"}
        self.serve(health={**old, "version": "0.0.1"})
        self.assertFalse(cli.start_server(2204))
        self.kill.assert_not_called()
        self.popen.assert_not_called()
        # Code on the real database still replaces it, so a 0.5 page is upgraded.
        with mock.patch.object(cli, "default_db", return_value=cli.db_path()):
            self.assertTrue(cli.start_server(2204))
        self.kill.assert_called_once_with(42, cli.signal.SIGTERM)

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


class NoRealServerTest(DatabaseTest):
    """The suite never starts or stops a real page server. A test once ran the SessionStart
    hook without mocking start_server: it started a server on 2204, on the test's database,
    that replaced the user's page and kept running after the tests."""

    def test_the_tests_page_is_not_the_real_one(self):
        self.assertEqual(cli._port(), TEST_PORT)
        self.assertNotEqual(TEST_PORT, cli.DEFAULT_PORT)
        self.assertNotIn(TEST_PORT, cli.BRANCH_PORTS)

    def test_a_hook_that_would_start_the_page_fails_the_test(self):
        # The hook turns any Exception into "page not started"; this one gets through.
        with self.assertRaises(RealServerStart):
            run("hook", "session-start", stdin="{}")

    def test_serving_in_the_background_fails_the_test(self):
        with self.assertRaises(RealServerStart):
            run("serve", "--background")


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
            ("/usr/bin/python3 /plugin/tempo/__main__.py serve --port 2204\n", 4242),
            ("/usr/bin/python3 -m http.server 2204\n", None),
        ]:
            with self.subTest(command=command):
                runs = [ran("4242\n"), ran(command)]
                with mock.patch.object(cli.subprocess, "run", side_effect=runs):
                    self.assertEqual(cli._tempo_pid(2204), pid)


class WaitProcessesTest(unittest.TestCase):
    def start(self, *args, env=None):
        """Start a Python that sleeps, and return once it runs.

        Popen returns as soon as the new program is loaded, which can be before Linux has
        filled in its command line and environment in /proc. Waiting for the child to print
        a line means its Python is already running, so both are in place.
        """
        code = "import sys, time; print('ready', flush=True); time.sleep(30)"
        process = subprocess.Popen(
            [sys.executable, "-c", code, *args], env=env, stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.assertEqual(process.stdout.readline(), "ready\n")
        return process

    def test_lists_the_processes_running_tempo_wait(self):
        wait = self.start("-m", "tempo", "wait", "--timeout", "1800")
        other = self.start("-m", "tempo", "serve")
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
        process = self.start(env=dict(os.environ, TEMPO_PROJECT="ONZE HQ"))
        self.assertEqual(cli._process_project_setting(process.pid), "ONZE HQ")
        plain = {k: v for k, v in os.environ.items() if not k.endswith("_PROJECT")}
        other = self.start(env=plain)
        self.assertIsNone(cli._process_project_setting(other.pid))
