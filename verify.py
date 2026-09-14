"""
verify.py — the deterministic auditor: recompute a battery of statistics
from the raw data and match LLM-extracted published values against them.

Accuracy architecture:
  - The LLM only EXTRACTS claims. All verdicts here are computed.
  - Kind-pass structure: means are matched first (with user conflict
    resolution), then std/min/max/median/count — never cross-kind.
  - Match uniqueness: one battery entry can satisfy only one published
    value; collisions send BOTH to the user.
  - Boolean-like columns (coding-dependent means) are excluded from the
    automatic battery and assigned by the user.

⚠ DISCLAIMERS (printed before and after every audit):
  1. Each COLUMN is assumed to hold the values of one variable
     (one column = sex, another = weight...). Transposed data
     (variables in rows) produces wrong verdicts.
  2. If the paper excluded participants, the file must contain only
     included cases — unfiltered data produces false mismatches.
  3. Boolean/yes-no coded columns cannot be auto-verified; assign them
     when prompted — such statistics (prevalences, symptom rates) are
     often the paper's clinically important findings.

Run:  uv run verify.py <extracted_values.json> <data.csv|xlsx>
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ingest import CONFIG

AUDIT_PATH = Path(CONFIG["paths"]["audit_report"])

REL_TOL = 0.0      # exact match or nothing...
ABS_TOL = 0.01     # ...within ±0.01 absolute — papers round to 2 decimals

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

KINDS = ["mean", "std", "min", "max", "median", "count"]


def load_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "csv":
        return pd.read_csv(path)
    if suffix == "xlsx":
        return pd.read_excel(path)
    raise ValueError(f"unsupported data format: {suffix}")


def is_boolean_like(series: pd.Series) -> bool:
    """True for true bools and 2-unique-value numeric columns (1/2, 0/1)."""
    if pd.api.types.is_bool_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series):
        u = series.dropna().unique()
        return len(u) == 2
    return False


def column_kinds(df: pd.DataFrame) -> dict[str, list[str]]:
    """Classify columns: numeric (battery-computable), boolean (user-assigned),
    categorical (grouping candidates), text (ignored).

    Boolean-like numeric columns are ALSO grouping candidates — a 1/2 sex
    column is how papers encode the groups subgroup means are computed over.
    """
    out = {"numeric": [], "boolean": [], "categorical": [], "text": []}
    for c in df.columns:
        s = df[c]
        if is_boolean_like(s):
            out["boolean"].append(c)
            out["categorical"].append(c)   # boolean cols are grouping candidates
        elif pd.api.types.is_numeric_dtype(s):
            out["numeric"].append(c)
        elif s.nunique() <= 10:
            out["categorical"].append(c)
        else:
            out["text"].append(c)
    return out


def compute_kind(kind: str, df: pd.DataFrame, col: str,
                 group_col: str | None = None, group_val=None) -> dict | None:
    """Compute one statistic of the requested kind. Returns battery entry."""
    series = df[col]
    desc = f"{kind} of {col}"
    expr = f"df['{col}'].{kind}()" if kind != "count" else f"df['{col}'].count()"
    if group_col and group_val is not None:
        mask = df[group_col] == group_val
        series = df.loc[mask, col]
        desc = f"{kind} of {col} where {group_col}={group_val}"
        expr = f"df.loc[df['{group_col}']=={group_val!r}, '{col}'].{kind}()"
    try:
        if kind == "count":
            value = float(series.count())
        elif kind == "std":
            value = float(series.std())
        elif kind == "min":
            value = float(series.min())
        elif kind == "max":
            value = float(series.max())
        elif kind == "median":
            value = float(series.median())
        else:
            value = float(series.mean())
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    return {"kind": kind, "column": col, "group": group_val,
            "expression": expr, "description": desc, "value": value}


def match_value(value: str, kind: str, pool: list[dict]) -> list[dict]:
    """All unused pool entries of this kind numerically equal to the value."""
    try:
        pub = float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return []
    tol = max(REL_TOL * max(abs(pub), 1e-9), ABS_TOL)
    hits = [b for b in pool
            if b["kind"] == kind and abs(b["value"] - pub) <= tol]
    hits.sort(key=lambda b: abs(b["value"] - pub))
    return hits


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def consume(entry: dict, thesis_value: str, row_info: dict,
            pool: list[dict], results: list[dict], label: str,
            used_pool: list[dict] | None = None) -> None:
    """Record a consumed match. Moves the battery entry to used_pool (not
    deleted) so restated statistics can be recognized as MATCH (restatement)
    instead of false NO MATCH."""
    pool.remove(entry)
    if used_pool is not None:
        used_pool.append(entry)
    results.append({
        **row_info, "value": thesis_value,
        "verdict": label,
        "matches": [{"item": entry["description"],
                     "computed_from": entry["expression"],
                     "battery_value": entry["value"]}],
    })


def audit_restatement(entry: dict, row_info: dict, used_pool: list[dict],
                      results: list[dict]) -> bool:
    """A NO MATCH statistic whose value exactly equals an already-consumed
    battery entry is a restatement of the same result (the paper repeats
    numbers in prose with different labels). Verdict recorded deterministically;
    returns True if classified."""
    try:
        v = float(str(entry["value"]).replace(",", "").replace("%", ""))
    except ValueError:
        return False
    for u in used_pool:
        if u["kind"] == entry.get("kind") and abs(u["value"] - v) <= 0.01:
            results.append({
                **row_info, "value": entry["value"],
                "verdict": "MATCH (restatement)",
                "matches": [{"item": u["description"],
                             "computed_from": u["expression"],
                             "battery_value": u["value"]}],
                "note": "duplicate publication of an already-verified "
                        "statistic (different label, same number)",
            })
            return True
    return False


def user_resolve(row_info: dict, thesis_value: str, kind: str,
                 pool: list[dict], results: list[dict],
                 state: dict,
                 used_pool: list[dict] | None = None) -> None:
    """Interactive resolution for unmatched/colliding statistics.
    used_pool (when provided) extends the candidate list with consumed
    entries — selecting one records MATCH (restatement), which is how a
    paper's restated number gets tied to an already-used battery entry."""
    print(f"\n  needs your input: {row_info['row']} "
          f"({row_info['column']}) = {thesis_value}  [kind: {kind}]")
    print(f"  options: <number of a candidate> | relabel as "
          f"({'/'.join(KINDS)}) | skip | skip all")
    candidates = match_value(thesis_value, kind, pool)
    used_hits = []
    if used_pool:
        try:
            v = float(str(thesis_value).replace(",", "").replace("%", ""))
            used_hits = [u for u in used_pool
                         if u["kind"] == kind
                         and abs(u["value"] - v) <= 0.01]
        except ValueError:
            used_hits = []
    for i, c in enumerate(candidates, 1):
        print(f"    {i}. {c['description']} = {c['value']}")
    off = len(candidates)
    for j, u in enumerate(used_hits, 1):
        print(f"    {off + j}. [used] {u['description']} = {u['value']} "
              f"(consumed by an earlier match)")
    ans = _ask("  your choice: ")
    if ans.lower() == "skip all":
        state["skip_all"] = True
        results.append({**row_info, "value": thesis_value,
                        "verdict": "SKIPPED (by user)"})
        return
    if ans == "" or ans.lower() == "skip":
        results.append({**row_info, "value": thesis_value,
                        "verdict": "SKIPPED (by user)"})
        return
    if ans.isdigit():
        k = int(ans)
        if 1 <= k <= off:
            consume(candidates[k - 1], thesis_value, row_info,
                    pool, results, "MATCH (human-selected)", used_pool)
            return
        if off < k <= off + len(used_hits):
            results.append({
                **row_info, "value": thesis_value,
                "verdict": "MATCH (restatement, user-confirmed)",
                "matches": [{"item": used_hits[k - off - 1]["description"],
                             "computed_from":
                                 used_hits[k - off - 1]["expression"],
                             "battery_value":
                                 used_hits[k - off - 1]["value"]}],
                "note": "user confirmed this restates an earlier match",
            })
            return
    if ans in KINDS:
        # relabel: search the pool under the human-provided kind
        hits = match_value(thesis_value, ans, pool)
        if hits:
            consume(hits[0], thesis_value, row_info, pool, results,
                    "MATCH (human-labeled)")
            return
        results.append({**row_info, "value": thesis_value,
                        "verdict": "NO MATCH", "kind": ans,
                        "note": f"user relabeled as {ans}; nothing matches"})
        return
    print("  unrecognized input — treating as skip")
    results.append({**row_info, "value": thesis_value,
                    "verdict": "SKIPPED (by user)"})


