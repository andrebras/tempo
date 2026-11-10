"""What the terminal screen shows and what its keys do, without a terminal: the Board
loads the projects and questions, keeps the selection, turns keys into actions (answer,
follow up, move, scroll, merge projects in Settings), and notices when the database
changes. The curses layer only
draws it and passes keys in, so all of this is tested directly.

Answers and follow-ups go through the same store methods as `tempo answer`, `tempo reply`
and the page, so the hooks deliver them in the same way.
"""

from __future__ import annotations

import sqlite3
import string
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import NamedTuple

from tempo.store import Entry, Moves, Question, QuestionError, Questions, Store, Summaries
from tempo.tui.text import Line, rich, wrap

PANELS = ("projects", "questions", "detail")
# Lowercase letters that move around, so they can't also pick an option: options with
# these letters are picked with Shift (Shift+h for option h). Shift+letter always works.
BOUND = set("hjklqrs")
# Below this width the screen shows one panel at a time.
NARROW = 60
# The modes of the Settings view: the projects, picking the one to merge into, and y/n.
SETTINGS = ("settings", "merge", "confirm")
# How the thread names each kind of entry, as the page's renderThread does.
DESCRIBE = {
    "context": "Claude added context",
    "reword": "Claude reworded the question",
    "followup": "You asked for more",
    "answer": "You answered",
}


class Rect(NamedTuple):
    top: int
    left: int
    height: int
    width: int


class Row(NamedTuple):
    """A line of the Questions panel: a goal's heading (question None) or a question."""

    goal: str | None
    question: Question | None


