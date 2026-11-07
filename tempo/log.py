"""A one-line record of each change request to the page and of each hook run, in
server.log next to the database. A line says only what happened (time, request or
hook, result), never a request body or the text of a question or answer."""

from __future__ import annotations

import contextlib
import os
import time

from tempo.store import db_path

# Past this size the log moves to server.log.1 (replacing an older one) and starts again.
MAX_BYTES = 1_000_000


def log_path() -> str:
    return os.path.join(os.path.dirname(db_path()), "server.log")


def open_log() -> int:
    """The log file, opened to append; created private, as errors in it may quote questions."""
    path = log_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        if os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)


def write(*fields: object) -> None:
    """Append one line: the UTC time, then the fields separated by spaces. Never raises,
    so a log problem can never break a request or a hook."""
    with contextlib.suppress(Exception):
        text = " ".join(str(field) for field in fields).replace("\n", " ")[:500]
        line = "%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), text)
        file = open_log()
        try:
            # One write per line, so lines from the server and hooks never interleave.
            os.write(file, line.encode())
        finally:
            os.close(file)
