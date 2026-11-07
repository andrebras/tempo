"""Tests never open the real database. Each test that needs one makes its own (see
helpers.DatabaseTest); a test that forgets gets TEMPO_DB pointing into a read-only
folder, so it fails loudly instead of touching ~/.claude/tempo/questions.db."""

import atexit
import os
import shutil
import tempfile

_locked = tempfile.mkdtemp(prefix="tempo-tests-")
os.chmod(_locked, 0o500)
atexit.register(shutil.rmtree, _locked, ignore_errors=True)
os.environ["TEMPO_DB"] = os.path.join(_locked, "no-test-database-picked.db")
os.environ.pop("OQ_DB", None)
