"""
verify.py — the deterministic auditor.

The LLM only EXTRACTS claims (see extract.py). Every verdict here is
computed from the raw dataset; no model is called in this module.

Matching rules:
  - Kind-pass structure: a published value is never matched across kinds —
    a mean can only be satisfied by a computed mean.
  - Uniqueness: one battery entry satisfies one published value only.
    Ambiguity is surfaced as a collision, never resolved silently.
  - Tie-break: when a published value ties between several battery entries,
    the ungrouped (overall) statistic wins, then the entry from the smallest
    cohort split, then the closest value. A paper's headline number is the
    overall statistic far more often than a subgroup coincidence that
    happens to share its decimal.
  - Boolean-like columns (coding-dependent means) are excluded from the
    automatic battery; their prevalences/counts are added only when an
    assignment callback says so, because the meaning of a 1/2 code is not
    knowable from the data alone.

Resolution is a callback (see `audit`), not input(): the GUI passes a
widget-driven resolver, scripts pass nothing and get deterministic verdicts.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from core import CONFIG, load_dataframe   # noqa: F401  (re-exported for callers)

AUDIT_PATH = Path(CONFIG["paths"]["audit_report"])

ABS_TOL = 0.01     # papers round to 2 decimals; exact equality is not a test
# The default comes from config.yaml (audit.tolerance) so the justification
# for ±0.01 lives in one place — the validation sweep, not a magic number.
KINDS = ["mean", "std", "min", "max", "median", "count"]

DISCLAIMER = (
    "DISCLAIMERS:\n"
    "  1. Each COLUMN is assumed to hold the values of one variable "
    "(one column = sex, another = weight). Transposed data (variables in "
    "rows) produces wrong verdicts.\n"
    "  2. If the paper excluded participants, the data file must contain "
    "ONLY the included cases — auditing unfiltered data produces false "
    "mismatches.\n"
    "  3. Boolean/yes-no coded columns cannot be auto-verified and are "
    "assigned manually when prompted. Such statistics (prevalences, "
    "symptom rates) are often a paper's most important findings."
)


def _num(value):
    """A published value as a float, or None if it is not numeric."""
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def is_boolean_like(series: pd.Series) -> bool:
    """True for real bools and numeric columns holding exactly two values."""
    if pd.api.types.is_bool_dtype(series):
        return True
    return (pd.api.types.is_numeric_dtype(series)
            and len(series.dropna().unique()) == 2)


def column_kinds(df: pd.DataFrame) -> dict[str, list[str]]:
    """numeric (battery-computable) | boolean (user-assigned) |
    categorical (grouping candidates) | text (ignored).

    Boolean-like numeric columns are ALSO grouping candidates — a 1/2 sex
    column is exactly how papers encode the groups subgroup means split by.
    """
    out = {"numeric": [], "boolean": [], "categorical": [], "text": []}
    for c in df.columns:
        s = df[c]
        if is_boolean_like(s):
            out["boolean"].append(c)
            out["categorical"].append(c)
        elif pd.api.types.is_numeric_dtype(s):
            out["numeric"].append(c)
        elif s.nunique() <= 10:
            out["categorical"].append(c)
        else:
            out["text"].append(c)
    return out


def _distinct_group_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Grouping columns that describe distinct cohort splits.

    Drops single-value columns (no split, only noise ties) and columns whose
    value-set duplicates an earlier split — sex and sex_label partition the
    same cohort and would double every subgroup entry.
    """
    seen, out = [], []
    for g in cols:
        sizes = tuple(sorted(int((df[g] == v).sum())
                             for v in df[g].dropna().unique()))
        if len(sizes) < 2 or sizes in seen:
            continue
        seen.append(sizes)
        out.append(g)
    return out


def compute_kind(kind: str, df: pd.DataFrame, col: str,
                 group_col: str | None = None, group_val=None) -> dict | None:
    """One statistic of the requested kind, overall or within one group."""
    series = df[col]
    desc, expr = f"{kind} of {col}", f"df['{col}'].{kind}()"
    if group_col is not None and group_val is not None:
        series = df.loc[df[group_col] == group_val, col]
        desc = f"{kind} of {col} where {group_col}={group_val}"
        expr = f"df.loc[df['{group_col}']=={group_val!r}, '{col}'].{kind}()"
    try:
        value = float(series.count() if kind == "count" else getattr(series, kind)())
    except (TypeError, ValueError, AttributeError):
        return None
    if not np.isfinite(value):
        return None
    return {"kind": kind, "column": col, "group": group_val,
            "groups": 0 if group_col is None else int(df[group_col].nunique()),
            "expression": expr, "description": desc, "value": value}


