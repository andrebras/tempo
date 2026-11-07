"""What every part of the store shares: the shapes it passes around, its error, the
clock, and the write transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Optional

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
Folders = list[dict[str, Optional[str]]]
Summaries = list[dict[str, Any]]
Ids = list[int]
# Questions moved by a join: {"from": old number, "to": new number}.
Moves = list[dict[str, int]]
# Gives a git folder's (first commit, remote address); either may be None.
Identity = Callable[[], tuple[Optional[str], Optional[str]]]


class QuestionError(ValueError):
    """Raised when a request breaks the question format or rules."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        if kind is None:
            self.db.execute("COMMIT")
        # SQLite rolls back by itself after some errors; a second rollback would fail and
        # hide the error that caused it.
        elif self.db.in_transaction:
            self.db.execute("ROLLBACK")


def _required(value: str | None, name: str) -> str:
    value = (value or "").strip()
    if not value:
        raise QuestionError("A question needs a %s" % name)
    return value


def _project_name(name: str) -> str:
    """A project's name as given, unless it is "." or "..": the page's links could not reach
    such a project, since a browser reads them as steps between folders."""
    if name in (".", ".."):
        raise QuestionError('A project can\'t be called "%s"' % name)
    return name
