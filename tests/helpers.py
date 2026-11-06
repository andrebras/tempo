import os
import tempfile
import unittest

from tempo.store import Store, connect


class DatabaseTest(unittest.TestCase):
    """Points OQ_DB at a fresh temporary database for each test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "questions.db")
        self.env = {"OQ_DB": path, "OQ_PROJECT": "demo"}
        self.saved = {key: os.environ.get(key) for key in self.env}
        os.environ.update(self.env)
        self.addCleanup(self.restore_env)
        self.store = Store(connect(path))
        self.addCleanup(self.store.db.close)

    def restore_env(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def add(self, **overrides):
        fields = dict(
            project="demo",
            title="Which way?",
            context="We need to pick one.",
            options=["Left", "Right"],
        )
        fields.update(overrides)
        return self.store.add(**fields)
