import os
import shutil
import subprocess
import tempfile
import unittest

from tempo.server import ROOT

LAUNCHER = os.path.join(ROOT, "bin", "tempo")


class LauncherTest(unittest.TestCase):
    """bin/tempo runs in the session's folder, which may pin a Python that isn't there."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = tmp.name
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TEMPO_", "OQ_", "ASDF_", "PYENV_", "MISE_"))
        }
        self.env["TEMPO_DB"] = os.path.join(self.folder, "questions.db")

    def run_tempo(self, env):
        return subprocess.run(
            [LAUNCHER, "list", "--project", "demo"],
            cwd=self.folder,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_a_python3_that_fails_in_this_folder_is_skipped(self):
        # What a version manager's shim does when the folder pins a Python that isn't
        # installed: it fails with exit code 126.
        shims = os.path.join(self.folder, "shims")
        os.mkdir(shims)
        shim = os.path.join(shims, "python3")
        with open(shim, "w") as file:
            file.write('#!/bin/sh\necho "No version is set for command python3" >&2\nexit 126\n')
        os.chmod(shim, 0o755)
        result = self.run_tempo({**self.env, "PATH": shims + ":/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No open questions", result.stdout)

    @unittest.skipUnless(shutil.which("asdf"), "asdf is not installed")
    def test_a_folder_pinning_a_missing_python_with_asdf(self):
        with open(os.path.join(self.folder, ".tool-versions"), "w") as file:
            file.write("python 3.4.99\n")
        # A Python started by asdf has its own folder first on PATH; a session's hooks
        # see the shims first.
        data = os.environ.get("ASDF_DATA_DIR") or os.path.expanduser("~/.asdf")
        path = os.path.join(data, "shims") + ":" + self.env["PATH"]
        result = self.run_tempo({**self.env, "PATH": path})
        self.assertEqual(result.returncode, 0, result.stderr)
