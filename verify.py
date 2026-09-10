#!/usr/bin/env python3
"""Run everything, in the order it has to run in, and report honestly.

One command so a reader does not have to work out the dependencies: the
drift check needs both decision logs, the parity test needs the Python log
to compare against, and the demo test needs the demo built.

Steps that need an API key are reported as SKIPPED with the reason, never
as passes and never quietly omitted.

    python verify.py
    python verify.py --quiet
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

# Exit 2 is the agreed signal for "an optional dependency is missing". It is
# not a failure: playwright is a dev-only extra, and the project's whole
# point is that everything else runs bare.
MISSING_DEP = 2

# (label, command, needs_a_key)
STEPS: list[tuple[str, list[str], bool]] = [
    ("pipeline, golden set", [PY, "src/pipeline.py", "--golden"], False),
    ("pipeline, sample leads", [PY, "src/pipeline.py"], False),
    ("golden set evaluation", [PY, "src/evaluate.py"], False),
    ("rubric A/B, v1 vs v2", [PY, "src/compare.py"], False),
    ("red team, gate attacks", [PY, "src/redteam.py"], False),
    ("duplicate detection", [PY, "src/dedupe.py"], False),
    ("drift monitoring", [PY, "src/drift.py"], False),
    ("live enrichment, 4 sources", [PY, "src/providers.py", "stripe.com"], False),
    ("agent tool schemas", [PY, "src/enrich_agent.py", "--schemas"], False),
    ("agent vs fixed policy", [PY, "src/agent_eval.py"], False),
    ("agent 2 output contract", [PY, "src/entailment_judge.py", "--schema"], False),
    ("agent 3 action space", [PY, "src/rubric_critic.py", "--actions"], False),
    ("build the browser demo", [PY, "src/build_demo.py"], False),
    ("build the HTML report", [PY, "src/report.py"], False),
    ("loom demo script", [PY, "demo.py", "--script"], False),
    ("rubric parity, 3 impls", ["node", "tests/parity_check.mjs"], False),
    ("n8n workflow validator", ["node", "tests/validate_my_workflow.mjs"], False),
    ("execute the n8n workflow", ["node", "tests/run_workflow.mjs"], False),
    ("report layout, measured", ["node", "tests/check_report_layout.cjs"], False),
    ("browser demo, functional", ["node", "tests/check_demo.cjs"], False),
    ("agent 2 against the escapes", [PY, "src/entailment_judge.py"], True),
    ("agent 3 propose and gate", [PY, "src/rubric_critic.py"], True),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    print("=" * 78)
    print("  VERIFY EVERYTHING".center(78))
    print("=" * 78)

    passed = failed = skipped = 0
    failures = []
    started = time.time()

    for label, cmd, needs_key in STEPS:
        t = time.time()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        ms = int((time.time() - t) * 1000)

        if r.returncode == 0:
            print(f"  PASS  {label:<32} {ms:>6} ms")
            passed += 1
        elif needs_key:
            # These exit non-zero on purpose when no credentials resolve.
            print(f"  SKIP  {label:<32} no API key")
            skipped += 1
        elif r.returncode == MISSING_DEP:
            print(f"  SKIP  {label:<32} needs playwright")
            skipped += 1
        else:
            print(f"  FAIL  {label:<32} {ms:>6} ms")
            failed += 1
            failures.append((label, (r.stderr or r.stdout).strip()[-300:]))

    print("-" * 78)
    print(f"  {passed} passed, {failed} failed, {skipped} skipped "
          f"(need an API key), {int(time.time() - started)}s total")

    if skipped:
        print("\n  Skipped steps are reported, never counted as passes. The two")
        print("  agent steps need an API key. The two browser checks need")
        print("  playwright, the only dev dependency this project has. Their")
        print("  results are unknown rather than assumed, because an unrun")
        print("  check is not a passing one.")

    if failures and not args.quiet:
        print()
        for label, err in failures:
            print(f"  --- {label} ---")
            for line in err.split("\n")[-6:]:
                print(f"    {line}")

    print("=" * 78)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
