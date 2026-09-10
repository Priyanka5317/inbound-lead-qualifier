"""Agent 2: outbound QA. Does the cited field actually support the sentence?

The GTM job this does: stop your outbound from telling a prospect something
that is not true. A rep sending one confidently wrong line about a company
costs more than a hundred unsent emails.

**This exists because the red team found a hole and named the fix.** The
regex gate checks two things: is the cited field real and present, and is
every number in the record. It cannot check whether a sentence *asserts more
than the field supports*. Three attacks walked through it:

    "Since you already run Snowflake, your data team is clearly mature."
    "Northwind Health is evaluating vendors this quarter."
    "Healthcare software teams like yours are mostly still doing this by hand."

Every one cites a real field whose value genuinely appears, and carries no
invented digit. `redteam.py` concluded: *closing it needs an entailment
check, which is a model-graded eval rather than a regex.* This is that.

It runs as a SECOND layer, after the regex gate, not instead of it. The regex
layer is free, deterministic and catches the crude attacks; the model is slow
and costs money, so it only sees what survived. Putting the cheap
deterministic filter first is the whole reason this is affordable to run on
every draft.

    python src/entailment_judge.py --schema     the output contract, no key
    python src/entailment_judge.py              judge the 3 known escapes
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODEL = "claude-opus-5"

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["entailed", "overreach"],
            "description": (
                "entailed: the field's value fully supports the sentence. "
                "overreach: the sentence asserts something beyond what the "
                "field states, even if the field is mentioned accurately."
            ),
        },
        "overreaching_span": {
            "type": "string",
            "description": (
                "The exact words that go beyond the field. Empty string when "
                "the verdict is entailed."
            ),
        },
        "why": {
            "type": "string",
            "description": "One sentence, plain, aimed at the person who wrote it.",
        },
    },
    "required": ["verdict", "overreaching_span", "why"],
    "additionalProperties": False,
}

SYSTEM = """You check whether a sentence in a sales email is supported by the
one data field it claims to rest on.

You are the second of two checks. A deterministic filter has already
confirmed the field exists, has a value, and that every number in the
sentence appears in the record. So the crude problems are gone. Your job is
the subtle one:

    does the field's value ENTAIL the sentence, or does the sentence assert
    more than the field states?

Entailed means a reasonable person reading only that field would agree the
sentence is true. Overreach means the sentence adds a judgement, an
intention, a market claim, a causal story, or a quantity that the field does
not carry, even when the field itself is quoted correctly.

Worked examples, all with a real field and a correct value:

  field tech_stack = ["HubSpot","Snowflake","AWS"]
  "You already run Snowflake."                        entailed
  "Since you run Snowflake, your data team is mature."  overreach
      the stack says what they run, not how good the team is

  field company_name = "Northwind Health"
  "I saw Northwind Health come through our form."     entailed
  "Northwind Health is evaluating vendors this quarter." overreach
      the name says nothing about buying intent

  field industry = "Healthcare software"
  "You are in healthcare software."                    entailed
  "Healthcare software teams are mostly doing this by hand." overreach
      a claim about the whole market, not about this company

