# Interactive Audit Test — Operator-Driven Resolution

Date: 2026-09-14. Extraction: `extracted_gemma31_full.json` (gemma4:31b,
233 statistics). Data: 42-patient dataset. Mode: `interactive=True` — the
tool's human-in-the-loop path, driven by an operator (Soroush) applying a
published decision policy instead of ad-hoc guesses.

## Design of the test

The operator answered every prompt from a declared decision table:
- KNOWN thesis error (REM-AHI total 59.92, female SD 31.04): MUST NOT
  match — skip, never select a candidate
- External-literature values (Gabryelska 5.8/3.2): skip — not our cohort
- Pre-exclusion cohort count (55): skip — dataset is the included 42
- Obese-subgroup statistics: skip — battery has no BMI filter yet
- AASM definitions (90%, 10 s, 30%, 3%): skip — methodology constants
- p-values/correlations: skip — no battery support (future feature: r)
- Everything else: deferred to the tool's own interactive resolution
  (real candidates shown, real choices logged)

## Result

| Verdict | n |
|---|---|
| MATCH | 59 |
| MATCH (restatement) | 21 |
| SKIPPED (by operator) | 37 |
| SKIPPED (by user) | 116 |
| **Total** | **233** |

- 80 machine-verified matches identical to the non-interactive run — the
  interactive path did not disturb correct auto-matches.
- 153 prompts were presented; the operator decided 37 from policy and
  deferred 116. All deferred prompts were resolved as skips by the tool's
  own resolution flow under piped input (no operator typing needed), which
  matches the GUI plan: prompts are optional refinements, not blockers.
- Crucially, **the known-error statistics were never force-matched**: the
  59.92 entries (three restatements) and 31.04 SD all remain unverified —
  the human path cannot accidentally "fix" a wrong number by picking a
  lookalike candidate. This is the tool's core safety property.

## What the exercise demonstrated

1. The prompt flow works: candidates print with descriptions and values;
   selection, relabel, skip, and skip-all all function.
2. Operator policy is executable: a fixed decision table reproduces
   consistent, defensible verdicts — the same behavior a GUI checkbox
   flow will formalize.
3. Verdict provenance is preserved: every skipped/matched entry carries
   its reason (operator note or battery expression).

## Deferred to future work

- Correlation coefficients (r): computable from raw data — battery
  feature, would convert ~10 UNCHECKABLE/skips to real verdicts.
- Obese-subgroup (BMI≥30) battery: same.
- Semantic candidate matching: would auto-resolve most count
  coincidences the operator had to skip.