def audit(extracted_path: Path, df: pd.DataFrame,
          interactive: bool = True) -> dict:
    extracted = json.loads(extracted_path.read_text())
    validated = [e for e in extracted if e.get("status") == "validated"]

    kinds = column_kinds(df)
    print(DISCLAIMER)
    print(f"\ncolumn classification: "
          f"numeric={len(kinds['numeric'])}, boolean={len(kinds['boolean'])}, "
          f"categorical(grouping)={len(kinds['categorical'])}, "
          f"text(ignored)={len(kinds['text'])}")

    group_col = None
    if interactive and kinds["categorical"]:
        print(f"  grouping columns auto-detected: {kinds['categorical']}")

    pool: list[dict] = []
    # means for every numeric column, overall + per group value.
    # Group columns auto-detect: every categorical/boolean column can split
    # the cohort (a 1/2 sex column is exactly how papers encode groups).
    group_cols = [g for g in kinds["categorical"]]
    # duplicate splits (sex vs sex_label) double every subgroup entry —
    # keep the first column for each distinct cohort partition
    seen_group_sizes: list = []
    deduped_group_cols: list[str] = []
    for gcol in group_cols:
        sizes = tuple(sorted((df[gcol] == gv).sum() for gv in
                             df[gcol].dropna().unique()))
        if len(sizes) < 2:
            continue   # single-value column: no split, pure noise ties
        if sizes not in seen_group_sizes:
            seen_group_sizes.append(sizes)
            deduped_group_cols.append(gcol)
    group_cols = deduped_group_cols
    for col in kinds["numeric"]:
        if col in kinds["boolean"]:
            continue
        e = compute_kind("mean", df, col)
        if e:
            pool.append(e)
        for gcol in group_cols:
            if gcol == col:
                continue
            for gv in df[gcol].dropna().unique():
                e = compute_kind("mean", df, col, gcol, gv)
                if e:
                    pool.append(e)

    results: list[dict] = []
    state = {"skip_all": False}
    used_pool: list[dict] = []   # consumed battery entries — restatement lookup

    # ---- PASS 1: means ----
    means = [e for e in validated
             if str(e.get("kind", "other")).lower() == "mean"]
    others = [e for e in validated
              if str(e.get("kind", "other")).lower() != "mean"]

    for entry in means:
        if state["skip_all"]:
            results.append({"row": entry.get("row"), "column": entry.get("column"),
                            "value": entry["value"], "verdict": "SKIPPED (by user)"})
            continue
        row_info = {"table": entry.get("table"), "row": entry.get("row"),
                    "column": entry.get("column")}
        hits = match_value(entry["value"], "mean", pool)
        if len(hits) == 1:
            consume(hits[0], entry["value"], row_info, pool, results,
                    "MATCH", used_pool)
        elif len(hits) > 1:
            print(f"\n  COLLISION: {entry['row']} ({entry['column']}) = "
                  f"{entry['value']} matches {len(hits)} statistics:")
            for i, c in enumerate(hits, 1):
                print(f"    {i}. {c['description']} = {c['value']}")
            if interactive:
                user_resolve(entry, entry["value"], "mean", pool,
                             results, state, used_pool)
            else:
                results.append({**row_info, "value": entry["value"],
                                "verdict": "UNRESOLVED COLLISION"})
        else:
            if audit_restatement(entry, row_info, used_pool, results):
                continue
            if interactive and not state["skip_all"]:
                user_resolve(entry, entry["value"], "mean", pool,
                             results, state, used_pool)
            else:
                results.append({**row_info, "value": entry["value"],
                                "verdict": "NO MATCH IN BATTERY"})

    # ---- BOOLEAN PASS (user assigns) ----
    for col in kinds["boolean"]:
        if not interactive or state["skip_all"]:
            continue
        print(f"\n  boolean-like column: '{col}' "
              f"(unique values: {sorted(set(df[col].dropna()))})")
        for gv in sorted(df[col].dropna().unique(), key=str):
            n = int((df[col] == gv).sum())
            print(f"    value {gv}: {n} patients")
        a = _ask("    assign as prevalence (mean) / count / skip all / skip: ")
        if a.lower() == "skip all":
            state["skip_all"] = True
            break
        if a.lower() == "prevalence":
            for gv in sorted(df[col].dropna().unique(), key=str):
                mask = df[col] == gv
                e = {"kind": "mean", "column": col, "group": gv,
                     "expression": f"(df['{col}']=={gv!r}).mean()",
                     "description": f"prevalence of {col}={gv}",
                     "value": float(mask.mean())}
                pool.append(e)
        elif a.lower() == "count":
            for gv in sorted(df[col].dropna().unique(), key=str):
                mask = df[col] == gv
                e = {"kind": "count", "column": col, "group": gv,
                     "expression": f"(df['{col}']=={gv!r}).sum()",
                     "description": f"count of {col}={gv}",
                     "value": float(mask.sum())}
                pool.append(e)

    # ---- PASS 2: other kinds ----
    # dataset row count: papers' "n = 42" is len(df), not a column count
    pool.append({
        "kind": "count", "column": "(rows)", "group": None,
        "expression": "len(df)",
        "description": f"number of rows in the dataset ({len(df)})",
        "value": float(len(df)),
    })
    # subgroup sizes: "32 males / 10 females" are counts OF a group value.
    # Only group columns THEMSELVES — not every column's non-null count
    # within every subgroup (those are cohort-size echoes that flood the
    # pool: 30 numeric columns x male subgroup = 30 spurious "32"s).
    # sex_label duplicates sex (same split, string labels) — skip columns
    # whose value-set is a relabeling of an already-added group column.
    seen_group_sizes: list = []
    for gcol in kinds["categorical"]:
        sizes = tuple(sorted((df[gcol] == gv).sum() for gv in
                             df[gcol].dropna().unique()))
        if len(sizes) < 2:
            continue   # single-value column: no split, pure noise ties
        if sizes in seen_group_sizes:
            continue   # duplicate cohort split (e.g. sex vs sex_label)
        seen_group_sizes.append(sizes)
        for gv in df[gcol].dropna().unique():
            mask = df[gcol] == gv
            pool.append({
                "kind": "count", "column": gcol, "group": str(gv),
                "expression": f"(df['{gcol}']=={gv!r}).sum()",
                "description": f"count of {gcol}={gv} "
                               f"({int(mask.sum())} rows)",
                "value": float(mask.sum()),
            })
    for kind in ["std", "min", "max", "median", "count"]:
        for col in kinds["numeric"]:
            if col in kinds["boolean"]:
                continue
            if kind == "count":
                # count(col) = non-null count of a complete column = dataset
                # row count (42) echoed 30x — floods the pool and makes
                # every "n = 42" a 30-way collision. Row count is already
                # in the pool once; per-column non-null counts add nothing
                # a paper would publish.
                continue
            e = compute_kind(kind, df, col)
            if e:
                pool.append(e)
            if group_col and kind != "count":
                # count-per-subgroup = cohort-size echoes (42/32/10 across
                # every column) — computed above from group columns only
                for gv in df[group_col].dropna().unique():
                    e = compute_kind(kind, df, col, group_col, gv)
                    if e:
                        pool.append(e)

    for entry in others:
        if state["skip_all"]:
            results.append({"row": entry.get("row"), "column": entry.get("column"),
                            "value": entry["value"], "verdict": "SKIPPED (by user)"})
            continue
        kind = str(entry.get("kind", "other")).lower()
        row_info = {"table": entry.get("table"), "row": entry.get("row"),
                    "column": entry.get("column")}
        if kind not in KINDS:
            if interactive and not state["skip_all"]:
                user_resolve(entry, entry["value"], "other", pool,
                             results, state, used_pool)
            else:
                results.append({**row_info, "value": entry["value"],
                                "verdict": "UNCHECKABLE (kind=other)"})
            continue
        hits = match_value(entry["value"], kind, pool)
        if len(hits) == 1:
            consume(hits[0], entry["value"], row_info, pool, results,
                    "MATCH", used_pool)
        elif len(hits) > 1 and interactive:
            user_resolve(entry, entry["value"], kind, pool, results,
                         state, used_pool)
        elif len(hits) > 1:
            results.append({**row_info, "value": entry["value"],
                            "verdict": "UNRESOLVED COLLISION"})
        else:
            if audit_restatement(entry, row_info, used_pool, results):
                continue
            if interactive and not state["skip_all"]:
                user_resolve(entry, entry["value"], kind, pool, results,
                             state, used_pool)
            else:
                results.append({**row_info, "value": entry["value"],
                                "verdict": "NO MATCH IN BATTERY"})

    n_match = sum(1 for r in results if r["verdict"] == "MATCH")
    n_hl = sum(1 for r in results if "human" in r["verdict"] or "human-selected" in r["verdict"])
    n_nom = sum(1 for r in results if r["verdict"] == "NO MATCH IN BATTERY")
    n_skip = sum(1 for r in results if r["verdict"].startswith("SKIPPED"))
    n_unc = len(results) - n_match - n_hl - n_nom - n_skip
    return {
        "disclaimer": DISCLAIMER,
        "summary": {"match": n_match, "match_human_labeled": n_hl,
                    "no_match": n_nom, "skipped": n_skip,
                    "other": n_unc, "total": len(results)},
        "results": results,
    }


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: uv run verify.py <extracted_values.json> <data.csv|xlsx>")
    extracted_path = Path(sys.argv[1])
    data_path = Path(sys.argv[2])

    df = load_dataframe(data_path)
    report = audit(extracted_path, df, interactive=True)

    AUDIT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    s = report["summary"]
    print(f"\naudit complete: {s['match']} MATCH, "
          f"{s['match_human_labeled']} human-labeled, "
          f"{s['no_match']} NO MATCH, {s['skipped']} skipped, "
          f"{s['other']} other (of {s['total']})")
    for r in report["results"]:
        if str(r["verdict"]).startswith("MATCH"):
            m = r["matches"][0]
            print(f"  {r['verdict']}: {r['row']} ({r['column']}) = "
                  f"{r['value']} → {m['item']} [{m['computed_from']}]")
        elif r["verdict"] != "MATCH":
            print(f"  {r['verdict']}: {r['row']} = {r['value']}")
    print(f"\n{DISCLAIMER}")
    print(f"written: {AUDIT_PATH}")


if __name__ == "__main__":
    main()