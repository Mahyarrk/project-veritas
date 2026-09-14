"""
main.py — project-veritas entry point: capability tiers and full pipeline.

Determines what the user provided and reports what the tool can do:
  paper only          -> RAG chat mode (auditing unavailable)
  paper + raw data    -> RAG chat + full audit (extract, verify)

Pipeline: setup -> ingest -> extract -> verify -> chat (interactive).

On first run, setup asks for the user's own LLM endpoints (any
OpenAI-compatible provider) and verifies them with a live call before
anything else runs. The model is assigned before anything starts and
cannot be changed mid-session — chat mode uses the same setup.

Run:
  uv run main.py <paper.txt|docx> [data.csv|xlsx]
  uv run main.py --setup          (re-run endpoint setup)
  (add --no-chat to run the audit and exit)
"""

import json
import sys
from pathlib import Path

import yaml

import setup as setup_module

# ---- FIRST-RUN SETUP: before any LLM-touching import ----
if "--setup" in sys.argv:
    setup_module.reconfigure()
    sys.exit(0)
setup_data = setup_module.ensure_setup()   # asks on first run, verifies live
setup_module.inject_env(setup_data)        # env vars for ingest overrides

# NOW the LLM-touching imports are safe: endpoints are configured.
from extract import extract_paper, EXTRACT_PATH
from ingest import load_paper, describe_dataset, save_store, process_file
from verify import audit, DISCLAIMER

CONFIG = yaml.safe_load(Path("config.yaml").read_text())


def classify_inputs(args: list[str]) -> tuple[Path | None, Path | None]:
    """Split argv into (paper, data) by suffix."""
    paper, data = None, None
    for a in args:
        path = Path(a)
        if not path.exists():
            print(f"skip (not found): {path}")
            continue
        suffix = path.suffix.lower().lstrip(".")
        if suffix in CONFIG["paper"]["formats"] and paper is None:
            paper = path
        elif suffix in CONFIG["data"]["formats"] and data is None:
            data = path
        else:
            print(f"skip (unsupported or duplicate): {path}")
    return paper, data


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--no-chat"]
    no_chat = "--no-chat" in sys.argv
    if not args:
        sys.exit("usage: uv run main.py <paper.txt|docx> [data.csv|xlsx] [--no-chat]")

    paper, data = classify_inputs(args)
    if paper is None:
        sys.exit("no paper provided — a paper (txt/docx) is required.")

    # ---- capability report ----
    print("=" * 60)
    print("project-veritas")
    print("=" * 60)
    print(f"paper: {paper.name}")
    if data:
        print(f"data: {data.name}")
        print("capability: RAG chat + full audit (extract, verify)")
    else:
        print("data: none")
        print("capability: RAG chat ONLY — auditing requires the raw data.")
        print("  Provide the dataset (csv/xlsx) to enable verification.")
    print("=" * 60)

    # ---- ingest ----
    store_path = Path(CONFIG["paths"]["store"])
    store = json.loads(store_path.read_text()) if store_path.exists() else {
        "documents": [], "chunks": [], "datasets": []
    }
    if not process_file(paper, store):
        sys.exit("could not ingest the paper — aborting.")
    data_ingest_failed = False
    if data:
        if not process_file(data, store):
            print("WARNING: data file could not be ingested — "
                  "auditing is DISABLED for this run. "
                  "Fix the data file and rerun to enable the audit.")
            data = None
            data_ingest_failed = True
    save_store(store_path, store)

    # ---- audit (requires data) ----
    if data:
        print("\n" + DISCLAIMER + "\n")
        print("extracting published statistics (this can take a while on "
              "large papers)...")
        results = extract_paper(paper)
        EXTRACT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        ok = sum(1 for r in results if r["status"] == "validated")
        flagged = sum(1 for r in results
                      if r["status"] == "extraction_incomplete")
        print(f"extraction: {ok} statistics validated, "
              f"{sum(1 for r in results if str(r['status']).startswith('discarded'))} "
              f"discarded" + (f", {flagged} INCOMPLETE" if flagged else ""))

        df = pd_read_any(data)
        report = audit(EXTRACT_PATH, df, interactive=True)
        Path(CONFIG["paths"]["audit_report"]).write_text(
            json.dumps(report, ensure_ascii=False, indent=2))

        s = report["summary"]
        print(f"\n{'=' * 60}\nAUDIT RESULTS: {s['match']} MATCH, "
              f"{s['match_human_labeled']} human-labeled, "
              f"{s['no_match']} NO MATCH, {s['skipped']} skipped "
              f"(of {s['total']})\n{'=' * 60}")
        for r in report["results"]:
            if str(r["verdict"]).startswith("MATCH"):
                m = r["matches"][0]
                print(f"  {r['verdict']}: {r['row']} = {r['value']} "
                      f"→ {m['item']} [{m['computed_from']}]")
            else:
                note = f" — {r['note']}" if r.get("note") else ""
                print(f"  {r['verdict']}: {r['row']} = {r['value']}{note}")
        print(f"\n{DISCLAIMER}")
        print(f"full report: {CONFIG['paths']['audit_report']}")
    elif data_ingest_failed:
        print("\naudit skipped: the data file could not be ingested "
              "(see warning above).")
    elif not no_chat:
        print("\naudit skipped (no data provided).")

    # ---- chat ----
    if no_chat:
        print("done (--no-chat).")
        return

    print("\n" + "=" * 60)
    print("chat mode — ask questions about the paper.")
    print("=" * 60)
    from chat import main as chat_main
    chat_main()


def pd_read_any(path: Path):
    import pandas as pd
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


if __name__ == "__main__":
    main()