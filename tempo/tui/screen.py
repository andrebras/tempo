"""The curses side of `tempo tui`: open the terminal, draw the Board (view.py) and pass
keys to it, and check about twice a second whether the database changed. curses.wrapper
puts the terminal back as it was when the screen closes, even after an error.

It never starts the page or opens a port: it only reads and writes the database.
"""

from __future__ import annotations

import contextlib
import curses
import locale
import os
import time
from typing import Any

from tempo.store import Store, connect
from tempo.tui.model import Board
from tempo.tui.view import draw

# How long to wait for a key before checking the database, in milliseconds.
TICK = 500

# Key names the Board understands, from curses' key codes.
KEYS = {
    curses.KEY_UP: "up",
    curses.KEY_DOWN: "down",
    curses.KEY_LEFT: "left",
    curses.KEY_RIGHT: "right",
    curses.KEY_BTAB: "btab",
    curses.KEY_NPAGE: "pgdn",
    curses.KEY_PPAGE: "pgup",
    curses.KEY_ENTER: "enter",
    curses.KEY_BACKSPACE: "backspace",
    curses.KEY_RESIZE: "resize",
}
CHARACTERS = {
    "\n": "enter",
    "\r": "enter",
    "\t": "tab",
    "\x1b": "esc",
    "\x7f": "backspace",
    "\b": "backspace",
    "\x15": "ctrl-u",
    "\x04": "ctrl-d",
    "\x03": "ctrl-c",
}


def key_name(key: int | str) -> str | None:
    """The Board's name for a key from get_wch(), or None for a key it doesn't use."""
    if isinstance(key, int):
        return KEYS.get(key)
    if key in CHARACTERS:
        return CHARACTERS[key]
    return key if key.isprintable() else None


class CursesCanvas:
    """The terminal as a canvas for view.draw."""

    def __init__(self, window: Any, styles: dict[str, int]) -> None:
        self.window = window
        self.styles = styles

    def size(self) -> tuple[int, int]:
        height, width = self.window.getmaxyx()
        return int(height), int(width)

    def put(self, y: int, x: int, text: str, style: str = "") -> None:
        # Writing the bottom-right cell moves the cursor past the end, which curses reports
        # as an error after drawing; what can't be drawn is simply left out.
        with contextlib.suppress(curses.error):
            self.window.addstr(y, x, text, self.styles.get(style, curses.A_NORMAL))


def styles(color: bool) -> dict[str, int]:
    """How each style name looks: in color when the terminal has it and NO_COLOR is unset,
    else with bold, dim, underline and reverse only."""
    pair = {"accent": 0, "green": 0, "red": 0, "yellow": 0, "magenta": 0}
    if color:
        curses.start_color()
        with contextlib.suppress(curses.error):
            curses.use_default_colors()
        colors = (
            curses.COLOR_CYAN,
            curses.COLOR_GREEN,
            curses.COLOR_RED,
            curses.COLOR_YELLOW,
            curses.COLOR_MAGENTA,
        )
        for number, (name, value) in enumerate(zip(pair, colors), start=1):
            curses.init_pair(number, value, -1)
            pair[name] = curses.color_pair(number)
    bold, dim, under = curses.A_BOLD, curses.A_DIM, curses.A_UNDERLINE
    return {
        "": curses.A_NORMAL,
        "bold": bold,
        "dim": dim,
        "title": bold,
        "heading": bold | pair["magenta"],
        "accent": pair["accent"] or bold,
        "letter": bold | pair["accent"],
        "rec": bold | (pair["green"] or under),
        "code": pair["yellow"] or under,
        "dot": pair["yellow"] or bold,
        "key": bold | pair["accent"],
        "prompt": bold,
        "ok": pair["green"] or bold,
        "error": bold | pair["red"],
        "border": dim,
        "border-focus": bold | pair["accent"],
        "selected": curses.A_REVERSE,
        "current": bold,
    }


def run(project: str) -> int:
    """Show the screen on the terminal until the user quits; Ctrl-C quits too."""
    # Without the user's locale, curses draws box lines and accents as garbage.
    locale.setlocale(locale.LC_ALL, "")
    with Store(connect()) as store:
        board = Board(store, project)
        with contextlib.suppress(KeyboardInterrupt):
            curses.wrapper(_loop, board)
    return 0


def _loop(window: Any, board: Board) -> None:
    # Esc normally waits a second for the rest of a key sequence; 25 ms is enough.
    with contextlib.suppress(AttributeError, curses.error):
        curses.set_escdelay(25)
    color = curses.has_colors() and not os.environ.get("NO_COLOR")
    canvas = CursesCanvas(window, styles(color))
    window.timeout(TICK)
    checked = time.monotonic()
    while True:
        window.erase()
        cursor = draw(board, canvas)
        with contextlib.suppress(curses.error):
            curses.curs_set(1 if cursor else 0)
            if cursor:
                window.move(*cursor)
        window.refresh()
        try:
            key = window.get_wch()
        except curses.error:
            # No key within TICK.
            key = None
        if key is None or time.monotonic() - checked >= TICK / 1000:
            board.changed()
            checked = time.monotonic()
        name = key_name(key) if key is not None else None
        if name and name != "resize" and not board.press(name):
            return
