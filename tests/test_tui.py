import contextlib
import io
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tempo import cli
from tempo.store import CODE_ROOT, Store, connect
from tempo.tui.model import NARROW, Board, detail_lines, layout
from tempo.tui.text import cell_width, clip, plain, rich, wrap
from tempo.tui.view import TextCanvas, draw
from tests.helpers import DatabaseTest


def inbox():
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["inbox"])
    return out.getvalue()


@contextlib.contextmanager
def deadline(seconds):
    """Fail, instead of hanging the whole test run, when the block takes too long."""

    def expire(*_):
        raise AssertionError("took more than %d seconds" % seconds)

    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class TextTest(unittest.TestCase):
    def test_paragraphs_lists_and_marks_read_as_on_the_page(self):
        text = "Pick **one** of `a` or `b`.\n\n- First\n- Second\n1. Numbered"
        lines = rich(text, 40)
        self.assertEqual(
            [plain(line) for line in lines],
            ["Pick one of a or b.", "", "• First", "• Second", "1. Numbered"],
        )
        self.assertIn(("one", "bold"), lines[0])
        self.assertIn(("a", "code"), lines[0])

    def test_wrapping_keeps_to_the_width_and_indents_list_items(self):
        text = "- " + "word " * 20 + "\n\nA paragraph that goes on for a while, longer than a line."
        lines = [plain(line) for line in rich(text, 20)]
        self.assertTrue(all(cell_width(line) <= 20 for line in lines))
        self.assertTrue(lines[0].startswith("• word"))
        self.assertTrue(lines[1].startswith("  word"))
        self.assertFalse(any(line.endswith(" ") for line in lines))

    def test_a_word_longer_than_a_line_is_cut(self):
        lines = [plain(line) for line in wrap([("x" * 25, "")], 10)]
        self.assertEqual(lines, ["x" * 10, "x" * 10, "x" * 5])

    def test_an_indent_wider_than_the_line_still_wraps(self):
        # Each of these looped forever, filling memory with empty lines, before the indent
        # was cut to the width.
        with deadline(2):
            lines = wrap([("(a) ", "letter"), ("Hello world", "")], 3, rest="    ")
            narrow = rich("- item text", 1)
            numbered = rich("12345678901234567890123456789012345. item text", 30)
            negative = rich("abc", -2)
        self.assertTrue(all(cell_width(plain(line)) <= 3 for line in lines))
        self.assertEqual("".join(plain(line).strip() for line in lines), "(a)Helloworld")
        self.assertTrue(all(cell_width(plain(line)) <= 1 for line in narrow))
        self.assertTrue(all(cell_width(plain(line)) <= 30 for line in numbered))
        self.assertEqual([plain(line) for line in negative], ["a", "b", "c"])

    def test_emoji_sequences_take_the_columns_a_terminal_gives_them(self):
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        self.assertEqual(cell_width(family), 2)
        self.assertEqual(cell_width("\U0001f44d\U0001f3fd"), 2)
        self.assertEqual(cell_width("a\u200bb"), 2)
        self.assertEqual(cell_width("\u2764\ufe0f"), 1)
        # Clipping and wrapping keep a joined emoji whole.
        self.assertEqual(clip("ab" + family + "cde", 6), "ab" + family + "c…")
        self.assertEqual(
            [plain(line) for line in wrap([(family + " " + family, "")], 3)], [family, family]
        )

    def test_wide_characters_take_two_columns(self):
        self.assertEqual(cell_width("数据库"), 6)
        self.assertEqual(clip("数据库", 5), "数据…")
        self.assertEqual(clip("short", 10), "short")


class BoardTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        # The board's own connection, so writes through self.store count as another
        # program's (Claude's, the page's), as they would in use.
        self.board_store = Store(connect(os.environ["TEMPO_DB"]))
        self.addCleanup(self.board_store.db.close)

    def board(self, home="demo"):
        return Board(self.board_store, home)

    def on_disk(self, name):
        """A folder that exists, so Settings keeps listing its project with no questions."""
        path = os.path.join(tempfile.mkdtemp(), name)
        os.mkdir(path)
        self.addCleanup(shutil.rmtree, os.path.dirname(path))
        return path

    def keys(self, board, *keys):
        for key in keys:
            board.press(key)

    def test_projects_list_open_counts_and_the_folders_project(self):
        self.add()
        self.add()
        self.add(project="shop")
        self.add(project="done")
        self.store.answer("done", 1, "a")
        board = self.board(home="here")
        self.assertEqual(board.projects, [("demo", 2), ("here", 0), ("shop", 1)])
        self.assertEqual(board.project, "here")
        self.assertIsNone(board.question)

    def test_questions_are_grouped_by_goal_with_start_here_first(self):
        self.add(title="No goal")
        self.add(title="Second", goal="Release")
        self.add(title="First", goal="Release", start_here="unlocks Q2")
        rows = self.board().rows()
        self.assertEqual(
            [row.goal or row.question["title"] for row in rows],
            ["Release", "First", "Second", "Other", "No goal"],
        )

    def test_no_headings_when_no_question_has_a_goal(self):
        self.add()
        self.assertEqual([row.goal for row in self.board().rows()], [None])

    def test_the_selection_stays_on_its_question_when_the_database_changes(self):
        self.add(title="One", goal="Later")
        self.add(title="Two")
        board = self.board()
        board.press("j")
        self.assertEqual(board.number, 2)
        self.assertFalse(board.changed())
        # Claude adds a question that is listed before it.
        self.add(title="Zero", goal="Later")
        self.assertTrue(board.changed())
        self.assertEqual([q["title"] for q in board.questions], ["One", "Zero", "Two"])
        self.assertEqual(board.number, 2)

    def test_a_question_answered_elsewhere_gives_way_to_the_next(self):
        for _ in range(3):
            self.add()
        board = self.board()
        board.press("j")
        self.store.answer("demo", 2, "a")
        self.assertTrue(board.changed())
        self.assertEqual(board.number, 3)

    def test_a_rewording_shows_without_moving(self):
        self.add(title="Old")
        board = self.board()
        self.store.reword("demo", 1, "Clearer", title="New")
        self.assertTrue(board.changed())
        self.assertEqual(board.question["title"], "New")

    def test_answering_with_a_letter_then_enter_reaches_claude_like_the_page(self):
        self.add(title="Left or right?")
        board = self.board()
        board.press("b")
        self.assertEqual(board.mode, "note")
        self.assertEqual(self.store.get("demo", 1)["status"], "open")
        board.press("enter")
        self.assertEqual(board.message, ("Answered Q1: (b) Right", "ok"))
        self.assertIsNone(board.question)
        from_screen = inbox()
        self.assertIn("demo Q1 (Left or right?) answered (b) Right", from_screen)
        # The same answer from the page reads the same to Claude.
        self.store.reopen("demo", 1)
        self.store.db.execute("DELETE FROM entries")
        self.store.answer("demo", 1, "b")
        self.assertEqual(inbox(), from_screen)

    def test_an_answer_can_carry_a_note(self):
        self.add()
        board = self.board()
        self.keys(board, "a", *"Nice", "x", "backspace", "enter")
        self.assertEqual(board.message, ("Answered Q1: (a) Left / Note: Nice", "ok"))
        self.assertEqual(self.store.get("demo", 1)["answer"]["body"], "Nice")

    def test_escape_leaves_the_question_open(self):
        self.add()
        board = self.board()
        self.keys(board, "b", "esc", "a", *"No", "esc")
        self.assertEqual(board.mode, "normal")
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_a_follow_up_is_saved_and_delivered(self):
        self.add(title="Left or right?")
        board = self.board()
        self.keys(board, "r", *"Why?", "enter")
        self.assertEqual(board.message, ("Added your follow-up to Q1.", "ok"))
        self.assertIn("demo Q1 (Left or right?) asks for more: Why?", inbox())
        self.assertEqual(board.number, 1)

    def test_the_stores_refusal_is_shown(self):
        self.add()
        board = self.board()
        board.press("a")
        # Answered on the page while the note prompt was showing.
        self.store.answer("demo", 1, "b")
        board.press("enter")
        self.assertEqual(
            board.message,
            ("This question is already answered; reopen it to answer again", "error"),
        )
        board.press("r")
        self.assertEqual(board.message, ("No question selected.", "error"))

    def test_a_reload_while_writing_a_note_keeps_the_answer_on_its_question(self):
        self.add()
        self.add()
        board = self.board()
        board.press("a")
        self.assertEqual(board.target, ("demo", 1))
        # Answered on the page meanwhile: the screen moves on to Q2 as it reloads.
        self.store.answer("demo", 1, "b")
        self.assertTrue(board.changed())
        self.assertEqual(board.number, 2)
        board.press("enter")
        self.assertEqual(board.message[1], "error")
        self.assertEqual(self.store.get("demo", 2)["status"], "open")

    def test_a_reword_while_writing_a_note_refuses_the_answer(self):
        self.add(options=["Keep the old API", "Delete everything"])
        board = self.board()
        board.press("a")
        # Claude swaps the options while the prompt still shows (a) Keep the old API.
        self.store.reword("demo", 1, "Swapped", options=["Delete everything", "Keep the old API"])
        self.assertTrue(board.changed())
        board.press("enter")
        self.assertEqual(
            board.message,
            ("Q1 changed since you looked at it (now version 2); check it again", "error"),
        )
        self.assertEqual(self.store.get("demo", 1)["status"], "open")
        # Picked again, on the wording now shown, the answer goes through.
        self.keys(board, "b", "enter")
        self.assertEqual(self.store.get("demo", 1)["answer"]["choice"], "b")

    def test_a_paste_is_typed_into_a_note_and_ignored_elsewhere(self):
        self.add()
        self.add()
        board = self.board()
        board.paste("b")
        self.assertEqual(board.mode, "normal")
        self.assertEqual(board.message[1], "error")
        self.keys(board, "a")
        board.paste("Go with a.\nbut why?")
        self.assertEqual((board.mode, board.typed), ("note", "Go with a.\nbut why?"))
        board.press("enter")
        self.assertEqual(self.store.get("demo", 1)["answer"]["body"], "Go with a.\nbut why?")
        self.assertEqual(self.store.get("demo", 2)["status"], "open")

    def test_an_empty_follow_up_is_refused_with_the_stores_words(self):
        self.add()
        board = self.board()
        self.keys(board, "r", "enter")
        self.assertEqual(board.message, ("A question needs a follow-up", "error"))

    def test_letters_that_move_need_shift_to_answer(self):
        self.add(options=list("ABCDEFGH"))
        board = self.board()
        board.press("z")
        self.assertEqual(board.message, ("Option (z) does not exist", "error"))
        board.press("h")
        self.assertEqual((board.mode, board.focus), ("normal", "projects"))
        board.press("H")
        self.assertEqual((board.mode, board.letter), ("note", "h"))

    def test_panel_keys(self):
        self.add()
        board = self.board()
        self.assertEqual(board.focus, "questions")
        self.keys(board, "l")
        self.assertEqual(board.focus, "detail")
        self.keys(board, "l", "tab")
        self.assertEqual(board.focus, "projects")
        self.keys(board, "btab", "enter")
        self.assertEqual(board.focus, "detail")
        self.keys(board, "ctrl-d", "j")
        self.assertEqual(board.scroll, board.page + 1)
        self.keys(board, "pgup", "pgup")
        self.assertEqual(board.scroll, 0)
        self.keys(board, "?")
        self.assertEqual(board.mode, "help")
        self.assertTrue(board.press("q"))
        self.assertFalse(board.press("q"))
        board.press("r")
        self.assertFalse(board.press("ctrl-c"))

    def test_moving_between_projects_shows_each_ones_questions(self):
        self.add(title="Here")
        self.add(project="shop", title="There")
        board = self.board()
        self.keys(board, "h", "j")
        self.assertEqual((board.project, board.question["title"]), ("shop", "There"))
        self.keys(board, "k")
        self.assertEqual(board.question["title"], "Here")

    def test_s_opens_settings_with_every_project_and_its_folders(self):
        self.add()
        shop = self.on_disk("shop")
        self.store.see_folder("shop", shop)
        board = self.board()
        board.press("s")
        self.assertEqual(board.mode, "settings")
        self.assertEqual(board.chosen, "demo")
        self.assertEqual([p["name"] for p in board.summaries], ["demo", "shop"])
        self.assertEqual(board.summaries[1]["folders"][0]["path"], shop)
        board.press("j")
        self.assertEqual(board.chosen, "shop")
        board.press("esc")
        self.assertEqual(board.mode, "normal")

    def test_settings_forget_an_empty_project_whose_folder_is_gone(self):
        self.add()
        self.store.see_folder("fork", "/nowhere/scratchpad/fork")
        board = self.board()
        board.press("s")
        self.assertEqual([p["name"] for p in board.summaries], ["demo"])

    def test_merging_moves_the_questions_and_says_where_they_went(self):
        self.add(title="Here")
        self.add(title="Also here")
        self.add(project="shop", title="There")
        self.store.see_folder("shop", "/work/shop")
        board = self.board(home="shop")
        self.keys(board, "s", "m")
        self.assertEqual((board.mode, board.into), ("merge", "demo"))
        board.press("enter")
        self.assertEqual(board.mode, "confirm")
        board.press("y")
        self.assertEqual(board.message, ("Moved 1 question from shop to demo: now Q3.", "ok"))
        self.assertEqual((board.mode, board.chosen), ("settings", "demo"))
        self.assertEqual([p["name"] for p in board.summaries], ["demo"])
        self.assertEqual(self.store.get("demo", 3)["title"], "There")
        self.assertEqual(self.store.project_for("shop", "/work/shop"), "demo")
        # The home project was merged away: the screen moves to the one that took it.
        board.press("esc")
        self.assertEqual((board.mode, board.project, board.home), ("normal", "demo", "demo"))
        self.assertEqual(board.projects, [("demo", 3)])

    def test_a_merge_of_several_names_the_range_and_one_without_moves_says_so(self):
        self.add()
        for title in ("One", "Two"):
            self.add(project="shop", title=title)
        empty = self.on_disk("empty")
        self.store.see_folder("empty", empty)
        board = self.board()
        self.keys(board, "s", "j", "j", "m", "enter", "y")
        self.assertEqual(board.message[0], "Moved 2 questions from shop to demo: now Q2\u2013Q3.")
        # A project with no questions yet only brings its folders.
        self.keys(board, "j", "m", "enter", "y")
        self.assertEqual(board.message, ("Merged empty into demo.", "ok"))
        self.assertEqual(self.store.project_for("empty", empty), "demo")

    def test_cancelling_a_merge_changes_nothing(self):
        self.add()
        self.add(project="shop")
        board = self.board()
        self.keys(board, "s", "m", "esc")
        self.assertEqual((board.mode, board.into), ("settings", None))
        self.keys(board, "m", "enter", "n")
        self.assertEqual((board.mode, board.into), ("settings", None))
        self.keys(board, "m", "enter", "esc", "q")
        self.assertEqual(board.mode, "normal")
        self.assertEqual(self.store.get("shop", 1)["project"], "shop")
        self.assertEqual(board.projects, [("demo", 1), ("shop", 1)])

    def test_a_project_cannot_be_merged_into_itself_or_alone(self):
        self.add()
        board = self.board()
        board.press("s")
        board.press("m")
        self.assertEqual(board.message, ("There is no other project to merge into.", "error"))
        self.add(project="shop")
        board.reload()
        self.keys(board, "m", "k", "enter")
        self.assertEqual(board.message, ("Pick another project to merge demo into.", "error"))
        self.assertEqual(board.mode, "merge")

    def test_detail_shows_options_with_the_recommended_one_and_the_thread(self):
        self.add(
            title="Which database?",
            context="We need **one**.",
            options=["SQLite", "Postgres"],
            recommended="a",
            goal="Release",
            start_here="unlocks Q2",
        )
        self.store.add_context("demo", 1, "SQLite needs no server.")
        self.store.follow_up("demo", 1, "What about backups?")
        lines = [plain(line) for line in detail_lines(self.store.get("demo", 1), 40)]
        self.assertEqual(lines[0], "demo · Q1 · open")
        self.assertIn("Waiting on Claude", lines)
        self.assertIn("Which database?", lines)
        self.assertIn("Start here: unlocks Q2", lines)
        self.assertIn("We need one.", lines)
        self.assertIn("(a) SQLite [recommended]", lines)
        self.assertIn("(b) Postgres", lines)
        self.assertIn("  SQLite needs no server.", lines)
        self.assertIn("  What about backups?", lines)
        self.assertTrue(all(cell_width(line) <= 40 for line in lines))

    def test_detail_shows_the_answer(self):
        self.add()
        self.store.answer("demo", 1, "b", "Nicer view")
        lines = [plain(line) for line in detail_lines(self.store.get("demo", 1), 60)]
        self.assertIn("Your answer: (b) Right · Nicer view", lines)
        self.assertIn("  (b) Right", lines)


