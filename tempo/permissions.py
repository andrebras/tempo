"""Permission requests from a thread's background Claude session, answered on the page.

`claude -p` cannot show a permission prompt. Instead, a thread's session is started with
`--permission-prompt-tool mcp__tempo_threads__approve`: when a tool call needs approval, Claude
Code calls the `approve` tool of this small MCP (Model Context Protocol) server, which
Claude Code runs as a child process and talks to over stdin and stdout, one JSON-RPC
message per line.

The tool gets `tool_name`, `input` and `tool_use_id`. It records the request in the
thread, waits for Allow or Deny on the page, and replies with JSON text:
{"behavior": "allow", "updatedInput": <input>} or {"behavior": "deny", "message": ...}.
With no answer within TIMEOUT, the request is denied.

Run as: python -m tempo.permissions <thread id>, with TEMPO_DB naming the database.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from typing import IO, Any

from tempo import threads
from tempo.store import now

SERVER, TOOL = threads.PERMISSION_SERVER, "approve"
# Under Claude Code's 30-minute idle limit for a silent MCP call, so Tempo's deny wins.
TIMEOUT = 10 * 60
POLL = 1.0

TOOL_INFO = {
    "name": TOOL,
    "description": "Ask the user, on the Tempo page, whether a tool call may run.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
    },
}


def ask(thread_id: int, tool: str, tool_input: dict[str, Any], timeout: float = TIMEOUT) -> dict:
    """Record a request in the thread and wait for the user's answer."""
    # One connection for the whole wait, closed at the end, as the wait polls it often.
    with contextlib.closing(threads.open_db()) as db:
        request_id = db.execute(
            "INSERT INTO thread_permissions (thread_id, tool, summary, created_at)"
            " VALUES (?, ?, ?, ?)",
            (thread_id, tool, threads.summary(tool_input), now()),
        ).lastrowid
        deadline = time.monotonic() + timeout
        while True:
            status = db.execute(
                "SELECT status FROM thread_permissions WHERE id = ?", (request_id,)
            ).fetchone()["status"]
            if status == "pending" and time.monotonic() >= deadline:
                db.execute(
                    "UPDATE thread_permissions SET status = 'expired'"
                    " WHERE id = ? AND status = 'pending'",
                    (request_id,),
                )
                status = "expired"
            if status == "allowed":
                return {"behavior": "allow", "updatedInput": tool_input}
            if status in ("denied", "expired"):
                words = "The user denied this" if status == "denied" else "No answer came in time"
                return {"behavior": "deny", "message": words + " on the Tempo page."}
            time.sleep(POLL)


def serve(thread_id: int, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    """Answer Claude Code's MCP messages until it closes stdin. Each tool call waits in
    its own thread, so several requests can be pending at once."""
    lock = threading.Lock()

    def send(message: dict[str, Any]) -> None:
        with lock:
            stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            stdout.flush()

    def call(message_id: Any, arguments: dict[str, Any]) -> None:
        tool_input = arguments.get("input")
        answer = ask(
            thread_id,
            str(arguments.get("tool_name") or "?"),
            tool_input if isinstance(tool_input, dict) else {},
        )
        content = [{"type": "text", "text": json.dumps(answer)}]
        send({"id": message_id, "result": {"content": content}})

    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method, message_id = message.get("method"), message.get("id")
        if message_id is None:
            continue  # a notification, such as notifications/initialized
        if method == "initialize":
            version = (message.get("params") or {}).get("protocolVersion", "2024-11-05")
            info = {"name": SERVER, "version": "1"}
            result = {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": info}
            send({"id": message_id, "result": result})
        elif method == "tools/list":
            send({"id": message_id, "result": {"tools": [TOOL_INFO]}})
        elif method == "tools/call" and (message.get("params") or {}).get("name") == TOOL:
            arguments = message["params"].get("arguments") or {}
            threading.Thread(target=call, args=(message_id, arguments), daemon=True).start()
        elif method == "ping":
            send({"id": message_id, "result": {}})
        else:
            send({"id": message_id, "error": {"code": -32601, "message": "Unknown method"}})


if __name__ == "__main__":
    serve(int(sys.argv[1]))
