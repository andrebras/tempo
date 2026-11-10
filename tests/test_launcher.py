import os
import shutil
import subprocess
import tempfile
import time
import unittest

from tempo import cli
from tempo.server import ROOT
from tests import TEST_PORT

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
        self.env["TEMPO_PORT"] = str(TEST_PORT)

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

    def test_a_tempo_package_in_the_current_folder_is_not_run(self):
        # Python puts the current folder first on its search path, so from inside another
        # Tempo checkout (or any folder with a tempo/ package) that code would run instead.
        fake = os.path.join(self.folder, "tempo")
        os.mkdir(fake)
        with open(os.path.join(fake, "__init__.py"), "w") as file:
            file.write("")
        with open(os.path.join(fake, "__main__.py"), "w") as file:
            file.write('print("the wrong tempo ran")\n')
        result = self.run_tempo(self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("the wrong tempo ran", result.stdout)
        self.assertIn("No open questions", result.stdout)

    def test_it_runs_through_a_symlink_to_it(self):
        # What `tempo install` makes in ~/.local/bin, here through a relative link too.
        bin_ = os.path.join(self.folder, "bin")
        os.mkdir(bin_)
        os.symlink(LAUNCHER, os.path.join(bin_, "real"))
        os.symlink("real", os.path.join(bin_, "tempo"))
        result = subprocess.run(
            [os.path.join(bin_, "tempo"), "list", "--project", "demo"],
            cwd=self.folder,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No open questions", result.stdout)

    def test_a_wait_it_starts_shows_as_a_tempo_wait(self):
        # The Stop hook finds running waits by their command in ps.
        wait = subprocess.Popen(
            [LAUNCHER, "wait", "--timeout", "30", "--project", "demo"],
            cwd=self.folder,
            env=self.env,
            stdout=subprocess.DEVNULL,
        )
        self.addCleanup(wait.wait)
        self.addCleanup(wait.kill)
        marker = os.path.join(self.folder, "wait-%d.json" % wait.pid)
        deadline = time.time() + 20
        while not os.path.exists(marker) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(marker), "the wait did not start")
        self.assertIn(wait.pid, cli._wait_processes())
