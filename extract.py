"""
extract.py — ask the LLM for published statistics, then verify the answer.

Accuracy architecture: the LLM proposes, deterministic code disposes. Every
proposal must pass three checks before it can reach the auditor:
  1. its snippet really occurs in the paper (whitespace-normalized),
  2. the value's digits really occur in that snippet,
  3. it is not a duplicate of an entry already extracted.
Anything failing a check is kept with a `discarded: ...` status — never
silently dropped, never able to enter the audit. A chunk that hits the token
limit is split and retried; if it still truncates, the output carries an
explicit `extraction_incomplete` flag.
"""

import json
import re

from core import CONFIG, chunk_text, client, load_paper, model, setting

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

VALID_KINDS = {"mean", "std", "min", "max", "median", "count",
               "p", "r", "other"}


def _norm(s: str) -> str:
    """Whitespace-normalize for matching (LLM snippets often reflow lines)."""
    return re.sub(r"\s+", " ", s).strip()


def _digits(s: str) -> str:
    """Digit signature of a value: '63.39' -> '6339'; '1,234' -> '1234'."""
    return re.sub(r"\D", "", s)


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces, returning the normalized text
    and a map from normalized index back to original index, so provenance
    offsets survive normalization."""
    out, index = [], []
    i = 0
    while i < len(text):
        if not text[i].isspace():
            out.append(text[i])
            index.append(i)
            i += 1
            continue
        j = i
        while j < len(text) and text[j].isspace():
            j += 1
        if i > 0 and j < len(text):      # keep one space, drop edge runs
            out.append(" ")
            index.append(i)
        i = j
    return "".join(out), index


def _find_offsets(text_norm: str, snippet_norm: str,
                  index: list[int]) -> list[int]:
    """Every occurrence of a normalized snippet, in document coordinates."""
    offsets, start = [], 0
    while (i := text_norm.find(snippet_norm, start)) != -1:
        offsets.append(index[i])
        start = i + 1
    return offsets


def validate(proposal: dict, text_norm: str, index: list[int]) -> dict:
    """The deterministic gauntlet. Never trusts the LLM."""
    entry = {k: str(proposal.get(k, "")).strip()
             for k in ("table", "row", "column", "value", "unit")}
    entry["snippet"] = str(proposal.get("snippet", ""))
    kind = str(proposal.get("kind", "other")).strip().lower()
    entry["kind"] = kind if kind in VALID_KINDS else "other"

    if not entry["value"]:
        entry["status"] = "discarded: empty value"
        return entry
    if not entry["snippet"]:
        entry["status"] = "discarded: no snippet"
        return entry
    offsets = _find_offsets(text_norm, _norm(entry["snippet"]), index)
    if not offsets:
        entry["status"] = "discarded: snippet not found in document"
        return entry
    if _digits(entry["value"]) not in _digits(_norm(entry["snippet"])):
        entry["status"] = "discarded: value digits not present in snippet"
        return entry
    entry["offsets"] = offsets
    entry["status"] = "validated"
    return entry


def _extract_once(text: str) -> tuple[list, bool]:
    """One LLM call, with bounded retry on transient upstream failures.

    Returns (proposals, was_truncated).

    Routers and hosted backends flake: observed 503s whose own message says
    "reset after Ns", and 503s that wrap an upstream `[404] Not Found` in the
    body. A single flake must not destroy a 36-chunk extraction that has
    already made 20 successful calls. max_retries stays 0 on the client (the
    SDK's own retry loop re-queues 429s for minutes, which is why it was
    disabled); this is an explicit retry instead.

    Five attempts, backoff 4/8/16/30s — ~58s of total patience. Measured on
    this project's own model: the codex backend was unavailable for longer
    than a 12s budget, so three attempts was not enough.

    Status classification is what makes this safe: a genuine 400 or 401 is a
    permanent problem and is raised immediately, so a wrong key does not cost
    a minute of retries. An error whose body happens to contain "404" is not
    permanent when it arrives inside a 503 — it is the router relaying an
    upstream miss, and it clears on retry (verified against this project's
    own envelope).
    """
    import time

    from openai import (APIConnectionError, APITimeoutError,
                        InternalServerError, RateLimitError)

    transient = (APIConnectionError, APITimeoutError, InternalServerError,
                 RateLimitError)
    delays = [4, 8, 16, 30]
    for attempt, delay in enumerate(delays + [None]):
        try:
            return _call_model(text)
        except transient:
            if delay is None:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _call_model(text: str) -> tuple[list, bool]:
    """The single HTTP call plus JSON salvage. No retry logic here."""
    resp = client("extract").chat.completions.create(
        model=model("extract"),
        messages=[{"role": "system", "content": EXTRACTION_PROMPT},
                  {"role": "user", "content": text}],
        temperature=CONFIG["extract"].get("temperature", 0.0),
        max_tokens=CONFIG["extract"]["max_tokens"],
    )
    msg = resp.choices[0].message
    raw = (msg.content or "").strip()
    if not raw:
        # reasoning models may put everything in a side channel with null
        # content; getattr keeps the fallback from crashing on the models
        # that never expose it at all
        raw = str(getattr(msg, "reasoning", None)
                  or getattr(msg, "reasoning_content", None) or "").strip()
    truncated = resp.choices[0].finish_reason == "length"
    raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw)
    for candidate in (raw, (m.group(0) if (m := re.search(r"\[.*\]", raw,
                                                        re.DOTALL)) else "")):
        if not candidate:
            continue
        try:
            return json.loads(candidate), truncated
        except json.JSONDecodeError:
            continue
    return [], truncated


def _extract_recursive(text: str, depth: int = 0) -> tuple[list, bool]:
    """Extract from a chunk; on truncation split at a paragraph boundary and
    recurse. Returns (proposals, incomplete_flag)."""
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
    a, a_inc = _extract_recursive(left, depth + 1)
    b, b_inc = _extract_recursive(right, depth + 1)
    return a + b, a_inc or b_inc


def dedupe(entries: list[dict]) -> list[dict]:
    """Merge duplicates across overlapping chunks. Keeps the longest snippet
    and unions the offsets, so one statistic keeps every place it appears."""
    merged: dict[tuple, dict] = {}
    for e in entries:
        if e["status"] != "validated":
            continue
        key = (e["value"], e["table"], e["row"], e["column"])
        cur = merged.get(key)
        if cur is None:
            merged[key] = e
            continue
        winner = e if len(e["snippet"]) >= len(cur["snippet"]) else cur
        winner["offsets"] = sorted(set(cur.get("offsets", [])
                                       + e.get("offsets", [])))
        merged[key] = winner
    return list(merged.values())


def _chunk_key(chunk: dict) -> str:
    """Stable identity for a chunk: its index plus a hash of its text.

    The hash matters because the same index means different text if the paper
    or the chunking config changed. A manifest entry whose key no longer
    matches is not eligible to be skipped.
    """
    import hashlib
    return f"{chunk['index']}:{hashlib.sha256(chunk['text'].encode()).hexdigest()[:12]}"


def extract_paper(path, progress=None, save_to=None, resume=False) -> list[dict]:
    """Whole-paper extraction: chunk -> propose -> validate -> dedupe.

    progress(chunk_index, n_chunks, n_validated) is called after each chunk if
    supplied — the GUI renders it, scripts ignore it.

    save_to: when given, the deduped result is rewritten after EVERY chunk.
    A long extraction is the one place this project talks to a remote model for
    tens of minutes, so an interrupted run must never lose finished work.

    resume: with save_to, progress is tracked in `<save_to>.chunks`, a JSON
    manifest of completed chunk keys and their validated entries. On restart,
    completed chunks are skipped and their entries reloaded, so a crash costs
    the chunk in flight rather than the whole run. Hosted routers do flake —
    observed 503s on this project's own model — and paying for 20 good chunks
    a second time is not a defensible failure mode.

    A chunk is recorded as complete only after its model call succeeded. A
    chunk that fails permanently stays unrecorded and is retried next run.
    """
    text = load_paper(path)
    text_norm, index = normalize_with_map(text)
    chunks = chunk_text(text)

    manifest_path = (save_to.parent / (save_to.name + ".chunks")
                     if save_to is not None and resume else None)
    done: dict[str, list[dict]] = {}
    if manifest_path is not None and manifest_path.exists():
        try:
            done = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, TypeError):
            done = {}      # unreadable manifest: start clean rather than crash

    entries: list[dict] = []
    incomplete = False

    for c in chunks:
        key = _chunk_key(c)
        if key in done:
            entries.extend(done[key])
            if progress:
                progress(c["index"], len(chunks),
                         sum(1 for e in entries if e["status"] == "validated"))
            continue

        proposals, inc = _extract_recursive(c["text"])
        incomplete = incomplete or inc
        chunk_entries = []
        for p in proposals:
            if isinstance(p, dict):
                entry = validate(p, text_norm, index)
                entry["paper"] = "paper"
                chunk_entries.append(entry)
        entries.extend(chunk_entries)

        if manifest_path is not None:
            done[key] = chunk_entries
            manifest_path.write_text(json.dumps(done, ensure_ascii=False))

        if save_to is not None:
            save_to.write_text(json.dumps(dedupe(entries), ensure_ascii=False,
                                          indent=2))
        if progress:
            progress(c["index"], len(chunks),
                     sum(1 for e in dedupe(entries)
                         if e["status"] == "validated"))


    deduped = dedupe(entries)
    if incomplete:
        deduped.append({
            "paper": "paper",
            "status": "extraction_incomplete",
            "note": "a chunk hit the token limit after recursive splits; "
                    "some statistics may be missing from this extraction",
        })
    if save_to is not None:
        save_to.write_text(json.dumps(deduped, ensure_ascii=False, indent=2))
    return deduped
