"""The user's own settings that live in the database, so the hooks, the page and the
`tempo config` command all see the same value. Each is one row of the settings table;
a setting with no row has its default."""

from __future__ import annotations

import re

from tempo.store.common import QuestionError, StoreBase

# Whether the SessionStart hook starts the page. On unless the user turned it off.
START_PAGE = "start_page"
# Whether the page may open a shell in a project's folder. Off unless the user turned it on,
# because it lets the page run any command as the user.
TERMINAL = "terminal"
# The terminal's font, as a CSS font list such as "JetBrains Mono", monospace; empty for the
# system's monospace font, the page's own. Letters, digits, spaces, quotes, commas, dots and
# hyphens only, so it can never be read as more than a font.
TERMINAL_FONT = "terminal_font"
FONT = re.compile(r"[\w \-,.'\"]{0,200}")
# The terminal's text size in pixels.
TERMINAL_SIZE = "terminal_size"
SIZES = range(8, 33)
DEFAULT_SIZE = 13


class SettingsStore(StoreBase):
    """The Store's settings methods."""

    def start_page(self) -> bool:
        """Whether each session starts the page; on by default."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (START_PAGE,))
        found = row.fetchone()
        return found is None or found["value"] != "off"

    def set_start_page(self, on: bool) -> None:
        self._set(START_PAGE, "on" if on else "off")

    def terminal(self) -> bool:
        """Whether the page may open a terminal; off by default."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (TERMINAL,))
        found = row.fetchone()
        return found is not None and found["value"] == "on"

    def set_terminal(self, on: bool) -> None:
        self._set(TERMINAL, "on" if on else "off")

    def terminal_font(self) -> str:
        """The terminal's font, or "" for the system's monospace font."""
        return self._get(TERMINAL_FONT) or ""

    def set_terminal_font(self, font: str) -> None:
        font = font.strip()
        if not FONT.fullmatch(font):
            raise QuestionError(
                'A font is one or more font names, such as "JetBrains Mono", monospace'
            )
        self._set(TERMINAL_FONT, font)

    def terminal_size(self) -> int:
        """The terminal's text size in pixels."""
        value = self._get(TERMINAL_SIZE)
        return int(value) if value and value.isdigit() and int(value) in SIZES else DEFAULT_SIZE

    def set_terminal_size(self, size: int) -> None:
        if size not in SIZES:
            raise QuestionError("The font size must be from %d to %d" % (SIZES[0], SIZES[-1]))
        self._set(TERMINAL_SIZE, str(size))

    def _get(self, name: str) -> str | None:
        found = self.db.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return None if found is None else str(found["value"])

    def _set(self, name: str, value: str) -> None:
        with self._transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", (name, value)
            )
