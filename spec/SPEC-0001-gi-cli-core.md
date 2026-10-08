---
id: SPEC-0001
title: Ĝi CLI core (health, ask, workspace, tasks, selfcheck)
version: 1.5.0
status: approved
intent: INT-0001
visibility: public
schemas: [health, task]
owner: VadimBley
---

# SPEC-0001: Ĝi CLI core

Changes in 1.5.0 (INT-0006): cloud metadata addresses are also refused when an endpoint name resolves to one at
connect time, and in every IPv6 form that embeds them (B7); invisible characters and combining marks can no longer
split the question markers (B17); the question is refused when it is longer than `limits.max_input_chars` after
normalising (B2, B17; AT-27 changed). AT-28..AT-31.

Changes in 1.4.0 (INT-0005): cloud metadata addresses are refused as endpoints (B6, B7, B15); `llm.token_file` is
opened component by component without symbolic links and must have exactly one link (B15); questions are normalised
(NFKC, format characters removed) before marker neutralising (B17). AT-24..AT-27.

Changes in 1.3.0 (INT-0004, ADR-0005): `gi --version` with the licence notice (B18); licence, notices and
contacts in the package and man page (B19). AT-22, AT-23.

Changes in 1.2.0 (INT-0003): the API token is sent only to loopback or private addresses and
`llm.token_file` must live in `~/.config/gi-ai/` (B15); marker text inside a question is neutralised (B2, B17);
non-2xx HTTP replies are model errors (B10). AT-18..AT-21.

Changes in 1.1.0 (INT-0002, ADR-0002, ADR-0003): new `lmstudio` backend using LM Studio's native REST API
(behaviours 9-15), `@gateway` endpoint host, optional API token, extra model settings, `gi ask --verbose`,
model-id pinning and unknown-field reporting in `gi health`, reachability judged by the reply body for every backend,
and `gi selfcheck` check `model-endpoint-private` (replaces "model endpoint local") plus `token-file-private`.

## 1. Behaviour
1. `gi health` prints name, version, status, backend, model and workspace state. Status is `ok` when the model endpoint
   answers with a **valid reply body** for the backend (behaviour 10 for `ollama`, behaviour 12 for `lmstudio`), else
   `degraded`. Exit 0 if ok, 1 if degraded. The report includes the resolved endpoint (`llm.endpoint`, with `@gateway`
   replaced, behaviour 14). `--json` output matches contract `health`.
2. `gi ask QUESTION` sends a system prompt plus the question (neutralised per behaviour 17, then wrapped in markers) to
   the configured model and prints the
   answer. Questions longer than `limits.max_input_chars` are refused with exit 2, both as typed
   (`gi ask: question longer than <limit> characters`) and after normalising (behaviour 17,
   `gi ask: question longer than <limit> characters after normalising`). Both checks run before any network call.
3. Model output is sanitised: ANSI/OSC escape sequences and control characters (except newline and tab) are removed, and
   output longer than `limits.max_output_chars` is truncated with a marker. This applies to every text taken from a model
   reply, including error messages reported by the model server.
4. `gi init-workspace` creates the workspace (mode 0700) with `tasks/`, `evidence/` and a hash-chained audit log
   `evidence/audit.log`.
5. `gi task new --type req|bug|vuln --title T` writes `GI-<TYPE>-<YYYYMMDD>-<NNN>.toml` (mode 0600) matching contract
   `task`, and appends an audit entry. `gi task list` lists records.
6. `gi selfcheck` checks: workspace private (`workspace-private`), audit chain intact (`audit-chain`), config files not
   world-writable (`config-not-world-writable:<path>`), **`model-endpoint-private`** and, when a token file is configured,
   **`token-file-private`**. Exit 1 if any check fails.
   - `model-endpoint-private` passes when the resolved endpoint host is loopback (detail `loopback`), or when
     `llm.allow_remote = true` and the host is a private, link-local or shared (CGNAT) IPv4/IPv6 address (detail
     `private network <ip>`). It fails for any other address (detail `PUBLIC <ip>`), for a cloud metadata address (behaviour 7,
     detail `METADATA <ip>`, even with `allow_remote`) and for a host name that isn't an IP literal, `localhost` or
     `@gateway` (detail `unresolved name <host>`).
   - `token-file-private` passes when `llm.token_file` is owned by the user and its mode has no group/other bits
     (detail shows the octal mode, never the content).
