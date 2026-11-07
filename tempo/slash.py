"""The user's slash commands in a Claude Code session: /tempo:list, /tempo:show,
/tempo:answer and /tempo:dive.

Each one is a skill (skills/<name>/SKILL.md) that runs `tempo slash <name>` before
Claude reads it, with the text typed after the command on stdin, so the user's words
never pass through a shell. What this prints becomes the skill's text, so Claude sees
what was recorded. Answers and follow-ups are written already delivered: the output
hands them to Claude, and the hooks must not hand them over again. On an error (an
unknown question or option) nothing is written, and the output says so and why, for
Claude to pass on.

Also here: reading an option the way the user names it, by letter or by its text, which
`tempo answer` uses too.
"""

from __future__ import annotations

import argparse
import os
import re
import string
import sys

from tempo.store import Option, Question, QuestionError, Store, connect, setting

# What the user may type after the command, before anything else: 57, Q57, q57.
NUMBER = re.compile(r"\s*q?(\d+)(.*)", re.IGNORECASE | re.DOTALL)
# A letter on its own after the number: "57a", "57 a", "57 a)", "57a) note".
LETTER = re.compile(r"\s*([a-z])(?:\)|(?=\s)|$)(.*)", re.IGNORECASE | re.DOTALL)
TRAILING = string.punctuation + string.whitespace
# The follow-up /tempo:dive records when the user types only the number.
DEFAULT_FOLLOW_UP = "Tell me more."


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    slash = commands.add_parser(
        "slash", help="Run by the plugin's slash commands (reads the typed text on stdin)"
    )
    slash.add_argument("name", choices=["list", "show", "answer", "dive"])
    slash.add_argument("--cwd", help="The session's project folder; defaults to this one")
    slash.set_defaults(run=run_slash)


def run_slash(args: argparse.Namespace) -> None:
    from tempo import cli

    project = setting("PROJECT") or cli.project_name(args.cwd or os.getcwd())
    typed = sys.stdin.read().strip()
    with Store(connect()) as store:
        try:
            print(COMMANDS[args.name](store, project, typed))
        except QuestionError as error:
            # Exiting 0 keeps the command going, so Claude tells the user; a failing exit
            # would stop it, and in some sessions (claude -p) nobody would see why.
            print("Nothing was recorded: %s." % str(error).rstrip("."))


def slash_list(store: Store, project: str, typed: str) -> str:
    from tempo import cli

    questions = store.list(project, "open")
    if not questions:
        return "No open questions in %s." % project
    lines = ["Open questions in %s:" % project]
    for question in questions:
        facts = [cli.open_for(question["created_at"])]
        if question["goal"]:
            facts.insert(0, "goal: %s" % question["goal"])
        lines.append("%s %s (%s)" % (question["label"], question["title"], ", ".join(facts)))
    return "\n".join(lines)


def slash_show(store: Store, project: str, typed: str) -> str:
    from tempo import cli

    number, _ = split_number(typed)
    return cli.format_question(store.get(project, number))


def slash_answer(store: Store, project: str, typed: str) -> str:
    from tempo import cli

    number, rest = split_number(typed)
    letter, note = split_answer(store.get(project, number), rest)
    question = store.answer(project, number, letter, note, delivered=True)
    return "Recorded your answer to %s in %s (%s): %s\n\n%s" % (
        question["label"],
        project,
        question["title"],
        cli._describe_answer(question, question["answer"]),
        cli.format_question(question),
    )


def slash_dive(store: Store, project: str, typed: str) -> str:
    from tempo import cli

    number, rest = split_number(typed)
    question = store.get(project, number)
    body = rest.strip()
    # Only a letter typed against the number ("57b") names an option: "57 a lot more"
    # is a follow-up that happens to start with "a".
    glued = LETTER.match(rest) if rest[:1].isalpha() else None
    option = _option(question, glued.group(1)) if glued else None
    if glued and option:
        body = "About (%s) %s: %s" % (
            option["letter"],
            option["text"],
            glued.group(2).strip() or DEFAULT_FOLLOW_UP,
        )
    question = store.follow_up(project, number, body or DEFAULT_FOLLOW_UP, delivered=True)
    return "Recorded your follow-up on %s in %s (%s): %s\n\n%s" % (
        question["label"],
        project,
        question["title"],
        question["entries"][-1]["body"],
        cli.format_question(question),
    )


COMMANDS = {"list": slash_list, "show": slash_show, "answer": slash_answer, "dive": slash_dive}


def split_number(typed: str) -> tuple[int, str]:
    """The question number at the start of the typed text, and the rest of it."""
    match = NUMBER.match(typed)
    if not match:
        raise QuestionError("Start with a question number, such as 57 or 57a")
    return int(match.group(1)), match.group(2)


def split_answer(question: Question, rest: str) -> tuple[str, str]:
    """The option letter and the note in what follows the number: a letter ("a",
    "a)"), or the start of an option's text ("yes keep it" on a Yes/No question). A valid
    letter wins over an option's text. Text that names no option is passed on as it was,
    so the store says the option does not exist."""
    letter = LETTER.match(rest)
    if letter and _option(question, letter.group(1)):
        return letter.group(1).lower(), letter.group(2).strip()
    found = [
        (option["letter"], rest.strip()[len(_text(option)) :])
        for option in question["options"]
        if _starts_with(rest.strip(), _text(option))
    ]
    if len(found) == 1:
        return found[0][0], found[0][1].lstrip(TRAILING).strip()
    if letter:
        return letter.group(1).lower(), letter.group(2).strip()
    if not rest.strip():
        raise QuestionError("Give the option's letter, such as %sa" % question["label"])
    word = rest.split()[0]
    return word, rest.strip()[len(word) :].strip()


def option_letter(question: Question, typed: str) -> str:
    """The letter of the option the user named for `tempo answer N <option>`: the letter
    itself, or the option's whole text in any case, without trailing punctuation, when
    exactly one option has that text. Anything else comes back as typed."""
    if _option(question, typed.strip()):
        return typed.strip().lower()
    wanted = typed.strip().rstrip(TRAILING).casefold()
    found = [o["letter"] for o in question["options"] if _text(o).casefold() == wanted]
    return found[0] if len(found) == 1 else typed


def _option(question: Question, letter: str) -> Option | None:
    return next((o for o in question["options"] if o["letter"] == letter.lower()), None)


def _text(option: Option) -> str:
    return option["text"].strip().rstrip(TRAILING)


def _starts_with(typed: str, text: str) -> bool:
    """Whether the typed words begin with the option's text, ending at a word's end."""
    head = typed[: len(text)]
    return (
        bool(text) and head.casefold() == text.casefold() and not typed[len(text) :][:1].isalnum()
    )
