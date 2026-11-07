"""SQLite storage for open questions.

A question belongs to a project and has a running number that is never reused.
Its wording lives in versions (a reword adds a version, old ones stay). Everything
that happens to it afterwards (added context, rewords, follow-ups, answers) is an
entry in its thread. Entries written by the user start undelivered; the hook and
`tempo wait` hand them to Claude and mark them delivered.

A project is just a name. The first time a session runs in a git folder, the folder is
filed under a project of the same name; renaming the project in Settings takes its
questions and folders along.

A question can name its goal: the piece of work its answer moves forward. Within a goal,
one question may be marked "start here", with the reason, when its answer unlocks or sets
the pattern for the others.
"""

from __future__ import annotations

import json
import os
import sqlite3
import string
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Any, cast

DEFAULT_DB = "~/.claude/tempo/questions.db"
# Where the database lived before the plugin was renamed to Tempo.
OLD_DEFAULT_DB = "~/.claude/open-questions/questions.db"

# The shapes passed around, as plain dicts that turn straight into JSON. A question has
# the keys of _question_dict, plus its latest version, thread and answer from get().
Question = dict[str, Any]
# One entry of a thread, see _entry_dict.
Entry = dict[str, Any]
# One lettered option: {"letter": "a", "text": "..."}.
Option = dict[str, str]
# Store has a list() method, which hides the builtin inside the class, so the lists its
# methods take and return are named here.
Questions = list[Question]
Entries = list[Entry]
Options = list[Option]
Names = list[str]
Folders = list[dict[str, str]]
Summaries = list[dict[str, Any]]
Ids = list[int]
# Questions moved by a join: {"from": old number, "to": new number}.
Moves = list[dict[str, int]]


def setting(name: str) -> str | None:
    """Read TEMPO_<name> from the environment, or OQ_<name>, its name before the rename."""
    return os.environ.get("TEMPO_" + name) or os.environ.get("OQ_" + name)


def db_path() -> str:
    """The database file: TEMPO_DB if set, else the default, or the old default if only
    that one exists. The old file is used where it is, never moved or copied."""
    chosen = setting("DB")
    if chosen:
        return chosen
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
  goal TEXT,
  start_here TEXT,
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


# Answered questions move to Archived this long after their latest answer.
ARCHIVE_AFTER = timedelta(days=7)

# A question is quick to answer when it has a recommended option, at most this many
# options, and its first wording still stands.
QUICK_OPTIONS = 3


