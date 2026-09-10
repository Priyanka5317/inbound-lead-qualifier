"""Step 3 of 6: qualify.

Scores a lead against rubric.json. The rubric is data, authored before any
prompt existed, and this module is nothing more than an interpreter for it.
That ordering is deliberate: if the rubric lived inside a prompt you could
not diff it, test it, or explain a score to a salesperson who disagrees.

The decision worth defending in an interview is the abstain rule. Missing
data is not zero. A lead whose employee count simply was not found is not a
small company, and scoring it as one silently disqualifies good leads.
"""

from __future__ import annotations

import json
from pathlib import Path

RUBRIC_PATH = Path(__file__).resolve().parent.parent / "rubric.json"


def load_rubric(path: Path | None = None) -> dict:
    with (path or RUBRIC_PATH).open(encoding="utf-8") as fh:
        return json.load(fh)


def _is_missing(value) -> bool:
    """Absent, not empty. An empty tech_stack is a known answer; None is not."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _evaluate(criterion: dict, value) -> bool:
    test = criterion["test"]
    if test == "between":
        return isinstance(value, (int, float)) and criterion["min"] <= value <= criterion["max"]
    if test == "keyword_any":
        return isinstance(value, str) and any(
            k in value.lower() for k in criterion["keywords"]
        )
    if test == "list_contains_any":
        if not isinstance(value, list):
            return False
        blob = " ".join(str(v).lower() for v in value)
        return any(k in blob for k in criterion["keywords"])
    if test == "in_set":
        return value in criterion["values"]
    if test == "is_true":
        return value is True
    if test == "gte":
        return isinstance(value, (int, float)) and value >= criterion["value"]
    if test == "taxonomy":
        return _taxonomy(criterion, value)
    raise ValueError(f"unknown test: {test}")


def _taxonomy(criterion: dict, value) -> bool | None:
    """Three-state industry match. Added in rubric v2, found by L-005.

    A keyword list is the wrong instrument for an industry taxonomy: the
    provider writes "Medical devices" and the list says "healthcare", so a
    good lead loses 20 points to a vocabulary mismatch rather than to a fact.

    Returns True (in ICP), False (known and outside it), or **None meaning
    unrecognised**, which the caller treats as missing rather than as zero.
    That third state is the whole point. Scoring an unknown label as zero is
    the same silent disqualification the abstain rule exists to prevent, one
    level up.
    """
    if not isinstance(value, str):
        return None
    text = value.lower().strip()

    for canonical in criterion["canonical"]:
        if canonical in text:
            return True
    for phrase, canonical in criterion.get("synonyms", {}).items():
        if phrase in text:
            return canonical in criterion["canonical"]
    for outside in criterion.get("known_outside_icp", []):
        if outside in text:
            return False

    # Genuinely unrecognised. Say so rather than guessing.
    return None if criterion.get("unrecognised_is_missing") else False


def qualify(facts: dict, rubric: dict | None = None) -> dict:
    """Score `facts` (enrichment record merged with lead form fields).

    Returns the score, the per criterion breakdown, and either a route or an
    abstain with the reason attached.
    """
    rubric = rubric or load_rubric()
    abstain_at = rubric["routing"]["abstain_rule"]["points"]

    breakdown: list[dict] = []
    blocking_missing: list[str] = []
    score = 0

    for section in ("fit", "intent"):
        for criterion in rubric[section]["criteria"]:
            value = facts.get(criterion["field"])
            missing = _is_missing(value)
            awarded = 0

            # A taxonomy test can report "present but unrecognised", which is
            # a third outcome the original two-state check could not express.
            if not missing and criterion["test"] == "taxonomy":
                if _taxonomy(criterion, value) is None:
                    missing = True

            if missing:
                if criterion["points"] >= abstain_at:
                    blocking_missing.append(criterion["field"])
                verdict = "unrecognised" if value not in (None, "", []) else "missing"
            else:
                met = _evaluate(criterion, value)
                awarded = criterion["points"] if met else 0
                verdict = "met" if met else "not met"
            score += awarded
            breakdown.append({
                "section": section,
                "id": criterion["id"],
                "label": criterion["label"],
                "field": criterion["field"],
                "value": value,
                "verdict": verdict,
                "awarded": awarded,
                "available": criterion["points"],
            })

    fit_score = sum(b["awarded"] for b in breakdown if b["section"] == "fit")
    intent_score = sum(b["awarded"] for b in breakdown if b["section"] == "intent")

    result = {
        "score": score,
        "fit_score": fit_score,
        "intent_score": intent_score,
        "breakdown": breakdown,
        "abstained": bool(blocking_missing),
        "missing_blocking_fields": blocking_missing,
    }

    if blocking_missing:
        result["reason"] = (
            "insufficient evidence: "
            + ", ".join(blocking_missing)
            + f" unresolved and each is worth {abstain_at}+ points"
        )
    else:
        won = [b["label"] for b in breakdown if b["awarded"] > 0]
        lost = [b["label"] for b in breakdown if b["verdict"] == "not met"]
        result["reason"] = (
            f"fit {fit_score}/{rubric['fit']['max']}, "
            f"intent {intent_score}/{rubric['intent']['max']}. "
            + ("Met: " + "; ".join(won) + ". " if won else "")
            + ("Missed: " + "; ".join(lost) + "." if lost else "")
        ).strip()

    return result


if __name__ == "__main__":
    from enrich import enrich

    facts = enrich("ops@northwind-health.com")
    facts.update({"requested_demo": True, "pricing_page_visits": 3, "emails_opened": 5})
    out = qualify(facts)
    print(f"score {out['score']}  abstained={out['abstained']}")
    for row in out["breakdown"]:
        print(f"  {row['awarded']:>3}/{row['available']:<3} {row['verdict']:<8} {row['label']}")
    print("\n" + out["reason"])
