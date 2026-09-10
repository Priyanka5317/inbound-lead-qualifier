"""Drift monitoring on the score distribution.

If average scores rise while the lead source has not changed, the rubric has
drifted, not the market. That sentence is the whole reason this file exists.

Reads the decision log rather than re-running the pipeline, so it works on
whatever actually happened in production instead of on a fresh replay.

    python src/drift.py
    python src/drift.py --baseline out/decisions-golden.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def summarise(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r["qualification"]["abstained"]]
    scores = [r["decision"]["score"] for r in scored]
    routes: dict[str, int] = {}
    for r in rows:
        routes[r["decision"]["route"]] = routes.get(r["decision"]["route"], 0) + 1

    criterion_hit: dict[str, int] = {}
    for r in scored:
        for b in r["qualification"]["breakdown"]:
            if b["awarded"] > 0:
                criterion_hit[b["id"]] = criterion_hit.get(b["id"], 0) + 1

    return {
        "n": len(rows),
        "n_scored": len(scored),
        "mean": statistics.mean(scores) if scores else 0.0,
        "median": statistics.median(scores) if scores else 0.0,
        "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        "abstain_rate": routes.get("abstain", 0) / len(rows) if rows else 0.0,
        "routes": routes,
        "criterion_hit_rate": {
            k: v / len(scored) for k, v in criterion_hit.items()
        } if scored else {},
    }


def histogram(rows: list[dict], width: int = 40) -> str:
    buckets = {f"{lo:>3}-{lo + 19:<3}": 0 for lo in range(0, 100, 20)}
    for r in rows:
        if r["qualification"]["abstained"]:
            continue
        lo = min(int(r["decision"]["score"]) // 20 * 20, 80)
        buckets[f"{lo:>3}-{lo + 19:<3}"] += 1
    top = max(buckets.values()) or 1
    return "\n".join(
        f"  {label}  {'#' * int(width * n / top):<{width}} {n}"
        for label, n in buckets.items()
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="out/decisions-golden.jsonl")
    ap.add_argument("--current", default="out/decisions-sample.jsonl")
    args = ap.parse_args()

    base_path, cur_path = ROOT / args.baseline, ROOT / args.current
    missing = [p for p in (base_path, cur_path) if not p.exists()]
    if missing:
        raise SystemExit(
            "run the pipeline first:\n"
            "  python src/pipeline.py --golden\n"
            "  python src/pipeline.py"
        )

    base, cur = summarise(load(base_path)), summarise(load(cur_path))

    print("=" * 66)
    print("SCORE DISTRIBUTION, BASELINE".center(66))
    print("=" * 66)
    print(histogram(load(base_path)))
    print(f"\n  n={base['n']}  scored={base['n_scored']}  "
          f"mean={base['mean']:.1f}  median={base['median']:.1f}  "
          f"sd={base['stdev']:.1f}  abstain={base['abstain_rate']:.0%}")

    print("\n" + "=" * 66)
    print("DRIFT CHECK vs CURRENT BATCH".center(66))
    print("=" * 66)
    delta = cur["mean"] - base["mean"]
    print(f"  baseline mean : {base['mean']:.1f}  (n={base['n_scored']})")
    print(f"  current mean  : {cur['mean']:.1f}  (n={cur['n_scored']})")
    print(f"  delta         : {delta:+.1f}")
    print(f"  abstain rate  : {base['abstain_rate']:.0%} -> {cur['abstain_rate']:.0%}")

    # A small batch moving a lot is noise. Flag the shape, not the number.
    if cur["n_scored"] < 30:
        print("\n  NOTE: current batch is under 30 scored leads, so a mean shift "
              "\n        this size is not yet distinguishable from sampling noise.")
    elif abs(delta) >= 10:
        print("\n  ALERT: mean moved 10+ points. Check whether the lead source "
              "\n         changed before concluding the market did.")

    print("\n  criterion hit rates, baseline vs current:")
    for key in sorted(set(base["criterion_hit_rate"]) | set(cur["criterion_hit_rate"])):
        b = base["criterion_hit_rate"].get(key, 0.0)
        c = cur["criterion_hit_rate"].get(key, 0.0)
        arrow = "  " if abs(c - b) < 0.15 else " <-"
        print(f"    {key:<26} {b:>5.0%} -> {c:>5.0%}{arrow}")
    print("=" * 66)


if __name__ == "__main__":
    main()
