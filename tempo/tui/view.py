"""How the Board looks: the panels with their borders, the rows in each, the bottom line
(key hints, a prompt, or what just happened) and the help overlay, drawn onto a canvas.

A canvas only needs its size and a way to put styled text at a place. The curses screen
is one (screen.py); TextCanvas, a grid of characters, is another, so tests and the
README's capture see exactly what the terminal shows, without a terminal.
"""

from __future__ import annotations

from typing import Protocol

from tempo.tui.model import Board, Rect, detail_lines, layout, waiting_on_claude
from tempo.tui.text import Line, cell_width, clip

TITLES = {"projects": "Projects", "questions": "Questions", "detail": "Detail"}
HINTS = {
    "projects": [("j/k", "project"), ("l", "questions")],
    "questions": [("j/k", "question"), ("Enter", "detail"), ("h/l", "panels")],
    "detail": [("j/k", "scroll"), ("PgUp/PgDn", "page"), ("h", "back")],
}
HELP = [
    ("j k ↑ ↓", "move; in Detail, scroll"),
    ("h l ← →", "previous or next panel"),
    ("Tab S-Tab", "next or previous panel"),
    ("Enter", "open the questions, then the detail"),
    ("a b c …", "answer with that option (asks y/n)"),
    ("A B C …", "the same; Shift is needed for h j k l q r"),
    ("r", "ask Claude for more (a follow-up)"),
    ("PgUp PgDn", "scroll the detail a page (or Ctrl-u Ctrl-d)"),
    ("?", "this help"),
    ("q Esc", "quit"),
]


class Canvas(Protocol):
    def size(self) -> tuple[int, int]:
        """The height and width, in lines and columns."""

    def put(self, y: int, x: int, text: str, style: str = "") -> None:
        """Write text at line y, column x; what falls outside is cut off."""


class TextCanvas:
    """A canvas that is just rows of characters, for tests and the README's capture."""

    def __init__(self, height: int, width: int) -> None:
        self.height, self.width = height, width
        self.cells = [[" "] * width for _ in range(height)]

    def size(self) -> tuple[int, int]:
        return self.height, self.width

    def put(self, y: int, x: int, text: str, style: str = "") -> None:
        if not 0 <= y < self.height:
            return
        for char in text:
            size = cell_width(char)
            if x + size > self.width:
                break
            if x >= 0:
                self.cells[y][x] = char
                # A wide character fills the next cell too.
                if size == 2:
                    self.cells[y][x + 1] = ""
            x += size

    def text(self) -> str:
        return "\n".join("".join(row).rstrip() for row in self.cells)


def draw(board: Board, canvas: Canvas) -> tuple[int, int] | None:
    """Draw the whole screen; returns where the cursor goes while typing, else None."""
    height, width = canvas.size()
    panels = layout(width, height, board.focus, len(board.projects))
    for name, rect in panels.items():
        title = TITLES[name]
        if len(panels) == 1:
            # One panel at a time: say which, and that h and l reach the others.
            title += " %d/3 · h/l" % (list(TITLES).index(name) + 1)
        _box(canvas, rect, title, name == board.focus)
        inner = Rect(rect.top + 1, rect.left + 1, rect.height - 2, rect.width - 2)
        if inner.height > 0 and inner.width > 0:
            {"projects": _projects, "questions": _questions, "detail": _detail}[name](
                board, canvas, inner, name == board.focus
            )
    cursor = _bottom(board, canvas, height - 1, width)
    if board.mode == "help":
        _help(canvas, height, width)
    return cursor


def _box(canvas: Canvas, rect: Rect, title: str, focused: bool) -> None:
    style = "border-focus" if focused else "border"
    top, left, height, width = rect
    if height < 2 or width < 2:
        return
    canvas.put(top, left, "┌" + "─" * (width - 2) + "┐", style)
    canvas.put(top, left + 2, clip(" %s " % title, width - 4), "title" if focused else style)
    for y in range(top + 1, top + height - 1):
        canvas.put(y, left, "│", style)
        canvas.put(y, left + width - 1, "│", style)
    canvas.put(top + height - 1, left, "└" + "─" * (width - 2) + "┘", style)


def _row(canvas: Canvas, y: int, rect: Rect, text: str, style: str, end: str = "") -> None:
    """One line of a list, cut to fit, with end (such as a count) at the right edge."""
    room = rect.width - (cell_width(end) + 1 if end else 0)
    line = clip(text, room)
    line += " " * (room - cell_width(line))
    canvas.put(y, rect.left, line + (" " + end if end else ""), style)


def _first_shown(selected: int, count: int, height: int) -> int:
    """The first row to show, so the selected row is on screen."""
    return max(0, min(selected - height + 1, count - height)) if selected >= height else 0


def _projects(board: Board, canvas: Canvas, rect: Rect, focused: bool) -> None:
    names = [name for name, _ in board.projects]
    start = _first_shown(names.index(board.project), len(names), rect.height)
    for y, (name, count) in enumerate(board.projects[start : start + rect.height]):
        # A dot marks a project with questions waiting on the user.
        text = "%s %s" % ("●" if count else " ", name)
        style = "dot" if count else "dim"
        if name == board.project:
            style = "selected" if focused else "current"
        _row(canvas, rect.top + y, rect, text, style, str(count))


