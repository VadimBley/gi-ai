# Security policy

Ĝi is free software maintained in spare time and provided without warranty (see [COPYING](COPYING)).

## Reporting a vulnerability

Report security problems **only privately** through GitHub:
<https://github.com/VadimBley/gi-ai/security/advisories/new>

Please do not report security problems by email or in public issues. I aim to reply within
14 days. Ordinary bugs go to <bugs@gi-ai.app>.

## What protects you

- Ĝi only connects to the model endpoint you configure, on this computer by default. Proxy
  settings are ignored and redirects are not followed.
- An API token is sent only to addresses on this computer or in a private network, and a token
  file must live inside `~/.config/gi-ai/`.
- Model output is treated as untrusted and cleaned before it is shown.
- Your workspace is private (0700) and has a tamper-evident audit log (`gi selfcheck`).
- Every change passes tests, static analysis, secret scanning, an isolation gate that keeps
  development material out of the package, and human review. Releases have build-provenance
  attestations, and the APT repository is signed by the maintainer with the key whose
  fingerprint is listed in the [README](README.md).

## What Ĝi does not protect against

- A malicious local model or a compromised user account.
- Wrong or harmful answers from the model. Check important answers yourself.

## Supported versions

Only the latest release receives fixes.
