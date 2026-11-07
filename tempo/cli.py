"""The `tempo` command: how Claude adds, rewords and reads questions, and how hooks
hand the user's answers back to Claude."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from tempo.store import (
    Entry,
    Question,
    QuestionError,
    Store,
    connect,
    db_path,
    setting,
)

DEFAULT_PORT = 2204


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

    show = commands.add_parser("show", help="Show a question with its whole thread")
    show.add_argument("number", type=_number)
    show.add_argument("--project")
    show.add_argument("--json", action="store_true")
    show.set_defaults(run=run_show)

    list_ = commands.add_parser("list", help="List questions (open by default)")
    list_.add_argument("--project")
    list_.add_argument("--all-projects", action="store_true")
    list_.add_argument("--status", default="open", choices=["open", "answered", "archived", "all"])
    list_.add_argument("--json", action="store_true")
    list_.set_defaults(run=run_list)

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

    inbox = commands.add_parser("inbox", help="Print answers and follow-ups not yet seen")
    inbox.add_argument("--project")
    inbox.add_argument("--all-projects", action="store_true")
    inbox.set_defaults(run=run_inbox)

    wait = commands.add_parser(
        "wait", help="Block until the user answers or follows up, print it, then exit"
    )
    wait.add_argument("--project")
    wait.add_argument("--all-projects", action="store_true")
    wait.add_argument("--timeout", type=int, default=0, help="Seconds; 0 waits forever")
    wait.add_argument("--interval", type=float, default=2.0)
    wait.set_defaults(run=run_wait)

    hook = commands.add_parser("hook", help="Claude Code hook entry point (reads JSON on stdin)")
    hook.add_argument("event", choices=["session-start", "user-prompt-submit", "stop"])
    hook.add_argument("--port", type=int, default=_port())
    hook.set_defaults(run=run_hook)

    serve = commands.add_parser("serve", help="Run the web page")
    serve.add_argument("--port", type=int, default=_port())
    serve.add_argument(
        "--background",
        action="store_true",
        help="Start detached; does nothing if the port is already serving",
    )
    serve.set_defaults(run=run_serve)

    return parser


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
        print("Added %s in %s: %s" % (question["label"], question["project"], _url(question)))


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
    text = take_inbox(None if args.all_projects else _project(args.project))
    print(text or "Nothing new from the user.")


def run_wait(args: argparse.Namespace) -> int:
    project = None if args.all_projects else _project(args.project)
    deadline = time.time() + args.timeout if args.timeout else None
    # A marker tells the Stop hook that a wait is running for this project.
    marker = os.path.join(os.path.dirname(_db_path()), "wait-%d.json" % os.getpid())
    with open(marker, "w", encoding="utf-8") as file:
        json.dump({"pid": os.getpid(), "project": project}, file)
    try:
        while True:
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


def wait_running(project: str) -> bool:
    """Whether a `tempo wait` covering the project is running. Markers left by waits
    that were killed are removed."""
    folder = os.path.dirname(_db_path())
    running = False
    for name in os.listdir(folder):
        if not (name.startswith("wait-") and name.endswith(".json")):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as file:
                marker = json.load(file)
            alive = _alive(int(marker["pid"]))
        except (OSError, ValueError, KeyError, TypeError):
            alive = False
        if alive:
            running = running or marker.get("project") in (None, project)
        else:
            with contextlib.suppress(OSError):
                os.remove(path)
    return running


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # The process exists but belongs to someone else.
    except OSError:
        return False
    return True


def run_hook(args: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    if args.event == "stop":
        reason = stop_reason(payload, cwd)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    project = setting("PROJECT") or project_name(cwd, record=True)
    if args.event == "session-start":
        start_server(args.port)
        text = session_note(project, args.port)
    else:
        text = take_inbox(project)
    if text:
        event = "SessionStart" if args.event == "session-start" else "UserPromptSubmit"
        print(
            json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})
        )
    return 0


def session_note(project: str, port: int) -> str:
    """What every session is told at its start, so questions go through Tempo even when
    the skill isn't picked and nothing about Tempo is in CLAUDE.md."""
    link = "http://127.0.0.1:%d/" % port
    lines = [
        "Tempo is installed: the user reads and answers questions at %s." % link,
        "- Every choice or decision for the user goes in Tempo with `tempo add` (see the tempo"
        " skill for the format), never only in chat. That includes yes-or-no confirmations,"
        " requests for permission and 'say whether…' items in pending steps.",
        "- Give every question a goal with `--goal`: the piece of work its answer moves"
        " forward, named the same way across questions (e.g. 'Release 0.4')."
        ' Mark one question in a goal `--start-here "unlocks …"` only when its answer'
        " really unlocks or sets the pattern for the others.",
        "- End every update with the open questions of this project, one line each:"
        " number, title and link.",
        "- An answer on the Tempo page never approves merging, publishing or installing:"
        " record the question in Tempo and also ask for a one-line OK in chat.",
    ]
    with Store(connect()) as store:
        questions = store.list(project, "open")
    if questions:
        lines.append("Open questions in %s:" % project)
        for q in questions:
            goal = " [%s%s]" % (q["goal"], ", start here" if q["start_here"] else "")
            lines.append(
                "- %s %s%s %s" % (q["label"], q["title"], goal if q["goal"] else "", _url(q))
            )
    return "\n".join(lines)