class Board:
    """The screen's state: the projects, the selected project's open questions, the
    selected question in full, which panel has the focus, and what the keys are doing
    (moving, typing a note or a follow-up, showing help, or Settings).

    With sidebar set, it is the compact view for a narrow pane beside Claude's (`tempo tui
    --sidebar`): the list of questions, or one question opened over it."""

    def __init__(self, store: Store, home: str, sidebar: bool = False) -> None:
        self.store = store
        self.sidebar = sidebar
        # Every open question, of every project, as last loaded.
        self.everything: Questions = []
        # When each open question was last opened here: its activity then, by project and
        # number, as the page keeps it per browser. What is open at the start counts as
        # opened, as on the page's first visit, so only what changes later gets a dot.
        self.opened: dict[tuple[str, int], str] = {}
        # The current folder's project: always listed, even with nothing open.
        self.home = home
        self.project = home
        self.projects: list[tuple[str, int]] = []
        self.questions: Questions = []
        self.number: int | None = None
        self.question: Question | None = None
        self.focus = "questions"
        # "normal", "help", "note" (an answer waiting for Enter), "reply", or one of
        # SETTINGS.
        self.mode = "normal"
        self.letter = ""
        # The version of the question the option was picked on, so an answer to a wording
        # Claude has since changed is refused.
        self.version: int | None = None
        # The question an answer or follow-up being written is for, fixed when it starts,
        # so a reload meanwhile (which may select another question) can't redirect it.
        self.target: tuple[str, int] | None = None
        self.typed = ""
        # The line shown after an action: (text, "ok" or "error").
        self.message: tuple[str, str] | None = None
        # The detail panel's first shown line, and how many lines a page is; the screen
        # sets page and keeps scroll within the text as it draws.
        self.scroll = 0
        self.page = 10
        # Settings: every project with its folders, the selected one, and while merging,
        # the one picked to merge it into. Names, so a reload keeps them in place.
        self.summaries: Summaries = []
        self.chosen = home
        self.into: str | None = None
        self._seen: int | None = None
        self.reload()
        self.opened = {(q["project"], q["number"]): q["activity"] for q in self.everything}

    # Loading

    def reload(self) -> None:
        """Read everything again, keeping the selection on the same question while it is
        still open; otherwise on the one now in its place, as the page moves on to the next
        question after an answer."""
        numbers = [question["number"] for question in self.questions]
        place = numbers.index(self.number) if self.number in numbers else 0
        self._seen = self._data_version()
        everything = self.everything = self.store.list(None, "open")
        counts = Counter(question["project"] for question in everything)
        # The selected project stays listed, so answering its last question doesn't move away.
        names = set(counts) | {self.home, self.project}
        self.projects = [(name, counts[name]) for name in sorted(names, key=str.lower)]
        # list() gives each project's questions grouped by goal, as the page shows them.
        self.questions = [q for q in everything if q["project"] == self.project]
        numbers = [question["number"] for question in self.questions]
        if self.number not in numbers:
            self.number = numbers[min(place, len(numbers) - 1)] if numbers else None
            self.scroll = 0
        self.question = self.store.get(self.project, self.number) if self.number else None
        self._mark_opened()
        if self.mode in SETTINGS:
            self.store.forget_gone_folders()
            self.summaries = self.store.project_summaries()
            listed = [str(project["name"]) for project in self.summaries]
            if self.into is not None and not {self.chosen, self.into} <= set(listed):
                # One of the two was merged or renamed elsewhere meanwhile: pick again.
                self.mode, self.into = "settings", None
            if self.chosen not in listed:
                self.chosen = listed[0] if listed else self.project

    def changed(self) -> bool:
        """Reload if another connection (Claude, the page, a command) wrote to the database
        since the last load. SQLite's data_version moves only on others' writes, so this
        costs one tiny query."""
        if self._data_version() == self._seen:
            return False
        self.reload()
        return True

    def fresh(self, question: Question) -> bool:
        """Whether a question gets the dot for something new: Claude asked, reworded or
        added to it since it was last opened here."""
        seen = self.opened.get((question["project"], question["number"]))
        return bool(seen != question["activity"])

    def fresh_project(self, name: str) -> bool:
        """Whether a project has a question with something new."""
        return any(self.fresh(q) for q in self.everything if q["project"] == name)

    def _mark_opened(self) -> None:
        """The question on screen counts as opened: in the sidebar, only once it is open."""
        if self.question is None or (self.sidebar and self.focus != "detail"):
            return
        for question in self.questions:
            if question["number"] == self.number:
                self.opened[(self.project, self.number)] = question["activity"]

    def _data_version(self) -> int:
        return int(self.store.db.execute("PRAGMA data_version").fetchone()[0])

    def rows(self) -> list[Row]:
        """The Questions panel's lines: a heading before each goal's questions once any
        question has a goal, "Other" for those without, as on the page."""
        if not any(question["goal"] for question in self.questions):
            return [Row(None, question) for question in self.questions]
        rows: list[Row] = []
        group: object = object()
        for question in self.questions:
            if question["goal"] != group:
                group = question["goal"]
                rows.append(Row(question["goal"] or "Other", None))
            rows.append(Row(None, question))
        return rows

    # Keys

    def press(self, key: str) -> bool:
        """Act on one key, named as the screen names them ("j", "B", "enter", "esc", "tab",
        "btab", "up", "pgdn", "ctrl-d", "backspace", …). False means quit."""
        if key == "ctrl-c":
            # Usually a terminal turns Ctrl-C into an interrupt; without one, it still quits.
            return False
        if self.mode == "help":
            self.mode = "normal"
            return True
        if self.mode in ("note", "reply"):
            self._type(key)
            return True
        self.message = None
        if self.mode in SETTINGS:
            self._settings(key)
            return True
        if self.sidebar and self._sidebar(key):
            return True
        if key in ("q", "esc"):
            return False
        if key in ("j", "down"):
            self.move(1)
        elif key in ("k", "up"):
            self.move(-1)
        elif key in ("l", "right", "enter") and self.focus != "detail":
            self.focus = PANELS[PANELS.index(self.focus) + 1]
        elif key in ("h", "left") and self.focus != "projects":
            self.focus = PANELS[PANELS.index(self.focus) - 1]
        elif key in ("tab", "btab"):
            step = 1 if key == "tab" else -1
            self.focus = PANELS[(PANELS.index(self.focus) + step) % len(PANELS)]
        elif key in ("pgdn", "ctrl-d", "pgup", "ctrl-u"):
            step = self.page if key in ("pgdn", "ctrl-d") else -self.page
            self.scroll = max(0, self.scroll + step)
        elif key == "?":
            self.mode = "help"
        elif key == "s":
            self.mode, self.chosen = "settings", self.project
            self.reload()
        elif key == "r":
            if self._selected():
                self.mode, self.typed, self.target = "reply", "", self._here()
        elif len(key) == 1 and key.lower() in string.ascii_lowercase and key not in BOUND:
            self._pick(key.lower())
        return True

    def _sidebar(self, key: str) -> bool:
        """The sidebar's own keys; False for a key it reads as the full view does. In the
        list, h and l switch project and Enter opens the question; once it is open, h or
        Esc goes back to the list."""
        if self.focus == "detail":
            if key not in ("h", "left", "esc"):
                return False
            self.focus = "questions"
        elif key in ("enter", "right", "left", "l", "h"):
            if key == "enter" and self._selected():
                self.focus, self.scroll = "detail", 0
                self._mark_opened()
            elif key != "enter":
                self.switch(1 if key in ("l", "right") else -1)
        elif key in ("esc", "tab", "btab"):
            # The list is the sidebar's only panel, and Esc there must not close it.
            pass
        else:
            return False
        return True

    def switch(self, step: int) -> None:
        """Show the next or previous project, round the list."""
        names = [name for name, _ in self.projects]
        index = (names.index(self.project) + step) % len(names)
        self.project, self.number, self.scroll, self.questions = names[index], None, 0, []
        self.reload()

    def paste(self, text: str) -> None:
        """Pasted text: added to the note or follow-up being written, newlines and all.
        Anywhere else it is ignored, so a paste never acts as keys: a letter in it would
        pick an option, and a newline would send."""
        if self.mode in ("note", "reply"):
            self.typed += "".join(char for char in text if char == "\n" or char.isprintable())
        elif self.mode == "normal":
            self.message = ("Paste into a note (e after picking) or a follow-up (r).", "error")

    def move(self, step: int) -> None:
        """j/k in the focused panel: another project, another question, or scroll."""
        if self.focus == "projects":
            names = [name for name, _ in self.projects]
            index = names.index(self.project) + step
            if 0 <= index < len(names):
                self.project, self.number, self.scroll = names[index], None, 0
                self.questions = []
                self.reload()
        elif self.focus == "questions":
            numbers = [question["number"] for question in self.questions]
            if self.number in numbers:
                index = numbers.index(self.number) + step
                if 0 <= index < len(numbers):
                    self.number, self.scroll = numbers[index], 0
                    self.question = self.store.get(self.project, self.number)
        else:
            self.scroll = max(0, self.scroll + step)

    def _selected(self) -> bool:
        if self.question is None:
            self.message = ("No question selected.", "error")
        return self.question is not None

    def _pick(self, letter: str) -> None:
        if not self._selected() or self.question is None:
            return
        texts = {option["letter"]: option["text"] for option in self.question["options"]}
        if letter not in texts:
            # The store's own words for it.
            self.message = ("Option (%s) does not exist" % letter, "error")
            return
        self.mode, self.typed, self.letter, self.target = "note", "", letter, self._here()
        self.version = self.question["version"]

    def _type(self, key: str) -> None:
        if key == "esc":
            self.mode = "normal"
        elif key == "enter":
            if self.mode == "note":
                self.answer(self.letter, self.typed)
            else:
                self.reply(self.typed)
        elif key == "backspace":
            self.typed = self.typed[:-1]
        elif key == "ctrl-u":
            self.typed = ""
        elif len(key) == 1 and key.isprintable():
            self.typed += key

    # Settings

    def _settings(self, key: str) -> None:
        """A key in Settings: j/k move along the projects; m merges the selected one into
        another, picked the same way, then y confirms. Esc steps back; from the list, Esc,
        q and s go back to the questions."""
        if self.mode == "confirm":
            if key == "y":
                self.merge()
            elif key in ("n", "esc"):
                self.mode, self.into = "settings", None
        elif key in ("j", "down", "k", "up"):
            names = [str(project["name"]) for project in self.summaries]
            if not names:
                return
            current = self.into if self.mode == "merge" else self.chosen
            index = names.index(current) if current in names else 0
            index = max(0, min(len(names) - 1, index + (1 if key in ("j", "down") else -1)))
            if self.mode == "merge":
                self.into = names[index]
            else:
                self.chosen = names[index]
        elif self.mode == "merge":
            if key == "esc":
                self.mode, self.into = "settings", None
            elif key == "enter" and self.into == self.chosen:
                self.message = ("Pick another project to merge %s into." % self.chosen, "error")
            elif key == "enter":
                self.mode = "confirm"
        elif key == "m":
            if len(self.summaries) < 2:
                self.message = ("There is no other project to merge into.", "error")
            else:
                # Start on the first other project, so Enter right away picks one.
                others = [str(p["name"]) for p in self.summaries if p["name"] != self.chosen]
                self.mode, self.into = "merge", others[0]
        elif key in ("esc", "q", "s"):
            self.mode = "normal"
            self.reload()

    def merge(self) -> None:
        """Merge the selected project into the one picked, as the page's Settings joins
        them, and say where its questions went."""
        old, new = self.chosen, str(self.into)
        self.mode, self.into = "settings", None
        try:
            moves = self.store.rename_project(old, new)
        except (QuestionError, sqlite3.Error) as error:
            self.message = (str(error), "error")
            return
        self.message = (merged(old, new, moves), "ok")
        # The old project is gone: stay on the one that took its questions and folders.
        self.chosen = new
        if self.project == old:
            self.project, self.number = new, None
        if self.home == old:
            self.home = new
        self.reload()

    # Saving

    def answer(self, letter: str, note: str) -> None:
        """Answer the selected question as `tempo answer` does, then move on to the next."""
        from tempo.cli import _describe_answer, answered_label

        def save() -> str:
            question = self.store.answer(*self._target(), letter, note, version=self.version)
            return "Answered %s: %s" % (
                answered_label(question),
                _describe_answer(question, question["answer"]),
            )

        self._save(save)

    def reply(self, body: str) -> None:
        """Ask for more on the selected question as `tempo reply` does."""

        def save() -> str:
            question = self.store.follow_up(*self._target(), body)
            return "Added your follow-up to %s." % question["label"]

        self._save(save)

    def _here(self) -> tuple[str, int] | None:
        return (self.project, self.number) if self.number is not None else None

    def _target(self) -> tuple[str, int]:
        if self.target is None:
            raise QuestionError("No question selected.")
        return self.target

    def _save(self, save: Callable[[], str]) -> None:
        self.mode = "normal"
        try:
            self.message = (save(), "ok")
            if self.sidebar:
                # Back to the list, where the next question is selected.
                self.focus = "questions"
        except (QuestionError, sqlite3.Error) as error:
            self.message = (str(error), "error")
        self.reload()


