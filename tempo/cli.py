"""The `tempo` command: how Claude adds, rewords and reads questions, how hooks
hand the user's answers back to Claude, and how the user answers from the terminal."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import sqlite3
import string
import subprocess
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any

from tempo import log, slash
from tempo.store import (
    CODE_ROOT,
    Entries,
    Entry,
    Question,
    QuestionError,
    Store,
    branch_db,
    connect,
    db_path,
    default_db,
    setting,
)

DEFAULT_PORT = 2204
# Code on a branch database serves its page on a port in this range, picked from the
# database's path, so it never takes the real page's port.
BRANCH_PORTS = range(22100, 23000)
# Where Claude Code lists the installed plugins and their versions.
INSTALLED_PLUGINS = "~/.claude/plugins/installed_plugins.json"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.run(args) or 0
    except QuestionError as error:
        print("tempo: %s" % error, file=sys.stderr)
        return 2
    finally:
        while _opened:
            _opened.pop().db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tempo", description="Open questions between you and Claude, kept in SQLite."
    )
    commands = parser.add_subparsers(dest="command")

    _ask_commands(commands)
    _read_commands(commands)
    _change_commands(commands)
    _delivery_commands(commands)
    _user_commands(commands)
    _config_commands(commands)
    _run_commands(commands)
    slash.register(commands)
    return parser


def _ask_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Asking: add a question."""
    add = commands.add_parser("add", help="Add a question (prints its number)")
    add.add_argument("--project", default=None, help="Defaults to the current git repo's name")
    add.add_argument("--title", required=True, help="The question itself, one sentence")
    add.add_argument("--context", required=True, help="Everything needed to answer it cold")
    add.add_argument(
        "--option",
        action="append",
        dest="options",
        required=True,
        help="One option, written out; repeat for each (lettered a, b, c...)",
    )
    add.add_argument("--recommended", help="Letter of the option Claude recommends")
    add.add_argument(
        "--goal", help='The piece of work the answer moves forward, e.g. "Release 0.4"'
    )
    add.add_argument(
        "--start-here",
        metavar="REASON",
        help='Answer this one first in its goal, and why, e.g. "unlocks Q5 and Q6"',
    )
    # Priorities were replaced by goals; the option is still accepted for one release.
    add.add_argument("--priority", help=argparse.SUPPRESS)
    add.add_argument("--number", type=int, help="Use this number (must be the next free or higher)")
    add.add_argument("--json", action="store_true")
    add.set_defaults(run=run_add)


def _read_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Reading: show one question, or list them."""
    show = commands.add_parser("show", help="Show a question with its whole thread")
    show.add_argument("number", type=_code, help="A number such as 12, Q12, or 12b for an option")
    show.add_argument("--project")
    show.add_argument("--json", action="store_true")
    show.set_defaults(run=run_show)

    list_ = commands.add_parser("list", help="List questions (open by default)")
    list_.add_argument("--project")
    list_.add_argument("--all-projects", action="store_true")
    list_.add_argument("--status", default="open", choices=["open", "answered", "archived", "all"])
    list_.add_argument("--json", action="store_true")
    list_.set_defaults(run=run_list)


def _change_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Changing a question or a project after it was asked."""
    context = commands.add_parser("context", help="Add context to a question's thread")
    context.add_argument("number", type=_number)
    context.add_argument("body")
    context.add_argument("--project")
    context.set_defaults(run=run_context)

    reword = commands.add_parser("reword", help="Save a new version of a question")
    reword.add_argument("number", type=_number)
    reword.add_argument("--note", required=True, help="Why it was reworded, shown in the thread")
    reword.add_argument("--title")
    reword.add_argument("--context")
    reword.add_argument(
        "--option", action="append", dest="options", help="Replace all options; repeat for each"
    )
    reword.add_argument("--recommended")
    reword.add_argument("--goal")
    _start_here_arguments(reword)
    reword.add_argument("--project")
    reword.set_defaults(run=run_reword)

    goal = commands.add_parser(
        "goal", help="Set a question's goal or start-here mark without rewording it"
    )
    goal.add_argument("number", type=_number)
    goal.add_argument("goal", nargs="?", help='The goal; "" removes it')
    _start_here_arguments(goal)
    goal.add_argument("--project")
    goal.set_defaults(run=run_goal)

    # Priorities were replaced by goals; the command still runs, doing nothing, for one
    # release, so sessions that learned it earlier aren't stopped by an error.
    priority = commands.add_parser("priority")
    priority.add_argument("number", type=_number)
    priority.add_argument("priority")
    priority.add_argument("--project")
    priority.set_defaults(run=run_priority)

    archive = commands.add_parser("archive", help="Archive a project's answered questions")
    archive.add_argument("--project")
    archive.set_defaults(run=run_archive)

    next_ = commands.add_parser("set-next", help="Continue numbering from a given Q number")
    next_.add_argument("number", type=_number)
    next_.add_argument("--project")
    next_.set_defaults(run=run_set_next)


def _delivery_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Handing the user's answers and follow-ups to Claude."""
    inbox = commands.add_parser("inbox", help="Print answers and follow-ups not yet seen")
    inbox.add_argument("--project")
    inbox.add_argument(
        "--all-projects",
        action="store_true",
        help="Show every project's news without marking it seen, so its sessions still get it",
    )
    inbox.set_defaults(run=run_inbox)

    wait = commands.add_parser(
        "wait", help="Block until the user answers or follows up, print it, then exit"
    )
    wait.add_argument("--project")
    # Waiting for every project took other sessions' news; the option is still accepted,
    # with a note, for one release.
    wait.add_argument("--all-projects", action="store_true", help=argparse.SUPPRESS)
    wait.add_argument("--timeout", type=int, default=0, help="Seconds; 0 waits forever")
    wait.add_argument("--interval", type=float, default=2.0)
    wait.set_defaults(run=run_wait)


def _user_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The user's commands in the terminal: answer and follow up as on the page, and a badge."""
    answer = commands.add_parser(
        "answer", help="Answer a question with an option, as the page does"
    )
    answer.add_argument("number", type=_number)
    answer.add_argument("letter", help="The option's letter, such as b, or its text, such as yes")
    answer.add_argument("--note", default="", help="A note to go with the answer")
    answer.add_argument("--project")
    answer.set_defaults(run=run_answer)

    reply = commands.add_parser(
        "reply", help="Ask Claude for more on a question, as the page's follow-up does"
    )
    reply.add_argument("number", type=_number)
    reply.add_argument("body", help="What is unclear or what you would like to know")
    reply.add_argument("--project")
    reply.set_defaults(run=run_reply)

    status = commands.add_parser(
        "status", help="Print a short badge for the tmux status bar, such as '? 3'"
    )
    which = status.add_mutually_exclusive_group()
    which.add_argument("--project", help="Count this project instead of the folder's")
    which.add_argument("--all", action="store_true", help="Count every project")
    status.set_defaults(run=run_status)

    tui = commands.add_parser(
        "tui", help="Answer in a full-screen terminal view, with panels like the page's"
    )
    tui.add_argument("--project", help="Start on this project; the folder's by default")
    tui.set_defaults(run=run_tui)


