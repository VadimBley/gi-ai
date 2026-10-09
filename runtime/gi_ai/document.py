# Copyright (C) 2026 Vadim Bley
# This file is part of Ĝi (gi-ai).
#
# Ĝi is free software: you can redistribute it and/or modify it under the terms of the
# GNU Affero General Public License as published by the Free Software Foundation, version 3.
#
# Ĝi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
# the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along with Ĝi.
# If not, see <https://www.gnu.org/licenses/>.

"""A document for gi ask: a file the user names, or text piped in on standard input.

The file is checked by name, then by lstat, then again on the opened descriptor, so a path
that changes in between cannot reach another file. Nothing is ever written.
"""

from __future__ import annotations

import errno
import os
import select
import stat
import time

from gi_ai.llm import strip_controls

# The descriptor read as standard input (tests point it at a pipe of their own).
STDIN_FD = 0
READ_CHUNK = 65536
MAX_SHOWN_PATH = 200
BOM = b"\xef\xbb\xbf"
SYSTEM_DIRS = ("/proc", "/sys", "/dev")

HIDDEN = "hidden or instruction file"
SYSTEM = "secret or system file"
NOT_REGULAR = "not a regular file"
LINK = "symbolic link"


class DocumentError(Exception):
    """A document that is refused; the message is shown after "gi ask: "."""


def hidden_or_instruction(path: str) -> bool:
    """A hidden component (other than . and ..), or an upper-case Markdown file name."""
    parts = path.split("/")
    if any(part not in ("", ".", "..") and part.startswith(".") for part in parts):
        return True
    name = parts[-1]
    if not name.lower().endswith(".md"):
        return False
    stem = name[:-3]
    return any(c.isupper() for c in stem) and not any(c.islower() for c in stem)


def shown(path: str) -> str:
    """A path as it may be printed: undecodable bytes as \\xNN, no control sequences, cut."""
    text = os.fsencode(path).decode("utf-8", "backslashreplace")
    return strip_controls(text)[:MAX_SHOWN_PATH]


def _reason(exc: OSError) -> str:
    if exc.errno == errno.ENOENT:
        return "not found"
    if exc.errno in (errno.EACCES, errno.EPERM):
        return "permission denied"
    return "cannot read"


def _on_system_device(device: int) -> bool:
    """On the file system of /proc, /sys or /dev (only where that is a file system of its own)."""
    try:
        root = os.stat("/").st_dev
    except OSError:
        root = None
    for folder in SYSTEM_DIRS:
        try:
            dev = os.stat(folder).st_dev
        except OSError:
            continue
        if dev != root and dev == device:
            return True
    return False


def _under_system_dir(path: str) -> bool:
    """Below /proc, /sys or /dev by path (also mounts of their own there, such as /dev/shm)."""
    return any(path == folder or path.startswith(folder + "/") for folder in SYSTEM_DIRS)


def _is_token_file(info: os.stat_result, token_file: str | None) -> bool:
    if token_file is None:
        return False
    try:
        token = os.stat(os.path.expanduser(token_file))
    except OSError:
        return False
    return (token.st_dev, token.st_ino) == (info.st_dev, info.st_ino)


def _read(fd: int, limit: int, timeout: float) -> str:
    """At most 4 x limit + 4 bytes within `timeout` seconds, as UTF-8 text without a BOM."""
    cap = 4 * limit + 4
    deadline = time.monotonic() + timeout
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    chunks: list[bytes] = []
    total = 0
    while total < cap:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not poller.poll(max(1, int(remaining * 1000))):
            raise DocumentError("timed out reading the document")
        try:
            chunk = os.read(fd, min(READ_CHUNK, cap - total))
        except OSError:
            raise DocumentError("cannot read the document") from None
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    data = b"".join(chunks)
    if data.startswith(BOM):
        data = data[len(BOM) :]
    if len(data) > 4 * limit:
        raise DocumentError(f"document longer than {limit} characters")
    if b"\x00" in data:
        raise DocumentError("the document is not UTF-8 text")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentError("the document is not UTF-8 text") from None


def read_file(path: str, limit: int, timeout: float, token_file: str | None) -> str:
    """The text of the file `path` (gi ask --file PATH); DocumentError if it is refused."""
    expanded = os.path.expanduser(path)

    def refuse(reason: str) -> DocumentError:
        return DocumentError(f"cannot use {shown(path)}: {reason}")

    if hidden_or_instruction(expanded) or hidden_or_instruction(os.path.realpath(expanded)):
        raise refuse(HIDDEN)
    try:
        info = os.lstat(expanded)
    except OSError as exc:
        raise refuse(_reason(exc)) from None
    if _on_system_device(info.st_dev):
        raise refuse(SYSTEM)
    if stat.S_ISLNK(info.st_mode):
        raise refuse(LINK)
    if not stat.S_ISREG(info.st_mode):
        raise refuse(NOT_REGULAR)  # a FIFO or device is never opened
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
    try:
        fd = os.open(expanded, flags)
    except OSError as exc:
        raise refuse(LINK if exc.errno == errno.ELOOP else _reason(exc)) from None
    try:
        # What was opened, not what the name said: a folder link swapped after the checks
        # above shows up here.
        try:
            real = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            raise refuse("cannot read") from None
        if hidden_or_instruction(real):
            raise refuse(HIDDEN)
        if _under_system_dir(real):
            raise refuse(SYSTEM)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            info.st_dev,
            info.st_ino,
        ):
            raise refuse(NOT_REGULAR)
        if _on_system_device(opened.st_dev) or _is_token_file(opened, token_file):
            raise refuse(SYSTEM)
        return _read(fd, limit, timeout)
    finally:
        os.close(fd)


def read_stdin(explicit: bool, limit: int, timeout: float) -> str | None:
    """Standard input as text. Without --file -, only a pipe is read; otherwise None."""
    try:
        mode = os.fstat(STDIN_FD).st_mode
    except OSError:
        mode = None
    if explicit:
        if mode is None or not (stat.S_ISFIFO(mode) or stat.S_ISREG(mode)):
            raise DocumentError("cannot use standard input: not a pipe or file")
    elif mode is None or not stat.S_ISFIFO(mode):
        return None
    return _read(STDIN_FD, limit, timeout)
