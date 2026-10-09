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

"""Configuration loading: /etc/gi-ai/gi.toml < ~/.config/gi-ai/gi.toml < $GI_AI_CONFIG."""

from __future__ import annotations

import errno
import ipaddress
import math
import os
import re
import socket
import stat
import struct
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SYSTEM_CONFIG = Path("/etc/gi-ai/gi.toml")
ROUTE_FILE: Path = Path("/proc/net/route")
GATEWAY_HOST = "@gateway"
AUTH_ENV_NAME = "GI_AI_LLM_TOKEN"

BACKENDS = ("ollama", "lmstudio", "echo")
REASONING_LEVELS = ("off", "low", "medium", "high", "on", "default")
MAX_ENDPOINT_CHARS = 300
MAX_MODEL_CHARS = 200
MAX_TOKEN_FILE_BYTES = 65536
FILE_PLACE_RULE = "token_file must be a regular file inside ~/.config/gi-ai/ reached without links"

# scheme://host[:port][/] and nothing else: no user info, path, query or fragment.
_ENDPOINT_RE = re.compile(
    r"(?P<scheme>https?)://"
    r"(?P<host>@gateway|[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+(?:%25[A-Za-z0-9._~-]{1,64})?\])"
    r"(?::(?P<port>[0-9]{1,5}))?/?"
)

# Address classes, fixed here and never taken from the ipaddress module's private and loopback
# flags, whose tables differ between Python versions. Every other address is public.
_LOOPBACK_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "::ffff:127.0.0.0/104")
)
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
        "fe80::/10",
    )
)


# Cloud metadata services: never an endpoint, whatever llm.allow_remote says.
METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
# IPv6 forms that carry an IPv4 address in their last 32 bits: IPv4-mapped, IPv4-compatible,
# IPv4-translated (SIIT) and the well-known NAT64 prefix.
_LAST_32_BITS = tuple(
    ipaddress.ip_network(n) for n in ("::ffff:0:0/96", "::/96", "::ffff:0:0:0/96", "64:ff9b::/96")
)
# Local-use NAT64 (RFC 8215) with the IPv4 address at the RFC 6052 positions for /48, /56, /64
# and /96; bits 64-71 (the u-octet) are skipped. Each layout: (high bit, width) of its pieces.
_LOCAL_NAT64 = ipaddress.ip_network("64:ff9b:1::/48")
_RFC6052_LAYOUTS = (
    ((48, 16), (72, 16)),
    ((56, 8), (72, 24)),
    ((72, 32),),
    ((96, 32),),
)
_6TO4 = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")
# ISATAP interface identifiers (bits 64-95), under any prefix: 0000:5efe, 0100:5efe, 0200:5efe,
# 0300:5efe (the u and g bits set or not), followed by the IPv4 address.
_ISATAP_IDS = frozenset({0x00005EFE, 0x01005EFE, 0x02005EFE, 0x03005EFE})
# Spellings the C library also reads as an IPv4 address: 2852039166, 0xa9fea9fe, 0251.0376.0.1
_LEGACY_IPV4_RE = re.compile(r"[0-9A-Fa-fXx.]+")


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gi-ai" / "gi.toml"


def default_workspace() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "gi-ai" / "workspace"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    backend: str = "ollama"  # "ollama" | "lmstudio" | "echo" (offline, deterministic)
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen3:4b"
    timeout_s: int = 120
    allow_remote: bool = False
    reasoning: str = "off"
    context_length: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    token_file: str | None = None
    # Resolved at load time from the environment or token_file; never a config key.
    token: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class LimitsConfig:
    max_input_chars: int = 8000
    max_output_chars: int = 16000
    max_document_chars: int = 20000


@dataclass(frozen=True)
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    workspace: Path = field(default_factory=default_workspace)
    sources: tuple[str, ...] = ()


# Keys a config file may set. "token" is deliberately missing.
LLM_KEYS = frozenset(LLMConfig.__dataclass_fields__) - {"token"}
LIMITS_KEYS = frozenset(LimitsConfig.__dataclass_fields__)