class SidebarTest(BoardTest):
    """The compact view for a pane beside Claude's: `tempo tui --sidebar`."""

    def sidebar(self, home="demo"):
        return Board(self.board_store, home, sidebar=True)

    def screen(self, board, width=40, height=16):
        canvas = TextCanvas(height, width)
        draw(board, canvas)
        return canvas.text().split("\n")

    def test_the_list_shows_the_project_its_neighbours_and_the_questions_by_goal(self):
        self.add(title="Left or right?", goal="Release", start_here="unlocks the rest")
        self.add(title="Which colour?")
        self.add(project="shop", title="There")
        lines = self.screen(self.sidebar())
        self.assertEqual(lines[0], "◂ demo · 2 open ▸")
        self.assertEqual(lines[1], "shop 1")
        self.assertEqual(lines[2], "─" * 40)
        self.assertEqual(lines[3], "Release")
        # A block per question: the state column, number and age, then the title.
        self.assertEqual(lines[4], "» Q1" + " " * 34 + "1m")
        self.assertEqual(lines[5], "  Left or right?")
        self.assertEqual(lines[6], "")
        self.assertEqual(lines[7], "Other")
        self.assertTrue(lines[8].startswith("  Q2 ") and lines[8].endswith("1m"))
        self.assertEqual(lines[9], "  Which colour?")
        self.assertTrue(all(cell_width(line) <= 40 for line in lines))
        self.assertTrue(lines[-1].startswith("Enter open  h/l project"))

    def test_h_and_l_switch_project_and_enter_opens_the_question(self):
        self.add(title="Here")
        self.add(project="shop", title="There")
        board = self.sidebar()
        self.keys(board, "l")
        self.assertEqual((board.project, board.question["title"]), ("shop", "There"))
        self.keys(board, "l")
        self.assertEqual(board.project, "demo")
        # Esc in the list must not close the pane.
        self.assertTrue(board.press("esc"))
        self.keys(board, "enter")
        self.assertEqual(board.focus, "detail")
        lines = self.screen(board)
        self.assertIn("Q1 · h back", lines[0])
        self.assertIn("(b) Right", "\n".join(lines))
        self.keys(board, "esc")
        self.assertEqual(board.focus, "questions")

    def test_answering_an_open_question_goes_back_to_the_list(self):
        self.add(title="One")
        self.add(title="Two")
        board = self.sidebar()
        self.keys(board, "enter", "a", "enter")
        self.assertEqual(self.store.get("demo", 1)["status"], "answered")
        self.assertEqual((board.focus, board.number), ("questions", 2))

    def test_a_dot_marks_what_claude_changed_until_it_is_opened(self):
        self.add(title="Old")
        self.add(project="shop", title="There")
        board = self.sidebar()
        # Open when the sidebar started: nothing is new yet.
        self.assertNotIn("●", "\n".join(self.screen(board)))
        # Stamped after the start, as a change made later would be.
        with mock.patch("tempo.store.common.now", return_value="2099-01-01T00:00:00Z"):
            self.add(title="New")
            self.store.add_context("shop", 1, "More detail")
        board.changed()
        lines = self.screen(board)
        self.assertEqual(lines[1], "shop 1●")
        self.assertTrue(lines[6].startswith("● Q2"))
        self.assertEqual(lines[7], "  New")
        self.keys(board, "j", "enter", "h")
        self.assertNotIn("●", self.screen(board)[6])

    def test_a_long_title_takes_two_lines_and_is_cut_after_them(self):
        self.add(title="A title long enough to need more than two lines in a narrow pane here")
        lines = self.screen(self.sidebar(), width=30)
        self.assertEqual(lines[4], "  A title long enough to need")
        self.assertTrue(lines[5].startswith("  more than two") and lines[5].endswith("…"))
        self.assertEqual(lines[6], "")

    def test_the_state_column_shows_one_mark_new_first_then_waiting_then_start_here(self):
        self.add(title="Start", goal="Release", start_here="unlocks the rest")
        self.store.follow_up("demo", 1, "Why?")
        board = self.sidebar()
        # Waiting on Claude outranks start here.
        self.assertTrue(self.screen(board)[4].startswith("… Q1"))
        with mock.patch("tempo.store.common.now", return_value="2099-01-01T00:00:00Z"):
            self.store.add_context("demo", 1, "Because")
        board.changed()
        self.assertTrue(self.screen(board)[4].startswith("● Q1"))

    def test_the_selected_question_is_kept_wholly_on_screen(self):
        for number in range(1, 7):
            self.add(title="Question %d" % number)
        board = self.sidebar()
        self.keys(board, "j", "j", "j", "j", "j")
        lines = self.screen(board, height=12)
        # 12 rows: 3 on top, 1 for the keys, 8 for blocks of 3 lines each.
        self.assertIn("  Question 6", lines)
        self.assertTrue(lines[lines.index("  Question 6") - 1].startswith("  Q6"))


