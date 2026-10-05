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

"""SPEC-0001 v1.2.0 B1, B10, B12, B13: `gi health` with the lmstudio backend (AT-11, AT-12),
and non-2xx replies for both backends (AT-21)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gi_ai.contracts import validate

MODELS = "/api/v1/models"
SCHEMA_FILE = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "gi_ai"
    / "assets"
    / "schemas"
    / "health.schema.json"
)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
NAME_RE = re.compile(r"^(top|model|capabilities)\.([A-Za-z0-9_.-]{1,64}|\?)$")


def _schema():
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _health(cli, capsys, *extra):
    code = cli("--json", "health", *extra)
    out, err = capsys.readouterr()
    assert out, f"no JSON report (exit {code}): {err}"
    report = json.loads(out)
    validate(report, _schema())
    return code, report, out, err


# --- AT-11 -------------------------------------------------------------------------------


def test_at11_reachable_when_key_matches(cli, capsys, lmstudio_config, lm_replies):
    server = lmstudio_config.server
    others = [lm_replies.model_item("other/model-a"), lm_replies.model_item("other/model-b")]
    server.reply(MODELS, {"models": [*others, lm_replies.model_item()]}, method="GET")
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    assert report["status"] == "ok"
    lm = report["llm"]
    assert lm["backend"] == "lmstudio"
    assert lm["model"] == lmstudio_config.model
    assert lm["reachable"] is True
    assert lm["endpoint"] == server.url
    assert lm["loaded"] is True
    assert lm["capabilities"] == {
        "vision": False,
        "trained_for_tool_use": True,
        "max_context_length": 262144,
        "reasoning": ["off", "on"],
    }
    assert "server_version" not in lm
    assert lm["unknown_fields"] == []
    assert [(r.method, r.path) for r in server.requests] == [("GET", MODELS)]


def test_at11_not_reachable_when_no_key_matches(cli, capsys, lmstudio_config, lm_replies):
    items = [
        lm_replies.model_item("other/model-a"),
        lm_replies.model_item(lmstudio_config.model.upper()),
        lm_replies.model_item(lmstudio_config.model + " "),
        {"display_name": lmstudio_config.model, "id": lmstudio_config.model},
    ]
    lmstudio_config.server.reply(MODELS, {"models": items, "version": "0.4.25"})
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["status"] == "degraded"
    lm = report["llm"]
    assert lm["reachable"] is False
    assert "loaded" not in lm and "capabilities" not in lm
    assert lm["server_version"] == "0.4.25"


def test_at11_empty_models_list_is_degraded(cli, capsys, lmstudio_config):
    lmstudio_config.server.reply(MODELS, {"models": []})
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["llm"]["reachable"] is False
    assert report["llm"].get("unknown_fields", []) == []


@pytest.mark.parametrize("instances", [[], None, "yes", {"id": "x"}], ids=str)
def test_at11_loaded_false_unless_non_empty_instances(
    cli, capsys, lmstudio_config, lm_replies, instances
):
    item = lm_replies.model_item(loaded_instances=instances)
    if instances is None:
        del item["loaded_instances"]
    lmstudio_config.server.reply(MODELS, {"models": [item]})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0, "available but not loaded is still reachable"
    assert report["llm"]["reachable"] is True
    assert report["llm"]["loaded"] is False


def test_at11_server_version_only_when_string(cli, capsys, lmstudio_config, lm_replies):
    server = lmstudio_config.server
    server.reply(MODELS, {"models": [lm_replies.model_item()], "version": "0.4.25"})
    assert _health(cli, capsys)[1]["llm"]["server_version"] == "0.4.25"
    for bad in (425, None, ["0.4.25"], {"v": "0.4.25"}, True):
        server.reply(MODELS, {"models": [lm_replies.model_item()], "version": bad})
        assert "server_version" not in _health(cli, capsys)[1]["llm"], bad


def test_at11_capabilities_only_with_the_right_types(cli, capsys, lmstudio_config, lm_replies):
    item = lm_replies.model_item(
        max_context_length="262144",
        capabilities={"vision": "yes", "trained_for_tool_use": 1, "reasoning": ["off", "on"]},
    )
    lmstudio_config.server.reply(MODELS, {"models": [item]})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    caps = report["llm"].get("capabilities", {})
    assert "vision" not in caps
    assert "trained_for_tool_use" not in caps
    assert "max_context_length" not in caps
    assert "reasoning" not in caps or all(isinstance(r, str) for r in caps["reasoning"])


def test_at11_capabilities_missing_entirely(cli, capsys, lmstudio_config, lm_replies):
    item = lm_replies.model_item()
    del item["capabilities"], item["max_context_length"]
    lmstudio_config.server.reply(MODELS, {"models": [item]})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    assert report["llm"].get("capabilities", {}) == {}


def test_at11_adversarial_values_still_validate(cli, capsys, lmstudio_config, lm_replies):
    item = lm_replies.model_item(
        max_context_length=-5,
        capabilities={
            "vision": True,
            "trained_for_tool_use": None,
            "reasoning": {
                "allowed_options": ["off", 5, "x" * 40, "\x1b[2Jon\x07", None, "low"],
                "default": "\x1b]0;pwned\x07",
            },
        },
    )
    version = "\x1b[31m0.4.25\x1b[0m\x07\r\n" + "9" * 500
    junk = ["not a dict", 5, None, {"key": 5}]
    lmstudio_config.server.reply(MODELS, {"models": [*junk, item], "version": version})
    code, report, out, _ = _health(cli, capsys)
    assert code == 0
    lm = report["llm"]
    assert lm["reachable"] is True
    assert len(lm["server_version"]) <= 64
    assert not CONTROL.search(lm["server_version"])
    assert lm["server_version"].startswith("0.4.25")
    caps = lm["capabilities"]
    assert caps["vision"] is True
    assert "trained_for_tool_use" not in caps
    assert caps.get("max_context_length", 0) >= 0
    assert "off" in caps["reasoning"] and "low" in caps["reasoning"]
    for option in caps["reasoning"]:
        assert isinstance(option, str) and len(option) <= 32 and not CONTROL.search(option)
    assert "\x1b" not in out and "\x07" not in out and "pwned" not in out


def test_at11_huge_model_list(cli, capsys, lmstudio_config, lm_replies):
    items = [lm_replies.model_item(f"bulk/model-{i}") for i in range(2000)]
    items.append(lm_replies.model_item())
    lmstudio_config.server.reply(MODELS, {"models": items})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0 and report["llm"]["reachable"] is True


@pytest.mark.parametrize(
    "raw,status",
    [
        (b'{"models": "all of them"}', 200),
        (b'{"data": []}', 200),
        (b"[]", 200),
        (b"<html>LM Studio</html>", 200),
        (b"", 200),
        (b'{"models": []}', 500),
    ],
    ids=["models-str", "openai-shape", "top-list", "html", "empty", "http-500"],
)
def test_at11_invalid_models_reply_is_degraded(cli, capsys, lmstudio_config, raw, status):
    lmstudio_config.server.reply(MODELS, raw=raw, status=status)
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["status"] == "degraded"
    assert report["llm"]["reachable"] is False
    assert report["llm"]["endpoint"] == lmstudio_config.server.url


def test_at11_unreachable_server_is_degraded(cli, capsys, lmstudio_config, closed_port):
    lmstudio_config(endpoint=f"http://127.0.0.1:{closed_port}")
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["llm"]["reachable"] is False
    assert report["llm"]["endpoint"] == f"http://127.0.0.1:{closed_port}"


def test_at11_text_output_also_shows_the_endpoint(cli, capsys, lmstudio_config, lm_replies):
    lmstudio_config.server.reply(MODELS, {"models": [lm_replies.model_item()]})
    assert cli("health") == 0
    assert lmstudio_config.server.url in capsys.readouterr().out


# --- AT-12 -------------------------------------------------------------------------------

VALUE_MARK = "UNKNOWN-VALUE-7f3a"


def test_at12_unknown_fields_sorted_without_values(cli, capsys, lmstudio_config, lm_replies):
    item = lm_replies.model_item(
        new_field=VALUE_MARK,
        zeta={"nested": VALUE_MARK},
        Alpha=[VALUE_MARK],
    )
    item["capabilities"]["tool_choice"] = VALUE_MARK
    item["capabilities"]["audio"] = True
    # only the configured model's item is inspected
    other = lm_replies.model_item("other/model", other_extra=VALUE_MARK)
    body = {
        "models": [other, item],
        "version": "0.4.25",
        "object": VALUE_MARK,
        "extra_top": 1,
    }
    lmstudio_config.server.reply(MODELS, body)
    code, report, out, _ = _health(cli, capsys)
    assert code == 0
    assert report["llm"]["unknown_fields"] == [
        "capabilities.audio",
        "capabilities.tool_choice",
        "model.Alpha",
        "model.new_field",
        "model.zeta",
        "top.extra_top",
        "top.object",
    ]
    assert VALUE_MARK not in out
    assert cli("health") == 0
    text = capsys.readouterr().out
    assert VALUE_MARK not in text
    assert "model.new_field" in text


def test_at12_nested_members_below_known_names_are_not_reported(
    cli, capsys, lmstudio_config, lm_replies
):
    item = lm_replies.model_item()
    item["capabilities"]["reasoning"]["future_member"] = 1
    item["quantization"]["future_member"] = 1
    item["loaded_instances"][0]["future_member"] = 1
    lmstudio_config.server.reply(MODELS, {"models": [item]})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    assert report["llm"]["unknown_fields"] == []


def test_at12_invalid_names_become_question_marks(cli, capsys, lmstudio_config, lm_replies):
    bad_names = ["bad name", "x" * 65, "ünïcode-Ĝ", "", "a\x1b[31m", "semi;colon", "new\nline"]
    item = lm_replies.model_item(**{n: VALUE_MARK for n in bad_names}, good_one=1)
    item["capabilities"]["also bad"] = 1
    body = {"models": [item], **{n: VALUE_MARK for n in bad_names}}
    lmstudio_config.server.reply(MODELS, body)
    code, report, out, _ = _health(cli, capsys)
    assert code == 0
    fields = report["llm"]["unknown_fields"]
    assert fields == sorted(fields)
    assert len(fields) == len(set(fields)), "each <level>.? is reported once"
    assert set(fields) == {"capabilities.?", "model.?", "model.good_one", "top.?"}
    for f in fields:
        assert NAME_RE.match(f)
    assert VALUE_MARK not in out
    assert "x" * 65 not in out and "bad name" not in out and "\x1b" not in out


def test_at12_valid_name_edge_cases(cli, capsys, lmstudio_config, lm_replies):
    names = ["a", "x" * 64, "dotted.name", "with-dash_and.dot", "UPPER", "123"]
    item = lm_replies.model_item(**dict.fromkeys(names, 0))
    lmstudio_config.server.reply(MODELS, {"models": [item]})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    assert report["llm"]["unknown_fields"] == sorted(f"model.{n}" for n in names)


def test_at12_more_than_fifty_names_are_capped(cli, capsys, lmstudio_config, lm_replies):
    names = [f"f{i:02d}" for i in range(60)]
    item = lm_replies.model_item(**dict.fromkeys(names, VALUE_MARK))
    lmstudio_config.server.reply(MODELS, {"models": [item]})
    code, report, out, _ = _health(cli, capsys)
    assert code == 0
    assert report["llm"]["unknown_fields"] == sorted(f"model.{n}" for n in names)[:50]
    assert VALUE_MARK not in out


def test_at12_cap_applies_across_levels(cli, capsys, lmstudio_config, lm_replies):
    item = lm_replies.model_item(**{f"m{i:02d}": 0 for i in range(40)})
    item["capabilities"].update({f"c{i:02d}": 0 for i in range(40)})
    body = {"models": [item], **{f"t{i:02d}": 0 for i in range(40)}}
    lmstudio_config.server.reply(MODELS, body)
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    fields = report["llm"]["unknown_fields"]
    assert len(fields) == 50
    assert fields == sorted(fields)
    expected = sorted(
        [f"model.m{i:02d}" for i in range(40)]
        + [f"capabilities.c{i:02d}" for i in range(40)]
        + [f"top.t{i:02d}" for i in range(40)]
    )[:50]
    assert fields == expected


def test_at12_top_level_reported_even_without_matching_item(
    cli, capsys, lmstudio_config, lm_replies
):
    other = lm_replies.model_item("other/model", other_extra=1)
    lmstudio_config.server.reply(MODELS, {"models": [other], "extra_top": VALUE_MARK})
    code, report, out, _ = _health(cli, capsys)
    assert code == 1
    assert report["llm"]["unknown_fields"] == ["top.extra_top"]
    assert VALUE_MARK not in out


def test_at12_no_extra_members_means_empty_or_absent(cli, capsys, lmstudio_config, lm_replies):
    lmstudio_config.server.reply(MODELS, {"models": [lm_replies.model_item()], "version": "1.0.0"})
    code, report, _, _ = _health(cli, capsys)
    assert code == 0
    assert report["llm"].get("unknown_fields", []) == []


# --- AT-21: non-2xx replies make health degraded, both backends --------------------------

HTML_ERROR = b"<!DOCTYPE html><html><body><h1>Not here</h1></body></html>"


@pytest.mark.parametrize("status", [404, 500])
@pytest.mark.parametrize("raw", [HTML_ERROR, b"Internal Server Error"], ids=["html", "text"])
def test_at21_lmstudio_health_degraded_on_non_2xx(cli, capsys, lmstudio_config, status, raw):
    lmstudio_config.server.reply(MODELS, raw=raw, status=status, method="GET")
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["status"] == "degraded"
    assert report["llm"]["reachable"] is False


@pytest.mark.parametrize("status", [404, 500])
@pytest.mark.parametrize("raw", [HTML_ERROR, b"Internal Server Error"], ids=["html", "text"])
def test_at21_ollama_health_degraded_on_non_2xx(cli, capsys, gi_config, fake_server, status, raw):
    gi_config(backend="ollama", endpoint=fake_server.url, model="m", timeout_s=5)
    fake_server.reply("/api/tags", raw=raw, status=status, method="GET")
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["status"] == "degraded"
    assert report["llm"]["reachable"] is False
    assert [r.path for r in fake_server.requests] == ["/api/tags"]


@pytest.mark.parametrize("status", [404, 500])
def test_at21_lmstudio_health_non_2xx_with_a_valid_body(
    cli, capsys, lmstudio_config, lm_replies, status
):
    body = {"models": [lm_replies.model_item()], "version": "0.4.25"}
    lmstudio_config.server.reply(MODELS, body, status=status, method="GET")
    code, report, _, _ = _health(cli, capsys)
    assert code == 1
    assert report["status"] == "degraded"
    assert report["llm"]["reachable"] is False
    assert "server_version" not in report["llm"]


@pytest.mark.parametrize("status", [404, 500])
def test_at21_text_health_says_degraded(cli, capsys, gi_config, fake_server, status):
    gi_config(backend="ollama", endpoint=fake_server.url, model="m", timeout_s=5)
    fake_server.reply("/api/tags", raw=HTML_ERROR, status=status, method="GET")
    assert cli("health") == 1
    assert "status: degraded" in capsys.readouterr().out
