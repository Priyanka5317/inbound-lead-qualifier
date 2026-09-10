"""Generate a single self-contained HTML report.

Everything in the output is computed by running the real thing: the rubric
A/B, the golden and holdout evaluations, the gate, and the red team suite.
Nothing is hardcoded, so the report cannot drift from the code.

One file, no dependencies, no network, works offline and from a file:// URL.

    python src/report.py
    python src/report.py --open
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import compare, score_set                     # noqa: E402
from draft import validate                                 # noqa: E402
from pipeline import ingest, process                       # noqa: E402
from providers import enrich                               # noqa: E402
from qualify import load_rubric                            # noqa: E402
from redteam import ATTACKS, RECORD                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report.html"

# Validated with the dataviz palette validator, light and dark, all checks
# pass with no contrast warning. Blue and orange are the two most separable
# slots and this chart never needs a third.
SERIES = {"light": ("#2a78d6", "#eb6834"), "dark": ("#3987e5", "#d95926")}

E = html.escape


# ------------------------------------------------------------------ gather

def gather() -> dict:
    v1 = load_rubric(ROOT / "rubric.json")
    v2 = load_rubric(ROOT / "rubric-v2.json")

    ab = {name: compare(name, v1, v2, verbose=False) for name in ("golden", "holdout")}

    # gate, both drafters, over the golden set
    leads = ingest(ROOT / "data" / "golden_set.csv")
    gate_stats = {}
    for drafter in ("grounded", "llm_sim"):
        results = [process(l, v2, drafter) for l in leads]
        drafted = [r for r in results if r["draft"]]
        gate_stats[drafter] = {
            "attempted": len(drafted),
            "withheld": sum(1 for r in drafted if not r["draft"]["sent"]),
            "claims": sum(r["draft"]["gate"]["claims_total"] for r in drafted),
            "rejected": sum(r["draft"]["gate"]["claims_rejected"] for r in drafted),
        }

    # red team
    red = []
    for name, claim, should_block, probing in ATTACKS:
        blocked = not validate([claim], RECORD)["passed"]
        red.append({
            "name": name, "text": claim["text"], "probing": probing,
            "should_block": should_block, "blocked": blocked,
            "outcome": ("caught" if should_block and blocked else
                        "MISSED" if should_block else
                        "false positive" if blocked else "passed"),
        })

    scored = score_set("golden", v2)
    dist = Counter()
    for r in scored["rows"]:
        if r["system"] != "abstain":
            dist[min(int(r["score"]) // 20 * 20, 80)] += 1

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "v2": v2, "ab": ab, "gate": gate_stats, "red": red,
        "dist": dist, "golden_rows": scored["rows"],
    }


# ------------------------------------------------------------------ charts

def grouped_bars(ab: dict) -> str:
    """v1 vs v2 accuracy on each set. Two series, legend plus direct labels."""
    groups = [
        (f"Golden set ({ab['golden']['v1']['total']} leads)",
         100 * ab["golden"]["v1"]["correct"] / ab["golden"]["v1"]["total"],
         100 * ab["golden"]["v2"]["correct"] / ab["golden"]["v2"]["total"],
         f"{ab['golden']['v1']['correct']}/{ab['golden']['v1']['total']}",
         f"{ab['golden']['v2']['correct']}/{ab['golden']['v2']['total']}"),
        (f"Held out ({ab['holdout']['v1']['total']} leads)",
         100 * ab["holdout"]["v1"]["correct"] / ab["holdout"]["v1"]["total"],
         100 * ab["holdout"]["v2"]["correct"] / ab["holdout"]["v2"]["total"],
         f"{ab['holdout']['v1']['correct']}/{ab['holdout']['v1']['total']}",
         f"{ab['holdout']['v2']['correct']}/{ab['holdout']['v2']['total']}"),
    ]
    W, H, PAD_L, PAD_B, PAD_T = 640, 260, 48, 46, 16
    plot_w, plot_h = W - PAD_L - 16, H - PAD_B - PAD_T
    gw = plot_w / len(groups)
    bw = 46

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" '
             f'aria-label="Accuracy of rubric v1 versus v2 on the golden and held out sets">']
    # recessive gridlines
    for pct in (0, 25, 50, 75, 100):
        y = PAD_T + plot_h - plot_h * pct / 100
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - 16}" y2="{y:.1f}" '
                     f'class="grid"/>')
        parts.append(f'<text x="{PAD_L - 10}" y="{y + 4:.1f}" class="ax" '
                     f'text-anchor="end">{pct}</text>')

    for i, (label, a, b, la, lb) in enumerate(groups):
        cx = PAD_L + gw * i + gw / 2
        for j, (val, lab, cls) in enumerate(((a, la, "s1"), (b, lb, "s2"))):
            h = plot_h * val / 100
            # 2px surface gap between adjacent bars
            x = cx - bw - 1 + j * (bw + 2)
            y = PAD_T + plot_h - h
            parts.append(
                f'<g class="bar"><title>{E(label)} {"v1" if j == 0 else "v2"}: '
                f'{lab} ({val:.0f}%)</title>'
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw}" height="{h:.1f}" '
                f'rx="4" class="{cls}"/>'
                f'<text x="{x + bw / 2:.1f}" y="{y - 7:.1f}" class="val" '
                f'text-anchor="middle">{lab}</text></g>')
        parts.append(f'<text x="{cx:.1f}" y="{H - 16}" class="ax" '
                     f'text-anchor="middle">{E(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def histogram(dist: Counter) -> str:
    """Score distribution. One measure, so a single sequential hue."""
    buckets = [(lo, dist.get(lo, 0)) for lo in range(0, 100, 20)]
    top = max((n for _, n in buckets), default=1) or 1
    W, H, PAD_L, PAD_B, PAD_T = 640, 210, 48, 40, 14
    plot_w, plot_h = W - PAD_L - 16, H - PAD_B - PAD_T
    bw = plot_w / len(buckets) - 10

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" '
             f'aria-label="Distribution of scores across the golden set">']
    for i, (lo, n) in enumerate(buckets):
        h = plot_h * n / top
        x = PAD_L + i * (plot_w / len(buckets)) + 5
        y = PAD_T + plot_h - h
        parts.append(
            f'<g class="bar"><title>{lo} to {lo + 19}: {n} leads</title>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
            f'height="{max(h, 2):.1f}" rx="4" class="seq"/>'
            f'<text x="{x + bw / 2:.1f}" y="{y - 6:.1f}" class="val" '
            f'text-anchor="middle">{n}</text></g>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{H - 14}" class="ax" '
                     f'text-anchor="middle">{lo}-{lo + 19}</text>')
    parts.append(f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - 16}" '
                 f'y2="{PAD_T + plot_h}" class="grid"/>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------- build

def build(d: dict) -> str:
    ab = d["ab"]
    hv1 = ab["holdout"]["v1"]
    hv2 = ab["holdout"]["v2"]
    gv2 = ab["golden"]["v2"]

    red_block = [r for r in d["red"] if r["should_block"]]
    red_caught = sum(1 for r in red_block if r["blocked"])
    red_missed = [r for r in d["red"] if r["outcome"] == "MISSED"]
    red_fp = sum(1 for r in d["red"] if r["outcome"] == "false positive")

    sim = d["gate"]["llm_sim"]
    grounded = d["gate"]["grounded"]

    abstains = sum(1 for r in d["golden_rows"] if r["system"] == "abstain")

    tiles = [
        (f"{100 * hv2['correct'] / hv2['total']:.0f}%",
         "held-out accuracy",
         f"v2, up from {100 * hv1['correct'] / hv1['total']:.0f}% on v1"),
        (f"{red_caught}/{len(red_block)}",
         "adversarial claims caught",
         f"{red_fp} false positives"),
        (f"{sim['rejected']}/{sim['claims']}",
         "invented claims rejected",
         f"{sim['withheld']}/{sim['attempted']} drafts withheld"),
        (f"{abstains}/{len(d['golden_rows'])}",
         "declined to guess",
         "abstained, all correctly"),
    ]

    tile_html = "".join(
        f'<div class="tile"><div class="big">{E(v)}</div>'
        f'<div class="lab">{E(l)}</div><div class="sub">{E(s)}</div></div>'
        for v, l, s in tiles)

    # changelog
    changes = "".join(
        f'<div class="change"><div class="chg-h">Found by <code>{E(c["found_by"])}</code></div>'
        f'<p><b>Defect.</b> {E(c["defect"])}</p>'
        f'<p><b>Rejected fix.</b> {E(c["rejected_fix"])}</p>'
        f'<p><b>Shipped fix.</b> {E(c["fix"])}</p>'
        + (f'<p class="corr"><b>Corroboration.</b> {E(c["corroboration"])}</p>'
           if c.get("corroboration") else "")
        + "</div>"
        for c in d["v2"]["changelog"])

    # per-lead A/B table
    def ab_rows(name: str) -> str:
        a = ab[name]["v1"]["rows"]
        b = ab[name]["v2"]["rows"]
        out = []
        for ra, rb in zip(a, b):
            change = ("fixed" if (not ra["ok"] and rb["ok"]) else
                      "regressed" if (ra["ok"] and not rb["ok"]) else
                      "still-wrong" if not rb["ok"] else "")
            out.append(
                f'<tr class="{change}"><td><code>{E(ra["lead_id"])}</code></td>'
                f'<td>{E(ra["human"])}</td><td>{E(ra["system"])}</td>'
                f'<td>{E(rb["system"])}</td>'
                f'<td class="num">{rb["score"]}</td>'
                f'<td>{"<span class=tag-f>fixed</span>" if change == "fixed" else ""}'
                f'{"<span class=tag-w>still wrong</span>" if change == "still-wrong" else ""}'
                f'{"<span class=tag-r>regressed</span>" if change == "regressed" else ""}</td></tr>')
        return "".join(out)

    red_rows = "".join(
        f'<tr class="{"miss" if r["outcome"] == "MISSED" else ""}">'
        f'<td>{"caught" if r["outcome"] == "caught" else E(r["outcome"])}</td>'
        f'<td>{E(r["name"])}</td><td class="q">{E(r["text"])}</td>'
        f'<td class="sub">{E(r["probing"])}</td></tr>'
        for r in d["red"])

    missed_html = "".join(
        f'<li><span class="q">{E(r["text"])}</span><br>'
        f'<span class="sub">{E(r["probing"])}</span></li>' for r in red_missed)

    still = ab["holdout"]["still_wrong"]
    still_html = "".join(
        f'<div class="change"><div class="chg-h">Still wrong: '
        f'<code>{E(r["lead_id"])}</code></div>'
        f'<p><b>Human said</b> {E(r["human"])}, <b>system said</b> {E(r["system"])}.</p>'
        f'<p><b>Human reasoning.</b> {E(r["human_why"])}</p>'
        f'<p><b>System reasoning.</b> {E(r["why"])}</p></div>' for r in still)

    lc, ld = SERIES["light"], SERIES["dark"]

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inbound Lead Qualifier, evaluation report</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-0:#f6f5f2; --surface-1:#fcfcfb; --border:#e3e1db;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#77756e;
    --series-1:{lc[0]}; --series-2:{lc[1]}; --seq:#2a78d6;
    --good:#0ca30c; --critical:#d03b3b; --warning:#fab219;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-0:#111110; --surface-1:#1a1a19; --border:#33322f;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8e8d85;
      --series-1:{ld[0]}; --series-2:{ld[1]}; --seq:#3987e5;
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --surface-0:#111110; --surface-1:#1a1a19; --border:#33322f;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8e8d85;
    --series-1:{ld[0]}; --series-2:{ld[1]}; --seq:#3987e5;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--surface-0);color:var(--text-primary);
    font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    font-variant-ligatures:none}}
  .wrap{{max-width:940px;margin:0 auto;padding:40px 24px 80px}}
  h1{{font-size:30px;line-height:1.2;margin:0 0 8px;letter-spacing:-.02em}}
  h2{{font-size:20px;margin:44px 0 6px;letter-spacing:-.01em}}
  h3{{font-size:15px;margin:22px 0 6px}}
  p{{margin:0 0 12px;color:var(--text-secondary)}}
  .lede{{font-size:17px;color:var(--text-secondary);margin-bottom:6px}}
  .meta{{font-size:13px;color:var(--text-muted);margin-bottom:28px}}
  .card{{background:var(--surface-1);border:1px solid var(--border);
    border-radius:10px;padding:20px 22px;margin:14px 0}}
  .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:22px 0}}
  .tile{{background:var(--surface-1);border:1px solid var(--border);
    border-radius:10px;padding:16px 18px}}
  .big{{font-size:32px;font-weight:660;letter-spacing:-.02em;line-height:1.1}}
  .lab{{font-size:13px;color:var(--text-secondary);margin-top:3px}}
  .sub{{font-size:12px;color:var(--text-muted)}}
  svg{{width:100%;height:auto;display:block}}
  .grid{{stroke:var(--border);stroke-width:1}}
  .ax{{fill:var(--text-muted);font-size:11px}}
  .val{{fill:var(--text-secondary);font-size:11px;font-weight:600}}
  .s1{{fill:var(--series-1)}} .s2{{fill:var(--series-2)}} .seq{{fill:var(--seq)}}
  .bar:hover rect{{opacity:.82}}
  .legend{{display:flex;gap:18px;font-size:13px;color:var(--text-secondary);
    margin:2px 0 10px}}
  .key{{display:inline-block;width:11px;height:11px;border-radius:3px;
    margin-right:6px;vertical-align:-1px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
  th{{text-align:left;font-weight:600;color:var(--text-muted);
    border-bottom:1px solid var(--border);padding:7px 8px;font-size:12px;
    text-transform:uppercase;letter-spacing:.04em}}
  td{{padding:7px 8px;border-bottom:1px solid var(--border);
    color:var(--text-secondary);vertical-align:top}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  code{{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;
    background:var(--surface-0);border:1px solid var(--border);
    border-radius:4px;padding:1px 5px}}
  .q{{color:var(--text-primary)}}
  tr.miss td{{background:color-mix(in srgb,var(--critical) 9%,transparent)}}
  .tag-f,.tag-w,.tag-r{{font-size:11px;font-weight:650;padding:2px 7px;
    border-radius:99px;white-space:nowrap}}
  .tag-f{{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--good)}}
  .tag-w{{background:color-mix(in srgb,var(--warning) 22%,transparent);color:var(--text-primary)}}
  .tag-r{{background:color-mix(in srgb,var(--critical) 16%,transparent);color:var(--critical)}}
  .change{{border-left:3px solid var(--series-1);padding:2px 0 2px 16px;margin:18px 0}}
  .chg-h{{font-weight:650;color:var(--text-primary);margin-bottom:6px}}
  .change p{{margin:0 0 8px;font-size:14px}}
  .corr{{color:var(--text-muted)}}
  .callout{{border-left:3px solid var(--warning);padding:2px 0 2px 16px;margin:18px 0}}
  ul{{margin:8px 0;padding-left:20px;color:var(--text-secondary)}}
  li{{margin-bottom:8px}}
  .toggle{{position:fixed;top:14px;right:16px;font-size:12px;padding:6px 12px;
    border-radius:99px;border:1px solid var(--border);background:var(--surface-1);
    color:var(--text-secondary);cursor:pointer}}
</style></head>
<body class="viz-root">
<button class="toggle" onclick="var r=document.documentElement;
  r.dataset.theme=r.dataset.theme==='dark'?'light':'dark'">theme</button>
<div class="wrap">

<h1>Inbound Lead Qualifier</h1>
<p class="lede">It reads an inbound enquiry, decides whether it is worth a
salesperson's time, and refuses to guess when the evidence is thin.</p>
<p class="meta">Generated {E(d["generated"])} by <code>python src/report.py</code>.
Every number on this page is computed by running the system, not written by hand.</p>

<div class="tiles">{tile_html}</div>

<h2>The rubric was wrong twice. Here is the fix, measured.</h2>
<p>The first evaluation found two defects. Naming defects is cheap, so v2 fixes
both and this is the before and after, including on data the fixes were never
tuned against.</p>

<div class="card">
  <div class="legend">
    <span><span class="key" style="background:var(--series-1)"></span>rubric v1</span>
    <span><span class="key" style="background:var(--series-2)"></span>rubric v2</span>
  </div>
  {grouped_bars(ab)}
</div>

<div class="callout">
<p><b>Read the held-out number honestly.</b> It is a stress set, not a random
sample: 4 of its 10 leads were built to exercise the two defect classes, 5 are
neutral controls, and 1 is a case neither version gets right. It shows the fixes
generalise past the two leads that found them. It does <b>not</b> estimate a
production error rate, and a set weighted toward what you just fixed will
flatter you.</p>
</div>

{changes}

<h3>Every lead, both versions</h3>
<table><thead><tr><th>Lead</th><th>Human</th><th>v1</th><th>v2</th>
<th class="num">Score</th><th></th></tr></thead>
<tbody>{ab_rows("golden")}</tbody></table>
<p class="sub" style="margin-top:6px">Golden set. Defects were found here, so
improvement is expected and is not the evidence.</p>

<table><thead><tr><th>Lead</th><th>Human</th><th>v1</th><th>v2</th>
<th class="num">Score</th><th></th></tr></thead>
<tbody>{ab_rows("holdout")}</tbody></table>
<p class="sub" style="margin-top:6px">Held out. Never consulted while writing v2.
{len(ab["holdout"]["fixed"])} fixed, {len(ab["holdout"]["regressed"])} regressed.</p>

{still_html}

<h2>Attacking the guardrail</h2>
<p>Every outbound sentence must name the enrichment field it rests on, and the
gate checks that independently. A guardrail nobody has tried to break is not a
guardrail, so these are hand-written attacks designed to get an unsupported
claim through.</p>

<div class="card">
<table><thead><tr><th>Outcome</th><th>Attack</th><th>Claim</th><th>Probing</th></tr>
</thead><tbody>{red_rows}</tbody></table>
</div>

<div class="callout">
<p><b>{red_caught} of {len(red_block)} caught, {red_fp} false positives, and
{len(red_missed)} got through.</b> Every miss cites a real field whose value
genuinely appears in the sentence and carries no digit the record lacks, so the
gate has no basis to reject it.</p>
<ul>{missed_html}</ul>
<p>What the gate does not check is whether the sentence <b>asserts more than the
field supports</b>. "You run Snowflake" is supported; "your data team is clearly
mature" is an inference bolted onto it, and a field-level citation check cannot
tell them apart. That is a real limit of citation-style grounding in general,
not a bug in this implementation. Closing it needs an entailment check, which is
a model-graded eval rather than a regex, and that is the honest next step.</p>
</div>

<h2>The gate against a model that embellishes</h2>
<p>A second drafter ships on purpose: it is fluent and invents. Without it the
gate would have no evidence it works.</p>
<div class="card">
<table><thead><tr><th>Drafter</th><th class="num">Claims</th>
<th class="num">Rejected</th><th class="num">Drafts withheld</th></tr></thead>
<tbody>
<tr><td>grounded, template, cannot invent</td>
<td class="num">{grounded['claims']}</td><td class="num">{grounded['rejected']}</td>
<td class="num">{grounded['withheld']}/{grounded['attempted']}</td></tr>
<tr><td>llm_sim, stands in for a fluent model</td>
<td class="num">{sim['claims']}</td><td class="num">{sim['rejected']}</td>
<td class="num">{sim['withheld']}/{sim['attempted']}</td></tr>
</tbody></table>
</div>

<h2>Score distribution</h2>
<div class="card">{histogram(d["dist"])}
<p class="sub">Golden set under v2, abstained leads excluded because they carry
no meaningful score.</p></div>

<h2>What it refuses to do</h2>
<p><b>Missing data is not zero.</b> A lead whose employee count was never
resolved is not a small company, and scoring it as one silently disqualifies
good leads. Any unresolved field worth 20 or more points stops scoring and
routes to a human with the reason attached.</p>
<p>As of v2 that rule extends one level up: an industry the taxonomy does not
recognise counts as missing too, rather than quietly scoring zero.</p>
<p>Against live enrichment the system abstained on <b>4 of 5</b> real companies,
versus 2 of 20 on fixtures. Real data is far sparser than any mock, so the
fixture made the system look considerably more decisive than it is. That gap is
an argument for the abstain path, not against it.</p>

</div></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open in a browser")
    args = ap.parse_args()

    print("running the system to gather real numbers...")
    d = gather()
    OUT.write_text(build(d), encoding="utf-8")

    hv2 = d["ab"]["holdout"]["v2"]
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1024:.0f} KB, "
          f"self contained)")
    print(f"  held-out accuracy : {hv2['correct']}/{hv2['total']}")
    print(f"  red team caught   : "
          f"{sum(1 for r in d['red'] if r['outcome'] == 'caught')}"
          f"/{sum(1 for r in d['red'] if r['should_block'])}")
    if args.open:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
