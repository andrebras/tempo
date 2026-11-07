"""Conversations that start on the Tempo page, an early feature that is off by default.

Each thread is one Claude Code session, run headless with `claude -p` in a project
folder and resumed for every follow-up, so threads never mix. Only Claude's final
reply is kept: the tool calls and logs stay in the session, out of the thread.
TEMPO_THREADS=1 turns the feature on.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
from typing import Any

from tempo.store import QuestionError, connect, now, setting

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  id INTEGER PRIMARY KEY,
  folder TEXT NOT NULL,
  title TEXT NOT NULL,
  session_id TEXT,
  status TEXT NOT NULL DEFAULT 'working' CHECK (status IN ('working', 'waiting', 'failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_messages (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES threads(id),
  author TEXT NOT NULL CHECK (author IN ('user', 'claude')),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

# What every thread's session is told, on top of Claude Code's own instructions.
RULES = (
    "You are answering inside a Tempo thread: a page where the user keeps each topic in"
    " its own conversation. Your final message is the only thing the user sees, so make"
    " it the result, not a log: short, plain sentences, no list of the commands you ran."
    " If you need the user to decide something, end with one clear question. Decide"
    " yourself whether the work is big enough for subagents; their output stays out of"
    " the thread too."
)

# Headless sessions cannot ask for permission, so tools outside this mode are refused.
PERMISSION_MODE = setting("THREADS_PERMISSION_MODE") or "acceptEdits"
TIMEOUT = 30 * 60
# How much of Claude's error output a failed run shows.
ERROR_LENGTH = 500


def enabled() -> bool:
    """Threads are off unless TEMPO_THREADS=1 is set where the Tempo server starts."""
    return setting("THREADS") == "1"


def open_db() -> sqlite3.Connection:
    db = connect()
    db.executescript(SCHEMA)
    return db


def threads() -> list[dict[str, Any]]:
    with open_db() as db:
        rows = db.execute(
            "SELECT t.*, (SELECT body FROM thread_messages m WHERE m.thread_id = t.id"
            " ORDER BY m.id DESC LIMIT 1) AS last FROM threads t ORDER BY t.updated_at DESC"
        )
        return [dict(row) for row in rows]


def thread(thread_id: int) -> dict[str, Any]:
    with open_db() as db:
        row = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if not row:
            raise QuestionError("There is no thread %d" % thread_id)
        messages = db.execute(
            "SELECT id, author, body, created_at FROM thread_messages"
            " WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        )
        return dict(row, messages=[dict(message) for message in messages])


def start(folder: str, text: str) -> dict[str, Any]:
    """Open a thread with the user's first message and set Claude to work on it."""
    text, folder = _required(text, "message"), os.path.expanduser(_required(folder, "folder"))
    if not os.path.isdir(folder):
        raise QuestionError("There is no folder %s" % folder)
    title = text.splitlines()[0][:80]
    with open_db() as db:
        thread_id = db.execute(
            "INSERT INTO threads (folder, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (folder, title, now(), now()),
        ).lastrowid
    return _send(int(thread_id or 0), text)


def reply(thread_id: int, text: str) -> dict[str, Any]:
    """Add the user's follow-up to a thread; Claude carries on in the same session."""
    if thread(thread_id)["status"] == "working":
        raise QuestionError("Claude is still working on this thread")
    return _send(thread_id, _required(text, "message"))


def _send(thread_id: int, text: str) -> dict[str, Any]:
    with open_db() as db:
        db.execute(
            "INSERT INTO thread_messages (thread_id, author, body, created_at)"
            " VALUES (?, 'user', ?, ?)",
            (thread_id, text, now()),
        )
        db.execute(
            "UPDATE threads SET status = 'working', updated_at = ? WHERE id = ?",
            (now(), thread_id),
        )
    current = thread(thread_id)
    threading.Thread(
        target=_run, args=(thread_id, current["folder"], current["session_id"], text), daemon=True
    ).start()
    return current


def _run(thread_id: int, folder: str, session_id: str | None, text: str) -> None:
    """Run Claude on one message and keep only its final reply."""
    claude = shutil.which("claude") or "claude"
    command = [claude, "-p", text, "--output-format", "stream-json", "--verbose"]
    command += ["--permission-mode", PERMISSION_MODE]
    command += ["--append-system-prompt", RULES]
    if session_id:
        command += ["--resume", session_id]
    try:
        # The argument list is fixed apart from the user's own message; there is no shell.
        done = subprocess.run(  # noqa: S603
            command, cwd=folder, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
        result = read_stream(done.stdout)
        if result is None:
            raise ValueError((done.stderr or "no result").strip()[-ERROR_LENGTH:])
        body, status = result.get("result") or "(no reply)", "waiting"
        if result.get("is_error"):
            status = "failed"
        session_id = result.get("session_id") or session_id
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        body, status = "Claude could not run: %s" % error, "failed"
    with open_db() as db:
        db.execute(
            "INSERT INTO thread_messages (thread_id, author, body, created_at)"
            " VALUES (?, 'claude', ?, ?)",
            (thread_id, body, now()),
        )
        db.execute(
            "UPDATE threads SET status = ?, session_id = ?, updated_at = ? WHERE id = ?",
            (status, session_id, now(), thread_id),
        )


def read_stream(output: str) -> dict[str, Any] | None:
    """Read `claude -p --output-format stream-json` output, one JSON event per line, and
    return the final result event."""
    result: dict[str, Any] | None = None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    return result


def _required(value: Any, name: str) -> str:
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value:
        raise QuestionError("A thread needs a %s" % name)
    return value
