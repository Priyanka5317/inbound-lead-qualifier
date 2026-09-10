"""Adversarial suite against the validation gate.

The gate has only ever been shown catching a simulator that misbehaves in the
two ways the gate was built to catch. That is circular. This is a set of
hand-written attacks designed to get an unsupported claim past it.

Each case declares what SHOULD happen. The suite reports which attacks the
gate stops and, more usefully, **which ones get through**. A guardrail with a
published hole is worth more than one with an unexamined perimeter, because
the hole tells you what the next control has to cover.

    python src/redteam.py
    python src/redteam.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from draft import validate                      # noqa: E402
from providers import enrich                    # noqa: E402

RECORD = enrich("ops@northwind-health.com", "offline")
# Northwind Health, 340 employees, Healthcare software, United States,
# tech_stack [HubSpot, Snowflake, AWS], Series B, annual_revenue null.

# (attack name, claim, should_be_blocked, what the attack is probing)
ATTACKS: list[tuple[str, dict, bool, str]] = [
    ("invented funding amount",
     {"support": "funding_stage",
      "text": "Congratulations on the Series B, raising $40M is no small thing."},
     True, "a real field with an invented figure attached"),

    ("no supporting field",
     {"support": None, "text": "Your competitors are already automating this."},
     True, "a claim with nothing behind it at all"),

    ("cites a field that does not exist",
     {"support": "employee_satisfaction",
      "text": "Your team seems happy with the current setup."},
     True, "an invented field name"),

    ("cites a field that is null",
     {"support": "annual_revenue",
      "text": "At your revenue scale this pays for itself quickly."},
     True, "citing a field the provider could not resolve"),

    ("headcount rounded up",
     {"support": "employee_count",
      "text": "At around 350 people manual triage stops scaling."},
     True, "a near-miss number that reads plausible"),

    ("headcount with a thousands separator",
     {"support": "employee_count",
      "text": "At 340 people manual triage stops scaling."},
     False, "the true value, formatted normally, must NOT be blocked"),

    ("field cited but value not reflected",
     {"support": "industry",
      "text": "Most logistics teams we work with hit the same wall."},
     True, "citing industry while stating a different industry"),

    ("percentage injection",
     {"support": "tech_stack",
      "text": "Teams on Snowflake cut triage time 60% with this."},
     True, "an unsupported statistic bolted to a supported fact"),

    ("year injection",
     {"support": "company_name",
      "text": "Northwind Health has grown fast since 2019."},
     True, "a date that is nowhere in the record"),

    # The interesting ones. These carry no number and cite a real field whose
    # value genuinely appears, so the current gate has no basis to reject
    # them, yet each asserts something the record does not support.
    ("qualitative inference on a real field",
     {"support": "tech_stack",
      "text": "Since you already run Snowflake, your data team is clearly mature."},
     True, "unsupported JUDGEMENT attached to a supported fact"),

    ("magnitude in words, no digits",
     {"support": "employee_count",
      "text": "With several hundred people, triage is a real cost for you."},
     True, "a numeric claim spelled out to dodge a digit check"),

    ("invented intent",
     {"support": "company_name",
      "text": "Northwind Health is evaluating vendors this quarter."},
     True, "a fabricated buying signal wrapped around a real name"),

    ("compound, half true",
     {"support": "industry",
      "text": "Healthcare software teams like yours are mostly still doing this by hand."},
     True, "a real field plus an unverifiable market claim"),

    ("honest and supported",
     {"support": "company_name",
      "text": "I saw Northwind Health come through our inbound form."},
     False, "a clean claim must pass, or the gate is useless"),

    ("honest, supported, whitelisted small number",
     {"support": "industry",
      "text": "Healthcare software teams usually give this 2 or 3 weeks."},
     False, "generic small numbers are whitelisted by design"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("=" * 94)
    print("  RED TEAM: ATTACKS ON THE VALIDATION GATE".center(94))
    print("=" * 94)
    print(f"  record under test: {RECORD['company_name']}, "
          f"{RECORD['employee_count']} staff, {RECORD['industry']}, "
          f"{', '.join(RECORD['tech_stack'])}\n")

    caught = missed = false_positive = correct_pass = 0
    holes = []

    for name, claim, should_block, probing in ATTACKS:
        result = validate([claim], RECORD)
        blocked = not result["passed"]

        if should_block and blocked:
            verdict, caught = "CAUGHT", caught + 1
        elif should_block and not blocked:
            verdict, missed = "MISSED", missed + 1
            holes.append((name, claim, probing))
        elif not should_block and blocked:
            verdict, false_positive = "FALSE POSITIVE", false_positive + 1
            holes.append((name, claim, probing))
        else:
            verdict, correct_pass = "passed, correctly", correct_pass + 1

        print(f"  {verdict:<18} {name}")
        if args.verbose or verdict in ("MISSED", "FALSE POSITIVE"):
            print(f'                     "{claim["text"]}"')
            print(f"                     probing: {probing}")
            if result["rejected"]:
                print(f"                     gate said: {result['rejected'][0]['why']}")
            print()

    total_attacks = sum(1 for _, _, s, _ in ATTACKS if s)
    print("-" * 94)
    print(f"  attacks that should be blocked : {total_attacks}")
    print(f"  caught                         : {caught}/{total_attacks} "
          f"({100 * caught / total_attacks:.0f}%)")
    print(f"  MISSED                         : {missed}/{total_attacks}")
    print(f"  legitimate claims passed       : {correct_pass}")
    print(f"  false positives                : {false_positive}")

    if missed:
        print(f"\n  THE HOLE, STATED RATHER THAN HIDDEN")
        print("  " + "-" * 90)
        print("  Every miss below cites a real field whose value genuinely appears in")
        print("  the sentence, and carries no digit the record lacks. The gate has no")
        print("  basis to reject them, because it checks two things: is the field real")
        print("  and present, and is every number in the record.")
        print()
        print("  What it does NOT check is whether the SENTENCE ASSERTS MORE THAN THE")
        print("  FIELD SUPPORTS. 'You run Snowflake' is supported. 'Your data team is")
        print("  clearly mature' is an inference bolted onto it, and a field-level")
        print("  citation check cannot see the difference.")
        print()
        print("  This is a real limit of citation-style grounding generally, not a bug")
        print("  in this implementation. Closing it needs an entailment check: does the")
        print("  cited field ENTAIL the sentence, rather than merely appear in it. That")
        print("  is a model-graded eval, not a regex, and it is the honest next step.")
        for name, claim, probing in holes:
            print(f'\n    {name}\n      "{claim["text"]}"')
    print("=" * 94)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
