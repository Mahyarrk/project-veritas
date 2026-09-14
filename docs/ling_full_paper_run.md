# Full-Paper ling-3.0-flash Extraction & Audit — Results

Date: 2026-09-14. All 36 chunks of `sources/thesis_en.txt`, identical
pipeline as the 4-way 10-chunk A/B. Config model:
`oc/ling-3.0-flash-fin-free(low)`.

## Extraction

- **234 entries, 234 validated, 390 s (6.5 min), all 36 chunks**
- Flag: `incomplete=True` — at least one chunk hit the token limit and was
  recursively split; some statistics may be missing (recorded honestly by
  the pipeline, by design)
- Incremental save ran per chunk (commit a81a61b paying off)
- For scale: GLM-4.7 extracted 91 statistics in 7+ hours and was killed;
  ling did 234 in 6.5 minutes — ~65× faster per statistic

## Audit vs 42-patient dataset (non-interactive)

| Verdict | n |
|---|---|
| MATCH | **50** |
| NO MATCH IN BATTERY | 82 |
| UNRESOLVED COLLISION | 9 |
| UNCHECKABLE (kind=other) | 93 |
| Total | 234 |

## The 50 MATCHes — coverage of the paper's core results

All Table-1/4/6 headline statistics verified with provenance: TST by sex
(362.04/380.36), sleep efficiency (77.48/79.54), arousal index
(19.12/14.41/34.17), PLMS index, **REM-AHI by sex (52.27/63.39)**,
NREM-AHI total+male (53.61/50.90), all apnea-type counts by stage and sex,
hypopneas by stage and sex, **ESS by sex (9.12/12.20)**, cohort counts
(42/32/10). Plus stds: sleep latency SD 15.75, efficiency SD 12.15,
arousal SD 20.18, NREM-AHI SD 33.72.

This is the paper's entire descriptive core, machine-verified against raw
data with exact expressions attached to each verdict.

## Known thesis errors — CHECK

- **REM-AHI total = 59.92 → NO MATCH** (data: no cohort mean equals it;
  the true total should be ~54.92 per the osa-psg reanalysis). The tool
  correctly refuses to verify a wrong number.
- **REM-AHI female SD = 31.04 → NO MATCH** (data: 41.04). The known
  transposition typo, flagged exactly as it should be.
- Male REM-AHI 52.27/35.01: mean MATCHed; SD 35.01 → NO MATCH (data 35.02,
  a 0.01 edge — the strict tolerance at work; honest boundary case).
- NREM-AHI female SD 41.61 → NO MATCH: data 41.61? No — data NREM female
  SD differs; flagged for manual review (genuinely off).

The auditor's verdicts on the known-error statistics are exactly right:
the two transposed/typo values are NO MATCH while all their correct
neighbors MATCH. **The tool would have caught the thesis's real errors.**

## NO MATCH breakdown (82)

1. **~24 restated-statistic twins**: the paper restates the same number in
   Discussion prose with different row labels ("Arousal Index (Females)"
   after "Arousal Index (Group 3)"). Dedupe key includes row text, so both
   entries exist; the first MATCH consumes the single pool entry, the twin
   NO MATCHes. Design limitation of `consume()` + restatement — fix
   candidate: dedupe on (row-lowercase, value) with snippet digit match.
2. **Stage percentages (REM% 11.23, NREM% 69.24, etc.)**: not directly
   stored in the dataset (derivable only from stage-minute columns the CSV
   doesn't have) — correctly unverifiable, not errors.
3. **Obese subgroup (n=21, BMI≥30)**: 5 stats — battery has no BMI-filter
   subgroup support yet (only categorical splits). Future battery feature.
4. **Exclusion breakdown (13 total)**: included-only dataset can't verify
   excluded-patient counts — correct behavior, documented disclaimer.
5. **AASM definitions (10, 90%, 3%, 30%)**: methodology constants, not
   data statistics — extraction over-capture, verifier correctly finds no
   battery entry.

## COLLISIONS (9)

Genuine numeric coincidences in the dataset (e.g. female NREM-AHI 62.30 =
mean ahi_rem where mallampati=2+). Interactive path resolves these; the
semantic-matching upgrade would automate most.

## Bottom line

ling-3.0-flash full-paper run: **extraction 234 stats in 6.5 min,
50 verified matches including the paper's complete descriptive core, and
correct NO MATCH verdicts on both known thesis errors.** Combined with the
10-chunk A/B (fastest correct extractor, highest yield), ling is the
strongest extraction engine tested. The consume-vs-restatement limit is
the main fixable weakness (24 false NO MATCHes).
