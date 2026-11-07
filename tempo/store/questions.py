"""Questions, their versions and threads; and, for now, projects, folders and delivery,
which move to their own modules next."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import string
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from tempo.store import common
from tempo.store.common import (
    Entries,
    Entry,
    Folders,
    Identity,
    Ids,
    Moves,
    Names,
    Options,
    Question,
    QuestionError,
    Questions,
    Summaries,
    _project_name,
    _required,
    _Transaction,
)

# Answered questions move to Archived this long after their latest answer.
ARCHIVE_AFTER = timedelta(days=7)

# A question is quick to answer when it has a recommended option, at most this many
# options, and its first wording still stands.
QUICK_OPTIONS = 3


def shown_path(path: str | None) -> str | None:
    """A path as people read it, with the home folder shortened to ~."""
    if path is None:
        return None
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


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
        project = _project_name(_required(project, "project"))
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
            stamp = common.now()
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
        project = _project_name(_required(project, "project"))
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
        # The current version is read inside the lock, so two rewords at once number
        # their versions one after the other instead of both taking the same number.
        with self._transaction():
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
            (common.now(), cutoff),
        ).rowcount

    def archive_answered(self, project: str) -> int:
        cursor = self.db.execute(
            "UPDATE questions SET status = 'archived', updated_at = ?"
            " WHERE project = ? AND status = 'answered'",
            (common.now(), project),
        )
        return cursor.rowcount

    # Writing, by the user

    def follow_up(self, project: str, number: int, body: str) -> Question:
        body = _required(body, "follow-up")
        with self._transaction():
            question = self._find(project, number)
            if question["status"] == "archived":
                raise QuestionError("This question is archived")
            self._insert_entry(question, "followup", "user", body)
        return self.get(project, number)

    def answer(
        self, project: str, number: int, choice: str | None = None, body: str | None = ""
    ) -> Question:
        body = (body or "").strip()
        choice = (choice or "").strip().lower()
        # The checks run inside the write's lock, so a second answer or a reword from
        # another tab or the tempo command cannot slip in between them and the write.
        with self._transaction():
            question = self._find(project, number)
            if question["status"] == "archived":
                raise QuestionError("This question is archived")
            if question["status"] == "answered":
                raise QuestionError("This question is already answered; reopen it to answer again")
            current = self._current_version(question["id"])
            letters = [option["letter"] for option in json.loads(current["options"])]
            if choice:
                if choice not in letters:
                    raise QuestionError("Option (%s) does not exist" % choice)
            elif not body:
                raise QuestionError("Pick an option or write an answer")
            self._insert_entry(
                question,
                "answer",
                "user",
                body,
                choice=choice or None,
                version=current["version"],
            )
            self.db.execute(
                "UPDATE questions SET status = 'answered', updated_at = ? WHERE id = ?",
                (common.now(), question["id"]),
            )
        return self.get(project, number)

    def reopen(self, project: str, number: int) -> Question:
        """Open an answered question again so the user can give a new answer.

        The earlier answers stay in the thread; the next one is delivered as a change.
        """
        with self._transaction():
            question = self._find(project, number)
            if question["status"] != "answered":
                raise QuestionError("Only answered questions can be reopened")
            self.db.execute(
                "UPDATE questions SET status = 'open', updated_at = ? WHERE id = ?",
                (common.now(), question["id"]),
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
            " FROM entries e WHERE e.question_id = q.id ORDER BY e.id DESC LIMIT 1) AS waiting,"
            # Its latest news, as in project_activity: asked, reworded, or Claude's context.
            " MAX(q.created_at, v.created_at, COALESCE((SELECT MAX(created_at) FROM entries"
            " WHERE question_id = q.id AND author = 'claude' AND kind IN ('context', 'reword')),"
            " '')) AS activity"
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
            item["activity"] = row["activity"]
            item["quick"] = _quick(row["version"], json.loads(row["options"]), row["recommended"])
            rows.append(item)
        return rows if status in ("answered", "archived") else _by_goal(rows)

    def projects(self) -> Names:
        return [row["name"] for row in self.db.execute("SELECT name FROM projects ORDER BY name")]

    def project_activity(self) -> dict[str, str]:
        """Each project's latest news for the user: when a question was last asked or
        reworded, or Claude last added context (its reply to a follow-up)."""
        rows = self.db.execute(
            "SELECT q.project, MAX(MAX(q.created_at), MAX(COALESCE(v.created_at, '')),"
            " MAX(COALESCE(e.created_at, ''))) AS latest FROM questions q"
            " LEFT JOIN versions v ON v.question_id = q.id"
            " LEFT JOIN entries e ON e.question_id = q.id AND e.author = 'claude'"
            " AND e.kind IN ('context', 'reword')"
            " GROUP BY q.project"
        )
        return {row["project"]: row["latest"] for row in rows}

    # Projects and their folders, renamed in Settings

    def see_folder(
        self, folder: str, path: str | None = None, identity: Identity | None = None
    ) -> bool:
        """Remember a folder a session ran in; a new one is filed under its own name.

        A folder is matched by its path first, then by a row of the same name that has no
        path yet (from before paths were stored), which then gets this path. Returns True
        when the folder is new.

        identity gives a git folder's first commit and remote address. It may be slow on a
        long history, so it is asked only when the folder is new, gets its path, or has
        neither yet (a folder remembered before they were stored gets them once), and
        before the write lock is taken, so other sessions do not wait for git.
        """
        folder = _required(folder, "folder")
        row = self._folder_row(folder, path)
        fresh = path is not None and (
            row is None
            or row["path"] is None
            or (row["first_commit"] is None and row["remote"] is None)
        )
        first_commit, remote = identity() if identity and fresh else (None, None)
        with self._transaction():
            row = self._folder_row(folder, path)
            if row is not None:
                self.db.execute(
                    "UPDATE folders SET path = ?, seen_at = ?,"
                    " first_commit = COALESCE(?, first_commit), remote = COALESCE(?, remote)"
                    " WHERE rowid = ?",
                    (path or row["path"], common.now(), first_commit, remote, row["rowid"]),
                )
                return False
            # The name is the key, unless another folder of this name has it already.
            taken = self.db.execute("SELECT 1 FROM folders WHERE folder = ?", (folder,))
            key = path if path and taken.fetchone() else folder
            self.db.execute(
                "INSERT INTO folders (folder, path, project, seen_at, first_commit, remote)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (key, path, self._new_project(folder, path), common.now(), first_commit, remote),
            )
            return True

    def _new_project(self, folder: str, path: str | None) -> str:
        """The project a new folder starts in: its name, unless another folder is filed
        under that, as when two repos are both called api. Then its parent folder's name
        is added, "api (personal)", and a number if that is in use too."""
        used = self.db.execute("SELECT 1 FROM folders WHERE project = ?", (folder,)).fetchone()
        if not path or not used:
            return folder
        names = {project["name"] for project in self.project_summaries()}
        parent = os.path.basename(os.path.dirname(path))
        base = "%s (%s)" % (folder, parent) if parent else folder
        name, number = base, 2
        while name in names:
            name, number = "%s %d" % (base, number), number + 1
        return name

    def project_for(self, folder: str, path: str | None = None) -> str:
        """The project a folder is filed under; a folder never seen is its own project."""
        row = self._folder_row(folder, path)
        return str(row["project"]) if row else folder

    def _folder_row(self, folder: str, path: str | None) -> sqlite3.Row | None:
        """The folder at this path, or else the row of this name that has no path yet."""
        row: sqlite3.Row | None = None
        if path is not None:
            row = self.db.execute("SELECT rowid, * FROM folders WHERE path = ?", (path,)).fetchone()
        if row is None:
            row = self.db.execute(
                "SELECT rowid, * FROM folders WHERE folder = ? AND path IS NULL", (folder,)
            ).fetchone()
        return row

    def folders(self) -> Folders:
        """Every folder, by name and then path; a second folder of a name is keyed by its
        path, so its name comes from the path."""
        folders: Folders = [
            {
                "folder": os.path.basename(row["path"]) if row["path"] else row["folder"],
                "path": row["path"],
                "shown": shown_path(row["path"]),
                "project": row["project"],
                "seen_at": row["seen_at"],
            }
            for row in self.db.execute("SELECT * FROM folders")
        ]
        return sorted(folders, key=lambda folder: (folder["folder"] or "", folder["path"] or ""))

    # Folders that may be a known one renamed or moved

    def spot_rename(self, path: str, gone: Callable[[str], bool]) -> Folders:
        """Note that the new folder at path may be a known folder renamed or moved: one
        whose path is gone (no longer on disk). A gone folder stays on offer to each new
        folder until one is linked to it, so a banner the user kept apart, or one not
        answered yet, never hides it from the real renamed folder. Returns the
        candidates; the folder keeps its own project until the user picks.

        A gone folder whose repository is known is offered only to a copy of it: one with
        the same first commit or, when none has that, the same remote address. So a
        deleted repository is not offered to every unrelated new one. A gone folder
        remembered before Tempo stored these is offered to any new git folder, as before.
        """
        identities = {
            row["path"]: (row["first_commit"], row["remote"])
            for row in self.db.execute(
                "SELECT path, first_commit, remote FROM folders WHERE path IS NOT NULL"
            )
        }
        first_commit, remote = identities.get(path, (None, None))
        missing = [
            folder
            for folder in self.folders()
            if folder["path"] and folder["path"] != path and gone(str(folder["path"]))
        ]
        same = [f for f in missing if first_commit and identities[f["path"]][0] == first_commit]
        if not same:
            same = [f for f in missing if remote and identities[f["path"]][1] == remote]
        candidates = [f for f in missing if f in same or not any(identities[f["path"]])]
        if candidates:
            self.db.execute(
                "UPDATE folders SET renamed_from = ? WHERE path = ?",
                (json.dumps([folder["path"] for folder in candidates]), path),
            )
        return candidates

    def renames(self) -> Summaries:
        """New folders that may be renamed or moved ones, waiting for the user to say.
        A folder whose every candidate was since linked to another folder has none left."""
        known = {folder["path"]: folder for folder in self.folders()}
        rows = self.db.execute(
            "SELECT path, renamed_from FROM folders"
            " WHERE renamed_from IS NOT NULL AND rename_answered_at IS NULL ORDER BY path"
        )
        renames = [
            dict(
                known[row["path"]],
                candidates=[known[old] for old in json.loads(row["renamed_from"]) if old in known],
            )
            for row in rows
        ]
        return [rename for rename in renames if rename["candidates"]]

    def link_folder(self, path: str, project: str | None) -> None:
        """File the folder at path under a project, and settle any rename it waited on.
        The missing folders of that project it was offered are forgotten: the folder
        was found, so no other new folder is offered it again.

        Questions asked in the folder before it was linked went to its own project; when
        no other folder uses that project, it joins the one linked to, so their answers
        reach the folder's sessions."""
        project = (project or "").strip()
        if not project:
            raise QuestionError("Pick a project for the folder")
        row = self.db.execute(
            "SELECT project, renamed_from FROM folders WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            raise QuestionError("There is no folder at %s" % path)
        own = row["project"]
        shared = self.db.execute(
            "SELECT 1 FROM folders WHERE project = ? AND path IS NOT ?", (own, path)
        ).fetchone()
        if own != project and not shared and self._question_count(own):
            self.rename_project(own, project)
        with self._transaction():
            self.db.execute(
                "UPDATE folders SET project = ?, rename_answered_at = ? WHERE path = ?",
                (project, common.now(), path),
            )
            for old in json.loads(row["renamed_from"] or "[]"):
                self.db.execute(
                    "DELETE FROM folders WHERE path = ? AND project = ?", (old, project)
                )

    def keep_folder(self, path: str) -> None:
        """The folder at path is not a renamed one: it stays its own project."""
        self.db.execute(
            "UPDATE folders SET rename_answered_at = ? WHERE path = ?", (common.now(), path)
        )

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
        folders: dict[str, Folders] = {}
        for folder in self.folders():
            place = {key: folder[key] for key in ("folder", "path", "shown")}
            folders.setdefault(str(folder["project"]), []).append(place)
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
        _project_name(new)
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
        # Only one question per project and goal is marked "start here": the target
        # project's mark wins over a moved question's.
        marked = {
            row["goal"]
            for row in self.db.execute(
                "SELECT goal FROM questions WHERE project = ? AND start_here IS NOT NULL", (new,)
            )
        }
        moves = []
        for position, row in enumerate(rows):
            number = start + position
            start_here = None if row["goal"] in marked else row["start_here"]
            self.db.execute(
                "UPDATE questions SET project = ?, number = ?, start_here = ? WHERE id = ?",
                (new, number, start_here, row["id"]),
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
        return self._undelivered(project)

    def take_undelivered(self, project: str | None = None) -> Entries:
        """Return the user's undelivered entries and mark them delivered in one
        transaction, so two readers at once (two sessions' hooks, or a hook and tempo
        wait) never both pass on the same entry."""
        with self.delivering(project) as entries:
            return entries

    @contextlib.contextmanager
    def delivering(self, project: str | None = None) -> Iterator[Entries]:
        """take_undelivered for a caller that still has work to do with the entries, such
        as building the text that hands them over: they are marked delivered only when
        that work ends without an error, so a failure never loses them."""
        with self._transaction():
            entries = self._undelivered(project)
            yield entries
            self.mark_delivered([entry["id"] for entry in entries])

    def _undelivered(self, project: str | None) -> Entries:
        sql = (
            "SELECT e.*, q.project, q.number FROM entries e"
            " JOIN questions q ON q.id = e.question_id"
            " WHERE e.author = 'user' AND e.delivered_at IS NULL"
        )
        params: Names = []
        # Only None means every project: an empty name (a session in "/") is no project.
        if project is not None:
            sql += " AND q.project = ?"
            params.append(project)
        sql += " ORDER BY e.id"
        return [
            dict(_entry_dict(row), project=row["project"], number=row["number"])
            for row in self.db.execute(sql, params)
        ]

    def unanswered_follow_ups(self, project: str, before: str) -> Entries:
        """The latest follow-up of each question in the project, open or answered, that
        reached Claude before the given time with nothing after it: no context or reword
        from Claude, and no answer from the user."""
        rows = self.db.execute(
            "SELECT e.*, q.project, q.number FROM entries e"
            " JOIN questions q ON q.id = e.question_id"
            " WHERE q.project = ? AND q.status != 'archived'"
            " AND e.id = (SELECT MAX(id) FROM entries WHERE question_id = q.id"
            " AND kind = 'followup' AND author = 'user')"
            " AND e.delivered_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM entries c WHERE c.question_id = q.id"
            " AND c.kind != 'followup' AND c.id > e.id)"
            " ORDER BY q.number",
            (project, before),
        )
        return [
            dict(_entry_dict(row), project=row["project"], number=row["number"]) for row in rows
        ]

    def mark_delivered(self, entry_ids: Ids) -> None:
        if not entry_ids:
            return
        # Only "?" placeholders are joined into the SQL; the ids are bound as parameters.
        placeholders = ",".join("?" * len(entry_ids))
        self.db.execute(
            "UPDATE entries SET delivered_at = ? WHERE id IN (%s)" % placeholders,  # noqa: S608  # nosec B608
            [common.now(), *entry_ids],
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
            (goal, common.now(), question["id"]),
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
            (question_id, version, title, context, json.dumps(options), recommended, common.now()),
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
        stamp = common.now()
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
