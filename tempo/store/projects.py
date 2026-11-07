"""Projects and the folders filed under them: remembering the folders sessions run in,
spotting a folder that was renamed or moved, and renaming or joining projects in
Settings."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable

from tempo.store import common
from tempo.store.common import (
    Folders,
    Identity,
    Moves,
    Names,
    QuestionError,
    Summaries,
    _project_name,
    _required,
)
from tempo.store.questions import QuestionStore


def shown_path(path: str | None) -> str | None:
    """A path as people read it, with the home folder shortened to ~."""
    if path is None:
        return None
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


class ProjectStore(QuestionStore):
    """The Store's project and folder methods."""

    def projects(self) -> Names:
        return [row["name"] for row in self.db.execute("SELECT name FROM projects ORDER BY name")]

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

    # Helpers

    def _question_count(self, project: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM questions WHERE project = ?", (project,)
        ).fetchone()[0]

    def _has_project_row(self, project: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone()
            is not None
        )
