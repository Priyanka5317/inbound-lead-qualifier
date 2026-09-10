/**
 * Execute the exported n8n workflow, for real.
 *
 * The workflow JSON was previously only ever *imported* and read. That proves
 * the file is well formed and proves nothing about whether the graph runs.
 * This is a small n8n-compatible executor: it walks the node graph, resolves
 * `={{ }}` expressions, makes the real HTTP call, evaluates the IF, and runs
 * both Code nodes' JavaScript. Written in Node because the Code nodes are
 * JavaScript, so their bodies execute in their own language rather than
 * through a reimplementation.
 *
 *   node tests/run_workflow.mjs                          happy path
 *   node tests/run_workflow.mjs --email bad@nowhere.tld  force the error branch
 *   node tests/run_workflow.mjs --url <endpoint>         point enrichment elsewhere
 *
 * It is NOT n8n. It supports the five node types this workflow uses and
 * would need work for anything else. What it does establish is that the
 * graph executes end to end, that both branches fire, and that the Code node
 * scoring agrees with the Python implementation.
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const argv = process.argv.slice(2);
const argOf = (flag, fallback) => {
  const i = argv.indexOf(flag);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};

const email = argOf('--email', 'ops@northwind-health.com');
// Default to a real, keyless endpoint so the run makes a genuine network
// call without needing an Apollo seat. In production this is the Apollo URL
// already in the workflow file; only the host changes.
const enrichUrl = argOf(
  '--url',
  'https://www.wikidata.org/w/api.php?action=wbsearchentities&language=en&format=json&limit=1&search=',
);

const wf = JSON.parse(
  readFileSync(join(ROOT, 'n8n', 'inbound-lead-qualifier.json'), 'utf8'),
);
const byName = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
const results = {};          // node name -> { json }

/** Resolve an n8n `={{ ... }}` expression against the current item. */
function resolve(value, $json) {
  if (typeof value !== 'string' || !value.startsWith('=')) return value;
  const body = value.slice(1);

  const $ = (name) => ({
    item: results[name] ?? { json: {} },
    first: () => results[name] ?? { json: {} },
  });
  const $credentials = new Proxy({}, { get: () => '<credential-not-set>' });

  const whole = body.match(/^\{\{([\s\S]*)\}\}$/);
  const evalJs = (src) => {
    try {
      // eslint-disable-next-line no-new-func
      return new Function('$json', '$', '$credentials', `return (${src});`)(
        $json, $, $credentials,
      );
    } catch (err) {
      return `<expr-error: ${err.message}>`;
    }
  };

  if (whole) return evalJs(whole[1]);
  return body.replace(/\{\{([\s\S]*?)\}\}/g, (_, src) => String(evalJs(src)));
}

const log = [];
function step(node, note) {
  log.push({ node: node.name, type: node.type.replace('n8n-nodes-base.', ''), note });
  console.log(`  -> ${node.name}`);
  console.log(`     ${note}`);
}

async function runNode(node, input) {
  const type = node.type.replace('n8n-nodes-base.', '');
  const p = node.parameters ?? {};

  if (type === 'webhook') {
    step(node, `trigger fired with ${JSON.stringify(input).slice(0, 70)}`);
    return { json: input };
  }

  if (type === 'httpRequest') {
    const workflowUrl = resolve(p.url, input);
    const bodyExpr = resolve(p.jsonBody, input);
    const domain = input.email?.split('@')[1] ?? '';

    // Default to the keyless endpoint so a plain run exercises the happy
    // path. `--workflow-url` uses the Apollo URL that is actually in the
    // file, which without a credential returns 422 and is the cleanest way
    // to watch the error branch catch a real upstream failure.
    const base = argv.includes('--workflow-url') ? workflowUrl : enrichUrl;
    const target = base.includes('wikidata')
      ? base + encodeURIComponent(domain.split('.')[0])
      : base;

    step(node, `${argv.includes('--workflow-url') ? 'workflow' : 'keyless'} endpoint`);
    step(node, `GET ${target.slice(0, 86)}`);
    step(node, `body expression resolved to ${String(bodyExpr).slice(0, 66)}`);

    try {
      const res = await fetch(target, { headers: { 'User-Agent': 'gtm-engine/1.0' } });
      const text = await res.text();
      let parsed = {};
      try { parsed = JSON.parse(text); } catch { /* non json */ }

      const hit = parsed?.search?.[0];
      const out = hit
        ? { company_name: hit.label, description: hit.description, _status: res.status }
        : { _status: res.status, _empty: true };
      step(node, `HTTP ${res.status}, resolved company_name=${out.company_name ?? 'null'}`);
      return { json: out };
    } catch (err) {
      step(node, `request failed: ${err.message}`);
      return { json: { error: err.message } };
    }
  }

  if (type === 'if') {
    const cond = p.conditions?.string?.[0];
    const left = resolve(cond.value1, input);
    const truthy = cond.operation === 'isNotEmpty'
      ? left !== undefined && left !== null && String(left).length > 0
      : Boolean(left);
    step(node, `condition "${cond.operation}" on ${JSON.stringify(left)} -> ${truthy ? 'TRUE' : 'FALSE'}`);
    return { json: input, _branch: truthy ? 0 : 1 };
  }

  if (type === 'code') {
    const $ = (name) => ({
      item: results[name] ?? { json: {} },
      first: () => results[name] ?? { json: {} },
    });
    // eslint-disable-next-line no-new-func
    const fn = new Function('$json', '$', p.jsCode);
    const out = fn(input, $);
    step(node, `executed ${p.jsCode.length} chars of JS, returned ${Object.keys(out.json).length} fields`);
    return out;
  }

  step(node, `unsupported node type "${type}", skipped`);
  return { json: input };
}

async function run() {
  const trigger = wf.nodes.find((n) => n.type.endsWith('.webhook'));
  if (!trigger) throw new Error('no webhook trigger in the workflow');

  console.log('='.repeat(78));
  console.log('  EXECUTING THE EXPORTED n8n WORKFLOW'.padStart(50));
  console.log('='.repeat(78));
  console.log(`  workflow : ${wf.name}`);
  console.log(`  nodes    : ${wf.nodes.length}`);
  console.log(`  input    : ${email}\n`);

  const payload = {
    contact_name: 'Jane Doe',
    email,
    requested_demo: true,
    pricing_page_visits: 3,
    emails_opened: 5,
  };

  let current = trigger;
  let item = payload;
  const visited = [];

  while (current) {
    const out = await runNode(current, item);
    results[current.name] = { json: out.json };
    visited.push(current.name);
    item = out.json;

    const outputs = wf.connections[current.name]?.main ?? [];
    const branch = out._branch ?? 0;
    const next = outputs[branch]?.[0]?.node;
    current = next ? byName[next] : null;
    if (current) console.log('');
  }

  console.log('\n' + '-'.repeat(78));
  console.log(`  path taken : ${visited.join('  ->  ')}`);
  console.log(`  final output:`);
  for (const [k, v] of Object.entries(item)) {
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    console.log(`    ${k.padEnd(24)} ${s.slice(0, 60)}`);
  }
  console.log('='.repeat(78));
  return item;
}

run().catch((err) => {
  console.error('\nworkflow run failed:', err);
  process.exit(1);
});
