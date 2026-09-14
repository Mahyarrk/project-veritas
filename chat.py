"""
chat.py — multi-turn RAG chat over the ingested paper.

Accuracy architecture:
1. Every dependent question is rewritten into a self-contained search
   query, and the interpretation is CONFIRMED with the user before any
   search runs — the model may interpret, the user authorizes.
2. Deterministic refusal: if retrieval returns nothing above min_score,
   the LLM is never called — the refusal cannot be a hallucination.
3. Grounded answers: the model answers only from retrieved excerpts and
   cites them as [source, offset]; the sources block shows the user
   exactly what was retrieved.

Session persists in chat_history.json; resume is automatic, /new resets.

Run:  uv run chat.py
"""

import json
from datetime import datetime
from pathlib import Path

import yaml

from ingest import CONFIG, client, model
from retrieve import retrieve

HISTORY_PATH = Path(CONFIG["paths"]["chat_history"])

SYSTEM_PROMPT = """You are Veritas, a careful assistant answering questions
about a scientific paper. You will be given the conversation so far, the
user's latest question, and retrieved excerpts from the paper.

Rules:
1. Answer ONLY from the excerpts. Never use outside knowledge for facts
   about the paper.
2. Cite sources: after each factual claim, reference the excerpt as
   [source file, offset N].
3. If the excerpts do not contain the answer, reply exactly:
   "The provided document does not contain this information."
4. Use the conversation history to resolve references in follow-up
   questions, but ground every factual claim in the current excerpts.
"""

REWRITE_PROMPT = """Rewrite the user's latest question as a single
self-contained search query for document retrieval. Resolve pronouns and
omitted references using the conversation history.

Then classify: was the latest question dependent on the conversation
(pronouns, ellipsis, references to earlier messages) or fully
self-contained?

Respond in exactly this format:
DEPENDENT: yes|no
QUERY: <the rewritten self-contained question>

If the question is already fully self-contained, respond:
DEPENDENT: no
QUERY: <the question unchanged>"""

REFUSAL = "The provided document does not contain this information."

HELP = """commands:
  /help   show this guide
  /new    start a fresh session (clears history)
  /quit   exit (session saved automatically)
anything else is a question about the ingested paper."""


def load_history() -> list[dict]:
    """Load prior turns; tolerate missing/corrupted files."""
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text())
        turns = data.get("turns", [])
        return turns if isinstance(turns, list) else []
    except (json.JSONDecodeError, TypeError):
        return []  # corrupt history — fresh start beats a crash


def save_history(history: list[dict]) -> None:
    HISTORY_PATH.write_text(json.dumps(
        {"saved": datetime.now().isoformat(), "turns": history},
        ensure_ascii=False, indent=2))


def rewrite_query(history: list[dict], question: str) -> tuple[bool, str]:
    """Returns (dependent, self-contained search query).

    Falls back to (True, raw question) on any failure — when in doubt,
    confirm with the user.
    """
    if not history:
        return False, question           # first turn: nothing to resolve
    convo = "\n".join(
        f"{m['role']}: {m['content'][:400]}" for m in history[-10:]
    )
    try:
        c = client()
        resp = c.chat.completions.create(
            model=model("llm"),
            messages=[
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content":
                 f"conversation:\n{convo}\n\nlatest question: {question}"},
            ],
            temperature=CONFIG["llm"]["temperature"],
            max_tokens=300,
        )
        out = (resp.choices[0].message.content or "").strip()
        dependent_line = next(
            (l for l in out.splitlines() if l.upper().startswith("DEPENDENT:")), "")
        query_line = next(
            (l for l in out.splitlines() if l.upper().startswith("QUERY:")), "")
        if not query_line:
            return True, question         # malformed — confirm raw question
        query = query_line.split(":", 1)[1].strip()
        dependent = not dependent_line.lower().endswith("no")
        return dependent, (query or question)
    except Exception:                     # noqa: BLE001 — fallback, never fatal
        return True, question


def build_messages(history: list[dict], question: str,
                   excerpts: list[dict]) -> list[dict]:
    """system, system, prior turns, latest question WITH excerpts inlined.

    The question and its excerpts travel in ONE user message: some local
    models weight the last user message overwhelmingly, so separating the
    question from its context makes the question look answerable only from
    history — producing bogus refusals.
    """
    if excerpts:
        context = "\n\n---\n\n".join(
            f"[{e['source']}, offset {e['offset']}]\n{e['text']}"
            for e in excerpts
        )
        content = (f"{question}\n\nAnswer using ONLY these retrieved excerpts "
                   f"from the paper:\n\n{context}")
    else:
        content = question
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "Excerpts are re-retrieved each turn; "
         "older excerpts may no longer be present."},
        *history,
        {"role": "user", "content": content},
    ]


def answer(question: str, history: list[dict] | None = None) -> tuple[str, list[dict]]:
    """One RAG turn for the GUI: rewrite (if dependent), retrieve, answer.

    Returns (reply_text, excerpts_used). Raises on endpoint errors — the
    GUI decides how to present failures. No state is saved to disk here;
    history management is the caller's job."""
    history = history or []
    from retrieve import retrieve
    dependent, search_query = rewrite_query(history, question)
    excerpts = retrieve(search_query)
    if not excerpts:
        return REFUSAL, []
    messages = build_messages(history, question, excerpts)
    c = client()
    resp = c.chat.completions.create(
        model=model("llm"),
        messages=messages,
        temperature=CONFIG["llm"]["temperature"],
        max_tokens=CONFIG["llm"]["max_tokens"],
    )
    reply = (resp.choices[0].message.content or "").strip() or REFUSAL
    return reply, excerpts


def main() -> None:
    history = load_history()
    if history:
        print(f"resumed session with {len(history)} stored messages "
              f"(/new to start fresh)")
    print(HELP)

    while True:
        try:
            question = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break
        if not question:
            continue
        if question == "/quit":
            print("bye.")
            break
        if question == "/new":
            history = []
            save_history(history)
            print("new session started.")
            continue
        if question == "/help":
            print(HELP)
            continue
        if question.startswith("/"):
            print(f"unknown command: {question} — /help lists commands.")
            continue

        try:
            dependent, search_query = rewrite_query(history, question)
            if dependent:
                print(f"\nveritas > searching for: \"{search_query}\"")
                try:
                    ok = input("          confirm? [Enter=yes / type correction]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nbye.")
                    break
                if ok:
                    search_query = ok
                # user confirmed (Enter) or corrected — the confirmed query is
                # now the question: history, prompt and retrieval all align.
                question = search_query
            excerpts = retrieve(search_query)
            if not excerpts:
                print(f"\nveritas > {REFUSAL}")
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": REFUSAL})
                save_history(history)
                continue

            messages = build_messages(history, question, excerpts)
            c = client()
            resp = c.chat.completions.create(
                model=model("llm"),
                messages=messages,
                temperature=CONFIG["llm"]["temperature"],
                max_tokens=CONFIG["llm"]["max_tokens"],
            )
            reply = (resp.choices[0].message.content or "").strip() or REFUSAL

            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": reply})
            save_history(history)

            print(f"\nveritas > {reply}")
            print("sources:")
            for e in excerpts:
                print(f"  - {e['source']}, offset {e['offset']} "
                      f"(score {e['score']:.2f})")

        except Exception as exc:  # noqa: BLE001 — user-facing boundary
            print(f"\nveritas > ERROR: {exc}")
            print("       (this exchange was not saved; try again)")
            continue


if __name__ == "__main__":
    main()