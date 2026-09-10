"""Steps 1 and 6 of 6, plus the orchestration between them.

Ingest a lead, enrich, qualify, route, draft, log. Every decision is written
with the inputs that produced it, so any routing call can be reconstructed
afterwards instead of argued about.

    python src/pipeline.py                      run the sample leads
    python src/pipeline.py --golden             run the 20 lead golden set
    python src/pipeline.py --drafter llm_sim    show the gate rejecting work
    python src/pipeline.py --lead L-005         one lead, verbose
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from draft import draft as draft_message          # noqa: E402
from providers import enrich, render              # noqa: E402
from qualify import load_rubric, qualify          # noqa: E402
from route import route                           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
FORM_FIELDS = ("requested_demo", "pricing_page_visits", "emails_opened")


def _coerce(row: dict) -> dict:
    """Form input arrives as text. Blank stays None so abstain can fire."""
    out = dict(row)
    raw = (row.get("requested_demo") or "").strip().lower()
    out["requested_demo"] = True if raw == "true" else False if raw == "false" else None
    for field in ("pricing_page_visits", "emails_opened"):
        value = (row.get(field) or "").strip()
        out[field] = int(value) if value.isdigit() else None
    return out


def ingest(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [_coerce(r) for r in csv.DictReader(fh)]


def process(lead: dict, rubric: dict, drafter: str = "grounded",
            provider: str = "offline") -> dict:
    record = enrich(lead["email"], provider)

    facts = dict(record)
    for field in FORM_FIELDS:
        facts[field] = lead.get(field)

    qualification = qualify(facts, rubric)
    decision = route(qualification, rubric)

    drafted = None
    if decision["route"] in ("book a call", "nurture"):
        try:
            drafted = draft_message(lead, record, drafter)
        except Exception as err:                      # noqa: BLE001
            # A drafter being unavailable must not lose the routing decision
            # that was already made. Record why and carry on.
            drafted = {
                "drafter": drafter,
                "message": None,
                "sent": False,
                "unavailable": f"{type(err).__name__}: {err}",
                "gate": {"kept": [], "rejected": [], "passed": False,
                         "claims_total": 0, "claims_rejected": 0},
            }

    return {
        "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lead_id": lead.get("lead_id"),
        "email": lead["email"],
        "inputs": {
            "form": {f: lead.get(f) for f in FORM_FIELDS},
            "enrichment": record,
        },
        "qualification": qualification,
        "decision": decision,
        "draft": drafted,
    }


def write_log(results: list[dict], name: str) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / name
    with path.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", action="store_true", help="run the golden set")
    ap.add_argument("--drafter", default="grounded",
                    choices=["grounded", "llm_sim", "claude"])
    ap.add_argument("--provider", default="offline", choices=["offline", "live"],
                    help="offline reproduces the golden set; live hits the network")
    ap.add_argument("--lead", help="run one lead id verbosely")
    args = ap.parse_args()

    if args.golden or args.lead:
        source = ROOT / "data" / "golden_set.csv"
    elif args.provider == "live":
        # Real domains, because the golden set's companies are invented and a
        # live provider would correctly resolve nothing for them.
        source = ROOT / "data" / "live_leads.csv"
    else:
        source = ROOT / "data" / "leads.csv"
    leads = ingest(source)
    rubric = load_rubric()

    if args.lead:
        match = [l for l in leads if l["lead_id"] == args.lead]
        if not match:
            raise SystemExit(f"no lead {args.lead} in {source.name}")
        result = process(match[0], rubric, args.drafter, args.provider)
        print(f"LEAD    : {result['lead_id']}  {result['email']}")
        print("ENRICHED:")
        print(render(result["inputs"]["enrichment"]))
        print("\nSCORING :")
        for row in result["qualification"]["breakdown"]:
            print(f"  {row['awarded']:>3}/{row['available']:<3} "
                  f"{row['verdict']:<8} {row['label']}")
        print(f"\n  TOTAL {result['qualification']['score']}/100")
        print(f"\nROUTE   : {result['decision']['route']} "
              f"-> {result['decision']['destination']}")
        print(f"WHY     : {result['decision']['explanation']}")
        if result["draft"]:
            if result["draft"].get("unavailable"):
                # Distinct from a gate rejection. The gate never ran.
                print(f"\nDRAFT   : not attempted, drafter unavailable")
                print(f"  {result['draft']['unavailable']}")
                return
            gate = result["draft"]["gate"]
            print(f"\nGATE    : {len(gate['kept'])} kept, "
                  f"{gate['claims_rejected']} rejected, passed={gate['passed']}")
            for bad in gate["rejected"]:
                print(f"  REJECTED: {bad['text']}\n            -> {bad['why']}")
            if result["draft"]["sent"]:
                print("\nDRAFT   :\n" + result["draft"]["message"])
            else:
                print("\nDRAFT   : withheld, gate rejected a claim")
        return

    results = [process(l, rubric, args.drafter, args.provider) for l in leads]
    log = write_log(results, f"decisions-{'golden' if args.golden else 'sample'}.jsonl")

    counts: dict[str, int] = {}
    for r in results:
        counts[r["decision"]["route"]] = counts.get(r["decision"]["route"], 0) + 1

    print(f"processed {len(results)} leads from {source.name}")
    for key in ("book a call", "nurture", "disqualify", "abstain"):
        if key in counts:
            print(f"  {key:<12} {counts[key]}")

    drafted = [r for r in results if r["draft"]]
    if drafted:
        blocked = [r for r in drafted if not r["draft"]["sent"]]
        rejected_claims = sum(r["draft"]["gate"]["claims_rejected"] for r in drafted)
        total_claims = sum(r["draft"]["gate"]["claims_total"] for r in drafted)
        print(f"\ndrafter '{args.drafter}': {len(drafted)} drafts attempted")
        print(f"  drafts withheld by gate : {len(blocked)}/{len(drafted)}")
        print(f"  claims rejected         : {rejected_claims}/{total_claims}")

    print(f"\ndecision log -> {log.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
