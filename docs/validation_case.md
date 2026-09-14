# Validation Case — project-veritas vs Known Thesis Errors

Date: 2026-09-14. Script: `validate_case.py` (consumes committed
artifacts; `--fresh` reruns extraction). Extraction model: gemma4:31b,
full paper, 233 statistics. Data: 42-patient included cohort.

## Ground truth

Independently known from the osa-psg reanalysis (hand-verified against
archived SPSS outputs and raw data):

| ID | Error | Published | True | Type |
|---|---|---|---|---|
| E1 | REM-AHI total mean | 59.92 | 54.92 | value error (3 occurrences in paper) |
| E2 | REM-AHI female SD | 31.04 | 41.04 | value error (1 occurrence) |
| E3 | Table 13 t=7.89 test label | one-sample test presented in REM-vs-NREM context | — | test-selection error |

E3 is documented but **not scored**: raw-data verification checks whether
published *values* match the data; it cannot judge which statistical test
the author should have chosen. The tool's scope is value errors.

## Result: 2/2 value errors detected

| ID | Occurrences in paper | Flagged by audit |
|---|---|---|
| E1 | 3 | **3/3** (all NO MATCH) |
| E2 | 1 | **1/1** (NO MATCH) |

Every occurrence of every known-wrong number was flagged, across all its
restatements. No false "verification" of a wrong value anywhere in the
run — the tool's core safety property held end-to-end.

## Tolerance sweep — why ±0.01

The audit was rerun at seven absolute tolerances (relative fixed at 0):

| tol | verified matches | false matches of known errors |
|---|---|---|
| ±0.001 | 38 | 0 |
| ±0.002 | 44 | 0 |
| ±0.005 | 66 | 0 |
| **±0.01** | **80** | **0** |
| ±0.02 | 74 | 0 |
| ±0.03 | 70 | 0 |
| ±0.05 | 53 | 0 |

- Detection of the known errors is perfect at **every** tolerance (their
  data values differ from the published ones by 4.4 and 10.0 — far
  outside any plausible tolerance).
- Verified matches peak at ±0.01 (80) — the sweet spot where legitimate
  2-decimal rounding (e.g. published 362.04 vs computed 362.0437) is
  accepted. Wider tolerances don't add matches; they only widen the
  collision fog (subgroup means tie with cohort means), which *reduces*
  unique matches (74 → 53 at ±0.05).
- The red line (false matches of known errors) never leaves zero: even a
  sloppy tolerance wouldn't have "verified" the thesis's errors. The
  chosen ±0.01 is justified on coverage, not on risk.

Chart: `docs/validation_tolerance_sweep.png`.

## Honest limitations

1. **E3 out of scope** — test-selection errors need methodology review,
   not value matching. Stated in the tool's disclaimers.
2. **Single validation case** — the tool is tuned on our own thesis
   (author = operator). It has seen exactly one paper. Generalization is
   untested; the roadmap item is running it on osa-psg's comparison
   papers.
3. **93 UNCHECKABLE statistics** (p-values, r coefficients, percentages,
   subgroup stats) — no battery support yet. Correlation (r) and
   BMI-subgroup batteries would convert roughly 20 of these to real
   verdicts.
4. **56 NO MATCH residuals** — stage percentages not stored in the CSV,
   exclusion counts (correctly unverifiable), AASM definitions
   (over-extraction), and a few tolerance-edge cases (35.01 vs 35.02).
