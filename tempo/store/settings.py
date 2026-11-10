"""The user's own settings that live in the database, so the hooks, the page and the
`tempo config` command all see the same value. Each is one row of the settings table;
a setting with no row has its default."""

from __future__ import annotations

from tempo.store.common import StoreBase

# Whether the SessionStart hook starts the page. On unless the user turned it off.
START_PAGE = "start_page"


class SettingsStore(StoreBase):
    """The Store's settings methods."""

    def start_page(self) -> bool:
        """Whether each session starts the page; on by default."""
        row = self.db.execute("SELECT value FROM settings WHERE name = ?", (START_PAGE,))
        found = row.fetchone()
        return found is None or found["value"] != "off"

    def set_start_page(self, on: bool) -> None:
        with self._transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                (START_PAGE, "on" if on else "off"),
            )
