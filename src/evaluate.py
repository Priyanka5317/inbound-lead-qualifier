"""The number.

Runs the system against the 20 hand labelled leads in data/golden_set.csv and
reports agreement, abstain behaviour, and the cases it got wrong while
confident. All twenty were labelled before the system ran once.

The three buckets are not equally bad, which is why they are counted apart:

    agreement            system matched the human label
    correct abstain      system declined and the human also declined
    wrong with confidence  system committed to a route and was wrong

The third is the only one that costs anything in production. A confident
mistake gets acted on; an abstain gets looked at.

    python src/evaluate.py
    python src/evaluate.py --drafter llm_sim
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import ingest, process, write_log   # noqa: E402
from qualify import load_rubric                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drafter", default="grounded", choices=["grounded", "llm_sim"])
    args = ap.parse_args()

    leads = ingest(ROOT / "data" / "golden_set.csv")
    labels = {}
    with (ROOT / "data" / "golden_set.csv").open(encoding="utf-8-sig", newline="") as fh:
        import csv
        for row in csv.DictReader(fh):
            labels[row["lead_id"]] = {
                "label": row["human_label"].strip(),
                "why": row["human_reasoning"].strip(),
            }

    rubric = load_rubric()
    results = [process(l, rubric, args.drafter) for l in leads]
    write_log(results, "decisions-golden.jsonl")

    rows, agree, correct_abstain, wrong_confident, wrong_abstain = [], 0, 0, [], []

    for result in results:
        lead_id = result["lead_id"]
        human = labels[lead_id]["label"]
        system = result["decision"]["route"]
        score = result["decision"]["score"]
        match = human == system

        if match and system == "abstain":
            correct_abstain += 1
            agree += 1
        elif match:
            agree += 1
        elif system == "abstain":
            wrong_abstain.append((lead_id, human, score))
        else:
            wrong_confident.append({
                "lead_id": lead_id, "human": human, "system": system,
                "score": score, "human_why": labels[lead_id]["why"],
                "system_why": result["decision"]["explanation"],
            })

        rows.append((lead_id, human, system, score, "yes" if match else "NO"))

    total = len(results)
    print("=" * 78)
    print("GOLDEN SET EVALUATION".center(78))
    print("=" * 78)
    print(f"{'lead':<8}{'human label':<15}{'system':<15}{'score':>6}  match")
    print("-" * 78)
    for lead_id, human, system, score, match in rows:
        print(f"{lead_id:<8}{human:<15}{system:<15}{score:>6}  {match}")

    print("-" * 78)
    print(f"  Tested on {total} hand labelled leads.")
    print(f"  Agreement with human label   : {agree}/{total} "
          f"({100 * agree / total:.0f}%)")
    print(f"  Abstained                    : {correct_abstain}/{total} "
          f"(all correct)" if not wrong_abstain else
          f"  Abstained                    : {correct_abstain + len(wrong_abstain)}/{total}")
    print(f"  Wrong with confidence        : {len(wrong_confident)}/{total}")

    if wrong_confident:
        print("\n  FAILURE ANALYSIS")
        for bad in wrong_confident:
            print(f"    {bad['lead_id']}  human said '{bad['human']}', "
                  f"system said '{bad['system']}' at {bad['score']}")
            print(f"      human reasoning : {bad['human_why']}")
            print(f"      system reasoning: {bad['system_why']}")

    drafted = [r for r in results if r["draft"]]
    if drafted:
        withheld = [r for r in drafted if not r["draft"]["sent"]]
        rejected = sum(r["draft"]["gate"]["claims_rejected"] for r in drafted)
        claims = sum(r["draft"]["gate"]["claims_total"] for r in drafted)
        print(f"\n  VALIDATION GATE (drafter '{args.drafter}')")
        print(f"    Drafts attempted           : {len(drafted)}")
        print(f"    Drafts withheld by the gate: {len(withheld)}")
        print(f"    Unsupported claims caught  : {rejected}/{claims} "
              f"({100 * rejected / claims:.1f}%)" if claims else "")
        reasons = Counter(
            bad["why"].split("(")[0].split("'")[0].strip()
            for r in drafted for bad in r["draft"]["gate"]["rejected"]
        )
        for why, n in reasons.most_common():
            print(f"      {n:>3}  {why}")
    print("=" * 78)


if __name__ == "__main__":
    main()
