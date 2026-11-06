"""The `tempo` command: how Claude adds, rewords and reads questions, and how hooks
hand the user's answers back to Claude."""

import argparse
import json
import os
import subprocess
import sys
import time

from tempo.store import PRIORITIES, QuestionError, Store, connect

DEFAULT_PORT = 2204


def main(argv=None):
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


def build_parser():
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
        "--priority",
        default="soon",
        choices=PRIORITIES,
        help="now: work waits on it; soon: needed this week (default); later: no rush",
    )
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
    reword.add_argument("--project")
    reword.set_defaults(run=run_reword)

    priority = commands.add_parser("priority", help="Change how soon an answer is needed")
    priority.add_argument("number", type=_number)
    priority.add_argument("priority", choices=PRIORITIES)
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
    hook.add_argument("event", choices=["session-start", "user-prompt-submit"])
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


# Commands


def run_add(args):
    question = _store().add(
        _project(args.project),
        args.title,
        args.context,
        args.options,
        recommended=args.recommended,
        priority=args.priority,
        number=args.number,
    )
    if args.json:
        print(json.dumps(question, indent=2))
    else:
        print("Added %s in %s: %s" % (question["label"], question["project"], _url(question)))


def run_show(args):
    question = _store().get(_project(args.project), args.number)
    print(json.dumps(question, indent=2) if args.json else format_question(question))


def run_list(args):
    project = None if args.all_projects else _project(args.project)
    questions = _store().list(project, args.status)
    if args.json:
        print(json.dumps(questions, indent=2))
        return
    if not questions:
        print("No %s questions." % args.status)
    for question in questions:
        print(
            "%s%s %s [%s] %s %s"
            % (
                "" if project else question["project"] + " ",
                question["label"],
                question["status"],
                "v%d" % question["version"],
                question["priority"],
                question["title"],
            )
        )


def run_context(args):
    question = _store().add_context(_project(args.project), args.number, args.body)
    print("Added context to %s." % question["label"])


def run_reword(args):
    question = _store().reword(
        _project(args.project),
        args.number,
        args.note,
        title=args.title,
        context=args.context,
        options=args.options,
        recommended=args.recommended,
    )
    print("%s is now at version %d." % (question["label"], question["version"]))


def run_priority(args):
    question = _store().set_priority(_project(args.project), args.number, args.priority)
    print("%s priority: %s." % (question["label"], question["priority"]))


def run_archive(args):
    count = _store().archive_answered(_project(args.project))
    print("Archived %d answered question(s)." % count)


def run_set_next(args):
    project = _project(args.project)
    _store().set_next(project, args.number)
    print("The next question in %s will be Q%d." % (project, args.number))


def run_inbox(args):
    text = take_inbox(None if args.all_projects else _project(args.project))
    print(text or "Nothing new from the user.")


def run_wait(args):
    project = None if args.all_projects else _project(args.project)
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        text = take_inbox(project)
        if text:
            print(text)
            return 0
        if deadline and time.time() >= deadline:
            print("Nothing new from the user.")
            return 0
        time.sleep(args.interval)


def run_hook(args):
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    if args.event == "session-start":
        start_server(args.port)
        return 0
    text = take_inbox(os.environ.get("OQ_PROJECT") or project_name(cwd))
    if text:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": text,
                    }
                }
            )
        )
    return 0


def run_serve(args):
    if args.background:
        started = start_server(args.port)
        print(
            "Serving on http://127.0.0.1:%d/%s"
            % (args.port, "" if started else " (already running)")
        )
        return 0
    from tempo.server import serve

    serve(args.port)


# Shared


def take_inbox(project):
    """Return the user's undelivered answers and follow-ups as text, marking them seen."""
    with Store(connect()) as store:
        return _take_inbox(store, project)


def _take_inbox(store, project):
    entries = store.undelivered(project)
    if not entries:
        return ""
    lines = ["New from the user on the open questions page (http://127.0.0.1:%d/):" % _port()]
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
        else:
            line = "- %s asks for more: %s" % (heading, entry["body"])
        lines.append(line)
    if any(entry["kind"] == "followup" for entry in entries):
        lines.append(
            "For each follow-up, add context (`tempo context`) or reword the question"
            " (`tempo reword`); use a subagent when it needs research."
        )
    store.mark_delivered([entry["id"] for entry in entries])
    return "\n".join(lines)


def _describe_answer(question, entry):
    parts = []
    if entry["choice"]:
        version = question["versions"][entry["version"] - 1]
        option = next(o for o in version["options"] if o["letter"] == entry["choice"])
        parts.append("(%s) %s" % (entry["choice"], option["text"]))
    if entry["body"]:
        parts.append("Note: %s" % entry["body"] if parts else entry["body"])
    return " / ".join(parts)


def format_question(question):
    lines = [
        "%s %s (%s, version %d)%s"
        % (
            question["project"],
            question["label"],
            question["status"],
            question["version"],
            ", " + question["priority"],
        ),
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


def start_server(port):
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


def project_name(cwd):
    """The project is the git repo's top folder name (the main checkout, not a worktree)."""
    try:
        # git is looked up on PATH on purpose; the arguments are fixed.
        common = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return os.path.basename(os.path.dirname(common))
    except (OSError, subprocess.CalledProcessError):
        return os.path.basename(os.path.abspath(cwd))


def _project(value):
    return value or os.environ.get("OQ_PROJECT") or project_name(os.getcwd())


def _number(value):
    return int(value.upper().lstrip("Q"))


def _port():
    return int(os.environ.get("OQ_PORT", DEFAULT_PORT))


def _db_path():
    from tempo.store import DEFAULT_DB

    path = os.environ.get("OQ_DB") or DEFAULT_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


_opened = []


def _store():
    """Open the store; main() closes every store opened while running a command."""
    store = Store(connect())
    _opened.append(store)
    return store


def _url(question):
    from urllib.parse import quote

    return "http://127.0.0.1:%d/#/%s/%d" % (_port(), quote(question["project"]), question["number"])