7. Configuration: `/etc/gi-ai/gi.toml` < `~/.config/gi-ai/gi.toml` < `$GI_AI_CONFIG` < `--config`. Unknown keys are
   errors (exit 2). `[llm]` keys:
   - `backend`: `ollama` (default) | `lmstudio` | `echo`.
   - `endpoint`: `http(s)://host[:port]`. The host may be the placeholder **`@gateway`**, replaced at every run by the
     IPv4 default-route gateway (behaviour 14). A non-loopback host, including `@gateway`, is refused unless
     `llm.allow_remote = true`. **Cloud metadata addresses are always refused**, whatever `allow_remote` says:
     `169.254.169.254`, `169.254.170.2` and `fd00:ec2::254`. The command exits 2 with
     `refusing the endpoint <ip>: a cloud metadata address`. An address **reaches** a metadata address when it is one,
     or when it is an IPv4 literal in any spelling the C library accepts (e.g. `2852039166`, `0xa9fea9fe`,
     `0251.0376.0251.0376`, with or without a trailing dot) for one, or when it is an IPv6 address that embeds one of the
     two IPv4 metadata addresses. A zone id (`%...`) is ignored for this check (`fd00:ec2::254%eth0` reaches
     `fd00:ec2::254`). Embedding forms:
     - IPv4-mapped `::ffff:0:0/96`, IPv4-compatible `::/96`, IPv4-translated (SIIT) `::ffff:0:0:0/96` and well-known NAT64
       `64:ff9b::/96`: the last 32 bits;
     - local-use NAT64 `64:ff9b:1::/48` (RFC 8215): the IPv4 address at the RFC 6052 position for each of the prefix
       lengths /48, /56, /64 and /96 (any one match is enough); the u-octet (bits 64-71) is ignored, any value matches;
     - 6to4 `2002::/16`: bits 16-47.

     The check runs at two points:
     - **Config time**, before any network activity: on an IP literal endpoint host and on the address `@gateway`
       resolves to (behaviour 14).
     - **Connect time**, for an endpoint host that is a name (any host that isn't an IP literal, `localhost` included):
       Ĝi looks the name up through the system resolver **once per request** and checks every address the lookup
       returns. If any of them reaches a metadata address, the command exits 2 with the message above (`<ip>` = the
       first such address) and **no connection is opened**. For `localhost`, every looked-up address must also be a
       loopback address; otherwise the command exits 2 with
       `refusing the endpoint <ip>: localhost does not resolve to loopback` and no connection is opened. A lookup that
       fails or returns no address is a model error (`cannot resolve <host>`; ask exit 1, health `degraded`).
       Otherwise Ĝi connects only to addresses from that same lookup, one at a time in lookup order (no second lookup,
       so a changed DNS answer can't swap the address); `llm.timeout_s` bounds all connection attempts of a request
       together, not each one. After the TCP connection is established and **before the TLS handshake** (https) or the
       HTTP request (http), Ĝi checks the connected peer address again with the same rule (refused the same way,
       connection closed, nothing sent). The HTTP `Host` header and, for https, the TLS server name and certificate
       check keep using the configured name. Proxy environment variables stay ignored. This applies to `gi ask` and
       `gi health`; `gi selfcheck` does no lookup (behaviour 6, unchanged).

     **Redirects are never followed**, for any endpoint: a 3xx reply is a model error (ask exit 1) or unreachable
     (health `degraded`), and no request (and no token) is sent to its `Location`.
   - `model`, `timeout_s` (1-3600), `allow_remote` (default false): unchanged.
   - `reasoning`: `off` (default) | `low` | `medium` | `high` | `on` | `default`. Used by `lmstudio` only; `default` means
     the member is not sent and the model's own default applies (for models that reject the member).
   - `context_length` (256-1048576), `max_output_tokens` (1-131072), `temperature` (0-1): optional; sent to `lmstudio`
     only when set.
   - `token_file`: optional path to a file inside `~/.config/gi-ai/` holding an API token (behaviour 15). A token value in any config file is
     not possible: there is no key for it.
8. Ĝi never reads hidden files or upper-case Markdown files as user content.
9. `lmstudio` chat: `gi ask` sends exactly one `POST {endpoint}/api/v1/chat` with a JSON object containing only these
   members: `model` (= `llm.model`), `system_prompt` (the system prompt), `input` (the wrapped question, a string),
   `store: false`, `stream: false`, `reasoning` (= `llm.reasoning`, omitted when it is `default`), and `context_length`, `max_output_tokens`,
   `temperature` only when set. It never sends `previous_response_id`, `integrations`, tools, images or any other member.
   The `Authorization: Bearer <token>` header is sent only when a token is configured (behaviour 15).
10. Reply validation for every backend: a reply is valid only if it is a JSON object, has **no** top-level `error` member,
    and has the backend's expected shape: `ollama` chat → `message.content` string, `ollama` health → `models` array
    (`GET /api/tags`); `lmstudio` → behaviours 11-12. An HTTP 2xx status with an invalid body, and any non-2xx HTTP status,
    is a model error (ask: exit 1) or unreachable (health: `degraded`). When the body has an `error` member, the message shown is
    `the model server reported an error: <text>` with `<text>` sanitised (behaviour 3) and cut to 200 characters.
11. `lmstudio` answer: the reply must have an `output` array. The answer is the `content` strings of the items with
    `type: "message"`, in order, joined by a newline. Items with `type: "reasoning"` are ignored and never printed.
    Any item with `type: "tool_call"` makes the whole reply a model error (`unexpected tool call: Ĝi has no tools`, exit 1).
    Items of other types are ignored. No `message` item → model error (exit 1).
12. `lmstudio` health: `GET {endpoint}/api/v1/models` must return an object with a `models` array. The model is
    **available** when an item's `key` equals `llm.model`; only then `reachable` is true. The health report adds:
    `loaded` (that item's `loaded_instances` is a non-empty array), `capabilities` (from that item: `vision`,
    `trained_for_tool_use` as booleans, `max_context_length` as integer, and `reasoning` as the list of allowed options,
    each only when present with that type), `server_version` (only if the reply carries a string `version` member; it
    is omitted otherwise) and `unknown_fields` (behaviour 13).
13. Unknown-field reporting (`lmstudio` health): Ĝi compares member names against these known sets and lists every
    other name in `llm.unknown_fields`, sorted, without values: top level of `/api/v1/models` {`models`, `version`};
    a model item {`type`, `publisher`, `key`, `display_name`, `architecture`, `quantization`, `size_bytes`,
    `params_string`, `loaded_instances`, `max_context_length`, `format`, `capabilities`, `description`, `variants`,
    `selected_variant`}; `capabilities` {`vision`, `trained_for_tool_use`, `reasoning`}. Names are reported as
    `<level>.<name>` (`top`, `model`, `capabilities`), at most 50 entries; a name is reported only if it matches
    `^[A-Za-z0-9_.-]{1,64}$`, otherwise it is counted as `<level>.?`. Only the item for `llm.model` is inspected.
14. `@gateway`: Ĝi reads `/proc/net/route`, takes the first line whose destination is `00000000` and whose flags include
    the gateway bit (0x2), and converts its gateway field (little-endian hex) to a dotted IPv4 address. If there is no such
    line, or the file can't be read, the command exits 2 with `cannot find the default gateway for llm.endpoint`.
    The resolved address is used for that run only and is shown wherever the endpoint is shown.
15. API token: if the environment variable `GI_AI_LLM_TOKEN` is set and non-empty, it is the token; otherwise, if
    `llm.token_file` is set, the token is the file's content with surrounding whitespace removed. A token file that is
    missing, empty, not owned by the user, or readable/writable by group or others is a config error (exit 2) and the
    token is not used. `llm.token_file` must name a file inside `~/.config/gi-ai/` (after `~` expansion). Ĝi opens it **one path component
    at a time, starting at the home directory**, refusing a symbolic link in any component (including `.config`,
    `gi-ai` and the file itself), so that the file checked is the file read (no gap between check and open). The opened
    file must be a regular file with **exactly one hard link**. Any other case (another location, a symbolic link
    anywhere on the path, a hard link count above 1, not a regular file) is a config error (exit 2) with
    `token_file must be a regular file inside ~/.config/gi-ai/ reached without links`, and no token is read.
    `GI_AI_LLM_TOKEN` is unaffected (for setups whose config folder is a symbolic link).
    **Destination rule:** a token is sent only when the resolved endpoint host is a loopback address or a private,
    link-local or shared (CGNAT) IP address (the same address classes that pass `model-endpoint-private`, behaviour 6;
    cloud metadata addresses never get that far, behaviour 7),
    over http or https. If a token is configured and the endpoint host is anything else (a public IP or a DNS name other
    than `localhost`), the command exits 2 with `refusing to send the API token to <host>: not a local or private address`
    before any network call. The token is sent only to the configured endpoint, only in the `Authorization` header, and
    is never printed, logged, written to the workspace or included in error messages.
16. `gi ask --verbose` prints the answer as usual, then one line to standard error with the reply's numeric stats when
    present (`lmstudio`: `stats.input_tokens`, `stats.total_output_tokens`, `stats.reasoning_output_tokens`,
    `stats.tokens_per_second`, `stats.time_to_first_token_seconds`), e.g. `stats: in=42 out=118 tok/s=21.4 ttft=0.31s`.
    Non-numeric values are skipped. For other backends it prints `stats: none`.
17. Question neutralising: before wrapping (behaviour 2), the question is **normalised**: Unicode NFKC, then every
    **invisible character** is removed. Invisible means general category `Cf` (format characters such as zero-width
    spaces and joiners) or `Cn` (unassigned in the running Python's Unicode tables, so characters newer than that
    Python can't slip through as unknown marks), **or** one of these `Default_Ignorable_Code_Point` ranges, pinned from
    Unicode 15.1.0 `DerivedCoreProperties.txt` so that the removed set is at least these ranges on every supported
    Python:
    `U+00AD`, `U+034F`, `U+061C`, `U+115F..U+1160`, `U+17B4..U+17B5`, `U+180B..U+180F`, `U+200B..U+200F`,
    `U+202A..U+202E`, `U+2060..U+206F`, `U+3164`, `U+FE00..U+FE0F`, `U+FEFF`, `U+FFA0`, `U+FFF0..U+FFF8`,
    `U+1BCA0..U+1BCA3`, `U+1D173..U+1D17A`, `U+E0000..U+E0FFF`.
    The normalised text is checked against the length limit (behaviour 2). All lengths count Unicode code points.
    Then, in the normalised text, every occurrence of the marker strings `<<<QUESTION` and `QUESTION>>>` is replaced by
    `[marker removed]`. A marker is matched without regard to letter case **and to combining marks**: a character
    matches marker character X (a letter of `QUESTION`, `<` or `>`) when the first character of its NFD form,
    upper-cased with Python's `str.upper()`, equals X and the rest of its NFD form is combining marks (general category
    `Mn`, `Mc`, `Me`); e.g. `É`, `ı`, `≯`. Combining marks directly after any marker character are part of the match
    (e.g. `Q` + `U+0301`). Matches are found left to right in one pass without overlap; at the same start position the
    longer match wins (`<<<QUESTION>>>` becomes `[marker removed]>>>`). The replacement covers the whole matched text,
    marks included. Text outside a match keeps its marks and letters (`é`, `ñ`, Devanagari vowel signs are not changed).
    The model receives the normalised, neutralised text, so the question text inside the markers never exceeds
    ceil(`limits.max_input_chars` × 16 / 11) code points (the only growth left is `[marker removed]` replacing an
    11-character marker). Emoji joined
    by zero-width joiners or followed by a variation selector may appear as separate or plain-text emoji. NFKC itself
    comes from the running Python and may differ slightly between supported versions. Look-alike letters from other
    scripts (homoglyphs such as Cyrillic letters or `Ɋ`) and visible blanks (`U+2800`, line and paragraph separators
    `U+2028`/`U+2029`) are not mapped (residual risk, section 6). The ask prompt asset's version is bumped with this
    change.

18. `gi --version` (also `gi-ai --version`) prints exactly these lines to standard output and exits 0, without reading
    any config file, the environment's token or the network:
    ```
    gi-ai <version>
    Copyright (C) 2026 Vadim Bley
    License AGPL-3.0-only: GNU Affero General Public License version 3 <https://www.gnu.org/licenses/agpl-3.0.html>
    This is free software: you are free to change and redistribute it.
    There is NO WARRANTY, to the extent permitted by law.
    Source: https://github.com/VadimBley/gi-ai
    ```
    `<version>` is the same value `gi health` reports. `--version` combined with a command is a usage error (exit 2).
19. Licence and contacts in the package: the `.deb` installs `/usr/share/doc/gi-ai/copyright` in DEP-5 format with
    `License: AGPL-3.0-only` and the full licence text; every installed source file (Python, shell launcher, man page)
    starts with `Copyright (C) 2026 Vadim Bley` and the GNU AGPL version 3 notice (without "or any later version"). The
    man page has a COPYRIGHT section with the same statement, and its REPORTING BUGS section names `bugs@gi-ai.app` for
    bugs and `https://github.com/VadimBley/gi-ai/security/advisories/new` for security problems (never by email).

## 2. Interfaces
| Command | Exit codes |
|---|---|
| `gi health [--json]` | 0 ok, 1 degraded, 2 config error |
| `gi ask Q [--verbose]` | 0 ok, 1 model error, 2 usage/config error |
| `gi init-workspace [--path DIR]` | 0 |
| `gi task new/list` | 0 ok, 2 usage error or no workspace |
| `gi selfcheck [--json]` | 0 all pass, 1 any fail, 2 config error |
| `gi --version` | 0; 2 when combined with a command |

Files read: config files (behaviour 7), `llm.token_file`, `/proc/net/route` (only for `@gateway`).
Environment read: `GI_AI_CONFIG`, `GI_AI_LLM_TOKEN`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`.
Network: for a name endpoint, one lookup through the system resolver per request (behaviour 7); otherwise only the
resolved `llm.endpoint` (`/api/chat`, `/api/tags` for `ollama`; `/api/v1/chat`, `/api/v1/models` for
`lmstudio`).

## 3. Data contracts
```json schema=health
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "SPEC-0001/health",
  "type": "object",
  "additionalProperties": false,
  "required": ["name", "version", "status", "llm", "workspace"],
  "properties": {
    "name": {"type": "string", "const": "Ĝi"},
    "version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"},
    "status": {"type": "string", "enum": ["ok", "degraded"]},
    "llm": {
      "type": "object",
      "additionalProperties": false,
      "required": ["backend", "model", "reachable"],
      "properties": {
        "backend": {"type": "string", "enum": ["ollama", "lmstudio", "echo"]},
        "model": {"type": "string", "maxLength": 200},
        "reachable": {"type": "boolean"},
        "endpoint": {"type": "string", "maxLength": 300},
        "loaded": {"type": "boolean"},
        "server_version": {"type": "string", "maxLength": 64},
        "capabilities": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "vision": {"type": "boolean"},
            "trained_for_tool_use": {"type": "boolean"},
            "max_context_length": {"type": "integer", "minimum": 0},
            "reasoning": {"type": "array", "items": {"type": "string", "maxLength": 32}}
          }
        },
        "unknown_fields": {
          "type": "array",
          "items": {"type": "string", "pattern": "^(top|model|capabilities)\\.([A-Za-z0-9_.-]{1,64}|\\?)$"}
        }
      }
    },
    "workspace": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "exists"],
      "properties": {
        "path": {"type": "string"},
        "exists": {"type": "boolean"}
      }
    }
  }
}
```

```json schema=task
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "SPEC-0001/task",
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "type", "status", "priority", "title", "created", "details"],
  "properties": {
    "id": {"type": "string", "pattern": "^GI-(REQ|BUG|VULN)-[0-9]{8}-[0-9]{3}$"},
    "type": {"type": "string", "enum": ["req", "bug", "vuln"]},
    "status": {"type": "string", "enum": ["draft", "triaged", "in-progress", "in-review", "closed"]},
    "priority": {"type": "string", "enum": ["p1", "p2", "p3", "p4"]},
    "title": {"type": "string", "minLength": 1, "maxLength": 200},
    "created": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
    "details": {"type": "object"}
  }
}
```

## 4. Flow
```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant G as gi
    participant C as Config
    participant R as /proc/net/route
    participant M as LM Studio (Windows host)
    U->>G: gi ask "question"
    G->>C: load + validate (allow_remote needed for @gateway)
    G->>R: resolve @gateway (this run only)
    G->>G: metadata check on IP literal / @gateway address: metadata? exit 2
    G->>G: token from GI_AI_LLM_TOKEN or 0600 token_file
    G->>G: length check as typed, normalise (NFKC, invisible chars removed)
    G->>G: length check normalised, neutralise markers, wrap in markers
    opt endpoint host is a name
        G->>G: one lookup, any address reaching a metadata address? exit 2, no connection
    end
    G->>M: connect (looked-up address for names), re-check peer before TLS/HTTP: metadata? exit 2
    G->>M: POST /api/v1/chat (store false, reasoning off, no tools)
    M-->>G: JSON reply (untrusted)
    G->>G: error member? tool_call? take message items only
    G->>G: strip control sequences, cap length
    G-->>U: answer (+ stats on stderr with --verbose)
