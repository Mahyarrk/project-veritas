# project-veritas

A statistics auditor for published research papers. Give it a paper and the raw
data behind it: it extracts every published statistic, recomputes each one from
the data, and shows the exact calculation behind every match or mismatch.

Built and validated on a real case: it audited my MD thesis on REM vs. NREM sleep
apnea and flagged both known value errors in it (errors independently confirmed by
a separate hand analysis of the raw data).

**AI disclosure:** Built with AI assistance for code drafting. Every feature,
threshold, and test protocol was chosen by me, and the validation case (my own
thesis, with independently known errors) was my design. AI accelerated the work;
it did not make the scientific decisions.

## Why

Published statistics are rarely re-checked. A transposed digit, an SD copied into
the wrong cell, a mean that is not the mean of the cohort: errors like these
survive into the literature because verification is tedious. project-veritas makes
it mechanical. The auditor never takes the paper's word; it recomputes from raw
data and shows the exact expression that produced each verdict.

## How it works

```
paper (txt/docx) ──┐
                   ├──► extract (LLM) ──► verify (battery) ──► report
raw data (csv/xlsx)┘         │                   │
                  every statistic        each statistic recomputed
                  with its snippet,      from the data (pandas):
                  validated & deduped    verdict + provenance
```

1. **Extract.** An LLM (whatever your endpoint serves; configured in
   `config.yaml`) reads the paper in chunks and proposes every statistic: value,
   kind (mean/std/count/p/r/other), table, and the exact snippet containing it. A
   deterministic gauntlet then checks each proposal: the snippet must really occur
   in the paper, the digits must appear in that snippet, kinds must come from a
   fixed vocabulary, duplicates are dropped. The LLM proposes; it never verifies.
2. **Verify.** No LLM in this step. A battery recomputes what the data can
   produce: every mean, SD, min, max, median, and count, overall and per
   subgroup. Each published statistic is compared against the battery at a strict
   ±0.01 absolute tolerance (justified by a tolerance sweep, below), with
   semantic de-duplication so each match stays unique.
3. **Verdicts with provenance.** Every result carries the exact pandas expression
   that produced the number:

   > MATCH: Total Sleep Time (Male) = 362.04 →
   > `df.loc[df['sex']==1, 'tst_min'].mean()` = 362.0437

   A published value that ties between an overall statistic and one or more
   subgroup statistics resolves to the overall one and is labelled
   `MATCH (tie-break: overall)` — the inference is visible in the report, not
   folded into a plain MATCH. Remaining ties go to the user as a dropdown
   listing every candidate. Nothing is silently guessed.

## The validation case

I validated the tool against my own thesis, whose errors are independently known
from a prior hand analysis:

| Known error | Published | True value | Tool verdict |
|---|---|---|---|
| REM-AHI total mean | 59.92 | 54.92 | NO MATCH (all 3 occurrences) |
| REM-AHI female SD | 31.04 | 41.04 | NO MATCH |

2/2 known errors detected, zero false verifications, at every tolerance tested
(±0.001 to ±0.05). A third, test-selection error (a one-sample t-test presented
in a REM-vs-NREM context) is documented but out of scope: value verification
checks numbers, not test choices.

The tolerance was chosen empirically. A sweep from ±0.001 to ±0.05 shows matches
peaking at ±0.01 (109 verified verdicts of 233 extracted, including restatements
and tie-breaks), with zero false matches of the known errors at every tolerance.
Wider tolerances lose matches rather than gain them: more published values fall
inside the window, so more of them become ties and are withheld as collisions
instead of being credited.

![tolerance sweep](docs/validation_tolerance_sweep.png)

**Run summary (233 statistics extracted from the 23k-character thesis):**

| Verdict | n | Meaning |
|---|---|---|
| MATCH | 77 | Recomputed from data, expression shown |
| MATCH (tie-break: overall) | 7 | Tied with a subgroup coincidence; overall statistic won |
| MATCH (restatement) | 25 | The paper restates a verified number in prose |
| NO MATCH | 18 | No data-supported value (includes the 2 known errors) |
| COLLISION | 18 | Genuine ties, resolved by the user via dropdown |
| UNCHECKABLE | 88 | p-values, r coefficients, subgroup stats (battery roadmap) |

Subgroup standard deviations are computed and matched. An earlier version
silently never built that part of the battery — a dead guard meant only subgroup
*means* and *counts* existed, so 43 standard deviations were reported as NO MATCH
when the data did contain them. Fixing it moved MATCH from 59 to 77 and NO MATCH
from 56 to 18, while both known thesis errors stayed flagged: recall recovered,
nothing unmasked.

Full methodology and per-model results: [`docs/`](docs/).

## Model choice

Four extraction models were compared on an identical protocol
([10-chunk A/B](docs/model_ab_10chunk.md), then full-paper runs).

**10-chunk A/B** (chunks 0–9, identical pipeline and audit):

