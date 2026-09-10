"""Rubric A/B: does v2 actually beat v1, and does it beat it on data it has not seen?

The failure analysis in the README named two defects. Naming them is cheap.
This measures whether the fixes worked, on two sets:

  GOLDEN SET  20 leads. **The defects were found here**, so improvement on
              this set is close to guaranteed and is not evidence of much.
              It is reported anyway because a fix that does not repair the
              case that motivated it is not a fix.

  HOLDOUT     10 leads, hand labelled and never consulted while the v2 rubric
              was written. This is the number that means something.

**Read the holdout honestly.** It is a stress set, not a random sample: 4 of
its 10 leads were constructed to exercise the two defect classes, 5 are
neutral controls, and 1 is a case neither version gets right. So it measures
whether the fixes generalise past the two leads that found them, and it does
NOT estimate a production error rate. A set weighted toward the thing you
just fixed will flatter you, and saying so is part of reporting it.

    python src/compare.py
    python src/compare.py --set holdout
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import ingest, process          # noqa: E402
from qualify import load_rubric               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SETS = {
    "golden": ("golden_set.csv", "defects were found here, so improvement is expected"),
    "holdout": ("holdout_set.csv", "never consulted while writing v2"),
}


def labels_for(name: str) -> dict[str, dict]:
    out = {}
    with (ROOT / "data" / SETS[name][0]).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["lead_id"]] = {
                "label": row["human_label"].strip(),
                "why": row["human_reasoning"].strip(),
            }
    return out


def score_set(name: str, rubric: dict) -> dict:
    leads = ingest(ROOT / "data" / SETS[name][0])
    truth = labels_for(name)
    rows, correct = [], 0
    for lead in leads:
        result = process(lead, rubric)
        human = truth[lead["lead_id"]]["label"]
        system = result["decision"]["route"]
        ok = human == system
        correct += ok
        rows.append({
            "lead_id": lead["lead_id"],
            "human": human,
            "system": system,
            "score": result["decision"]["score"],
            "fit": result["decision"].get("fit_score"),
            "ok": ok,
            "floor": result["decision"].get("fit_floor_applied", False),
            "why": result["decision"]["explanation"],
            "human_why": truth[lead["lead_id"]]["why"],
        })
    return {"rows": rows, "correct": correct, "total": len(rows)}


def compare(name: str, v1: dict, v2: dict, verbose: bool = True) -> dict:
    a = score_set(name, v1)
    b = score_set(name, v2)

    fixed, broken, still_wrong, unchanged_ok = [], [], [], 0
    for ra, rb in zip(a["rows"], b["rows"]):
        if not ra["ok"] and rb["ok"]:
            fixed.append(rb)
        elif ra["ok"] and not rb["ok"]:
            broken.append(rb)
        elif not ra["ok"] and not rb["ok"]:
            still_wrong.append(rb)
        else:
            unchanged_ok += 1

    if verbose:
        note = SETS[name][1]
        print(f"\n{'=' * 88}")
        print(f"  {name.upper()} SET  ({a['total']} leads, {note})")
        print("=" * 88)
        print(f"  {'lead':<8}{'human':<14}{'v1':<14}{'v2':<14}{'v1':>4}{'v2':>4}   change")
        print("  " + "-" * 84)
        for ra, rb in zip(a["rows"], b["rows"]):
            change = (
                "FIXED" if (not ra["ok"] and rb["ok"]) else
                "REGRESSED" if (ra["ok"] and not rb["ok"]) else
                "still wrong" if not rb["ok"] else ""
            )
            print(f"  {ra['lead_id']:<8}{ra['human']:<14}{ra['system']:<14}"
                  f"{rb['system']:<14}{'y' if ra['ok'] else 'n':>4}"
                  f"{'y' if rb['ok'] else 'n':>4}   {change}")
        print("  " + "-" * 84)
        pa = 100 * a["correct"] / a["total"]
        pb = 100 * b["correct"] / b["total"]
        print(f"  v1 {a['correct']}/{a['total']} ({pa:.0f}%)"
              f"   ->   v2 {b['correct']}/{b['total']} ({pb:.0f}%)"
              f"   delta {pb - pa:+.0f} points")
        print(f"  fixed {len(fixed)}   regressed {len(broken)}   "
              f"still wrong {len(still_wrong)}   unchanged correct {unchanged_ok}")

        for r in fixed:
            print(f"\n  FIXED  {r['lead_id']}  now '{r['system']}'"
                  + ("  (fit floor applied)" if r["floor"] else ""))
            print(f"         {r['why'][:150]}")
        for r in broken:
            print(f"\n  REGRESSED  {r['lead_id']}  human '{r['human']}', v2 '{r['system']}'")
            print(f"         {r['why'][:150]}")
        for r in still_wrong:
            print(f"\n  STILL WRONG  {r['lead_id']}  human '{r['human']}', v2 '{r['system']}'")
            print(f"         human : {r['human_why']}")
            print(f"         system: {r['why'][:130]}")

    return {"v1": a, "v2": b, "fixed": fixed, "regressed": broken,
            "still_wrong": still_wrong}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["golden", "holdout", "both"], default="both")
    args = ap.parse_args()

    v1 = load_rubric(ROOT / "rubric.json")
    v2 = load_rubric(ROOT / "rubric-v2.json")

    print("=" * 88)
    print("  RUBRIC A/B: v1.0.0 vs v2.0.0".center(88))
    print("=" * 88)
    for entry in v2["changelog"]:
        print(f"\n  change, found by {entry['found_by']}")
        print(f"    defect       : {entry['defect'][:120]}")
        print(f"    rejected fix : {entry['rejected_fix'][:120]}")
        print(f"    shipped fix  : {entry['fix'][:120]}")

    names = ["golden", "holdout"] if args.set == "both" else [args.set]
    out = {n: compare(n, v1, v2) for n in names}

    if args.set == "both":
        print(f"\n{'=' * 88}")
        print("  SUMMARY".center(88))
        print("=" * 88)
        for n in names:
            a, b = out[n]["v1"], out[n]["v2"]
            print(f"  {n:<9} v1 {a['correct']}/{a['total']}"
                  f"  ->  v2 {b['correct']}/{b['total']}"
                  f"   ({len(out[n]['fixed'])} fixed, "
                  f"{len(out[n]['regressed'])} regressed)")
        print("\n  The holdout is the number that means something, and it is a stress")
        print("  set rather than a random sample: 4 of 10 were built to exercise the")
        print("  two defect classes. It shows the fixes generalise past the leads that")
        print("  found them. It does NOT estimate a production error rate.")
        print("=" * 88)


if __name__ == "__main__":
    main()
