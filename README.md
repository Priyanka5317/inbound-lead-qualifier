# Inbound Lead Qualifier

An agentic inbound lead qualifier that reads a new enquiry, decides whether it is worth a salesperson's time, and refuses to guess when the evidence is thin.

Repo: https://github.com/Priyanka5317/inbound-lead-qualifier

Runs on Python 3.11 with **no dependencies and no API key**. Clone it and the evaluation below reproduces.

Three things run against the real world rather than fixtures: **live enrichment** across four keyless sources, **a real Claude call** behind the same validation gate, and **the n8n workflow actually executing**. Each is opt-in; the offline path stays dependency-free so the golden number is reproducible.

---

## Start here

```bash
python src/report.py --open
```

Runs the whole system and writes **`report.html`**, one self-contained file with
no dependencies and no network. Every number in it is computed from a live run,
so the report cannot drift from the code. Light and dark, hover values, table
views, and a palette validated for colour-blind separation rather than eyeballed.

## Does it work?

Two rubric versions, measured on the set that found the defects **and** on a set
the fixes were never tuned against.

```
                 rubric v1        rubric v2
golden set       18/20  (90%)     20/20  (100%)     2 fixed, 0 regressed
held out          5/10  (50%)      9/10  ( 90%)     4 fixed, 0 regressed, 1 still wrong
```

```bash
python src/compare.py
```

**Read the held-out number honestly.** It is a stress set, not a random sample:
4 of its 10 leads were built to exercise the two defect classes, 5 are neutral
controls, and 1 is a case neither version gets right. It shows the fixes
generalise past the two leads that found them. It does **not** estimate a
production error rate, and a set weighted toward what you just fixed will
flatter you.

### Attacking the guardrail

```bash
python src/redteam.py
```

15 hand-written attacks designed to slip an unsupported claim past the gate.

```
caught                    :  9/12
false positives           :  0
got through               :  3/12
```

All three misses are the same class, and the class is the interesting part.
Each cites a real field whose value genuinely appears in the sentence and
carries no digit the record lacks, so the gate has no basis to reject it:

> "Since you already run Snowflake, your data team is clearly mature."

The gate checks whether a field is real and present, and whether every number
is in the record. It does **not** check whether the sentence asserts more than
the field supports. "You run Snowflake" is supported; "your data team is clearly
mature" is an inference bolted onto it. That is a real limit of citation-style
grounding in general rather than a bug here, and closing it needs an entailment
check, which is a model-graded eval and not a regex.

The three buckets are counted separately because they do not cost the same. A confident mistake gets acted on by a salesperson. An abstain gets looked at by one. Only the third bucket is a production incident, so collapsing all of it into a single "accuracy" figure would hide the thing that matters.

### Validation gate on the drafted message

Every sentence in an outbound draft must name the enrichment field it rests on, and the gate re-checks that claim independently of whoever wrote the sentence.

```
drafter 'grounded'  (template, can only interpolate real fields)
  claims rejected        :  0/59
  drafts withheld        :  0/15

drafter 'llm_sim'   (stands in for a fluent model that embellishes)
  claims rejected        : 30/89  (33.7%)
  drafts withheld        : 15/15
  caught: 15 unsupported numbers, 15 claims with no supporting field
```

The second drafter ships on purpose. **A gate that has never rejected anything is not evidence that it works.** Here is the gate refusing a real draft:

```
REJECTED: Congratulations on the Series B round, raising $40M is no small thing.
          -> unsupported number(s) ['40'] not present in the record
REJECTED: Your competitors are already automating this.
          -> no supporting field declared

DRAFT   : withheld, gate failed
```

The funding stage was real. The amount was invented. That is the failure mode worth catching, because it is the one a human reader cannot detect without the source record open beside them.

### Rubric parity

The scoring rubric exists twice, as `rubric.json` for Python and as JavaScript inside the n8n Code node. That duplication is a real cost, so it is tested rather than trusted:

```bash
node tests/parity_check.mjs
# PARITY OK: n8n and Python agree on all 20 golden leads.
```

---

## Running against the real world

The three sections above are measured on fixtures on purpose: a golden set has to be deterministic. These three paths are not.

### Live enrichment, four sources, no API key

```bash
python src/providers.py stripe.com
python src/pipeline.py --provider live
```

**Waterfall enrichment**: ask source A, and if a field comes back empty ask source B, then C. Coverage rises because no single source has everything.

| Source | Gives |
|---|---|
| company homepage | JSON-LD Organization, og tags, name |
| HTML tech signals | which vendor scripts the page actually loads |
| Wikidata | employee count, industry, country |
| DNS over HTTPS (MX) | mail platform |

None needs a key or an account. Every resolved field records **which source answered**, in `field_sources`, because otherwise you cannot tell a confident value from a last-resort guess. `confidence` is **derived from coverage**, not asserted: an enrichment that resolved two fields of seven does not get to report the same confidence as one that resolved six.

