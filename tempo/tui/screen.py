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
import sys
import time
from collections.abc import Callable
from typing import Any, Union

from tempo.store import Store, connect
from tempo.tui.model import Board
from tempo.tui.view import draw

# How long to wait for a key before checking the database, in milliseconds.
TICK = 500
# How long to wait for the rest of an escape sequence, and for more of a paste, in
# milliseconds. Both arrive at once from a local terminal; these leave room for ssh.
SEQUENCE_WAIT = 50
PASTE_WAIT = 1000
# Bracketed paste: the terminal marks pasted text, so the screen can tell it from typing.
PASTE_ON, PASTE_OFF = "\x1b[?2004h", "\x1b[?2004l"
PASTE_START, PASTE_END = "[200~", "\x1b[201~"

# A key as get_wch() gives it: a character, or a curses key code.
Key = Union[int, str]

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


def read_input(first: Key, more: Callable[[int], Key | None]) -> tuple[str, str] | None:
    """What the user did, from the key get_wch() gave (first) and the keys right after it
    (more(ms) gives the next within ms milliseconds, or None): ("key", name) for a key the
    Board reads, ("paste", text) for pasted text, or None for something to ignore.

    An Esc with nothing after it is Esc. An Esc with a key right after it is Alt (Option
    on a Mac) and that key, such as Option+Left (Esc b): it is ignored as one key, so it
    can't cancel the text being typed, quit or pick an option. An escape sequence curses
    doesn't know is ignored whole, rather than read as Esc and then letters.
    """
    if first != "\x1b":
        name = key_name(first)
        return ("key", name) if name else None
    after = more(0)
    if after is None:
        return ("key", "esc")
    if after == "O":
        # An F1-F4 or keypad key's sequence: Esc O and one more.
        more(0)
        return None
    if after != "[":
        return None
    sequence = "["
    while True:
        char = more(SEQUENCE_WAIT)
        if not isinstance(char, str):
            break
        sequence += char
        # A control sequence ends with a character from @ to ~.
        if "@" <= char <= "~":
            break
    return ("paste", _pasted(more)) if sequence == PASTE_START else None


def _pasted(more: Callable[[int], Key | None]) -> str:
    """The text of a paste, read up to the terminal's end mark. Should the mark never come,
    the paste ends when no more arrives."""
    text = ""
    while not text.endswith(PASTE_END):
        char = more(PASTE_WAIT)
        if char is None:
            break
        if char == curses.KEY_ENTER:
            char = "\n"
        if isinstance(char, str):
            text += char
    return text.removesuffix(PASTE_END).replace("\r\n", "\n").replace("\r", "\n")


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
    if curses.has_colors():
        try:
            # curses.wrapper has started colors, which would paint the screen white on
            # black. This keeps the terminal's own text and background colors, with or
            # without colors of ours.
            curses.use_default_colors()
        except curses.error:
            color = False
    if color:
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
    _terminal(PASTE_ON)
    try:
        _keys(window, board)
    finally:
        _terminal(PASTE_OFF)


def _terminal(code: str) -> None:
    """Send the terminal a control code, such as turning bracketed paste on."""
    sys.stdout.write(code)
    sys.stdout.flush()


def _read(window: Any, wait: int) -> Key | None:
    """The next key within wait milliseconds, or None."""
    window.timeout(wait)
    try:
        key: Key = window.get_wch()
        return key
    except curses.error:
        return None
    finally:
        window.timeout(TICK)


def _keys(window: Any, board: Board) -> None:
    """Draw the board and act on what the user does, until a key quits."""
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
        # None when no key came within TICK.
        key = _read(window, TICK)
        if key is None or time.monotonic() - checked >= TICK / 1000:
            board.changed()
            checked = time.monotonic()
        happened = read_input(key, lambda wait: _read(window, wait)) if key is not None else None
        if happened is None:
            continue
        kind, value = happened
        typing = board.mode in ("note", "reply")
        if kind == "paste":
            board.paste(value)
        elif value != "resize" and not board.press(value):
            return
        if typing and board.mode == "normal":
            # A note or follow-up was just sent (or cancelled). Keys already waiting are
            # the rest of a paste from a terminal without bracketed paste, not typing:
            # dropped, so they can't pick an option on the next question.
            while _read(window, 0) is not None:
                pass
