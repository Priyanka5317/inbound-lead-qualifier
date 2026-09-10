"""Step 5 of 6: draft, behind a validation gate.

Every sentence in an outbound draft must name the enrichment field it rests
on, and the gate checks that claim independently of whoever wrote it. A
sentence with no support is dropped. A sentence carrying a number that is not
in the record is dropped. If anything is dropped the draft is rejected rather
than quietly sent thinner.

Two drafters ship on purpose:

    grounded  a template that can only interpolate real fields
    llm_sim   a stand in for a fluent model that embellishes

The second one exists so the gate can be shown catching something. A gate
that has never rejected anything is not evidence.
"""

from __future__ import annotations

import re

GENERIC_NUMBERS = {"1", "2", "3", "5", "10", "15", "20", "30"}


def _record_number_strings(record: dict) -> set[str]:
    found: set[str] = set()
    for value in record.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            found.add(str(int(value)))
        elif isinstance(value, str):
            found.update(re.findall(r"\d+", value))
        elif isinstance(value, list):
            for item in value:
                found.update(re.findall(r"\d+", str(item)))
    return found


def _value_appears(value, sentence: str) -> bool:
    """Is the cited field's value actually reflected in the sentence?"""
    low = sentence.lower()
    if isinstance(value, bool):
        return True                      # a flag shapes tone, not wording
    if isinstance(value, (int, float)):
        return str(int(value)) in low.replace(",", "")
    if isinstance(value, str):
        head = value.lower().split()[0] if value.split() else value.lower()
        return head in low
    if isinstance(value, list):
        return any(str(v).lower() in low for v in value)
    return False


def validate(claims: list[dict], record: dict) -> dict:
    """Enforce the contract. Returns kept sentences and every rejection."""
    allowed_numbers = _record_number_strings(record)
    kept, rejected = [], []

    for claim in claims:
        text, field = claim["text"], claim.get("support")

        if not field:
            rejected.append({"text": text, "why": "no supporting field declared"})
            continue
        if field not in record:
            rejected.append({"text": text, "why": f"cites unknown field '{field}'"})
            continue
        if record.get(field) is None:
            rejected.append({"text": text, "why": f"cites unresolved field '{field}'"})
            continue
        if not _value_appears(record[field], text):
            rejected.append({
                "text": text,
                "why": f"does not reflect the value of '{field}' ({record[field]!r})",
            })
            continue

        stray = [
            n for n in re.findall(r"\d[\d,]*", text)
            if n.replace(",", "") not in allowed_numbers
            and n.replace(",", "") not in GENERIC_NUMBERS
        ]
        if stray:
            rejected.append({
                "text": text,
                "why": f"unsupported number(s) {stray} not present in the record",
            })
            continue

        kept.append(claim)

    return {
        "kept": kept,
        "rejected": rejected,
        "passed": not rejected,
        "claims_total": len(claims),
        "claims_rejected": len(rejected),
    }


def _grounded_claims(lead: dict, record: dict) -> list[dict]:
    claims = []
    if record.get("company_name"):
        claims.append({
            "support": "company_name",
            "text": f"I saw {record['company_name']} come through our inbound form.",
        })
    if record.get("employee_count"):
        claims.append({
            "support": "employee_count",
            "text": f"At {record['employee_count']} people you are past the point "
                    f"where manual triage keeps up.",
        })
    if record.get("industry"):
        claims.append({
            "support": "industry",
            "text": f"Most of the {record['industry'].lower()} teams we work with "
                    f"hit the same wall.",
        })
    if record.get("tech_stack"):
        claims.append({
            "support": "tech_stack",
            "text": f"Since you already run {record['tech_stack'][0]}, the data side "
                    f"is mostly plumbing we have done before.",
        })
    return claims


def _llm_sim_claims(lead: dict, record: dict) -> list[dict]:
    """A fluent drafter. Grounded where it can be, embellished where it cannot."""
    claims = _grounded_claims(lead, record)
    if record.get("funding_stage"):
        # Invents an amount. The stage is real; the figure is not in the record.
        claims.append({
            "support": "funding_stage",
            "text": f"Congratulations on the {record['funding_stage']} round, "
                    f"raising $40M is no small thing.",
        })
    # A claim with nothing behind it at all.
    claims.append({
        "support": None,
        "text": "Your competitors are already automating this.",
    })
    return claims


def _claude_claims(lead: dict, record: dict) -> list[dict]:
    """The real model. Imported lazily so the SDK stays an optional extra."""
    from llm_drafter import claude_claims
    return claude_claims(lead, record)


DRAFTERS = {
    "grounded": _grounded_claims,      # template, cannot invent
    "llm_sim": _llm_sim_claims,        # simulator, invents on purpose
    "claude": _claude_claims,          # the real model, same gate applied
}


def draft(lead: dict, record: dict, drafter: str = "grounded") -> dict:
    claims = DRAFTERS[drafter](lead, record)
    gate = validate(claims, record)

    first_name = (lead.get("contact_name") or "there").split()[0]
    body = " ".join(c["text"] for c in gate["kept"])
    message = (
        f"Hi {first_name},\n\n{body}\n\n"
        "Worth a short call to see whether it applies to your setup?\n\n"
        "Priyanka"
    )

    return {
        "drafter": drafter,
        "message": message if gate["passed"] else None,
        "sent": gate["passed"],
        "gate": gate,
        "message_if_gate_ignored": (
            f"Hi {first_name},\n\n"
            + " ".join(c["text"] for c in claims)
            + "\n\nWorth a short call to see whether it applies to your setup?\n\nPriyanka"
        ),
    }
