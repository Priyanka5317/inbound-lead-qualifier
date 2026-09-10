/**
 * Parity: do all THREE implementations of the rubric agree?
 *
 * The scoring rules exist in three places, because three surfaces need them:
 *
 *   rubric.json + src/qualify.py    the evaluation, and the source of truth
 *   n8n Code node                   what a GTM team would actually run
 *   web/rubric-engine.js            the browser demo, no server
 *
 * Three copies is a real maintenance cost. The only honest way to carry it
 * is to test it rather than promise it, so this scores all twenty golden
 * leads through every implementation and fails if any two disagree.
 *
 *   node tests/parity_check.mjs
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { qualify as webQualify, route as webRoute } from '../web/rubric-engine.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(ROOT, p), 'utf8');

const workflow = JSON.parse(read('n8n/inbound-lead-qualifier.json'));
const scoreNode = workflow.nodes.find((n) => n.name === 'Code: Score & Route');
const enrichment = JSON.parse(read('data/enrichment_source.json'));
const rubricV1 = JSON.parse(read('rubric.json'));

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

const golden = parseCsv(read('data/golden_set.csv'));

// Python's answers, from the decision log it writes.
let pythonRoutes;
try {
  pythonRoutes = new Map(
    read('out/decisions-golden.jsonl').trim().split('\n')
      .map((l) => JSON.parse(l))
      .map((r) => [r.lead_id, { route: r.decision.route, score: r.decision.score }]),
  );
} catch {
  console.error('\nRun the Python side first so there is something to compare against:');
  console.error('    python src/pipeline.py --golden\n');
  process.exit(2);
}

const runNode = new Function('$json', '$', scoreNode.parameters.jsCode);

let mismatches = 0;
console.log('='.repeat(82));
console.log('  RUBRIC PARITY: python vs n8n vs browser'.padStart(56));
console.log('='.repeat(82));
console.log('lead     python           n8n              browser          parity');
console.log('-'.repeat(82));

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

  // n8n Code node, with n8n's helpers stubbed
  const n8nOut = runNode(
    { ...org, company_name: org.company_name ?? null },
    () => ({ item: { json: form } }),
  ).json;

  // browser engine, same rubric file the Python side reads
  const facts = { ...org, ...form };
  const webOut = webRoute(webQualify(facts, rubricV1), rubricV1);

  const py = pythonRoutes.get(lead.lead_id);
  const agree = py.route === n8nOut.route && py.score === n8nOut.score
    && py.route === webOut.route && py.score === webOut.score;
  if (!agree) mismatches++;

  const cell = (r, s) => `${r} ${String(s).padStart(3)}`.padEnd(17);
  console.log(
    `${lead.lead_id.padEnd(9)}${cell(py.route, py.score)}`
    + `${cell(n8nOut.route, n8nOut.score)}${cell(webOut.route, webOut.score)}`
    + `${agree ? 'ok' : 'MISMATCH'}`,
  );
}

console.log('-'.repeat(82));
console.log(
  mismatches === 0
    ? `PARITY OK: python, n8n and the browser agree on all ${golden.length} golden leads.`
    : `PARITY BROKEN: ${mismatches}/${golden.length} disagree. Fix before shipping.`,
);
console.log('='.repeat(82));
process.exit(mismatches === 0 ? 0 : 1);