```

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> triaged
    triaged --> in_progress
    in_progress --> in_review
    in_review --> closed
    closed --> [*]
```

## 5. Runtime constraints
- Python 3.11+ standard library only. Package `gi-ai`, Architecture all, installs to `/usr/lib/gi-ai`, commands `gi` and
  `gi-ai`.
- Launcher runs Python in isolated mode (`-I`).
- No network except the resolved model endpoint (and, for a name endpoint, its one resolver lookup per request). Loopback by default; a non-loopback endpoint only with `allow_remote`.
- `lmstudio` targets LM Studio ≥ 0.4.0 (native `/api/v1`); tested against fakes built from the documented shapes and,
  before a release touching the backend, smoke-tested against LM Studio 0.4.25.
- Every chat is stateless towards the model server (`store: false`); conversations exist only where Ĝi prints them.

## 6. Security & privacy (OWASP LLM Top 10 2025)
| Risk | Applies? | Control |
|---|---|---|
| LLM01 Prompt injection | yes | System prompt says user/tool text is data; question normalised (NFKC, format and pinned default-ignorable characters removed) and neutralised with markers matched across case and combining marks (B17) and wrapped in markers; residual: homoglyphs from other scripts; Ĝi has no tools; tool calls in a reply are rejected (B11) |
| LLM02 Sensitive information disclosure | yes | Loopback by default; remote only with `allow_remote` and checked as private (B6); `store: false` so LM Studio keeps no conversation (B9); token never printed/logged and sent only to local/private addresses (B15); cloud metadata addresses refused as IP literals in every embedding form, via `@gateway` and via names at connect time, redirects never followed (B7); residual: other non-standard NAT64 prefixes and translators on the path, Teredo `2001::/32` and ISATAP (`::5efe:a.b.c.d`) forms, Alibaba Cloud's 100.100.100.200 inside the shared (CGNAT) range; workspace 0700, files 0600 |
| LLM03 Supply chain | yes | Ĝi pins the model id it asks for and reports `available`/`loaded` honestly (B12); model integrity is checked outside Ĝi (ADR-0002 digest) |
| LLM04 Data and model poisoning | no | Ĝi does not train or fine-tune; model integrity is checked outside Ĝi (ADR-0002 digest) |
| LLM05 Improper output handling | yes | Output and server error text sanitised before printing (B3, B10); reasoning never printed (B11) |
| LLM06 Excessive agency | no (no tools in this spec) | No `integrations`/tools sent (B9); `tool_call` replies rejected (B11); any future tool use needs its own spec + ADR |
| LLM07 System prompt leakage | no | The system prompt is public product data (no secrets in it) |
| LLM08 Vector and embedding weaknesses | no | No embeddings or retrieval in this spec |
| LLM09 Misinformation | yes | Unchanged: answers are shown as model output; the manual says to check important answers |
| LLM10 Unbounded consumption | yes | Input/output caps, input checked as typed and after normalising (B2, B17); request timeout; optional `max_output_tokens`; reasoning off by default |

