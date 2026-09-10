"""A real agent, and a measurement of whether it earns its place.

The fixed enrichment waterfall in `providers.py` queries four sources in a
hardcoded order, every time, for every lead. That is a policy, and it is a
dumb one: some companies resolve from their homepage alone, some need all
four, and a dead domain needs none of them.

This gives the model those four sources **as tools** and lets it decide which
to call, in what order, and when to stop. That is genuine model-driven
control, not a pipeline with a model bolted on: there is a loop, the model
picks the next action, and nothing in the code prescribes the order.

**The point is not that an agent is fancier. The point is that it is a
testable claim.** An agent that calls all four sources anyway has bought
nothing and cost a round trip per lead. So `agent_eval.py` measures it
against the fixed waterfall on the tradeoff that actually matters:

    coverage   fields resolved out of the expected shape
    cost       tool calls spent getting there
    judgement  does it correctly give up on a domain with nothing behind it

Most projects that say "agentic" cannot produce that comparison. This one is
built to lose it if it deserves to.

    python src/enrich_agent.py stripe.com
    python src/enrich_agent.py --schemas       print tool schemas, no API call
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from providers import (                              # noqa: E402
    EXPECTED_FIELDS, from_dns, from_homepage, from_tech_signals, from_wikidata,
)

MODEL = "claude-opus-5"

# Every tool call is recorded here so cost can be measured rather than
# guessed. Reset per lead by run().
TRACE: list[dict] = []
FACTS: dict = {}


def _record(tool: str, args: dict, found: dict, ms: int) -> str:
    """Merge what a source returned, log the call, and report back plainly."""
    useful = {
        k: v for k, v in found.items()
        if not k.startswith("_") and k in EXPECTED_FIELDS and v not in (None, "", [])
    }
    new = {k: v for k, v in useful.items() if FACTS.get(k) in (None, "", [])}
    FACTS.update(new)

    TRACE.append({
        "tool": tool, "args": args, "ms": ms,
        "fields_returned": sorted(useful), "fields_new": sorted(new),
        "wasted": not new,
    })

    missing = [f for f in EXPECTED_FIELDS if FACTS.get(f) in (None, "", [])]
    if not useful:
        return (f"{tool}: nothing found. Still missing: "
                f"{', '.join(missing) if missing else 'nothing'}")
    return (
        f"{tool} returned {json.dumps(useful, default=str)}. "
        + (f"New fields: {', '.join(new)}. " if new else "None of it was new. ")
        + (f"Still missing: {', '.join(missing)}." if missing
           else "Every expected field is now resolved.")
    )


# --------------------------------------------------------------- the tools

def _mk_tools():
    """Built lazily so importing this module never requires the SDK."""
    from anthropic import beta_tool

    @beta_tool
    def fetch_homepage(domain: str) -> str:
        """Fetch the company's own website and read its structured metadata.

        Best for: company_name, and sometimes hq_country or employee_count if
        the site publishes an Organization schema block. Almost always
        returns a name. Costs one page load, so it is the cheapest first move
        for an unknown domain, and it is the only source that proves the
        domain resolves at all.

        Args:
            domain: Bare domain with no scheme, for example "stripe.com".
        """
        t = time.time()
        found = from_homepage(domain)
        found.pop("_html", None)
        return _record("fetch_homepage", {"domain": domain}, found,
                       int((time.time() - t) * 1000))

    @beta_tool
    def detect_tech_signals(domain: str) -> str:
        """Identify which vendor scripts the company's homepage loads.

        Best for: tech_stack only. Returns nothing else, ever. Skip it if
        tech_stack is already resolved or if the homepage did not load, since
        it reads the same page.

        Args:
            domain: Bare domain with no scheme, for example "stripe.com".
        """
        t = time.time()
        html = from_homepage(domain).get("_html")
        found = from_tech_signals(html)
        return _record("detect_tech_signals", {"domain": domain}, found,
                       int((time.time() - t) * 1000))

    @beta_tool
    def query_wikidata(query: str) -> str:
        """Look the company up in Wikidata's structured database.

        Best for: employee_count, industry and hq_country. It is the ONLY
        source here that reliably returns headcount, so it is usually
        necessary when employee_count is still missing. It is also the
        slowest, needing several requests, and it only covers companies
        notable enough to have an entry, so it often returns nothing for
        small or private companies.

        Args:
            query: The company name if you already know it, otherwise the
                domain. A real name matches far better than a domain.
        """
        t = time.time()
        found = from_wikidata(query if "." in query else query, query)
        return _record("query_wikidata", {"query": query}, found,
                       int((time.time() - t) * 1000))

    @beta_tool
    def lookup_mx(domain: str) -> str:
        """Read the domain's MX records to identify its mail platform.

        Best for: adding one entry to tech_stack, for example Google
        Workspace or Microsoft 365. Fast and almost always answers for a live
        domain, but it never returns headcount, industry or country, so it
        cannot help with those.

        Args:
            domain: Bare domain with no scheme, for example "stripe.com".
        """
        t = time.time()
        found = from_dns(domain)
        return _record("lookup_mx", {"domain": domain}, found,
                       int((time.time() - t) * 1000))

    return [fetch_homepage, detect_tech_signals, query_wikidata, lookup_mx]


SYSTEM = """You enrich a company record from its email domain, by choosing which
data sources to query.

