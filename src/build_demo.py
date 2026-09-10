"""Build the browser demo into one self-contained file.

`web/index.html` is generated, not hand written. The scoring engine, both
rubric versions, the golden set and the enrichment records are inlined at
build time from the same files the Python evaluation reads, so the demo
cannot drift from the thing it demonstrates.

Inlining also means the result works from a `file://` URL as well as from
GitHub Pages, because a browser blocks `fetch` and ES module imports over
`file://` and a demo that only runs on a server is a demo most people never
open.

    python src/build_demo.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = WEB / "index.html"

# Validated with the dataviz palette validator, light and dark, all checks
# pass. Same two slots the evaluation report uses.
S1_L, S2_L = "#2a78d6", "#eb6834"
S1_D, S2_D = "#3987e5", "#d95926"


def load() -> dict:
    engine = (WEB / "rubric-engine.js").read_text(encoding="utf-8")
    # strip the module keyword; the demo inlines it as a plain script
    engine = engine.replace("export function", "function")

    enrichment = {
        k: v for k, v in
        json.loads((ROOT / "data" / "enrichment_source.json").read_text("utf-8")).items()
        if not k.startswith("_")
    }

    leads = []
    with (ROOT / "data" / "golden_set.csv").open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            raw = (r["requested_demo"] or "").strip().lower()
            leads.append({
                "lead_id": r["lead_id"],
                "contact_name": r["contact_name"],
                "email": r["email"],
                "message": r["message"],
                "requested_demo": True if raw == "true" else False if raw == "false" else None,
                "pricing_page_visits": int(r["pricing_page_visits"]) if r["pricing_page_visits"].isdigit() else None,
                "emails_opened": int(r["emails_opened"]) if r["emails_opened"].isdigit() else None,
                "human_label": r["human_label"],
                "human_reasoning": r["human_reasoning"],
            })

    return {
        "engine": engine,
        "v1": json.loads((ROOT / "rubric.json").read_text("utf-8")),
        "v2": json.loads((ROOT / "rubric-v2.json").read_text("utf-8")),
        "enrichment": enrichment,
        "leads": leads,
    }


ATTACKS = [
    ("invented funding amount", "funding_stage",
     "Congratulations on the Series B, raising $40M is no small thing."),
    ("no supporting field", "",
     "Your competitors are already automating this."),
    ("rounded the headcount", "employee_count",
     "At around 350 people manual triage stops scaling."),
    ("the true headcount", "employee_count",
     "At 340 people manual triage stops scaling."),
    ("percentage injection", "tech_stack",
     "Teams on Snowflake cut triage time 60% with this."),
    ("magnitude in words", "employee_count",
     "With several hundred people, triage is a real cost for you."),
    ("gets through: unsupported judgement", "tech_stack",
     "Since you already run Snowflake, your data team is clearly mature."),
    ("gets through: invented buying signal", "company_name",
     "Northwind Health is evaluating vendors this quarter."),
]


def build(d: dict) -> str:
    attacks_js = json.dumps([
        {"name": n, "support": s, "text": t} for n, s, t in ATTACKS
    ])
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inbound Lead Qualifier, live demo</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-0:#f6f5f2; --surface-1:#fcfcfb; --surface-2:#eeece7;
    --border:#e3e1db; --text-primary:#0b0b0b; --text-secondary:#52514e;
    --text-muted:#77756e; --series-1:{S1_L}; --series-2:{S2_L};
    --good:#0ca30c; --critical:#d03b3b; --warning:#fab219;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-0:#111110; --surface-1:#1a1a19; --surface-2:#232320;
      --border:#33322f; --text-primary:#fff; --text-secondary:#c3c2b7;
      --text-muted:#8e8d85; --series-1:{S1_D}; --series-2:{S2_D};
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --surface-0:#111110; --surface-1:#1a1a19; --surface-2:#232320;
    --border:#33322f; --text-primary:#fff; --text-secondary:#c3c2b7;
    --text-muted:#8e8d85; --series-1:{S1_D}; --series-2:{S2_D};
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--surface-0);color:var(--text-primary);
    font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    font-variant-ligatures:none}}
  .wrap{{max-width:1000px;margin:0 auto;padding:38px 22px 90px}}
  h1{{font-size:30px;margin:0 0 6px;letter-spacing:-.02em}}
  h2{{font-size:19px;margin:40px 0 4px;letter-spacing:-.01em}}
  p{{margin:0 0 12px;color:var(--text-secondary)}}
  .lede{{font-size:17px;color:var(--text-secondary)}}
  .meta{{font-size:13px;color:var(--text-muted);margin-bottom:6px}}
  a{{color:var(--series-1)}}
  .card{{background:var(--surface-1);border:1px solid var(--border);
    border-radius:10px;padding:18px 20px;margin:14px 0}}
  .row{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  @media(max-width:760px){{.row{{grid-template-columns:1fr}}}}
  label{{display:block;font-size:12px;color:var(--text-muted);
    text-transform:uppercase;letter-spacing:.05em;margin:10px 0 4px}}
  select,input,textarea{{width:100%;padding:8px 10px;border-radius:7px;
    border:1px solid var(--border);background:var(--surface-0);
    color:var(--text-primary);font:14px/1.5 inherit}}
  textarea{{min-height:76px;resize:vertical}}
  .grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
  @media(max-width:760px){{.grid4{{grid-template-columns:repeat(2,1fr)}}}}
  .badge{{display:inline-block;padding:5px 13px;border-radius:99px;
    font-size:13px;font-weight:650}}
  .b-call{{background:color-mix(in srgb,var(--good) 17%,transparent);color:var(--good)}}
  .b-nurture{{background:color-mix(in srgb,var(--series-1) 17%,transparent);color:var(--series-1)}}
  .b-dq{{background:var(--surface-2);color:var(--text-secondary)}}
  .b-abstain{{background:color-mix(in srgb,var(--warning) 26%,transparent);color:var(--text-primary)}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
    color:var(--text-muted);border-bottom:1px solid var(--border);padding:6px 7px}}
  td{{padding:6px 7px;border-bottom:1px solid var(--border);color:var(--text-secondary)}}
  td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .met{{color:var(--good);font-weight:600}}
  .notmet{{color:var(--text-muted)}}
  .miss{{color:var(--warning);font-weight:600}}
  .why{{font-size:13px;color:var(--text-secondary);margin-top:10px;
    padding:10px 12px;background:var(--surface-0);border-radius:7px;
    border-left:3px solid var(--series-1)}}
  code{{font:12px ui-monospace,Menlo,monospace;background:var(--surface-0);
    border:1px solid var(--border);border-radius:4px;padding:1px 5px}}
  button{{cursor:pointer;font:13px inherit;padding:7px 13px;border-radius:7px;
    border:1px solid var(--border);background:var(--surface-2);
    color:var(--text-primary)}}
  button:hover{{border-color:var(--series-1)}}
  button.primary{{background:var(--series-1);color:#fff;border-color:transparent;
    font-weight:600}}
  .chips{{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0}}
  .chips button{{font-size:12px;padding:5px 10px}}
  .verdict{{padding:13px 15px;border-radius:8px;margin-top:12px;font-size:14px}}
  .v-pass{{background:color-mix(in srgb,var(--good) 12%,transparent);
    border-left:3px solid var(--good)}}
  .v-block{{background:color-mix(in srgb,var(--critical) 12%,transparent);
    border-left:3px solid var(--critical)}}
  .v-hole{{background:color-mix(in srgb,var(--warning) 16%,transparent);
    border-left:3px solid var(--warning)}}
  .toggle{{position:fixed;top:13px;right:15px;font-size:12px;padding:6px 12px;
    border-radius:99px;border:1px solid var(--border);background:var(--surface-1);
    color:var(--text-secondary);cursor:pointer;z-index:9}}
  .sub{{font-size:12px;color:var(--text-muted)}}
  .kv{{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;
    color:var(--text-secondary)}}
  .kv b{{color:var(--text-primary);font-weight:600}}
</style></head>
<body class="viz-root">
<button class="toggle" onclick="var r=document.documentElement;
  r.dataset.theme=r.dataset.theme==='dark'?'light':'dark'">theme</button>
<div class="wrap">

<h1>Inbound Lead Qualifier</h1>
<p class="lede">A GTM system that reads an inbound enquiry, decides whether it
is worth a salesperson's time, and refuses to guess when the evidence is thin.</p>
<p class="meta">Running entirely in your browser. No server, no key, nothing to
install. The scoring below is the same code the test suite runs.
<a href="https://github.com/Priyanka5317/inbound-lead-qualifier">Source</a></p>

<h2>1. Qualify a lead</h2>
<p>Pick an enquiry, or change the numbers and watch the decision move. Both
rubric versions score it at once, so you can see what the second one fixed.</p>

<div class="card">
  <div class="row">
    <div>
      <label for="lead">Inbound enquiry</label>
      <select id="lead"></select>
      <div class="grid4" style="margin-top:12px">
        <div><label for="demo">Demo?</label>
          <select id="demo"><option value="true">yes</option>
          <option value="false">no</option><option value="">not answered</option></select></div>
        <div><label for="pricing">Pricing visits</label>
          <input id="pricing" type="number" min="0" max="20"></div>
        <div><label for="emails">Emails opened</label>
          <input id="emails" type="number" min="0" max="20"></div>
        <div><label for="emp">Headcount</label>
          <input id="emp" type="number" min="0" max="99999" placeholder="unknown"></div>
      </div>
      <div id="company" style="margin-top:12px"></div>
    </div>
    <div>
      <label>Decision</label>
      <div id="decision"></div>
    </div>
  </div>
  <div style="margin-top:16px"><label>How it scored, rubric v2</label>
    <table><thead><tr><th>Criterion</th><th class="n">Awarded</th><th>Verdict</th></tr></thead>
    <tbody id="breakdown"></tbody></table>
  </div>
</div>

<h2>2. Try to get a lie past the gate</h2>
<p>Every outbound sentence must name the enrichment field it rests on, and the
gate checks that claim independently. <b>Write a sentence about Northwind
Health and try to sneak something past it.</b> The record it checks against is
below the box.</p>

<div class="card">
  <div class="chips" id="attacks"></div>
  <label for="claim">Your sentence</label>
  <textarea id="claim">Congratulations on the Series B, raising $40M is no small thing.</textarea>
  <div class="row" style="margin-top:10px">
    <div><label for="support">Field it rests on</label>
      <select id="support"></select></div>
    <div style="display:flex;align-items:flex-end">
      <button class="primary" id="check" style="width:100%">Check it</button></div>
  </div>
  <div id="gate"></div>
  <div style="margin-top:14px"><label>The record it is checked against</label>
    <div id="record" class="sub"></div></div>
</div>

<h2>3. What the agent is for</h2>
<p>Enrichment has four sources. The old policy called all four, every time.
Measured over five real domains that is <b>20 calls with 11 of them wasted</b>,
and a dead domain burns all four. So the model gets those sources as tools and
picks. The evaluation is written so the agent can lose: if it matches the fixed
policy at the same cost, the printed verdict says to delete it.</p>
<div class="card">
  <div class="kv"><span>fixed policy, mean coverage</span><b>0.32</b></div>
  <div class="kv"><span>fixed policy, calls spent</span><b>20</b></div>
  <div class="kv"><span>of those, returned nothing new</span><b>11</b></div>
  <div class="kv"><span>agent</span><b>needs an API key, reported pending</b></div>
  <p class="sub" style="margin-top:10px">An unrun comparison is not the same as
  a favourable one, so it is reported as pending rather than quietly skipped.</p>
</div>

</div>

<script>
{d["engine"]}

const V1 = {json.dumps(d["v1"])};
const V2 = {json.dumps(d["v2"])};
const ENRICH = {json.dumps(d["enrichment"])};
const LEADS = {json.dumps(d["leads"])};
const ATTACKS = {attacks_js};
const FIELDS = ["company_name","employee_count","industry","hq_country",
                "tech_stack","funding_stage","annual_revenue"];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));

// ---------- section 1
function currentLead() {{
  const lead = LEADS.find((l) => l.lead_id === $('lead').value) || LEADS[0];
  const org = ENRICH[lead.email.split('@')[1]] || {{}};
  const empRaw = $('emp').value.trim();
  return {{
    lead,
    facts: {{
      ...org,
      employee_count: empRaw === '' ? null : Number(empRaw),
      requested_demo: $('demo').value === '' ? null : $('demo').value === 'true',
      pricing_page_visits: $('pricing').value === '' ? null : Number($('pricing').value),
      emails_opened: $('emails').value === '' ? null : Number($('emails').value),
    }},
    org,
  }};
}}

function badge(r) {{
  const cls = r === 'book a call' ? 'b-call' : r === 'nurture' ? 'b-nurture'
    : r === 'abstain' ? 'b-abstain' : 'b-dq';
  return `<span class="badge ${{cls}}">${{esc(r)}}</span>`;
}}

function render() {{
  const {{ lead, facts, org }} = currentLead();
  const r1 = route(qualify(facts, V1), V1);
  const r2 = route(qualify(facts, V2), V2);

  $('company').innerHTML = `<div class="sub"><b>${{esc(org.company_name || 'unknown company')}}</b>`
    + ` &middot; ${{esc(org.industry || 'industry unknown')}}`
    + ` &middot; ${{esc(org.hq_country || 'country unknown')}}`
    + ` &middot; ${{esc((org.tech_stack || []).join(', ') || 'no tech signals')}}</div>`
    + `<div class="sub" style="margin-top:6px">"${{esc(lead.message)}}"</div>`;

  const moved = r1.route !== r2.route;
  $('decision').innerHTML =
    `<div class="kv"><span>rubric v1</span><span>${{badge(r1.route)}} <b>${{r1.score}}</b></span></div>`
    + `<div class="kv"><span>rubric v2</span><span>${{badge(r2.route)}} <b>${{r2.score}}</b></span></div>`
    + `<div class="kv"><span>a human said</span><b>${{esc(lead.human_label)}}</b></div>`
    + (moved ? `<div class="why"><b>v2 changed this decision.</b> ${{esc(r2.explanation)}}</div>`
             : `<div class="why">${{esc(r2.explanation)}}</div>`);

  const q2 = qualify(facts, V2);
  $('breakdown').innerHTML = q2.breakdown.map((b) => {{
    const cls = b.verdict === 'met' ? 'met'
      : (b.verdict === 'missing' || b.verdict === 'unrecognised') ? 'miss' : 'notmet';
    return `<tr><td>${{esc(b.label)}}</td>`
      + `<td class="n">${{b.awarded}}/${{b.available}}</td>`
      + `<td class="${{cls}}">${{esc(b.verdict)}}</td></tr>`;
  }}).join('')
   + `<tr><td><b>Total</b></td><td class="n"><b>${{q2.score}}</b></td>`
   + `<td class="sub">fit ${{q2.fit_score}} / intent ${{q2.intent_score}}</td></tr>`;
}}

// ---------- section 2
const GATE_RECORD = ENRICH['northwind-health.com'];

function renderRecord() {{
  $('record').innerHTML = FIELDS.map((f) => {{
    const v = GATE_RECORD[f];
    const shown = v === null || v === undefined ? '<i>unresolved</i>'
      : Array.isArray(v) ? esc(v.join(', ')) : esc(v);
    return `<code>${{f}}</code> ${{shown}}`;
  }}).join(' &nbsp; ');
}}

function checkClaim() {{
  const text = $('claim').value.trim();
  if (!text) return;
  const support = $('support').value || null;
  const res = validate([{{ text, support }}], GATE_RECORD);

  if (res.passed) {{
    const risky = /clearly|obviously|mature|evaluating|mostly|probably|seems|likely/i.test(text);
    $('gate').innerHTML = `<div class="verdict ${{risky ? 'v-hole' : 'v-pass'}}">`
      + `<b>Passed.</b> Every claim traces to a real field and every number is `
      + `in the record.`
      + (risky
        ? `<br><br><b>And this is the known hole.</b> The sentence cites a real `
          + `field whose value genuinely appears, and carries no invented number, `
          + `so the gate has no basis to reject it. But it asserts more than the `
          + `field supports. A field-level citation check cannot tell "you run `
          + `Snowflake" from "your data team is clearly mature". Closing that `
          + `needs an entailment check, which is a model-graded eval rather than `
          + `a regex.`
        : '')
      + `</div>`;
  }} else {{
    $('gate').innerHTML = `<div class="verdict v-block"><b>Rejected, and the `
      + `whole draft is withheld.</b><br>${{esc(res.rejected[0].why)}}</div>`;
  }}
}}

// ---------- boot
$('lead').innerHTML = LEADS.map((l) => {{
  const org = ENRICH[l.email.split('@')[1]] || {{}};
  return `<option value="${{l.lead_id}}">${{l.lead_id}} &middot; `
    + `${{esc(org.company_name || l.email)}}</option>`;
}}).join('');

$('support').innerHTML = '<option value="">(no field, unsupported)</option>'
  + FIELDS.map((f) => `<option value="${{f}}">${{f}}</option>`).join('');
$('support').value = 'funding_stage';

$('attacks').innerHTML = ATTACKS.map((a, i) =>
  `<button data-i="${{i}}">${{esc(a.name)}}</button>`).join('');
$('attacks').onclick = (e) => {{
  const i = e.target.dataset.i;
  if (i === undefined) return;
  $('claim').value = ATTACKS[i].text;
  $('support').value = ATTACKS[i].support;
  checkClaim();
}};

function syncLead() {{
  const lead = LEADS.find((l) => l.lead_id === $('lead').value) || LEADS[0];
  const org = ENRICH[lead.email.split('@')[1]] || {{}};
  $('demo').value = lead.requested_demo === null ? '' : String(lead.requested_demo);
  $('pricing').value = lead.pricing_page_visits ?? '';
  $('emails').value = lead.emails_opened ?? '';
  $('emp').value = org.employee_count ?? '';
  render();
}}

$('lead').onchange = syncLead;
['demo', 'pricing', 'emails', 'emp'].forEach((id) => {{
  $(id).oninput = render; $(id).onchange = render;
}});
$('check').onclick = checkClaim;

syncLead();
renderRecord();
checkClaim();
</script>
</body></html>
"""


def main() -> None:
    d = load()
    OUT.write_text(build(d), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1024:.0f} KB, "
          f"self contained)")
    print(f"  inlined: engine, rubric v1 + v2, {len(d['leads'])} leads, "
          f"{len(d['enrichment'])} companies")
    print("  works from file:// and from GitHub Pages")


if __name__ == "__main__":
    main()
