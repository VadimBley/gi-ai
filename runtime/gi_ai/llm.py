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

"""Talk to a language model server. Default: Ollama on 127.0.0.1; LM Studio optional."""

from __future__ import annotations

import http.client
import json
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from gi_ai.config import LLMConfig, is_loopback_address, literal_address, metadata_address

# ANSI escape sequences and other C0/C1 control characters except \n and \t.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)")
# Also bidirectional overrides and isolates, which can make text read differently.
# Lone surrogates too: printed with surrogateescape they become raw C1 bytes.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\ud800-\udfff]")
_FIELD_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
# Printable ASCII without spaces: what a bearer value can hold in an HTTP header.
_HEADER_SAFE = re.compile(r"[\x21-\x7e]+")

MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_ERROR_CHARS = 200
MAX_UNKNOWN_FIELDS = 50
MAX_STAT_VALUE = 10**15
READ_CHUNK = 65536
REDACTED = "[hidden]"

STATS_KEYS = (
    "input_tokens",
    "total_output_tokens",
    "reasoning_output_tokens",
    "tokens_per_second",
    "time_to_first_token_seconds",
)
KNOWN_TOP = frozenset({"models", "version"})
KNOWN_MODEL = frozenset(
    {
        "type",
        "publisher",
        "key",
        "display_name",
        "architecture",
        "quantization",
        "size_bytes",
        "params_string",
        "loaded_instances",
        "max_context_length",
        "format",
        "capabilities",
        "description",
        "variants",
        "selected_variant",
    }
)
KNOWN_CAPABILITIES = frozenset({"vision", "trained_for_tool_use", "reasoning"})


class LLMError(RuntimeError):
    pass


class EndpointRefused(Exception):
    """The endpoint leads to an address Ĝi never talks to. Not a model error: the command stops."""


def strip_controls(text: str) -> str:
    """Remove terminal escape sequences and control characters (keeps \\n and \\t)."""
    return _CONTROL.sub("", _ANSI.sub("", text))


