"""Conversations that start on the Tempo page, an early feature that is off by default.

Each thread is one Claude Code session, run headless with `claude -p` in a project
folder and resumed for every follow-up, so threads never mix. Claude's final reply is
the message; the tools it used are kept beside it as short "steps", which the page
shows only when asked. When a tool call needs approval, the request shows in the thread
with Allow and Deny (see tempo/permissions.py). TEMPO_THREADS=1 turns the feature on.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from typing import Any

from tempo.store import CODE_ROOT, QuestionError, connect, db_path, now, setting

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
  steps TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_permissions (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES threads(id),
  tool TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'allowed', 'denied', 'expired')),
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

# What runs without asking; anything else is put to the user in the thread.
PERMISSION_MODE = setting("THREADS_PERMISSION_MODE") or "acceptEdits"
TIMEOUT = 30 * 60
# How much of Claude's error output a failed run shows.
ERROR_LENGTH = 500
# How much of a step's input and result is kept: enough to see what happened.
SUMMARY_LENGTH, RESULT_LENGTH = 200, 500


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
            " ORDER BY m.id DESC LIMIT 1) AS last, (SELECT COUNT(*) FROM thread_permissions p"
            " WHERE p.thread_id = t.id AND p.status = 'pending') AS asking"
            " FROM threads t ORDER BY t.updated_at DESC"
        )
        return [dict(row) for row in rows]


def thread(thread_id: int) -> dict[str, Any]:
    with open_db() as db:
        row = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if not row:
            raise QuestionError("There is no thread %d" % thread_id)
        messages = db.execute(
            "SELECT id, author, body, steps, created_at FROM thread_messages"
            " WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        )
        pending = db.execute(
            "SELECT id, tool, summary, created_at FROM thread_permissions"
            " WHERE thread_id = ? AND status = 'pending' ORDER BY id",
            (thread_id,),
        )
        return dict(
            row,
            messages=[dict(message, steps=json.loads(message["steps"])) for message in messages],
            permissions=[dict(request) for request in pending],
        )


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


def decide(thread_id: int, request_id: int, allow: Any) -> dict[str, Any]:
    """Answer a pending permission request from the page with Allow or Deny."""
    with open_db() as db:
        changed = db.execute(
            "UPDATE thread_permissions SET status = ?"
            " WHERE id = ? AND thread_id = ? AND status = 'pending'",
            ("allowed" if allow is True else "denied", request_id, thread_id),
        ).rowcount
    if not changed:
        raise QuestionError("That request is no longer waiting for an answer")
    return thread(thread_id)


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
    """Run Claude on one message and keep its final reply and the steps it took."""
    claude = shutil.which("claude") or "claude"
    command = [claude, "-p", text, "--output-format", "stream-json", "--verbose"]
    command += ["--permission-mode", PERMISSION_MODE]
    command += ["--append-system-prompt", RULES]
    command += ["--mcp-config", _permission_server(thread_id)]
    command += ["--permission-prompt-tool", PERMISSION_TOOL]
    if session_id:
        command += ["--resume", session_id]
    steps: list[dict[str, Any]] = []
    try:
        # The argument list is fixed apart from the user's own message; there is no shell.
        done = subprocess.run(  # noqa: S603
            command, cwd=folder, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
        result, steps = read_stream(done.stdout)
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
            "INSERT INTO thread_messages (thread_id, author, body, steps, created_at)"
            " VALUES (?, 'claude', ?, ?, ?)",
            (thread_id, body, json.dumps(steps), now()),
        )
        db.execute(
            "UPDATE threads SET status = ?, session_id = ?, updated_at = ? WHERE id = ?",
            (status, session_id, now(), thread_id),
        )
        # A request left from a run that ended can no longer be answered.
        db.execute(
            "UPDATE thread_permissions SET status = 'expired'"
            " WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        )


# The MCP server that puts permission requests to the user; see tempo/permissions.py.
PERMISSION_SERVER = "tempo_threads"
PERMISSION_TOOL = "mcp__%s__approve" % PERMISSION_SERVER


def _permission_server(thread_id: int) -> str:
    """The --mcp-config JSON that starts tempo/permissions.py for this thread. It runs in
    the project folder, so it is told where the code and the database are."""
    server = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "tempo.permissions", str(thread_id)],
        "env": {"PYTHONPATH": CODE_ROOT, "TEMPO_DB": db_path()},
    }
    return json.dumps({"mcpServers": {PERMISSION_SERVER: server}})


def read_stream(output: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Read `claude -p --output-format stream-json` output: one JSON event per line.

    Returns the final result event and the main conversation's tool calls, each with a
    short summary of its input and the start of its result. Subagents' own steps are
    left out, like their messages."""
    result: dict[str, Any] | None = None
    steps: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result = event
        if event.get("type") not in ("assistant", "user") or event.get("parent_tool_use_id"):
            continue
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                steps[block.get("id")] = {
                    "tool": block.get("name") or "?",
                    "input": summary(block.get("input") or {}),
                    "result": "",
                    "error": False,
                }
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in steps:
                step = steps[block["tool_use_id"]]
                step["result"] = _trim(_text(block.get("content")), RESULT_LENGTH)
                step["error"] = bool(block.get("is_error"))
    return result, list(steps.values())


def summary(tool_input: dict[str, Any]) -> str:
    """The one input that says what a tool call did, else the whole input as JSON."""
    for key in ("command", "file_path", "pattern", "url", "query", "description", "prompt"):
        if isinstance(tool_input.get(key), str):
            return _trim(tool_input[key], SUMMARY_LENGTH)
    return _trim(json.dumps(tool_input), SUMMARY_LENGTH)


def _text(content: Any) -> str:
    """A tool result is either text or a list of blocks, of which only text is kept."""
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def _trim(text: str, length: int) -> str:
    text = text.strip()
    return text if len(text) <= length else text[: length - 1] + "…"


def _required(value: Any, name: str) -> str:
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value:
        raise QuestionError("A thread needs a %s" % name)
    return value
