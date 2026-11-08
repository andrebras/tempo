"""Where the database lives, its tables, and the steps that bring an older database up
to date (CONTRIBUTING.md, "Changing the database")."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import stat
import time
from collections.abc import Callable

from tempo.store.common import QuestionError, _Transaction

DEFAULT_DB = "~/.claude/tempo/questions.db"
# Where the database lived before the plugin was renamed to Tempo.
OLD_DEFAULT_DB = "~/.claude/open-questions/questions.db"
# The folder this code runs from: the installed plugin, the main checkout or a worktree.
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setting(name: str) -> str | None:
    """Read TEMPO_<name> from the environment, or OQ_<name>, its name before the rename."""
    return os.environ.get("TEMPO_" + name) or os.environ.get("OQ_" + name)


def db_path() -> str:
    """The database file: TEMPO_DB if set, else a branch's own database, else the default,
    or the old default if only that one exists. The old file is used where it is, never
    moved or copied."""
    chosen = setting("DB") or branch_db()
    if chosen:
        # Settings such as Claude Code's env block pass "~" and relative paths as written.
        return os.path.abspath(os.path.expanduser(chosen))
    return default_db()


def default_db() -> str:
    """The real questions' database, the one used when nothing chooses another."""
    path, old = os.path.expanduser(DEFAULT_DB), os.path.expanduser(OLD_DEFAULT_DB)
    if not os.path.exists(path) and os.path.exists(old):
        return old
    return path


def branch_db(root: str = CODE_ROOT) -> str | None:
    """A throwaway database inside the code's folder when that folder is a git checkout
    that isn't on main, so code still being built never touches (or migrates) the real
    questions; None otherwise.

    Only two kinds of code use the real database: the installed plugin, which has no .git,
    and the main checkout on main. Any worktree, whatever its branch, and the main checkout
    on another branch or on no branch, get their own."""
    git = os.path.join(root, ".git")
    if os.path.isdir(git):
        try:
            with open(os.path.join(git, "HEAD"), encoding="utf-8") as head:
                on_main = head.read().strip() == "ref: refs/heads/main"
        except OSError:
            on_main = False
        if on_main:
            return None
    elif not os.path.isfile(git):
        return None
    return os.path.join(root, ".tempo-dev", "questions.db")


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
  seen_at TEXT,
  path TEXT,
  renamed_from TEXT,
  rename_answered_at TEXT,
  first_commit TEXT,
  remote TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  name TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_undelivered ON entries (delivered_at) WHERE delivered_at IS NULL;
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path()
    if path != ":memory:" and os.path.dirname(path):
        # Only this account may read the questions. An existing folder is left alone: it
        # may be one the user picked and shares on purpose.
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    if path != ":memory:":
        _make_private(path)
    db.row_factory = sqlite3.Row
    _use_wal(db)
    db.execute("PRAGMA foreign_keys = ON")
    try:
        _migrate(db)
    except BaseException:
        db.close()
        raise
    return db


def _use_wal(db: sqlite3.Connection) -> None:
    """Switch the file to WAL mode, which then stays set. While another process is making
    the same switch on a brand-new database, SQLite says it is locked without waiting, so
    this tries again for up to two seconds."""
    for _ in range(40):
        try:
            db.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error):
                raise
            time.sleep(0.05)
    db.execute("PRAGMA journal_mode = WAL")


def _make_private(path: str) -> None:
    """Make the database file readable by this account only, before WAL mode makes its
    -wal and -shm files, which SQLite creates with the same permissions."""
    with contextlib.suppress(OSError):
        if stat.S_IMODE(os.stat(path).st_mode) != 0o600:
            os.chmod(path, 0o600)


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


