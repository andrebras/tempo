"""SQLite storage for open questions.

A question belongs to a project and has a running number that is never reused.
Its wording lives in versions (a reword adds a version, old ones stay). Everything
that happens to it afterwards (added context, rewords, follow-ups, answers) is an
entry in its thread. Entries written by the user start undelivered; the hook and
`tempo wait` hand them to Claude and mark them delivered.

A project is just a name. The first time a session runs in a git folder, the folder is
filed under a project of the same name; renaming the project in Settings takes its
questions and folders along. A folder is known by its full path; rows from before paths
were stored have only a name, and the first session in a folder of that name fills it in.
The folder column is the row's key: the folder's name, or its full path when another
folder already has that name.

A question can name its goal: the piece of work its answer moves forward. Within a goal,
one question may be marked "start here", with the reason, when its answer unlocks or sets
the pattern for the others.

The code is in parts: common (shared shapes, the error, the clock, the connection),
schema (where the database is, its tables and migrations), questions, projects (with
their folders) and delivery (handing the user's words to Claude). Store, below, puts the
parts' methods together. Everything is imported from here, as
`from tempo.store import Store, connect`.
"""

from tempo.store.common import (
    Entries,
    Entry,
    Folders,
    Identity,
    Ids,
    Moves,
    Names,
    Option,
    Options,
    Question,
    QuestionError,
    Questions,
    Summaries,
    now,
)
from tempo.store.delivery import DeliveryStore
from tempo.store.projects import ProjectStore, shown_path
from tempo.store.questions import ARCHIVE_AFTER, QUICK_OPTIONS
from tempo.store.schema import (
    ADDITIONS,
    CODE_ROOT,
    DEFAULT_DB,
    MIGRATIONS,
    OLD_DEFAULT_DB,
    SCHEMA,
    _add_column,
    _add_folder_paths,
    _migrate,
    branch_db,
    connect,
    db_path,
    setting,
)


class Store(ProjectStore, DeliveryStore):
    """The questions database: open it with `with Store(connect()) as store:`."""


__all__ = [
    "ADDITIONS",
    "ARCHIVE_AFTER",
    "CODE_ROOT",
    "DEFAULT_DB",
    "MIGRATIONS",
    "OLD_DEFAULT_DB",
    "QUICK_OPTIONS",
    "SCHEMA",
    "Entries",
    "Entry",
    "Folders",
    "Identity",
    "Ids",
    "Moves",
    "Names",
    "Option",
    "Options",
    "Question",
    "QuestionError",
    "Questions",
    "Store",
    "Summaries",
    "_add_column",
    "_add_folder_paths",
    "_migrate",
    "branch_db",
    "connect",
    "db_path",
    "now",
    "setting",
    "shown_path",
]
