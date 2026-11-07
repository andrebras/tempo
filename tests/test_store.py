import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from tempo.store import (
    MIGRATIONS,
    SCHEMA,
    QuestionError,
    Store,
    _add_folder_paths,
    _migrate,
    connect,
    db_path,
)
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
    def test_lists_whether_a_follow_up_is_waiting_on_claude(self):
        for _ in range(3):
            self.add()
        self.store.follow_up("demo", 2, "Why?")
        self.store.follow_up("demo", 3, "And?")
        self.store.mark_delivered(
            [e["id"] for e in self.store.undelivered("demo") if e["number"] == 3]
        )
        self.assertEqual([q["waiting"] for q in self.store.list("demo")], [None, "sent", "seen"])
        self.store.add_context("demo", 3, "Because.")
        self.assertIsNone(self.store.list("demo")[2]["waiting"])

    def test_groups_open_questions_by_goal_oldest_group_first_and_no_goal_last(self):
        self.add(title="Loose")
        self.add(title="Release one", goal="Release 0.4")
        self.add(title="Settings one", goal="Q5 settings")
        self.add(title="Release two", goal="Release 0.4")
        self.add(title="Settings two", goal="Q5 settings")
        self.add(title="Other project", project="other", goal="Release 0.4")
        self.assertEqual(
            [q["title"] for q in self.store.list(status="open")],
            [
                "Release one",
                "Release two",
                "Settings one",
                "Settings two",
                "Loose",
                "Other project",
            ],
        )

    def test_start_here_comes_first_in_its_goal_and_only_one_per_goal(self):
        self.add(goal="Release")
        self.add(goal="Release")
        self.add(goal="Docs", start_here="unlocks the docs")
        self.add(goal="Release", start_here="unlocks Q1 and Q2")
        self.assertEqual([q["number"] for q in self.store.list("demo")], [4, 1, 2, 3])
        self.store.set_goal("demo", 2, start_here="  sets the  pattern ")
        marked = {q["number"]: q["start_here"] for q in self.store.list("demo")}
        self.assertEqual(marked, {1: None, 2: "sets the pattern", 3: "unlocks the docs", 4: None})
        self.assertEqual([q["number"] for q in self.store.list("demo")], [2, 1, 4, 3])

    def test_start_here_needs_a_goal_and_a_reason(self):
        with self.assertRaises(QuestionError):
            self.add(start_here="unlocks everything")
        with self.assertRaises(QuestionError):
            self.add(goal="Release", start_here=" ")
        self.add()
        with self.assertRaises(QuestionError):
            self.store.set_goal("demo", 1, start_here="unlocks everything")

    def test_set_goal_keeps_the_wording_and_a_new_goal_drops_the_mark(self):
        self.add(goal="Release", start_here="unlocks the rest")
        question = self.store.set_goal("demo", 1, goal="  Docs  site ")
        self.assertEqual((question["goal"], question["start_here"]), ("Docs site", None))
        self.assertEqual((question["version"], question["entries"]), (1, []))
        question = self.store.set_goal("demo", 1, goal="Release", start_here="unlocks Q2")
        self.assertEqual(question["start_here"], "unlocks Q2")
        self.assertIsNone(self.store.set_goal("demo", 1, clear_start_here=True)["start_here"])
        self.assertIsNone(self.store.set_goal("demo", 1, goal="")["goal"])
        with self.assertRaises(QuestionError):
            self.store.set_goal("demo", 1)

    def test_reword_can_change_the_goal_and_mark(self):
        self.add(goal="Release")
        self.add(goal="Release", start_here="unlocks Q1")
        question = self.store.reword("demo", 1, "Clearer", goal="Release", start_here="unlocks Q2")
        self.assertEqual((question["version"], question["start_here"]), (2, "unlocks Q2"))
        self.assertIsNone(self.store.get("demo", 2)["start_here"])
        question = self.store.reword("demo", 1, "Moved", goal="Docs")
        self.assertEqual((question["goal"], question["start_here"]), ("Docs", None))
        question = self.store.reword("demo", 1, "Same goal", title="New title")
        self.assertEqual(question["goal"], "Docs")

    def test_quick_means_recommended_few_options_and_never_reworded(self):
        cases = {
            "recommended": (dict(recommended="a"), True),
            "no recommendation": (dict(), False),
            "four options": (dict(options=["a", "b", "c", "d"], recommended="a"), False),
        }
        for name, (fields, quick) in cases.items():
            with self.subTest(name):
                number = self.add(**fields)["number"]
                self.assertEqual(self.store.get("demo", number)["quick"], quick)
                listed = next(q for q in self.store.list("demo") if q["number"] == number)
                self.assertEqual(listed["quick"], quick)
        self.add(recommended="a")
        self.assertFalse(self.store.reword("demo", 4, "Clearer", title="T")["quick"])

    def test_a_reworded_question_keeps_when_it_was_first_asked(self):
        self.add()
        self.store.db.execute("UPDATE questions SET created_at = '2026-01-01T00:00:00Z'")
        question = self.store.reword("demo", 1, "Clearer", title="T")
        self.assertEqual(question["created_at"], "2026-01-01T00:00:00Z")

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
            self.assertIsNone(store.get("demo", 1)["goal"])

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
            [
                {
                    "name": "onzehq",
                    "questions": 0,
                    "folders": [{"folder": "onzehq", "path": None, "shown": None}],
                }
            ],
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
            [
                {
                    "name": "onzehq",
                    "questions": 1,
                    "folders": [
                        {"folder": "onzehq", "path": None, "shown": None},
                        {"folder": "onzehq-api", "path": None, "shown": None},
                    ],
                }
            ],
        )

    def test_renaming_only_changes_the_case_keeps_everything(self):
        # What happened for real: dataforge became DataForge, and its folder stayed behind
        # as an empty dataforge project.
        self.add(project="dataforge")
        self.store.see_folder("dataforge")
        self.store.rename_project("dataforge", "DataForge")
        self.assertEqual(
            self.store.project_summaries(),
            [
                {
                    "name": "DataForge",
                    "questions": 1,
                    "folders": [{"folder": "dataforge", "path": None, "shown": None}],
                }
            ],
        )

    def test_rename_merges_into_a_project_without_questions(self):
        self.add(project="onzehq")
        self.store.set_next("ONZE HQ", 10)
        self.store.rename_project("onzehq", "ONZE HQ")
        self.assertEqual(self.store.get("ONZE HQ", 1)["label"], "Q1")
        self.assertEqual(self.add(project="ONZE HQ")["label"], "Q10")

    def test_rename_joins_two_projects_that_both_have_questions(self):
        # What happened for real: a folder rename split Tempo (Q1-Q27) and a new tempo.
        for _ in range(3):
            self.add(project="Tempo")
        self.store.set_next("Tempo", 28)
        self.add(project="tempo")
        self.store.set_next("tempo", 4)
        self.add(project="tempo", title="Second")
        self.add(project="tempo", title="Third")
        self.store.see_folder("tempo")
        moves = self.store.rename_project("tempo", "Tempo")
        self.assertEqual(
            moves, [{"from": 1, "to": 28}, {"from": 4, "to": 29}, {"from": 5, "to": 30}]
        )
        self.assertEqual(self.store.projects(), ["Tempo"])
        self.assertEqual(
            [(q["label"], q["title"]) for q in self.store.list("Tempo")][-3:],
            [("Q28", "Which way?"), ("Q29", "Second"), ("Q30", "Third")],
        )
        self.assertEqual(self.store.get("Tempo", 1)["label"], "Q1")
        self.assertEqual(self.add(project="Tempo")["label"], "Q31")
        self.assertEqual(self.store.project_for("tempo"), "Tempo")
        trace = self.store.get("Tempo", 29)["entries"][-1]
        self.assertEqual(
            (trace["kind"], trace["author"], trace["body"]),
            ("context", "claude", "Moved from tempo Q4 when tempo joined Tempo."),
        )
        self.assertEqual(self.store.get("Tempo", 3)["entries"], [])

    def test_rename_into_a_project_with_questions_keeps_numbers_when_it_has_none(self):
        self.add(project="onzehq")
        self.store.see_folder("onzehq-api")
        self.assertEqual(self.store.rename_project("onzehq-api", "onzehq"), [])
        self.assertEqual([q["label"] for q in self.store.list("onzehq")], ["Q1"])
        self.assertEqual(self.store.project_for("onzehq-api"), "onzehq")

    def test_rename_refuses_bad_names_and_changes_nothing(self):
        self.add(project="onzehq")
        self.add(project="ONZE HQ")
        for old, new in [
            ("onzehq", " "),
            ("onzehq", "onzehq"),
            ("missing", "Anything"),
        ]:
            with self.subTest(old=old, new=new), self.assertRaises(QuestionError):
                self.store.rename_project(old, new)
        self.assertEqual(self.store.projects(), ["ONZE HQ", "onzehq"])
        self.assertEqual(len(self.store.list("onzehq")), 1)

    def test_a_folder_is_known_by_its_path(self):
        self.assertTrue(self.store.see_folder("api", "/work/api"))
        self.assertFalse(self.store.see_folder("api", "/work/api"))
        self.store.rename_project("api", "Work API")
        # Another folder with the same name is a folder of its own.
        self.assertTrue(self.store.see_folder("api", "/home/api"))
        self.assertEqual(self.store.project_for("api", "/work/api"), "Work API")
        self.assertEqual(self.store.project_for("api", "/home/api"), "api")
        self.assertEqual(self.store.project_for("api", "/never/api"), "api")
        self.assertEqual(
            [(f["path"], f["project"]) for f in self.store.folders()],
            [("/home/api", "api"), ("/work/api", "Work API")],
        )

    def test_a_folder_known_by_name_only_gets_its_path(self):
        # Rows from before paths were stored: the first session in that name adopts one.
        self.store.db.execute(
            "INSERT INTO folders (folder, project) VALUES ('open-questions', 'Tempo')"
        )
        self.assertEqual(self.store.project_for("open-questions", "/lab/open-questions"), "Tempo")
        self.assertFalse(self.store.see_folder("open-questions", "/lab/open-questions"))
        folder = self.store.folders()[0]
        self.assertEqual((folder["path"], folder["project"]), ("/lab/open-questions", "Tempo"))
        self.assertIsNotNone(folder["seen_at"])
        # Once adopted, a second folder of that name starts on its own.
        self.assertTrue(self.store.see_folder("open-questions", "/elsewhere/open-questions"))
        self.assertEqual(
            self.store.project_for("open-questions", "/elsewhere/open-questions"), "open-questions"
        )

    def test_paths_in_the_home_folder_are_shown_with_a_tilde(self):
        home = os.path.expanduser("~")
        self.store.see_folder("tempo", os.path.join(home, "Lab", "tempo"))
        self.assertEqual(self.store.folders()[0]["shown"], "~/Lab/tempo")
        self.assertEqual(self.store.project_summaries()[0]["folders"][0]["shown"], "~/Lab/tempo")

    def test_renaming_a_never_seen_project_twice_keeps_one_folder_row(self):
        self.add(project="onzehq")
        self.store.rename_project("onzehq", "ONZE HQ")
        self.store.rename_project("ONZE HQ", "Onze")
        self.assertEqual(
            [(f["folder"], f["project"]) for f in self.store.folders()], [("onzehq", "Onze")]
        )

    def test_old_folders_without_a_project_are_filed_under_their_name(self):
        self.store.db.execute("INSERT INTO folders (folder) VALUES ('dataforge')")
        self.store.db.execute("PRAGMA user_version = 0")
        _migrate(self.store.db)
        self.assertEqual(self.store.folders()[0]["project"], "dataforge")