| Model | Time (s) | Stats | MATCH | NO MATCH | COLLISION | UNCHECKABLE |
|---|---|---|---|---|---|---|
| GLM-4.7-flash (reasoning) | 461 | 26 | 6 | 14 | 3 | 3 |
| nemotron-3-super | 177 | 25 | 4 | 10 | 0 | 11 |
| gemma4:31b | 23 | 23 | 6 | 10 | 3 | 4 |
| ling-3.0-flash | 63 | 31 | 6 | 14 | 3 | 8 |

gemma-4-31b was 20× faster than GLM and 7.7× faster than nemotron; all four
passed every proposed statistic through the validation gauntlet. The ±0.01 audit
even caught GLM emitting an age range ("20–65") as one malformed statistic, which
nemotron and gemma both split correctly.

**Full-paper runs** (all 36 chunks):

| Model | Full-paper extraction | Verified matches |
|---|---|---|
| GLM-4.7-flash (reasoning) | did not finish (stalled at 7 h) | — |
| nemotron-3-super | — | — |
| ling-3.0-flash | 6.5 min, 234 stats | 70 |
| **gemma-4-31b** | **4.3 min, 233 stats, no gaps** | **80** |

Those runs predate the subgroup-SD fix, so their MATCH counts are on the old
battery. The banner run above (233 statistics) is the current one, extracted with
the model configured in `config.yaml`, `cx/gpt-6-luna` via a router.

Nine statistics were found identically by all four models. The verdicts are
model-independent: the verifier is deterministic, the extractor only proposes.

## Features

- **Human-in-the-loop audit.** Every published value the battery cannot assign to
  exactly one statistic becomes a dropdown of the actual candidates, in the same
  interface that runs the audit. Known-safe skips are operator decisions.
- **No silent failures.** Unknown LLM output layouts are reported, never guessed.
  A truncated chunk is flagged in the output as `extraction_incomplete`. The
  endpoint is live-verified before anything runs.
- **Bring your own endpoint.** Any OpenAI-compatible provider works (local
  LM Studio, Ollama, hosted routers). Keys stay in `~/.veritas/`, never in the
  repo.

## Quick start

```bash
git clone https://github.com/Mahyarrk/project-veritas
cd project-veritas
uv sync                      # Python 3.12+, deps from pyproject.toml
uv run streamlit run gui.py  # endpoint setup → upload paper + data → verdicts
```

Reproduce the validation case (headless, no GUI needed):

```bash
uv run validate_case.py
uv run validate_case.py --fresh   # re-extract first (needs the endpoint + key)
```

Configure the extraction model and the audit tolerance in `config.yaml`; the API
key is supplied by `VERITAS_EXTRACT_KEY`, never stored in the repo.

## Limitations

1. **One validation case.** The tool was built and tuned on a single paper, my
   own thesis. It has not yet run blind on third-party papers; that is the next
   step.
2. **Value errors only.** The auditor checks whether published numbers match the
   data. It cannot detect wrong test choices, wrong denominators beyond the
   data's cohorts, or selective reporting.
3. **Recall is unmeasured.** The verdicts are precise — every MATCH shows the
   expression that produced it — but there is no gold-standard set of every
   statistic in the thesis, so how much of each paper gets checked is unknown.
   88 of 233 extracted values remain uncheckable by construction (p-values,
   correlations). Coverage is a lower bound, not a measurement.
4. **The data must be the included cohort.** If the paper excluded participants,
   the file must contain only the included cases. Auditing unfiltered data
   produces false mismatches; I observed exactly that before fixing the input.
5. **Statistical scope.** p-values, correlation coefficients, and subset
   statistics beyond categorical splits (e.g. a BMI≥30 subgroup) are extracted
   but not yet recomputable. The battery covers means, SDs, min/max, medians and
   counts, overall and per categorical split.
6. **Extraction is LLM-dependent.** The gauntlet catches malformed proposals (one
   model emitted "20–65" as a single minimum; rejected), but no extraction is
   exhaustive. A token-limited chunk is flagged, never silently dropped.
7. **Nobody can rerun the validation from the repo alone.** The dataset and the
   thesis text are excluded from version control (they contain clinical data), so
   `validate_case.py` reproduces the reported numbers only on a machine that has
   them.

## Author

**Dr. Mahyar Mirzazadeh, MD** — [LinkedIn](https://www.linkedin.com/in/mahyar-mirzazadeh-550b3b166)

## Repository layout

```
core.py           config, endpoint client, paper + dataset loading (one each)
extract.py        LLM statistic extraction + validation gauntlet
verify.py         the auditor: battery, matching, tie-break, verdicts
gui.py            Streamlit UI — the only interface (this is the whole product)
validate_case.py  validation against known thesis errors + tolerance sweep
config.yaml       model settings, tolerance, paths
docs/             model benchmarks, validation protocols, technical reports
runs/             committed run artifacts: extraction outputs, audit reports
scripts/          utility scripts (validation chart generator)
sources/          validation inputs (not committed: clinical data)
```

`verify.py` contains no model calls. That is the design, not an accident: the
extractor proposes claims, the auditor recomputes them from raw data and shows
the expression behind every verdict.
