"""Step 4 of 6: route.

Four destinations, not three. `needs_human` is a first class outcome rather
than an error state, which is the whole reason the abstain path exists.
"""

from __future__ import annotations

BOOK = "book a call"
NURTURE = "nurture"
DISQUALIFY = "disqualify"
ABSTAIN = "abstain"

DESTINATION = {
    BOOK: "sales_queue",
    NURTURE: "nurture_sequence",
    DISQUALIFY: "closed_lost",
    ABSTAIN: "needs_human",
}


def route(qualification: dict, rubric: dict) -> dict:
    if qualification["abstained"]:
        return {
            "route": ABSTAIN,
            "destination": DESTINATION[ABSTAIN],
            "score": qualification["score"],
            "explanation": qualification["reason"],
        }

    score = qualification["score"]
    thresholds = rubric["routing"]

    # Fit floor, added in rubric v2, found by L-019. Fit and intent are not
    # substitutes: a 2600 person hospital with no warehouse cleared 70 on
    # intent alone. Below the floor a lead can still reach nurture, so
    # nothing is lost, it is only slowed.
    floor = thresholds.get("minimum_fit_to_book")
    fit_blocked = floor is not None and qualification["fit_score"] < floor

    if score >= thresholds["book_a_call_at_or_above"] and not fit_blocked:
        decision = BOOK
    elif score >= thresholds["book_a_call_at_or_above"] and fit_blocked:
        decision = NURTURE
    elif score >= thresholds["nurture_at_or_above"]:
        decision = NURTURE
    else:
        decision = DISQUALIFY

    explanation = qualification["reason"]
    if fit_blocked and score >= thresholds["book_a_call_at_or_above"]:
        explanation = (
            f"scored {score} but fit is only {qualification['fit_score']}, "
            f"below the floor of {floor}. Intent alone does not promote to a "
            f"call, so this drops to nurture. " + explanation
        )

    return {
        "route": decision,
        "destination": DESTINATION[decision],
        "score": score,
        "fit_score": qualification["fit_score"],
        "fit_floor_applied": bool(fit_blocked),
        "explanation": explanation,
    }