class MigrateTest(DatabaseTest):
    def test_folders_keyed_by_name_keep_their_projects(self):
        path = os.path.join(self.tmp.name, "old.db")
        old = sqlite3.connect(path)
        old.executescript(SCHEMA.replace("seen_at TEXT,\n  path TEXT\n", "seen_at TEXT\n"))
        old.execute(
            "INSERT INTO folders (folder, project, seen_at) VALUES"
            " ('open-questions', 'Tempo', 'x'), ('tempo', 'Tempo', NULL),"
            " ('onzehq', 'ONZE HQ', NULL)"
        )
        old.execute("PRAGMA user_version = 3")
        old.commit()
        old.close()
        with Store(connect(path)) as store:
            self.assertEqual(
                [(f["folder"], f["path"], f["project"], f["seen_at"]) for f in store.folders()],
                [
                    ("onzehq", None, "ONZE HQ", None),
                    ("open-questions", None, "Tempo", "x"),
                    ("tempo", None, "Tempo", None),
                ],
            )
            self.assertEqual(store.project_for("tempo", "/lab/tempo"), "Tempo")
        with Store(connect(path)) as store:
            self.assertEqual(len(store.folders()), 3)
            self.assertEqual(_user_version(store.db), len(MIGRATIONS))

    def test_an_older_tempo_can_still_write_folders_after_the_upgrade(self):
        # A Tempo from before paths may still run on the same database: its statement
        # must keep working, and a name it adds is adopted by the next session there.
        self.store.see_folder("api", "/work/api")
        self.store.db.execute(
            "INSERT INTO folders (folder, project, seen_at) VALUES (?, ?, ?)"
            " ON CONFLICT (folder) DO UPDATE SET seen_at = excluded.seen_at",
            ("web", "web", "x"),
        )
        self.assertEqual(self.store.project_for("web", "/work/web"), "web")
        self.assertFalse(self.store.see_folder("web", "/work/web"))
        # Running the step again, as on a database that is already upgraded, changes nothing.
        _add_folder_paths(self.store.db)
        self.assertEqual(len(self.store.folders()), 2)

    def test_a_fresh_database_ends_at_the_latest_version(self):
        self.assertEqual(_user_version(self.store.db), len(MIGRATIONS))

    def test_adds_goals_to_a_database_from_before_them(self):
        path = os.path.join(self.tmp.name, "old.db")
        old = sqlite3.connect(path)
        old.executescript(SCHEMA.replace("  goal TEXT,\n  start_here TEXT,\n", ""))
        old.execute("PRAGMA user_version = 2")
        old.execute("INSERT INTO projects (name) VALUES ('demo')")
        old.execute(
            "INSERT INTO questions (project, number, priority, created_at, updated_at)"
            " VALUES ('demo', 1, 'now', 'x', 'x')"
        )
        old.execute(
            "INSERT INTO versions (question_id, version, title, context, options, created_at)"
            " VALUES (1, 1, 'Which way?', 'We need to pick one.', '[]', 'x')"
        )
        old.commit()
        old.close()
        with Store(connect(path)) as store:
            question = store.get("demo", 1)
            self.assertEqual((question["goal"], question["start_here"]), (None, None))
            self.assertEqual(_user_version(store.db), len(MIGRATIONS))

    def test_an_unversioned_database_that_already_has_the_changes_upgrades(self):
        # Databases from before user_version: current shape, but at version 0.
        self.add(goal="Release", start_here="unlocks the rest")
        self.store.db.execute("INSERT INTO folders (folder, project) VALUES ('web', 'demo')")
        self.store.db.execute("PRAGMA user_version = 0")
        with Store(connect(os.environ["TEMPO_DB"])) as store:
            self.assertEqual(_user_version(store.db), len(MIGRATIONS))
            self.assertEqual(store.get("demo", 1)["goal"], "Release")
            self.assertEqual(store.get("demo", 1)["start_here"], "unlocks the rest")
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
