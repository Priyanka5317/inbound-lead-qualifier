/**
 * Grade your own n8n build against the Week 1 "done when".
 *
 * The drill is: build the five node workflow from a blank canvas with no
 * tutorial open. The problem with that as a goal is that "done" is a feeling.
 * This makes it a pass or fail.
 *
 *   1. build it in n8n
 *   2. Workflow menu > Download, which saves a .json
 *   3. node tests/validate_my_workflow.mjs ~/Downloads/My_workflow.json
 *
 * Run it with no argument and it grades the reference build instead, which is
 * how you check the checker.
 *
 * It deliberately does NOT compare node for node against the reference. Your
 * build should differ. What it checks is that the five capabilities are
 * present and wired, because those are what the drill is for.
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const REFERENCE = join(ROOT, 'n8n', 'inbound-lead-qualifier.json');

const target = process.argv[2] ?? REFERENCE;
const isReference = target === REFERENCE;

let wf;
try {
  wf = JSON.parse(readFileSync(target, 'utf8'));
} catch (err) {
  console.error(`\ncould not read a workflow from:\n  ${target}\n\n${err.message}\n`);
  console.error('In n8n: Workflow menu > Download. Then pass that file path.\n');
  process.exit(2);
}

const nodes = wf.nodes ?? [];
const connections = wf.connections ?? {};
const typeOf = (n) => (n.type ?? '').replace('n8n-nodes-base.', '');
const has = (...kinds) => nodes.filter((n) => kinds.includes(typeOf(n)));

const TRIGGERS = ['webhook', 'scheduleTrigger', 'manualTrigger', 'cron', 'formTrigger', 'executeWorkflowTrigger'];
const BRANCHES = ['if', 'switch', 'filter'];

/** Nodes reachable by following connections out of every trigger. */
function reachable() {
  const seen = new Set();
  const queue = has(...TRIGGERS).map((n) => n.name);
  while (queue.length) {
    const name = queue.shift();
    if (seen.has(name)) continue;
    seen.add(name);
    for (const output of connections[name]?.main ?? []) {
      for (const link of output ?? []) {
        if (link?.node && !seen.has(link.node)) queue.push(link.node);
      }
    }
  }
  return seen;
}

/** Does any branch node have two or more of its outputs wired? */
function branchIsForked() {
  for (const node of has(...BRANCHES)) {
    const outputs = connections[node.name]?.main ?? [];
    const wired = outputs.filter((o) => (o ?? []).length > 0);
    if (wired.length >= 2) return node.name;
  }
  return null;
}

/** Anything that looks like a pasted secret rather than a credential ref. */
function hardcodedSecrets() {
  const found = [];
  const suspicious = /(api[_-]?key|secret|token|password|bearer)/i;
  const looksReal = /^[A-Za-z0-9_\-]{20,}$/;
  const safe = (v) => typeof v !== 'string'
    || v.includes('{{') || v.includes('$credentials');

  // Two shapes to cover. Plain `"apiKey": "sk_live_..."`, and n8n's own
  // name/value pair shape used by header and query parameters, where the
  // suspicious word sits in `name` and the secret sits in its sibling `value`.
  const walk = (obj, nodeName) => {
    if (Array.isArray(obj)) {
      for (const item of obj) walk(item, nodeName);
      return;
    }
    if (!obj || typeof obj !== 'object') return;

    if (suspicious.test(String(obj.name ?? '')) && !safe(obj.value)
        && looksReal.test(obj.value)) {
      found.push(`${nodeName}: ${obj.name}`);
    }
    for (const [key, value] of Object.entries(obj)) {
      if (suspicious.test(key) && !safe(value) && looksReal.test(value)) {
        found.push(`${nodeName}: ${key}`);
      }
      walk(value, nodeName);
    }
  };

  for (const node of nodes) walk(node.parameters ?? {}, node.name);
  return [...new Set(found)];
}

const forked = branchIsForked();
const live = reachable();
const orphans = nodes.filter((n) => !live.has(n.name) && !TRIGGERS.includes(typeOf(n)));
const secrets = hardcodedSecrets();

const checks = [
  {
    label: 'Has a trigger node',
    pass: has(...TRIGGERS).length >= 1,
    hint: 'Add a Webhook node. A workflow with no trigger cannot be activated.',
  },
  {
    label: 'Has an HTTP Request node',
    pass: has('httpRequest').length >= 1,
    hint: 'This is the node the plan says to spend disproportionate time on.',
  },
  {
    label: 'Has a branching node (IF / Switch)',
    pass: has(...BRANCHES).length >= 1,
    hint: 'Add an IF node testing whether enrichment returned a company name.',
  },
  {
    label: 'Branch has BOTH outputs wired (the error branch)',
    pass: Boolean(forked),
    hint: 'This is the whole point of the drill. Wire the false output to a '
        + 'recovery path, not nowhere. A failure must not silently stop the run.',
  },
  {
    label: 'Has a Code node',
    pass: has('code').length >= 1,
    hint: 'Add a Code node that scores the lead and returns a route.',
  },
  {
    label: 'At least 5 nodes',
    pass: nodes.length >= 5,
    hint: `Found ${nodes.length}. The drill is a five node workflow.`,
  },
  {
    label: 'No orphaned nodes',
    pass: orphans.length === 0,
    hint: `Unreachable from a trigger: ${orphans.map((n) => n.name).join(', ')}`,
  },
  {
    label: 'No hardcoded secrets in node parameters',
    pass: secrets.length === 0,
    hint: `Looks pasted rather than referenced: ${secrets.join(', ')}. `
        + 'Use a credential; exported JSON does not carry credentials, but it '
        + 'does carry anything typed into a parameter.',
  },
];

const width = Math.max(...checks.map((c) => c.label.length));
console.log('\n' + '='.repeat(width + 12));
console.log(`  WEEK 1 DONE-WHEN CHECK`);
console.log(`  ${isReference ? 'grading the REFERENCE build (self test)' : 'grading: ' + target}`);
console.log('='.repeat(width + 12));

let failed = 0;
for (const c of checks) {
  console.log(`  ${c.pass ? 'PASS' : 'FAIL'}  ${c.label.padEnd(width)}`);
  if (!c.pass) {
    failed++;
    console.log(`        -> ${c.hint}`);
  }
}

console.log('-'.repeat(width + 12));
console.log(`  nodes: ${nodes.length}   types: ${[...new Set(nodes.map(typeOf))].join(', ')}`);
if (forked) console.log(`  forked at: ${forked}`);

if (failed === 0) {
  console.log(`\n  ALL ${checks.length} CHECKS PASS.`);
  if (isReference) {
    console.log('  That was the reference. Now build your own and pass this again.');
  } else {
    console.log('  Week 1 "done when" is met. Tick it.');
  }
} else {
  console.log(`\n  ${failed} of ${checks.length} checks failed. Fix and re-run.`);
}
console.log('='.repeat(width + 12) + '\n');

process.exit(failed === 0 ? 0 : 1);