class LayoutTest(unittest.TestCase):
    def test_wide_screens_show_three_panels_like_lazygit(self):
        panels = layout(80, 24, "questions", projects=3)
        self.assertEqual(set(panels), {"projects", "questions", "detail"})
        self.assertEqual(panels["projects"].height + panels["questions"].height, 23)
        self.assertEqual(panels["detail"].left, panels["projects"].width)
        self.assertEqual(panels["detail"].left + panels["detail"].width, 80)

    def test_narrow_screens_show_the_focused_panel_alone(self):
        panels = layout(NARROW - 1, 24, "detail", projects=3)
        self.assertEqual(list(panels), ["detail"])
        self.assertEqual(panels["detail"].width, NARROW - 1)
        self.assertEqual(list(layout(80, 6, "projects", projects=3)), ["projects"])


try:
    import curses
    import fcntl
    import pty
    import struct
    import termios
except ImportError:
    curses = None


def _can_run_curses():
    if curses is None:
        return False
    try:
        curses.setupterm("xterm-256color", sys.stdout.fileno() if sys.stdout.isatty() else -1)
    except (curses.error, OSError, ValueError):
        return False
    return True


@unittest.skipUnless(_can_run_curses(), "needs curses, a pseudo-terminal and terminfo")
class ScreenTest(DatabaseTest):
    """The real curses screen in a pseudo-terminal (pty), as a tmux popup would run it."""

    def start(self, height=24, width=80, **settings):
        self.master, slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.resize(height, width, slave)
        env = dict(os.environ, TERM="xterm-256color")
        env.pop("NO_COLOR", None)
        env.update(settings)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tempo", "tui"],
            cwd=CODE_ROOT,
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        self.addCleanup(self.stop)
        self.screen = b""

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)

    def resize(self, height, width, fd=None):
        fcntl.ioctl(
            self.master if fd is None else fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", height, width, 0, 0),
        )

    def see(self, text, seconds=10):
        """Read what the screen draws until text shows, leaving out the terminal's control
        codes (colors, cursor moves) that come between words."""
        deadline = time.monotonic() + seconds
        while text not in re.sub(
            r"\x1b(\[[0-9;?]*[A-Za-z]|\([AB0])", "", self.screen.decode(errors="replace")
        ):
            left = deadline - time.monotonic()
            if left <= 0:
                self.fail("%r never showed; the screen drew: %r" % (text, self.screen[-2000:]))
            ready, _, _ = select.select([self.master], [], [], left)
            if ready:
                try:
                    self.screen += os.read(self.master, 65536)
                except OSError:
                    self.fail("the screen closed before %r showed" % text)

    def finish(self, seconds=10):
        """The exit code, once the screen closes; what it draws meanwhile is read, so it
        never waits on a full terminal."""
        deadline = time.monotonic() + seconds
        while self.process.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    self.screen += os.read(self.master, 65536)
                except OSError:
                    break
        return self.process.wait(timeout=max(0.1, deadline - time.monotonic()))

    def test_the_screen_answers_and_quits_with_q(self):
        self.add(title="Left or right?")
        self.start()
        self.see("Left or right?")
        self.see("Projects")
        os.write(self.master, b"b")
        self.see("Note for (b)")
        os.write(self.master, b"\r")
        self.see("Answered Q1")
        os.write(self.master, b"q")
        self.assertEqual(self.finish(), 0)
        self.assertEqual(self.store.get("demo", 1)["answer"]["choice"], "b")
        # The terminal is switched back from the full-screen view on the way out.
        self.assertIn(b"\x1b[?1049l", self.screen)

    def test_pasted_text_and_alt_keys_never_act_as_keys(self):
        self.add(title="Left or right?")
        self.add(title="Up or down?")
        self.start()
        self.see("Left or right?")
        # The screen asks the terminal to mark pastes, and turns that off on the way out.
        self.assertIn(b"\x1b[?2004h", self.screen)
        os.write(self.master, b"a")
        self.see("Note for (a)")
        os.write(self.master, b"\x1b[200~Go with a.\nbut why?\x1b[201~")
        self.see("Go with a.\u21b5but why?")
        # Option+Left, to move back a word, keeps the note.
        os.write(self.master, b"\x1bb")
        os.write(self.master, b"!")
        self.see("but why?!")
        os.write(self.master, b"\r")
        self.see("Answered Q1")
        # A terminal that doesn't mark pastes sends the newline as Enter: what comes right
        # after the follow-up is sent is dropped, rather than answering Q2 with (b).
        os.write(self.master, b"rWhy?\nbut yes")
        self.see("Added your follow-up to Q2")
        os.write(self.master, b"q")
        self.assertEqual(self.finish(), 0)
        self.assertIn(b"\x1b[?2004l", self.screen)
        self.assertEqual(self.store.get("demo", 1)["answer"]["body"], "Go with a.\nbut why?!")
        question = self.store.get("demo", 2)
        self.assertEqual((question["status"], question["entries"][-1]["body"]), ("open", "Why?"))

    def test_the_screen_follows_the_database_and_a_resize(self):
        self.start()
        self.see("No open questions")
        self.add(title="Added by Claude")
        self.see("Added by Claude")
        self.resize(24, 50)
        os.kill(self.process.pid, signal.SIGWINCH)
        self.see("2/3")
        os.write(self.master, b"\x1b")
        self.assertEqual(self.finish(), 0)

    def test_no_color_draws_without_colors(self):
        self.add(title="Left or right?", recommended="a")
        self.start(NO_COLOR="1")
        self.see("Left or right?")
        os.write(self.master, b"q")
        self.assertEqual(self.finish(), 0)
        # No text or background color codes (ESC [ 3x m, ESC [ 4x m): bold, dim and reverse
        # only, on the terminal's own colors.
        self.assertNotRegex(self.screen, rb"\x1b\[(\d+;)*[34][0-8](;\d+)*m")
        self.start()
        self.see("Left or right?")
        os.write(self.master, b"q")
        self.assertEqual(self.finish(), 0)
        self.assertRegex(self.screen, rb"\x1b\[(\d+;)*3[0-8](;\d+)*m")

    def test_an_error_inside_the_screen_still_restores_the_terminal(self):
        self.add(title="Left or right?")
        self.start()
        self.see("Left or right?")
        # The next reload fails: the screen ends with the error, the terminal put back.
        self.store.db.execute("ALTER TABLE questions RENAME TO gone")
        self.assertNotEqual(self.finish(), 0)
        restored = self.screen.rfind(b"\x1b[?1049l")
        self.assertGreater(restored, 0)
        self.assertIn(b"no such table", self.screen[restored:])