## 7. Acceptance tests
- [ ] AT-1 health JSON validates against `health` and reports `ok` with the echo backend.
- [ ] AT-2 ask refuses input over the limit with exit 2.
- [ ] AT-3 escape sequences in model output are removed.
- [ ] AT-4 workspace is 0700; audit tampering is detected by selfcheck.
- [ ] AT-5 task files validate against `task`; IDs increment per day.
- [ ] AT-6 remote endpoint refused unless allowed; unknown config keys refused.
- [ ] AT-7 `.deb` installs and `gi health` works on Ubuntu 24.04, 26.04, Debian 12, 13, Mint 22.
- [ ] AT-8 `lmstudio` ask against a fake server: the request body has exactly the members of B9 with `store: false`,
      `stream: false`, `reasoning: "off"` (absent with `reasoning = "default"`); optional members appear only when
      configured; the answer joins the `message`
      items; `reasoning` items never appear in the output.
- [ ] AT-9 a reply with a `tool_call` item → exit 1 with `unexpected tool call`; a reply without `message` items → exit 1.
- [ ] AT-10 HTTP 200 with `{"error": "..."}` → ask exits 1 with the sanitised, ≤200-character message; health `degraded`
      (both backends: `ollama` `/api/tags` and `lmstudio` `/api/v1/models`).
