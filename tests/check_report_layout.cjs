/**
 * Verify the rendered report by MEASUREMENT, not by screenshot.
 *
 * The palette validator checks colour and says nothing about layout. This
 * covers the rest: overflow, label collisions, zero-size charts, and whether
 * dark mode actually renders rather than merely being declared.
 *
 *   node tests/check_report_layout.cjs
 */

const path = require('path');

// playwright is a dev-only convenience and may live in a parent project's
// node_modules rather than here. Resolve it without requiring NODE_PATH to
// be set by hand, and fail with an instruction rather than a stack trace.
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  const candidates = [
    path.join(__dirname, '..', '..', '..', '..', 'node_modules', 'playwright'),
    path.join(__dirname, '..', '..', '..', 'node_modules', 'playwright'),
  ];
  for (const c of candidates) {
    try { ({ chromium } = require(c)); break; } catch { /* keep looking */ }
  }
  if (!chromium) {
    console.error('');
    console.error('This layout check needs playwright:   npm i -D playwright');
    console.error('It is the only dev dependency in the project, and it checks the');
    console.error('rendered report rather than the source. Everything else runs bare.');
    console.error('');
    process.exit(2);
  }
}

const FILE = 'file:///' + path.join(__dirname, '..', 'report.html').split('\\').join('/');

(async () => {
  const browser = await chromium.launch();
  const failures = [];
  const notes = [];

  for (const theme of ['light', 'dark']) {
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
    await page.goto(FILE);
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    await page.waitForTimeout(120);

    const r = await page.evaluate(() => {
      const out = { overflow: null, charts: [], collisions: [], invisible: [], tiles: 0 };

      out.overflow = {
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
      };

      // Charts must have real area.
      for (const svg of document.querySelectorAll('svg')) {
        const b = svg.getBoundingClientRect();
        out.charts.push({
          w: Math.round(b.width), h: Math.round(b.height),
          label: svg.getAttribute('aria-label') || '(no aria-label)',
          marks: svg.querySelectorAll('rect').length,
        });
      }

      // Text inside each SVG must not overlap another text node.
      for (const svg of document.querySelectorAll('svg')) {
        const texts = [...svg.querySelectorAll('text')].map((t) => ({
          s: t.textContent.trim(), b: t.getBoundingClientRect(),
        })).filter((t) => t.s);
        for (let i = 0; i < texts.length; i++) {
          for (let j = i + 1; j < texts.length; j++) {
            const a = texts[i].b, c = texts[j].b;
            const ox = Math.min(a.right, c.right) - Math.max(a.left, c.left);
            const oy = Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top);
            if (ox > 1 && oy > 1) {
              out.collisions.push(`"${texts[i].s}" over "${texts[j].s}"`);
            }
          }
        }
      }

      // Anything with text but no rendered box is a broken element.
      for (const el of document.querySelectorAll('.big,.tile,.card,h1,h2,td,th')) {
        const b = el.getBoundingClientRect();
        if (el.textContent.trim() && (b.width < 1 || b.height < 1)) {
          out.invisible.push(el.className || el.tagName);
        }
      }

      out.tiles = document.querySelectorAll('.tile').length;
      out.bodyBg = getComputedStyle(document.body).backgroundColor;
      out.textColor = getComputedStyle(document.body).color;
      out.tables = document.querySelectorAll('table').length;
      out.legends = document.querySelectorAll('.legend').length;
      out.titles = document.querySelectorAll('svg title').length;
      return out;
    });

    const tag = `[${theme}]`;
    if (r.overflow.scrollW > r.overflow.clientW + 1) {
      failures.push(`${tag} horizontal overflow: ${r.overflow.scrollW} > ${r.overflow.clientW}`);
    }
    for (const c of r.charts) {
      if (c.w < 50 || c.h < 30) failures.push(`${tag} chart too small: ${c.label} ${c.w}x${c.h}`);
      if (c.marks === 0) failures.push(`${tag} chart has no marks: ${c.label}`);
    }
    if (r.collisions.length) {
      failures.push(`${tag} ${r.collisions.length} label collision(s): ${r.collisions.slice(0, 3).join('; ')}`);
    }
    if (r.invisible.length) {
      failures.push(`${tag} ${r.invisible.length} element(s) with text but no box`);
    }
    notes.push(`${tag} bg ${r.bodyBg} / text ${r.textColor}, ${r.charts.length} charts, ` +
               `${r.tiles} tiles, ${r.tables} tables, ${r.legends} legend(s), ` +
               `${r.titles} hover titles`);
    await page.close();
  }

  // Dark mode must actually differ from light, not just be declared.
  const [light, dark] = notes;
  if (light.split('bg ')[1] === dark.split('bg ')[1]) {
    failures.push('dark mode renders identically to light, so the toggle does nothing');
  }

  console.log('='.repeat(78));
  console.log('  REPORT LAYOUT CHECK (measured, no screenshots)');
  console.log('='.repeat(78));
  notes.forEach((n) => console.log('  ' + n));
  console.log('-'.repeat(78));
  if (failures.length) {
    failures.forEach((f) => console.log('  FAIL  ' + f));
  } else {
    console.log('  ALL CHECKS PASS: no overflow, no label collisions, charts have area,');
    console.log('  every element renders, and dark mode is genuinely distinct.');
  }
  console.log('='.repeat(78));

  await browser.close();
  process.exit(failures.length ? 1 : 0);
})();
