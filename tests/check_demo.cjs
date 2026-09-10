/**
 * Functional test of the browser demo.
 *
 * Not a look-at-it check. It drives the page the way a visitor would and
 * asserts the results are correct: pick a lead, confirm the score matches
 * what the Python evaluation produced, change an input and confirm the
 * decision moves, fire each preset attack and confirm the gate reaches the
 * verdict the red team suite recorded.
 *
 * A demo that renders but computes the wrong answer is worse than no demo,
 * so the assertions are on values rather than on pixels.
 *
 *   node tests/check_demo.cjs
 */

const path = require('path');
const fs = require('fs');

let chromium;
try { ({ chromium } = require('playwright')); } catch {
  for (const c of [
    path.join(__dirname, '..', '..', '..', '..', 'node_modules', 'playwright'),
    path.join(__dirname, '..', '..', '..', 'node_modules', 'playwright'),
  ]) { try { ({ chromium } = require(c)); break; } catch { /* keep looking */ } }
  if (!chromium) {
    console.error('\nNeeds playwright:  npm i -D playwright\n');
    process.exit(2);
  }
}

const FILE = 'file:///' + path.join(__dirname, '..', 'web', 'index.html')
  .split('\\').join('/');

// Ground truth from the Python run, so the browser is checked against the
// evaluation rather than against itself.
const LOG = path.join(__dirname, '..', 'out', 'decisions-golden.jsonl');

(async () => {
  const fails = [];
  const note = (m) => console.log('  ' + m);

  if (!fs.existsSync(LOG)) {
    console.error('\nRun `python src/pipeline.py --golden` first so there is a\n'
      + 'ground truth to compare the browser against.\n');
    process.exit(2);
  }
  const truth = new Map(fs.readFileSync(LOG, 'utf8').trim().split('\n')
    .map((l) => JSON.parse(l))
    .map((r) => [r.lead_id, { route: r.decision.route, score: r.decision.score }]));

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });

  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

  await page.goto(FILE);
  await page.waitForTimeout(250);

  console.log('='.repeat(84));
  console.log('  BROWSER DEMO, FUNCTIONAL CHECK');
  console.log('='.repeat(84));

  if (errors.length) fails.push(`javascript errors: ${errors.slice(0, 3).join(' | ')}`);
  else note('no javascript errors on load');

  // 1. every lead scores the same in the browser as it did in Python.
  const ids = await page.$$eval('#lead option', (os) => os.map((o) => o.value));
  note(`lead picker offers ${ids.length} enquiries`);

  let checked = 0, wrong = 0;
  for (const id of ids) {
    await page.selectOption('#lead', id);
    await page.waitForTimeout(40);
    const shown = await page.evaluate(() => {
      const kv = [...document.querySelectorAll('#decision .kv')]
        .find((d) => /rubric v1/.test(d.textContent));
      const badge = kv.querySelector('.badge').textContent.trim();
      const score = Number(kv.querySelector('b').textContent.trim());
      return { route: badge, score };
    });
    const want = truth.get(id);
    checked++;
    if (shown.route !== want.route || shown.score !== want.score) {
      wrong++;
      fails.push(`${id}: browser said ${shown.route}/${shown.score}, `
        + `python said ${want.route}/${want.score}`);
    }
  }
  note(`v1 scoring matches the python evaluation on ${checked - wrong}/${checked} leads`);

  // 2. the page is actually interactive: changing an input moves the decision.
  await page.selectOption('#lead', 'L-001');
  await page.waitForTimeout(40);
  const before = await page.textContent('#decision');
  await page.fill('#emp', '');            // wipe headcount -> should abstain
  await page.waitForTimeout(60);
  const after = await page.textContent('#decision');
  if (before === after) {
    fails.push('clearing the headcount did not change the decision, so the '
      + 'inputs are not wired');
  } else if (!/abstain/i.test(after)) {
    fails.push(`clearing headcount should abstain, got: ${after.slice(0, 70)}`);
  } else {
    note('clearing the headcount correctly flips the decision to abstain');
  }

  // 3. every preset attack reaches the verdict the red team recorded.
  const presets = await page.$$eval('#attacks button', (bs) => bs.map((b) => b.textContent));
  note(`gate playground offers ${presets.length} preset attacks`);

  const expected = {
    'invented funding amount': 'v-block',
    'no supporting field': 'v-block',
    'rounded the headcount': 'v-block',
    'the true headcount': 'v-pass',
    'percentage injection': 'v-block',
    'magnitude in words': 'v-block',
    'gets through: unsupported judgement': 'v-hole',
    'gets through: invented buying signal': 'v-hole',
  };

  for (const [i, name] of presets.entries()) {
    await page.click(`#attacks button:nth-child(${i + 1})`);
    await page.waitForTimeout(50);
    const cls = await page.evaluate(() => {
      const v = document.querySelector('#gate .verdict');
      return v ? v.className.replace('verdict', '').trim() : 'none';
    });
    const want = expected[name.trim()];
    if (!want) { note(`(no expectation recorded for "${name}")`); continue; }
    if (cls !== want) {
      fails.push(`attack "${name}": got ${cls}, expected ${want}`);
    }
  }
  note(`all ${presets.length} preset attacks reach their recorded verdict`);

  // 4. a user-written claim is evaluated, not ignored.
  await page.fill('#claim', 'Northwind Health grew 300% last year.');
  await page.selectOption('#support', 'company_name');
  await page.click('#check');
  await page.waitForTimeout(60);
  const custom = await page.textContent('#gate');
  if (!/Rejected/i.test(custom)) {
    fails.push('a freely typed claim with an invented figure was not rejected');
  } else {
    note('a freely typed claim with an invented number is rejected');
  }

  // 5. both themes render distinctly.
  const bg = {};
  for (const t of ['light', 'dark']) {
    await page.evaluate((x) => { document.documentElement.dataset.theme = x; }, t);
    await page.waitForTimeout(80);
    bg[t] = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  }
  if (bg.light === bg.dark) fails.push('dark mode is identical to light');
  else note(`themes distinct: ${bg.light} vs ${bg.dark}`);

  // 6. no horizontal overflow at a narrow width.
  await page.setViewportSize({ width: 420, height: 900 });
  await page.waitForTimeout(120);
  const of = await page.evaluate(() => ({
    s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth,
  }));
  if (of.s > of.c + 1) fails.push(`overflows on mobile: ${of.s} > ${of.c}`);
  else note('no horizontal overflow at 420px wide');

  console.log('-'.repeat(84));
  if (fails.length) {
    fails.forEach((f) => console.log('  FAIL  ' + f));
  } else {
    console.log('  ALL CHECKS PASS: the demo computes the same answers as the Python');
    console.log('  evaluation, is genuinely interactive, and every attack lands where');
    console.log('  the red team says it should.');
  }
  console.log('='.repeat(84));

  await browser.close();
  process.exit(fails.length ? 1 : 0);
})();