def match_value(value, kind: str, pool: list[dict],
                tol: float = ABS_TOL) -> list[dict]:
    """Unused pool entries of this kind equal to the published value.

    Sorted by the tie-break rule: ungrouped first, then smallest cohort
    split, then smallest numeric distance.
    """
    pub = _num(value)
    if pub is None:
        return []
    hits = [b for b in pool
            if b["kind"] == kind and abs(b["value"] - pub) <= tol]
    hits.sort(key=lambda b: (b["group"] is not None, b["groups"],
                             abs(b["value"] - pub)))
    return hits


def _record(results: list[dict], row_info: dict, value, verdict: str,
            entries: list[dict] | None = None, note: str | None = None) -> None:
    out = {**row_info, "value": value, "verdict": verdict}
    if entries:
        out["matches"] = [{"item": e["description"],
                           "computed_from": e["expression"],
                           "battery_value": e["value"]} for e in entries]
    if note:
        out["note"] = note
    results.append(out)


def _consume(entry: dict, value, row_info: dict, pool: list[dict],
             used: list[dict], results: list[dict], verdict: str) -> None:
    """Consume one battery entry; keep it in `used` so a later restatement of
    the same number is recognised instead of reported as a false NO MATCH."""
    pool.remove(entry)
    used.append(entry)
    _record(results, row_info, value, verdict, [entry])


def _find_restatement(value, kind: str, used: list[dict],
                      tol: float = ABS_TOL) -> dict | None:
    """An already-consumed entry with this value and kind: the paper repeats
    a result in prose under a different label."""
    v = _num(value)
    if v is None:
        return None
    for u in used:
        if u["kind"] == kind and abs(u["value"] - v) <= tol:
            return u
    return None


def _build_battery(df: pd.DataFrame, kinds: dict, group_cols: list[str],
                   assign=None) -> list[dict]:
    """Every statistic the raw data can produce, with its expression.

    Degenerate numeric columns (a single distinct value, e.g. an all-zero
    history flag) are skipped: their mean/min/max/median are all that
    constant, which manufactures enormous false ties and verifies nothing a
    paper would report.

    count(col) is never computed: on a complete column it is the row count
    echoed once per column, which floods the pool and turns every published
    "n = 42" into a 30-way collision. Row count is added once, below.
    """
    numeric = [c for c in kinds["numeric"]
               if c not in kinds["boolean"] and df[c].dropna().nunique() > 1]
    pool: list[dict] = []

    for kind in KINDS:
        if kind == "count":
            continue
        for col in numeric:
            e = compute_kind(kind, df, col)
            if e:
                pool.append(e)
            for gcol in group_cols:
                if gcol == col:
                    continue
                for gv in df[gcol].dropna().unique():
                    e = compute_kind(kind, df, col, gcol, gv)
                    if e:
                        pool.append(e)

    # boolean-like columns: prevalence/count entries only on instruction
    for col in kinds["boolean"]:
        if assign is None:
            continue
        values = sorted(df[col].dropna().unique(), key=str)
        choice = assign(col, values,
                        {gv: int((df[col] == gv).sum()) for gv in values})
        if choice == "skip_all":
            break
        if choice not in ("prevalence", "count"):
            continue
        for gv in values:
            mask = df[col] == gv
            is_prev = choice == "prevalence"
            pool.append({
                "kind": "mean" if is_prev else "count",
                "column": col, "group": gv, "groups": int(df[col].nunique()),
                "expression": f"(df['{col}']=={gv!r}).{'mean' if is_prev else 'sum'}()",
                "description": f"{choice} of {col}={gv}",
                "value": float(mask.mean() if is_prev else mask.sum()),
            })

    # dataset row count: a paper's "n = 42" is len(df), not a column count
    pool.append({"kind": "count", "column": "(rows)", "group": None, "groups": 0,
                 "expression": "len(df)",
                 "description": f"number of rows in the dataset ({len(df)})",
                 "value": float(len(df))})

    # subgroup sizes: counts OF a group value — group columns only, never
    # every column's non-null count within every subgroup (cohort-size echoes)
    for gcol in group_cols:
        for gv in df[gcol].dropna().unique():
            mask = df[gcol] == gv
            pool.append({"kind": "count", "column": gcol, "group": str(gv),
                         "groups": int(df[gcol].nunique()),
                         "expression": f"(df['{gcol}']=={gv!r}).sum()",
                         "description": f"count of {gcol}={gv} ({int(mask.sum())} rows)",
                         "value": float(mask.sum())})
    return pool


