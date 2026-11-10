"""Projects and the folders filed under them: remembering the folders sessions run in,
and renaming or joining projects in Settings."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from tempo.store import common
from tempo.store.common import (
    Folders,
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


# The system's folders for temporary files. A session that runs in one, such as an agent's
# scratchpad or a test, is thrown away with it, so it is never a project of its own. The
# paths are only compared with, never written to.
SYSTEM_TEMPORARY = ("/tmp", "/var/tmp")  # noqa: S108  # nosec B108
TEMPORARY = tuple(
    sorted({os.path.realpath(path) for path in (tempfile.gettempdir(), *SYSTEM_TEMPORARY)})
)


def temporary(path: str) -> bool:
    """Whether path is inside one of the system's temporary folders."""
    return any(path == root or path.startswith(root + os.sep) for root in TEMPORARY)


class ProjectStore(QuestionStore):
    """The Store's project and folder methods."""

    def projects(self) -> Names:
        return [row["name"] for row in self.db.execute("SELECT name FROM projects ORDER BY name")]

    # Projects and their folders, renamed in Settings

    def see_folder(self, folder: str, path: str | None = None) -> bool:
        """Remember a folder a session ran in; a new one is filed under its own name.

        A folder is matched by its path first, then by a row of the same name that has no
        path yet (from before paths were stored), which then gets this path. Returns True
        when the folder is new. A folder that was renamed or moved has a new path, so it
        is a new folder: its project starts empty. A folder inside a temporary folder is
        not remembered; its project is listed only once it has a question.
        """
        folder = _required(folder, "folder")
        if path and temporary(path):
            return False
        with self._transaction():
            row = self._folder_row(folder, path)
            if row is not None:
                self.db.execute(
                    "UPDATE folders SET path = ?, seen_at = ? WHERE rowid = ?",
                    (path or row["path"], common.now(), row["rowid"]),
                )
                return False
            # The name is the key, unless another folder of this name has it already.
            taken = self.db.execute("SELECT 1 FROM folders WHERE folder = ?", (folder,))
            key = path if path and taken.fetchone() else folder
            self.db.execute(
                "INSERT INTO folders (folder, path, project, seen_at) VALUES (?, ?, ?, ?)",
                (key, path, self._new_project(folder, path), common.now()),
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

    def forget_gone_folders(self) -> int:
        """Forget the folders of projects that have nothing in them and nowhere to be.

        A session's folder is remembered before its first question, so a throwaway folder
        leaves an empty project behind. Such a project's folders are forgotten once every
        one of them is gone from disk or inside a temporary folder; the project then is no
        longer listed. A project
        with questions or a numbering is never touched, nor a folder still on disk or
        one whose path isn't known. Returns how many folders were forgotten.

        Only the folders of empty projects are checked, a handful of paths, and the
        database is written only when one has to go, so listing projects stays cheap.
        """
        if not self._gone_folders():
            return 0
        with self._transaction():
            # Checked again under the lock: a question may have arrived meanwhile.
            gone = self._gone_folders()
            self.db.executemany("DELETE FROM folders WHERE rowid = ?", [(row,) for row in gone])
        return len(gone)

    def _gone_folders(self) -> list[int]:
        """The rowids of the folders forget_gone_folders forgets."""
        places: dict[str, list[tuple[int, str | None]]] = {}
        for row in self.db.execute(
            "SELECT rowid, project, path FROM folders f"
            " WHERE NOT EXISTS (SELECT 1 FROM questions q WHERE q.project = f.project)"
            " AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.name = f.project)"
        ):
            places.setdefault(str(row["project"]), []).append((row["rowid"], row["path"]))
        return [
            rowid
            for folders in places.values()
            if all(path and (temporary(path) or not os.path.exists(path)) for _, path in folders)
            for rowid, _ in folders
        ]

    def rename_project(self, old: str, new: str | None) -> Moves:
        """Give a project a new name: its questions, numbering and folders move with it.

        Joining a project into one that already has questions moves its questions after
        the other's, in their order, and notes each one's old number in its thread.
        Returns those moves as {"from": old number, "to": new number}; empty when the
        numbers stay the same.
        """
        with self._transaction():
            return self._rename_project(old, new)

    def _rename_project(self, old: str, new: str | None) -> Moves:
        """rename_project, inside a transaction the caller holds."""
        new = (new or "").strip()
        if not new:
            raise QuestionError("A project needs a name")
        _project_name(new)
        if new == old:
            raise QuestionError("The project is already called %s" % new)
        if not any(project["name"] == old for project in self.project_summaries()):
            raise QuestionError("There is no project called %s" % old)
        moves: Moves = []
        # A project with no questions yet may still have numbers in use: those below its
        # next number (set with set-next, for an older list), so moved questions go after.
        taken = self._question_count(new) or self._next_number(new) > 1
        if taken and self._question_count(old):
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
        return int(
            self.db.execute(
                "SELECT COUNT(*) FROM questions WHERE project = ?", (project,)
            ).fetchone()[0]
        )

    def _has_project_row(self, project: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone()
            is not None
        )