def merged(old: str, new: str, moves: Moves) -> str:
    """Where a merged project's questions went, worded as the page's joinNotice: "Moved
    5 questions from tempo to Tempo: now Q28-Q32." (with an en dash). When no numbers
    changed, which the page doesn't mention, just that it is done."""
    if not moves:
        return "Merged %s into %s." % (old, new)
    first, last = moves[0]["to"], moves[-1]["to"]
    numbers = "Q%d" % first if first == last else "Q%d\u2013Q%d" % (first, last)
    count = "%d question%s" % (len(moves), "" if len(moves) == 1 else "s")
    return "Moved %s from %s to %s: now %s." % (count, old, new, numbers)


def detail_lines(question: Question, width: int) -> list[Line]:
    """The Detail panel's text for a question, as the page shows it: where it is, its
    title, goal and facts, context, lettered options with the recommended one marked, the
    latest answer, and its thread."""
    from tempo.cli import open_for

    about = [question["project"], question["label"], question["status"]]
    if question["version"] > 1:
        about.append("reworded (version %d)" % question["version"])
    lines: list[Line] = [[(" · ".join(about), "dim")]]
    if waiting_on_claude(question):
        lines.append([("Waiting on Claude", "accent")])
    lines += wrap([(question["title"], "title")], width)
    facts = ["Goal: %s" % question["goal"] if question["goal"] else "No goal"]
    if question["status"] == "open":
        facts += ["quick"] if question["quick"] else []
        facts.append(open_for(question["created_at"]))
    lines += wrap([(" · ".join(facts), "dim")], width)
    if question["start_here"]:
        lines += wrap([("Start here: ", "accent"), (question["start_here"], "")], width)
    lines += [[], *rich(question["context"], width), []]
    for option in question["options"]:
        spans = [("(%s) " % option["letter"], "letter"), (option["text"], "")]
        if option["letter"] == question["recommended"]:
            spans += [(" ", ""), ("[recommended]", "rec")]
        lines += wrap(spans, width, rest="    ")
    answer = question["answer"]
    if answer:
        reopened = question["status"] == "open"
        label = "Your previous answer: " if reopened else "Your answer: "
        lines += [[], *wrap([(label, "bold"), (_chosen(question, answer), "")], width)]
    lines += [[], [("Thread", "heading")]]
    lines.append([("Asked %s" % local_time(question["versions"][0]["created_at"]), "dim")])
    for entry in question["entries"]:
        meta = "%s · version %d · %s" % (
            DESCRIBE.get(entry["kind"], entry["kind"]),
            entry["version"],
            local_time(entry["created_at"]),
        )
        lines += wrap([(meta, "dim")], width)
        body = "\n".join(part for part in (_chosen(question, entry, False), entry["body"]) if part)
        lines += [[("  ", ""), *line] for line in rich(body, width - 2)]
    return lines


