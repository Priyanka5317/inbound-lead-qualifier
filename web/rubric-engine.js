/**
 * The rubric scorer and the validation gate, in JavaScript.
 *
 * This is the THIRD implementation of the same rules. Python runs the
 * evaluation, the n8n Code node runs them in a workflow, and this runs them
 * in a browser with no server. Three copies is a real maintenance cost and
 * the only honest way to hold it is a test: `tests/parity_check.mjs` scores
 * all twenty golden leads through every implementation and fails if any two
 * disagree.
 *
 * Loaded as an ES module by web/index.html and imported by the parity test,
 * so the browser demo and the test are provably running the same code.
 */

const MISSING = (v) => v === null || v === undefined
  || (typeof v === 'string' && v.trim() === '');

/** Three-state industry match. True in ICP, false outside it, null unknown. */
export function taxonomy(criterion, value) {
  if (typeof value !== 'string') return null;
  const text = value.toLowerCase().trim();

  for (const c of criterion.canonical) if (text.includes(c)) return true;
  for (const [phrase, canonical] of Object.entries(criterion.synonyms || {})) {
    if (text.includes(phrase)) return criterion.canonical.includes(canonical);
  }
  for (const out of criterion.known_outside_icp || []) {
    if (text.includes(out)) return false;
  }
  return criterion.unrecognised_is_missing ? null : false;
}

function evaluate(criterion, value) {
  switch (criterion.test) {
    case 'between':
      return typeof value === 'number'
        && value >= criterion.min && value <= criterion.max;
    case 'keyword_any':
      return typeof value === 'string'
        && criterion.keywords.some((k) => value.toLowerCase().includes(k));
    case 'list_contains_any': {
      if (!Array.isArray(value)) return false;
      const blob = value.join(' ').toLowerCase();
      return criterion.keywords.some((k) => blob.includes(k));
    }
    case 'in_set': return criterion.values.includes(value);
    case 'is_true': return value === true;
    case 'gte': return typeof value === 'number' && value >= criterion.value;
    case 'taxonomy': return taxonomy(criterion, value);
    default: throw new Error(`unknown test: ${criterion.test}`);
  }
}

/** Score facts against a rubric. Mirrors src/qualify.py exactly. */
export function qualify(facts, rubric) {
  const abstainAt = rubric.routing.abstain_rule.points;
  const breakdown = [];
  const blocking = [];
  let score = 0;

  for (const section of ['fit', 'intent']) {
    for (const criterion of rubric[section].criteria) {
      const value = facts[criterion.field];
      let missing = MISSING(value);
      let awarded = 0;

      // A taxonomy test can report "present but unrecognised", a third
      // outcome the two-state check could not express.
      if (!missing && criterion.test === 'taxonomy'
          && taxonomy(criterion, value) === null) missing = true;

      let verdict;
      if (missing) {
        if (criterion.points >= abstainAt) blocking.push(criterion.field);
        verdict = !MISSING(value) ? 'unrecognised' : 'missing';
      } else {
        const met = evaluate(criterion, value);
        awarded = met ? criterion.points : 0;
        verdict = met ? 'met' : 'not met';
      }
      score += awarded;
      breakdown.push({
        section, id: criterion.id, label: criterion.label,
        field: criterion.field, value, verdict,
        awarded, available: criterion.points,
      });
    }
  }

  const fitScore = breakdown.filter((b) => b.section === 'fit')
    .reduce((a, b) => a + b.awarded, 0);
  const intentScore = breakdown.filter((b) => b.section === 'intent')
    .reduce((a, b) => a + b.awarded, 0);

  const out = {
    score, fit_score: fitScore, intent_score: intentScore, breakdown,
    abstained: blocking.length > 0, missing_blocking_fields: blocking,
  };

  if (blocking.length) {
    out.reason = `insufficient evidence: ${blocking.join(', ')} unresolved `
      + `and each is worth ${abstainAt}+ points`;
  } else {
    const won = breakdown.filter((b) => b.awarded > 0).map((b) => b.label);
    const lost = breakdown.filter((b) => b.verdict === 'not met').map((b) => b.label);
    out.reason = `fit ${fitScore}/${rubric.fit.max}, intent ${intentScore}/`
      + `${rubric.intent.max}. `
      + (won.length ? `Met: ${won.join('; ')}. ` : '')
      + (lost.length ? `Missed: ${lost.join('; ')}.` : '');
    out.reason = out.reason.trim();
  }
  return out;
}

