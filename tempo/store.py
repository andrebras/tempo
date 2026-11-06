"""SQLite storage for open questions.

A question belongs to a project and has a running number that is never reused.
Its wording lives in versions (a reword adds a version, old ones stay). Everything
that happens to it afterwards (added context, rewords, follow-ups, answers) is an
entry in its thread. Entries written by the user start undelivered; the hook and
`tempo wait` hand them to Claude and mark them delivered.

A project is just a name. The first time a session runs in a git folder, the folder is
filed under a project of the same name; renaming the project in Settings takes its
questions and folders along.
"""

import json
import os
import sqlite3
import string
from datetime import datetime, timezone

DEFAULT_DB = "~/.claude/tempo/questions.db"
# Where the database lived before the plugin was renamed to Tempo.
OLD_DEFAULT_DB = "~/.claude/open-questions/questions.db"


def setting(name):
    """Read TEMPO_<name> from the environment, or OQ_<name>, its name before the rename."""
    return os.environ.get("TEMPO_" + name) or os.environ.get("OQ_" + name)


def db_path():
    """The database file: TEMPO_DB if set, else the default, or the old default if only
    that one exists. The old file is used where it is, never moved or copied."""
    if setting("DB"):
        return setting("DB")
    path, old = os.path.expanduser(DEFAULT_DB), os.path.expanduser(OLD_DEFAULT_DB)
    if not os.path.exists(path) and os.path.exists(old):
        return old
    return path


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  name TEXT PRIMARY KEY,
  next_number INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL REFERENCES projects(name),
  number INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'archived')),
  priority TEXT NOT NULL DEFAULT 'soon' CHECK (priority IN ('now', 'soon', 'later')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project, number)
);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  version INTEGER NOT NULL,
  title TEXT NOT NULL,
  context TEXT NOT NULL,
  options TEXT NOT NULL,
  recommended TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (question_id, version)
);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  kind TEXT NOT NULL CHECK (kind IN ('context', 'reword', 'followup', 'answer')),
  author TEXT NOT NULL CHECK (author IN ('claude', 'user')),
  body TEXT NOT NULL DEFAULT '',
  choice TEXT,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS folders (
  folder TEXT PRIMARY KEY,
  project TEXT,
  seen_at TEXT
);
CREATE INDEX IF NOT EXISTS entries_undelivered ON entries (delivered_at) WHERE delivered_at IS NULL;
"""


# How soon Claude needs the answer, in sort order: work is waiting on it, needed this
# week, no rush.
PRIORITIES = ("now", "soon", "later")


class QuestionError(ValueError):
    """Raised when a request breaks the question format or rules."""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=None):
    path = path or db_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    _migrate(db)
    return db


def _migrate(db):
    """Bring databases created by older versions up to the current schema."""
    columns = [row["name"] for row in db.execute("PRAGMA table_info(questions)")]
    if "priority" not in columns:
        # The first version had an urgent flag; it became the 'now' priority.
        db.execute(
            "ALTER TABLE questions ADD COLUMN priority TEXT NOT NULL DEFAULT 'soon'"
            " CHECK (priority IN ('now', 'soon', 'later'))"
        )
        db.execute("UPDATE questions SET priority = 'now' WHERE urgent = 1")
    # Folders used to fall back to their own name when no project was picked.
    db.execute("UPDATE folders SET project = folder WHERE project IS NULL")


class Store:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    # Writing, by Claude

    def add(self, project, title, context, options, recommended=None, priority="soon", number=None):
        project = _required(project, "project")
        title = _required(title, "title")
        context = _required(context, "context")
        options = _letter(options)
        recommended = _recommended(recommended, options)
        priority = _priority(priority)
        with self._transaction():
            self.db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (project,))
            next_number = self._next_number(project)
            if number is None:
                number = next_number
            elif number < next_number:
                raise QuestionError(
                    "Q%d is already used or skipped in %s; the next free number is Q%d"
                    % (number, project, next_number)
                )
            stamp = now()
            cursor = self.db.execute(
                "INSERT INTO questions (project, number, priority, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (project, number, priority, stamp, stamp),
            )
            self._insert_version(cursor.lastrowid, 1, title, context, options, recommended)
            self.db.execute(
                "UPDATE projects SET next_number = ? WHERE name = ?", (number + 1, project)
            )
        return self.get(project, number)

    def set_next(self, project, number):
        project = _required(project, "project")
        with self._transaction():
            self.db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (project,))
            current = self._next_number(project)
            if number < current:
                raise QuestionError(
                    "Numbers are never reused: %s is already at Q%d" % (project, current)
                )
            self.db.execute("UPDATE projects SET next_number = ? WHERE name = ?", (number, project))

    def add_context(self, project, number, body):
        body = _required(body, "context")
        question = self._find(project, number)
        self._insert_entry(question, "context", "claude", body)
        return self.get(project, number)

    def reword(
        self, project, number, note, title=None, context=None, options=None, recommended=None
    ):
        note = _required(note, "note (why it was reworded)")
        question = self._find(project, number)
        if question["status"] == "archived":
            raise QuestionError("Archived questions cannot be reworded")
        current = self._current_version(question["id"])
        options = _letter(options) if options is not None else json.loads(current["options"])
        if recommended is None and options is not None:
            letters = [option["letter"] for option in options]
            recommended = current["recommended"] if current["recommended"] in letters else None
        recommended = _recommended(recommended, options)
        version = current["version"] + 1
        with self._transaction():
            self._insert_version(
                question["id"],
                version,
                _required(title, "title") if title is not None else current["title"],
                _required(context, "context") if context is not None else current["context"],
                options,
                recommended,
            )
            self._insert_entry(question, "reword", "claude", note, version=version)
            # A reword after an answer means the answer asked for more, so the question
            # is open again until the user answers the new version.
            self.db.execute("UPDATE questions SET status = 'open' WHERE id = ?", (question["id"],))
        return self.get(project, number)

    def set_priority(self, project, number, priority):
        priority = _priority(priority)
        question = self._find(project, number)
        self.db.execute(
            "UPDATE questions SET priority = ?, updated_at = ? WHERE id = ?",
            (priority, now(), question["id"]),
        )
        return self.get(project, number)

    def archive_answered(self, project):
        cursor = self.db.execute(
            "UPDATE questions SET status = 'archived', updated_at = ?"
            " WHERE project = ? AND status = 'answered'",
            (now(), project),
        )
        return cursor.rowcount

    # Writing, by the user

    def follow_up(self, project, number, body):
        body = _required(body, "follow-up")
        question = self._find(project, number)
        if question["status"] == "archived":
            raise QuestionError("This question is archived")
        self._insert_entry(question, "followup", "user", body)
        return self.get(project, number)

    def answer(self, project, number, choice=None, body=""):
        question = self._find(project, number)
        if question["status"] == "archived":
            raise QuestionError("This question is archived")
        if question["status"] == "answered":
            raise QuestionError("This question is already answered; reopen it to answer again")
        body = (body or "").strip()
        current = self._current_version(question["id"])
        letters = [option["letter"] for option in json.loads(current["options"])]
        if choice:
            choice = choice.strip().lower()
            if choice not in letters:
                raise QuestionError("Option (%s) does not exist" % choice)
        elif not body:
            raise QuestionError("Pick an option or write an answer")
        with self._transaction():
            self._insert_entry(question, "answer", "user", body, choice=choice or None)
            self.db.execute(
                "UPDATE questions SET status = 'answered', updated_at = ? WHERE id = ?",
                (now(), question["id"]),
            )
        return self.get(project, number)

    def reopen(self, project, number):
        """Open an answered question again so the user can give a new answer.

        The earlier answers stay in the thread; the next one is delivered as a change.
        """
        question = self._find(project, number)
        if question["status"] != "answered":
            raise QuestionError("Only answered questions can be reopened")
        self.db.execute(
            "UPDATE questions SET status = 'open', updated_at = ? WHERE id = ?",
            (now(), question["id"]),
        )
        return self.get(project, number)

    # Reading

    def get(self, project, number):
        question = self._find(project, number)
        versions = [
            _version_dict(row)
            for row in self.db.execute(
                "SELECT * FROM versions WHERE question_id = ? ORDER BY version", (question["id"],)
            )
        ]
        entries = [
            _entry_dict(row)
            for row in self.db.execute(
                "SELECT * FROM entries WHERE question_id = ? ORDER BY id", (question["id"],)
            )
        ]
        result = _question_dict(question)
        result.update(versions[-1])
        result["versions"] = versions
        result["entries"] = entries
        answers = [entry for entry in entries if entry["kind"] == "answer"]
        result["answer"] = answers[-1] if answers else None
        return result

    def list(self, project=None, status="open"):
        sql = (
            "SELECT q.*, v.title, v.context, v.version FROM questions q"
            " JOIN versions v ON v.question_id = q.id"
            " AND v.version = (SELECT MAX(version) FROM versions WHERE question_id = q.id)"
            " WHERE 1 = 1"
        )
        params = []
        if project:
            sql += " AND q.project = ?"
            params.append(project)
        if status and status != "all":
            sql += " AND q.status = ?"
            params.append(status)
        sql += (
            " ORDER BY q.project,"
            " CASE q.priority WHEN 'now' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END, q.number"
        )
        rows = []
        for row in self.db.execute(sql, params):
            item = _question_dict(row)
            item["title"] = row["title"]
            item["context"] = row["context"]
            item["version"] = row["version"]
            rows.append(item)
        return rows

    def projects(self):
        return [row["name"] for row in self.db.execute("SELECT name FROM projects ORDER BY name")]

    # Projects and their folders, renamed in Settings

    def see_folder(self, folder):
        """Remember a folder a session ran in; a new one is filed under its own name."""
        folder = _required(folder, "folder")
        self.db.execute(
            "INSERT INTO folders (folder, project, seen_at) VALUES (?, ?, ?)"
            " ON CONFLICT (folder) DO UPDATE SET seen_at = excluded.seen_at",
            (folder, folder, now()),
        )

    def project_for(self, folder):
        """The project a folder is filed under; a folder never seen is its own project."""
        row = self.db.execute("SELECT project FROM folders WHERE folder = ?", (folder,)).fetchone()
        return row["project"] if row else folder

    def folders(self):
        return [
            {"folder": row["folder"], "project": row["project"], "seen_at": row["seen_at"]}
            for row in self.db.execute("SELECT * FROM folders ORDER BY folder")
        ]

    def project_summaries(self):
        """Every project with its question count and folders: projects that have
        questions or a numbering, and the project of every folder a session ran in, even
        before its first question."""
        counts = {
            row["name"]: row["questions"]
            for row in self.db.execute(
                "SELECT p.name, COUNT(q.id) AS questions FROM projects p"
                " LEFT JOIN questions q ON q.project = p.name GROUP BY p.name"
            )
        }
        folders = {}
        for folder in self.folders():
            folders.setdefault(folder["project"], []).append(folder["folder"])
        return [
            {"name": name, "questions": counts.get(name, 0), "folders": folders.get(name, [])}
            for name in sorted(set(counts) | set(folders), key=str.lower)
        ]

    def rename_project(self, old, new):
        """Give a project a new name: its questions, numbering and folders move with it.

        Joining two projects is fine as long as only one of them has questions; otherwise
        both would have a Q1.
        """
        new = (new or "").strip()
        if not new:
            raise QuestionError("A project needs a name")
        if new == old:
            raise QuestionError("The project is already called %s" % new)
        if not any(project["name"] == old for project in self.project_summaries()):
            raise QuestionError("There is no project called %s" % old)
        if self._question_count(new) and self._question_count(old):
            raise QuestionError(
                "%s already has questions, so their numbers would clash; pick another name" % new
            )
        with self._transaction():
            if self._question_count(old) or self._has_project_row(old):
                self.db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (new,))
                self.db.execute(
                    "UPDATE projects SET next_number = MAX(next_number, ?) WHERE name = ?",
                    (self._next_number(old), new),
                )
                self.db.execute("UPDATE questions SET project = ? WHERE project = ?", (new, old))
                self.db.execute("DELETE FROM projects WHERE name = ?", (old,))
            moved = self.db.execute(
                "UPDATE folders SET project = ? WHERE project = ?", (new, old)
            ).rowcount
            if not moved:
                # Projects named after their folder before folders were remembered: keep
                # sessions in that folder on this project.
                self.db.execute(
                    "INSERT OR IGNORE INTO folders (folder, project) VALUES (?, ?)", (old, new)
                )

    def undelivered(self, project=None):
        sql = (
            "SELECT e.*, q.project, q.number FROM entries e"
            " JOIN questions q ON q.id = e.question_id"
            " WHERE e.author = 'user' AND e.delivered_at IS NULL"
        )
        params = []
        if project:
            sql += " AND q.project = ?"
            params.append(project)
        sql += " ORDER BY e.id"
        return [
            dict(_entry_dict(row), project=row["project"], number=row["number"])
            for row in self.db.execute(sql, params)
        ]

    def mark_delivered(self, entry_ids):
        if not entry_ids:
            return
        # Only "?" placeholders are joined into the SQL; the ids are bound as parameters.
        placeholders = ",".join("?" * len(entry_ids))
        self.db.execute(
            "UPDATE entries SET delivered_at = ? WHERE id IN (%s)" % placeholders,  # noqa: S608  # nosec B608
            [now(), *entry_ids],
        )

    # Helpers

    def _transaction(self):
        return _Transaction(self.db)

    def _next_number(self, project):
        row = self.db.execute(
            "SELECT next_number FROM projects WHERE name = ?", (project,)
        ).fetchone()
        return row["next_number"] if row else 1

    def _question_count(self, project):
        return self.db.execute(
            "SELECT COUNT(*) FROM questions WHERE project = ?", (project,)
        ).fetchone()[0]

    def _has_project_row(self, project):
        return (
            self.db.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone()
            is not None
        )

    def _find(self, project, number):
        row = self.db.execute(
            "SELECT * FROM questions WHERE project = ? AND number = ?", (project, int(number))
        ).fetchone()
        if row is None:
            raise QuestionError("Q%s does not exist in %s" % (number, project))
        return row

    def _current_version(self, question_id):
        return self.db.execute(
            "SELECT * FROM versions WHERE question_id = ? ORDER BY version DESC LIMIT 1",
            (question_id,),
        ).fetchone()

    def _insert_version(self, question_id, version, title, context, options, recommended):
        self.db.execute(
            "INSERT INTO versions"
            " (question_id, version, title, context, options, recommended, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (question_id, version, title, context, json.dumps(options), recommended, now()),
        )

    def _insert_entry(self, question, kind, author, body, choice=None, version=None):
        stamp = now()
        if version is None:
            version = self._current_version(question["id"])["version"]
        self.db.execute(
            "INSERT INTO entries"
            " (question_id, kind, author, body, choice, version, created_at, delivered_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                question["id"],
                kind,
                author,
                body,
                choice,
                version,
                stamp,
                stamp if author == "claude" else None,
            ),
        )
        self.db.execute("UPDATE questions SET updated_at = ? WHERE id = ?", (stamp, question["id"]))


class _Transaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind, value, traceback):
        self.db.execute("COMMIT" if kind is None else "ROLLBACK")


def _required(value, name):
    value = (value or "").strip()
    if not value:
        raise QuestionError("A question needs a %s" % name)
    return value


def _letter(options):
    options = [(option or "").strip() for option in (options or [])]
    if len(options) < 2 or not all(options):
        raise QuestionError("A question needs at least two options, each written out")
    if len(options) > 26:
        raise QuestionError("A question can have at most 26 options")
    return [
        {"letter": letter, "text": text} for letter, text in zip(string.ascii_lowercase, options)
    ]


def _priority(priority):
    priority = (priority or "").strip().lower()
    if priority not in PRIORITIES:
        raise QuestionError("Priority must be one of: %s" % ", ".join(PRIORITIES))
    return priority


def _recommended(recommended, options):
    if not recommended:
        return None
    recommended = recommended.strip().lower()
    if recommended not in [option["letter"] for option in options]:
        raise QuestionError("The recommended option (%s) does not exist" % recommended)
    return recommended


def _question_dict(row):
    return {
        "project": row["project"],
        "number": row["number"],
        "label": "Q%d" % row["number"],
        "status": row["status"],
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _version_dict(row):
    return {
        "version": row["version"],
        "title": row["title"],
        "context": row["context"],
        "options": json.loads(row["options"]),
        "recommended": row["recommended"],
        "created_at": row["created_at"],
    }


def _entry_dict(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "author": row["author"],
        "body": row["body"],
        "choice": row["choice"],
        "version": row["version"],
        "created_at": row["created_at"],
        "delivered": row["delivered_at"] is not None,
    }
