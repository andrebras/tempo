"""Text for the terminal screen: the page's light Markdown turned into styled lines that
fit a width.

A line is a list of spans, each a (text, style) pair. A style is a name such as "bold"
or "code", or "" for plain text; the screen picks how each name looks (see screen.py),
so this file knows nothing about curses.
"""

from __future__ import annotations

import re
import unicodedata

Span = tuple[str, str]
Line = list[Span]

# The same Markdown the page reads (rich() and inline() in page.js): blank lines between
# paragraphs, "- " or "1. " lists, **bold** and `code`.
_BULLET = re.compile(r"^\s*[-*]\s+")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+")
_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def cell_width(text: str) -> int:
    """How many terminal columns text takes: wide characters (such as CJK) take two,
    combining marks none."""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def clip(text: str, width: int) -> str:
    """text cut to fit width columns, ending with "…" when something was cut."""
    if cell_width(text) <= width:
        return text
    if width <= 0:
        return ""
    kept, used = "", 0
    for char in text:
        size = cell_width(char)
        if used + size > width - 1:
            break
        kept += char
        used += size
    return kept + "…"


def inline(text: str, style: str = "") -> Line:
    """One line of text as spans: **bold** and `code` get their styles, the marks go."""
    spans: Line = []
    last = 0
    for match in _INLINE.finditer(text):
        if match.start() > last:
            spans.append((text[last : match.start()], style))
        if match.group(1) is not None:
            spans.append((match.group(1), "bold"))
        else:
            spans.append((match.group(2), "code"))
        last = match.end()
    if last < len(text):
        spans.append((text[last:], style))
    return spans


def rich(text: str, width: int, style: str = "") -> list[Line]:
    """Markdown text as lines no wider than width: a blank line between paragraphs, list
    items with a bullet (or their number) and the rest of the item indented under it."""
    lines: list[Line] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        if lines:
            lines.append([])
        for row in (row for row in block.split("\n") if row.strip()):
            bullet, numbered = _BULLET.match(row), _NUMBERED.match(row)
            if bullet:
                lines += wrap(inline(row[bullet.end() :], style), width, "• ", "  ")
            elif numbered:
                mark = "%s. " % numbered.group(1)
                lines += wrap(inline(row[numbered.end() :], style), width, mark, " " * len(mark))
            else:
                lines += wrap(inline(row.strip(), style), width)
    return lines


def wrap(spans: Line, width: int, first: str = "", rest: str = "") -> list[Line]:
    """Spans broken into lines of at most width columns at spaces, keeping each word's
    style. first starts the first line and rest the others (a bullet and its indent). A
    word longer than a whole line is cut into pieces."""
    # However narrow the panel, each line keeps room for at least one character, so a
    # bullet or indent wider than the line can't leave none (which looped forever).
    width = max(1, width)
    first, rest = (
        _fit(first, width - 1, at_least_one=False),
        _fit(rest, width - 1, at_least_one=False),
    )
    lines: list[Line] = []
    line: Line = [(first, "")] if first else []
    used = start = cell_width(first)
    space: str | None = None

    def new_line() -> None:
        nonlocal line, used, start
        lines.append(line)
        line = [(rest, "")] if rest else []
        used = start = cell_width(rest)

    for text, style in spans:
        for word in re.split(r"(\s+)", text):
            if not word:
                continue
            if word.isspace():
                # Kept until the next word, so no line starts or ends with a space.
                space = style if used > start else None
                continue
            gap = 1 if space is not None and used > start else 0
            if used + gap + cell_width(word) > width and used > start:
                new_line()
                gap = 0
            if gap:
                line.append((" ", space or ""))
                used += 1
            # A word wider than a whole line is cut into pieces that fit.
            while word and cell_width(word) > width - used:
                piece = _fit(word, max(1, width - used))
                line.append((piece, style))
                word = word[len(piece) :]
                new_line()
            if word:
                line.append((word, style))
                used += cell_width(word)
            space = None
    if used > start or not lines:
        lines.append(line)
    return lines


def _fit(word: str, room: int, at_least_one: bool = True) -> str:
    """The longest start of word that fits room columns: at least one character, unless
    at_least_one is False."""
    kept = ""
    for char in word:
        if cell_width(kept + char) > room and (kept or not at_least_one):
            break
        kept += char
    return kept


def plain(line: Line) -> str:
    """A line's text without its styles."""
    return "".join(text for text, _ in line)
