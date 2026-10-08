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
import json
import math
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

from gi_ai import DISPLAY_NAME, __version__, assets, tasks, workspace
from gi_ai.config import Config, ConfigError, endpoint_privacy, load, token_file_privacy
from gi_ai.contracts import validate
from gi_ai.llm import ChatReply, LLMError, Message, make_backend, sanitize_output

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

# The markers around the question in the ask prompt; a question may not carry its own.
_MARKER_RE = re.compile(r"<<<QUESTION|QUESTION>>>", re.IGNORECASE)
MARKER_REMOVED = "[marker removed]"

VERSION_TEXT = (
    f"gi-ai {__version__}\n"
    "Copyright (C) 2026 Vadim Bley\n"
    "License AGPL-3.0-only: GNU Affero General Public License version 3 "
    "<https://www.gnu.org/licenses/agpl-3.0.html>\n"
    "This is free software: you are free to change and redistribute it.\n"
    "There is NO WARRANTY, to the extent permitted by law.\n"
    "Source: https://github.com/VadimBley/gi-ai\n"
)


def normalise_question(question: str) -> str:
    """NFKC, then without format characters (zero-width spaces, joiners, direction marks)."""
    text = unicodedata.normalize("NFKC", question)
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def neutralise_markers(question: str) -> str:
    return _MARKER_RE.sub(MARKER_REMOVED, question)


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
    system = assets.load_toml("prompts", "system.toml")["prompt"]["text"]
    template = assets.load_toml("prompts", "ask.toml")["prompt"]["text"]
    messages = [
        Message("system", system),
        Message(
            "user",
            template.replace("{{question}}", neutralise_markers(normalise_question(question))),
        ),
    ]
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
    return HANDLERS[args.cmd](cfg, args)
