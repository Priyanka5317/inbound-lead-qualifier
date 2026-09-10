"""Duplicate company detection, plus a survivorship rule.

Week 2 of the bootcamp asks you to create a duplicate company record in a
HubSpot sandbox and work out how you would detect it automatically, and flags
that exercise as the one that comes up in interviews. This is that answer,
written as code so it can be run rather than described.

Four detectors, cheapest first, because in a real CRM you cannot afford to
fuzzy match every pair against every other pair:

    1  exact domain              free, catches almost nothing in practice
    2  domain root, any TLD      catches .com / .io / .co splits
    3  normalised legal name     strips Inc, Corp, LLC, Ltd, punctuation
    4  fuzzy name similarity     difflib, only within the same country

Then the part most implementations skip: deciding which record survives.
Detecting a duplicate is the easy half. Merging one without losing data is
where CRM hygiene projects actually fail.

    python src/dedupe.py
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

LEGAL_SUFFIXES = [
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "llc", "llp", "plc", "gmbh", "bv", "ab", "pbc",
    "holdings", "group", "technologies", "technology", "labs", "the",
]

FUZZY_THRESHOLD = 0.87


def normalise_name(name: str) -> str:
    """Northwind Health, Inc. and northwind health become the same string."""
    text = re.sub(r"[^\w\s]", " ", (name or "").lower())
    words = [w for w in text.split() if w not in LEGAL_SUFFIXES]
    return " ".join(words)


def domain_root(domain: str) -> str:
    """northwind-health.com and northwind-health.io share a root."""
    host = (domain or "").lower().strip()
    host = re.sub(r"^(www\.|mail\.)", "", host)
    return host.rsplit(".", 1)[0] if "." in host else host


def completeness(record: dict) -> int:
    return sum(
        1 for k, v in record.items()
        if k not in ("domain", "confidence") and v not in (None, "", [])
    )


def find_duplicates(records: list[dict]) -> list[dict]:
    """Return candidate duplicate pairs, each with the rule that caught it."""
    found = []

    for a, b in combinations(records, 2):
        rule = confidence = None

        if a["domain"] and a["domain"] == b["domain"]:
            rule, confidence = "exact_domain", 1.00
        elif domain_root(a["domain"]) and domain_root(a["domain"]) == domain_root(b["domain"]):
            rule, confidence = "domain_root_any_tld", 0.95
        elif normalise_name(a["company_name"]) and \
                normalise_name(a["company_name"]) == normalise_name(b["company_name"]):
            rule, confidence = "normalised_legal_name", 0.90
        else:
            # Fuzzy is last and is fenced by country, because "Meridian Care"
            # in the US and in Germany are usually two real companies.
            if a.get("hq_country") and a.get("hq_country") == b.get("hq_country"):
                ratio = SequenceMatcher(
                    None,
                    normalise_name(a["company_name"]),
                    normalise_name(b["company_name"]),
                ).ratio()
                if ratio >= FUZZY_THRESHOLD:
                    rule, confidence = "fuzzy_name_same_country", round(ratio, 3)

        if rule:
            survivor, merged_from = survivorship(a, b)
            found.append({
                "rule": rule,
                "match_confidence": confidence,
                "record_a": a["company_name"] + " <" + a["domain"] + ">",
                "record_b": b["company_name"] + " <" + b["domain"] + ">",
                "survivor": survivor["company_name"] + " <" + survivor["domain"] + ">",
                "fields_recovered": merged_from,
            })

    return found


def survivorship(a: dict, b: dict) -> tuple[dict, list[str]]:
    """Pick the surviving record, then backfill it from the loser.

    Order: more complete wins, then higher provider confidence. A merge that
    keeps the 'better' record and discards the other loses real data, so any
    field the survivor is missing is taken from the record being merged away.
    """
    key = lambda r: (completeness(r), r.get("confidence") or 0.0)
    survivor, loser = (a, b) if key(a) >= key(b) else (b, a)

    recovered = [
        field for field, value in loser.items()
        if survivor.get(field) in (None, "", []) and value not in (None, "", [])
    ]
    return survivor, recovered


def _load() -> list[dict]:
    with (DATA / "enrichment_source.json").open(encoding="utf-8") as fh:
        raw = json.load(fh)
    records = []
    for domain, body in raw.items():
        if domain.startswith("_"):
            continue
        records.append({"domain": domain, **body})

    # The deliberate duplicates the bootcamp asks you to create by hand.
    records += [
        {   # same company, .io instead of .com, and a legal suffix
            "domain": "northwind-health.io", "company_name": "Northwind Health, Inc.",
            "employee_count": 340, "industry": "Healthcare software",
            "hq_country": "United States", "tech_stack": ["Snowflake"],
            "funding_stage": None, "annual_revenue": "42M", "confidence": 0.64,
        },
        {   # same company, name spelled slightly differently, same country
            "domain": "cascadefintech.co.uk", "company_name": "Cascade FinTech Ltd",
            "employee_count": None, "industry": "Fintech",
            "hq_country": "United Kingdom", "tech_stack": ["Snowflake", "Looker"],
            "funding_stage": "Series C", "annual_revenue": None, "confidence": 0.58,
        },
    ]
    return records


def main() -> None:
    records = _load()
    dupes = find_duplicates(records)

    print("=" * 78)
    print(f"DUPLICATE COMPANY DETECTION over {len(records)} records".center(78))
    print("=" * 78)

    if not dupes:
        print("  no duplicate candidates found")
        return

    for d in dupes:
        print(f"\n  RULE       : {d['rule']}  (confidence {d['match_confidence']})")
        print(f"  record A   : {d['record_a']}")
        print(f"  record B   : {d['record_b']}")
        print(f"  survivor   : {d['survivor']}")
        print(f"  recovered  : {d['fields_recovered'] or 'nothing, survivor was complete'}")

    print("\n" + "-" * 78)
    print(f"  {len(dupes)} duplicate pair(s) from {len(records)} records.")
    print("  Detection is the easy half. The survivorship rule above is the half")
    print("  that decides whether merging loses data, and it is what the")
    print("  interview question is really about.")
    print("=" * 78)


if __name__ == "__main__":
    main()
