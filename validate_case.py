"""
validate_case.py — the validation case: this tool against known errors.

Ground truth comes from the osa-psg reanalysis, hand-verified against the
archived SPSS outputs and the raw data:

  E1  REM-AHI total mean published 59.92 — data says 54.92. VALUE ERROR.
  E2  REM-AHI female SD published 31.04 — data says 41.04. VALUE ERROR.
  E3  Table 13 t = 7.89 attached to a REM-vs-NREM claim; the arithmetic
      traces to a one-sample test. TEST-SELECTION error — out of scope for
      value verification (documented, not scored).

Scored: was each value error flagged (anything but MATCH), how many correct
statistics were wrongly flagged, and how verdicts move across tolerances —
the sweep that justifies ±0.01 rather than asserting it.

Tolerance is a parameter of audit(), so the sweep no longer monkeypatches
module globals: no order dependence, safe to parallelise.

Run:  uv run validate_case.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

import core
from verify import audit

SRC = Path("sources/thesis_en.txt")
DATA = Path("sources/data_included.csv")
EXTRACTED = Path("runs/extracted/extracted_gemma31_full.json")
TOLERANCES = [0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05]

KNOWN_VALUE_ERRORS = [
    {"id": "E1", "label": "REM-AHI total mean = 59.92",
     "value": 59.92, "true_value": 54.92,
     "where": "Table 6, REM-AHI row, Total Cohort"},
    {"id": "E2", "label": "REM-AHI female SD = 31.04",
     "value": 31.04, "true_value": 41.04,
     "where": "Table 6, REM-AHI row, Female SD"},
]


def main() -> None:
    print("=" * 60)
    print("validate_case: project-veritas vs known thesis errors")
    print("=" * 60)

    if "--fresh" in sys.argv:
        from extract import extract_paper
        print(f"re-extracting {SRC} ...")
        core.save_json(EXTRACTED, extract_paper(SRC))
        print(f"written: {EXTRACTED}")

    df = pd.read_csv(DATA)
    wanted = {str(e["value"]) for e in KNOWN_VALUE_ERRORS}

    # ---- detection at the shipped tolerance ----
    report = audit(EXTRACTED, df)
    print(f"\n=== detection at ±{core.CONFIG['audit']['tolerance']} ===")
    detection = {}
    for err in KNOWN_VALUE_ERRORS:
        hits = [r for r in report["results"] if r.get("value") == str(err["value"])]
        flagged = [r for r in hits if not str(r["verdict"]).startswith("MATCH")]
        detection[err["id"]] = {"label": err["label"],
                                "occurrences": len(hits),
                                "flagged": len(flagged),
                                "detected": bool(flagged)}
        print(f"  {err['id']}: {err['label']} — "
              f"{'DETECTED' if flagged else 'MISSED'} "
              f"({len(flagged)}/{len(hits)} occurrences flagged)")
    s = report["summary"]
    print(f"  coverage: {s['match'] + s['match_human_labeled']} MATCH, "
          f"{s['match_restatement']} restated, {s['no_match']} NO MATCH, "
          f"{s['collision']} collision, {s['uncheckable']} uncheckable "
          f"(of {s['total']})")

    # ---- tolerance sweep ----
    print("\n=== tolerance sweep ===")
    sweep = []
    for tol in TOLERANCES:
        rep = audit(EXTRACTED, df, tolerance=tol)
        rs = rep["results"]
        n_match = sum(1 for r in rs if str(r["verdict"]).startswith("MATCH"))
        # an error counts as detected if EVERY occurrence of its value was
        # flagged; one surviving MATCH on the wrong number is a miss
        det = sum(1 for e in KNOWN_VALUE_ERRORS
                  if all(not str(r["verdict"]).startswith("MATCH")
                         for r in rs if r.get("value") == str(e["value"])))
        false_matches = sum(1 for r in rs if r.get("value") in wanted
                            and str(r["verdict"]).startswith("MATCH"))
        sweep.append({"tol": tol, "matches": n_match,
                      "errors_detected": det, "false_matches": false_matches})
        print(f"  ±{tol:<6} matches={n_match:<4} errors detected={det}/2 "
              f"false-matches={false_matches}")

    best = max(sweep, key=lambda s: s["matches"])
    print(f"\n  best coverage at ±{best['tol']} ({best['matches']} matched). "
          "Wider tolerances lose matches, not gain them: more values fall "
          "inside the window, so more published numbers become ties and are "
          "withheld as collisions instead of being credited.")

    out = {"extraction_model": core.model("extract"),
           "tolerance": core.CONFIG["audit"]["tolerance"],
           "known_errors": KNOWN_VALUE_ERRORS, "detection": detection,
           "tolerance_sweep": sweep}
    core.save_json(Path("runs/validation_report.json"), out)
    print("\nwritten: runs/validation_report.json")

    if all(d["detected"] for d in detection.values()):
        print("\nRESULT: both known value errors detected.")
    else:
        print("\nRESULT: at least one known error was MISSED — investigate.")


if __name__ == "__main__":
    main()
