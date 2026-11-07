import os
import subprocess
import unittest

from tempo import slash
from tests.helpers import DatabaseTest
from tests.test_cli import run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = ("list", "show", "answer", "dive")


class ParsingTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.question = self.add(options=["Left", "Right"])
        self.yes_no = self.add(options=["Yes", "No."])

    def test_the_number_comes_with_or_without_a_q(self):
        for typed in ("57", "Q57", "q57 more", " 57a"):
            self.assertEqual(slash.split_number(typed)[0], 57)
        with self.assertRaises(slash.QuestionError):
            slash.split_number("which one?")

    def test_a_letter_in_any_of_the_ways_the_user_writes_it(self):
        for rest, expected in (
            ("a", ("a", "")),
            (" a", ("a", "")),
            (" B)", ("b", "")),
            ("a) keep the old flag", ("a", "keep the old flag")),
            (" b keep it", ("b", "keep it")),
        ):
            self.assertEqual(slash.split_answer(self.question, rest), expected, rest)

    def test_an_option_by_its_text(self):
        for rest, expected in (
            (" yes", ("a", "")),
            (" No", ("b", "")),
            (" no!", ("b", "")),
            (" yes, but later", ("a", "but later")),
            (" Left keep it", ("a", "keep it")),
        ):
            question = self.question if "eft" in rest else self.yes_no
            self.assertEqual(slash.split_answer(question, rest), expected, rest)

    def test_a_word_that_only_starts_like_an_option_is_not_it(self):
        self.assertEqual(slash.split_answer(self.yes_no, " nobody"), ("nobody", ""))

    def test_a_valid_letter_wins_over_an_option_text(self):
        question = self.add(options=["B", "A"])
        self.assertEqual(slash.split_answer(question, " a"), ("a", ""))

    def test_tempo_answer_reads_a_letter_or_an_options_whole_text(self):
        self.assertEqual(slash.option_letter(self.yes_no, "yes"), "a")
        self.assertEqual(slash.option_letter(self.yes_no, "NO"), "b")
        self.assertEqual(slash.option_letter(self.yes_no, "B"), "b")
        self.assertEqual(slash.option_letter(self.yes_no, "maybe"), "maybe")


class SlashCommandTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.add(title="Left or right?")
        self.add(title="Ship it?", options=["Yes", "No"])

    def slash(self, name, typed=""):
        return run("slash", name, stdin=typed)

    def test_list_shows_this_projects_open_questions(self):
        self.add(project="shop", title="Elsewhere")
        code, out, _ = self.slash("list")
        self.assertEqual(code, 0)
        self.assertIn("Open questions in demo:", out)
        self.assertIn("Q1 Left or right?", out)
        self.assertNotIn("Elsewhere", out)

    def test_show_takes_a_number_or_a_code(self):
        code, out, _ = self.slash("show", "1b")
        self.assertEqual(code, 0)
        self.assertIn("(b) Right", out)

    def test_answer_records_it_already_delivered(self):
        code, out, _ = self.slash("answer", "Q1 b) nicer view\n")
        self.assertEqual(code, 0)
        self.assertIn("Recorded your answer to Q1 in demo (Left or right?): (b) Right", out)
        self.assertIn("Note: nicer view", out)
        self.assertEqual(self.store.get("demo", 1)["status"], "answered")
        # The output gave it to Claude: the hook and tempo wait pass on nothing more.
        self.assertEqual(run("hook", "user-prompt-submit", stdin="{}")[1], "")
        self.assertEqual(run("inbox")[1], "Nothing new from the user.\n")

    def test_answer_by_an_options_text(self):
        code, out, _ = self.slash("answer", "2 yes")
        self.assertEqual(code, 0)
        self.assertIn("(a) Yes", out)

    def test_an_unknown_question_or_option_records_nothing(self):
        for typed, error in (
            ("1z", "Option (z) does not exist"),
            ("9 a", "Q9 does not exist in demo"),
            ("2 maybe", "Option (maybe) does not exist"),
            ("which one?", "Start with a question number, such as 57 or 57a"),
        ):
            self.assertEqual(
                self.slash("answer", typed), (0, "Nothing was recorded: %s.\n" % error, "")
            )
        self.assertEqual(
            self.slash("dive", "9")[1], "Nothing was recorded: Q9 does not exist in demo.\n"
        )
        self.assertEqual(self.store.get("demo", 1)["entries"], [])
        self.assertEqual(self.store.get("demo", 2)["entries"], [])

    def test_dive_records_a_follow_up_with_a_default(self):
        code, out, _ = self.slash("dive", "1")
        self.assertEqual(code, 0)
        self.assertIn("Recorded your follow-up on Q1 in demo (Left or right?): Tell me more.", out)
        self.assertIn("Thread:", out)
        code, out, _ = self.slash("dive", "1 why not b?")
        self.assertIn(": why not b?", out)
        code, out, _ = self.slash("dive", "1b")
        self.assertIn(": About (b) Right: Tell me more.", out)
        self.assertEqual(run("inbox")[1], "Nothing new from the user.\n")

    def test_text_is_taken_as_written_never_run(self):
        typed = '1 it\'s "odd" $(touch x) `id` ; rm -rf /\n'
        code, _, _ = self.slash("dive", typed)
        self.assertEqual(code, 0)
        self.assertEqual(self.store.get("demo", 1)["entries"][-1]["body"], typed.strip()[2:])


class SkillFilesTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, "skills", name, "SKILL.md"), encoding="utf-8") as file:
            return file.read()

    def test_each_command_runs_this_plugins_tempo_with_the_text_on_stdin(self):
        for name in SKILLS:
            text = self.read(name)
            self.assertIn("disable-model-invocation: true", text, name)
            self.assertIn(
                'allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash %s *)' % name, text
            )
            self.assertIn(
                '```!\n"${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash %s --cwd "${CLAUDE_PROJECT_DIR}"'
                " <<'TEMPO_TYPED_TEXT'\n$ARGUMENTS\nTEMPO_TYPED_TEXT\n```" % name,
                text,
            )
            words = " ".join(text.split())
            self.assertIn("never run `tempo answer` or `tempo reply`", words)
            self.assertIn("If the output says nothing was recorded", words)

    def test_the_heredoc_hands_the_text_over_untouched(self):
        # The same shell lines the skill runs, with the text Claude Code puts in place.
        typed = '57 it\'s "odd" $(id) `id` ; echo no'
        script = "cat <<'TEMPO_TYPED_TEXT'\n%s\nTEMPO_TYPED_TEXT\n" % typed
        out = subprocess.run(
            ["/bin/sh", "-c", script], capture_output=True, text=True, check=True
        ).stdout
        self.assertEqual(out, typed + "\n")
