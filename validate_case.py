"""
validate_case.py — formal validation of project-veritas against the thesis.

The validation case: our own thesis, whose errors are independently known
from the osa-psg reanalysis (hand-verified against the archived SPSS
outputs and raw data). This script scores the tool's audit against that
ground truth.

Ground truth (from docs/ + osa-psg report.md):
  E1  REM-AHI total mean published 59.92 — data says 54.92. VALUE ERROR.
  E2  REM-AHI female SD published 31.04 — data says 41.04. VALUE ERROR.
  E3  Table 13 t=7.89 attached to a REM-vs-NREM claim; arithmetic traces
      to a one-sample test. TEST-SELECTION error — out of scope for
      raw-data value verification (documented, not scored).

Scored dimensions:
  - detection: was each value-error statistic flagged (NO MATCH /
    skipped / anything but MATCH)?
  - false positives: correct statistics wrongly flagged?
  - coverage: how many extracted statistics got a real verdict?
  - tolerance sweep: audit rerun at 10 tolerances to plot the
    sensitivity/specificity tradeoff that justifies ±0.01.

Modes:
  default     consume committed artifacts (extracted_gemma31_full.json)
  --fresh     rerun extraction first (needs API access, ~4 min)

Run:  uv run validate_case.py [--fresh]
"""

import json
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # headless: PNG output only
import matplotlib.pyplot as plt

import yaml

CONFIG = yaml.safe_load(Path("config.yaml").read_text())
SRC = Path("sources/thesis_en.txt")
DATA = Path("sources/data_included.csv")
FRESH = "--fresh" in sys.argv

# ---- ground truth ----------------------------------------------------
KNOWN_VALUE_ERRORS = [
    {"id": "E1", "label": "REM-AHI total mean = 59.92",
     "value": 59.92, "true_value": 54.92,
     "where": "Table 6, REM-AHI row, Total Cohort"},
    {"id": "E2", "label": "REM-AHI female SD = 31.04",
     "value": 31.04, "true_value": 41.04,
     "where": "Table 6, REM-AHI row, Female SD"},
    # E3 (test-selection error) documented but NOT scored: the tool
    # verifies values against data; it cannot judge which test the
    # author SHOULD have run.
]
TOLERANCES = [0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05]


def run_fresh_extraction() -> None:
    """Rerun extraction, overwriting the committed artifact."""
    import extract as ex
    text = ex.load_paper(SRC)
    text_norm, offset_map = ex._normalize_with_map(text)
    chunks = ex.chunk_text(text)
    entries = []
    for c in chunks:
        proposals, _ = ex._extract_recursive(c["text"])
        for p in proposals:
            if isinstance(p, dict):
                entry = ex._validate(p, text_norm, offset_map)
                entry["paper"] = "paper"
                entries.append(entry)
        Path(CONFIG["paths"]["extracted"]).write_text(
            json.dumps(ex._dedupe(entries), ensure_ascii=False, indent=2))
    print("extraction: complete")


def audit_at_tolerance(tol_abs: float, tol_rel: float = 0.0) -> dict:
    """Run the audit with a custom absolute tolerance by patching the
    module constants (used for the sweep)."""
    import verify as V
    old_rel, old_abs = V.REL_TOL, V.ABS_TOL
    V.REL_TOL, V.ABS_TOL = tol_rel, tol_abs
    try:
        import pandas as pd
        df = pd.read_csv(DATA)
        return V.audit(Path("extracted_gemma31_full.json"), df,
                       interactive=False)
    finally:
        V.REL_TOL, V.ABS_TOL = old_rel, old_abs


def score_detection(report: dict) -> dict:
    """For each known error: was the corresponding statistic flagged
    (anything but MATCH)?"""
    out = {}
    for err in KNOWN_VALUE_ERRORS:
        hits = [r for r in report["results"]
                if r.get("value") == str(err["value"])
                and not str(r["verdict"]).startswith("MATCH")]
        out[err["id"]] = {
            "label": err["label"],
            "detected": len(hits) > 0,
            "occurrences_in_paper": len(
                [r for r in report["results"]
                 if r.get("value") == str(err["value"])]),
            "flagged": len(hits),
        }
    return out


def main() -> None:
    print("=" * 60)
    print("validate_case: project-veritas vs known thesis errors")
    print("=" * 60)

    if "--fresh" in sys.argv:
        print("rerunning extraction (--fresh)...")
        # run main's pipeline minus chat
        import subprocess
        subprocess.run(
            ["uv", "run", "main.py", str(SRC), str(DATA), "--no-chat"],
            check=True)
    else:
        if not Path(CONFIG["paths"]["extracted"]).exists():
            sys.exit("no extracted_values.json — run with --fresh or "
                     "main.py first")
        # ensure the audit artifact exists
        if not Path(CONFIG["paths"]["audit_report"]).exists():
            from verify import audit, load_dataframe
            import pandas as pd
            df = pd.read_csv(DATA)
            rep = audit(Path(CONFIG["paths"]["extracted"]), df,
                        interactive=False)
            Path(CONFIG["paths"]["audit_report"]).write_text(
                json.dumps(rep, ensure_ascii=False, indent=2))

    report = json.loads(
        Path(CONFIG["paths"]["audit_report"]).read_text())

    # ---- error detection score ----
    det = score_detection(report)
    detected = sum(1 for d in det.values() if d["flagged"] >= 1)
    print("\n=== known-error detection ===")
    for eid, d in det.items():
        print(f"  {eid}: {d['label']} — "
              f"{'DETECTED' if d['detected'] else 'MISSED'} "
              f"({d['flagged']}/{d['occurrences_in_paper']} occurrences flagged)")

    # ---- tolerance sweep ----
    import pandas as pd
    import verify as V
    df = pd.read_csv(DATA)
    sweep = []
    for tol in [0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05]:
        rep = audit_at_tolerance(tol)
        v = rep["summary"]
        # false-positive proxy: MATCH of a known-error value
        fp = sum(1 for x in rep["results"]
                 for e in KNOWN_VALUE_ERRORS
                 if x.get("value") == str(e["value"])
                 and str(x["verdict"]).startswith("MATCH"))
        det_n = sum(1 for x in rep["results"]
                    if x.get("value") in
                    [str(e["value"]) for e in KNOWN_VALUE_ERRORS]
                    and not str(x["verdict"]).startswith("MATCH"))
        n_match = sum(1 for x in rep["results"]
                      if str(x["verdict"]).startswith("MATCH"))
        sweep.append({"tol": tol, "matches": n_match,
                      "errors_detected": det_n, "false_matches": fp})
        print(f"  tol ±{tol}: matches={n_match}, "
              f"errors detected={det_n}/2, false-matches={fp}")

    out = {
        "known_errors": KNOWN_VALUE_ERRORS,
        "detection": det,
        "tolerance_sweep": sweep,
        "extraction_model": CONFIG["cloudflare"]["model"],
    }
    Path("validation_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    print("\nwritten: validation_report.json")


if __name__ == "__main__":
    main()