class ViewTest(DatabaseTest):
    """The screen as text, drawn by the same code that draws it in the terminal."""

    def setUp(self):
        super().setUp()
        self.board = Board(self.store, "demo")

    def screen(self, width=80, height=24):
        canvas = TextCanvas(height, width)
        cursor = draw(self.board, canvas)
        return canvas.text().split("\n"), cursor

    def test_wide_screens_show_the_three_panels_and_the_keys(self):
        self.add(title="Left or right?", goal="Release")
        self.board.reload()
        lines, cursor = self.screen()
        self.assertEqual(len(lines), 24)
        self.assertTrue(all(cell_width(line) <= 80 for line in lines))
        self.assertIn("┌─ Projects ", lines[0])
        self.assertIn("┌─ Detail ", lines[0])
        text = "\n".join(lines)
        self.assertIn("● demo", text)
        self.assertIn("│Release", text)
        self.assertIn(" Q1 Left or right?", text)
        self.assertIn("(b) Right", text)
        self.assertTrue(lines[-1].startswith("j/k question  Enter detail"))
        self.assertIn("a-b answer", lines[-1])
        self.assertIsNone(cursor)

    def test_narrow_screens_show_the_focused_panel_alone(self):
        self.add(title="Left or right?")
        self.board.reload()
        lines, _ = self.screen(width=50)
        self.assertIn("Questions 2/3 · h/l", lines[0])
        self.assertNotIn("Detail", "\n".join(lines))
        self.board.press("l")
        lines, _ = self.screen(width=50)
        self.assertIn("Detail 3/3", lines[0])

    def test_nothing_open(self):
        lines, _ = self.screen()
        text = "\n".join(lines)
        self.assertIn("Nothing open here.", text)
        self.assertIn("No open questions in demo.", text)

    def test_the_bottom_line_takes_an_optional_note(self):
        self.add(title="Left or right?")
        self.board.reload()
        self.board.press("b")
        lines, _ = self.screen()
        self.assertEqual(lines[-1], "Note for (b), optional (Enter sends, Esc cancels):")
        for key in ("O", "k"):
            self.board.press(key)
        lines, cursor = self.screen()
        self.assertEqual(lines[-1], "Note for (b), optional (Enter sends, Esc cancels): Ok")
        self.assertEqual(cursor, (23, len(lines[-1])))
        self.board.press("enter")
        lines, _ = self.screen()
        self.assertEqual(lines[-1], "Answered Q1: (b) Right / Note: Ok")

    def test_long_text_scrolls_and_says_where_it_is(self):
        self.add(context="\n\n".join("Paragraph %d." % n for n in range(30)))
        self.board.reload()
        lines, _ = self.screen(height=12)
        self.assertIn(" 1-9 of ", lines[10])
        self.board.press("pgdn")
        lines, _ = self.screen(height=12)
        # A page is the panel's height less one line, kept for context.
        self.assertIn(" 9-17 of ", lines[10])
        for _ in range(20):
            self.board.press("ctrl-d")
            self.screen(height=12)
        lines, _ = self.screen(height=12)
        total = int(lines[10].split(" of ")[1].split()[0])
        # Scrolled no further than the last page.
        self.assertIn(" %d-%d of %d " % (total - 8, total, total), lines[10])

    def test_help_lists_the_keys(self):
        self.board.press("?")
        text = "\n".join(self.screen()[0])
        self.assertIn("─ Help ", text)
        self.assertIn("Shift is needed for h j k l q r s", text)
        self.board.press("x")
        self.assertNotIn("─ Help ", "\n".join(self.screen()[0]))

    def test_settings_lists_projects_with_their_folders_and_asks_before_merging(self):
        self.add()
        self.add(project="shop")
        self.store.see_folder("shop", "/work/shop")
        self.board.press("s")
        lines, cursor = self.screen()
        text = "\n".join(lines)
        self.assertIn("─ Settings · Projects ", lines[0])
        self.assertIn("demo", text)
        self.assertIn("1 question", text)
        self.assertIn("    shop  /work/shop", text)
        self.assertTrue(lines[-1].startswith("j/k project  m merge into…  Esc back"))
        self.assertIsNone(cursor)
        self.board.press("m")
        lines, _ = self.screen()
        self.assertIn("─ Settings · Merge demo into… ", lines[0])
        self.board.press("enter")
        lines, _ = self.screen()
        self.assertEqual(lines[-1], "Merge demo into shop? y merges, n cancels")

    def test_wide_characters_fill_two_cells(self):
        canvas = TextCanvas(1, 6)
        canvas.put(0, 0, "数据库表")
        self.assertEqual(canvas.text(), "数据库")


