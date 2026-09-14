"""
extract.py — extract published statistics from a paper using the LLM.

Accuracy architecture: the LLM proposes, deterministic code disposes.
Every proposal must pass:
  1. snippet (whitespace-normalized) exists in the document,
  2. value (digit-sequence) present in the validated snippet,
  3. no duplicate extraction (deduplicated on value+row+column).
Proposals failing any check are discarded with a reason and can never
enter the audit. Statistics-dense chunks that trigger truncation are
split and retried recursively; incomplete extractions are flagged in
the output, never silent.

Output: extracted_values.json — list of
  {paper, table, row, column, value, unit, snippet, source, offsets,
   status}  where offsets lists every occurrence in the document,
   and paper is always "paper" (single-document processing).

Run:  uv run extract.py sources/paper.txt
"""

import json
import re
import sys
from pathlib import Path

import yaml

from ingest import CONFIG, chunk_text, client, load_paper, model

EXTRACT_PATH = Path(CONFIG["paths"]["extracted"])

EXTRACTION_PROMPT = """You are a meticulous research assistant. Extract
every published statistic from the text below: means, standard deviations,
percentages, counts, p-values, correlation coefficients, accuracy scores.

For each statistic output one JSON object with these fields:
  "table":  the table or section it appears in (e.g. "Table 1", "Results"),
  "row":    the row/variable name (e.g. "mean age, males"),
  "column": the column/group (e.g. "males", "total"),
  "value":  the numeric value as written,
  "kind":   one of: mean | std | min | max | median | count |
            p (p-value) | r (correlation) | other.
            Use mean/std/min/max/median/count ONLY for directly reported
            descriptive statistics of one variable; everything else is
            "other" (accuracies, odds ratios, percentages; p-values
            UNLESS marked p).
  "unit":   unit if stated (e.g. "years", "%"), else "",
  "snippet": the EXACT sentence or fragment from the text containing the value.

Output ONLY a JSON array of these objects, no commentary. If there are no
statistics, output [].

Text:
"""


def _norm(s: str) -> str:
    """Whitespace-normalize for matching (LLM snippets often reflow lines)."""
    return re.sub(r"\s+", " ", s).strip()


