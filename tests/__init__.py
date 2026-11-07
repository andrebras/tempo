"""Tests never open the real database. Each test that needs one makes its own (see
helpers.DatabaseTest); a test that forgets gets TEMPO_DB pointing into a read-only
folder, so it fails loudly instead of touching ~/.claude/tempo/questions.db."""

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

# Most of the suite's time went on SQLite waiting for the disk after each write (an
# fsync), which only matters if the computer loses power. Test databases are thrown
# away, so they skip that wait; everything else about them is as in use.
_connect = sqlite3.connect


def _connect_without_disk_waits(*args, **kwargs):
    db = _connect(*args, **kwargs)
    db.execute("PRAGMA synchronous = OFF")
    return db


sqlite3.connect = _connect_without_disk_waits