def sanitize_output(text: str, max_chars: int) -> str:
    """Model output is untrusted: strip terminal control sequences and cap length."""
    text = strip_controls(text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[output truncated]"
    return text


def _one_line(text: str, max_chars: int) -> str:
    return " ".join(strip_controls(text).split())[:max_chars]


@dataclass
class Message:
    role: str  # "system" | "user"
    content: str


@dataclass
class ChatReply:
    text: str
    stats: dict[str, float] | None = None


# --- HTTP -----------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could carry the request (and its token) to another host: never follow."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _refusal(host: str, addresses: list[str]) -> EndpointRefused | None:
    """Why addresses looked up for `host` (or its peer) may not be used; None if they may."""
    for address in addresses:
        metadata = metadata_address(address)
        if metadata is not None:
            return EndpointRefused(f"refusing the endpoint {metadata}: a cloud metadata address")
    if host.lower() == "localhost":
        for address in addresses:
            if not is_loopback_address(address.split("%", 1)[0]):
                shown = address.split("%", 1)[0]
                return EndpointRefused(
                    f"refusing the endpoint {shown}: localhost does not resolve to loopback"
                )
    return None


def _addresses(host: str, port: int) -> list[tuple[int, tuple[Any, ...]]]:
    """(family, socket address) to try: the literal itself, or ONE lookup of the name."""
    literal = literal_address(host)
    if literal is not None:
        if literal.version == 4:
            return [(socket.AF_INET, (str(literal), port))]
        return [(socket.AF_INET6, (str(literal), port, 0, 0))]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        infos = []
    found = [(int(family), tuple(sockaddr)) for family, _, _, _, sockaddr in infos]
    if not found:
        raise LLMError(f"cannot resolve {host}")
    return found


def _guarded_connect(host: str, port: int, timeout: Any, source: Any) -> socket.socket:
    """Connect to the endpoint without a second lookup; check every address before use.

    The addresses come from one lookup (or the literal); all of them are checked first, then
    tried in order, all attempts within one `timeout`. The connected peer is checked again
    before anything (TLS handshake or HTTP request) is sent.
    """
    candidates = _addresses(host, port)
    refusal = _refusal(host, [str(sockaddr[0]) for _, sockaddr in candidates])
    if refusal is not None:
        raise refusal
    budget = float(timeout) if isinstance(timeout, (int, float)) else None
    deadline = None if budget is None else time.monotonic() + budget
    failure: OSError = OSError(f"cannot connect to {host}")
    for family, sockaddr in candidates:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            failure = TimeoutError("timed out")
            break
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(remaining)
            if source:
                sock.bind(source)
            sock.connect(sockaddr)
            peer = str(sock.getpeername()[0])
        except OSError as exc:
            sock.close()
            failure = exc
            continue
        refusal = _refusal(host, [peer])
        if refusal is not None:
            sock.close()
            raise refusal
        sock.settimeout(budget)
        return sock
    raise failure


class _GuardedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP over a socket from _guarded_connect; Host stays the configured name."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # connect() opens its socket through this attribute, before any request byte is sent.
        self._create_connection = self._guarded

    def _guarded(self, address: tuple[str, int], timeout: Any = None, source: Any = None) -> Any:
        return _guarded_connect(address[0], address[1], timeout, source)


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    """TLS over a socket from _guarded_connect; SNI and certificate use the configured name."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = self._guarded

    def _guarded(self, address: tuple[str, int], timeout: Any = None, source: Any = None) -> Any:
        return _guarded_connect(address[0], address[1], timeout, source)


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> Any:
        # No context: HTTPSConnection builds the default one (certificate and name checked).
        return self.do_open(_GuardedHTTPSConnection, req)


def _opener() -> urllib.request.OpenerDirector:
    # ProxyHandler({}) ignores proxy environment variables: only the endpoint is contacted.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        _GuardedHTTPHandler(),
        _GuardedHTTPSHandler(),
    )


def _error_member(obj: object) -> str | None:
    """The text of a top-level `error` member, or None when there is none."""
    if not isinstance(obj, dict) or "error" not in obj:
        return None
    err = obj["error"]
    if isinstance(err, str):
        return err
    if isinstance(err, dict) and isinstance(err.get("message"), str):
        return str(err["message"])
    return "(no message)"


def _clean_error(text: str, secrets: tuple[str, ...]) -> str:
    """Server text for an error message: no controls, token hidden, then cut."""
    clean = strip_controls(text)
    for secret in secrets:
        if secret:
            clean = clean.replace(secret, REDACTED)
    return " ".join(clean.split())[:MAX_ERROR_CHARS]


def _reported(text: str, secrets: tuple[str, ...]) -> LLMError:
    return LLMError(f"the model server reported an error: {_clean_error(text, secrets)}")


def _read_capped(resp: Any) -> bytes:
    """Read at most MAX_REPLY_BYTES + 1 bytes."""
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_REPLY_BYTES:
        chunk = resp.read(min(READ_CHUNK, MAX_REPLY_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _parse_json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        return None


def _http_json(
    url: str, payload: dict[str, Any] | None, timeout: float, headers: dict[str, str]
) -> dict[str, Any]:
    """One request to the model server; the reply must be a JSON object without `error`."""
    auth = headers.get("Authorization", "")
    secrets = (auth, auth.removeprefix("Bearer ")) if auth else ()
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(  # noqa: S310 - scheme validated in config
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method="POST" if data is not None else "GET",
    )
    endpoint = url.split("/api/", 1)[0]
    body = _with_deadline(lambda: _exchange(req, timeout, secrets, endpoint), timeout)
    if len(body) > MAX_REPLY_BYTES:
        raise LLMError("the model server reply is too large")
    parsed = _parse_json(body)
    if not isinstance(parsed, dict):
        raise LLMError("unexpected reply from the model server (not a JSON object)")
    text = _error_member(parsed)
    if text is not None:
        raise _reported(text, secrets)
    return parsed


def _with_deadline(work: Any, timeout: float) -> bytes:
    """Run one exchange; give up after `timeout` seconds in total, however slow the server."""
    result: list[bytes] = []
    failure: list[LLMError | EndpointRefused] = []

    def run() -> None:
        try:
            result.append(work())
        except (LLMError, EndpointRefused) as exc:
            failure.append(exc)
        except BaseException:  # never let a raw message (or a crash) out of the worker
            failure.append(LLMError("unexpected error while talking to the model server"))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise LLMError(f"the model server did not answer within {timeout:g} s")
    if failure or not result:
        raise failure[0] if failure else LLMError("no reply from the model server")
    return result[0]


def _exchange(
    req: urllib.request.Request, timeout: float, secrets: tuple[str, ...], endpoint: str
) -> bytes:
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return _read_capped(resp)
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read(MAX_REPLY_BYTES + 1)
        except (OSError, http.client.HTTPException):
            err_body = b""
        finally:
            exc.close()
        text = _error_member(_parse_json(err_body)) if len(err_body) <= MAX_REPLY_BYTES else None
        if text is not None:
            raise _reported(text, secrets) from None
        raise LLMError(f"the model server answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        # the reason can quote bytes the server sent (e.g. a bad status line)
        detail = _clean_error(str(reason), secrets)
        raise LLMError(f"cannot reach the model server at {endpoint}: {detail}") from None
    except ValueError:
        # e.g. a header value that cannot be encoded; the message could quote it, so drop it
        pass
    raise LLMError("the request to the model server could not be sent")


# --- backends -------------------------------------------------------------------------


class Backend:
    def chat(self, messages: list[Message]) -> ChatReply:  # pragma: no cover - interface
        raise NotImplementedError

    def health_fields(self) -> dict[str, object]:  # pragma: no cover - interface
        raise NotImplementedError

    def reachable(self) -> bool:
        return self.health_fields().get("reachable") is True


class EchoBackend(Backend):
    """Offline backend for tests and demos. Deterministic, no network."""

    def chat(self, messages: list[Message]) -> ChatReply:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return ChatReply(f"echo: {last_user}")

    def health_fields(self) -> dict[str, object]:
        return {"reachable": True}


class OllamaBackend(Backend):
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    def __repr__(self) -> str:
        return f"OllamaBackend(endpoint={self.cfg.endpoint!r}, model={self.cfg.model!r})"

    def _request(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        url = self.cfg.endpoint.rstrip("/") + path
        return _http_json(url, payload, self.cfg.timeout_s, {})

    def chat(self, messages: list[Message]) -> ChatReply:
        payload = {
            "model": self.cfg.model,
            "stream": False,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        reply = self._request("/api/chat", payload)
        message = reply.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMError("unexpected reply from the model server")
        return ChatReply(content)

    def health_fields(self) -> dict[str, object]:
        try:
            reply = self._request("/api/tags", None)
        except LLMError:
            return {"reachable": False}
        return {"reachable": isinstance(reply.get("models"), list)}


def _numeric_stats(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    stats: dict[str, float] = {}
    for key in STATS_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, (int, float)) and abs(value) <= MAX_STAT_VALUE:
            stats[key] = value
    return stats or None


def _unknown_names(level: str, obj: dict[str, Any], known: frozenset[str]) -> set[str]:
    names = set()
    for name in obj:
        if name in known:
            continue
        valid = isinstance(name, str) and _FIELD_NAME.fullmatch(name)
        names.add(f"{level}.{name}" if valid else f"{level}.?")
    return names


def _capabilities(item: dict[str, Any]) -> dict[str, object]:
    caps: dict[str, object] = {}
    raw = item.get("capabilities")
    raw = raw if isinstance(raw, dict) else {}
    for key in ("vision", "trained_for_tool_use"):
        if isinstance(raw.get(key), bool):
            caps[key] = raw[key]
    ctx = item.get("max_context_length")
    if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx >= 0:
        caps["max_context_length"] = ctx
    reasoning = raw.get("reasoning")
    options = reasoning.get("allowed_options") if isinstance(reasoning, dict) else None
    if isinstance(options, list):
        allowed: list[str] = []
        for option in options:
            if not isinstance(option, str):
                continue
            clean = _one_line(option, 1000)
            if clean and len(clean) <= 32 and clean not in allowed:
                allowed.append(clean)
        caps["reasoning"] = allowed[:32]
    return caps


class LMStudioBackend(Backend):
    """LM Studio's native REST API: stateless chats, no tools."""

    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    def __repr__(self) -> str:
        return f"LMStudioBackend(endpoint={self.cfg.endpoint!r}, model={self.cfg.model!r})"

    def _request(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        token = self.cfg.token
        if token and not _HEADER_SAFE.fullmatch(token):
            raise LLMError(
                "the API token has characters that cannot be sent "
                "(only printable ASCII without spaces)"
            )
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = self.cfg.endpoint.rstrip("/") + path
        return _http_json(url, payload, self.cfg.timeout_s, headers)

    def chat(self, messages: list[Message]) -> ChatReply:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "system_prompt": "\n\n".join(m.content for m in messages if m.role == "system"),
            "input": "\n\n".join(m.content for m in messages if m.role == "user"),
            "store": False,
            "stream": False,
        }
        if self.cfg.reasoning != "default":
            payload["reasoning"] = self.cfg.reasoning
        for key in ("context_length", "max_output_tokens", "temperature"):
            value = getattr(self.cfg, key)
            if value is not None:
                payload[key] = value
        reply = self._request("/api/v1/chat", payload)
        output = reply.get("output")
        if not isinstance(output, list):
            raise LLMError("unexpected reply from the model server (no output list)")
        items = [item for item in output if isinstance(item, dict)]
        if any(item.get("type") == "tool_call" for item in items):
            raise LLMError("unexpected tool call: Ĝi has no tools")
        parts: list[str] = []
        for item in items:
            if item.get("type") != "message":
                continue  # reasoning and unknown item types are never shown
            content = item.get("content")
            if not isinstance(content, str):
                raise LLMError("unexpected reply from the model server (message without text)")
            parts.append(content)
        if not parts:
            raise LLMError("the model server sent no answer message")
        return ChatReply("\n".join(parts), _numeric_stats(reply.get("stats")))

    def health_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {"reachable": False}
        try:
            reply = self._request("/api/v1/models", None)
        except LLMError:
            return fields
        models = reply.get("models")
        if not isinstance(models, list):
            return fields
        version = reply.get("version")
        if isinstance(version, str):
            fields["server_version"] = _one_line(version, 64)
        unknown = _unknown_names("top", reply, KNOWN_TOP)
        item = next(
            (m for m in models if isinstance(m, dict) and m.get("key") == self.cfg.model), None
        )
        if item is not None:
            fields["reachable"] = True
            instances = item.get("loaded_instances")
            fields["loaded"] = isinstance(instances, list) and len(instances) > 0
            fields["capabilities"] = _capabilities(item)
            unknown |= _unknown_names("model", item, KNOWN_MODEL)
            caps = item.get("capabilities")
            if isinstance(caps, dict):
                unknown |= _unknown_names("capabilities", caps, KNOWN_CAPABILITIES)
        fields["unknown_fields"] = sorted(unknown)[:MAX_UNKNOWN_FIELDS]
        return fields


def make_backend(cfg: LLMConfig) -> Backend:
    if cfg.backend == "echo":
        return EchoBackend()
    if cfg.backend == "lmstudio":
        return LMStudioBackend(cfg)
    return OllamaBackend(cfg)
