"""Agent 3: tune the ICP from rep disagreement, then let the eval decide.

The GTM job this does: every week a salesperson rejects leads the scoring
model sent them, and accepts ones it did not. That disagreement is the most
valuable signal a revenue team produces and it is almost always thrown away.
This reads it and proposes the next rubric version.

**The agent proposes. The measurement decides.** Its output is never trusted
and never written over the live rubric. The harness applies the patch to a
copy, runs the full A/B on the golden set and the held-out set, and accepts
it only if it improves the held-out score without regressing anything. A
proposal that fails is printed with its score and discarded.

That gate matters more than the agent. A model asked to improve a rubric
will always produce something; the question is whether it survives contact
with data it has not seen, and only the eval can answer that.

**The action space is deliberately small.** The agent cannot rewrite the
rubric, invent criteria, or restructure scoring. It can adjust thresholds,
adjust the points on an existing criterion, and extend the industry
taxonomy. A bounded action space is what makes an auto-applied patch safe to
run at all.

    python src/rubric_critic.py --actions      the action space, no key
    python src/rubric_critic.py                propose, apply, evaluate
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
MODEL = "claude-opus-5"

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "description": "Between 1 and 3 changes. Fewer and better beats more.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "set_threshold",
                            "set_criterion_points",
                            "add_industry_synonym",
                            "add_industry_outside_icp",
                        ],
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "For set_threshold: one of book_a_call_at_or_above, "
                            "nurture_at_or_above, minimum_fit_to_book. "
                            "For set_criterion_points: the criterion id. "
                            "For the industry actions: the provider's literal "
                            "industry string, lowercased."
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "The new number as a string for the first two "
                            "actions. For add_industry_synonym, the canonical "
                            "term it maps to: healthcare, fintech or saas. "
                            "For add_industry_outside_icp, an empty string."
                        ),
                    },
                    "found_by": {
                        "type": "string",
                        "description": "The lead id whose disagreement motivates this.",
                    },
                    "defect": {
                        "type": "string",
                        "description": "What is wrong, in one sentence.",
                    },
                    "rejected_fix": {
                        "type": "string",
                        "description": (
                            "A plausible alternative you considered and turned "
                            "down, and why it is worse."
                        ),
                    },
                },
                "required": ["action", "target", "value", "found_by",
                             "defect", "rejected_fix"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["changes"],
    "additionalProperties": False,
}

SYSTEM = """You tune a B2B lead scoring rubric from the cases where a human
salesperson disagreed with it.

You will be shown the current rubric and every lead the system routed
differently from the human, with both sides' reasoning.

Constraints, and they are hard:
1. Only the four listed actions exist. You cannot add criteria, remove them,
   restructure scoring, or change how a test works.
2. Propose at most three changes. One good change beats three speculative
   ones, and every change you propose will be measured against data you
   cannot see.
3. Fix causes, not symptoms. If a lead scored wrongly because a category
   label was not recognised, extend the taxonomy. Do not move a threshold to
   make one lead come out right, because that shifts every other lead too.
4. For every change, name a plausible fix you REJECTED and say why it is
   worse. If you cannot name one, you have not thought about it.
5. A held-out set you will never see decides whether your patch ships. Do
   not tune for the examples in front of you.

