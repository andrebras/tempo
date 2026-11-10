"""Shells the page opens in a project's folder, so the user doesn't have to switch to their
terminal app.

Each shell is the user's own login shell in a pseudo-terminal (PTY), so their prompt,
aliases and PATH load, and full-screen programs such as claude work. The page reads its
output as one long response (Server-Sent Events) and sends keys and new sizes as POSTs;
none of it touches the database. A shell lasts as long as the page reads its output: when
that response ends, because the tab closed or the server stops, the shell is stopped too.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import secrets
import signal
import struct
import subprocess
import sys
import termios
import threading
from collections.abc import Iterator

from tempo.store import QuestionError

# How long a shell may wait for the page to start reading its output before it is stopped,
# so a page that opened one and went away leaves nothing running.
ATTACH = 30.0
# How often a quiet shell's output says it is still there, so a page that went away is
# noticed (the write fails) and its shell stopped.
PING = 15.0
# Output kept for a page that has not read it yet; older output is dropped.
KEEP = 1_000_000
# More shells than any one person needs; a runaway page cannot start hundreds.
MOST = 32
# Run in the new shell's process before the shell itself. Popen makes the process the
# leader of a new session; this makes the PTY its controlling terminal, so Ctrl-C and job
# control work, then becomes the shell. A separate process, because forking this
# threaded server to run Python in the child is not safe.
LAUNCH = (
    "import fcntl, os, sys, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0);"
    " os.execvp(sys.argv[1], sys.argv[1:])"
)


class Shell:
    """One login shell in a PTY, and the output the page has not read yet."""

    def __init__(self, folder: str) -> None:
        shell = os.environ.get("SHELL") or "/bin/sh"
        env = dict(os.environ, TERM="xterm-256color")
        # Set when a Claude session started the server; claude would refuse to start in it.
        env.pop("CLAUDECODE", None)
        self.fd, child = pty.openpty()
        try:
            # Running the user's own shell is the whole point; the argument list is fixed.
            self.process = subprocess.Popen(  # noqa: S603
                [sys.executable, "-I", "-c", LAUNCH, shell, "-l"],
                stdin=child,
                stdout=child,
                stderr=child,
                cwd=folder,
                env=env,
                start_new_session=True,
            )
        except BaseException:
            os.close(self.fd)
            raise
        finally:
            os.close(child)
        self.pending = bytearray()
        self.ended = False
        self.watched = False
        self.changed = threading.Condition()
        # Held to use or close the PTY, so a key is never written to a closed (or reused)
        # file descriptor. Reading needs no lock: only the reader closes it.
        self.using = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        """Collect the shell's output until it ends, then free the PTY."""
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:  # EIO once the shell and everything it started has gone
                data = b""
            with self.changed:
                if not data:
                    self.ended = True
                    self.changed.notify_all()
                    break
                self.pending += data
                del self.pending[:-KEEP]
                self.changed.notify_all()
        with self.using:
            os.close(self.fd)

    def output(self) -> Iterator[bytes | None]:
        """The output as it comes, or None after PING seconds without any; it stops when
        the shell ends. Only one reader: a second is refused."""
        with self.changed:
            if self.watched:
                raise QuestionError("This terminal is already shown in another tab")
            self.watched = True
        return self._chunks()

    def _chunks(self) -> Iterator[bytes | None]:
        while True:
            with self.changed:
                if not self.pending and not self.ended:
                    self.changed.wait(PING)
                data, ended = bytes(self.pending), self.ended
                self.pending.clear()
            if data:
                yield data
            elif ended:
                return
            else:
                yield None

    def write(self, data: bytes) -> None:
        with self.using, contextlib.suppress(OSError):
            if not self.ended:
                os.write(self.fd, data)

    def resize(self, cols: int, rows: int) -> None:
        with self.using, contextlib.suppress(OSError):
            if not self.ended:
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def stop(self) -> None:
        """Hang up on the shell, as closing a terminal window does; kill it if it stays."""
        for sign in (signal.SIGHUP, signal.SIGKILL):
            with contextlib.suppress(OSError):
                os.killpg(self.process.pid, sign)
            try:
                self.process.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                continue


class Shells:
    """The open shells, by a key no other page can guess."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.shells: dict[str, Shell] = {}

    def open(self, folder: str) -> str:
        if not os.path.isdir(folder):
            raise QuestionError("The folder %s no longer exists" % folder)
        with self.lock:
            if len(self.shells) >= MOST:
                raise QuestionError("Too many terminals are open; end one first")
            key = secrets.token_urlsafe(16)
            self.shells[key] = shell = Shell(folder)
        timer = threading.Timer(ATTACH, self._unread, (key, shell))
        timer.daemon = True
        timer.start()
        return key

    def get(self, key: str) -> Shell | None:
        with self.lock:
            return self.shells.get(key)

    def close(self, key: str) -> None:
        with self.lock:
            shell = self.shells.pop(key, None)
        if shell:
            shell.stop()

    def close_all(self) -> None:
        with self.lock:
            keys = list(self.shells)
        for key in keys:
            self.close(key)

    def _unread(self, key: str, shell: Shell) -> None:
        if not shell.watched:
            self.close(key)


SHELLS = Shells()