const DESTINATION = {
  'book a call': 'sales_queue',
  nurture: 'nurture_sequence',
  disqualify: 'closed_lost',
  abstain: 'needs_human',
};

/** Route a scored lead. Mirrors src/route.py, fit floor included. */
export function route(qualification, rubric) {
  if (qualification.abstained) {
    return {
      route: 'abstain', destination: DESTINATION.abstain,
      score: qualification.score, fit_score: qualification.fit_score,
      fit_floor_applied: false, explanation: qualification.reason,
    };
  }

  const t = rubric.routing;
  const floor = t.minimum_fit_to_book;
  const fitBlocked = floor !== undefined && qualification.fit_score < floor;

  let decision;
  if (qualification.score >= t.book_a_call_at_or_above && !fitBlocked) {
    decision = 'book a call';
  } else if (qualification.score >= t.book_a_call_at_or_above && fitBlocked) {
    decision = 'nurture';
  } else if (qualification.score >= t.nurture_at_or_above) {
    decision = 'nurture';
  } else {
    decision = 'disqualify';
  }

  let explanation = qualification.reason;
  if (fitBlocked && qualification.score >= t.book_a_call_at_or_above) {
    explanation = `scored ${qualification.score} but fit is only `
      + `${qualification.fit_score}, below the floor of ${floor}. Intent alone `
      + `does not promote to a call, so this drops to nurture. ${explanation}`;
  }

  return {
    route: decision, destination: DESTINATION[decision],
    score: qualification.score, fit_score: qualification.fit_score,
    fit_floor_applied: Boolean(fitBlocked), explanation,
  };
}

// --------------------------------------------------------- the gate

const GENERIC_NUMBERS = new Set(['1', '2', '3', '5', '10', '15', '20', '30']);

function recordNumbers(record) {
  const found = new Set();
  for (const v of Object.values(record)) {
    if (typeof v === 'boolean') continue;
    if (typeof v === 'number') found.add(String(Math.trunc(v)));
    else if (typeof v === 'string') (v.match(/\d+/g) || []).forEach((n) => found.add(n));
    else if (Array.isArray(v)) {
      v.forEach((i) => (String(i).match(/\d+/g) || []).forEach((n) => found.add(n)));
    }
  }
  return found;
}

function valueAppears(value, sentence) {
  const low = sentence.toLowerCase();
  if (typeof value === 'boolean') return true;
  if (typeof value === 'number') {
    return low.replace(/,/g, '').includes(String(Math.trunc(value)));
  }
  if (typeof value === 'string') {
    const head = value.toLowerCase().split(/\s+/)[0] || value.toLowerCase();
    return low.includes(head);
  }
  if (Array.isArray(value)) {
    return value.some((v) => low.includes(String(v).toLowerCase()));
  }
  return false;
}

/** Enforce the citation contract. Mirrors src/draft.py validate(). */
export function validate(claims, record) {
  const allowed = recordNumbers(record);
  const kept = [];
  const rejected = [];

  for (const claim of claims) {
    const { text } = claim;
    const field = claim.support;

    if (!field) {
      rejected.push({ text, why: 'no supporting field declared' });
      continue;
    }
    if (!(field in record)) {
      rejected.push({ text, why: `cites unknown field '${field}'` });
      continue;
    }
    if (record[field] === null || record[field] === undefined) {
      rejected.push({ text, why: `cites unresolved field '${field}'` });
      continue;
    }
    if (!valueAppears(record[field], text)) {
      rejected.push({
        text,
        why: `does not reflect the value of '${field}' (${JSON.stringify(record[field])})`,
      });
      continue;
    }
    const stray = (text.match(/\d[\d,]*/g) || [])
      .map((n) => n.replace(/,/g, ''))
      .filter((n) => !allowed.has(n) && !GENERIC_NUMBERS.has(n));
    if (stray.length) {
      rejected.push({
        text,
        why: `unsupported number(s) ${JSON.stringify(stray)} not present in the record`,
      });
      continue;
    }
    kept.push(claim);
  }

  return {
    kept,
    rejected,
    passed: rejected.length === 0,
    claims_total: claims.length,
    claims_rejected: rejected.length,
  };
}
