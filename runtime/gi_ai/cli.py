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

"""Command line interface: gi <command>."""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

from gi_ai import DISPLAY_NAME, __version__, assets, document, tasks, workspace
from gi_ai.config import Config, ConfigError, endpoint_privacy, load, token_file_privacy
from gi_ai.contracts import validate
from gi_ai.llm import (
    ChatReply,
    EndpointRefused,
    LLMError,
    Message,
    make_backend,
    sanitize_output,
)

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

# The markers around the question and the document in the ask prompt; neither may carry its own.
MARKERS = ("<<<QUESTION", "QUESTION>>>", "<<<DOCUMENT", "DOCUMENT>>>")
MARKER_REMOVED = "[marker removed]"
_MARKS = frozenset({"Mn", "Mc", "Me"})
# Default_Ignorable_Code_Point ranges from Unicode 15.1.0 DerivedCoreProperties.txt, pinned so
# that every supported Python removes at least these, whatever its own Unicode tables say.
INVISIBLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
# Lone surrogates and private-use characters, pinned too (unchanged since Unicode 2.0).
SURROGATE_PRIVATE_RANGES = (
    (0xD800, 0xDFFF),
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)
_REMOVED_CATEGORIES = frozenset({"Cf", "Cn", "Cs", "Co"})
# Control characters (Cc) other than newline and tab, removed from a document.
_DOCUMENT_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

VERSION_TEXT = (
    f"gi-ai {__version__}\n"
    "Copyright (C) 2026 Vadim Bley\n"
    "License AGPL-3.0-only: GNU Affero General Public License version 3 "
    "<https://www.gnu.org/licenses/agpl-3.0.html>\n"
    "This is free software: you are free to change and redistribute it.\n"
    "There is NO WARRANTY, to the extent permitted by law.\n"
    "Source: https://github.com/VadimBley/gi-ai\n"
)


@functools.lru_cache(maxsize=4096)
def _invisible(char: str) -> bool:
    if unicodedata.category(char) in _REMOVED_CATEGORIES:
        return True
    code = ord(char)
    return any(low <= code <= high for low, high in INVISIBLE_RANGES + SURROGATE_PRIVATE_RANGES)


# Pieces that each start with an ASCII character (or the text's start). NFKC never joins a
# character with a following ASCII one (no composition has an ASCII second part, and an ASCII
# character is a starter that canonical ordering never moves), so the text may be normalised
# piece by piece, cut only before an ASCII character, with the same result as all at once.
_SAFE_PIECES = re.compile(r"[\x00-\x7f]*[^\x00-\x7f]*")


def normalise_question(question: str, limit: int | None = None, chunk_chars: int = 8192) -> str:
    """NFKC, then without invisible characters (format, unassigned, surrogate, private use,
    default-ignorable).

    With a limit, work stops once the kept text is longer than it: the caller only needs to
    know that it is too long, not the rest of a text that NFKC grew many times over. NFKC runs
    on pieces of about `chunk_chars` characters, so that stop comes early.
    """
    kept: list[str] = []

    def take(text: str) -> bool:
        for char in unicodedata.normalize("NFKC", text):
            if not _invisible(char):
                kept.append(char)
                if limit is not None and len(kept) > limit:
                    return False
        return True

    buffer: list[str] = []
    size = 0
    for match in _SAFE_PIECES.finditer(question):
        piece = match.group()
        if not piece:
            continue
        buffer.append(piece)
        size += len(piece)
        if size >= chunk_chars:
            if not take("".join(buffer)):
                return "".join(kept)
            buffer, size = [], 0
    if buffer:
        take("".join(buffer))
    return "".join(kept)


def _base(char: str) -> str | None:
    """The upper-cased base letter of a character that is a letter plus combining marks."""
    parts = unicodedata.normalize("NFD", char)
    if any(unicodedata.category(m) not in _MARKS for m in parts[1:]):
        return None
    return parts[0].upper()


def _match_end(bases: list[str | None], marks: list[bool], start: int, marker: str) -> int:
    """End of `marker` matched at `start` (marks after each marker character included), or -1."""
    pos = start
    for wanted in marker:
        if pos >= len(bases) or bases[pos] != wanted:
            return -1
        pos += 1
        while pos < len(marks) and marks[pos]:
            pos += 1
    return pos


def neutralise_markers(question: str) -> str:
    """Replace marker text, matched across letter case and combining marks, in one pass."""
    bases = [_base(c) for c in question]
    marks = [unicodedata.category(c) in _MARKS for c in question]
    out: list[str] = []
    pos = 0
    while pos < len(question):
        end = max(_match_end(bases, marks, pos, marker) for marker in MARKERS)
        if end > pos:
            out.append(MARKER_REMOVED)
            pos = end
        else:
            out.append(question[pos])
            pos += 1
    return "".join(out)


