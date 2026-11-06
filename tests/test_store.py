import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from tempo.store import MIGRATIONS, SCHEMA, QuestionError, Store, _migrate, connect, db_path
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

    def test_lists_answered_questions_newest_answer_first(self):
        for _ in range(3):
            self.add()
        # Answered in the order Q2, Q3, Q1, an hour apart.
        for hours_ago, number in ((3, 2), (2, 3), (1, 1)):
            self.store.answer("demo", number, "a")
            self.store.db.execute(
                "UPDATE entries SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)"
                " WHERE question_id = (SELECT id FROM questions WHERE number = ?)",
                ("-%d hours" % hours_ago, number),
            )
        self.assertEqual([q["number"] for q in self.store.list("demo", "answered")], [1, 3, 2])

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
        old.execute(
            "INSERT INTO versions (question_id, version, title, context, options, created_at)"
            " VALUES (1, 1, 'Which way?', 'We need to pick one.', '[]', 'x')"
        )
        old.execute("INSERT INTO folders (folder) VALUES ('dataforge')")
        old.commit()
        old.close()
        with Store(connect(path)) as store:
            rows = store.db.execute("SELECT number, priority FROM questions ORDER BY number")
            self.assertEqual([tuple(row) for row in rows], [(1, "now"), (2, "soon")])
            title = store.db.execute("SELECT title FROM versions").fetchone()[0]
            self.assertEqual(title, "Which way?")
            self.assertEqual(store.folders()[0]["project"], "dataforge")
            self.assertEqual(_user_version(store.db), len(MIGRATIONS))

    def test_answers_older_than_a_week_are_archived_when_listing(self):
        for _ in range(3):
            self.add()
        self.store.answer("demo", 1, "a")
        self.store.answer("demo", 2, "a")
        self.store.db.execute(
            "UPDATE entries SET created_at = '2020-01-01T00:00:00Z' WHERE question_id ="
            " (SELECT id FROM questions WHERE number = 1)"
        )
        self.assertEqual(
            [q["status"] for q in self.store.list("demo", "all")], ["archived", "answered", "open"]
        )

    def test_archive_moves_only_answered_questions(self):
        self.add()
        self.add()
        self.store.answer("demo", 1, "a")
        self.assertEqual(self.store.archive_answered("demo"), 1)
        self.assertEqual(
            [q["status"] for q in self.store.list("demo", "all")], ["archived", "open"]
        )


