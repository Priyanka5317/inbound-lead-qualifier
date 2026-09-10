"""Step 2 of 6: enrich.

Resolves a company from an email domain. Talks to a provider through one
adapter function, so swapping the offline provider for Apollo or Clay is a
single change and nothing downstream moves.

The output shape is the point of this module. Two fields do the work that
separates a script from something someone would run in production:

    confidence         how much the provider trusts this record
    unresolved_fields  what it could not find, recorded rather than dropped

A script returns what it found. A system also returns what it missed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "enrichment_source.json"

# Every field this pipeline expects a provider to return.
EXPECTED_FIELDS = [
    "company_name",
    "employee_count",
    "industry",
    "hq_country",
    "tech_stack",
    "funding_stage",
    "annual_revenue",
]


def _load_source() -> dict:
    with DATA.open(encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def domain_of(email: str) -> str:
    return email.strip().lower().rsplit("@", 1)[-1]


def enrich(email: str, provider: str = "offline") -> dict:
    """Return a company record for the domain behind `email`.

    Never raises on a miss. An unknown domain returns a record whose every
    field is unresolved, which the scorer then handles through the abstain
    path rather than by scoring zeros.
    """
    domain = domain_of(email)
    raw = _load_source().get(domain)

    record = {
        "domain": domain,
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": provider,
    }

    if raw is None:
        for field in EXPECTED_FIELDS:
            record[field] = None
        record["confidence"] = 0.0
        record["unresolved_fields"] = list(EXPECTED_FIELDS)
        record["provider_hit"] = False
        return record

    for field in EXPECTED_FIELDS:
        record[field] = raw.get(field)
    record["confidence"] = raw.get("confidence", 0.0)
    record["provider_hit"] = True
    record["unresolved_fields"] = [
        f for f in EXPECTED_FIELDS if record.get(f) is None
    ]
    return record


def render(record: dict) -> str:
    """Human readable form, matching Appendix B.1 of the bootcamp."""
    order = [
        "company_name", "domain", "employee_count", "industry", "hq_country",
        "tech_stack", "funding_stage", "enriched_at", "source", "confidence",
        "unresolved_fields",
    ]
    width = max(len(k) for k in order)
    lines = []
    for key in order:
        value = record.get(key)
        if isinstance(value, list):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        lines.append(f"  {key.ljust(width)} : {value}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    email = sys.argv[1] if len(sys.argv) > 1 else "jane.doe@northwind-health.com"
    print(f"INPUT  : {email}")
    print("OUTPUT :")
    print(render(enrich(email)))