@unittest.skipUnless(curses is not None, "needs curses")
class KeyNameTest(unittest.TestCase):
    def test_keys_get_the_names_the_board_reads(self):
        from tempo.tui.screen import key_name

        self.assertEqual(key_name("j"), "j")
        self.assertEqual(key_name("é"), "é")
        self.assertEqual(key_name("\n"), "enter")
        self.assertEqual(key_name("\x1b"), "esc")
        self.assertEqual(key_name("\x04"), "ctrl-d")
        self.assertEqual(key_name(curses.KEY_BTAB), "btab")
        self.assertEqual(key_name(curses.KEY_NPAGE), "pgdn")
        self.assertIsNone(key_name("\x01"))
        self.assertIsNone(key_name(curses.KEY_F1))


class ReadInputTest(unittest.TestCase):
    """Keys as the screen reads them: Esc, Alt and a key, escape sequences, and pastes."""

    def read(self, first, *after):
        from tempo.tui.screen import read_input

        waiting = list(after)
        return read_input(first, lambda wait: waiting.pop(0) if waiting else None)

    def test_esc_alone_is_esc_and_other_keys_keep_their_names(self):
        self.assertEqual(self.read("\x1b"), ("key", "esc"))
        self.assertEqual(self.read("j"), ("key", "j"))
        self.assertEqual(self.read("\n"), ("key", "enter"))
        self.assertIsNone(self.read("\x01"))

    def test_alt_and_a_key_is_ignored_as_one_key(self):
        # Option+Left and Option+Right on a Mac send Esc b and Esc f.
        self.assertIsNone(self.read("\x1b", "b"))
        self.assertIsNone(self.read("\x1b", "q"))
        # An escape sequence curses doesn't know, such as Ctrl+Right, is ignored whole.
        self.assertIsNone(self.read("\x1b", *"[1;5C"))
        self.assertIsNone(self.read("\x1b", *"OP"))

    def test_a_paste_is_text_newlines_and_all(self):
        paste = ["[", "2", "0", "0", "~", *"Go with a.\r\nbut why?", *"\x1b[201~"]
        self.assertEqual(self.read("\x1b", *paste), ("paste", "Go with a.\nbut why?"))
        # A paste whose end mark never comes ends with what arrived.
        self.assertEqual(self.read("\x1b", *"[200~half"), ("paste", "half"))


class CommandTest(DatabaseTest):
    def test_tui_needs_a_terminal(self):
        err = io.StringIO()
        with mock.patch.object(sys.stdin, "isatty", return_value=False), redirect_stderr(err):
            self.assertEqual(cli.main(["tui"]), 1)
        self.assertEqual(err.getvalue(), "tempo: tui needs a terminal\n")

    @unittest.skipUnless(curses is not None, "needs curses")
    def test_tui_starts_on_the_folders_project_and_never_starts_the_page(self):
        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(sys.stdout, "isatty", return_value=True),
            mock.patch("tempo.tui.screen.run", return_value=0) as run,
            mock.patch.object(cli, "start_server") as start,
        ):
            self.assertEqual(cli.main(["tui"]), 0)
            self.assertEqual(cli.main(["tui", "--project", "shop"]), 0)
            self.assertEqual(cli.main(["tui", "--sidebar"]), 0)
            self.assertEqual(cli.main(["tui", "--question", "3"]), 0)
        # TEMPO_PROJECT is set to demo, as `tempo list` would read it.
        self.assertEqual(
            [call.args for call in run.call_args_list],
            [("demo",), ("shop",), ("demo",), ("demo",)],
        )
        self.assertEqual(
            [(call.kwargs["sidebar"], call.kwargs["question"]) for call in run.call_args_list],
            [(False, None), (False, None), (True, None), (False, 3)],
        )
        start.assert_not_called()