def stop_reason(payload: dict[str, Any], cwd: str) -> str | None:
    """Why the reply that just ended must not stop yet, or None.

    In order: the user's new answers and follow-ups (handed over and marked delivered),
    a question asked only in chat, then open questions with no `tempo wait` running to
    bring the answers back. Blocks at most once per reply (stop_hook_active), and any
    error lets the reply end, so the hook can never trap a session.
    """
    if payload.get("stop_hook_active"):
        return None
    try:
        project = setting("PROJECT") or project_name(cwd)
        inbox = take_inbox(project)
        if inbox:
            return inbox
        unrecorded = unrecorded_options(payload)
        if unrecorded:
            return unrecorded
        with Store(connect()) as store:
            questions = store.list(project, "open")
        if questions and not wait_running(project):
            return (
                "%s has open questions in Tempo, but no `tempo wait` is running, so answers"
                " from the page would not reach you until the user writes in chat. Start"
                " `tempo wait --timeout 1800` with the Bash tool's run_in_background option,"
                " then end the reply." % project
            )
    except Exception:
        return None
    return None


# Lettered options such as "(a) …" and "(b) …" on separate items of one reply.
OPTION = re.compile(r"(?:^|\s)\(([a-e])\)\s", re.MULTILINE)
# A yes-or-no question or a request for permission, starting a sentence or a list item.
ASK = re.compile(
    r"(?:^|[.!?:]\s+|^\s*[-*]\s+)(?:(?:may|can|could|should|shall) (?:i|we)"
    r"|do you want|would you like|want me to|ok(?:ay)? to)\b[^.!?\n]*\?"
    r"|\byes or no\b|\breply (?:yes|no|ok)\b|\(y(?:es)?/n(?:o)?\)",
    re.IGNORECASE | re.MULTILINE,
)
# What doesn't count as asking: code, quoted lines and quoted phrases.
QUOTED = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`|^\s*>.*$|\"[^\"\n]*\"|“[^”\n]*”", re.S | re.M)
# A list item naming a question and its link, as in the closing list of open questions:
# "- Q3 Title http://127.0.0.1:2204/#/…" or "- **Q3** Title [Goal] http://…".
LISTED = re.compile(r"^\s*[-*]\s.*\bQ\d+\b.*127\.0\.0\.1:\d+/#/.*$", re.MULTILINE)
TEMPO_LINK = re.compile(r"127\.0\.0\.1:\d+/#/")
TEMPO_RUN = re.compile(r"\b(?:tempo|oq) (?:add|reword|context)\b")


def unrecorded_options(payload: dict[str, Any]) -> str | None:
    """Why the reply that just ended must not stop yet, or None.

    A reply that asks the user something (lettered options, a yes-or-no question or a
    request for permission) is a question asked only in chat, unless the reply ran
    `tempo add`, `tempo reword` or `tempo context`, or links a Tempo question outside the
    closing list of open questions. Code and quoted text never count as asking. Runs once
    per reply: when Claude was already sent back (stop_hook_active), it lets the reply
    end. Any problem reading the transcript lets it end too, so the check can never trap
    a session.
    """
    if payload.get("stop_hook_active"):
        return None
    try:
        text, commands = _last_reply(payload["transcript_path"])
    except (KeyError, OSError, ValueError, TypeError, AttributeError):
        return None
    prose = QUOTED.sub(" ", text)
    asks = {"a", "b"} <= set(OPTION.findall(prose)) or ASK.search(prose)
    recorded = TEMPO_RUN.search(commands) or TEMPO_LINK.search(LISTED.sub("", prose))
    if asks and not recorded:
        return (
            "This reply asks the user to decide something (lettered options, a yes-or-no"
            " question or a request for permission), but it is not in Tempo. Add it with"
            " `tempo add` (see the tempo skill; a yes-or-no question gets the options Yes"
            " and No), then give the question in full with its link. If it is already in"
            " Tempo, give its link in the body of the reply. Merging, publishing and"
            " installing still also need a one-line OK in chat."
        )
    return None


def _last_reply(path: str) -> tuple[str, str]:
    """What Claude wrote, and the tool inputs it ran, since the user's last message."""
    texts: list[str] = []
    commands: list[str] = []
    with open(path, encoding="utf-8") as transcript:
        for line in transcript:
            entry = json.loads(line)
            content = (entry.get("message") or {}).get("content")
            if entry.get("type") == "user" and not _tool_results(content):
                texts, commands = [], []
            elif entry.get("type") == "assistant" and isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        commands.append(json.dumps(block.get("input", {})))
    return "\n".join(texts), "\n".join(commands)


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
    with Store(connect()) as store:
        return _take_inbox(store, project)


def _take_inbox(store: Store, project: str | None) -> str:
    entries = store.undelivered(project)
    if not entries:
        return ""
    lines = ["New from the user on the Tempo page (http://127.0.0.1:%d/):" % _port()]
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
    if any(entry["kind"] == "followup" for entry in entries):
        lines.append(
            "Answer each follow-up in the question itself with `tempo reword --context`, so"
            " the box the user reads shows it; use a subagent when it needs research."
        )
    store.mark_delivered([entry["id"] for entry in entries])
    return "\n".join(lines)


def _describe_answer(question: Question, entry: Entry) -> str:
    parts: list[str] = []
    if entry["choice"]:
        version = question["versions"][entry["version"] - 1]
        option = next(o for o in version["options"] if o["letter"] == entry["choice"])
        parts.append("(%s) %s" % (entry["choice"], option["text"]))
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
    """Start the page in the background unless something already listens on the port."""
    import socket

    with socket.socket() as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # The child gets its own copy of the log file handle, so ours can close. The
    # argument list is fixed and there is no shell, so nothing outside reaches it.
    with open(os.path.join(os.path.dirname(_db_path()), "server.log"), "a") as log:
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "tempo", "serve", "--port", str(port)],
            cwd=root,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return True


def project_name(cwd: str, record: bool = False) -> str:
    """The project a folder is filed under, named after the folder until renamed.

    With record, the folder is remembered, so its project shows in Settings.
    """
    folder = folder_name(cwd)
    with Store(connect()) as store:
        if record:
            store.see_folder(folder)
        return store.project_for(folder)


def folder_name(cwd: str) -> str:
    """The git repo's top folder name (the main checkout, not a worktree)."""
    try:
        # git is looked up on PATH on purpose; the arguments are fixed.
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        # Git answers relative to cwd (".git") or with a full path; join handles both.
        common = os.path.normpath(os.path.join(os.path.abspath(cwd), common))
        return os.path.basename(os.path.dirname(common))
    except (OSError, subprocess.CalledProcessError):
        return os.path.basename(os.path.abspath(cwd))


def _project(value: str | None, record: bool = False) -> str:
    return value or setting("PROJECT") or project_name(os.getcwd(), record)


def _number(value: str) -> int:
    return int(value.upper().lstrip("Q"))


def _port() -> int:
    return int(setting("PORT") or DEFAULT_PORT)


def _db_path() -> str:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


_opened: list[Store] = []


def _store() -> Store:
    """Open the store; main() closes every store opened while running a command."""
    store = Store(connect())
    _opened.append(store)
    return store


def _url(question: Question) -> str:
    from urllib.parse import quote

    return "http://127.0.0.1:%d/#/%s/%d" % (_port(), quote(question["project"]), question["number"])