The rubric's existing design positions, which you should preserve:
missing data is never scored as zero; an unrecognised industry counts as
missing rather than as a miss; fit and intent are not substitutes."""


class CriticUnavailable(RuntimeError):
    pass


def _client():
    try:
        import anthropic
    except ImportError as err:
        raise CriticUnavailable("pip install anthropic") from err
    c = anthropic.Anthropic()
    if not (c.api_key or getattr(c, "auth_token", None)):
        raise CriticUnavailable("no credentials resolved. Set ANTHROPIC_API_KEY.")
    return c


def apply_patch(rubric: dict, changes: list[dict]) -> tuple[dict, list[str]]:
    """Apply a patch to a COPY. Returns the new rubric and what was applied.

    Every action is validated before it lands. A patch that names an unknown
    threshold or criterion is skipped with a note, never guessed at.
    """
    out = copy.deepcopy(rubric)
    applied = []

    for c in changes:
        action, target, value = c["action"], c["target"], c["value"]

        if action == "set_threshold":
            if target not in ("book_a_call_at_or_above", "nurture_at_or_above",
                              "minimum_fit_to_book"):
                applied.append(f"SKIPPED unknown threshold '{target}'")
                continue
            try:
                out["routing"][target] = int(float(value))
            except ValueError:
                applied.append(f"SKIPPED non numeric threshold '{value}'")
                continue
            applied.append(f"{target} -> {out['routing'][target]}")

        elif action == "set_criterion_points":
            found = None
            for section in ("fit", "intent"):
                for crit in out[section]["criteria"]:
                    if crit["id"] == target:
                        found = crit
            if not found:
                applied.append(f"SKIPPED unknown criterion '{target}'")
                continue
            try:
                found["points"] = int(float(value))
            except ValueError:
                applied.append(f"SKIPPED non numeric points '{value}'")
                continue
            applied.append(f"{target} points -> {found['points']}")

        elif action in ("add_industry_synonym", "add_industry_outside_icp"):
            crit = next((c2 for c2 in out["fit"]["criteria"]
                         if c2["id"] == "industry_in_icp"), None)
            if not crit or crit.get("test") != "taxonomy":
                applied.append("SKIPPED: this rubric has no taxonomy criterion")
                continue
            if action == "add_industry_synonym":
                if value not in crit["canonical"]:
                    applied.append(f"SKIPPED '{target}' maps to unknown '{value}'")
                    continue
                crit.setdefault("synonyms", {})[target.lower()] = value
                applied.append(f"synonym '{target}' -> {value}")
            else:
                crit.setdefault("known_outside_icp", []).append(target.lower())
                applied.append(f"'{target}' marked outside ICP")

        else:
            applied.append(f"SKIPPED unknown action '{action}'")

    out["version"] = "3.0.0-candidate"
    out["parent"] = rubric.get("version")
    out["changelog"] = [
        {"found_by": c["found_by"], "defect": c["defect"],
         "rejected_fix": c["rejected_fix"],
         "fix": f"{c['action']} {c['target']} = {c['value']}"}
        for c in changes
    ]
    return out, applied


def gather_disagreements(rubric: dict) -> list[dict]:
    from compare import score_set
    rows = []
    for name in ("golden", "holdout"):
        for r in score_set(name, rubric)["rows"]:
            if not r["ok"]:
                rows.append({**r, "set": name})
    return rows


def propose(rubric: dict, disagreements: list[dict]) -> dict:
    client = _client()
    extra = {}
    if ws := os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        extra["anthropic-workspace-id"] = ws

    # Only golden-set disagreements are shown. The holdout is what judges the
    # patch, so showing it would be marking your own homework.
    visible = [d for d in disagreements if d["set"] == "golden"]

    prompt = (
        "Current rubric:\n"
        + json.dumps(rubric, indent=2)[:6000]
        + "\n\nLeads where the salesperson disagreed with the system:\n"
        + json.dumps([{
            "lead_id": d["lead_id"], "human_said": d["human"],
            "system_said": d["system"], "score": d["score"], "fit": d["fit"],
            "human_reasoning": d["human_why"],
            "system_reasoning": d["why"][:220],
        } for d in visible], indent=2)
        + "\n\nPropose at most three changes."
    )

    response = client.beta.messages.create(
        extra_headers=extra or None,
        model=MODEL, max_tokens=16000, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": PATCH_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise CriticUnavailable("declined by safety classifiers")
    body = next((b.text for b in response.content if b.type == "text"), None)
    return json.loads(body)


def main() -> None:
    if "--actions" in sys.argv:
        print("=" * 80)
        print("  ACTION SPACE, deliberately small".center(80))
        print("=" * 80)
        for a in PATCH_SCHEMA["properties"]["changes"]["items"]["properties"]["action"]["enum"]:
            print(f"  {a}")
        print("\n  The agent cannot add or remove criteria, change how a test")
        print("  works, or restructure scoring. A bounded action space is what")
        print("  makes an auto-applied patch safe enough to run at all.")
        print("=" * 80)
        return

    from compare import compare
    from qualify import load_rubric

    v2 = load_rubric(ROOT / "rubric-v2.json")

    print("=" * 88)
    print("  RUBRIC CRITIC: propose from disagreement, let the eval decide".center(88))
    print("=" * 88)

    disagreements = gather_disagreements(v2)
    print(f"\n  disagreements with the current rubric: {len(disagreements)}")
    for d in disagreements:
        print(f"    [{d['set']}] {d['lead_id']}  human '{d['human']}' vs "
              f"system '{d['system']}' at {d['score']}")

    if not disagreements:
        print("\n  Nothing to learn from. The rubric agrees with every label.")
        return

    try:
        patch = propose(v2, disagreements)
    except CriticUnavailable as err:
        print(f"\n  NOT RUN: {err}")
        print("\n  The disagreements above are real and computed. The proposal")
        print("  step needs a key. Its action space is inspectable with")
        print("  --actions, and the acceptance gate below runs on whatever it")
        print("  proposes, so nothing here depends on trusting the model.")
        print("=" * 88)
        raise SystemExit(1)

    print(f"\n  proposed {len(patch['changes'])} change(s):")
    for c in patch["changes"]:
        print(f"\n    {c['action']}  {c['target']} = {c['value']}")
        print(f"      found by     : {c['found_by']}")
        print(f"      defect       : {c['defect']}")
        print(f"      rejected fix : {c['rejected_fix']}")

    candidate, applied = apply_patch(v2, patch["changes"])
    print("\n  applied:")
    for a in applied:
        print(f"    {a}")

    print("\n  " + "-" * 84)
    print("  THE GATE: does it survive the held-out set?")
    before = compare("holdout", v2, v2, verbose=False)["v2"]
    after = compare("holdout", v2, candidate, verbose=False)["v2"]
    g_before = compare("golden", v2, v2, verbose=False)["v2"]
    g_after = compare("golden", v2, candidate, verbose=False)["v2"]

    print(f"    golden   {g_before['correct']}/{g_before['total']} -> "
          f"{g_after['correct']}/{g_after['total']}")
    print(f"    holdout  {before['correct']}/{before['total']} -> "
          f"{after['correct']}/{after['total']}")

    improved = after["correct"] > before["correct"]
    regressed = g_after["correct"] < g_before["correct"]

    out = ROOT / "out" / "rubric-v3-candidate.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(candidate, indent=2), encoding="utf-8")

    print()
    if improved and not regressed:
        print("  ACCEPTED: improves the held-out score with no regression.")
        print(f"  Candidate written to {out.relative_to(ROOT)} for review.")
        print("  It is NOT written over the live rubric. A human still reads it.")
    elif regressed:
        print("  REJECTED: regressed the golden set. Discarded.")
    else:
        print("  REJECTED: no held-out improvement, so it bought nothing.")
        print("  Discarded. A model asked to improve something will always")
        print("  produce a change; the gate is what makes that safe.")
    print("=" * 88)


if __name__ == "__main__":
    main()
