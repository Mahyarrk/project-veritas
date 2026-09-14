# Accuracy Analysis — GLM-4.7-flash Extraction, 18 Chunks

Date: 2026-09-14. Source artifacts:
`extracted_values_glm_18chunks.json` (74 validated statistics from thesis
chunks 0–17), audited non-interactively against `Book1.xlsx` (55 rows).
Report: `audit_report_glm_18chunks.json`.

## Headline verdict distribution (74 statistics)

| Verdict | n | Interpretation |
|---|---|---|
| MATCH | 4 | auto-verified against raw data |
| UNRESOLVED COLLISION | 15 | value found in battery but 2–17 candidates tie |
| NO MATCH IN BATTERY | 41 | no battery entry within tolerance |
| UNCHECKABLE (kind=other) | 14 | percentages, odds-type stats — no battery |
| **Total** | **74** | |

Non-interactive run: collisions and other-kind stats auto-skip (the
interactive path exists precisely to resolve them — test 3's job).

## The four auto-matches — scrutiny

| Published | Battery | Diff | Real match? |
|---|---|---|---|
| NREM-AHI 53.61 | mean q31 where q9='Screen...PLMD' = 54.38 | 0.77 (1.44% rel) | **doubtful** — 1.5% tolerance let a subgroup mean pass as the cohort mean; 54.38 ≠ 53.61 is a different quantity |
| age min 20 | min q5 = 19.9 | 0.10 | **borderline** — 19.9 rounds to 20 only if the paper rounded oddly (20 could be 19.95 truncated); plausible but unproven |
| age max 65 | max q2 = 65.0 | exact | legitimate |
| REM sleep % std (male) 7.72 | std q36 = 7.697 | 0.023 | legitimate rounding |

**2 of 4 auto-matches are solid; 2 are tolerance artifacts.** The dual
tolerance (REL_TOL 0.015 / ABS_TOL 0.06) is too permissive in both
directions: 1.5% relative on a ~54-scale value admits a 0.77 gap, and the
0.06 absolute floor lets a 0.10 gap through via the relative arm.

## Why 41 NO MATCH — the honest breakdown

1. **Dataset is 55 rows; the paper's cohort is 42.** The thesis says 13
   patients were excluded; Book1.xlsx contains all 55. The disclaimers
   warn exactly this: auditing unfiltered data produces false mismatches.
   Every "remaining patients = 42", "males = 32", "females = 10" count
   fails because the battery counts columns over 55 rows (and counts
   per-column non-nulls, not dataset rows — a battery gap).
2. **Count battery gap:** "42 patients" is `len(df after exclusions)` —
   the battery has no row-count entry, so even a filtered 42-row file
   would need the count-of-rows statistic added.
3. **Exclusion-criteria counts (13, 4, 4, 2, 1, 1, 1):** verifiable only
   against a per-patient exclusion/diagnosis column, if it exists — most
   are text-coded, not numeric.

## Collision finding — the interesting one

15 collisions, all means, ties of 2–17 battery entries (e.g. Sleep
Efficiency 77.48 ties 17 different `mean of q27 where ...` subsets).
Root cause: **the battery computes every subgroup mean of every numeric
column across every categorical split — a combinatorial pool.** Real
cohort statistics land inside that pool's numeric fog, so unique matching
fails where the tool's core promise (item→number provenance) matters most.
This is not extraction error — it's a matcher-design limitation:
matching should use the *group semantics* from the extraction (row says
"Male", so candidate must be a sex-split), not numeric proximity alone.

## Known thesis errors — check

The two known Table-6 errors (REM-AHI total 59.92→54.92; female SD
31.04→41.04) live in chunks 18+ — **not covered by this 18-chunk
extraction.** The NREM-AHI table rows that WERE extracted (53.61/33.72
total, 50.90/31.13 male, 62.30/41.61 female) all match the corrected
thesis values — extraction fidelity on these is confirmed against the
thesis itself (they arrived validated with exact snippets). The t=7.89
one-sample-test context (Table 10) is also chunk 18+.

## Scorecard for the GLM extraction contract (the actual test subject)

Extraction quality per se: **74/74 proposed statistics passed the
validation gauntlet** (snippet-digit presence, dedupe, kind allowlist) —
zero discard noise, no fabricated fields, table/row/column labels sane.
The failures below are verifier-side (battery design, tolerance,
unfiltered data), not extractor-side:

| Layer | Verdict |
|---|---|
| Extraction (GLM + gauntlet) | clean — 0 false proposals detected |
| Matching/tolerance | needs tightening (2 of 4 matches suspect) |
| Battery design | needs row-count + semantic group matching |
| Input data | unfiltered 55 rows — false mismatches expected and observed |

## Actions arising

1. Tighten tolerance: REL_TOL 0.015→~0.005, drop the absolute floor or
   scale it by magnitude (rounding of a 2-decimal published value implies
   ±0.005, not ±0.06)
2. Add dataset row-count to the count battery (`len(df)`)
3. Semantic matching: extraction's column/group hints ("Male", "Total
   Cohort") should prune the candidate pool before numeric comparison
4. Re-audit with a 42-row filtered dataset to isolate true mismatches
5. Model decision (user's pick) then rerun full-paper extraction
