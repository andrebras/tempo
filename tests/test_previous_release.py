"""The previous release's code against a database migrated by this code.

A Claude session keeps the hooks and the `tempo` command it started with, so an older
release keeps reading and writing the database after a newer one has migrated it. This
test unpacks the last release from git history, migrates a temporary database with the
current code, and runs the old `tempo add`, `list` and `hook user-prompt-submit` on it.
"""

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tempo import store as store_module
from tempo.store import CODE_ROOT, MIGRATIONS, Store, _add_column, connect


def git(*args):
    return subprocess.run(["git", "-C", CODE_ROOT, *args], capture_output=True, check=True).stdout


def previous_release():
    """The newest commit titled "Release …" whose version differs from this code's, or
    None when the history has none (a shallow clone, or no git at all)."""
    with open(os.path.join(CODE_ROOT, ".claude-plugin", "plugin.json")) as file:
        ours = json.load(file)["version"]
    try:
        commits = git("log", "--grep=^Release ", "--format=%H").decode().split()
        for commit in commits:
            manifest = git("show", "%s:.claude-plugin/plugin.json" % commit)
            if json.loads(manifest)["version"] != ours:
                return commit
    except (OSError, subprocess.CalledProcessError):
        pass
    return None


class PreviousReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = previous_release()
        if cls.commit is None:
            raise unittest.SkipTest(
                "no earlier release in this clone's history (a shallow clone?);"
                " fetch the full history to run it"
            )
        cls.tmp = tempfile.TemporaryDirectory()
        cls.code = os.path.join(cls.tmp.name, "release")
        archive = git("archive", "--format=tar", cls.commit, "bin", "tempo", ".claude-plugin")
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(cls.code, filter="data")
            else:
                tar.extractall(cls.code)  # Python before 3.12: our own repository's files.

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.db = os.path.join(self.tmp.name, "questions.db")
        with Store(connect(self.db)) as store:
            store.add(project="demo", title="Left?", context="C", options=["Left", "Right"])
            store.answer("demo", 1, "b", "Nicer view")
            store.add(project="demo", title="Open?", context="C", options=["Yes", "No"])
        self.addCleanup(os.remove, self.db)

    def old_tempo(self, *args, stdin="", project="demo"):
        env = dict(os.environ, TEMPO_DB=self.db, TEMPO_PROJECT=project, TEMPO_PORT="2299")
        env.pop("PYTHONPATH", None)
        env.pop("TEMPO_IN_THREAD", None)
        # Run from the temporary folder: `python -m` puts the working folder first on the
        # import path, and this checkout's tempo package must not be the one found.
        result = subprocess.run(
            ["sh", os.path.join(self.code, "bin", "tempo"), *args],
            cwd=self.tmp.name,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_old_code_runs_on_the_current_database(self):
        self.assertIn("Q2 [open, v1, open 1 minute] Open?", self.old_tempo("list"))
        added = self.old_tempo(
            "add", "--title", "Up?", "--context", "C", "--option", "Up", "--option", "Down"
        )
        self.assertIn("Added Q3 in demo", added)
        out = self.old_tempo("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x"}))
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("demo Q1 (Left?) answered (b) Right", context)
        with Store(connect(self.db)) as store:
            self.assertEqual(store.get("demo", 3)["title"], "Up?")
            self.assertEqual(store.undelivered("demo"), [])

    def test_old_code_runs_after_a_compatible_addition(self):
        # A later Tempo adds a column and a table the old code knows nothing about.
        def addition(db):
            _add_column(db, "folders", "later", "TEXT")
            _add_column(db, "questions", "later", "TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS later (id INTEGER PRIMARY KEY)")

        with mock.patch.object(store_module, "ADDITIONS", [addition]):
            connect(self.db).close()
        added = self.old_tempo(
            "add", "--title", "Up?", "--context", "C", "--option", "Up", "--option", "Down"
        )
        self.assertIn("Added Q3 in demo", added)
        self.assertIn("Q3 [open, v1", self.old_tempo("list"))
        out = self.old_tempo("hook", "user-prompt-submit", stdin=json.dumps({"cwd": "/x"}))
        self.assertIn("demo Q1 (Left?) answered (b) Right", out)
        # With no project set, the old prompt hook records its folder as it always has.
        folder = os.path.join(self.tmp.name, "web")
        os.makedirs(folder, exist_ok=True)
        self.old_tempo("hook", "user-prompt-submit", stdin=json.dumps({"cwd": folder}), project="")
        with Store(connect(self.db)) as store:
            version = store.db.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, len(MIGRATIONS))
            columns = [row["name"] for row in store.db.execute("PRAGMA table_info(folders)")]
            self.assertIn("later", columns)
            self.assertIn("web", [folder["folder"] for folder in store.folders()])

    def test_it_is_the_old_code_that_runs(self):
        out = subprocess.run(
            [sys.executable, "-c", "import tempo.cli; print(tempo.cli.__file__)"],
            cwd=self.tmp.name,
            env=dict(os.environ, PYTHONPATH=self.code),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertTrue(os.path.realpath(out.strip()).startswith(os.path.realpath(self.code)))