def _merge(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in top.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


# --- endpoint -------------------------------------------------------------------------


def endpoint_host(endpoint: str) -> str | None:
    """The host part of a strict endpoint (IPv6 without brackets, zone id as "%zone"), or None."""
    m = _ENDPOINT_RE.fullmatch(endpoint)
    if m is None:
        return None
    host = m.group("host")
    return host[1:-1].replace("%25", "%", 1) if host.startswith("[") else host


def _check_endpoint(endpoint: str) -> str:
    """Validate the strict endpoint form and return its host."""
    hint = "llm.endpoint must look like http://host:port (no user, path or query)"
    if len(endpoint) > MAX_ENDPOINT_CHARS:
        raise ConfigError(f"llm.endpoint is longer than {MAX_ENDPOINT_CHARS} characters")
    m = _ENDPOINT_RE.fullmatch(endpoint)
    if m is None:
        raise ConfigError(hint)
    port = m.group("port")
    if port is not None and not 1 <= int(port) <= 65535:
        raise ConfigError("llm.endpoint port must be between 1 and 65535")
    host = m.group("host")
    if host.startswith("["):
        address, _, zone = host[1:-1].partition("%25")
        try:
            ipaddress.IPv6Address(address)
        except ValueError:
            raise ConfigError(hint) from None
        if zone:
            # A zone id is read only to refuse a metadata address; no other endpoint has one.
            if metadata_address(address) is None:
                raise ConfigError(hint)
            return f"{address}%{zone}"
        return address
    if host != GATEWAY_HOST and (host.startswith(".") or ".." in host):
        raise ConfigError(hint)
    return host


def _parse(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a host literal names as written, read the way the C library reads it, or None.

    A host ending in "." is a name: the C library looks it up instead of reading it.
    A zone id ("%eth0") is never accepted here; only metadata_address() reads past one.
    """
    if "%" in host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        if not _LEGACY_IPV4_RE.fullmatch(host):
            return None
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))  # parses only, no lookup
        except OSError:
            return None


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Like _parse(), with an IPv4-mapped address unwrapped (the address a socket reaches)."""
    ip = _parse(host)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def address_class(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """The class of an address as written: "loopback", "private" or "public" (fixed tables)."""
    if any(ip.version == net.version and ip in net for net in _LOOPBACK_NETWORKS):
        return "loopback"
    if any(ip.version == net.version and ip in net for net in _PRIVATE_NETWORKS):
        return "private"
    return "public"


def literal_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a host literal names (read like the C library reads it), or None for a name."""
    return _ip(host)


def is_loopback_address(address: str) -> bool:
    """Is this address literal (no name, no zone id) a loopback address?"""
    ip = _parse(address)
    return ip is not None and address_class(ip) == "loopback"


def _is_loopback(host: str) -> bool:
    return host.lower() == "localhost" or is_loopback_address(host)


def _bits(value: int, high: int, width: int) -> int:
    """`width` bits of a 128-bit value, starting `high` bits from the top."""
    return (value >> (128 - high - width)) & ((1 << width) - 1)


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """Every IPv4 address an IPv6 address carries in a known embedding form."""
    value = int(ip)
    found = []
    if any(ip in net for net in _LAST_32_BITS):
        found.append(value & 0xFFFFFFFF)
    if ip in _LOCAL_NAT64:
        for layout in _RFC6052_LAYOUTS:
            v4 = 0
            for high, width in layout:
                v4 = (v4 << width) | _bits(value, high, width)
            found.append(v4)
    if ip in _6TO4:
        found.append(_bits(value, 16, 32))
    if ip in _TEREDO:
        found.append(_bits(value, 32, 32))  # server
        found.append(~value & 0xFFFFFFFF)  # client, every bit inverted
    if _bits(value, 64, 32) in _ISATAP_IDS:
        found.append(value & 0xFFFFFFFF)
    return [ipaddress.IPv4Address(v4) for v4 in found]


def metadata_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The cloud metadata address a host literal reaches, or None."""
    # A zone id does not change the address ("fd00:ec2::254%eth0"); IPv6Address equality
    # would include it, so it is dropped before comparing.
    host = host.split("%", 1)[0]
    # "169.254.169.254." is looked up as a name, which may well answer with that address.
    ip = _ip(host[:-1] if host.endswith(".") else host)
    if ip is None:
        return None
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        candidates += _embedded_ipv4(ip)
    for candidate in candidates:
        if candidate in METADATA_ADDRESSES:
            return candidate
    return None


def default_gateway(route_file: Path) -> str:
    """The IPv4 default-route gateway from a /proc/net/route style table."""
    try:
        text = route_file.read_bytes().decode("ascii", errors="replace")
    except OSError:
        raise ConfigError("cannot find the default gateway for llm.endpoint") from None
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 4 or cols[1] != "00000000":
            continue
        gw, flags = cols[2], cols[3]
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", gw) or not re.fullmatch(r"[0-9A-Fa-f]{1,8}", flags):
            continue
        if not int(flags, 16) & 0x2:
            continue
        value = int(gw, 16)
        if value == 0:
            continue
        return socket.inet_ntoa(struct.pack("<I", value))
    raise ConfigError("cannot find the default gateway for llm.endpoint")


def endpoint_privacy(endpoint: str, allow_remote: bool) -> tuple[bool, str]:
    """Is the (resolved) endpoint on this computer or on a private network?"""
    host = endpoint_host(endpoint)
    if host is None:
        return False, "unresolved name " + endpoint[:MAX_ENDPOINT_CHARS]
    if host == GATEWAY_HOST:
        try:
            host = default_gateway(ROUTE_FILE)
        except ConfigError:
            return False, f"unresolved name {GATEWAY_HOST}"
    metadata = metadata_address(host)
    if metadata is not None:
        return False, f"METADATA {metadata}"
    if _is_loopback(host):
        return True, "loopback"
    ip = _parse(host)
    if ip is None:
        return False, f"unresolved name {host}"
    if address_class(ip) == "private":
        if allow_remote:
            return True, f"private network {ip}"
        return False, f"private network {ip} (llm.allow_remote is false)"
    return False, f"PUBLIC {ip}"


def token_destination_ok(endpoint: str) -> tuple[bool, str]:
    """May the API token be sent to this (resolved) endpoint? Same classes as the selfcheck."""
    ok, _ = endpoint_privacy(endpoint, allow_remote=True)
    host = endpoint_host(endpoint)
    return ok, endpoint[:MAX_ENDPOINT_CHARS] if host is None else host


# --- token ----------------------------------------------------------------------------


def token_dir() -> Path:
    # The documented place, taken literally (XDG_CONFIG_HOME does not move it).
    return Path.home() / ".config" / "gi-ai"


def _token_parts(raw_path: str) -> list[str]:
    """The path below the home directory, one name per component; only inside token_dir()."""
    home = os.path.abspath(Path.home())
    base = os.path.abspath(token_dir())
    # Lexical: no symbolic link is followed, ".." is collapsed.
    path = os.path.abspath(os.path.expanduser(raw_path))
    if path == base or os.path.commonpath([base, path]) != base:
        raise ConfigError(FILE_PLACE_RULE)
    return os.path.relpath(path, home).split(os.sep)


def _open_token_file(raw_path: str, where: str) -> int:
    """Open the token file one component at a time from the home directory, no links."""
    parts = _token_parts(raw_path)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    folder_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | cloexec
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | cloexec
    try:
        fd = os.open(Path.home(), os.O_RDONLY | os.O_DIRECTORY | cloexec)
    except OSError:
        raise ConfigError(f"{where} cannot be opened") from None
    try:
        for name in parts[:-1]:
            inner = os.open(name, folder_flags, dir_fd=fd)
            os.close(fd)
            fd = inner
        return os.open(parts[-1], file_flags, dir_fd=fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ConfigError(FILE_PLACE_RULE) from None
        reason = "does not exist" if exc.errno == errno.ENOENT else "cannot be opened"
        raise ConfigError(f"{where} {reason}") from None
    finally:
        os.close(fd)


def _read_token_file(raw_path: str) -> str:
    path = os.path.expanduser(raw_path)
    where = f"llm.token_file {path}"
    fd = _open_token_file(raw_path, where)
    try:
        info = os.fstat(fd)
        # The file checked is the file read: one regular file, one name.
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ConfigError(FILE_PLACE_RULE)
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.geteuid():
            raise ConfigError(f"{where} is not owned by you")
        if mode & 0o077:
            raise ConfigError(
                f"{where} can be read or written by others (mode {oct(mode)}); run chmod 600"
            )
        chunks: list[bytes] = []
        size = 0
        while size <= MAX_TOKEN_FILE_BYTES:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        raise ConfigError(f"{where} cannot be read") from None
    finally:
        os.close(fd)
    if size > MAX_TOKEN_FILE_BYTES:
        raise ConfigError(f"{where} is too large")
    try:
        token = b"".join(chunks).decode("utf-8").strip()
    except UnicodeDecodeError:
        token = None
    if token is None:
        raise ConfigError(f"{where} is not text")
    if not token:
        raise ConfigError(f"{where} is empty")
    return token


def token_file_privacy(raw_path: str) -> tuple[bool, str]:
    """Selfcheck view of the token file: owner and mode, never the content."""
    path = os.path.expanduser(raw_path)
    try:
        info = os.lstat(path)
    except OSError:
        return False, f"{path} cannot be found"
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return False, f"{path} is a symbolic link"
    if not stat.S_ISREG(info.st_mode):
        return False, f"{path} is not a regular file"
    if info.st_uid != os.geteuid():
        return False, f"mode {oct(mode)}, not owned by you"
    if mode & 0o077:
        return False, f"mode {oct(mode)} (want 0o600)"
    return True, f"mode {oct(mode)}"


def _resolve_token(token_file: str | None) -> str | None:
    env = os.environ.get(AUTH_ENV_NAME)
    if env:
        return env
    if token_file is not None:
        return _read_token_file(token_file)
    return None


# --- [llm] and [limits] ---------------------------------------------------------------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_types(llm_raw: dict[str, Any]) -> None:
    strings = ("backend", "endpoint", "model", "reasoning", "token_file")
    ints = ("timeout_s", "context_length", "max_output_tokens")
    for key in strings:
        if key in llm_raw and not isinstance(llm_raw[key], str):
            raise ConfigError(f"llm.{key} must be a string")
    for key in ints:
        if key in llm_raw and not _is_int(llm_raw[key]):
            raise ConfigError(f"llm.{key} must be a whole number")
    if "allow_remote" in llm_raw and not isinstance(llm_raw["allow_remote"], bool):
        raise ConfigError("llm.allow_remote must be true or false")
    if "temperature" in llm_raw:
        value = llm_raw["temperature"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError("llm.temperature must be a number")
        if not math.isfinite(value):
            raise ConfigError("llm.temperature must be between 0 and 1")


def _check_range(name: str, value: int | float | None, low: float, high: float) -> None:
    if value is not None and not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")


def _build_llm(llm_raw: dict[str, Any], refuse_metadata: bool = True) -> LLMConfig:
    _check_types(llm_raw)
    llm = LLMConfig(**llm_raw)
    if llm.backend not in BACKENDS:
        raise ConfigError("llm.backend must be 'ollama', 'lmstudio' or 'echo'")
    if llm.reasoning not in REASONING_LEVELS:
        raise ConfigError("llm.reasoning must be one of " + ", ".join(REASONING_LEVELS))
    if not 1 <= len(llm.model) <= MAX_MODEL_CHARS:
        raise ConfigError(f"llm.model must have 1 to {MAX_MODEL_CHARS} characters")
    _check_range("llm.timeout_s", llm.timeout_s, 1, 3600)
    _check_range("llm.context_length", llm.context_length, 256, 1048576)
    _check_range("llm.max_output_tokens", llm.max_output_tokens, 1, 131072)
    _check_range("llm.temperature", llm.temperature, 0, 1)
    if llm.token_file is not None:
        if not llm.token_file:
            raise ConfigError("llm.token_file must not be empty")
        _token_parts(llm.token_file)

    host = _check_endpoint(llm.endpoint)
    not_local = ConfigError(
        "llm.endpoint is not on this computer; set llm.allow_remote = true to allow it"
    )
    endpoint = llm.endpoint
    resolved = host
    if host == GATEWAY_HOST:
        if not llm.allow_remote:
            raise not_local  # before the route table is read
        # Resolved for this run only; never written back.
        resolved = default_gateway(ROUTE_FILE)
        endpoint = endpoint.replace(GATEWAY_HOST, resolved, 1)
        if len(endpoint) > MAX_ENDPOINT_CHARS:
            raise ConfigError(f"llm.endpoint is longer than {MAX_ENDPOINT_CHARS} characters")
    metadata = metadata_address(resolved)
    if metadata is not None:
        if refuse_metadata:
            raise ConfigError(f"refusing the endpoint {metadata}: a cloud metadata address")
        # Only the selfcheck gets here: it reports the address and sends nothing.
        return replace(llm, endpoint=endpoint, token=None)
    if not llm.allow_remote and (host == GATEWAY_HOST or not _is_loopback(host)):
        raise not_local
    token = _resolve_token(llm.token_file)
    if token is not None:
        ok, dest = token_destination_ok(endpoint)
        if not ok:
            raise ConfigError(
                f"refusing to send the API token to {dest}: not a local or private address"
            )
    return replace(llm, endpoint=endpoint, token=token)


def _build_limits(lim_raw: dict[str, Any]) -> LimitsConfig:
    for key, value in lim_raw.items():
        if not _is_int(value):
            raise ConfigError(f"limits.{key} must be a whole number")
    limits = LimitsConfig(**lim_raw)
    if not 1 <= limits.max_input_chars <= 200_000:
        raise ConfigError("limits.max_input_chars out of range")
    if not 1 <= limits.max_output_chars <= 1_000_000:
        raise ConfigError("limits.max_output_chars out of range")
    if not 1000 <= limits.max_document_chars <= 1_000_000:
        raise ConfigError("limits.max_document_chars must be between 1000 and 1000000")
    return limits


def _build(raw: dict[str, Any], sources: list[str], refuse_metadata: bool = True) -> Config:
    extra_top = set(raw) - {"llm", "limits", "workspace"}
    if extra_top:
        raise ConfigError(f"unknown sections: {sorted(extra_top)}")
    llm_raw = raw.get("llm", {})
    lim_raw = raw.get("limits", {})
    ws_raw = raw.get("workspace", {})
    for name, section, allowed in (("llm", llm_raw, LLM_KEYS), ("limits", lim_raw, LIMITS_KEYS)):
        if not isinstance(section, dict):
            raise ConfigError(f"[{name}] must be a table")
        extra = set(section) - allowed
        if extra:
            raise ConfigError(f"unknown keys in [{name}]: {sorted(extra)}")
    if not isinstance(ws_raw, dict):
        raise ConfigError("[workspace] must be a table")

    llm = _build_llm(llm_raw, refuse_metadata)
    limits = _build_limits(lim_raw)

    ws_value = ws_raw.get("path")
    workspace = Path(os.path.expanduser(ws_value)) if ws_value else default_workspace()
    return Config(llm=llm, limits=limits, workspace=workspace, sources=tuple(sources))


def load(extra_path: Path | None = None, *, refuse_metadata: bool = True) -> Config:
    """Load the configuration. refuse_metadata=False lets the selfcheck report such an endpoint."""
    candidates = [SYSTEM_CONFIG, user_config_path()]
    env_path = os.environ.get("GI_AI_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    if extra_path:
        candidates.append(extra_path)

    merged: dict[str, Any] = {}
    used: list[str] = []
    for path in candidates:
        if path.is_file():
            with path.open("rb") as fh:
                try:
                    merged = _merge(merged, tomllib.load(fh))
                except tomllib.TOMLDecodeError as exc:
                    raise ConfigError(f"{path}: {exc}") from exc
            used.append(str(path))
    return _build(merged, used, refuse_metadata)