def _chosen(question: Question, entry: Entry, note: bool = True) -> str:
    """An answer as "(b) Right", with its note after a dot when note is set."""
    parts: list[str] = []
    if entry["choice"]:
        version = question["versions"][entry["version"] - 1]
        option = next((o for o in version["options"] if o["letter"] == entry["choice"]), None)
        parts.append("(%s) %s" % (option["letter"], option["text"]) if option else "")
    if note and entry["body"]:
        parts.append(str(entry["body"]))
    return " · ".join(part for part in parts if part)


def waiting_on_claude(question: Question) -> bool:
    """Whether the user's last word is a follow-up Claude hasn't answered yet: list() rows
    say so in "waiting", full questions show it in their thread."""
    if "waiting" in question:
        return bool(question["waiting"])
    entries = question.get("entries") or []
    return bool(entries) and entries[-1]["kind"] == "followup" and entries[-1]["author"] == "user"


def local_time(stamp: str) -> str:
    """A stored UTC time as local time, such as "2026-11-07 14:03"."""
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def layout(width: int, height: int, focus: str, projects: int) -> dict[str, Rect]:
    """Where each panel goes; the last line is left for the key hints. Wide enough, it is
    lazygit's shape: Projects above Questions on the left, Detail on the right. Narrower
    than NARROW, only the focused panel shows, over the whole screen."""
    body = max(1, height - 1)
    if width < NARROW or body < 9:
        return {focus: Rect(0, 0, body, width)}
    left = min(40, max(24, width // 3))
    top = min(projects + 2, max(3, body // 3))
    return {
        "projects": Rect(0, 0, top, left),
        "questions": Rect(top, 0, body - top, left),
        "detail": Rect(0, left, body, width - left),
    }