Real output for `stripe.com`: headcount 8,000 and "financial services" from Wikidata, name from the homepage, Google Workspace from the MX records, `confidence 0.57`, and `hq_country`, `funding_stage`, `annual_revenue` honestly reported unresolved.

**The finding that matters, and it is not flattering.** On five real companies the system abstained on **four**. Against the fixtures it abstained on 2 of 20.

Live data is far sparser than any mock, so **the fixture made the system look considerably more decisive than it is.** That gap is the single most useful thing the live provider surfaced, and it is an argument for the abstain path rather than against it: on real enrichment, "I do not know" is the honest answer most of the time, and a system without that option would have been confidently wrong four times.

**The documented `L-005` taxonomy defect also reproduced independently on real data.** Stripe scored fit 0/60 because Wikidata calls it "financial services" and the rubric matches the literal keyword `fintech`. Same defect, different dataset, found without looking for it.

### A real Claude call, behind the same gate

```bash
pip install anthropic          # the only part of the project that needs a dependency
python src/llm_drafter.py --check
python src/pipeline.py --lead L-001 --drafter claude
```

Previously the gate was only tested against a simulator written to misbehave. That proves the gate's logic, not that it catches a real model.

`src/llm_drafter.py` calls **`claude-opus-5`** through the official SDK with adaptive thinking, and asks for a **structured output**: a list of claims where each one must name the enrichment field it rests on. Not prose. That means the model's output is checked by **exactly the same `validate()` the simulator went through**, with nothing relaxed for it. Server-side refusal fallbacks are enabled, and `stop_reason` is checked before the body is read.

Asking for claims rather than paragraphs is the design decision worth defending: it forces the model to commit to a source per sentence *before* anything is sent, which turns a fluency problem into a checkable one.

**Honest limit:** this machine has no Anthropic credentials, so the live call has not been executed here. Verified instead: the SDK installs, the client constructs, and every parameter used (`thinking`, `output_config`, `betas`, `fallbacks`) is a real named parameter on the installed SDK v1.5.0. Set a key and `--drafter claude` runs.

### The workflow actually executing

```bash
node tests/run_workflow.mjs                     # TRUE branch
node tests/run_workflow.mjs --workflow-url      # FALSE branch, real 422
```

The workflow JSON used to be only *imported*. That proves the file is well formed and nothing else. `tests/run_workflow.mjs` is a small n8n-compatible executor: it walks the graph, resolves `={{ }}` expressions, makes the real HTTP call, evaluates the IF, and runs both Code nodes' JavaScript in Node.

Both paths, executed:

```
TRUE   webhook -> HTTP 200 (company_name=Stripe) -> IF TRUE  -> Code: Score & Route
FALSE  webhook -> HTTP 422 (no credential)       -> IF FALSE -> Code: Needs Human
```

The FALSE run is the better demonstration. Pointed at the Apollo URL that is genuinely in the workflow file, with no credential configured, it returns 422 and **the error branch catches it and routes to `needs_human` instead of the run dying.** That is the difference between a workflow with an error branch and one without, shown rather than asserted.

It is not n8n. It supports the five node types this workflow uses. What it establishes is that the graph runs, both branches fire, and the Code node's scoring agrees with the Python implementation.

---

## What it does

Takes a raw inbound lead, resolves the company behind the email domain, scores fit and intent against a written rubric, routes to a call, a nurture sequence, a disqualification or a human, then drafts a first touch message that cannot assert anything absent from the enrichment record.

Every decision is written to `out/decisions-*.jsonl` with the inputs that produced it, so a routing call can be reconstructed later rather than argued about.

---

## How it decides

The rubric is `rubric.json`. It was written before any prompt existed, and the scorer is only an interpreter for it. If the rubric lived inside a prompt you could not diff it, test it, or explain a score to a salesperson who disagrees with it.

| Section | Criterion | Points |
|---|---|---|
| Fit | Employee count 100 to 2000 | 20 |
| Fit | Industry in [healthcare, fintech, SaaS] | 20 |
| Fit | Tech stack includes a cloud warehouse | 10 |
| Fit | Based in a country we sell to | 10 |
| Intent | Requested a demo | 25 |
| Intent | Visited pricing page 2+ times | 10 |
| Intent | Opened 3+ emails | 5 |

| Score | Route | Destination |
|---|---|---|
| 70 and above | book a call | `sales_queue` |
| 40 to 69 | nurture | `nurture_sequence` |
| below 40 | disqualify | `closed_lost` |
| any blocking field unresolved | **abstain** | `needs_human` |

---

## What it refuses to do

**Missing data is not zero.** A lead whose employee count was never resolved is not a small company, and scoring it as one silently disqualifies good leads whose data simply was not found.

