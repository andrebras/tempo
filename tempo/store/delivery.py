"""Handing the user's answers and follow-ups to Claude: the hook and `tempo wait` take
the entries not delivered yet and mark them delivered."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from tempo.store import common
from tempo.store.common import Entries, Ids, Names, StoreBase
from tempo.store.questions import _entry_dict


class DeliveryStore(StoreBase):
    """The Store's delivery methods."""

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
