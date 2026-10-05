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

"""Regression tests for the security review of the LM Studio backend (SPEC-0001 B3, B10, B16)."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from gi_ai import llm

CHAT = "/api/v1/chat"


@pytest.fixture
def raw_server():
    """A loopback server that answers every connection with the given raw bytes."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    state = {"reply": b""}

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    conn.recv(65536)
                    conn.sendall(state["reply"])
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.getsockname()[1]}"
    yield state
    srv.close()


def test_b3_bad_status_line_from_server_is_sanitised(cli, capsys, gi_config, raw_server):
    raw_server["reply"] = b"\x1b]0;PWNED\x07\x1b[31mEVIL STATUS " + b"x" * 1000 + b"\r\nfake: x\r\n"
    gi_config(backend="lmstudio", endpoint=raw_server["url"], model="m", timeout_s=5)
    assert cli("ask", "q") == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x07" not in err and "\r" not in err
    assert len(err.splitlines()) == 1
    assert len(err) < 400


def test_b16_huge_integer_stat_does_not_crash(cli, capsys, lmstudio_config, lm_replies):
    body = lm_replies.chat(lm_replies.message("answer"), stats={"input_tokens": 10**400})
    lmstudio_config.server.reply(CHAT, body, method="POST")
    assert cli("ask", "--verbose", "q") == 0
    out, err = capsys.readouterr()
    assert out == "answer\n"
    assert err.splitlines() == ["stats: none"]


def test_b3_bidi_controls_are_removed(cli, capsys, lmstudio_config, lm_replies):
    text = "safe‮txt.exe⁦x⁩ and Ĝi ‍ keeps joiners"
    lmstudio_config.server.reply(CHAT, lm_replies.chat(lm_replies.message(text)), method="POST")
    assert cli("ask", "q") == 0
    out = capsys.readouterr().out
    assert out == "safetxt.exex and Ĝi ‍ keeps joiners\n"
    assert llm.strip_controls("a‪b‬c⁧d") == "abcd"


@pytest.fixture
def trickle_server():
    """A loopback server that sends a valid-looking reply one byte at a time, forever."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                head = b"HTTP/1.1 200 OK\r\nContent-Length: 4000000\r\n\r\n"
                try:
                    conn.recv(65536)
                    conn.sendall(head)
                    while not stop.wait(0.05):
                        conn.sendall(b" ")
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    stop.set()
    srv.close()


def test_timeout_covers_the_whole_reply(cli, capsys, gi_config, trickle_server):
    gi_config(backend="lmstudio", endpoint=trickle_server, model="m", timeout_s=1)
    started = time.monotonic()
    assert cli("ask", "q") == 1
    assert time.monotonic() - started < 10  # without the deadline this takes hours
    assert "did not answer within" in capsys.readouterr().err


def test_b3_lone_surrogates_are_removed(cli, capsys, lmstudio_config, lm_replies):
    text = "a\udc9b31mb\ud800c"
    lmstudio_config.server.reply(CHAT, lm_replies.chat(lm_replies.message(text)), method="POST")
    assert cli("ask", "q") == 0
    assert capsys.readouterr().out == "a31mbc\n"
    assert llm.strip_controls("x\udfffy") == "xy"


def test_b15_token_at_the_cut_is_never_partly_shown(
    cli, capsys, monkeypatch, gi_config, raw_server
):
    secret = "tok-" + "CutLeak-9a8b7c6d5e4f"
    monkeypatch.setenv("GI_AI_LLM_TOKEN", secret)
    raw_server["reply"] = b"Z" * 190 + secret.encode() + b"\r\n"
    gi_config(backend="lmstudio", endpoint=raw_server["url"], model="m", timeout_s=5)
    assert cli("ask", "q") == 1
    err = capsys.readouterr().err
    for n in range(6, len(secret) + 1):
        assert secret[:n] not in err


def test_worker_crash_is_a_clean_model_error(cli, capsys, monkeypatch, lmstudio_config):
    def crash(*args, **kwargs):
        raise SystemExit("raw text from the worker")

    monkeypatch.setattr(llm, "_exchange", crash)
    assert cli("ask", "q") == 1
    err = capsys.readouterr().err
    assert "raw text" not in err
    assert err.startswith("gi ask: ")