def _config_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The user's settings, from the terminal: the same ones the page's Settings show."""
    config = commands.add_parser(
        "config",
        help="Your settings: show them, or change one, such as `tempo config page off`",
    )
    config.add_argument(
        "name", nargs="?", choices=["page"], help="page: start the page with each session"
    )
    config.add_argument("value", nargs="?", choices=["on", "off"])
    config.set_defaults(run=run_config)


def _run_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Run by Claude Code and by Tempo itself: the hooks and the page."""
    hook = commands.add_parser("hook", help="Claude Code hook entry point (reads JSON on stdin)")
    hook.add_argument("event", choices=["session-start", "user-prompt-submit", "stop"])
    hook.add_argument("--port", type=int, default=_port())
    hook.set_defaults(run=run_hook)

    serve = commands.add_parser("serve", help="Run the web page")
    serve.add_argument("--port", type=int, default=_port())
    serve.add_argument(
        "--background",
        action="store_true",
        help="Start detached; keeps a current Tempo already on the port, replaces a stale one",
    )
    serve.set_defaults(run=run_serve)


def _start_here_arguments(parser: argparse.ArgumentParser) -> None:
    mark = parser.add_mutually_exclusive_group()
    mark.add_argument(
        "--start-here",
        metavar="REASON",
        help='Answer this one first in its goal, and why, e.g. "unlocks Q5 and Q6"',
    )
    mark.add_argument("--no-start-here", action="store_true", help="Remove the start-here mark")


PRIORITY_GONE = (
    "tempo: priorities are no longer used, so the priority was ignored."
    " Give each question a --goal instead (see `tempo goal --help`)."
)


WAIT_ALL_GONE = (
    "tempo: wait --all-projects is no longer used, because it took news meant for other"
    " sessions. This waits for this folder's project only."
)


# Commands


def run_add(args: argparse.Namespace) -> None:
    if args.priority is not None:
        print(PRIORITY_GONE, file=sys.stderr)
    question = _store().add(
        _project(args.project, record=True),
        args.title,
        args.context,
        args.options,
        recommended=args.recommended,
        goal=args.goal,
        start_here=args.start_here,
        number=args.number,
    )
    if args.json:
        print(json.dumps(question, indent=2))
    else:
        print("Added %s in %s: %s" % (question["label"], question["project"], _pointer(question)))


def run_show(args: argparse.Namespace) -> None:
    question = _store().get(_project(args.project), args.number)
    print(json.dumps(question, indent=2) if args.json else format_question(question))


def run_list(args: argparse.Namespace) -> None:
    project = None if args.all_projects else _project(args.project)
    questions = _store().list(project, args.status)
    if args.json:
        print(json.dumps(questions, indent=2))
        return
    if not questions:
        print("No %s questions." % args.status)
    for question in questions:
        facts = [question["status"], "v%d" % question["version"]]
        if question["goal"]:
            facts.append("goal: %s" % question["goal"])
        if question["start_here"]:
            facts.append("start here")
        if question["status"] == "open":
            facts += ["quick"] if question["quick"] else []
            facts.append(open_for(question["created_at"]))
        print(
            "%s%s [%s] %s"
            % (
                "" if project else question["project"] + " ",
                question["label"],
                ", ".join(facts),
                question["title"],
            )
        )


def run_context(args: argparse.Namespace) -> None:
    question = _store().add_context(_project(args.project), args.number, args.body)
    print("Added context to %s." % question["label"])


def run_reword(args: argparse.Namespace) -> None:
    question = _store().reword(
        _project(args.project),
        args.number,
        args.note,
        title=args.title,
        context=args.context,
        options=args.options,
        recommended=args.recommended,
        goal=args.goal,
        start_here=args.start_here,
        clear_start_here=args.no_start_here,
    )
    print("%s is now at version %d." % (question["label"], question["version"]))


def run_goal(args: argparse.Namespace) -> None:
    question = _store().set_goal(
        _project(args.project),
        args.number,
        goal=args.goal,
        start_here=args.start_here,
        clear_start_here=args.no_start_here,
    )
    print("%s %s." % (question["label"], describe_goal(question)))


def run_priority(args: argparse.Namespace) -> None:
    print(PRIORITY_GONE, file=sys.stderr)


def run_archive(args: argparse.Namespace) -> None:
    count = _store().archive_answered(_project(args.project))
    print("Archived %d answered question(s)." % count)


def run_set_next(args: argparse.Namespace) -> None:
    project = _project(args.project)
    _store().set_next(project, args.number)
    print("The next question in %s will be Q%d." % (project, args.number))


def run_inbox(args: argparse.Namespace) -> None:
    if args.all_projects:
        # Taking every project's news would keep it from the sessions it is meant for.
        store = _store()
        text = _describe_inbox(store, store.undelivered())[0]
        if text:
            text += "\n(This is only shown: each project's sessions still receive it.)"
    else:
        text = take_inbox(_project(args.project))
    print(text or "Nothing new from the user.")


def run_wait(args: argparse.Namespace) -> int:
    if args.all_projects:
        print(WAIT_ALL_GONE, file=sys.stderr)
    named = args.project or setting("PROJECT")
    # Without a name, the folder's project is looked up again on every check, so a
    # project renamed or joined in Settings while the wait runs is followed.
    path = None if named else folder_path(project_folder())
    project = _folder_project(path) if path else named
    deadline = time.time() + args.timeout if args.timeout else None
    # The session's shell. When it is gone, the wait is an orphan: news it took would
    # reach nobody, and could be meant for a later session in the same project.
    parent = os.getppid()
    # A marker tells the Stop hook that a wait is running for this project.
    marker = os.path.join(os.path.dirname(_db_path()), "wait-%d.json" % os.getpid())
    _write_marker(marker, project)
    try:
        while True:
            current = _folder_project(path) if path else project
            if current != project:
                project = current
                _write_marker(marker, project)
            if os.getppid() != parent:
                return 0
            text = take_inbox(project)
            if text:
                print(text)
                return 0
            if deadline and time.time() >= deadline:
                print("Nothing new from the user.")
                return 0
            time.sleep(args.interval)
    finally:
        with contextlib.suppress(OSError):
            os.remove(marker)


def run_answer(args: argparse.Namespace) -> None:
    store, project = _store(), _project(args.project)
    letter = slash.option_letter(store.get(project, args.number), args.letter)
    question = store.answer(project, args.number, letter, args.note)
    print(
        "Answered %s: %s"
        % (answered_label(question), _describe_answer(question, question["answer"]))
    )