- [ ] AT-11 health: `reachable` true only when a `models` item's `key` equals `llm.model`; `loaded`, `capabilities`
      reported from that item; `server_version` only when present; output validates against `health`.
- [ ] AT-12 unknown fields: a fake reply with extra members at each level is reported as sorted `<level>.<name>`
      entries (max 50, invalid names as `<level>.?`), never with values; no extra members → empty or absent list.
- [ ] AT-13 `@gateway`: resolved from a fixture route table (`0100A8C0` → `192.168.0.1`); no default route → exit 2
      with the message of B14; `@gateway` without `allow_remote` → exit 2.
- [ ] AT-14 token: env var wins over file; file mode 0644 → exit 2; header sent only when a token is configured; the
      token value never appears in stdout, stderr, the workspace or exception text.
- [ ] AT-15 selfcheck `model-endpoint-private`: PASS for 127.0.0.1, `localhost`, 172.29.224.1 (with `allow_remote`),
      100.64.0.1, fe80::1; FAIL for 8.8.8.8 and for a host name; `token-file-private` FAIL for 0640.
- [ ] AT-16 `--verbose` prints the stats line on stderr for `lmstudio` and `stats: none` for `ollama`/`echo`.
- [ ] AT-17 no network call goes anywhere but the resolved endpoint (socket-level test), apart from the single resolver
      lookup of B7 for a name endpoint, observed through the fake resolver.