class QuestionError(ValueError):
    """Raised when a request breaks the question format or rules."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | None = None) -> sqlite3.Connection:
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


def _add_priority(db: sqlite3.Connection) -> None:
    """The first version had an urgent flag; it became the 'now' priority."""
    columns = [row["name"] for row in db.execute("PRAGMA table_info(questions)")]
    if "priority" not in columns:
        db.execute(
            "ALTER TABLE questions ADD COLUMN priority TEXT NOT NULL DEFAULT 'soon'"
            " CHECK (priority IN ('now', 'soon', 'later'))"
        )
        db.execute("UPDATE questions SET priority = 'now' WHERE urgent = 1")


def _fill_folder_projects(db: sqlite3.Connection) -> None:
    """Folders used to fall back to their own name when no project was picked."""
    db.execute("UPDATE folders SET project = folder WHERE project IS NULL")


def _add_goal(db: sqlite3.Connection) -> None:
    """Goals and "start here" replace the priority, which stays in old rows but is unused."""
    columns = [row["name"] for row in db.execute("PRAGMA table_info(questions)")]
    for column in ("goal", "start_here"):
        if column not in columns:
            db.execute("ALTER TABLE questions ADD COLUMN %s TEXT" % column)


# Steps that bring older databases up to SCHEMA, in order. The database's user_version
# says how many have run; never reorder or remove one, only add new ones at the end.
# Databases made before user_version was used are at 0 whatever their shape, so each
# step must also be safe on a database that already has its change.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _add_priority,
    _fill_folder_projects,
    _add_goal,
]


def _version(db: sqlite3.Connection) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def _migrate(db: sqlite3.Connection) -> None:
    """Run each step this database has not had yet, once, in its own transaction."""
    for number, step in enumerate(MIGRATIONS, start=1):
        if _version(db) >= number:
            continue
        with _Transaction(db):
            # Another process may have run the step while this one waited for the lock.
            if _version(db) < number:
                step(db)
                db.execute("PRAGMA user_version = %d" % number)


class Store:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.db.close()

    # Writing, by Claude

    def add(
        self,
        project: str,
        title: str,
        context: str,
        options: Names,
        recommended: str | None = None,
        goal: str | None = None,
        start_here: str | None = None,
        number: int | None = None,
    ) -> Question:
        project = _required(project, "project")
        title = _required(title, "title")
        context = _required(context, "context")
        lettered = _letter(options)
        recommended = _recommended(recommended, lettered)
        goal = _goal(goal)
        start_here = _start_here(start_here, goal)
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
                "INSERT INTO questions (project, number, goal, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (project, number, goal, stamp, stamp),
            )
            # An INSERT always sets lastrowid; it is only None for other statements.
            question_id = cast(int, cursor.lastrowid)
            if start_here:
                self._mark_start_here(question_id, project, goal, start_here)
            self._insert_version(question_id, 1, title, context, lettered, recommended)
            self.db.execute(
                "UPDATE projects SET next_number = ? WHERE name = ?", (number + 1, project)
            )
        return self.get(project, number)

    def set_next(self, project: str, number: int) -> None:
        project = _required(project, "project")
        with self._transaction():
            self.db.execute("INSERT OR IGNORE INTO projects (name) VALUES (?)", (project,))
            current = self._next_number(project)
            if number < current:
                raise QuestionError(
                    "Numbers are never reused: %s is already at Q%d" % (project, current)
                )
            self.db.execute("UPDATE projects SET next_number = ? WHERE name = ?", (number, project))

    def add_context(self, project: str, number: int, body: str) -> Question:
        body = _required(body, "context")
        question = self._find(project, number)
        self._insert_entry(question, "context", "claude", body)
        return self.get(project, number)

    def reword(
        self,
        project: str,
        number: int,
        note: str,
        title: str | None = None,
        context: str | None = None,
        options: Names | None = None,
        recommended: str | None = None,
        goal: str | None = None,
        start_here: str | None = None,
        clear_start_here: bool = False,
    ) -> Question:
        note = _required(note, "note (why it was reworded)")
        question = self._find(project, number)
        if question["status"] == "archived":
            raise QuestionError("Archived questions cannot be reworded")
        goals = self._goal_change(question, goal, start_here, clear_start_here)
        current = self._current_version(question["id"])
        lettered = _letter(options) if options is not None else json.loads(current["options"])
        if recommended is None and lettered is not None:
            letters = [option["letter"] for option in lettered]
            recommended = current["recommended"] if current["recommended"] in letters else None
        recommended = _recommended(recommended, lettered)
        version = current["version"] + 1
        with self._transaction():
            self._insert_version(
                question["id"],
                version,
                _required(title, "title") if title is not None else current["title"],
                _required(context, "context") if context is not None else current["context"],
                lettered,
                recommended,
            )
            self._insert_entry(question, "reword", "claude", note, version=version)
            if goals is not None:
                self._set_goal(question, *goals)
            # A reword after an answer means the answer asked for more, so the question
            # is open again until the user answers the new version.
            self.db.execute("UPDATE questions SET status = 'open' WHERE id = ?", (question["id"],))
        return self.get(project, number)

    def set_goal(
        self,
        project: str,
        number: int,
        goal: str | None = None,
        start_here: str | None = None,
        clear_start_here: bool = False,
    ) -> Question:
        """Change a question's goal or its "start here" mark without rewording it.

        goal None keeps the goal and an empty goal removes it. A new goal drops the mark,
        which was about the old goal's questions, unless start_here gives a new one.
        """
        question = self._find(project, number)
        goals = self._goal_change(question, goal, start_here, clear_start_here)
        if goals is None:
            raise QuestionError("Give a goal, --start-here or --no-start-here")
        with self._transaction():
            self._set_goal(question, *goals)
        return self.get(project, number)

    def archive_old(self) -> int:
        """Archive every answered question whose latest answer is ARCHIVE_AFTER old."""
        cutoff = (datetime.now(timezone.utc) - ARCHIVE_AFTER).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self.db.execute(
            "UPDATE questions SET status = 'archived', updated_at = ?"
            " WHERE status = 'answered' AND (SELECT MAX(created_at) FROM entries"
            " WHERE question_id = questions.id AND kind = 'answer') <= ?",
            (now(), cutoff),
        ).rowcount

    def archive_answered(self, project: str) -> int:
        cursor = self.db.execute(
            "UPDATE questions SET status = 'archived', updated_at = ?"
            " WHERE project = ? AND status = 'answered'",
            (now(), project),
        )
        return cursor.rowcount

    # Writing, by the user

    def follow_up(self, project: str, number: int, body: str) -> Question:
        body = _required(body, "follow-up")
        question = self._find(project, number)
        if question["status"] == "archived":
            raise QuestionError("This question is archived")
        self._insert_entry(question, "followup", "user", body)
        return self.get(project, number)

    def answer(
        self, project: str, number: int, choice: str | None = None, body: str | None = ""
    ) -> Question:
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

    def reopen(self, project: str, number: int) -> Question:
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

    def get(self, project: str, number: int) -> Question:
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
        # The question's own creation time, not its latest version's, says how long it is open.
        result["created_at"] = question["created_at"]
        result["quick"] = _quick(result["version"], result["options"], result["recommended"])
        result["versions"] = versions
        result["entries"] = entries
        answers = [entry for entry in entries if entry["kind"] == "answer"]
        result["answer"] = answers[-1] if answers else None
        return result

    def list(self, project: str | None = None, status: str | None = "open") -> Questions:
        # Every list, on the page or from the command, first moves old answers to Archived.
        self.archive_old()
        sql = (
            "SELECT q.*, v.title, v.context, v.version, v.options, v.recommended,"
            # Whether the user's last word is a follow-up Claude hasn't answered yet: 'sent'
            # until a session picks it up, 'seen' once one has, else NULL.
            " (SELECT CASE WHEN e.kind = 'followup' AND e.author = 'user' THEN"
            " CASE WHEN e.delivered_at IS NULL THEN 'sent' ELSE 'seen' END END"
            " FROM entries e WHERE e.question_id = q.id ORDER BY e.id DESC LIMIT 1) AS waiting"
            " FROM questions q"
            " JOIN versions v ON v.question_id = q.id"
            " AND v.version = (SELECT MAX(version) FROM versions WHERE question_id = q.id)"
            " WHERE 1 = 1"
        )
        params: Names = []
        if project:
            sql += " AND q.project = ?"
            params.append(project)
        if status and status != "all":
            sql += " AND q.status = ?"
            params.append(status)
        if status in ("answered", "archived"):
            # Newest decision first, so the latest answer is at the top of the list.
            sql += (
                " ORDER BY q.project, (SELECT MAX(created_at) FROM entries"
                " WHERE question_id = q.id AND kind = 'answer') DESC, q.number DESC"
            )
        else:
            sql += " ORDER BY q.project, q.created_at, q.number"
        rows: Questions = []
        for row in self.db.execute(sql, params):
            item = _question_dict(row)
            item["title"] = row["title"]
            item["context"] = row["context"]
            item["version"] = row["version"]
            item["waiting"] = row["waiting"]
            item["quick"] = _quick(row["version"], json.loads(row["options"]), row["recommended"])
            rows.append(item)
        return rows if status in ("answered", "archived") else _by_goal(rows)

    def projects(self) -> Names:
        return [row["name"] for row in self.db.execute("SELECT name FROM projects ORDER BY name")]

    # Projects and their folders, renamed in Settings

    def see_folder(self, folder: str) -> None:
        """Remember a folder a session ran in; a new one is filed under its own name."""
        folder = _required(folder, "folder")
        self.db.execute(
            "INSERT INTO folders (folder, project, seen_at) VALUES (?, ?, ?)"
            " ON CONFLICT (folder) DO UPDATE SET seen_at = excluded.seen_at",
            (folder, folder, now()),
        )

    def project_for(self, folder: str) -> str:
        """The project a folder is filed under; a folder never seen is its own project."""
        row = self.db.execute("SELECT project FROM folders WHERE folder = ?", (folder,)).fetchone()
        return row["project"] if row else folder

    def folders(self) -> Folders:
        return [
            {"folder": row["folder"], "project": row["project"], "seen_at": row["seen_at"]}
            for row in self.db.execute("SELECT * FROM folders ORDER BY folder")
        ]

    def project_summaries(self) -> Summaries:
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
        folders: dict[str, Names] = {}
        for folder in self.folders():
            folders.setdefault(folder["project"], []).append(folder["folder"])
        return [
            {"name": name, "questions": counts.get(name, 0), "folders": folders.get(name, [])}
            for name in sorted(set(counts) | set(folders), key=str.lower)
        ]

    def rename_project(self, old: str, new: str | None) -> Moves:
        """Give a project a new name: its questions, numbering and folders move with it.

        Joining a project into one that already has questions moves its questions after
        the other's, in their order, and notes each one's old number in its thread.
        Returns those moves as {"from": old number, "to": new number}; empty when the
        numbers stay the same.
        """
        new = (new or "").strip()
        if not new:
            raise QuestionError("A project needs a name")
        if new == old:
            raise QuestionError("The project is already called %s" % new)
        if not any(project["name"] == old for project in self.project_summaries()):
            raise QuestionError("There is no project called %s" % old)
        moves: Moves = []
        with self._transaction():
            if self._question_count(new) and self._question_count(old):
                moves = self._renumber_into(old, new)
            elif self._question_count(old) or self._has_project_row(old):
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
        return moves

    def _renumber_into(self, old: str, new: str) -> Moves:
        """Move old's questions after new's, keeping their order, and note where they came from."""
        start = self._next_number(new)
        rows = self.db.execute(
            "SELECT * FROM questions WHERE project = ? ORDER BY number", (old,)
        ).fetchall()
        moves = []
        for position, row in enumerate(rows):
            number = start + position
            self.db.execute(
                "UPDATE questions SET project = ?, number = ? WHERE id = ?",
                (new, number, row["id"]),
            )
            self._insert_entry(
                row,
                "context",
                "claude",
                "Moved from %s Q%d when %s joined %s." % (old, row["number"], old, new),
            )
            moves.append({"from": row["number"], "to": number})
        self.db.execute(
            "UPDATE projects SET next_number = ? WHERE name = ?", (start + len(rows), new)
        )
        return moves

    def undelivered(self, project: str | None = None) -> Entries:
        sql = (
            "SELECT e.*, q.project, q.number FROM entries e"
            " JOIN questions q ON q.id = e.question_id"
            " WHERE e.author = 'user' AND e.delivered_at IS NULL"
        )
        params: Names = []
        if project:
            sql += " AND q.project = ?"
            params.append(project)
        sql += " ORDER BY e.id"
        return [
            dict(_entry_dict(row), project=row["project"], number=row["number"])
            for row in self.db.execute(sql, params)
        ]

    def mark_delivered(self, entry_ids: Ids) -> None:
        if not entry_ids:
            return
        # Only "?" placeholders are joined into the SQL; the ids are bound as parameters.
        placeholders = ",".join("?" * len(entry_ids))
        self.db.execute(
            "UPDATE entries SET delivered_at = ? WHERE id IN (%s)" % placeholders,  # noqa: S608  # nosec B608
            [now(), *entry_ids],
        )

    # Helpers

    def _transaction(self) -> _Transaction:
        return _Transaction(self.db)

    def _next_number(self, project: str) -> int:
        row = self.db.execute(
            "SELECT next_number FROM projects WHERE name = ?", (project,)
        ).fetchone()
        return row["next_number"] if row else 1

    def _question_count(self, project: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM questions WHERE project = ?", (project,)
        ).fetchone()[0]

    def _has_project_row(self, project: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone()
            is not None
        )

    def _find(self, project: str, number: int | str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM questions WHERE project = ? AND number = ?", (project, int(number))
        ).fetchone()
        if row is None:
            raise QuestionError("Q%s does not exist in %s" % (number, project))
        return row

    def _goal_change(
        self,
        question: sqlite3.Row,
        goal: str | None,
        start_here: str | None,
        clear_start_here: bool,
    ) -> tuple[str | None, str | None] | None:
        """The goal and mark a question ends up with, checked, or None for no change."""
        if goal is None and start_here is None and not clear_start_here:
            return None
        if start_here is not None and clear_start_here:
            raise QuestionError("Pass either a start-here reason or no-start-here, not both")
        new_goal = question["goal"] if goal is None else _goal(goal)
        if clear_start_here:
            mark = None
        elif start_here is not None:
            mark = start_here
        else:
            mark = question["start_here"] if new_goal == question["goal"] else None
        return new_goal, _start_here(mark, new_goal)

    def _set_goal(self, question: sqlite3.Row, goal: str | None, start_here: str | None) -> None:
        self.db.execute(
            "UPDATE questions SET goal = ?, start_here = NULL, updated_at = ? WHERE id = ?",
            (goal, now(), question["id"]),
        )
        if start_here:
            self._mark_start_here(question["id"], question["project"], goal, start_here)

    def _mark_start_here(
        self, question_id: int, project: str, goal: str | None, reason: str
    ) -> None:
        """Mark one question as the one to answer first; only one per project and goal."""
        self.db.execute(
            "UPDATE questions SET start_here = NULL WHERE project = ? AND goal IS ? AND id != ?",
            (project, goal, question_id),
        )
        self.db.execute("UPDATE questions SET start_here = ? WHERE id = ?", (reason, question_id))

    def _current_version(self, question_id: int) -> sqlite3.Row:
        return self.db.execute(
            "SELECT * FROM versions WHERE question_id = ? ORDER BY version DESC LIMIT 1",
            (question_id,),
        ).fetchone()

    def _insert_version(
        self,
        question_id: int,
        version: int,
        title: str,
        context: str,
        options: Options,
        recommended: str | None,
    ) -> None:
        self.db.execute(
            "INSERT INTO versions"
            " (question_id, version, title, context, options, recommended, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (question_id, version, title, context, json.dumps(options), recommended, now()),
        )

    def _insert_entry(
        self,
        question: sqlite3.Row,
        kind: str,
        author: str,
        body: str,
        choice: str | None = None,
        version: int | None = None,
    ) -> None:
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
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def __enter__(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.db.execute("COMMIT" if kind is None else "ROLLBACK")


def _required(value: str | None, name: str) -> str:
    value = (value or "").strip()
    if not value:
        raise QuestionError("A question needs a %s" % name)
    return value


def _letter(options: Sequence[str | None] | None) -> Options:
    texts = [(option or "").strip() for option in (options or [])]
    if len(texts) < 2 or not all(texts):
        raise QuestionError("A question needs at least two options, each written out")
    if len(texts) > 26:
        raise QuestionError("A question can have at most 26 options")
    return [{"letter": letter, "text": text} for letter, text in zip(string.ascii_lowercase, texts)]


def _goal(goal: str | None) -> str | None:
    """A goal on one line, or None for no goal."""
    return " ".join((goal or "").split()) or None


def _start_here(reason: str | None, goal: str | None) -> str | None:
    """The one-line reason a question is the one to answer first, or None."""
    if reason is None:
        return None
    reason = " ".join(reason.split())
    if not reason:
        raise QuestionError("Say what answering it first unlocks, for example 'unlocks Q5 and Q6'")
    if goal is None:
        raise QuestionError("Give the question a goal first: start here marks one per goal")
    return reason


def _quick(version: int, options: Options, recommended: str | None) -> bool:
    """Quick to answer: a recommended option, few options, and never reworded."""
    return version == 1 and recommended is not None and len(options) <= QUICK_OPTIONS


def _by_goal(questions: Questions) -> Questions:
    """Open questions grouped by goal within each project: groups in order of their
    oldest question, the questions without a goal last; within a group the "start here"
    question first, then oldest first. The questions arrive per project, oldest first."""
    projects: dict[str, int] = {}
    groups: dict[tuple[str, str | None], int] = {}
    for index, question in enumerate(questions):
        projects.setdefault(question["project"], index)
        groups.setdefault((question["project"], question["goal"]), index)

    def key(item: tuple[int, Question]) -> tuple[int, bool, int, bool, int]:
        index, question = item
        project, goal = question["project"], question["goal"]
        return (
            projects[project],
            goal is None,
            groups[(project, goal)],
            not question["start_here"],
            index,
        )

    return [question for _, question in sorted(enumerate(questions), key=key)]


def _recommended(recommended: str | None, options: Options) -> str | None:
    if not recommended:
        return None
    recommended = recommended.strip().lower()
    if recommended not in [option["letter"] for option in options]:
        raise QuestionError("The recommended option (%s) does not exist" % recommended)
    return recommended


def _question_dict(row: sqlite3.Row) -> Question:
    return {
        "project": row["project"],
        "number": row["number"],
        "label": "Q%d" % row["number"],
        "status": row["status"],
        "goal": row["goal"],
        "start_here": row["start_here"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _version_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "version": row["version"],
        "title": row["title"],
        "context": row["context"],
        "options": json.loads(row["options"]),
        "recommended": row["recommended"],
        "created_at": row["created_at"],
    }


def _entry_dict(row: sqlite3.Row) -> Entry:
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