def run_reply(args: argparse.Namespace) -> None:
    question = _store().follow_up(_project(args.project), args.number, args.body)
    print("Added your follow-up to %s." % question["label"])


def run_status(args: argparse.Namespace) -> None:
    """Print "? N" when N questions of the current folder's project wait on the user, else
    nothing. tmux runs this every few seconds, so it reads the file as it is: it never
    creates, migrates or writes the database, and prints nothing (not even an error) when it
    can't read it."""
    if args.all:
        count = open_count(db_path())
    else:
        project = args.project or setting("PROJECT")
        count = open_count(db_path(), project, None if project else folder_path(os.getcwd()))
    if count:
        print("? %d" % count)


def run_config(args: argparse.Namespace) -> None:
    """Print the settings, or change the one named and print it."""
    store = _store()
    if args.value is not None:
        store.set_start_page(args.value == "on")
    if store.start_page():
        print("page on: each session starts the page at http://127.0.0.1:%d/" % _port())
    else:
        print(
            "page off: sessions don't start the page; `tempo serve --background` starts it."
            " Claude points you to the terminal (`tempo show`, `tempo answer`)."
        )


def run_tui(args: argparse.Namespace) -> int:
    """The page's questions in the terminal (tempo/tui). It never starts the page."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("tempo: tui needs a terminal", file=sys.stderr)
        return 1
    # Imported here, so the hooks and other commands never load curses.
    from tempo.tui import screen

    return screen.run(_project(args.project))


def open_count(path: str, project: str | None = None, folder: str | None = None) -> int:
    """How many questions are open: in one project, in the project a folder (its full
    path) is filed under, or in all of them. 0 when the file can't be read."""
    if not os.path.isfile(path):
        return 0
    from urllib.parse import quote

    try:
        # mode=rw never creates the file. Not mode=ro: a WAL database that no one has open
        # has no -shm file, and a read-only connection can't make one, so it can't read.
        db = sqlite3.connect("file:%s?mode=rw" % quote(path), uri=True, timeout=1)
        try:
            db.execute("PRAGMA query_only = ON")
            if folder:
                project = _filed_under(db, folder)
            sql = "SELECT COUNT(*) FROM questions WHERE status = 'open'"
            params = [project] if project else []
            if project:
                sql += " AND project = ?"
            return int(db.execute(sql, params).fetchone()[0])
        finally:
            db.close()
    except sqlite3.Error:
        return 0


def _filed_under(db: sqlite3.Connection, path: str) -> str:
    """The project a folder is filed under, read as Store.project_for does but without
    opening a Store, which would create or migrate the database."""
    name = folder_name(path)
    for sql, value in (
        ("SELECT project FROM folders WHERE path = ?", path),
        ("SELECT project FROM folders WHERE folder = ? AND path IS NULL", name),
    ):
        try:
            row = db.execute(sql, (value,)).fetchone()
        except sqlite3.OperationalError:
            # An old database without folders, or without their paths.
            continue
        if row and row[0]:
            return str(row[0])
    return name


def _folder_project(path: str) -> str:
    with Store(connect()) as store:
        return store.project_for(os.path.basename(path), path)


def _write_marker(marker: str, project: str | None) -> None:
    """Write the wait marker whole, so the Stop hook never reads half of one."""
    with open(marker + ".tmp", "w", encoding="utf-8") as file:
        json.dump({"pid": os.getpid(), "project": project}, file)
    os.replace(marker + ".tmp", marker)


def wait_running(project: str) -> bool:
    """Whether a `tempo wait` covering the project is running. Markers left by waits
    that were killed are removed, and so are those whose pid has since been given to a
    process that is not a wait.

    A wait that left no marker here (one from an install older than markers, or one
    whose database, and so its markers, are in another folder) counts for the project it
    names with --project, else for its folder's project, or for every project when its
    folder cannot be found.
    """
    folder = os.path.dirname(_db_path())
    waits = _wait_processes()
    running = False
    marked: set[int] = set()
    for name in os.listdir(folder):
        if not (name.startswith("wait-") and name.endswith(".json")):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as file:
                marker = json.load(file)
            pid = int(marker["pid"])
            alive = _alive(pid) if waits is None else pid in waits
        except (OSError, ValueError, KeyError, TypeError):
            alive = False
        if alive:
            marked.add(pid)
            running = running or marker.get("project") in (None, project)
        else:
            with contextlib.suppress(OSError):
                os.remove(path)
    unmarked = {pid: command for pid, command in (waits or {}).items() if pid not in marked}
    return running or any(
        _unmarked_project(pid, command) in (None, project) for pid, command in unmarked.items()
    )


def _unmarked_project(pid: int, command: str) -> str | None:
    """The project a wait without a marker serves, or None when that cannot be told."""
    named = re.search(r"--project[= ](\S+)", command)
    if named:
        return named.group(1)
    override = _process_project_setting(pid)
    if override:
        return override
    cwd = _process_cwd(pid)
    return project_name(cwd) if cwd else None