- [ ] AT-18 token destination: with a token configured, endpoints 127.0.0.1, `localhost`, `@gateway` (fixture
      192.168.0.1), 172.29.224.1 and fe80::1 are accepted; 8.8.8.8 and `example.org` exit 2 with the B15 message and no
      socket is opened; without a token the same public/DNS endpoints still work with `allow_remote` (unchanged).
- [ ] AT-19 `token_file` outside `~/.config/gi-ai/` (e.g. `~/secret.txt`, `~/.config/gi-ai/../x`, a symlink into the
      directory) → exit 2 and the file is never read; inside the directory with mode 0600 → used.
- [ ] AT-20 a question containing `QUESTION>>>`, `question>>>` and `<<<QUESTION` reaches the model with each replaced by
      `[marker removed]` and exactly one pair of real markers; the Ĝi eval set has a case for it.
- [ ] AT-21 HTTP 404/500 with a non-JSON body → ask exit 1 (`the model server answered HTTP <code>`), health `degraded`.
- [ ] AT-22 `gi --version` and `gi-ai --version` print the six lines of B18 exactly (version = `health` version), exit 0,
      with an unreadable config file and `GI_AI_LLM_TOKEN` set (nothing read, no socket opened); `gi --version health` → 2.
- [ ] AT-23 the built `.deb` contains `usr/share/doc/gi-ai/copyright` with `License: AGPL-3.0-only` and the licence text;
      every Python file, the launcher and the man page carry the B19 notice; the man page names `bugs@gi-ai.app` and the
      advisories URL; the isolation gate stays clean.
