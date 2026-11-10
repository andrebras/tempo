"""How the Board looks: the panels with their borders, the rows in each, the bottom line
(key hints, a prompt, or what just happened), the help overlay and the Settings view,
drawn onto a canvas.

A canvas only needs its size and a way to put styled text at a place. The curses screen
is one (screen.py); TextCanvas, a grid of characters, is another, so tests and the
README's capture see exactly what the terminal shows, without a terminal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from tempo.store import Question
from tempo.tui.model import SETTINGS, Board, Rect, detail_lines, layout, waiting_on_claude
from tempo.tui.text import Line, cell_width, clip, plain, wrap

TITLES = {"projects": "Projects", "questions": "Questions", "detail": "Detail"}
HINTS = {
    "projects": [("j/k", "project"), ("l", "questions"), ("s", "settings")],
    "questions": [("j/k", "question"), ("Enter", "detail"), ("h/l", "panels")],
    "detail": [("j/k", "scroll"), ("PgUp/PgDn", "page"), ("h", "back")],
    "settings": [("j/k", "project"), ("m", "merge into…"), ("Esc", "back to the questions")],
    "merge": [("j/k", "project"), ("Enter", "merge into this one"), ("Esc", "cancel")],
}
# The sidebar's keys, in the list and with a question open.
SIDEBAR = {
    "questions": [("Enter", "open"), ("h/l", "project"), ("g", "Claude")],
    "detail": [("h", "back")],
}
HELP = [
    ("j k ↑ ↓", "move; in Detail, scroll"),
    ("h l ← →", "previous or next panel"),
    ("Tab S-Tab", "next or previous panel"),
    ("Enter", "open the questions, then the detail"),
    ("a b c …", "answer with that option (asks y/n)"),
    ("A B C …", "the same; Shift is needed for h j k l q r s"),
    ("g", "sidebar: go to the project's Claude (tmux)"),
    ("r", "ask Claude for more (a follow-up)"),
    ("s", "Settings: the projects, their folders, merging"),
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
    if board.mode in SETTINGS:
        _settings(board, canvas, Rect(0, 0, max(1, height - 1), width))
        return _bottom(board, canvas, height - 1, width)
    if board.sidebar:
        _sidebar(board, canvas, Rect(0, 0, max(1, height - 1), width))
        cursor = _bottom(board, canvas, height - 1, width)
        if board.mode == "help":
            _help(canvas, height, width)
        return cursor
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
    if board.sidebar:
        _sidebar_questions(board, canvas, rect)
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
        end = "quick" if question["quick"] else ""
        _row(canvas, rect.top + y, rect, text, style, end)


def _sidebar_questions(board: Board, canvas: Canvas, rect: Rect) -> None:
    """The sidebar's questions, as the page lists them: each in a block of a line with its
    state mark, number, "quick" and age, then its title in up to two lines, and a blank
    line after; a heading before each goal. The selected block is highlighted whole and
    kept wholly on screen."""
    lines: list[tuple[Line, str]] = []
    block = (0, 0)
    for row in board.rows():
        question = row.question
        if question is None:
            lines.append(([(str(row.goal), "heading")], ""))
            continue
        start = len(lines)
        style = "selected" if question["number"] == board.number else ""
        mark, mark_style = _state_mark(board, question)
        age = _age(question["created_at"])
        head = "%s %s%s" % (mark, question["label"], " quick" if question["quick"] else "")
        head = clip(head, max(1, rect.width - cell_width(age) - 1))
        gap = " " * max(1, rect.width - cell_width(head) - cell_width(age))
        lines.append(
            ([(mark, mark_style), (head[len(mark) :], ""), (gap, ""), (age, "dim")], style)
        )
        title = wrap([(question["title"], "")], rect.width - 2)
        if len(title) > 2:
            # The rest of a long title is cut at the end of the second line.
            title = [title[0], [(" ".join(plain(line) for line in title[1:]), "")]]
        for line in title:
            lines.append(([("  " + clip(plain(line), rect.width - 2), "")], style))
        if style:
            block = (start, len(lines))
        lines.append(([], ""))
    # Scrolled just enough that the selected block is whole.
    top = max(0, block[1] - rect.height)
    if block[0] < top:
        top = block[0]
    for y, (line, style) in enumerate(lines[top : top + rect.height]):
        if style:
            _row(canvas, rect.top + y, rect, plain(line), style)
        else:
            _spans(canvas, rect.top + y, rect.left, line, rect.width)


def _state_mark(board: Board, question: Question) -> tuple[str, str]:
    """A question's mark in the sidebar's state column, with the page's meanings. Only one
    fits: something new wins, as it is what to read next; then waiting on Claude, as
    nothing can be done meanwhile; then the goal's question to start with."""
    if board.fresh(question):
        return "●", "dot"
    if waiting_on_claude(question):
        return "…", "accent"
    if question["start_here"]:
        return "»", "letter"
    return " ", ""