def _process_project_setting(pid: int) -> str | None:
    """TEMPO_PROJECT (or OQ_PROJECT) as another process of this user sees it, or None."""
    try:
        with open("/proc/%d/environ" % pid, "rb") as file:  # Linux
            env = dict(
                item.split("=", 1) for item in file.read().decode().split("\0") if "=" in item
            )
        return env.get("TEMPO_PROJECT") or env.get("OQ_PROJECT")
    except OSError:
        pass
    try:
        # ps -E adds the process's environment after its command (macOS). ps is looked up
        # on PATH on purpose; the only value passed in is a number.
        listing = subprocess.run(  # noqa: S603
            ["ps", "-E", "-o", "command=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"\s(?:TEMPO|OQ)_PROJECT=(.*?)(?=\s[A-Za-z_][A-Za-z0-9_]*=|$)", listing)
    return found.group(1) if found else None


def _process_cwd(pid: int) -> str | None:
    """The folder a process runs in, or None when it cannot be found."""
    with contextlib.suppress(OSError):
        return os.readlink("/proc/%d/cwd" % pid)  # Linux
    try:
        # lsof is looked up on PATH on purpose; the only value passed in is a number.
        listing = subprocess.run(  # noqa: S603
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((line[1:] for line in listing.splitlines() if line.startswith("n/")), None)


# How a Tempo command shows in ps: bin/tempo runs `python3 /…/tempo/__main__.py wait …`
# (older releases ran `python3 -m tempo wait …`), and start_server runs
# `python3 -m tempo serve …`.
TEMPO_COMMAND = r"(?:^|\s)(?:-m tempo|\S*/tempo/__main__\.py) %s(?:\s|$)"
WAIT_COMMAND = re.compile(TEMPO_COMMAND % "wait")
SERVE_COMMAND = re.compile(TEMPO_COMMAND % "serve")


def _wait_processes() -> dict[int, str] | None:
    """The `tempo wait` processes running, as their commands by pid, or None when ps
    cannot be run."""
    try:
        # ps is looked up on PATH on purpose; the arguments are fixed and there is no shell.
        listing = subprocess.run(
            ["ps", "-A", "-o", "pid=,command="],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    waits = {}
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and WAIT_COMMAND.search(command):
            waits[int(pid)] = command
    return waits


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # The process exists but belongs to someone else.
    except OSError:
        return False
    return True


def run_hook(args: argparse.Namespace) -> int:
    # Another tool that runs Claude sessions on the user's behalf sets this, so Tempo's
    # hooks stay quiet in those sessions.
    if os.environ.get("TEMPO_IN_THREAD") == "1":
        log.write("hook", args.event, "-", "quiet: TEMPO_IN_THREAD")
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd = project_folder(payload)
    if args.event == "stop":
        reason, project, outcome = stop_outcome(payload, cwd)
        log.write("hook", args.event, project, outcome)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    # A hook must never block or break the session over a Tempo problem: exit code 2
    # would discard the user's prompt. Errors go to stderr, which Claude Code does not
    # pass to the session when the hook exits 0.
    page = ""
    try:
        if args.event == "session-start" and not page_on():
            page = ", page off"
        elif args.event == "session-start":
            # Only requested: when two sessions start at once, both start a server, and the
            # one that finds the port taken stops at once.
            page = ", page start requested" if start_server(args.port) else ", page left running"
    except Exception as error:
        page = ", page not started"
        print("tempo: the page did not start: %s" % error, file=sys.stderr)
    project = "-"
    note = restart_note(payload.get("session_id"))
    try:
        override = setting("PROJECT")
        project = override or project_name(cwd, record=True)
        # The restart line goes out with the rest, in the same capped text.
        room = HOOK_TEXT_LIMIT - len(note) - 1 if note else HOOK_TEXT_LIMIT
        if args.event == "session-start":
            path = None if override else folder_path(cwd)
            text = session_note(project, args.port, path, limit=room)
            outcome = "note sent" + page
        else:
            text, delivered = deliver_inbox(project, limit=room)
            outcome = "delivered %d" % delivered if delivered else "nothing new"
    except Exception as error:
        log.write("hook", args.event, project, "error: %s" % error)
        print("tempo: hook skipped: %s" % error, file=sys.stderr)
        # The restart line still goes out when the rest of the hook fails.
        text, outcome = "", ""
    if outcome:
        log.write("hook", args.event, project, outcome)
    text = "\n".join(part for part in (note, text) if part)
    if text:
        event = "SessionStart" if args.event == "session-start" else "UserPromptSubmit"
        print(
            json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
        )
    return 0


def session_note(project: str, port: int, path: str | None = None, limit: int | None = None) -> str:
    """What every session is told at its start, so questions go through Tempo even when
    the skill isn't picked and nothing about Tempo is in CLAUDE.md. When the session's
    folder (at path) may be a known one renamed or moved, Claude is asked to say so.
    With the page turned off, questions are pointed to in the terminal instead.

    The open questions are listed while the text stays within limit characters; a last
    line counts the ones left out and says how to see them."""
    with Store(connect()) as store:
        on = store.start_page()
        questions = store.list(project, "open")
        rename = next((r for r in store.renames() if path and r["path"] == path), None)
    if on:
        where = "at http://127.0.0.1:%d/" % port
    else:
        where = (
            "in the terminal with `tempo show N` and `tempo answer N <letter>`. The page is"
            " turned off, so never give page links"
        )
    lines = [
        "Tempo is installed: the user reads and answers questions %s." % where,
        "- Every choice or decision for the user goes in Tempo with `tempo add` (see the tempo"
        " skill for the format), never only in chat. That includes yes-or-no confirmations,"
        " requests for permission and 'say whether…' items in pending steps.",
        "- Give every question a goal with `--goal`: the piece of work its answer moves"
        " forward, named the same way across questions (e.g. 'Release 0.4')."
        ' Mark one question in a goal `--start-here "unlocks …"` only when its answer'
        " really unlocks or sets the pattern for the others.",
        "- End every update with the open questions of this project, one line each: %s"
        % (
            "the number as a link to the question, then the title, as in"
            " `- [Q3](http://127.0.0.1:2204/#/shop/3) Pick a colour`. Never print a bare link."
            if on
            else "the number, then the title, as in `- Q3 Pick a colour`."
        ),
        "- An answer in Tempo never approves merging, publishing or installing:"
        " record the question in Tempo and also ask for a one-line OK in chat.",
    ]
    if rename:
        lines.append(rename_note(rename, on))
    if questions:
        lines.append("Open questions in %s:" % project)
        listed = []
        for q in questions:
            goal = " [%s%s]" % (q["goal"], ", start here" if q["start_here"] else "")
            listed.append(
                "- %s %s%s %s"
                % (q["label"], q["title"], goal if q["goal"] else "", _pointer(q, on))
            )
        lines = _fitting(lines, listed, limit, MORE_QUESTIONS)[0]
    return "\n".join(lines)


# What a hook hands Claude at once, in characters. Claude Code caps a hook's
# additionalContext (and its plain output) at 10,000 characters: beyond that, Claude gets
# only a file path and a preview of the first 2,000. The Stop hook's reason has no
# documented cap, so it is held to the same one. The rest is a margin.
HOOK_TEXT_LIMIT = 9000
MORE_QUESTIONS = "- … and %d more open questions: run `tempo list` to see them all."
MORE_NEWS = (
    "%d more answers or follow-ups from the user are waiting: they come with the next"
    " message, or run `tempo inbox` to read them now."
)
CUT_SHORT = " … (cut short: `tempo show` with its number shows it all)"


def _fitting(
    head: list[str], items: list[str], limit: int | None, more: str, keep_first: bool = False
) -> tuple[list[str], int]:
    """The head lines, then the items that fit after them within limit characters, then
    a line made from more that counts the items left out. Returns the lines and how many
    items they hold; with no limit, every item. With keep_first, the first item goes in
    even when it is too long alone, cut short to fit."""
    if limit is None:
        return [*head, *items], len(items)
    lines = list(head)
    used = len("\n".join(head))
    for index, item in enumerate(items):
        # The "N more" line must still fit after this item, unless it is the last one.
        left = len(items) - index - 1
        room = limit - used - 1 - (len(more % left) + 1 if left else 0)
        if len(item) > room:
            if index or not keep_first:
                return [*lines, more % (len(items) - index)], index
            item = item[: max(room - len(CUT_SHORT), 0)] + CUT_SHORT
        lines.append(item)
        used += len(item) + 1
    return lines, len(items)


def restart_note(session_id: object) -> str:
    """One line asking for a restart when the plugin installed since this session started
    is newer than the code its hooks run. Said once per session, and never by code that
    runs from a git checkout or worktree. Any problem means no line."""
    from tempo import server

    try:
        if not isinstance(session_id, str) or not session_id:
            return ""
        if os.path.exists(os.path.join(CODE_ROOT, ".git")):
            return ""
        ours, installed = server.version(), installed_version()
        if not ours or not installed or not _older(ours, installed):
            return ""
        # The sessions already told, one id per line, next to the database.
        marker = os.path.join(os.path.dirname(_db_path()), "restart-noted")
        told: list[str] = []
        with contextlib.suppress(FileNotFoundError), open(marker, encoding="utf-8") as file:
            told = file.read().split()
        if session_id in told:
            return ""
        # The last hundred sessions are plenty, so the file stays small.
        with open(marker, "w", encoding="utf-8") as file:
            file.write("\n".join([*told[-99:], session_id]) + "\n")
    except Exception as error:
        print("tempo: version check skipped: %s" % error, file=sys.stderr)
        return ""
    return (
        "Tempo in this session is %s but %s is installed: tell the user to restart this"
        " session (`claude --continue`) to use it." % (ours, installed)
    )


def installed_version() -> str | None:
    """The highest version of Tempo that Claude Code lists as installed, or None when the
    list is missing or not in a shape this code knows."""
    try:
        with open(os.path.expanduser(INSTALLED_PLUGINS), encoding="utf-8") as file:
            entries = json.load(file)["plugins"]["tempo@tempo"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return None
    best: str | None = None
    for entry in entries:
        version = entry.get("version") if isinstance(entry, dict) else None
        if isinstance(version, str) and _older(best, version):
            best = version
    return best


def rename_note(rename: dict[str, Any], page: bool = True) -> str:
    """Asks Claude to tell the user that this new folder may be a renamed or moved one.
    Only the page can link it, so with the page off the user is told how to start it."""
    candidates = rename["candidates"]
    shows = "shows" if page else "(started with `tempo serve --background`) shows"
    where = "This folder, `%s` (%s), is new to Tempo" % (rename["folder"], rename["shown"])
    if len(candidates) == 1:
        old = candidates[0]
        return (
            "- %s, and %s, filed under %s, no longer exists, so it looks renamed or moved."
            ' Tell the user in chat: the Tempo page %s a banner with a "Link to %s" button,'
            " or they can keep this folder as its own project. Until they pick, its questions"
            " go to the project %s."
            % (where, old["shown"], old["project"], shows, old["project"], rename["project"])
        )
    gone = ", ".join(str(old["shown"]) for old in candidates)
    return (
        "- %s, and several known folders no longer exist (%s), so it may be one of them"
        " renamed or moved. Tell the user in chat: the Tempo page %s a banner with a"
        ' "Link to <project>" button for each missing folder, or they can keep this folder'
        " as its own project. Until they pick, its questions go to the project %s."
        % (where, gone, shows, rename["project"])
    )


def stop_outcome(payload: dict[str, Any], cwd: str) -> tuple[str | None, str, str]:
    """Why the reply that just ended must not stop yet (or None), with the project and
    a short outcome for the log.

    In order: the user's new answers and follow-ups (handed over and marked delivered),
    a question asked only in chat, follow-ups Claude was given a while ago and has not
    answered, then open questions with no `tempo wait` running to bring the answers
    back. Blocks at most once per reply (stop_hook_active), and any
    error lets the reply end, so the hook can never trap a session.
    """
    if payload.get("stop_hook_active"):
        return None, "-", "let through: already sent back once"
    project = "-"
    try:
        project = setting("PROJECT") or project_name(cwd)
        if not project:
            # A folder with no name, such as "/", belongs to no project.
            return None, "-", "let through: no project"
        inbox, delivered = deliver_inbox(project, limit=HOOK_TEXT_LIMIT)
        if inbox:
            return inbox, project, "delivered %d" % delivered
        page = page_on()
        unrecorded = unrecorded_options(payload, page)
        if unrecorded:
            return unrecorded, project, "blocked: unrecorded question"
        with Store(connect()) as store:
            cutoff = datetime.now(timezone.utc) - FOLLOW_UP_GRACE
            unanswered = store.unanswered_follow_ups(project, cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if unanswered:
                reason = unanswered_note(store, unanswered, page)
                return reason, project, "blocked: unanswered follow-ups %d" % len(unanswered)
            questions = store.list(project, "open")
        if questions and not wait_running(project):
            reason = (
                "%s has open questions in Tempo, but no `tempo wait` is running, so answers"
                "%s would not reach you until the user writes in chat. Start"
                " `tempo wait --timeout 1800` with the Bash tool's run_in_background option,"
                " then end the reply." % (project, " from the page" if page else "")
            )
            return reason, project, "blocked: no wait"
    except Exception as error:
        return None, project, "error: %s" % error
    return None, project, "let through"


# How long Claude has to answer a follow-up it was given before the Stop hook raises it.
FOLLOW_UP_GRACE = timedelta(minutes=5)


def unanswered_note(store: Store, entries: Entries, page: bool = True) -> str:
    """Sends Claude back to answer follow-ups it was given but left without a reply."""
    lines = ["The user asked for more on these questions and has had no answer yet:"]
    for entry in entries:
        question = store.get(entry["project"], entry["number"])
        # Rewording reopens an answered question, so its reply goes in the thread.
        how = "context %d" if question["status"] == "answered" else "reword %d --context"
        lines.append(
            "- %s %s (%s) asked: %s Answer it with `tempo %s`."
            % (
                entry["project"],
                question["label"],
                question["title"],
                entry["body"],
                how % entry["number"],
            )
        )
    lines.append(
        "Answer each in the question itself, so %s shows it, then end the reply."
        % ("the box the user reads on the page" if page else "`tempo show`")
    )
    return "\n".join(lines)


# Lettered options: "(a) …" and "(b) …" in a sentence, or one per line or list item, as
# "(a) …", "- a) …" or "**A.** …". A "(b)" after "and" lists facts, not a choice.
OPTION = re.compile(
    r"(?:^|(?<!\band)\s)\(([a-e])\)\s"
    r"|^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?\(?([a-eA-E])[).](?:\*\*)?[ \t]",
    re.MULTILINE,
)
# A question's words up to its "?": a dot counts when it isn't ending a sentence, as in
# v0.5.4, README.md or "e.g. ".
QUESTION = r"(?:[^.!?\n]|\.(?=\S)|(?<=e\.g|i\.e)\.)*\?"
# A "?" Claude answers at once ("…? No, …", "…? Because …") is not asked of the user.
UNANSWERED = r"(?![ \t]*(?:yes|no|nope|not|because)\b)"
# A yes-or-no question, a choice or a request for permission, starting a sentence or a
# list item, or a request to say which.
ASK = re.compile(
    r"(?:^|[.!?:]\s+|^\s*[-*]\s+)(?:(?:may|can|could|should|shall) (?:i|we)"
    r"|do you (?:want|prefer)|would you (?:like|prefer)|want me to|ok(?:ay)? to"
    r"|is it ok(?:ay)? (?:if|to)|which (?:one |option )?(?:do|would|should) (?:you|i|we))\b"
    + QUESTION
    + UNANSWERED
    + r"|\b(?:go ahead|proceed|ok(?:ay)?)\?"
    r"|\b(?:say|tell me|let me know|confirm) (?:whether|if|which)"
    r" (?:(?:i|we) (?:should|can|may)|you(?:'d)? (?:want|like|prefer)|one|option)\b"
    r"|\byes or no\b|\breply (?:yes|no|ok)\b|\(y(?:es)?/n(?:o)?\)",
    re.IGNORECASE | re.MULTILINE,
)
# What doesn't count as asking: code, quoted lines and quoted phrases.
QUOTED = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`|^\s*>.*$|\"[^\"\n]*\"|“[^”\n]*”", re.S | re.M)
# A list item or table row naming a question and its link, as in the closing list of open
# questions: "- Q3 Title http://127.0.0.1:2204/#/…", "1. **Q3** Title http://…" or
# "| Q3 | Title | http://… |".
LISTED = re.compile(r"^\s*(?:[-*]|\d+\.|\|)\s.*\bQ\d+\b.*127\.0\.0\.1:\d+/#/.*$", re.MULTILINE)
TEMPO_LINK = re.compile(r"127\.0\.0\.1:\d+/#/")
# With the page off there are no links: a question is named by its number, as in "Q3",
# and the closing list is any list item or table row naming one.
LISTED_NUMBER = re.compile(r"^\s*(?:[-*]|\d+\.|\|)\s.*\bQ\d+\b.*$", re.MULTILINE)
TEMPO_NUMBER = re.compile(r"\bQ\d+\b")
# A Bash command that runs `tempo add`, `reword` or `context`: first, or after `&&` or
# `;`, with settings or a path before it, as in `cd x && TEMPO_PROJECT=y bin/tempo add`.
TEMPO_RUN = re.compile(
    r"(?:^|&&|\|\||;|\n)\s*(?:\w+=\S*\s+)*(?:\S*/)?(?:tempo|oq) (?:add|reword|context)\b"
)
PARAGRAPH = re.compile(r"\n[ \t]*\n")
LINKED_NUMBER = "its number linked to it, as in [Q5](http://127.0.0.1:2204/#/…)"


def unrecorded_options(payload: dict[str, Any], page: bool = True) -> str | None:
    """Why the reply that just ended must not stop yet, or None.

    A reply that asks the user something (lettered options, a yes-or-no question or a
    request for permission) is a question asked only in chat, unless the reply ran
    `tempo add`, `tempo reword` or `tempo context` with the Bash tool and it worked, or
    links a Tempo question next to what it asks: in the same paragraph, or for options
    in the one before (a closing list of open questions doesn't count). Code and quoted
    text never count as asking. Runs once
    per reply: when Claude was already sent back (stop_hook_active), it lets the reply
    end. Any problem reading the transcript lets it end too, so the check can never trap
    a session. With the page off, naming the question's number (Q5) counts as its link.
    """
    if payload.get("stop_hook_active"):
        return None
    try:
        text, commands = _last_reply(payload["transcript_path"])
    except (KeyError, OSError, ValueError, TypeError, AttributeError):
        return None
    if any(TEMPO_RUN.search(command) for command in commands):
        return None
    paragraphs = PARAGRAPH.split(QUOTED.sub(" ", text))
    link, listed = (TEMPO_LINK, LISTED) if page else (TEMPO_NUMBER, LISTED_NUMBER)
    linked = [bool(link.search(listed.sub("", p))) for p in paragraphs]
    asks = any(ASK.search(p) and not linked[i] for i, p in enumerate(paragraphs))
    found = [_letters(p) for p in paragraphs]
    if {"a", "b"} <= set().union(*found):
        first = next(i for i, letters in enumerate(found) if letters)
        asks = asks or not any(linked[max(0, first - 1) : first + 1])
    if asks:
        return (
            "This reply asks the user to decide something (lettered options, a yes-or-no"
            " question or a request for permission), but it is not in Tempo. Add it with"
            " `tempo add` (see the tempo skill; a yes-or-no question gets the options Yes"
            " and No), then give the question in full with %s. If it is already in"
            " Tempo, give %s in the body of the reply. Merging, publishing and"
            " installing still also need a one-line OK in chat."
            % (
                (LINKED_NUMBER, "its number as a link")
                if page
                else ("its number", "its number (such as Q5)")
            )
        )
    return None


def _letters(paragraph: str) -> set[str]:
    return {(inline or listed).lower() for inline, listed in OPTION.findall(paragraph)}


def _last_reply(path: str) -> tuple[str, list[str]]:
    """What Claude wrote since the user's last message, and the Bash commands it ran
    that did not fail."""
    texts: list[str] = []
    commands: dict[str, str] = {}
    failed: set[str] = set()
    with open(path, encoding="utf-8") as transcript:
        for line in transcript:
            entry = json.loads(line)
            content = (entry.get("message") or {}).get("content")
            if entry.get("type") == "user" and not _tool_results(content):
                texts, commands, failed = [], {}, set()
            elif entry.get("type") == "user" and isinstance(content, list):
                failed |= {block.get("tool_use_id") for block in content if block.get("is_error")}
            elif entry.get("type") == "assistant" and isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use" and block.get("name") == "Bash":
                        command = (block.get("input") or {}).get("command")
                        commands[block.get("id") or str(len(commands))] = str(command)
    return "\n".join(texts), [c for key, c in commands.items() if key not in failed]


def _tool_results(content: Any) -> bool:
    return isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def run_serve(args: argparse.Namespace) -> int | None:
    if args.background:
        started = start_server(args.port)
        print(
            "Serving on http://127.0.0.1:%d/%s"
            % (args.port, "" if started else " (already running)")
        )
        return 0
    from tempo.server import serve

    serve(args.port)
    return None


# Shared


def take_inbox(project: str | None) -> str:
    """Return the user's undelivered answers and follow-ups as text, marking them seen."""
    return deliver_inbox(project)[0]


def deliver_inbox(project: str | None, limit: int | None = None) -> tuple[str, int]:
    """take_inbox, with how many answers and follow-ups it handed over. With a limit, it
    hands over only the entries whose text fits in that many characters (always at least
    one) and leaves the rest undelivered, for the next hook or `tempo inbox`."""
    # The text is built before the entries are marked delivered, so an error while
    # building it leaves them for the next hook instead of losing them.
    with Store(connect()) as store, store.delivering(project) as entries:
        text, kept = _describe_inbox(store, entries, limit)
        # Only the entries still in the list are marked delivered.
        del entries[kept:]
        return text, kept


def _describe_inbox(store: Store, entries: Entries, limit: int | None = None) -> tuple[str, int]:
    """The text that hands the entries to Claude, and how many of them it holds: all of
    them, or with a limit, those that fit in that many characters. The first one always
    goes in, cut short if it is too long alone, so a long note never blocks the rest."""
    if not entries:
        return "", 0
    # One wording wherever the answer was given: the page, the terminal screen or a command.
    if store.start_page():
        lines = ["New from the user in Tempo (http://127.0.0.1:%d/):" % _port()]
    else:
        lines = ["New from the user in Tempo:"]
    for entry in entries:
        question = store.get(entry["project"], entry["number"])
        version = question["versions"][entry["version"] - 1]
        heading = "%s %s (%s)" % (entry["project"], question["label"], version["title"])
        if entry["kind"] == "answer":
            earlier = [
                e for e in question["entries"] if e["kind"] == "answer" and e["id"] < entry["id"]
            ]
            verb = "changed the answer to" if earlier else "answered"
            line = "- %s %s %s" % (heading, verb, _describe_answer(question, entry))
            if earlier:
                line += "\n  Before: %s" % _describe_answer(question, earlier[-1])
            if "?" in (entry["body"] or ""):
                # Rewording reopens an answered question, so its reply goes in the thread.
                how = (
                    "`tempo context %d`" % entry["number"]
                    if question["status"] == "answered"
                    else "`tempo reword %d --context`" % entry["number"]
                )
                line += "\n  The note asks something: answer it in the question with %s." % how
        else:
            line = "- %s asks for more: %s" % (heading, entry["body"])
        lines.append(line)
    follow_up = (
        "Answer each follow-up in the question itself with `tempo reword --context`, so"
        " the box the user reads shows it; use a subagent when it needs research."
    )
    # The follow-up line goes last, so the entries' share of the limit leaves room for it.
    room = None if limit is None else limit - len(follow_up) - 1
    lines, kept = _fitting(lines[:1], lines[1:], room, MORE_NEWS, keep_first=True)
    if any(entry["kind"] == "followup" for entry in entries[:kept]):
        lines.append(follow_up)
    return "\n".join(lines), kept


def answered_label(question: Question) -> str:
    """The label of a question just answered, naming the version answered once Claude has
    reworded it: `tempo answer` answers the current wording, which the user may not have
    seen, so the confirmation says which one it was."""
    version = question["answer"]["version"]
    return "%s (version %d)" % (question["label"], version) if version > 1 else question["label"]


def _describe_answer(question: Question, entry: Entry) -> str:
    parts: list[str] = []
    if entry["choice"]:
        version = question["versions"][entry["version"] - 1]
        # An option missing from its version (saved by an older bug) shows just its letter.
        option = next((o for o in version["options"] if o["letter"] == entry["choice"]), None)
        parts.append(
            "(%s) %s" % (entry["choice"], option["text"]) if option else "(%s)" % entry["choice"]
        )
    if entry["body"]:
        parts.append("Note: %s" % entry["body"] if parts else entry["body"])
    return " / ".join(parts)


def describe_goal(question: Question) -> str:
    text = "goal: %s" % question["goal"] if question["goal"] else "no goal"
    if question["start_here"]:
        text += "; start here: %s" % question["start_here"]
    return text


def open_for(created_at: str) -> str:
    """How long a question has been open, in the largest whole unit: "open 3 days"."""
    created = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    minutes = max(1, int((datetime.now(timezone.utc) - created).total_seconds() // 60))
    for size, unit in ((24 * 60, "day"), (60, "hour"), (1, "minute")):
        if minutes >= size:
            amount = minutes // size
            return "open %d %s%s" % (amount, unit, "" if amount == 1 else "s")
    return "open 1 minute"


def format_question(question: Question) -> str:
    facts = [question["status"], "version %d" % question["version"], describe_goal(question)]
    if question["status"] == "open":
        facts += ["quick"] if question["quick"] else []
        facts.append(open_for(question["created_at"]))
    lines = [
        "%s %s (%s)" % (question["project"], question["label"], ", ".join(facts)),
        question["title"],
        "",
        question["context"],
        "",
    ]
    for option in question["options"]:
        lines.append(
            "(%s) %s%s"
            % (
                option["letter"],
                option["text"],
                "  [recommended]" if option["letter"] == question["recommended"] else "",
            )
        )
    if question["entries"]:
        lines += ["", "Thread:"]
        for entry in question["entries"]:
            text = entry["body"]
            if entry["choice"]:
                text = "(%s) %s" % (entry["choice"], text)
            lines.append(
                "- %s %s %s (v%d): %s"
                % (entry["created_at"], entry["author"], entry["kind"], entry["version"], text)
            )
    return "\n".join(lines)


def start_server(port: int) -> bool:
    """Start the page in the background, replacing a Tempo server that is old or broken.

    A Tempo server of this version or a newer one is left running, whatever folder it runs
    from, and so is one on another database and anything on the port that cannot be shown
    to be Tempo.
    """
    from tempo.server import ROOT

    if _listening(port) and not _stop_stale(port):
        return False
    # The child gets its own copy of the log file handle, so ours can close. The
    # argument list is fixed and there is no shell, so nothing outside reaches it.
    with os.fdopen(log.open_log(), "a") as server_log:
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "tempo", "serve", "--port", str(port)],
            cwd=ROOT,
            stdout=server_log,
            stderr=server_log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return True


def _listening(port: int) -> bool:
    import socket

    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _stop_stale(port: int) -> bool:
    """Stop the Tempo server on the port if it is old or broken; True once the port is free."""
    from tempo import server

    try:
        health = json.loads(_fetch(port, "/health") or b"null")
    except ValueError:
        health = None
    if isinstance(health, dict) and health.get("app") == "tempo":
        # A Tempo on another database is someone else's page: never replaced, whatever
        # its version. One from before /health named its database (0.5 and older) is
        # taken to be on the real one, so only code on the real one replaces it.
        if (health.get("db") or default_db()) != db_path():
            return False
        if not _older(health.get("version"), server.version()):
            return False
        pid = health.get("pid")
    elif _fetch(port, "/") is not None:
        # The page works: an older Tempo without /health, or another program entirely.
        return False
    else:
        pid = _tempo_pid(port)
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False
    # A new server cannot bind the port until the old one has let go of it.
    for _ in range(20):
        if not _listening(port):
            return True
        time.sleep(0.1)
    return False


def _older(theirs: object, ours: str | None) -> bool:
    """Whether a running server's version is older than this code's. A missing or odd
    version counts as older; if this code's own version is unknown, nothing is."""

    def parts(version: object) -> tuple[int, ...] | None:
        try:
            return tuple(int(part) for part in str(version).split("."))
        except ValueError:
            return None

    mine = parts(ours) if ours else None
    if mine is None:
        return False
    running = parts(theirs) if theirs else None
    return running is None or running < mine


def _fetch(port: int, path: str) -> bytes | None:
    """The body of a GET on the local port, or None unless it answers 200 within a second."""
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.read() if response.status == 200 else None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def _tempo_pid(port: int) -> int | None:
    """The process listening on the port, but only if its command is `tempo serve`."""
    try:
        # lsof and ps are looked up on PATH on purpose. There is no shell, and the only
        # values passed in are the port number and a pid made of digits only.
        pids = subprocess.run(  # noqa: S603
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.split()
        if len(pids) != 1 or not pids[0].isdigit():
            return None
        command = subprocess.run(  # noqa: S603
            ["ps", "-o", "command=", "-p", pids[0]],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(pids[0]) if SERVE_COMMAND.search(command) else None


def project_folder(payload: dict[str, Any] | None = None) -> str:
    """The folder whose project a session is in: the one Claude Code started it in
    (CLAUDE_PROJECT_DIR, which Claude Code sets for hooks), else the hook input's cwd,
    else this process's. The cwd follows each `cd` Claude runs, so a session that `cd`s
    into another project would otherwise take that project's answers."""
    cwd = (payload or {}).get("cwd")
    return (
        os.environ.get("CLAUDE_PROJECT_DIR") or (cwd if isinstance(cwd, str) else "") or os.getcwd()
    )


def project_name(cwd: str, record: bool = False) -> str:
    """The project a folder is filed under, named after the folder until renamed.

    With record, the folder is remembered, so its project shows in Settings. Only a new
    git repo may be a renamed or moved one: a scratch folder outside git never is.
    """
    repo = repo_path(cwd)
    path = repo or os.path.realpath(cwd)
    folder = folder_name(path)
    with Store(connect()) as store:
        identity = (lambda: repo_identity(path)) if repo else None
        if record and store.see_folder(folder, path, identity) and repo:
            store.spot_rename(path, gone=lambda known: not os.path.exists(known))
        return store.project_for(folder, path)


def folder_path(cwd: str) -> str:
    """The full path of the git repo's top folder (the main checkout, not a worktree), or
    of cwd outside git. Symlinks are resolved, so one folder always has one path.

    Letter case is kept as given. On macOS a folder can be entered as Tempo or tempo, but
    Claude Code sends hooks the cwd as the system reports it (getcwd, and `pwd -P` after
    each Bash command), which is always the folder's own case, so it never splits a
    project in two."""
    return repo_path(cwd) or os.path.realpath(cwd)


def repo_path(cwd: str) -> str | None:
    """The full path of the git repo's top folder, or None outside git: the main checkout
    for a worktree, the bare repo itself (such as shop.git) for a worktree of a bare repo,
    and its own folder for a submodule."""
    try:
        common, top = _git(cwd, "rev-parse", "--git-common-dir", "--show-toplevel").splitlines()
        # Git answers relative to cwd (".git") or with a full path; join handles both.
        common = os.path.normpath(os.path.join(os.path.abspath(cwd), common))
        if os.path.basename(common) != ".git":
            # A bare repo, or a submodule's (or another separate) git folder.
            if _git(common, "rev-parse", "--is-bare-repository").strip() != "true":
                return os.path.realpath(top)
            # A hidden bare folder, as in repo/.bare, stands for the folder it is in.
            if not os.path.basename(common).startswith("."):
                return os.path.realpath(common)
        return os.path.realpath(os.path.dirname(common))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def repo_identity(repo: str) -> tuple[str | None, str | None]:
    """The repo's first commit and the address of its origin remote, either None when
    git cannot tell (no commit yet, no remote, or no git). A renamed copy keeps both, so
    they tell a renamed repo from an unrelated one. Merged histories can have several
    first commits; with --reverse the oldest comes first.

    A user name and password or token in the address (https://me:token@host/…) are
    left out, so they are never copied into the database."""
    first_commit = _git_line(repo, "rev-list", "--max-parents=0", "--reverse", "HEAD")
    remote = _git_line(repo, "config", "--get", "remote.origin.url")
    if remote:
        remote = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@", r"\1", remote)
    return first_commit, remote


def _git_line(cwd: str, *args: str) -> str | None:
    """The first line git prints, or None when it fails or prints nothing."""
    try:
        lines = _git(cwd, *args).splitlines()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    return (lines[0].strip() or None) if lines else None


def _git(cwd: str, *args: str) -> str:
    # git is looked up on PATH on purpose. There is no shell, and every caller passes
    # fixed arguments.
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def folder_name(path: str) -> str:
    """A folder's name, without the .git a bare repo's folder ends with."""
    name = os.path.basename(path)
    return name[: -len(".git")] if name.endswith(".git") and name != ".git" else name


def _project(value: str | None, record: bool = False) -> str:
    return value or setting("PROJECT") or project_name(os.getcwd(), record)


def _number(value: str) -> int:
    return int(value.upper().lstrip("Q"))


def _code(value: str) -> int:
    """The question number in a short code such as "12b", the way the user names an answer."""
    return _number(value.rstrip(string.ascii_letters) or value)


def _port() -> int:
    """TEMPO_PORT if set; else, for code on a branch database, a port of its own, so its
    page never takes the place of the real one; else 2204."""
    chosen = setting("PORT")
    if chosen:
        return int(chosen)
    branch = None if setting("DB") else branch_db()
    if branch:
        return BRANCH_PORTS[zlib.crc32(branch.encode()) % len(BRANCH_PORTS)]
    return DEFAULT_PORT


def _db_path() -> str:
    path = db_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    return path


_opened: list[Store] = []


def _store() -> Store:
    """Open the store; main() closes every store opened while running a command."""
    store = Store(connect())
    _opened.append(store)
    return store


def page_on() -> bool:
    """Whether sessions start the page (the user's `tempo config page` setting). Any
    problem reading it counts as on, which is how Tempo worked before the setting."""
    try:
        with Store(connect()) as store:
            return store.start_page()
    except Exception as error:
        print("tempo: page setting not read: %s" % error, file=sys.stderr)
        return True


def _pointer(question: Question, page: bool | None = None) -> str:
    """Where the user finds a question: its link on the page, or with the page off, the
    commands that show and answer it in the terminal."""
    if page is None:
        page = page_on()
    if page:
        return _url(question)
    return "`tempo show %d`, answer with `tempo answer %d <letter>`" % (
        question["number"],
        question["number"],
    )


def _url(question: Question) -> str:
    from urllib.parse import quote

    return "http://127.0.0.1:%d/#/%s/%d" % (_port(), quote(question["project"]), question["number"])