- [ ] AT-24 endpoints `http://169.254.169.254`, `http://169.254.170.2:80`, `http://[fd00:ec2::254]` with
      `allow_remote = true` → exit 2 with the B7 message and no socket opened, with and without a token; `@gateway`
      resolving (fixture route table) to 169.254.169.254 → exit 2; selfcheck shows `METADATA <ip>` FAIL;
      169.254.1.1 and fe80::1 still pass.
- [ ] AT-25 token_file refused (exit 2, B15 message, file never read) when `~/.config` or `~/.config/gi-ai` is a symbolic
      link, when the file is a symbolic link, when it is a hard link (link count 2) to a 0600 file elsewhere, and when it
      is a directory or FIFO; a single-link 0600 regular file in a real `~/.config/gi-ai/` is used.
- [ ] AT-26 the open is component-wise: replacing `~/.config/gi-ai` by a symbolic link between path resolution and open
      (test hook) never makes Ĝi read the other file.
- [ ] AT-27 questions with fullwidth `ＱＵＥＳＴＩＯＮ＞＞＞`, `QUES\u200bTION>>>` and `<<<\u2060QUESTION` reach the model with
      `[marker removed]` and exactly one pair of real markers; a question of exactly `limits.max_input_chars` ASCII
      characters is accepted; the Ĝi eval set gets a case; the ask prompt asset version is bumped. (Changed in 1.5.0: a
      question over the limit only after normalising is now refused, AT-30.)
