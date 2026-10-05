# Ĝi (gi-ai)

*Ĝi* is Esperanto for "it". Ĝi is a small, private AI assistant for Linux that runs
**entirely on your own computer**. It asks a local language model ([Ollama](https://ollama.com) or
[LM Studio](https://lmstudio.ai)), keeps private task notes with a tamper-evident audit log and
checks its own security baseline.

- Ubuntu 24.04 / 26.04, Debian 12 / 13, Linux Mint 22 and Ubuntu on WSL (amd64)
- No cloud, no telemetry, no accounts. The only network connection goes to the model server you
  configure, on this computer by default.
- Model output is treated as untrusted and cleaned before it is shown.

## Install

Ĝi is installed from its signed APT repository at <https://gi-ai.app/apt/>.

1. Download the signing key and check its fingerprint before you trust it:

   ```bash
   KEYDIR="$(mktemp -d)"
   curl -fsSLo "$KEYDIR/gi-ai.asc" https://gi-ai.app/apt/gi-ai.asc
   gpg --show-keys "$KEYDIR/gi-ai.asc"
   ```

   The fingerprint must be exactly (gpg may print it without spaces):

   ```
   D523 F5BF 268D FD9F CD08 2391 E1B6 1810 1C64 54A0
   ```

   If it differs, stop and do not install.

2. Add the key and the repository, then install:

   ```bash
   sudo install -d -m 0755 /etc/apt/keyrings
   sudo gpg --dearmor -o /etc/apt/keyrings/gi-ai.gpg "$KEYDIR/gi-ai.asc"
   echo "deb [signed-by=/etc/apt/keyrings/gi-ai.gpg] https://gi-ai.app/apt stable main" \
     | sudo tee /etc/apt/sources.list.d/gi-ai.list
   sudo apt update && sudo apt install gi-ai
   ```

## Connect a model

### Ollama

Install [Ollama](https://ollama.com), pull a model and you are done. These are Ĝi's defaults:

```bash
ollama pull qwen3:4b
gi health
```

### LM Studio

Ĝi uses the native REST API of LM Studio 0.4.0 or newer. Put this into
`~/.config/gi-ai/gi.toml`:

```toml
[llm]
backend = "lmstudio"
endpoint = "http://127.0.0.1:1234"
model = "qwen/qwen3.5-9b"
```

When LM Studio runs on the Windows host and Ĝi runs in WSL, use the special host `@gateway`.
It is replaced on every run by the default-route gateway address, which is the Windows host:

```toml
[llm]
backend = "lmstudio"
endpoint = "http://@gateway:1234"
model = "qwen/qwen3.5-9b"
allow_remote = true
timeout_s = 300
```

If LM Studio requires an API token, set `GI_AI_LLM_TOKEN` or put the token in a file that only
you can read (`chmod 600`) inside `~/.config/gi-ai/` and set
`token_file = "~/.config/gi-ai/lmstudio.token"`. Ĝi sends the token only to addresses on this
computer or in a private network, and never prints or stores it.

## First commands

```bash
gi health                       # is the model server reachable?
gi ask "Explain what a hash-chained audit log is in two sentences."
gi ask --verbose "What is the capital of Slovakia?"   # adds token counts and speed
gi init-workspace               # your private workspace (readable only by you)
gi task new --type req --title "Try Ĝi"
gi task list
gi selfcheck                    # check the local security baseline
gi --version
man gi-ai
```

## Updates

Updates arrive with your normal system updates:

```bash
sudo apt update && sudo apt upgrade
```

## Verifying releases

Every release on GitHub has the `.deb`, a `SHA256SUMS` file, an SPDX SBOM and signed
build-provenance attestations. The APT repository itself is signed with the key above.

```bash
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify gi-ai_<version>_all.deb --repo VadimBley/gi-ai
```

## How Ĝi is made

Ĝi is developed with AI assistance (Claude Code); every change is reviewed by a human, and
releases are signed by the maintainer.

## Contact

- Bugs: <bugs@gi-ai.app>
- Security problems: only privately via
  <https://github.com/VadimBley/gi-ai/security/advisories/new> (never by email), see
  [SECURITY.md](SECURITY.md)
- Commercial licences: <license@gi-ai.app>
- Website: <https://gi-ai.app/>

## Licence

Ĝi is free software, licensed under the GNU Affero General Public License v3.0 only
(AGPL-3.0-only). You may use, study, share and modify it, including commercially. If you
distribute Ĝi or a modified version, or let others use a modified version over a network, you
must make the source of your version available under the same licence. Commercial licences for
using Ĝi without these obligations are available upon request: license@gi-ai.app.

[![GNU AGPLv3](.github/assets/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.html)

The full licence text is in [COPYING](COPYING).
