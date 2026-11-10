"""The user's own settings that live in the database, so the hooks, the page and the
`tempo config` command all see the same value. Each is one row of the settings table;
a setting with no row has its default."""

from __future__ import annotations

from tempo.store.common import StoreBase

# Whether the SessionStart hook starts the page. Off unless the user turned it on.
START_PAGE = "start_page"
# Which side of each tmux window `tempo tmux` puts the sidebar on: left unless set.
TMUX_SIDE = "tmux_side"


class SettingsStore(StoreBase):
    """The Store's settings methods."""

    def start_page(self) -> bool:
        """Whether each session starts the page; off by default."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (START_PAGE,))
        found = row.fetchone()
        return found is not None and found["value"] == "on"

    def set_start_page(self, on: bool) -> None:
        with self._transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                (START_PAGE, "on" if on else "off"),
            )

    def tmux_side(self) -> str:
        """The side of each tmux window the sidebar goes on, "left" or "right"."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (TMUX_SIDE,))
        found = row.fetchone()
        return "right" if found is not None and found["value"] == "right" else "left"

    def set_tmux_side(self, side: str) -> None:
        with self._transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", (TMUX_SIDE, side)
            )