def _age(created_at: str) -> str:
    """How long a question has been open, short for a narrow pane: "4d", "3h", "12m"."""
    created = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    minutes = max(1, int((datetime.now(timezone.utc) - created).total_seconds() // 60))
    for size, unit in ((24 * 60, "d"), (60, "h"), (1, "m")):
        if minutes >= size:
            return "%d%s" % (minutes // size, unit)
    return "1m"


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


def _sidebar(board: Board, canvas: Canvas, rect: Rect) -> None:
    """The compact view: the project with its neighbours on top, then its questions by
    goal, each with the page's marks; or the question that is open, over the whole pane."""
    if board.focus == "detail":
        title = "%s · h back" % (board.question["label"] if board.question else "Detail")
        _box(canvas, rect, title, True)
        inner = Rect(rect.top + 1, rect.left + 1, rect.height - 2, rect.width - 2)
        if inner.height > 0 and inner.width > 0:
            _detail(board, canvas, inner, True)
        return
    count = dict(board.projects).get(board.project, 0)
    head = "◂ %s · %d open ▸" % (board.project, count)
    canvas.put(rect.top, rect.left, clip(head, rect.width), "title")
    # The other projects with something open or new, a dot on those with news.
    others: Line = []
    for name, open_count in board.projects:
        if name == board.project or not (open_count or board.fresh_project(name)):
            continue
        others += [(" " if others else "", ""), ("%s %d" % (name, open_count), "dim")]
        if board.fresh_project(name):
            others.append(("●", "dot"))
    _spans(canvas, rect.top + 1, rect.left, others, rect.width)
    canvas.put(rect.top + 2, rect.left, "─" * rect.width, "border")
    _questions(board, canvas, Rect(rect.top + 3, rect.left, rect.height - 3, rect.width), True)


def _settings(board: Board, canvas: Canvas, rect: Rect) -> None:
    """Every project with its question count and folders, the selected one highlighted;
    while merging, the project picked to merge it into."""
    merging = board.into is not None
    title = "Settings · Merge %s into…" % board.chosen if merging else "Settings · Projects"
    _box(canvas, rect, title, True)
    inner = Rect(rect.top + 1, rect.left + 1, rect.height - 2, rect.width - 2)
    if inner.height <= 0 or inner.width <= 0:
        return
    about = (
        "Merging moves a project's questions and folders into another. If both have "
        "questions, the moved ones get new numbers after the other's; each keeps a note "
        "of its old one."
    )
    lines: list[tuple[Line, str, str]] = [
        (line, "", "") for line in wrap([(about, "dim")], inner.width)
    ]
    lines.append(([], "", ""))
    selected = 0
    for project in board.summaries:
        name = str(project["name"])
        style = ""
        if name == (board.into if merging else board.chosen):
            style, selected = "selected", len(lines)
        elif merging and name == board.chosen:
            style = "current"
        count = project["questions"]
        end = "%d question%s" % (count, "" if count == 1 else "s")
        lines.append(([(name, style)], style, end))
        for folder in project["folders"]:
            place = [("    %s" % folder["folder"], "dim")]
            if folder["shown"]:
                place.append(("  %s" % folder["shown"], "dim"))
            lines.append((place, "", ""))
    if not board.summaries:
        lines.append(([("No projects yet.", "dim")], "", ""))
    start = _first_shown(selected, len(lines), inner.height)
    for y, (line, style, end) in enumerate(lines[start : start + inner.height]):
        if style or end:
            _row(canvas, inner.top + y, inner, plain(line), style, end)
        else:
            _spans(canvas, inner.top + y, inner.left, line, inner.width)


def _spans(canvas: Canvas, y: int, x: int, line: Line, width: int) -> None:
    end = x + width
    for text, style in line:
        piece = clip(text, end - x)
        canvas.put(y, x, piece, style)
        x += cell_width(piece)


def _bottom(board: Board, canvas: Canvas, y: int, width: int) -> tuple[int, int] | None:
    """The last line: the prompt being answered, else what just happened, else the keys."""
    label = "Q%d" % board.target[1] if board.target else ""
    if board.mode in ("note", "reply"):
        note = board.mode == "note"
        ask = "Note for (%s), optional" % board.letter if note else "Follow-up on %s" % label
        # The keys are said only where they leave room to type.
        ask += " (Enter sends, Esc cancels): " if width >= 70 else ": "
        ask = clip(ask, max(0, width - 20))
        room = max(1, width - cell_width(ask) - 1)
        # A pasted newline shows as an arrow, on the one line there is. Only the end shows
        # when the text is longer than the line.
        typed = board.typed.replace("\n", "↵")
        while cell_width(typed) > room:
            typed = typed[max(1, len(typed) - room) :]
        canvas.put(y, 0, ask, "prompt")
        canvas.put(y, cell_width(ask), typed)
        return y, cell_width(ask) + cell_width(typed)
    if board.mode == "confirm":
        ask = "Merge %s into %s? y merges, n cancels" % (board.chosen, board.into)
        canvas.put(y, 0, clip(ask, width - 1), "prompt")
        return None
    if board.message:
        text, kind = board.message
        canvas.put(y, 0, clip(text, width - 1), kind)
        return None
    if board.mode in HINTS:
        _spans(canvas, y, 0, _keys(list(HINTS[board.mode])), width - 1)
        return None
    keys = list((SIDEBAR if board.sidebar else HINTS)[board.focus])
    if board.question and (board.focus == "detail" or not board.sidebar):
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
