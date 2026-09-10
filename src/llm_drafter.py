"""The real model call.

Until now the validation gate was only ever tested against a simulator I
wrote to misbehave on purpose. That proves the gate's logic but not that it
catches a real model, which is the claim that actually matters. This module
closes that gap.

Design decision worth defending: **the model is asked to return claims, each
naming the enrichment field it rests on**, via a structured output schema.
Not prose. That means the gate in `draft.py` checks the model's output with
exactly the same code it already used on the simulator, and the model has to
commit to a source for every sentence before anything is sent.

The gate is deliberately NOT relaxed for the real model. If Claude writes a
sentence it cannot source, the sentence is dropped and the draft is withheld,
same as for the simulator.

Requires the official SDK and credentials, which the rest of the project does
not. Everything else here runs on the standard library with no key, so this
stays an optional extra rather than a dependency:

    pip install anthropic
    # then either an API key in the environment, or `ant auth login`

    python src/llm_drafter.py                   one lead, live
    python src/llm_drafter.py --check           credentials and SDK only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODEL = "claude-opus-5"

# The model must return this shape. Each claim names the field it rests on,
# which is what makes the output checkable rather than merely plausible.
CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "description": (
                "Sentences for the opening of an outbound email. Between 2 and 4."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One sentence, plain and specific.",
                    },
                    "support": {
                        "type": "string",
                        "description": (
                            "The single enrichment field this sentence rests on. "
                            "Must be one of the field names given in the record. "
                            "Every sentence must cite exactly one."
                        ),
                    },
                },
                "required": ["text", "support"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

SYSTEM = """You draft the opening lines of a business outbound email.

Hard rules, enforced in code after you reply:
1. Every sentence must rest on ONE field from the enrichment record, and you
   must name that field in `support`.
2. Never state a fact that is not present in the record. No funding amounts,
   no customer names, no growth figures, no headcount you were not given.
3. If a field is null or missing, do not refer to it at all.
4. Any number you write must appear in the record.
5. No flattery, no "I hope this finds you well", no em dashes.

A sentence you cannot source will be deleted and the whole draft withheld, so
a shorter grounded draft is strictly better than a longer speculative one."""


class LLMUnavailable(RuntimeError):
    """The SDK or credentials are missing. Actionable, not mysterious."""


def _client():
    try:
        import anthropic
    except ImportError as err:
        raise LLMUnavailable(
            "the official SDK is not installed. This is the only part of the "
            "project that needs it:\n    pip install anthropic"
        ) from err

    try:
        client = anthropic.Anthropic()
    except Exception as err:                     # noqa: BLE001
        raise LLMUnavailable(f"could not construct a client: {err}") from err

    # Constructing a client does NOT validate credentials, and does not raise
    # when none are present, so an availability check that only constructs
    # reports success and then fails at request time. Check the resolved
    # credential directly.
    if not (client.api_key or getattr(client, "auth_token", None)):
        raise LLMUnavailable(
            "the SDK is installed but no credentials resolved. Either:\n"
            "    ant auth login          (stores a profile the SDK reads)\n"
            "    set ANTHROPIC_API_KEY   (environment variable)"
        )
    return client


def claude_claims(lead: dict, record: dict) -> list[dict]:
    """Ask Claude for grounded claims. Returns the same shape as the simulator."""
    client = _client()

    usable = {
        k: v for k, v in record.items()
        if not k.startswith("_")
        and k not in ("field_sources", "unresolved_fields", "provider_hit")
        and v not in (None, "", [])
    }

    prompt = (
        "Enrichment record for this company. These are the ONLY facts available.\n\n"
        + json.dumps(usable, indent=2)
        + "\n\nThe person who enquired: "
        + json.dumps({
            "contact_name": lead.get("contact_name"),
            "message": lead.get("message"),
        }, indent=2)
        + "\n\nDraft between 2 and 4 opening sentences, each citing its field."
    )

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": CLAIM_SCHEMA}},
        # Opt into server-side fallbacks by default, per Anthropic's current
        # guidance: on a policy decline the API retries on a fallback model
        # inside the same call, routed by refusal category.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    # A refusal is HTTP 200 with stop_reason set, so it must be checked before
    # reading content or you parse an empty body and blame the schema.
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise LLMUnavailable(
            "the request was declined by safety classifiers"
            + (f" (category {detail.category})" if detail else "")
            + ". The whole fallback chain refused."
        )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise LLMUnavailable(
            "no text block in the response. Blocks present: "
            + ", ".join(b.type for b in response.content)
        )

    claims = json.loads(text).get("claims", [])
    for c in claims:
        c["drafted_by"] = MODEL
    return claims


def check() -> int:
    try:
        client = _client()
    except LLMUnavailable as err:
        print("NOT AVAILABLE\n")
        print(err)
        return 1
    print(f"SDK        : installed")
    print(f"credentials: resolved")
    print(f"model      : {MODEL}")
    key = os.environ.get("ANTHROPIC_API_KEY")
    print(f"source     : {'ANTHROPIC_API_KEY' if key else 'profile or other resolver'}")
    del client
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(check())

    from providers import enrich

    lead = {
        "contact_name": "Jane Doe",
        "email": "ops@northwind-health.com",
        "message": "Looking to consolidate our patient analytics reporting.",
    }
    record = enrich(lead["email"], "offline")

    try:
        claims = claude_claims(lead, record)
    except LLMUnavailable as err:
        print("LLM unavailable, so the simulator remains the default drafter.\n")
        print(err)
        raise SystemExit(1)

    print(f"model returned {len(claims)} claims\n")
    for c in claims:
        print(f"  support: {c['support']}")
        print(f"    {c['text']}\n")

    from draft import validate
    gate = validate(claims, record)
    print(f"gate: {len(gate['kept'])} kept, {gate['claims_rejected']} rejected, "
          f"passed={gate['passed']}")
    for bad in gate["rejected"]:
        print(f"  REJECTED: {bad['text']}\n            -> {bad['why']}")
