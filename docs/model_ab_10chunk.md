# 10-Chunk Model Comparison — 4-Way A/B (GitHub Report Section)

Date: 2026-09-14. Chunks 0–9 of `sources/thesis_en.txt` (Abstract through
early Results), identical extraction pipeline and audit (non-interactive,
42-patient dataset, strict ±0.01 tolerance) for every model. Models via
9router. Artifacts: `extracted_*10.json`, `audit_*10.json` per model.

## Headline table

| Model | Time (s) | Stats | MATCH | Restate | NO MATCH | COLLISION | UNCHECKABLE |
|---|---|---|---|---|---|---|---|
| GLM-4.7-flash (reasoning) | 461 | 26 | 6 | —* | 14 | 3 | 3 |
| nemotron-3-super | 177 | 25 | 4 | —* | 10 | 0 | 11 |
| gemma4:31b | **23** | 23 | 6 | —* | 10 | 3 | 4 |
| ling-3.0-flash | 63 | **31** | 6 | —* | 14 | 3 | 8 |

\* the used-pool restatement feature was added after the 10-chunk runs;
these audits predate it. The full-paper reruns (ling: 70 verified, gemma:
80 verified) include restatement classification.

## Yield

- Highest: ling (31) — but ~6 of its finds are AASM scoring *definitions*
  (apnea ≥90%/10 s, hypopnea ≥30%/3%) — methodology constants, not cohort
  statistics. Defensible as thoroughness; irrelevant for verification.
- GLM (26), nemotron (25), gemma (23) cluster tightly.
- All four: **zero discard noise** — every proposed statistic passed the
  validation gauntlet (snippet-digit presence, kind allowlist, dedupe).

## Speed

gemma4:31b is 20× faster than GLM and 7.7× faster than nemotron on this
section. GLM's cost is reasoning-token overhead; nemotron's MoE routing
adds latency vs gemma's dense hosted inference.

## Consensus analysis

**9 statistics were found identically by all four models** — the Methods
core: cohort demographics (age 20/65), all six exclusion-criteria counts,
and the headline p=0.001. Pairwise overlaps 10–15 of ~23–31: models agree
on the core but differ in periphery, driven by *labeling*, not numbers
(e.g. nemotron's "male patients/32" vs GLM's "males/count 32").

Notable per-model behavior:
- **GLM error caught by the referee:** extracted the age range as ONE
  statistic ('20–65', kind=min) — a malformed entry the strict ±0.01
  audit correctly refused. Nemotron and gemma both split it into proper
  min=20/max=65 entries.
- **nemotron:** most conservative `kind` labeling (7 'other'), zero
  collisions, fewest matches — honest but under-reaching.
- **gemma:** uniquely caught the ODI correlation pair (r=0.570 AND
  p=0.0001) from the Abstract.
- **ling:** uniquely captured AASM scoring definitions (over-extraction).

## Audit quality notes

- The 6 GLM/ling/gemma matches include 3 semantically-wrong count
  coincidences (13 → apnea_feeling=1, 4 → snoring=1, 2 → mallampati=2):
  numerically correct, semantically accidental. nemotron's 4 matches are
  the same set minus coincidences. The interactive path exists to let the
  user adjudicate these; semantic matching is a future upgrade.
- NO MATCHes at 10 chunks are dominated by age min/max (paper says 20,
  data says 19.9 — correctly refused at ±0.01) and exclusion counts
  (unverifiable against included-only data — correct behavior).

## Conclusion of the 10-chunk A/B

gemma4:31b: best speed with full correctness; ling: highest raw yield;
nemotron: most conservative; GLM: dominated (slowest, only extraction
error). gemma advanced to the full-paper run, where it confirmed the
result (233 stats in 4.3 min, 80 verified, no coverage gaps) — see
`ling_full_paper_run.md` and `docs/` for the full-paper comparison.
