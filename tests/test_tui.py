import io
import os
import re
import select
import signal
import subprocess
import sys
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

    def test_answering_with_a_letter_after_confirming_reaches_claude_like_the_page(self):
        self.add(title="Left or right?")
        board = self.board()
        board.press("b")
        self.assertEqual(board.mode, "confirm")
        self.assertEqual(self.store.get("demo", 1)["status"], "open")
        self.assertEqual(board.option, "Right")
        board.press("y")
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
        self.keys(board, "a", "e", *"Nice", "x", "backspace", "enter")
        self.assertEqual(board.message, ("Answered Q1: (a) Left / Note: Nice", "ok"))
        self.assertEqual(self.store.get("demo", 1)["answer"]["body"], "Nice")

    def test_no_or_escape_leaves_the_question_open(self):
        self.add()
        board = self.board()
        self.keys(board, "a", "n", "b", "esc", "a", "e", "esc")
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
        # Answered on the page while the confirm was showing.
        self.store.answer("demo", 1, "b")
        board.press("y")
        self.assertEqual(
            board.message,
            ("This question is already answered; reopen it to answer again", "error"),
        )
        board.press("r")
        self.assertEqual(board.message, ("No question selected.", "error"))

    def test_a_reload_while_confirming_keeps_the_answer_on_its_question(self):
        self.add()
        self.add()
        board = self.board()
        board.press("a")
        self.assertEqual(board.target, ("demo", 1))
        # Answered on the page meanwhile: the screen moves on to Q2 as it reloads.
        self.store.answer("demo", 1, "b")
        self.assertTrue(board.changed())
        self.assertEqual(board.number, 2)
        board.press("y")
        self.assertEqual(board.message[1], "error")
        self.assertEqual(self.store.get("demo", 2)["status"], "open")

    def test_a_reword_while_confirming_refuses_the_answer(self):
        self.add(options=["Keep the old API", "Delete everything"])
        board = self.board()
        board.press("a")
        # Claude swaps the options while the prompt still shows (a) Keep the old API.
        self.store.reword("demo", 1, "Swapped", options=["Delete everything", "Keep the old API"])
        self.assertTrue(board.changed())
        board.press("y")
        self.assertEqual(
            board.message,
            ("Q1 changed since you looked at it (now version 2); check it again", "error"),
        )
        self.assertEqual(self.store.get("demo", 1)["status"], "open")
        # Picked again, on the wording now shown, the answer goes through.
        self.keys(board, "b", "y")
        self.assertEqual(self.store.get("demo", 1)["answer"]["choice"], "b")

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
        self.assertEqual((board.mode, board.letter), ("confirm", "h"))

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
        self.see("Answer Q1 with (b) Right?")
        os.write(self.master, b"y")
        self.see("Answered Q1")
        os.write(self.master, b"q")
        self.assertEqual(self.finish(), 0)
        self.assertEqual(self.store.get("demo", 1)["answer"]["choice"], "b")
        # The terminal is switched back from the full-screen view on the way out.
        self.assertIn(b"\x1b[?1049l", self.screen)

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

    def test_the_bottom_line_asks_to_confirm_then_takes_a_note(self):
        self.add(title="Left or right?")
        self.board.reload()
        self.board.press("b")
        lines, _ = self.screen()
        self.assertEqual(lines[-1], "Answer Q1 with (b) Right?  y send  e add a note  n cancel")
        for key in ("e", "O", "k"):
            self.board.press(key)
        lines, cursor = self.screen()
        self.assertEqual(lines[-1], "Note for (b) (Enter sends, Esc cancels): Ok")
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
        self.assertIn("Shift is needed for h j k l q r", text)
        self.board.press("x")
        self.assertNotIn("─ Help ", "\n".join(self.screen()[0]))

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
        # TEMPO_PROJECT is set to demo, as `tempo list` would read it.
        self.assertEqual([call.args for call in run.call_args_list], [("demo",), ("shop",)])
        start.assert_not_called()