Be strict. A sales email that is confidently wrong about a prospect costs
more than one that is short. When in doubt, call it overreach and say which
words to cut."""


class JudgeUnavailable(RuntimeError):
    """No SDK or no credentials. Actionable, not mysterious."""


def _client():
    try:
        import anthropic
    except ImportError as err:
        raise JudgeUnavailable("pip install anthropic") from err
    c = anthropic.Anthropic()
    if not (c.api_key or getattr(c, "auth_token", None)):
        raise JudgeUnavailable(
            "no credentials resolved. Set ANTHROPIC_API_KEY, or run "
            "`ant auth login`."
        )
    return c


def judge(text: str, field: str, record: dict) -> dict:
    """Is `text` entailed by `record[field]`? Returns the verdict and the span."""
    client = _client()
    extra = {}
    if ws := os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        extra["anthropic-workspace-id"] = ws

    prompt = (
        f"Field cited: {field}\n"
        f"Field value: {json.dumps(record.get(field), default=str)}\n\n"
        f"Full record, for context only. The sentence must rest on the cited "
        f"field alone:\n{json.dumps({k: v for k, v in record.items() if not k.startswith('_')}, indent=2, default=str)}\n\n"
        f"Sentence:\n{text}"
    )

    response = client.beta.messages.create(
        extra_headers=extra or None,
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise JudgeUnavailable("the request was declined by safety classifiers")

    body = next((b.text for b in response.content if b.type == "text"), None)
    if not body:
        raise JudgeUnavailable("no text block in the response")
    out = json.loads(body)
    out["judged_by"] = MODEL
    return out


def validate_with_judge(claims: list[dict], record: dict) -> dict:
    """Both layers. Regex first because it is free, then the model on survivors."""
    from draft import validate

    layer1 = validate(claims, record)
    kept, escalated = [], []

    for claim in layer1["kept"]:
        verdict = judge(claim["text"], claim["support"], record)
        if verdict["verdict"] == "overreach":
            escalated.append({
                "text": claim["text"],
                "why": f"overreach: {verdict['why']}",
                "span": verdict["overreaching_span"],
                "layer": "entailment",
            })
        else:
            kept.append(claim)

    rejected = [{**r, "layer": "regex"} for r in layer1["rejected"]] + escalated
    return {
        "kept": kept,
        "rejected": rejected,
        "passed": not rejected,
        "claims_total": len(claims),
        "claims_rejected": len(rejected),
        "caught_by_regex": len(layer1["rejected"]),
        "caught_by_judge": len(escalated),
    }


# The three that walked through the regex gate. If the judge is worth its
# cost it catches these, and it must NOT reject the honest control.
ESCAPES = [
    ("tech_stack",
     "Since you already run Snowflake, your data team is clearly mature.", True),
    ("company_name",
     "Northwind Health is evaluating vendors this quarter.", True),
    ("industry",
     "Healthcare software teams like yours are mostly still doing this by hand.", True),
    ("company_name",
     "I saw Northwind Health come through our inbound form.", False),
    ("employee_count",
     "At 340 people manual triage stops scaling.", False),
]


def main() -> None:
    if "--schema" in sys.argv:
        print(json.dumps(VERDICT_SCHEMA, indent=2))
        return

    from providers import enrich
    record = enrich("ops@northwind-health.com", "offline")

    print("=" * 88)
    print("  ENTAILMENT JUDGE vs THE THREE THAT ESCAPED THE REGEX GATE".center(88))
    print("=" * 88)

    try:
        _client()
    except JudgeUnavailable as err:
        print(f"\n  NOT RUN: {err}\n")
        print("  The three sentences below defeated the regex gate. Each cites a")
        print("  real field whose value appears, and carries no invented number,")
        print("  so a field-level check has no basis to reject them:\n")
        for field, text, should in ESCAPES:
            if should:
                print(f'    cites {field}\n      "{text}"')
        print("\n  This layer is what the red team said was needed. It is written")
        print("  and its output contract is inspectable with --schema, but it has")
        print("  not been run, so its catch rate is UNKNOWN and is reported as")
        print("  unknown rather than assumed.")
        print("=" * 88)
        raise SystemExit(1)

    caught = correct = 0
    for field, text, should_catch in ESCAPES:
        v = judge(text, field, record)
        over = v["verdict"] == "overreach"
        ok = over == should_catch
        correct += ok
        caught += over and should_catch
        print(f"\n  {'OK  ' if ok else 'MISS'}  {v['verdict']:<10} cites {field}")
        print(f'        "{text}"')
        if over:
            print(f"        span : {v['overreaching_span']}")
        print(f"        why  : {v['why']}")

    total_should = sum(1 for _, _, s in ESCAPES if s)
    print("\n" + "-" * 88)
    print(f"  escapes caught      : {caught}/{total_should}")
    print(f"  verdicts correct    : {correct}/{len(ESCAPES)} "
          f"(includes {len(ESCAPES) - total_should} honest controls that must pass)")
    print("=" * 88)


if __name__ == "__main__":
    main()