Your job is NOT to call every tool. It is to resolve as many of these fields
as you can while spending as few calls as possible:

    company_name, employee_count, industry, hq_country, tech_stack,
    funding_stage, annual_revenue

Rules:
1. Read each tool's description before choosing. They have genuinely
   different strengths and some cannot return certain fields at all.
2. Never call a tool that can only return fields you already have.
3. If the homepage returns nothing at all, the domain is probably dead. Stop
   rather than working through the rest.
4. Some fields may be unavailable from any of these sources. Stopping with
   them unresolved is the correct outcome, not a failure. Do not keep calling
   tools hoping something turns up.
5. When you stop, state in one or two sentences which fields you resolved,
   which you could not, and why you stopped. Be specific about whether a
   field is genuinely unavailable or merely not covered by these sources.

An unresolved field reported honestly is worth more than a guess."""


def run(domain: str, verbose: bool = True) -> dict:
    """Let the model enrich `domain`, and return what it did as well as what it found."""
    import anthropic

    TRACE.clear()
    FACTS.clear()

    client = anthropic.Anthropic()
    extra_headers = {}
    if ws := os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        extra_headers["anthropic-workspace-id"] = ws

    started = time.time()
    runner = client.beta.messages.tool_runner(
        extra_headers=extra_headers or None,
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        tools=_mk_tools(),
        messages=[{"role": "user", "content":
                   f"Enrich the company behind the domain {domain}."}],
    )

    final_text, turns = "", 0
    for message in runner:
        turns += 1
        for block in message.content:
            if block.type == "text" and block.text.strip():
                final_text = block.text.strip()
            if verbose and block.type == "tool_use":
                print(f"  call {len(TRACE) + 1}: {block.name}({json.dumps(block.input)})")

    elapsed = int((time.time() - started) * 1000)
    resolved = [f for f in EXPECTED_FIELDS if FACTS.get(f) not in (None, "", [])]

    return {
        "domain": domain,
        "policy": "agent",
        "facts": dict(FACTS),
        "resolved": resolved,
        "unresolved": [f for f in EXPECTED_FIELDS if f not in resolved],
        "coverage": round(len(resolved) / len(EXPECTED_FIELDS), 2),
        "tool_calls": len(TRACE),
        "wasted_calls": sum(1 for t in TRACE if t["wasted"]),
        "turns": turns,
        "elapsed_ms": elapsed,
        "trace": list(TRACE),
        "reasoning": final_text,
    }


def print_schemas() -> None:
    """Show the tool schemas the SDK generates. No API call, no key needed."""
    tools = _mk_tools()
    print("=" * 78)
    print("  TOOL SCHEMAS, generated from the function signatures".center(78))
    print("=" * 78)
    for t in tools:
        spec = t.to_dict() if hasattr(t, "to_dict") else {
            "name": getattr(t, "name", t.__name__),
            "description": (getattr(t, "description", None) or t.__doc__ or "").strip(),
            "input_schema": getattr(t, "input_schema", None),
        }
        print(f"\n  {spec['name']}")
        first = (spec.get("description") or "").split("\n")[0]
        print(f"    {first}")
        props = (spec.get("input_schema") or {}).get("properties", {})
        for arg, meta in props.items():
            print(f"    arg: {arg} ({meta.get('type')})")
    print("\n" + "=" * 78)


if __name__ == "__main__":
    if "--schemas" in sys.argv:
        print_schemas()
        raise SystemExit(0)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = args[0] if args else "stripe.com"

    try:
        out = run(target)
    except Exception as err:                         # noqa: BLE001
        if "anthropic-workspace-id" in str(err):
            raise SystemExit(
                "\nThis API key is not scoped to a workspace. Either create a "
                "workspace-scoped key, or set ANTHROPIC_WORKSPACE_ID.\n"
            )
        if "anthropic" in str(type(err)).lower() or "api" in str(err).lower():
            raise SystemExit(f"\nThe agent needs credentials: {err}\n")
        raise

    print(f"\n  resolved   : {', '.join(out['resolved']) or 'nothing'}")
    print(f"  unresolved : {', '.join(out['unresolved']) or 'nothing'}")
    print(f"  coverage   : {out['coverage']}  "
          f"({len(out['resolved'])}/{len(EXPECTED_FIELDS)} fields)")
    print(f"  tool calls : {out['tool_calls']} "
          f"({out['wasted_calls']} returned nothing new)")
    print(f"  elapsed    : {out['elapsed_ms']} ms")
    print(f"\n  the agent's own account of why it stopped:\n    {out['reasoning']}")