class PopupTest(BoardTest):
    """Inside tmux, the sidebar opens a question in a popup that shows only it."""

    def test_enter_opens_the_question_elsewhere_and_counts_it_as_seen(self):
        self.add(title="One")
        opened = []

        def opener(project, number):
            opened.append((project, number))
            return True

        board = Board(self.board_store, "demo", sidebar=True, opener=opener)
        board.press("enter")
        self.assertEqual(opened, [("demo", 1)])
        self.assertEqual(board.focus, "questions")

    def test_without_a_popup_the_question_opens_over_the_list(self):
        self.add(title="One")
        board = Board(self.board_store, "demo", sidebar=True, opener=lambda *q: False)
        board.press("enter")
        self.assertEqual(board.focus, "detail")

    def test_one_question_closes_once_answered_or_on_escape(self):
        self.add(title="One")
        self.add(title="Two")
        board = Board(self.board_store, "demo", question=2)
        self.assertEqual((board.focus, board.question["title"]), ("detail", "Two"))
        self.assertTrue(board.press("a"))
        self.assertFalse(board.press("enter"))
        self.assertEqual(self.store.get("demo", 2)["status"], "answered")
        board = Board(self.board_store, "demo", question=1)
        self.assertFalse(board.press("esc"))
        self.assertEqual(self.store.get("demo", 1)["status"], "open")

    def test_one_question_closes_when_it_is_answered_elsewhere(self):
        self.add(title="One")
        board = Board(self.board_store, "demo", question=1)
        self.assertFalse(board.done)
        self.store.answer("demo", 1, "a")
        board.changed()
        self.assertTrue(board.done)
        self.assertTrue(Board(self.board_store, "demo", question=7).done)

    @unittest.skipUnless(curses is not None, "needs curses")
    def test_the_popup_runs_this_tempo_on_this_database(self):
        from tempo.tui import screen

        command = screen.popup_command("my shop", 4)
        self.assertEqual(command[:2], ["tmux", "display-popup"])
        self.assertIn("-E", command)
        self.assertIn("TEMPO_DB=%s" % os.environ["TEMPO_DB"], command)
        self.assertEqual(
            command[-1],
            "%s tui --project 'my shop' --question 4" % os.path.join(CODE_ROOT, "bin", "tempo"),
        )
        popups = screen.Popups()
        with mock.patch("subprocess.Popen", side_effect=OSError):
            self.assertFalse(popups.open("demo", 1))
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.poll.return_value = 0
            self.assertTrue(popups.open("demo", 1))
            self.assertEqual(popen.call_args.args[0], screen.popup_command("demo", 1))
            popups.collect()
        self.assertEqual(popups.running, [])


class JumpTest(BoardTest):
    """g in the sidebar goes to the pane where the shown project's Claude session runs."""

    def test_g_jumps_with_the_projects_folders_and_says_what_went_wrong(self):
        self.store.see_folder("shop", "/work/shop")
        self.store.see_folder("demo", "/work/demo")
        self.add(project="shop", title="There")
        self.add(title="Here")
        asked = []

        def jumper(project, paths):
            asked.append((project, paths))
            return None if project == "shop" else "No Claude session open for %s." % project

        board = Board(self.board_store, "shop", sidebar=True, jumper=jumper)
        board.press("g")
        self.assertEqual((asked, board.message), ([("shop", ["/work/shop"])], None))
        # h and l only browse; g goes.
        board.press("l")
        self.assertEqual(len(asked), 1)
        board.press("g")
        self.assertEqual(board.message, ("No Claude session open for demo.", "error"))
        self.assertEqual(self.store.get("shop", 1)["status"], "open")

    def test_outside_tmux_g_says_so_and_shift_g_still_answers(self):
        self.add(title="One", options=list("abcdefg"))
        board = Board(self.board_store, "demo", sidebar=True)
        board.press("g")
        self.assertEqual(board.message, ("g works only inside tmux.", "error"))
        board.press("G")
        self.assertEqual(board.mode, "note")

    @unittest.skipUnless(curses is not None, "needs curses")
    def test_the_claude_pane_is_found_in_the_projects_folders(self):
        from tempo.tui import screen

        listing = "\n".join(
            [
                "%1\tshop\t@1\t100\t2.1.311\t/work/shop",
                "%2\tshop\t@1\t100\tzsh\t/work/shop",
                "%3\tshop\t@2\t300\tclaude\t/work/shop/.claude/worktrees/x",
                "%4\tother\t@3\t900\t2.1.311\t/work/shopping",
                "%5\tshop\t@4\t999\t2.1.311\t/work/shop",
            ]
        )
        # The newest Claude pane inside a folder, never the sidebar's own (%5).
        self.assertEqual(screen.claude_pane(listing, ["/work/shop"], "%5"), ("shop", "@2", "%3"))
        self.assertIsNone(screen.claude_pane(listing, ["/work/none"], None))
        self.assertIsNone(screen.claude_pane(listing, [], None))

    @unittest.skipUnless(curses is not None, "needs curses")
    def test_jump_switches_to_the_pane_or_says_none_is_open(self):
        from tempo.tui import screen

        calls = []

        def tmux(command, **_):
            calls.append(command[1:])
            out = "%3\tshop\t@2\t1\t2.1.311\t/work/shop\n" if command[1] == "list-panes" else ""
            return subprocess.CompletedProcess(command, 0, out, "")

        with mock.patch("subprocess.run", side_effect=tmux):
            self.assertIsNone(screen.jump("shop", ["/work/shop"]))
            self.assertEqual(
                calls[1:],
                [["switch-client", "-t", "shop"], ["select-window", "-t", "@2"],
                 ["select-pane", "-t", "%3"]],
            )  # fmt: skip
            self.assertEqual(
                screen.jump("demo", ["/work/demo"]), "No Claude session open for demo."
            )
        failure = subprocess.CalledProcessError(1, ["tmux"], "", "no server running\n")
        with mock.patch("subprocess.run", side_effect=failure):
            self.assertEqual(screen.jump("shop", ["/work/shop"]), "tmux failed: no server running")