So any unresolved field worth 20 or more points stops scoring entirely and routes to a human with the reason attached. Two of the twenty golden leads hit this path:

- `L-006` employee count unresolved by the provider, confidence 0.52
- `L-011` the demo field was never submitted on the form, so intent is unknown rather than absent

Both were labelled abstain by hand too. The enrichment record also carries `confidence` and `unresolved_fields` on every lookup, which is the difference between a script that returns what it found and a system that also returns what it missed.

---

## Failure analysis

Two leads were wrong with confidence, and they are two different defects rather than one noisy threshold.

**`L-005` Fieldstone Clinics. Human said book a call, system said nurture at 65.**

The provider recorded the industry as `Medical devices`. The rubric matches on the literal keywords `healthcare`, `fintech`, `saas`, so a 220 person clinical operations company with a warehouse and a demo request lost the entire 20 point industry block on a taxonomy mismatch. The lead was fine. The category label was not.

*The fix is not a lower threshold.* It is that a keyword list is the wrong instrument for an industry taxonomy. Next iteration maps provider industry strings onto the ICP through a synonym table, and anything unmapped abstains instead of scoring zero, which is the same argument as the missing data rule applied one level up.

**`L-019` Grandview Hospital. Human said nurture, system said book a call at 70.**

A 2600 person hospital with no warehouse cleared the booking threshold on **intent alone**, scoring the full 40 for a demo request plus pricing visits plus email opens, and landing exactly on 70. It is above the size band and has no warehouse, so an implementation would stall. The rubric let strong intent buy its way past weak fit.

*The fix is a floor, not a reweighting.* Fit and intent are not substitutes, so the next iteration requires a minimum fit score before any intent points can promote a lead to a call. Note that this lead sat precisely on the boundary, which is worth saying out loud rather than rounding away.

Both defects were found by twenty rows of hand labelling that took under an hour, and neither would have surfaced from reading the rubric.

---

## Drift monitoring

```bash
python src/drift.py
```

Tracks the score distribution against a baseline. **If average scores rise while the lead source is unchanged, the rubric has drifted, not the market.**

It also refuses to cry wolf: under 30 scored leads it says explicitly that a mean shift is not yet distinguishable from sampling noise, rather than printing an alert that means nothing.

---

## Architecture

```
rubric.json                  v1, the rubric as data, authored before any prompt
rubric-v2.json               v2, every change tied to the lead that found it
src/enrich.py                step 2, original single-source adapter (kept as the
                             smallest readable example; the pipeline now uses providers.py)
src/qualify.py               step 3  rubric interpreter, abstain rule
src/route.py                 step 4  four destinations, needs_human is first class
src/draft.py                 step 5  drafters + the validation gate
src/pipeline.py              steps 1 and 6, orchestration and the decision log
src/evaluate.py              the golden set number
src/drift.py                 score distribution over time
src/compare.py               rubric A/B across golden and held-out sets
src/redteam.py               adversarial attacks on the validation gate
src/report.py                generates the self-contained HTML report
src/providers.py             step 2 as used, offline + live four-source waterfall
src/llm_drafter.py           the real claude-opus-5 call, structured output
tests/run_workflow.mjs       executes the exported n8n graph, both branches
n8n/inbound-lead-qualifier.json   the 5 node workflow, importable
tests/parity_check.mjs       proves the two rubric copies agree
tests/validate_my_workflow.mjs    grades a hand built n8n workflow, pass or fail
demo.py                      one command guided walkthrough for recording
data/golden_set.csv          20 leads, hand labelled before the first run
data/enrichment_source.json  offline stand in for Apollo or Clay
```

Enrichment goes through one adapter function, so pointing it at Apollo or Clay is a single change and nothing downstream moves.

## Run it

```bash
python src/enrich.py ops@northwind-health.com   # one enriched record
python src/pipeline.py                          # sample leads
python src/pipeline.py --golden                 # the 20 lead set
python src/pipeline.py --lead L-001 --drafter llm_sim   # watch the gate reject
python src/evaluate.py                          # the accuracy number
python src/compare.py                           # rubric v1 vs v2, both sets
python src/redteam.py                           # attack the gate
python src/report.py --open                     # the HTML report
python src/drift.py                             # distribution check
node tests/parity_check.mjs                     # rubric parity
python demo.py                                  # guided 90 second walkthrough
node tests/validate_my_workflow.mjs <your.json> # grade your own n8n build
python src/providers.py stripe.com              # live enrichment, no key
python src/pipeline.py --provider live          # whole pipeline, real data
python src/llm_drafter.py --check               # Claude credentials check
node tests/run_workflow.mjs                     # execute the n8n graph
```

## Scope

Two weekends, held. Explicitly out of scope: any interface beyond a table, multi channel sequencing, real CRM write back, and anything requiring a paid tier.
