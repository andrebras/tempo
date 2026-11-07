"""The hooks as Claude Code runs them: `bin/tempo hook …` in a separate process, with the
JSON Claude Code sends on stdin, against a temporary database. An answer saved the way
the page saves it must reach Claude exactly once, and a follow-up left unanswered is
raised when the reply ends."""

import json
import os
import subprocess
import tempfile
import unittest

from tempo.store import CODE_ROOT, Store, connect
from tests import TEST_PORT

TEMPO = os.path.join(CODE_ROOT, "bin", "tempo")
SESSION = "5f0c9a1e-3b7d-4c2a-9e61-0d8f2b4a7c35"


class HooksEndToEndTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # The session's folder, outside git, so its project is named after it.
        self.cwd = os.path.join(tmp.name, "shop")
        os.mkdir(self.cwd)
        home = os.path.join(tmp.name, "home")
        os.mkdir(home)
        self.db = os.path.join(tmp.name, "questions.db")
        self.transcript = os.path.join(tmp.name, "%s.jsonl" % SESSION)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TEMPO_", "OQ_")) and key != "PYTHONPATH"
        }
        self.env.update(HOME=home, TEMPO_DB=self.db, TEMPO_PORT=str(TEST_PORT))
        self.reply("Carry on", "Done: the tests pass.")

    def reply(self, prompt, text):
        """Write a transcript in which the user asked something and Claude answered."""
        lines = [
            {
                "type": "user",
                "sessionId": SESSION,
                "cwd": self.cwd,
                "message": {"role": "user", "content": prompt},
            },
            {
                "type": "assistant",
                "sessionId": SESSION,
                "cwd": self.cwd,
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            },
        ]
        with open(self.transcript, "w", encoding="utf-8") as file:
            file.writelines(json.dumps(line) + "\n" for line in lines)

    def tempo(self, *args, payload=None):
        result = subprocess.run(
            ["sh", TEMPO, *args],
            cwd=self.cwd,
            env=self.env,
            input=json.dumps(payload) if payload is not None else "",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def prompt(self):
        """What the UserPromptSubmit hook adds to the session's context, or ""."""
        out = self.tempo(
            "hook",
            "user-prompt-submit",
            payload={
                "session_id": SESSION,
                "transcript_path": self.transcript,
                "cwd": self.cwd,
                "permission_mode": "default",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Carry on",
            },
        )
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def stop(self, active=False):
        """Why the Stop hook sends Claude back, or ""."""
        out = self.tempo(
            "hook",
            "stop",
            payload={
                "session_id": SESSION,
                "transcript_path": self.transcript,
                "cwd": self.cwd,
                "permission_mode": "default",
                "hook_event_name": "Stop",
                "stop_hook_active": active,
            },
        )
        if not out:
            return ""
        decision = json.loads(out)
        self.assertEqual(decision["decision"], "block")
        return decision["reason"]

    def ask_and_answer(self):
        added = self.tempo(
            "add", "--title", "Which DB?", "--context", "C", "--option", "Postgres",
            "--option", "SQLite",
        )  # fmt: skip
        self.assertIn("Added Q1 in shop", added)
        # The page saves an answer through the store, as here.
        with Store(connect(self.db)) as store:
            store.answer("shop", 1, "b", "Smaller")

    def test_an_answer_reaches_the_next_prompt_once(self):
        self.ask_and_answer()
        context = self.prompt()
        self.assertIn("shop Q1 (Which DB?) answered (b) SQLite / Note: Smaller", context)
        self.assertEqual(self.prompt(), "")
        self.assertEqual(self.stop(), "")

    def test_an_answer_reaches_the_end_of_the_reply_once(self):
        self.ask_and_answer()
        self.assertIn("shop Q1 (Which DB?) answered (b) SQLite", self.stop())
        self.assertEqual(self.stop(active=True), "")
        self.assertEqual(self.stop(), "")
        self.assertEqual(self.prompt(), "")
        with Store(connect(self.db)) as store:
            self.assertEqual(store.undelivered(), [])

    def test_an_answer_from_the_terminal_reaches_the_next_prompt_once(self):
        self.tempo(
            "add", "--title", "Which DB?", "--context", "C", "--option", "Postgres",
            "--option", "SQLite",
        )  # fmt: skip
        self.assertEqual(
            self.tempo("answer", "1", "b", "--note", "Smaller"),
            "Answered Q1: (b) SQLite / Note: Smaller\n",
        )
        context = self.prompt()
        self.assertIn("shop Q1 (Which DB?) answered (b) SQLite / Note: Smaller", context)
        self.assertEqual(self.prompt(), "")
        self.assertEqual(self.stop(), "")

    def test_a_follow_up_left_unanswered_is_raised_until_answered(self):
        self.tempo("add", "--title", "Ship?", "--context", "C", "--option", "Yes", "--option", "No")
        with Store(connect(self.db)) as store:
            store.follow_up("shop", 1, "What is left?")
        self.assertIn("shop Q1 (Ship?) asks for more: What is left?", self.prompt())
        # The reply that got it ended without an answer, and some minutes went by.
        with Store(connect(self.db)) as store:
            store.db.execute("UPDATE entries SET delivered_at = '2026-01-01T10:00:00Z'")
        reason = self.stop()
        self.assertIn("shop Q1 (Ship?) asked: What is left?", reason)
        self.assertIn("`tempo reword 1 --context`", reason)
        self.assertEqual(self.stop(active=True), "")
        self.tempo("reword", "1", "--note", "Answered the follow-up", "--context", "Docs only.")
        # Answered, so it is not raised again (the hook may still ask for a wait).
        self.assertNotIn("What is left?", self.stop())


if __name__ == "__main__":
    unittest.main()
