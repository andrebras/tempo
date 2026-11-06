import os
import sqlite3

from open_questions.store import SCHEMA, QuestionError, Store, connect
from tests.helpers import DatabaseTest


class AddTest(DatabaseTest):
    def test_numbers_questions_per_project(self):
        self.assertEqual(self.add()["label"], "Q1")
        self.assertEqual(self.add()["label"], "Q2")
        self.assertEqual(self.add(project="other")["label"], "Q1")

    def test_letters_options_and_keeps_the_recommendation(self):
        question = self.add(options=["Left", "Right", "Back"], recommended="B")
        self.assertEqual([o["letter"] for o in question["options"]], ["a", "b", "c"])
        self.assertEqual(question["recommended"], "b")

    def test_refuses_incomplete_questions(self):
        for overrides in [
            dict(project=" "),
            dict(title=""),
            dict(context=""),
            dict(options=["Only one"]),
            dict(options=["Left", " "]),
            dict(recommended="z"),
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(QuestionError):
                self.add(**overrides)

    def test_never_reuses_a_number(self):
        self.add(number=40)
        self.assertEqual(self.add()["label"], "Q41")
        with self.assertRaises(QuestionError):
            self.add(number=12)

    def test_set_next_only_moves_forward(self):
        self.store.set_next("demo", 281)
        self.assertEqual(self.add()["label"], "Q281")
        with self.assertRaises(QuestionError):
            self.store.set_next("demo", 5)


class ThreadTest(DatabaseTest):
    def test_reword_adds_a_version_and_keeps_the_old_one(self):
        self.add(recommended="a")
        question = self.store.reword("demo", 1, "Clearer", title="Left or right?")
        self.assertEqual(question["version"], 2)
        self.assertEqual(question["title"], "Left or right?")
        self.assertEqual(question["context"], "We need to pick one.")
        self.assertEqual(question["recommended"], "a")
        self.assertEqual(question["versions"][0]["title"], "Which way?")
        self.assertEqual(question["entries"][-1]["kind"], "reword")

    def test_reword_drops_a_recommendation_whose_option_is_gone(self):
        self.add(options=["Left", "Right", "Back"], recommended="c")
        question = self.store.reword("demo", 1, "Fewer options", options=["Up", "Down"])
        self.assertIsNone(question["recommended"])

    def test_answer_by_option_records_the_version_seen(self):
        self.add()
        self.store.reword("demo", 1, "Clearer", title="Left or right?")
        question = self.store.answer("demo", 1, "B", "Because of the view")
        self.assertEqual(question["status"], "answered")
        self.assertEqual(question["answer"]["choice"], "b")
        self.assertEqual(question["answer"]["version"], 2)

    def test_a_second_answer_needs_the_question_reopened(self):
        self.add()
        self.store.answer("demo", 1, "a")
        with self.assertRaises(QuestionError):
            self.store.answer("demo", 1, "b")
        self.assertEqual(self.store.reopen("demo", 1)["status"], "open")
        question = self.store.answer("demo", 1, "b")
        self.assertEqual([e["choice"] for e in question["entries"]], ["a", "b"])
        self.assertEqual(question["answer"]["choice"], "b")

    def test_only_answered_questions_can_be_reopened(self):
        self.add()
        with self.assertRaises(QuestionError):
            self.store.reopen("demo", 1)

    def test_answer_needs_a_real_option_or_text(self):
        self.add()
        with self.assertRaises(QuestionError):
            self.store.answer("demo", 1, "c")
        with self.assertRaises(QuestionError):
            self.store.answer("demo", 1, None, "  ")
        self.assertEqual(self.store.answer("demo", 1, None, "Neither")["status"], "answered")

    def test_rewording_an_answered_question_opens_it_again(self):
        self.add()
        self.store.answer("demo", 1, None, "Give me more options")
        question = self.store.reword("demo", 1, "More options", options=["Up", "Down", "Left"])
        self.assertEqual(question["status"], "open")
        self.assertEqual(question["answer"]["version"], 1)

    def test_archived_questions_cannot_be_reworded(self):
        self.add()
        self.store.answer("demo", 1, "a")
        self.store.archive_answered("demo")
        with self.assertRaises(QuestionError):
            self.store.reword("demo", 1, "Too late", title="New")

    def test_user_entries_wait_for_delivery_and_claude_entries_do_not(self):
        self.add()
        self.store.add_context("demo", 1, "More detail")
        self.store.follow_up("demo", 1, "What does left mean?")
        pending = self.store.undelivered("demo")
        self.assertEqual([entry["kind"] for entry in pending], ["followup"])
        self.store.mark_delivered([entry["id"] for entry in pending])
        self.assertEqual(self.store.undelivered("demo"), [])

    def test_undelivered_is_scoped_by_project(self):
        self.add()
        self.add(project="other")
        self.store.follow_up("other", 1, "Hm?")
        self.assertEqual(self.store.undelivered("demo"), [])
        self.assertEqual(len(self.store.undelivered()), 1)


class ListTest(DatabaseTest):
    def test_lists_by_priority_then_number_within_a_project(self):
        self.add(title="Calm", priority="later")
        self.add(title="Normal")
        self.add(title="Hurry", priority="now")
        self.assertEqual([q["title"] for q in self.store.list("demo")], ["Hurry", "Normal", "Calm"])

    def test_priority_must_be_a_known_level(self):
        with self.assertRaises(QuestionError):
            self.add(priority="urgent")
        self.add()
        self.assertEqual(self.store.set_priority("demo", 1, "LATER")["priority"], "later")

    def test_upgrades_a_database_that_still_has_the_urgent_flag(self):
        path = os.path.join(self.tmp.name, "old.db")
        old = sqlite3.connect(path)
        old.executescript(
            SCHEMA.replace(
                "priority TEXT NOT NULL DEFAULT 'soon'"
                " CHECK (priority IN ('now', 'soon', 'later'))",
                "urgent INTEGER NOT NULL DEFAULT 0",
            )
        )
        old.execute("INSERT INTO projects (name) VALUES ('demo')")
        old.execute(
            "INSERT INTO questions (project, number, urgent, created_at, updated_at)"
            " VALUES ('demo', 1, 1, 'x', 'x'), ('demo', 2, 0, 'x', 'x')"
        )
        old.commit()
        old.close()
        with Store(connect(path)) as store:
            rows = store.db.execute("SELECT number, priority FROM questions ORDER BY number")
            self.assertEqual([tuple(row) for row in rows], [(1, "now"), (2, "soon")])

    def test_archive_moves_only_answered_questions(self):
        self.add()
        self.add()
        self.store.answer("demo", 1, "a")
        self.assertEqual(self.store.archive_answered("demo"), 1)
        self.assertEqual(
            [q["status"] for q in self.store.list("demo", "all")], ["archived", "open"]
        )
