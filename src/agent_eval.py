"""Does letting the model choose beat a fixed policy?

An agent is only worth its round trip if it does something the hardcoded
version cannot. So this measures both on the same domains:

    FIXED   query all four sources, every time, in a fixed order
    AGENT   the model picks which sources to call and when to stop

on the tradeoff that decides it:

    coverage  fields resolved out of the seven expected
    cost      source invocations spent getting there
    waste     calls that returned nothing new

**The agent is allowed to lose.** If it matches the fixed policy's coverage
at the same cost, it has bought nothing and the honest conclusion is to keep
the hardcoded waterfall. Publishing that would be a better result than
quietly not measuring it.

The fixed baseline needs no credentials and always runs. The agent half runs
when an API key is present, and is reported as pending when it is not,
rather than being silently skipped.

    python src/agent_eval.py
    python src/agent_eval.py --domains stripe.com,notion.so
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from providers import (                              # noqa: E402
    EXPECTED_FIELDS, from_dns, from_homepage, from_tech_signals, from_wikidata,
)

ROOT = Path(__file__).resolve().parent.parent

# Chosen to span the range the policy has to handle: a large well documented
# company, a large one that is oddly absent from structured sources, a
# private mid-size company, and a domain with nothing behind it at all.
DEFAULT_DOMAINS = [
    "stripe.com",
    "snowflake.com",
    "vercel.com",
    "notion.so",
    "a-domain-that-does-not-exist-xyz.com",
]


def fixed_policy(domain: str) -> dict:
    """The hardcoded waterfall: all four sources, always, in order."""
    facts: dict = {}
    calls = []
    started = time.time()

    def absorb(name: str, found: dict) -> None:
        useful = {
            k: v for k, v in found.items()
            if not k.startswith("_") and k in EXPECTED_FIELDS
            and v not in (None, "", [])
        }
        new = {k: v for k, v in useful.items() if facts.get(k) in (None, "", [])}
        facts.update(new)
        calls.append({"tool": name, "fields_new": sorted(new), "wasted": not new})

    home = from_homepage(domain)
    html = home.pop("_html", None)
    absorb("fetch_homepage", home)
    absorb("detect_tech_signals", from_tech_signals(html))
    absorb("query_wikidata", from_wikidata(domain, facts.get("company_name")))
    absorb("lookup_mx", from_dns(domain))

    resolved = [f for f in EXPECTED_FIELDS if facts.get(f) not in (None, "", [])]
    return {
        "domain": domain, "policy": "fixed", "facts": facts,
        "resolved": resolved,
        "coverage": round(len(resolved) / len(EXPECTED_FIELDS), 2),
        "tool_calls": len(calls),
        "wasted_calls": sum(1 for c in calls if c["wasted"]),
        "elapsed_ms": int((time.time() - started) * 1000),
        "trace": calls,
    }


def agent_available() -> tuple[bool, str]:
    try:
        import anthropic
    except ImportError:
        return False, "the anthropic SDK is not installed (pip install anthropic)"
    try:
        c = anthropic.Anthropic()
    except Exception as err:                         # noqa: BLE001
        return False, f"client could not be constructed: {err}"
    if not (c.api_key or getattr(c, "auth_token", None)):
        return False, "no credentials resolved (set ANTHROPIC_API_KEY)"
    return True, "ready"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", help="comma separated, overrides the default set")
    ap.add_argument("--save", default="out/agent_eval.json")
    args = ap.parse_args()

    domains = ([d.strip() for d in args.domains.split(",")]
               if args.domains else DEFAULT_DOMAINS)

    print("=" * 92)
    print("  FIXED POLICY vs AGENT: coverage against cost".center(92))
    print("=" * 92)

    ok, why = agent_available()
    rows = []

    print(f"\n  {'domain':<40}{'policy':<8}{'cover':>7}{'calls':>7}"
          f"{'waste':>7}{'ms':>8}")
    print("  " + "-" * 88)

    for domain in domains:
        f = fixed_policy(domain)
        rows.append(f)
        print(f"  {domain:<40}{'fixed':<8}{f['coverage']:>7.2f}"
              f"{f['tool_calls']:>7}{f['wasted_calls']:>7}{f['elapsed_ms']:>8}")

        if ok:
            from enrich_agent import run as agent_run
            try:
                a = agent_run(domain, verbose=False)
                rows.append(a)
                print(f"  {'':<40}{'agent':<8}{a['coverage']:>7.2f}"
                      f"{a['tool_calls']:>7}{a['wasted_calls']:>7}{a['elapsed_ms']:>8}")
                print(f"  {'':<48}{a['reasoning'][:70]}")
            except Exception as err:                 # noqa: BLE001
                print(f"  {'':<40}{'agent':<8}  failed: {str(err)[:52]}")

    fixed = [r for r in rows if r["policy"] == "fixed"]
    agent = [r for r in rows if r["policy"] == "agent"]

    print("\n" + "-" * 92)
    fc = sum(r["coverage"] for r in fixed) / len(fixed)
    fk = sum(r["tool_calls"] for r in fixed)
    fw = sum(r["wasted_calls"] for r in fixed)
    print(f"  FIXED   mean coverage {fc:.2f}   {fk} calls total   "
          f"{fw} wasted ({100 * fw / fk:.0f}%)")

    if agent:
        ac = sum(r["coverage"] for r in agent) / len(agent)
        ak = sum(r["tool_calls"] for r in agent)
        aw = sum(r["wasted_calls"] for r in agent)
        print(f"  AGENT   mean coverage {ac:.2f}   {ak} calls total   "
              f"{aw} wasted ({100 * aw / ak:.0f}%)" if ak else "  AGENT   no calls")

        print()
        if ac >= fc and ak < fk:
            print("  VERDICT: the agent matched or beat coverage while spending fewer")
            print("  calls. Letting the model choose earned its round trip here.")
        elif ac > fc:
            print(f"  VERDICT: the agent resolved more ({ac:.2f} vs {fc:.2f}) but spent")
            print(f"  {ak} calls against {fk}. Worth it only if coverage is the")
            print("  binding constraint rather than cost.")
        elif ak < fk and ac < fc:
            print(f"  VERDICT: the agent was cheaper ({ak} vs {fk} calls) and resolved")
            print(f"  less ({ac:.2f} vs {fc:.2f}). That is a real tradeoff, not a win.")
        else:
            print("  VERDICT: the agent did not beat the fixed policy on either axis.")
            print("  The honest conclusion is to keep the hardcoded waterfall and drop")
            print("  the agent. A round trip that buys nothing is a cost, not a feature.")
    else:
        print(f"  AGENT   not run: {why}")
        print()
        print("  The fixed baseline above is complete and reproducible today. The")
        print("  agent half needs a key. Reported as pending rather than skipped,")
        print("  because an unrun comparison is not the same as a favourable one.")

    print("=" * 92)

    out = ROOT / args.save
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print(f"\n  detail written to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