def _print(obj: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            print(f"{k}: {v}")
    else:
        print(obj)


def cmd_health(cfg: Config, args: argparse.Namespace) -> int:
    fields = make_backend(cfg.llm).health_fields()
    reachable = fields.get("reachable") is True
    llm_report: dict[str, object] = {
        "backend": cfg.llm.backend,
        "model": cfg.llm.model,
        "reachable": reachable,
        "endpoint": cfg.llm.endpoint,
    }
    llm_report.update({k: v for k, v in fields.items() if k not in llm_report})
    ws = cfg.workspace
    report = {
        "name": DISPLAY_NAME,
        "version": __version__,
        "status": "ok" if reachable else "degraded",
        "llm": llm_report,
        "workspace": {"path": str(ws), "exists": (ws / workspace.AUDIT_FILE).exists()},
    }
    validate(report, assets.load_json("schemas", "health.schema.json"))
    _print(report, args.json)
    return EXIT_OK if reachable else EXIT_FAIL


def _count(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# (stats key, label, formatter) in the order they are printed.
_STATS_FORMAT = (
    ("input_tokens", "in", _count),
    ("total_output_tokens", "out", _count),
    ("reasoning_output_tokens", "reasoning", _count),
    ("tokens_per_second", "tok/s", lambda v: f"{v:.1f}"),
    ("time_to_first_token_seconds", "ttft", lambda v: f"{v:.2f}s"),
)


def stats_line(reply: ChatReply) -> str:
    parts = []
    for key, label, fmt in _STATS_FORMAT:
        value = (reply.stats or {}).get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        parts.append(f"{label}={fmt(value)}")
    return "stats: " + (" ".join(parts) if parts else "none")


def document_text(raw: str, limit: int, token: str | None) -> str:
    """A decoded document, checked and normalised like a question (not yet neutralised)."""
    secrets = () if not token else (token, normalise_question(token))
    if any(s and s in raw for s in secrets):
        raise document.DocumentError("the document contains the API token")
    if len(raw) > limit:
        raise document.DocumentError(f"document longer than {limit} characters")
    text = _DOCUMENT_CONTROLS.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    normalised = normalise_question(text, limit)
    if len(normalised) > limit:
        raise document.DocumentError(f"document longer than {limit} characters after normalising")
    if any(s and s in normalised for s in secrets):
        raise document.DocumentError("the document contains the API token")
    return normalised


def _document(cfg: Config, args: argparse.Namespace) -> str | None:
    """The document for this question: --file PATH, --file -, a pipe on standard input, or None."""
    limit, timeout = cfg.limits.max_document_chars, cfg.llm.timeout_s
    explicit = args.file is not None
    if args.file is None or args.file == "-":
        raw = document.read_stdin(explicit, limit, timeout)
        if raw is None:
            return None
    else:
        raw = document.read_file(args.file, limit, timeout, cfg.llm.token_file)
    text = document_text(raw, limit, cfg.llm.token)
    if not text:
        if explicit:
            raise document.DocumentError("the document is empty")
        return None  # an empty pipe is no document
    return text


def cmd_ask(cfg: Config, args: argparse.Namespace) -> int:
    question = " ".join(args.question).strip()
    if not question:
        print("gi ask: please write a question", file=sys.stderr)
        return EXIT_USAGE
    if len(question) > cfg.limits.max_input_chars:
        print(
            f"gi ask: question longer than {cfg.limits.max_input_chars} characters", file=sys.stderr
        )
        return EXIT_USAGE
    normalised = normalise_question(question, cfg.limits.max_input_chars)
    if len(normalised) > cfg.limits.max_input_chars:
        print(
            f"gi ask: question longer than {cfg.limits.max_input_chars} characters "
            "after normalising",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        doc = _document(cfg, args)
    except document.DocumentError as exc:
        print(f"gi ask: {exc}", file=sys.stderr)
        return EXIT_USAGE
    system = assets.load_toml("prompts", "system.toml")["prompt"]["text"]
    ask = assets.load_toml("prompts", "ask.toml")
    user = ask["prompt"]["text"].replace("{{question}}", neutralise_markers(normalised))
    if doc is not None:
        wrapped = ask["document"]["text"].replace("{{document}}", neutralise_markers(doc))
        user = wrapped + "\n" + user
    messages = [Message("system", system), Message("user", user)]
    try:
        reply = make_backend(cfg.llm).chat(messages)
    except LLMError as exc:
        print(f"gi ask: {exc}", file=sys.stderr)
        return EXIT_FAIL
    answer = sanitize_output(reply.text, cfg.limits.max_output_chars)
    _print({"answer": answer} if args.json else answer, args.json)
    if args.verbose:
        sys.stdout.flush()  # the answer comes first, also when stdout is a pipe
        print(stats_line(reply), file=sys.stderr)
    return EXIT_OK


def cmd_init_workspace(cfg: Config, args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser() if args.path else cfg.workspace
    created = workspace.init(root)
    _print({"workspace": str(root), "created": created}, args.json)
    return EXIT_OK


def cmd_task(cfg: Config, args: argparse.Namespace) -> int:
    try:
        if args.task_cmd == "new":
            path = tasks.new(cfg.workspace, args.type, args.title)
            _print({"created": str(path)}, args.json)
        else:
            items = tasks.list_tasks(cfg.workspace)
            if args.json:
                _print(items, True)
            else:
                for t in items:
                    print(f"{t['id']}  {t['status']:<12} {t['title']}")
    except (ValueError, workspace.WorkspaceError) as exc:
        print(f"gi task: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def cmd_selfcheck(cfg: Config, args: argparse.Namespace) -> int:
    """Check the user's local security baseline (see `man gi-ai`)."""
    checks: list[dict[str, object]] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        checks.append({"id": cid, "ok": ok, "detail": detail})

    ws = cfg.workspace
    if (ws / workspace.AUDIT_FILE).exists():
        mode = workspace.mode_of(ws)
        add("workspace-private", mode & 0o077 == 0, f"mode {oct(mode)} (want 0o700)")
        ok, detail = workspace.verify_audit(ws)
        add("audit-chain", ok, detail)
    else:
        add("workspace-private", True, "no workspace yet")
    for src in cfg.sources:
        mode = stat.S_IMODE(os.stat(src).st_mode)
        add(f"config-not-world-writable:{src}", mode & 0o002 == 0, oct(mode))
    ok, detail = endpoint_privacy(cfg.llm.endpoint, cfg.llm.allow_remote)
    add("model-endpoint-private", ok, detail)
    if cfg.llm.token_file is not None:
        ok, detail = token_file_privacy(cfg.llm.token_file)
        add("token-file-private", ok, detail)
    result = {"ok": all(c["ok"] for c in checks), "checks": checks}
    if args.json:
        _print(result, True)
    else:
        for c in checks:
            print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['id']}: {c['detail']}")
    return EXIT_OK if result["ok"] else EXIT_FAIL


class _Once(argparse.Action):
    """An option that may be given only once."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string} may be given only once")
        setattr(namespace, self.dest, values)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gi", description=f"{DISPLAY_NAME}: local AI assistant")
    # Handled in main() before parsing, so no config, token or network is touched.
    p.add_argument("--version", action="store_true", help="show version and licence, then exit")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--config", help="extra config file (TOML)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="check that Ĝi and the local model work")
    a = sub.add_parser("ask", help="ask the local model a question")
    a.add_argument("--verbose", action="store_true", help="also print reply statistics")
    a.add_argument(
        "--file",
        action=_Once,
        metavar="PATH",
        help="a text file the question is about (- for standard input)",
    )
    a.add_argument("question", nargs="+")
    w = sub.add_parser("init-workspace", help="create your private workspace")
    w.add_argument("--path", help="workspace directory")
    t = sub.add_parser("task", help="local task records")
    tsub = t.add_subparsers(dest="task_cmd", required=True)
    tn = tsub.add_parser("new")
    tn.add_argument("--type", required=True, choices=tasks.TYPES)
    tn.add_argument("--title", required=True)
    tsub.add_parser("list")
    sub.add_parser("selfcheck", help="check your local security baseline")
    return p


HANDLERS = {
    "health": cmd_health,
    "ask": cmd_ask,
    "init-workspace": cmd_init_workspace,
    "task": cmd_task,
    "selfcheck": cmd_selfcheck,
}


def _version_usage_error() -> int:
    print("gi: --version takes no other arguments", file=sys.stderr)
    return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    options = argv[: argv.index("--")] if "--" in argv else argv
    if "--version" in options:
        if len(argv) != 1:
            return _version_usage_error()
        sys.stdout.write(VERSION_TEXT)
        return EXIT_OK
    args = build_parser().parse_args(argv)
    if args.version:  # an abbreviation such as --vers next to a command
        return _version_usage_error()
    try:
        # The selfcheck reports a cloud metadata endpoint instead of stopping at it.
        cfg = load(
            Path(args.config) if args.config else None,
            refuse_metadata=args.cmd != "selfcheck",
        )
    except ConfigError as exc:
        print(f"gi: configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return HANDLERS[args.cmd](cfg, args)
    except EndpointRefused as exc:
        # Found at connect time (a looked-up name or the connected peer); nothing was sent.
        print(f"gi: {exc}", file=sys.stderr)
        return EXIT_USAGE
