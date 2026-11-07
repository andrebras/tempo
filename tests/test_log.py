import os
import re
from unittest import mock

from tempo import log
from tests.helpers import DatabaseTest

# A UTC time, then the fields.
LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (.*)$")


class LogTest(DatabaseTest):
    """The one-line log of change requests and hook runs, in server.log."""

    def lines(self):
        """The log's lines, without their times."""
        with open(os.path.join(self.tmp.name, "server.log"), encoding="utf-8") as file:
            return [LINE.match(line).group(1) for line in file.read().splitlines()]

    def test_writes_one_line_next_to_the_database(self):
        log.write("POST", "/api/questions/demo/1/answer", 200)
        log.write("hook", "stop", "demo", "error: two\nlines")
        self.assertEqual(
            self.lines(),
            ["POST /api/questions/demo/1/answer 200", "hook stop demo error: two lines"],
        )

    def test_a_new_log_is_private(self):
        log.write("hook", "stop", "demo", "let through")
        mode = os.stat(os.path.join(self.tmp.name, "server.log")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_a_full_log_moves_to_server_log_1(self):
        path = os.path.join(self.tmp.name, "server.log")
        with open(path, "w") as file:
            file.write("old\n" * (log.MAX_BYTES // 4 + 1))
        log.write("hook", "stop", "demo", "let through")
        self.assertEqual(self.lines(), ["hook stop demo let through"])
        self.assertGreater(os.path.getsize(path + ".1"), log.MAX_BYTES)

    def test_a_log_problem_is_ignored(self):
        with mock.patch.object(log.os, "open", side_effect=OSError("disk full")):
            log.write("hook", "stop", "demo", "let through")
        with mock.patch.object(log, "log_path", side_effect=RuntimeError):
            log.write("hook", "stop", "demo", "let through")
