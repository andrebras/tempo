"""Tests never open the real database. Each test that needs one makes its own (see
helpers.DatabaseTest); a test that forgets gets TEMPO_DB pointing into a read-only
folder, so it fails loudly instead of touching ~/.claude/tempo/questions.db.

Tests never start or stop a real page server either. The page's port is TEST_PORT, never
the real page's 2204, and tempo.cli.start_server fails the test that calls it without
mocking it (StartServerTest runs the real one, with every outside effect mocked)."""

import atexit
import os
import shutil
import sqlite3
import tempfile

_locked = tempfile.mkdtemp(prefix="tempo-tests-")
os.chmod(_locked, 0o500)
atexit.register(shutil.rmtree, _locked, ignore_errors=True)
os.environ["TEMPO_DB"] = os.path.join(_locked, "no-test-database-picked.db")
os.environ.pop("OQ_DB", None)
# Hooks name the project from the folder Claude Code started the session in, when set.
os.environ.pop("CLAUDE_PROJECT_DIR", None)

# A port kept for tests: not the real page's 2204, and outside cli.BRANCH_PORTS. Tests that
# run bin/tempo in a process of their own pass it on, since they clear the TEMPO_ settings.
TEST_PORT = 2209
os.environ["TEMPO_PORT"] = str(TEST_PORT)


class RealServerStart(BaseException):
    """A test reached the real start_server. It is not an Exception, so the hooks, which
    never let an Exception break a session, cannot swallow it: the test errors instead."""


from tempo import cli  # noqa: E402 (after the settings above)

REAL_START_SERVER = cli.start_server


def _no_real_server(port: int) -> bool:
    # A real start would replace the page on the port, and the server it starts outlives
    # the tests: it once took the user's page over during a release.
    raise RealServerStart("a test called tempo.cli.start_server(%d); mock it" % port)


cli.start_server = _no_real_server

# Whether ~/.local/bin/tempo links to Tempo depends on the real home folder, so tests see
# it as linked, and sessions don't offer /tempo:install, unless they patch it.
REAL_TEMPO_LINKED = cli.tempo_linked
cli.tempo_linked = lambda: True

# Most of the suite's time went on SQLite waiting for the disk after each write (an
# fsync), which only matters if the computer loses power. Test databases are thrown
# away, so they skip that wait; everything else about them is as in use.
_connect = sqlite3.connect


def _connect_without_disk_waits(*args, **kwargs):
    db = _connect(*args, **kwargs)
    db.execute("PRAGMA synchronous = OFF")
    return db


sqlite3.connect = _connect_without_disk_waits