class FakeTmux:
    """A tmux server for `tempo tmux`: windows with their panes, server options and hooks."""

    def __init__(self, windows, on="", hooks=None):
        self.windows = {window: list(panes) for window, panes in windows.items()}
        self.marked = set()
        self.on = on
        self.hooks = hooks or {}
        self.calls = []
        self.next = 90

    def __call__(self, command, **_):
        args = command[1:]
        self.calls.append(args)
        return subprocess.CompletedProcess(command, 0, self.answer(args), "")

    def answer(self, args):
        name = args[0]
        if name == "show-options":
            return self.on + "\n" if self.on else ""
        if name == "set-option" and args[1] == "-g":
            self.on = args[3]
        elif name == "set-option" and args[1] == "-gu":
            self.on = ""
        elif name == "set-option" and args[1] == "-p":
            self.marked.add(args[3])
        elif name == "list-windows":
            return "".join(window + "\n" for window in self.windows)
        elif name == "list-panes" and args[1] == "-a":
            panes = [pane for panes in self.windows.values() for pane in panes]
            return "".join("%s\t%s\n" % (p, "1" if p in self.marked else "") for p in panes)
        elif name == "list-panes":
            return "".join("1\n" if p in self.marked else "\n" for p in self.windows[args[2]])
        elif name == "display-message":
            return "/work/%s\n" % args[3].strip("@")
        elif name == "split-window":
            self.next += 1
            pane = "%%%d" % self.next
            self.windows[args[2]].append(pane)
            return pane + "\n"
        elif name == "kill-pane":
            for panes in self.windows.values():
                if args[2] in panes:
                    panes.remove(args[2])
            self.marked.discard(args[2])
        elif name == "show-hooks":
            return "".join(
                "%s[%d] %s\n" % (args[2], i, c) for i, c in self.hooks.get(args[2], {}).items()
            )
        elif name == "set-hook" and args[1] == "-ag":
            hooks = self.hooks.setdefault(args[2], {})
            hooks[max(hooks, default=-1) + 1] = args[3]
        elif name == "set-hook" and args[1] == "-gu":
            hook, index = args[2].rstrip("]").split("[")
            del self.hooks[hook][int(index)]
        return ""

    def sidebars(self):
        return {w: [p for p in panes if p in self.marked] for w, panes in self.windows.items()}

    def splits(self):
        return [args for args in self.calls if args[0] == "split-window"]


class TmuxTest(DatabaseTest):
    """`tempo tmux` turns the sidebar on or off in every tmux window, as workmux does."""

    def run_tmux(self, tmux, *args, env=None):
        env = {"TMUX": "/tmp/tmux-501/default,1,0", "TMUX_PANE": "%1", **(env or {})}
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, env),
            mock.patch("subprocess.run", side_effect=tmux),
            redirect_stderr(err),
        ):
            code = cli.main(["tmux", *args])
        return code, err.getvalue()

    def test_on_every_window_gets_a_full_height_sidebar_on_the_left_with_its_project(self):
        tmux = FakeTmux({"@1": ["%1"], "@2": ["%2", "%3"]})
        self.assertEqual(self.run_tmux(tmux)[0], 0)
        self.assertEqual(tmux.on, "left")
        self.assertEqual(tmux.sidebars(), {"@1": ["%91"], "@2": ["%92"]})
        split = tmux.splits()[1]
        self.assertEqual(split[:7], ["split-window", "-t", "@2", "-hbfd", "-l", "40", "-c"])
        self.assertIn("TEMPO_DB=%s" % os.environ["TEMPO_DB"], split)
        tempo = os.path.join(CODE_ROOT, "bin", "tempo")
        self.assertEqual(split[-1], "%s tui --sidebar --project 2" % tempo)
        # New windows and sessions get one through tmux's hooks.
        for hook in cli.SIDEBAR_HOOKS:
            (command,) = tmux.hooks[hook].values()
            self.assertTrue(command.startswith("run-shell -b 'env TEMPO_DB="))
            self.assertTrue(command.endswith(" tmux --window #{window_id}'"))

    def test_a_new_window_gets_one_while_it_is_on_and_only_one(self):
        tmux = FakeTmux({"@1": ["%1"]}, on="left")
        self.assertEqual(self.run_tmux(tmux, "--window", "@1", env={"TMUX": ""})[0], 0)
        self.assertEqual(tmux.sidebars(), {"@1": ["%91"]})
        self.run_tmux(tmux, "--window", "@1")
        self.assertEqual(len(tmux.splits()), 1)
        # Off, the hook does nothing.
        off = FakeTmux({"@1": ["%1"]})
        self.run_tmux(off, "--window", "@1")
        self.assertEqual(off.splits(), [])

    def test_run_again_every_sidebar_and_only_our_hooks_go(self):
        tmux = FakeTmux(
            {"@1": ["%1"], "@2": ["%2"]}, hooks={"after-new-window": {0: "run-shell mine"}}
        )
        self.run_tmux(tmux)
        self.run_tmux(tmux)
        self.assertEqual(tmux.on, "")
        self.assertEqual(tmux.sidebars(), {"@1": [], "@2": []})
        self.assertEqual(tmux.hooks["after-new-window"], {0: "run-shell mine"})
        self.assertEqual(tmux.hooks["after-new-session"], {})

    def test_the_side_is_remembered_and_switching_it_moves_every_sidebar(self):
        tmux = FakeTmux({"@1": ["%1"]})
        self.run_tmux(tmux, "--side", "right")
        self.assertEqual(tmux.splits()[0][3], "-hfd")
        self.run_tmux(tmux, "--side", "left")
        self.assertEqual((tmux.on, tmux.sidebars()), ("left", {"@1": ["%92"]}))
        self.run_tmux(tmux)
        self.run_tmux(tmux)
        # Plain `tempo tmux` keeps the side last chosen.
        self.assertEqual(tmux.splits()[-1][3], "-hbfd")
        with Store(connect()) as store:
            self.assertEqual(store.tmux_side(), "left")

    def test_outside_tmux_it_says_so(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMUX", None)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(cli.main(["tmux"]), 1)
        self.assertIn("only inside tmux", err.getvalue())

    def test_a_tmux_failure_is_reported(self):
        failure = subprocess.CalledProcessError(1, ["tmux"], "", "no server running\n")
        with (
            mock.patch.dict(os.environ, {"TMUX": "x", "TMUX_PANE": "%1"}),
            mock.patch("subprocess.run", side_effect=failure),
            redirect_stderr(io.StringIO()) as err,
        ):
            self.assertEqual(cli.main(["tmux"]), 1)
        self.assertEqual(err.getvalue(), "tempo: tmux failed: no server running\n")
