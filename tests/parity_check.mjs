/**
 * Parity check: does the n8n Code node score identically to src/qualify.py?
 *
 * The project carries the rubric twice, once as rubric.json read by Python and
 * once as JavaScript inside the n8n Code node. That duplication is a real cost
 * and the only honest way to hold it is to test it rather than trust it.
 *
 *   node tests/parity_check.mjs
 *
 * Reads the golden set, replays it through the extracted n8n jsCode with a
 * small shim for n8n's $json / $() helpers, and diffs against the routes
 * Python wrote into out/decisions-golden.jsonl.
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const workflow = JSON.parse(
  readFileSync(join(ROOT, 'n8n', 'inbound-lead-qualifier.json'), 'utf8'),
);
const scoreNode = workflow.nodes.find((n) => n.name === 'Code: Score & Route');
const jsCode = scoreNode.parameters.jsCode;

const enrichment = JSON.parse(
  readFileSync(join(ROOT, 'data', 'enrichment_source.json'), 'utf8'),
);

function parseCsv(text) {
  const rows = [];
  const lines = text.replace(/\r/g, '').trim().split('\n');
  const head = lines.shift().split(',');
  for (const line of lines) {
    const cells = [];
    let cur = '', inQ = false;
    for (const ch of line) {
      if (ch === '"') inQ = !inQ;
      else if (ch === ',' && !inQ) { cells.push(cur); cur = ''; }
      else cur += ch;
    }
    cells.push(cur);
    rows.push(Object.fromEntries(head.map((h, i) => [h, cells[i] ?? ''])));
  }
  return rows;
}

const golden = parseCsv(readFileSync(join(ROOT, 'data', 'golden_set.csv'), 'utf8'));

const pythonRoutes = new Map(
  readFileSync(join(ROOT, 'out', 'decisions-golden.jsonl'), 'utf8')
    .trim().split('\n').map((l) => JSON.parse(l))
    .map((r) => [r.lead_id, { route: r.decision.route, score: r.decision.score }]),
);

// Run the node's body with n8n's helpers stubbed out.
const runNode = new Function('$json', '$', `${jsCode}`);

let mismatches = 0;
console.log('lead     n8n route        n8n  py route         py   parity');
console.log('-'.repeat(66));

for (const lead of golden) {
  const domain = lead.email.split('@')[1];
  const org = enrichment[domain] ?? {};

  const demoRaw = lead.requested_demo.trim().toLowerCase();
  const form = {
    email: lead.email,
    contact_name: lead.contact_name,
    requested_demo: demoRaw === 'true' ? true : demoRaw === 'false' ? false : null,
    pricing_page_visits: lead.pricing_page_visits === '' ? null : Number(lead.pricing_page_visits),
    emails_opened: lead.emails_opened === '' ? null : Number(lead.emails_opened),
  };

  const $json = { ...org, company_name: org.company_name ?? null };
  const $ = () => ({ item: { json: form } });

  const out = runNode($json, $).json;
  const py = pythonRoutes.get(lead.lead_id);
  const ok = out.route === py.route && out.score === py.score;
  if (!ok) mismatches++;

  console.log(
    `${lead.lead_id.padEnd(9)}${out.route.padEnd(17)}${String(out.score).padStart(3)}  ` +
    `${py.route.padEnd(17)}${String(py.score).padStart(3)}  ${ok ? 'ok' : 'MISMATCH'}`,
  );
}

console.log('-'.repeat(66));
console.log(
  mismatches === 0
    ? `PARITY OK: n8n and Python agree on all ${golden.length} golden leads.`
    : `PARITY BROKEN: ${mismatches}/${golden.length} disagree. Fix before shipping.`,
);
process.exit(mismatches === 0 ? 0 : 1);
