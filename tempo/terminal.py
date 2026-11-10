"""Shells the page opens in a project's folder, so the user doesn't have to switch to their
terminal app.

Each shell is the user's own login shell in a pseudo-terminal (PTY), so their prompt,
aliases and PATH load, and full-screen programs such as claude work. The page reads its
output as one long response (Server-Sent Events) and sends keys and new sizes as POSTs;
none of it touches the database. Each folder has one shell, and the page reads it only
while it is shown, so a hidden terminal holds no connection to the server.

With tmux installed, the shell runs in a tmux session named for the folder, and the PTY
runs only a tmux client attached to it: when the page stops reading, the client ends and
tmux keeps the shell, even through a server restart. Without tmux, the server keeps the
shell itself until it is ended, the terminal is turned off or the server stops, and keeps
its latest output for the page to read when it comes back.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import pty
import re
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
from collections.abc import Iterator

from tempo.store import QuestionError

# How often a quiet shell's output says it is still there, so a page that went away is
# noticed (the write fails) and its reader, or its tmux client, ends.
PING = 15.0
# The latest output, kept for a page that comes back to a shell; older output is dropped.
KEEP = 1_000_000
# More shells than any one person needs; a runaway page cannot start hundreds.
MOST = 32
# The tmux command, or None without tmux. It uses the user's own tmux server, so they can
# attach to a shell from their own terminal too, with `tmux attach -t <session>`.
_FOUND = shutil.which("tmux")
TMUX: list[str] | None = [_FOUND] if _FOUND else None
# How the name of each tmux session Tempo starts begins.
PREFIX = "tempo-"
# Run in the new shell's process before the shell itself. Popen makes the process the
# leader of a new session; this makes the PTY its controlling terminal, so Ctrl-C and job
# control work, then becomes the shell. A separate process, because forking this
# threaded server to run Python in the child is not safe.
LAUNCH = (
    "import fcntl, os, sys, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0);"
    " os.execvp(sys.argv[1], sys.argv[1:])"
)


def session_name(folder: str) -> str:
    """The folder's tmux session: its name, readable, then a short hash of its full path,
    so two folders with the same name get two sessions. tmux refuses dots and colons."""
    path = os.path.realpath(folder)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.basename(path)).strip("-") or "folder"
    return "%s%s-%s" % (PREFIX, name, hashlib.sha256(path.encode()).hexdigest()[:6])


def _environment() -> dict[str, str]:
    """The environment for a shell or tmux, without what would get in their way."""
    env = dict(os.environ, TERM="xterm-256color")
    # CLAUDECODE is set when a Claude session started the server, and claude would refuse to
    # start in it. TMUX and TMUX_PANE are set when the server started inside tmux, which
    # would then refuse to nest a client, and use that tmux server instead of the usual one.
    for name in ("CLAUDECODE", "TMUX", "TMUX_PANE"):
        env.pop(name, None)
    return env


def _tmux(*args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a tmux command and wait for it; None without tmux or when it hangs."""
    if not TMUX:
        return None
    try:
        # A fixed tmux command; the only names passed are made by session_name.
        return subprocess.run(  # noqa: S603
            [*TMUX, *args],
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def end_sessions() -> None:
    """End every tmux session Tempo started, and no other: only names session_name makes."""
    listed = _tmux("list-sessions", "-F", "#{session_name}")
    mine = re.compile(r"%s.+-[0-9a-f]{6}" % re.escape(PREFIX))
    for name in listed.stdout.split() if listed and listed.returncode == 0 else []:
        if mine.fullmatch(name):
            _tmux("kill-session", "-t", "=" + name)


class Shell:
    """One login shell in a PTY, or a tmux client attached to one, and its latest output."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.session = session_name(folder) if TMUX else None
        if TMUX and self.session:  # both or neither; mypy needs TMUX itself checked
            # Attach to the folder's session, or start it; tmux then runs the user's shell.
            command = [*TMUX, "-u", "new-session", "-A", "-s", self.session, "-c", folder]
        else:
            command = [os.environ.get("SHELL") or "/bin/sh", "-l"]
        self.fd, child = pty.openpty()
        try:
            # Running the user's own shell is the whole point; the argument list is fixed.
            self.process = subprocess.Popen(  # noqa: S603
                [sys.executable, "-I", "-c", LAUNCH, *command],
                stdin=child,
                stdout=child,
                stderr=child,
                cwd=folder,
                env=_environment(),
                start_new_session=True,
            )
        except BaseException:
            os.close(self.fd)
            raise
        finally:
            os.close(child)
        # The latest output, and how many bytes came before it, so a page that comes back
        # says how many it has and gets only the rest.
        self.kept = bytearray()
        self.start = 0
        self.ended = False
        # Counts the pages that started reading; only the latest one gets the output.
        self.reader = 0
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
                self.kept += data
                extra = len(self.kept) - KEEP
                if extra > 0:
                    del self.kept[:extra]
                    self.start += extra
                self.changed.notify_all()
        with self.using:
            os.close(self.fd)

    def output(self, since: int = 0) -> tuple[int, Iterator[bytes | None]]:
        """This reader's number, and the output after its first `since` bytes as it comes,
        or None after PING seconds without any. It stops when the shell ends or a newer
        reader starts: the same page coming back, or the terminal shown in another tab."""
        with self.changed:
            self.reader += 1
            self.changed.notify_all()
            return self.reader, self._chunks(self.reader, since)

    def _chunks(self, reader: int, at: int) -> Iterator[bytes | None]:
        while True:
            with self.changed:
                if reader == self.reader and at >= self._end() and not self.ended:
                    self.changed.wait(PING)
                if reader != self.reader:
                    return
                end = self._end()
                at = min(max(at, self.start), end)
                data, ended = bytes(self.kept[at - self.start :]), self.ended
                at = end
            if data:
                yield data
            elif ended:
                return
            else:
                yield None

    def _end(self) -> int:
        """How many bytes of output there have been."""
        return self.start + len(self.kept)

    def write(self, data: bytes) -> None:
        with self.using, contextlib.suppress(OSError):
            if not self.ended:
                os.write(self.fd, data)

    def resize(self, cols: int, rows: int) -> None:
        with self.using, contextlib.suppress(OSError):
            if not self.ended:
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def stop(self) -> None:
        """Hang up on the shell, as closing a terminal window does; kill it if it stays. With
        tmux, only the client stops: the session and its shell go on."""
        for sign in (signal.SIGHUP, signal.SIGKILL):
            with contextlib.suppress(OSError):
                os.killpg(self.process.pid, sign)
            try:
                self.process.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                continue


class Shells:
    """The open shells, one per folder, by a key no other page can guess."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.shells: dict[str, Shell] = {}

    def open(self, folder: str) -> tuple[str, Shell]:
        """The folder's shell and its key: the one running, or a new one."""
        if not os.path.isdir(folder):
            raise QuestionError("The folder %s no longer exists" % folder)
        with self.lock:
            for key, shell in list(self.shells.items()):
                if shell.folder == folder and not shell.ended:
                    return key, shell
                if shell.folder == folder:
                    del self.shells[key]
                    shell.stop()  # it has ended: this only collects its process
            if len(self.shells) >= MOST:
                raise QuestionError("Too many terminals are open; end one first")
            key = secrets.token_urlsafe(16)
            self.shells[key] = shell = Shell(folder)
        return key, shell

    def get(self, key: str) -> Shell | None:
        with self.lock:
            return self.shells.get(key)

    def left(self, key: str, shell: Shell, reader: int) -> None:
        """A page stopped reading. An ended shell goes. A tmux client goes too, unless a newer
        page reads it, as tmux keeps the shell. A shell without tmux waits for the page."""
        if shell.ended or (shell.session and shell.reader == reader):
            self.close(key)

    def close(self, key: str) -> None:
        """Stop the shell, or with tmux its client only."""
        with self.lock:
            shell = self.shells.pop(key, None)
        if shell:
            shell.stop()

    def end(self, key: str) -> None:
        """End shell: stop the shell, and end its tmux session."""
        shell = self.get(key)
        if shell and shell.session:
            _tmux("kill-session", "-t", "=" + shell.session)
        self.close(key)

    def close_all(self) -> None:
        """When the server stops: every shell stops, but tmux sessions go on."""
        with self.lock:
            keys = list(self.shells)
        for key in keys:
            self.close(key)

    def end_all(self) -> None:
        """When the terminal is turned off: every shell and every Tempo tmux session ends."""
        end_sessions()
        self.close_all()


SHELLS = Shells()
