#!/usr/bin/env python3
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

"""Run evals/gi/cases.toml against the real CLI with a local Ollama model.

Sampling is fixed for reproducible runs. Eval-only settings, never runtime defaults: Ĝi
sends no sampling options to Ollama, so the script derives local Ollama models from
--model whose Modelfile sets temperature and seed, and points Ĝi at those.

- Blocking cases: one run at temperature 0 with seed EVAL_SEED. A failure fails the job.
- Cases with `blocking = false`: `runs` runs with seeds EVAL_SEED, EVAL_SEED + 1, ... at
  MEASURE_TEMPERATURE. Printed as "resisted <k>/<runs>"; never fails the job.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL_SEED = 42
MEASURE_TEMPERATURE = 0.7


def derive_model(base: str, name: str, temperature: float, seed: int, workdir: Path) -> str:
    """Create (or replace) a local Ollama model `name` = `base` with fixed sampling."""
    modelfile = workdir / f"{name}.Modelfile"
    modelfile.write_text(
        f"FROM {base}\nPARAMETER temperature {temperature}\nPARAMETER seed {seed}\n",
        encoding="utf-8",
    )
    subprocess.run(["ollama", "create", name, "-f", str(modelfile)], check=True, timeout=600)
    return name


def ask(
    question: str, model: str, home: Path, document: str | None = None
) -> subprocess.CompletedProcess[str]:
    cfg = home / f"{model}.toml"
    cfg.write_text(f'[llm]\nmodel = "{model}"\ntimeout_s = 300\n', encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GI_AI_CONFIG": str(cfg),
        "PYTHONPATH": str(REPO / "runtime"),
    }
    argv = [sys.executable, "-m", "gi_ai", "ask"]
    if document is not None:
        doc = home / "document.txt"
        doc.write_text(document, encoding="utf-8")
        argv += ["--file", str(doc)]
    return subprocess.run(
        [*argv, question],
        env=env,
        stdin=subprocess.DEVNULL,  # an inherited pipe would be read as a document
        capture_output=True,
        text=True,
        timeout=400,
    )


def passes(case: dict, r: subprocess.CompletedProcess[str]) -> bool:
    answer = r.stdout.lower()
    ok = r.returncode == 0
    ok &= not any(s.lower() in answer for s in case.get("must_not", []))
    if case.get("must_any"):
        ok &= any(s.lower() in answer for s in case["must_any"])
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    cases = tomllib.loads((REPO / "evals/gi/cases.toml").read_text(encoding="utf-8"))["case"]
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        fixed = derive_model(args.model, "gi-eval-fixed", 0, EVAL_SEED, home)
        failed = blocking = 0
        for case in cases:
            if case.get("blocking", True):
                blocking += 1
                r = ask(case["question"], fixed, home, case.get("document"))
                ok = passes(case, r)
                print(f"{'PASS' if ok else 'FAIL'} {case['name']}")
                if not ok:
                    print(f"  answer: {r.stdout[:400]!r} stderr: {r.stderr[:200]!r}")
                    failed += 1
                continue
            runs = int(case.get("runs", 5))
            resisted = 0
            for i in range(runs):
                seed = EVAL_SEED + i
                name = f"gi-eval-measure-{seed}"
                model = derive_model(args.model, name, MEASURE_TEMPERATURE, seed, home)
                r = ask(case["question"], model, home, case.get("document"))
                ok = passes(case, r)
                resisted += ok
                print(f"  {case['name']} seed {seed}: {'resisted' if ok else 'obeyed'}")
                if not ok:
                    print(f"    answer: {r.stdout[:200]!r} stderr: {r.stderr[:200]!r}")
            print(f"MEASURE {case['name']}: resisted {resisted}/{runs} (non-blocking)")
    print(f"gi evals: {blocking - failed}/{blocking} blocking cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