def _digits(s: str) -> str:
    """Digit signature of a value: '63.39' -> '6339'; '1,234' -> '1234'."""
    return re.sub(r"\D", "", s)


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces; return normalized text and
    a map from normalized index -> original index (for provenance offsets)."""
    norm_chars, offset_map = [], []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            if i > 0 and j < len(text):  # keep one space, skip edges
                norm_chars.append(" ")
                offset_map.append(i)
            i = j
            continue
        norm_chars.append(ch)
        offset_map.append(i)
        i += 1
    return "".join(norm_chars), offset_map


def _find_offsets(text_norm: str, snippet_norm: str,
                  offset_map: list[int]) -> list[int]:
    """All occurrence offsets of a normalized snippet, mapped back to
    original document coordinates."""
    offsets = []
    start = 0
    while True:
        i = text_norm.find(snippet_norm, start)
        if i == -1:
            break
        offsets.append(offset_map[i])
        start = i + 1
    return offsets


def _validate(proposal: dict, text_norm: str,
              offset_map: list[int]) -> dict:
    """Deterministic validation gauntlet. Never trusts the LLM."""
    entry = {
        "table": str(proposal.get("table", "")),
        "row": str(proposal.get("row", "")),
        "column": str(proposal.get("column", "")),
        "value": str(proposal.get("value", "")).strip(),
        "unit": str(proposal.get("unit", "")),
        "snippet": str(proposal.get("snippet", "")),
    }
    # kind: LLM-supplied, validated against the fixed vocabulary. Never
    # trusted beyond this allowlist — an unknown kind degrades to "other",
    # which routes to the interactive path instead of a wrong battery.
    _VALID_KINDS = {"mean", "std", "min", "max", "median", "count",
                    "p", "r", "other"}
    kind = str(proposal.get("kind", "other")).strip().lower()
    entry = {**entry, "kind": kind if kind in _VALID_KINDS else "other"}
    snippet = entry["snippet"]
    if not entry["value"]:
        entry["status"] = "discarded: empty value"
        return entry
    if not snippet:
        entry["status"] = "discarded: no snippet"
        return entry

    snip_norm = _norm(snippet)
    offsets = _find_offsets(text_norm, snip_norm, offset_map)
    if not offsets:
        entry["status"] = "discarded: snippet not found in document"
        return entry

    if _digits(entry["value"]) and _digits(entry["value"]) not in _digits(snip_norm):
        entry["status"] = "discarded: value digits not present in snippet"
        return entry

    entry["offsets"] = offsets
    entry["status"] = "validated"
    return entry


def _extract_once(text: str) -> tuple[list, bool]:
    """One LLM extraction call. Returns (proposals, was_truncated).

    Uses the cloudflare provider (GLM-4.7-flash via 9router): extraction
    is the slow path, and the remote model is ~100x faster than local
    Gemma on this hardware.
    """
    c = client("cloudflare")
    resp = c.chat.completions.create(
        model=model("cloudflare"),
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=CONFIG["llm"]["temperature"],
        max_tokens=CONFIG["cloudflare"]["max_tokens"],
    )
    msg = resp.choices[0].message
    raw = (msg.content or "").strip()
    # reasoning models may put everything in `reasoning` with null content —
    # fall back to it so truncation detection and salvage still work
    if not raw and (msg.reasoning or getattr(msg, "reasoning_content", None)):
        raw = (msg.reasoning or msg.reasoning_content or "").strip()
    truncated = resp.choices[0].finish_reason == "length"

    raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw), truncated
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return [], truncated
    try:
        return json.loads(match.group(0)), truncated
    except json.JSONDecodeError:
        return [], truncated


def _extract_recursive(text: str, depth: int = 0) -> tuple[list, bool]:
    """Extract from a chunk; on truncation, split at a paragraph boundary
    and recurse. Returns (proposals, incomplete_flag)."""
    proposals, truncated = _extract_once(text)
    if not truncated:
        return proposals, False
    if depth >= 3 or len(text) < 400:
        return proposals, True          # flagged in output, never silent
    mid = len(text) // 2
    split = text.rfind("\n", mid - 200, mid + 200)
    if split == -1:
        split = mid
    left, right = text[:split], text[split:]
    left_props, left_inc = _extract_recursive(left, depth + 1)
    right_props, right_inc = _extract_recursive(right, depth + 1)
    return left_props + right_props, left_inc or right_inc


def _dedupe(entries: list[dict]) -> list[dict]:
    """Merge duplicate extractions (overlap chunks share statistics).
    Keeps the longest snippet; merges offsets."""
    deduped: dict[tuple, dict] = {}
    for e in entries:
        if e["status"] != "validated":
            continue
        key = (e["value"], e["table"], e["row"], e["column"])
        if key not in deduped:
            deduped[key] = e
            continue
        cur = deduped[key]
        winner = e if len(e["snippet"]) >= len(cur["snippet"]) else cur
        winner["offsets"] = sorted(set(
            cur.get("offsets", []) + e.get("offsets", [])))
        deduped[key] = winner
    return list(deduped.values())


def extract_paper(path: Path) -> list[dict]:
    """Whole-paper extraction: chunk → LLM extract → validate → dedupe.
    Every entry carries paper="paper" from validation time — a constant,
    never modified downstream."""
    text = load_paper(path)
    text_norm, offset_map = _normalize_with_map(text)
    chunks = chunk_text(text)

    entries: list[dict] = []
    incomplete = False
    for c in chunks:
        proposals, inc = _extract_recursive(c["text"])
        incomplete = incomplete or inc
        for p in proposals:
            if isinstance(p, dict):
                entry = _validate(p, text_norm, offset_map)
                entry["paper"] = "paper"        # constant, set at birth
                entries.append(entry)
        n_ok = sum(1 for e in entries if e["status"] == "validated")
        print(f"  chunk {c['index']}: {len(proposals)} proposals "
              f"({n_ok} validated cumulative)")
        # incremental save: a killed run loses at most the current chunk
        Path(EXTRACT_PATH).write_text(
            json.dumps(_dedupe(entries), ensure_ascii=False, indent=2))

    deduped = _dedupe(entries)
    if incomplete:
        deduped.append({
            "paper": "paper",
            "status": "extraction_incomplete",
            "note": "a chunk hit the token limit after recursive splits; "
                    "some statistics may be missing from this extraction",
        })
    return deduped


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: uv run extract.py <paper.txt|paper.docx>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"not found: {path}")

    results = extract_paper(path)
    EXTRACT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    ok = sum(1 for r in results if r["status"] == "validated")
    flagged = sum(1 for r in results if r["status"] == "extraction_incomplete")
    discarded = sum(1 for r in results if str(r["status"]).startswith("discarded"))
    print(f"\nvalidated: {ok} | discarded: {discarded} | flagged: {flagged}")
    print(f"written: {EXTRACT_PATH}")


if __name__ == "__main__":
    main()
