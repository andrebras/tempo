"""bin/release: its refusals and its file edits, in a throwaway repository with its own origin.

Nothing is pushed to GitHub and no pull request is opened: the clean CI run and the publish
step are replaced by mocks.
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("release", str(ROOT / "bin" / "release"))
spec = importlib.util.spec_from_loader("release", loader)
release = importlib.util.module_from_spec(spec)
loader.exec_module(release)

PLUGIN = {"name": "tempo", "version": "0.5.4", "description": "Questions."}
PYPROJECT = '[project]\nname = "tempo"\nversion = "0.5.4"\n\n[tool.ruff]\ntarget-version = "py39"\n'
CHANGELOG = """\
# Changelog

Intro.

## [Unreleased]

### Added

- A new thing.

## [0.5.4] - 2026-11-01

### Fixed

- An old thing.
"""
TODAY = date(2026, 11, 7)


class ReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Build the repository and its origin once; each test works on a copy of both."""
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.template = Path(tmp.name)
        config = cls.template / "gitconfig"
        config.write_text(
            "[user]\n\tname = t\n\temail = t@t\n[commit]\n\tgpgsign = false\n"
            "[init]\n\tdefaultBranch = main\n"
        )
        # No GIT_DIR and the like from a hook or git command running the tests, and none
        # of this computer's git settings.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        cls.addClassCleanup(patcher.stop)

        origin = cls.template / "origin.git"
        root = cls.template / "tempo"
        cls.git("init", "--quiet", "--bare", str(origin), cwd=cls.template)
        cls.git("clone", "--quiet", str(origin), str(root), cwd=cls.template)
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(PLUGIN, indent=2) + "\n")
        (root / "pyproject.toml").write_text(PYPROJECT)
        (root / "CHANGELOG.md").write_text(CHANGELOG)
        cls.git("add", ".", cwd=root)
        cls.git("commit", "--quiet", "-m", "Start", cwd=root)
        cls.git("push", "--quiet", "origin", "main", cwd=root)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.origin = Path(tmp.name) / "origin.git"
        self.root = Path(tmp.name) / "tempo"
        shutil.copytree(self.template / "origin.git", self.origin)
        shutil.copytree(self.template / "tempo", self.root)
        self.repo("remote", "set-url", "origin", str(self.origin), cwd=self.root)

    @staticmethod
    def git(*args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    def repo(self, *args, cwd=None):
        return self.git(*args, cwd=cwd or self.root)

    def plugin(self):
        return self.root / ".claude-plugin" / "plugin.json"

    def stub(self, name, **kwargs):
        patcher = mock.patch.object(release, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def release(self, version="0.6.0"):
        """Run bin/release with the clean CI run and the publish step replaced by mocks."""
        ci = self.stub("clean_ci")
        publish = self.stub("publish", return_value="https://pr/1")
        out = io.StringIO()
        with redirect_stdout(out):
            release.release(version, root=self.root, today=TODAY)
        return ci, publish, out.getvalue()

    def assertRefused(self, message, version="0.6.0"):
        with self.assertRaisesRegex(release.Refusal, message):
            self.release(version)
        self.assertEqual(self.repo("branch", "--show-current"), "main")
        self.assertEqual(self.repo("branch", "--list", "release/*"), "")

    def test_refuses_a_version_that_is_not_x_y_z(self):
        for version in ("0.6", "v0.6.0", "0.6.0-rc1", "0.6.0.1", "zero"):
            with self.subTest(version):
                self.assertRefused("not a version like 1.2.3", version)

    def test_refuses_a_version_that_is_not_higher(self):
        for version in ("0.5.4", "0.5.3", "0.4.9"):
            with self.subTest(version):
                self.assertRefused("not higher than the current version, 0.5.4", version)

    def test_compares_versions_as_numbers(self):
        release.check_version("0.10.0", "0.9.9")
        release.check_version("1.0.0", "0.99.99")

    def test_refuses_a_branch_other_than_main(self):
        self.repo("switch", "--quiet", "-c", "other")
        with self.assertRaisesRegex(release.Refusal, "on 'other'"):
            self.release()

    def test_refuses_uncommitted_changes(self):
        (self.root / "notes.txt").write_text("draft")
        self.assertRefused("uncommitted changes")

    def test_refuses_main_behind_origin(self):
        other = self.root.parent / "other"
        self.repo("clone", "--quiet", str(self.origin), str(other))
        (other / "more.txt").write_text("more")
        self.repo("add", ".", cwd=other)
        self.repo("commit", "--quiet", "-m", "More", cwd=other)
        self.repo("push", "--quiet", "origin", "main", cwd=other)
        self.assertRefused("not the same as origin/main")

    def test_refuses_main_ahead_of_origin(self):
        (self.root / "more.txt").write_text("more")
        self.repo("add", ".")
        self.repo("commit", "--quiet", "-m", "More")
        self.assertRefused("not the same as origin/main")

    def test_refuses_an_empty_unreleased_section(self):
        empty = CHANGELOG.replace("- A new thing.\n", "")
        (self.root / "CHANGELOG.md").write_text(empty)
        self.repo("commit", "--quiet", "-am", "Empty")
        self.repo("push", "--quiet", "origin", "main")
        self.assertRefused("empty: nothing to release")

    def test_refuses_a_changelog_without_unreleased(self):
        (self.root / "CHANGELOG.md").write_text(CHANGELOG.replace("[Unreleased]", "[0.5.5]"))
        self.repo("commit", "--quiet", "-am", "No unreleased")
        self.repo("push", "--quiet", "origin", "main")
        self.assertRefused("no '## \\[Unreleased\\]' section")

    def test_refuses_a_version_the_changelog_already_has(self):
        changelog = CHANGELOG.replace("## [0.5.4]", "## [0.6.0] - 2026-11-02\n\n## [0.5.4]")
        (self.root / "CHANGELOG.md").write_text(changelog)
        self.repo("commit", "--quiet", "-am", "Odd")
        self.repo("push", "--quiet", "origin", "main")
        self.assertRefused("already has a section for 0.6.0")

    def test_commits_the_release_on_its_own_branch(self):
        ci, publish, out = self.release("0.6.0")

        self.assertEqual(self.repo("log", "-1", "--format=%s", "release/0.6.0"), "Release 0.6.0")
        changed = self.repo("diff", "--name-only", "main", "release/0.6.0").splitlines()
        self.assertEqual(
            sorted(changed), [".claude-plugin/plugin.json", "CHANGELOG.md", "pyproject.toml"]
        )
        self.assertEqual(self.repo("branch", "--show-current"), "main")

        self.repo("switch", "--quiet", "release/0.6.0")
        self.assertEqual(json.loads(self.plugin().read_text()), dict(PLUGIN, version="0.6.0"))
        self.assertEqual(
            (self.root / "pyproject.toml").read_text(),
            PYPROJECT.replace('version = "0.5.4"', 'version = "0.6.0"'),
        )
        self.assertEqual(
            (self.root / "CHANGELOG.md").read_text(),
            CHANGELOG.replace("## [Unreleased]", "## [0.6.0] - 2026-11-07"),
        )

        ci.assert_called_once_with(self.root, "release/0.6.0")
        root, branch, version, body = publish.call_args.args
        self.assertEqual((root, branch, version), (self.root, "release/0.6.0", "0.6.0"))
        self.assertIn("version 0.5.4 → 0.6.0", body)
        self.assertIn("- A new thing.", body)
        self.assertIn("claude plugin marketplace update tempo", out)
        self.assertIn("claude plugin update tempo@tempo", out)
        self.assertIn("==> Run bin/ci in a clean copy", out)

    def test_keeps_the_plugin_file_layout(self):
        text = '{\n  "name": "tempo",\n  "version": "0.5.4",\n  "author": { "name": "x" }\n}\n'
        self.plugin().write_text(text)
        release.apply_release(self.root, "0.6.0", TODAY)
        self.assertEqual(self.plugin().read_text(), text.replace("0.5.4", "0.6.0"))

    def test_adds_the_new_versions_compare_link(self):
        base = "https://github.com/andrebras/tempo"
        links = "\n[Unreleased]: %s/compare/v0.5.4...HEAD\n[0.5.4]: %s/compare/v0.5.3...v0.5.4\n"
        (self.root / "CHANGELOG.md").write_text(CHANGELOG + links % (base, base))
        release.apply_release(self.root, "0.6.0", TODAY)
        self.assertTrue(
            (self.root / "CHANGELOG.md").read_text().endswith(
                "\n[Unreleased]: %s/compare/v0.6.0...HEAD\n"
                "[0.6.0]: %s/compare/v0.5.4...v0.6.0\n"
                "[0.5.4]: %s/compare/v0.5.3...v0.5.4\n" % (base, base, base)
            )
        )

    def test_stops_when_ci_fails(self):
        self.stub("clean_ci", side_effect=subprocess.CalledProcessError(1, ["bin/ci"]))
        publish = self.stub("publish")
        with redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            release.release("0.6.0", root=self.root, today=TODAY)
        publish.assert_not_called()

    def test_runs_ci_in_a_clean_copy_outside_the_checkout(self):
        marker = self.root.parent / "ci-ran-in"
        ci = self.root / "bin" / "ci"
        ci.parent.mkdir()
        ci.write_text('#!/bin/sh\npwd > "%s"\ngit status --porcelain >> "%s"\n' % (marker, marker))
        ci.chmod(0o755)
        (self.root / "notes.txt").write_text("not committed, so not in the copy")
        self.repo("add", "bin")
        self.repo("commit", "--quiet", "-m", "CI")
        with redirect_stdout(io.StringIO()):
            release.clean_ci(self.root, "main")
        folder, *status = marker.read_text().splitlines()
        self.assertNotEqual(Path(folder).resolve(), self.root.resolve())
        self.assertFalse(Path(folder).resolve().is_relative_to(self.root.resolve()))
        self.assertEqual(status, [])
        self.assertFalse(Path(folder).exists())
        self.assertEqual(len(self.repo("worktree", "list").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
