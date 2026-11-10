"""The user's own settings that live in the database, so the hooks, the page and the
`tempo config` command all see the same value. Each is one row of the settings table;
a setting with no row has its default."""

from __future__ import annotations

from tempo.store.common import StoreBase

# Whether the SessionStart hook starts the page. Off unless the user turned it on.
START_PAGE = "start_page"
# Which side of each tmux window `tempo sidebar` puts the sidebar on: left unless set.
TMUX_SIDE = "tmux_side"
# Whether `tempo sidebar` last turned the sidebar on, so `--restore` brings it back after
# tmux restarts. Off unless the user turned it on.
TMUX_SIDEBAR = "tmux_sidebar"
# Whether the SessionStart hook has Claude point the user to /tempo:install while the
# ~/.local/bin/tempo link is missing. On until `tempo install` works or the user says no.
INSTALL_HINT = "install_hint"


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

    def tmux_sidebar(self) -> bool:
        """Whether the sidebar was last turned on; off by default."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (TMUX_SIDEBAR,))
        found = row.fetchone()
        return found is not None and found["value"] == "on"

    def set_tmux_sidebar(self, on: bool) -> None:
        with self._transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                (TMUX_SIDEBAR, "on" if on else "off"),
            )

    def install_hint(self) -> bool:
        """Whether sessions offer /tempo:install while tempo isn't linked; on by default."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (INSTALL_HINT,))
        found = row.fetchone()
        return found is None or found["value"] != "off"

    def set_install_hint(self, on: bool) -> None:
        with self._transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                (INSTALL_HINT, "on" if on else "off"),
            )