- [ ] AT-28 metadata via names (socket-level, as AT-17): with `allow_remote = true` and a fake resolver, an endpoint name
      resolving to 169.254.169.254, to 169.254.170.2, to fd00:ec2::254, to `64:ff9b::a9fe:a9fe`, or to
      [192.168.0.10, 169.254.169.254] → `gi ask` and `gi health` exit 2 with the B7 message, exactly one lookup and no
      `connect`; a name resolving to 192.168.0.10 connects to that address with `Host:` = the name and no second lookup;
      a fake connection whose peer address is 169.254.169.254 → exit 2, nothing sent (for https: no TLS ClientHello;
      on success SNI and certificate name are the configured name); `localhost` resolving to 169.254.169.254 or to
      192.168.0.10 → exit 2 (B7 messages), no `connect`; a failing or empty lookup → ask exit 1 `cannot resolve <host>`,
      health `degraded`; a name with three unreachable addresses and `timeout_s = 2` gives up within about 2 seconds,
      not 6; a fake endpoint answering `302 Location: http://169.254.169.254/` → ask exit 1, health `degraded`, no second
      connect, token not sent; `gi selfcheck` does no lookup.
- [ ] AT-29 the AT-24 bypass list gains `http://[64:ff9b:1:a9fe:a9:fe00::]` (/48 layout), `http://[64:ff9b:1::a9fe:a9fe]`
      (/96 layout), `http://[64:ff9b:1:a9:fe:a9fe::]` (/56), `http://[64:ff9b:1:0:a9:fea9:fe00::]` (/64),
      `http://[64:ff9b:1:a9fe:ffa9:fe00::]` (/48, non-zero u-octet), `http://[::ffff:0:a9fe:a9fe]`,
      `http://[fd00:ec2::254%25eth0]`, `http://[::ffff:169.254.169.254%25lo]`, `http://[2002:a9fe:a9fe::1]` and `http://[2002:a9fe:aa02::]`: each →
      exit 2, no socket; `http://[2002:c0a8:1::1]` (6to4 for 192.168.0.1) and `http://[64:ff9b:1::c0a8:1]` are not
      refused as metadata.
- [ ] AT-30 a question that NFKC grows past the limit (`U+FDFA` × `limits.max_input_chars`) → exit 2 with the B2
      "after normalising" message and no socket; with a limit of 100, the question `<<<QUESTION` × 9 + `x` (100
      characters) is accepted and reaches the model as `[marker removed]` × 9 + `x` (145 characters).
- [ ] AT-31 these questions reach the model with `[marker removed]` and exactly one pair of real markers:
      `QUESTION` + `U+FE0F` + `>>>`, `<<<QUES` + `U+034F` + `TION`, `QUEST` + `U+3164` + `ION>>>`,
      `Q` + `U+0301` + `UESTION>>>`, `<<<QUÉSTION`, `<<<QUESTıON`, `QUESTION>` + `U+0301` + `>>`,
      `QUESTION>` + `U+0338` + `>>` (NFKC `≯`), `<<` + `U+0338` + `<QUESTION` (NFKC `≮`) and `QUESTION` + `U+11F00` + `>>>`
      (a mark unassigned on Python 3.11); `<<<QUESTION>>>` reaches the model as `[marker removed]>>>`; `Ñandú café` and a
      Devanagari word with vowel signs reach the model unchanged; the eval set gets a case.

## 8. Open questions
None.