class ProjectsTest(DatabaseTest):
    def test_a_new_folder_is_filed_under_its_own_name(self):
        self.store.see_folder("onzehq")
        self.assertEqual(self.store.project_for("onzehq"), "onzehq")
        self.assertEqual(self.store.project_for("never-seen"), "never-seen")
        self.assertEqual(
            self.store.project_summaries(),
            [{"name": "onzehq", "questions": 0, "folders": ["onzehq"]}],
        )

    def test_rename_moves_questions_numbering_and_folders(self):
        self.add(project="onzehq")
        self.store.set_next("onzehq", 40)
        self.add(project="onzehq")
        self.store.follow_up("onzehq", 40, "Why?")
        self.store.see_folder("onzehq")
        self.store.rename_project("onzehq", "ONZE HQ")
        self.assertEqual(self.store.projects(), ["ONZE HQ"])
        self.assertEqual([q["label"] for q in self.store.list("ONZE HQ")], ["Q1", "Q40"])
        self.assertEqual(self.add(project="ONZE HQ")["label"], "Q41")
        self.assertEqual(self.store.undelivered("ONZE HQ")[0]["project"], "ONZE HQ")
        self.assertEqual(self.store.project_for("onzehq"), "ONZE HQ")
        self.store.see_folder("onzehq")
        self.assertEqual(self.store.project_for("onzehq"), "ONZE HQ")

    def test_rename_maps_a_folder_that_was_never_seen(self):
        self.add(project="onzehq")
        self.store.rename_project("onzehq", "ONZE HQ")
        self.assertEqual(self.store.project_for("onzehq"), "ONZE HQ")

    def test_renaming_an_empty_project_into_another_joins_its_folders(self):
        self.add(project="onzehq")
        self.store.see_folder("onzehq")
        self.store.see_folder("onzehq-api")
        self.store.rename_project("onzehq-api", "onzehq")
        self.assertEqual(
            self.store.project_summaries(),
            [{"name": "onzehq", "questions": 1, "folders": ["onzehq", "onzehq-api"]}],
        )

    def test_renaming_only_changes_the_case_keeps_everything(self):
        # What happened for real: dataforge became DataForge, and its folder stayed behind
        # as an empty dataforge project.
        self.add(project="dataforge")
        self.store.see_folder("dataforge")
        self.store.rename_project("dataforge", "DataForge")
        self.assertEqual(
            self.store.project_summaries(),
            [{"name": "DataForge", "questions": 1, "folders": ["dataforge"]}],
        )

    def test_rename_merges_into_a_project_without_questions(self):
        self.add(project="onzehq")
        self.store.set_next("ONZE HQ", 10)
        self.store.rename_project("onzehq", "ONZE HQ")
        self.assertEqual(self.store.get("ONZE HQ", 1)["label"], "Q1")
        self.assertEqual(self.add(project="ONZE HQ")["label"], "Q10")

    def test_rename_refuses_a_clash_and_changes_nothing(self):
        self.add(project="onzehq")
        self.add(project="ONZE HQ")
        for old, new in [
            ("onzehq", "ONZE HQ"),
            ("onzehq", " "),
            ("onzehq", "onzehq"),
            ("missing", "Anything"),
        ]:
            with self.subTest(old=old, new=new), self.assertRaises(QuestionError):
                self.store.rename_project(old, new)
        self.assertEqual(self.store.projects(), ["ONZE HQ", "onzehq"])
        self.assertEqual(len(self.store.list("onzehq")), 1)

    def test_old_folders_without_a_project_are_filed_under_their_name(self):
        self.store.db.execute("INSERT INTO folders (folder) VALUES ('dataforge')")
        self.store.db.execute("PRAGMA user_version = 0")
        _migrate(self.store.db)
        self.assertEqual(self.store.folders()[0]["project"], "dataforge")


class MigrateTest(DatabaseTest):
    def test_a_fresh_database_ends_at_the_latest_version(self):
        self.assertEqual(_user_version(self.store.db), len(MIGRATIONS))

    def test_an_unversioned_database_that_already_has_the_changes_upgrades(self):
        # Databases from before user_version: current shape, but at version 0.
        self.add(priority="later")
        self.store.db.execute("INSERT INTO folders (folder, project) VALUES ('web', 'demo')")
        self.store.db.execute("PRAGMA user_version = 0")
        with Store(connect(os.environ["TEMPO_DB"])) as store:
            self.assertEqual(_user_version(store.db), len(MIGRATIONS))
            self.assertEqual(store.get("demo", 1)["priority"], "later")
            self.assertEqual(store.folders()[0]["project"], "demo")

    def test_connecting_again_does_not_redo_the_steps(self):
        # Filling folder projects is a step; a second connect must leave this one alone.
        self.store.db.execute("INSERT INTO folders (folder) VALUES ('dataforge')")
        with Store(connect(os.environ["TEMPO_DB"])) as store:
            self.assertIsNone(store.folders()[0]["project"])
            self.assertEqual(_user_version(store.db), len(MIGRATIONS))


def _user_version(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


class DbPathTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        patcher = mock.patch.dict("os.environ", {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("TEMPO_DB", "OQ_DB"):
            os.environ.pop(key, None)

    def create(self, folder):
        path = os.path.join(self.home, ".claude", folder, "questions.db")
        os.makedirs(os.path.dirname(path))
        open(path, "w").close()
        return path

    def test_defaults_to_the_tempo_folder(self):
        self.assertEqual(db_path(), os.path.join(self.home, ".claude", "tempo", "questions.db"))

    def test_uses_the_old_database_where_it_is_when_there_is_no_new_one(self):
        old = self.create("open-questions")
        self.assertEqual(db_path(), old)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "tempo")))

    def test_prefers_the_new_database_when_both_exist(self):
        self.create("open-questions")
        new = self.create("tempo")
        self.assertEqual(db_path(), new)

    def test_an_explicit_path_wins(self):
        self.create("open-questions")
        with mock.patch.dict("os.environ", {"OQ_DB": "/elsewhere/questions.db"}):
            self.assertEqual(db_path(), "/elsewhere/questions.db")