def _add_folder_paths(db: sqlite3.Connection) -> None:
    """Folders were known by name only; now each has its full path, so two folders with
    the same name are told apart. Old rows get their path on next use.

    Only a column and an index are added, so an older Tempo still running on this
    database keeps working: it reads and writes folders by name as before.
    """
    columns = [row["name"] for row in db.execute("PRAGMA table_info(folders)")]
    if "path" not in columns:
        db.execute("ALTER TABLE folders ADD COLUMN path TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS folders_path ON folders (path)")


def _add_renames(db: sqlite3.Connection) -> None:
    """A new folder may be a known one renamed or moved: it keeps the paths it may have
    come from until the user links it or keeps it apart."""
    columns = [row["name"] for row in db.execute("PRAGMA table_info(folders)")]
    for column in ("renamed_from", "rename_answered_at"):
        if column not in columns:
            db.execute("ALTER TABLE folders ADD COLUMN %s TEXT" % column)


# Two kinds of database change (CONTRIBUTING.md, "Changing the database"):
#
# - Compatible: a change older code can ignore, such as a new table, a new index or a new
#   column that may be empty. It goes into SCHEMA, and a new column also into ADDITIONS.
#   Both run on every open and add only what is missing; user_version stays the same, so
#   a session still running an older Tempo keeps working on the database.
# - Incompatible: a change older code would get wrong, such as a renamed or dropped column
#   or a value whose meaning changed. It is a numbered step in MIGRATIONS, which raises
#   user_version, and older code then refuses the database (see _migrate).
#
# Steps that bring older databases up to SCHEMA, in order. The database's user_version
# says how many have run; never reorder or remove one, only add new ones at the end.
# Databases made before user_version was used are at 0 whatever their shape, so each
# step must also be safe on a database that already has its change. The first steps are
# compatible ones made before ADDITIONS existed; they stay numbered, since existing
# databases count them in their user_version.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _add_priority,
    _fill_folder_projects,
    _add_goal,
    _add_folder_paths,
    _add_renames,
]


# Compatible additions, run on every open after SCHEMA, in the same transaction. Each must
# add only what is missing (use _add_column), and must work on any older database, since
# it runs before the numbered steps. An index on a new column is made here too, after the
# column, as SCHEMA runs before the column exists on an older database.
def _add_repo_identity(db: sqlite3.Connection) -> None:
    """A git folder keeps its repository's first commit and remote address, so a missing
    folder is offered only to a new folder that is a copy of the same repository."""
    _add_column(db, "folders", "first_commit", "TEXT")
    _add_column(db, "folders", "remote", "TEXT")


ADDITIONS: list[Callable[[sqlite3.Connection], None]] = [
    _add_repo_identity,
]


def _add_column(db: sqlite3.Connection, table: str, column: str, kind: str) -> None:
    """Add a column to a table unless it has it already. Only for fixed names from this
    module: SQLite cannot take a table or column name as a parameter."""
    columns = [row["name"] for row in db.execute("PRAGMA table_info(%s)" % table)]
    if column not in columns:
        db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, kind))


def _version(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def _migrate(db: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA: create what is missing and make the compatible
    additions, then run each numbered step it has not had yet, once, in its own
    transaction.

    A database a newer Tempo has moved past these steps is refused before anything is
    read or written, since this code cannot know what the newer steps changed."""
    if _version(db) > len(MIGRATIONS):
        raise QuestionError(
            "The database was last changed by a newer Tempo (step %d; this one knows %d)."
            " Update Tempo, or restart the session to use the installed version."
            % (_version(db), len(MIGRATIONS))
        )
    # One statement at a time inside one write transaction, so processes opening a brand-new
    # database at the same moment wait for each other (executescript would commit first).
    with _Transaction(db):
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        # Inside the same write lock: two sessions opening at once cannot both find a
        # column missing and both add it.
        for addition in ADDITIONS:
            addition(db)
    for number, step in enumerate(MIGRATIONS, start=1):
        if _version(db) >= number:
            continue
        with _Transaction(db):
            # Another process may have run the step while this one waited for the lock.
            if _version(db) < number:
                step(db)
                db.execute("PRAGMA user_version = %d" % number)