def _questions(board: Board, canvas: Canvas, rect: Rect, focused: bool) -> None:
    rows = board.rows()
    if not rows:
        canvas.put(rect.top, rect.left, clip("Nothing open here.", rect.width), "dim")
        return
    selected = next(
        (
            i
            for i, row in enumerate(rows)
            if row.question and row.question["number"] == board.number
        ),
        0,
    )
    start = _first_shown(selected, len(rows), rect.height)
    for y, row in enumerate(rows[start : start + rect.height]):
        question = row.question
        if question is None:
            _row(canvas, rect.top + y, rect, str(row.goal), "heading")
            continue
        # » marks the goal's start-here question, … one waiting on Claude.
        mark = "»" if question["start_here"] else "…" if waiting_on_claude(question) else " "
        text = "%s%s %s" % (mark, question["label"], question["title"])
        style = ""
        if question["number"] == board.number:
            style = "selected" if focused else "current"
        _row(canvas, rect.top + y, rect, text, style, "quick" if question["quick"] else "")


def _detail(board: Board, canvas: Canvas, rect: Rect, focused: bool) -> None:
    if board.question is None:
        text = "No open questions in %s." % board.project
        canvas.put(rect.top, rect.left, clip(text, rect.width), "dim")
        return
    lines = detail_lines(board.question, rect.width)
    board.page = max(1, rect.height - 1)
    board.scroll = min(board.scroll, max(0, len(lines) - rect.height))
    for y, line in enumerate(lines[board.scroll : board.scroll + rect.height]):
        _spans(canvas, rect.top + y, rect.left, line, rect.width)
    if len(lines) > rect.height:
        # How far down the text is, on the bottom border.
        where = " %d-%d of %d " % (
            board.scroll + 1,
            min(len(lines), board.scroll + rect.height),
            len(lines),
        )
        x = rect.left + rect.width - cell_width(where)
        canvas.put(rect.top + rect.height, x, where, "border-focus" if focused else "border")


def _spans(canvas: Canvas, y: int, x: int, line: Line, width: int) -> None:
    end = x + width
    for text, style in line:
        piece = clip(text, end - x)
        canvas.put(y, x, piece, style)
        x += cell_width(piece)


def _bottom(board: Board, canvas: Canvas, y: int, width: int) -> tuple[int, int] | None:
    """The last line: the prompt being answered, else what just happened, else the keys."""
    label = "Q%d" % board.target[1] if board.target else ""
    if board.mode == "confirm":
        keys = [("y", "send"), ("e", "add a note"), ("n", "cancel")]
        if width < 70:
            keys = [("y", "yes"), ("e", "note"), ("n", "no")]
        room = max(0, width - cell_width(_plain(keys)) - 3)
        ask = "Answer %s with (%s) %s?" % (label, board.letter, board.option)
        if cell_width(ask) > room:
            # Too long for the line: the letter alone, rather than half the option.
            ask = "Answer %s with (%s)?" % (label, board.letter)
        ask = clip(ask, room)
        canvas.put(y, 0, ask, "prompt")
        _spans(canvas, y, cell_width(ask) + 2, _keys(keys), width - cell_width(ask) - 3)
        return None
    if board.mode in ("note", "reply"):
        note = board.mode == "note"
        ask = "Note for (%s)" % board.letter if note else "Follow-up on %s" % label
        # The keys are said only where they leave room to type.
        ask += " (Enter sends, Esc cancels): " if width >= 70 else ": "
        ask = clip(ask, max(0, width - 20))
        room = max(1, width - cell_width(ask) - 1)
        typed = board.typed
        while cell_width(typed) > room:
            # Only the end shows when the text is longer than the line.
            typed = typed[1:]
        canvas.put(y, 0, ask, "prompt")
        canvas.put(y, cell_width(ask), typed)
        return y, cell_width(ask) + cell_width(typed)
    if board.message:
        text, kind = board.message
        canvas.put(y, 0, clip(text, width - 1), kind)
        return None
    keys = list(HINTS[board.focus])
    if board.question:
        letters = [option["letter"] for option in board.question["options"]]
        keys.append(("%s-%s" % (letters[0], letters[-1]), "answer"))
        keys.append(("r", "follow up"))
    keys += [("?", "help"), ("q", "quit")]
    _spans(canvas, y, 0, _keys(keys), width - 1)
    return None


def _keys(keys: list[tuple[str, str]]) -> Line:
    line: Line = []
    for key, what in keys:
        if line:
            line.append(("  ", ""))
        line += [(key, "key"), (" " + what, "dim")]
    return line


def _plain(keys: list[tuple[str, str]]) -> str:
    return "  ".join("%s %s" % pair for pair in keys)


def _help(canvas: Canvas, height: int, width: int) -> None:
    lines = [("Keys", "title"), ("", "")]
    lines += [("%-10s %s" % pair, "") for pair in HELP]
    lines += [
        ("", ""),
        ("The screen updates by itself when Claude or the page changes something.", "dim"),
        ("Press any key to close.", "dim"),
    ]
    box_width = min(width, max(cell_width(text) for text, _ in lines) + 4)
    box_height = min(height, len(lines) + 2)
    top, left = (height - box_height) // 2, (width - box_width) // 2
    for y in range(top, top + box_height):
        canvas.put(y, left, " " * box_width)
    _box(canvas, Rect(top, left, box_height, box_width), "Help", True)
    for y, (text, style) in enumerate(lines[: box_height - 2]):
        canvas.put(top + 1 + y, left + 2, clip(text, box_width - 4), style)