def audit(extracted_path: Path, df: pd.DataFrame, resolve=None,
          assign=None, tolerance: float | None = None) -> dict:
    """Audit extracted claims against the raw data.

    resolve(row_info, value, kind, candidates, used_hits) -> (action, payload)
        "match"    payload = a battery entry from candidates
        "restate"  payload = a used entry from used_hits
        "relabel"  payload = a kind string to retry under
        "skip" / "skip_all" / "unresolved"
    assign(col, values, counts) -> "prevalence" | "count" | "skip" | "skip_all"

    Both callbacks are optional. With neither, the audit is fully
    deterministic: ambiguous values are reported, never guessed.
    """
    tol = (tolerance if tolerance is not None
           else CONFIG.get("audit", {}).get("tolerance", ABS_TOL))
    extracted = json.loads(Path(extracted_path).read_text())
    validated = [e for e in extracted if e.get("status") == "validated"]

    kinds = column_kinds(df)
    group_cols = _distinct_group_cols(df, kinds["categorical"])
    pool = _build_battery(df, kinds, group_cols, assign)

    results: list[dict] = []
    used: list[dict] = []
    state = {"skip_all": False}

    def ask(entry, kind):
        """Surface an ambiguous value; deterministic verdict if unresolved."""
        row_info = {"table": entry.get("table"), "row": entry.get("row"),
                    "column": entry.get("column")}
        cands = match_value(entry["value"], kind, pool, tol)
        v = _num(entry["value"])
        used_hits = ([] if v is None else
                     [u for u in used if u["kind"] == kind
                      and abs(u["value"] - v) <= tol])
        action, payload = ("unresolved", None)
        if resolve is not None:
            action, payload = resolve(row_info, entry["value"], kind,
                                      cands, used_hits)
        if action == "skip_all":
            state["skip_all"] = True
        elif action == "match" and payload in pool:
            _consume(payload, entry["value"], row_info, pool, used, results,
                     "MATCH (human-selected)")
        elif action == "restate" and payload is not None:
            _record(results, row_info, entry["value"],
                    "MATCH (restatement, user-confirmed)", [payload],
                    "user confirmed this restates an earlier match")
        elif action == "relabel" and payload in KINDS:
            relabeled = match_value(entry["value"], payload, pool, tol)
            if relabeled:
                _consume(relabeled[0], entry["value"], row_info, pool, used,
                         results, "MATCH (human-labeled)")
            else:
                _record(results, row_info, entry["value"],
                        "NO MATCH IN BATTERY",
                        note=f"user relabeled as {payload}; nothing matches")
        elif action == "skip":
            _record(results, row_info, entry["value"], "SKIPPED (by user)")
        elif cands:
            _record(results, row_info, entry["value"],
                    "UNRESOLVED COLLISION", cands)
        else:
            _record(results, row_info, entry["value"], "NO MATCH IN BATTERY")

    for entry in validated:
        row_info = {"table": entry.get("table"), "row": entry.get("row"),
                    "column": entry.get("column")}
        if state["skip_all"]:
            _record(results, row_info, entry["value"], "SKIPPED (by user)")
            continue
        kind = str(entry.get("kind", "other")).lower()
        if kind not in KINDS:
            _record(results, row_info, entry["value"], "UNCHECKABLE (kind=other)")
            continue
        hits = match_value(entry["value"], kind, pool, tol)
        if len(hits) == 1:
            _consume(hits[0], entry["value"], row_info, pool, used, results,
                     "MATCH")
            continue
        overall = [h for h in hits if h["group"] is None]
        if len(overall) == 1:
            # one overall statistic ties with subgroup coincidences: the
            # published number is the paper's headline, not a coincidence in
            # a 2- or 3-way split. Recorded as its own verdict so the
            # inference stays visible in the report.
            _consume(overall[0], entry["value"], row_info, pool, used, results,
                     "MATCH (tie-break: overall)")
            continue
        restated = _find_restatement(entry["value"], kind, used, tol)
        if restated is not None:
            _record(results, row_info, entry["value"], "MATCH (restatement)",
                    [restated], "duplicate publication of an already-verified "
                                "statistic (different label, same number)")
            continue
        if hits:
            ask(entry, kind)
        else:
            _record(results, row_info, entry["value"], "NO MATCH IN BATTERY")

    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    return {
        "disclaimer": DISCLAIMER,
        "summary": {
            "match": by_verdict.get("MATCH", 0),
            "match_restatement": by_verdict.get("MATCH (restatement)", 0),
            "match_tiebreak": by_verdict.get("MATCH (tie-break: overall)", 0),
            "match_human_labeled": sum(
                n for v, n in by_verdict.items()
                if "human" in v and "restatement" not in v),
            "no_match": by_verdict.get("NO MATCH IN BATTERY", 0),
            "collision": by_verdict.get("UNRESOLVED COLLISION", 0),
            "uncheckable": by_verdict.get("UNCHECKABLE (kind=other)", 0),
            "skipped": sum(n for v, n in by_verdict.items()
                           if v.startswith("SKIPPED")),
            "tolerance": tol,
            "total": len(results),
            "by_verdict": by_verdict,
        },
        "results": results,
    }
