"""
gui.py — the only interface: browser UI for the deterministic auditor.

Flow: extraction endpoint -> upload paper + raw data -> extract -> verify ->
verdicts. Values that tie between several computed statistics are resolved
here, in widgets; nothing ever blocks on stdin.

Run:  uv run streamlit run gui.py
"""

import json
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

st.set_page_config(page_title="project-veritas", page_icon="🔬", layout="wide")

CONFIG = yaml.safe_load(Path("config.yaml").read_text())
SOURCES = Path(CONFIG["paths"]["sources"])
EXTRACTED = Path(CONFIG["paths"]["extracted"])
REPORT = Path(CONFIG["paths"]["audit_report"])
REMEMBER = Path.home() / ".veritas" / "setup.json"
ENV = ("VERITAS_EXTRACT_URL", "VERITAS_EXTRACT_MODEL", "VERITAS_EXTRACT_KEY")


def _client(url: str, key: str, timeout: float = 20.0):
    from openai import OpenAI
    return OpenAI(base_url=url, api_key=key or "none", timeout=timeout,
                  max_retries=0)


def list_models(url: str, key: str) -> tuple[list[str], str | None]:
    """Ask an OpenAI-compatible endpoint which models it serves."""
    if not url.strip():
        return [], "no base URL given"
    try:
        ids = sorted(m.id for m in
                     (getattr(_client(url, key).models.list(), "data", None) or []))
        return (ids, None) if ids else ([], "endpoint returned an empty model list")
    except Exception as exc:                       # noqa: BLE001
        return [], f"{type(exc).__name__}: {str(exc)[:160]}"


def test_endpoint(url: str, key: str, name: str) -> tuple[bool, str]:
    if not str(name).strip():
        return False, "no model selected"
    try:
        msg = _client(url, key).chat.completions.create(
            model=name, messages=[{"role": "user", "content": "ping"}],
            max_tokens=5, temperature=0).choices[0].message
        text = (msg.content or getattr(msg, "reasoning_content", "") or "").strip()
        return True, f"responded: {text[:30]!r}"
    except Exception as exc:                       # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def endpoint_form() -> None:
    """First page: the extraction endpoint. Nothing else is reachable."""
    st.title("🔬 project-veritas")
    st.subheader("Extraction endpoint")
    st.markdown(
        "Enter the LLM endpoint that will read the paper. Any "
        "OpenAI-compatible provider works — local (LM Studio, Ollama) or "
        "hosted. **Required each time the app starts**; it is not written to "
        "disk unless you tick *remember on this machine*.")
    st.caption("The key lives in the running process only (in-memory env "
               "injection). Ticking the remember box stores it in "
               "~/.veritas/setup.json with owner-only permissions.")

    saved = {}
    if REMEMBER.exists():
        try:
            saved = json.loads(REMEMBER.read_text())
        except Exception:                          # noqa: BLE001
            saved = {}
    if saved.get("extract") and st.button("↩︎ Fill from remembered setup"):
        for f in ("url", "key", "model"):
            st.session_state[f"extract_{f}"] = saved["extract"].get(f, "")
        st.session_state.pop("extract_models", None)
        st.session_state.pop("extract_models_err", None)
        st.rerun()

    url = st.text_input("Base URL", key="extract_url",
                        placeholder="http://localhost:20128/v1")
    key = st.text_input("API key (blank if local)", key="extract_key",
                        type="password", placeholder="sk-...")
    if st.button("🔍 Check models"):
        st.session_state["extract_models"], st.session_state[
            "extract_models_err"] = list_models(url, key)
    err = st.session_state.get("extract_models_err")
    if err:
        st.error(f"model list failed — {err}")
    found = st.session_state.get("extract_models") or []
    if found and not err:
        st.caption(f"{len(found)} model(s) found on this endpoint")
    name = (st.selectbox("Model", found, key="extract_model_pick",
                         accept_new_options=True,
                         help="Pick a model the endpoint reported, or type one.")
            if found else
            st.text_input("Model", key="extract_model",
                          placeholder="press Check models"))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔎 Test connection", use_container_width=True):
            with st.spinner("testing..."):
                ok, msg = test_endpoint(url, key, name)
            (st.success if ok else st.error)(msg)
    with c2:
        if st.button("✅ Save & continue", type="primary",
                     use_container_width=True):
            if not url.strip() or not str(name).strip():
                st.error("Fill the base URL and model.")
                st.stop()
            for var, val in zip(ENV, (url.strip(), str(name).strip(), key)):
                os.environ[var] = val
            if st.session_state.get("remember_ends"):
                REMEMBER.parent.mkdir(parents=True, exist_ok=True)
                REMEMBER.write_text(json.dumps(
                    {"extract": {"url": url.strip(), "key": key,
                                 "model": str(name).strip()}}, indent=2))
                os.chmod(REMEMBER, 0o600)
                st.toast(f"remembered in {REMEMBER}")
            st.session_state["ready"] = True
            st.rerun()

    st.checkbox("Remember this endpoint on this machine "
                "(~/.veritas/setup.json)", key="remember_ends", value=False,
                help="Off by default. When on, the URL, key and model are "
                     "written to your home directory with owner-only "
                     "permissions and offered back on the next start.")


if not st.session_state.get("ready"):
    endpoint_form()
    st.stop()

# imports only after the endpoint is injected
import core                                     # noqa: E402
from extract import extract_paper               # noqa: E402
from verify import audit                        # noqa: E402

st.sidebar.title("🔬 project-veritas")
st.sidebar.success("Endpoint configured ✓")
st.sidebar.caption(f"**extract** · {os.environ.get(ENV[1], '')}")
if st.sidebar.button("Reconfigure endpoint"):
    st.session_state["ready"] = False
    st.rerun()

st.header("Audit a paper")
col1, col2 = st.columns(2)
with col1:
    paper_file = st.file_uploader("Paper (txt/docx)", type=["txt", "docx"])
with col2:
    data_file = st.file_uploader("Raw data (csv/xlsx)", type=["csv", "xlsx"])

if st.button("▶ Run audit", type="primary", disabled=not paper_file,
             use_container_width=True):
    if not data_file:
        st.warning("No data file — the audit needs the raw dataset to "
                   "recompute anything, so only extraction will run.")
    SOURCES.mkdir(exist_ok=True)
    paper_path = SOURCES / paper_file.name
    paper_path.write_bytes(paper_file.getvalue())
    data_path = None
    if data_file:
        data_path = SOURCES / data_file.name
        data_path.write_bytes(data_file.getvalue())

    t0 = time.time()
    status = st.status("Extracting published statistics...", expanded=True)
    with status:
        def report_chunk(i, n, ok_so_far):
            status.update(label=f"chunk {i + 1}/{n}: {ok_so_far} statistics "
                                f"validated so far")

        entries = extract_paper(paper_path, progress=report_chunk)
        core.save_json(EXTRACTED, entries)
        st.write(f"{sum(1 for e in entries if e.get('status') == 'validated')} "
                 f"statistics validated in {time.time() - t0:.0f}s")
        if any(e.get("status") == "extraction_incomplete" for e in entries):
            st.warning("Extraction was truncated on at least one chunk — some "
                       "statistics may be missing. Treat coverage as a lower "
                       "bound, not a measurement.")

    st.session_state.pop("report", None)
    st.session_state.pop("collisions", None)
    st.session_state.pop("pending", None)
    if data_path:
        try:
            st.session_state["pending"] = {"df": core.load_dataframe(data_path)}
        except ValueError as exc:
            st.error(str(exc))
    st.rerun()

# ---- resolution, two passes over the same deterministic call order ----
# Pass 1 collects every published value that ties between computed
# statistics. The widgets collect answers. Pass 2 replays the audit with a
# resolver that returns answer #n for the n-th question — audit() asks in the
# same order for identical inputs, so position is a sound key.
pending = st.session_state.get("pending")
if pending and "report" not in st.session_state:
    df = pending["df"]
    asked: list[dict] = []

    def collect(row_info, value, kind, candidates, used_hits):
        asked.append({"row_info": row_info, "value": value, "kind": kind,
                      "candidates": candidates, "used_hits": used_hits})
        return ("unresolved", None)

    with st.spinner("Recomputing the battery..."):
        first = audit(EXTRACTED, df, resolve=collect)
    st.session_state["collisions"] = asked

    if not asked:
        st.session_state["report"] = first
        st.session_state.pop("pending", None)
        st.rerun()

    st.info(f"{len(asked)} published value(s) could not be assigned to exactly "
            "one computed statistic. Choose the true source for each; anything "
            "left unresolved is reported as a collision, never guessed.")

    def options_for(q):
        labels = ["— not resolved —"]
        payloads = [("unresolved", None)]
        for c in q["candidates"]:
            labels.append(f"{c['description']} = {c['value']:.4g}")
            payloads.append(("match", c))
        for u in q["used_hits"]:
            labels.append(f"[already used] {u['description']} = {u['value']:.4g}")
            payloads.append(("restate", u))
        return labels + ["skip", "skip the rest"], payloads + [
            ("skip", None), ("skip_all", None)]

    chosen: list[str] = []
    with st.form("resolve_form"):
        for i, q in enumerate(asked):
            st.markdown(f"**{q['row_info']['row']}** ({q['row_info']['column']})"
                        f" = {q['value']}  ·  kind: {q['kind']}")
            labels, _ = options_for(q)
            chosen.append(st.selectbox("source", labels, key=f"res_{i}",
                                       label_visibility="collapsed"))
        submitted = st.form_submit_button("Apply and finish the audit")

    if submitted:
        answers = [payloads[labels.index(pick)]
                   for q, pick in zip(asked, chosen)
                   for labels, payloads in [options_for(q)]]

        cursor = {"n": 0}

        def decide(row_info, value, kind, candidates, used_hits):
            n = cursor["n"]
            cursor["n"] = n + 1
            return answers[n] if n < len(answers) else ("unresolved", None)

        report = audit(EXTRACTED, df, resolve=decide)
        core.save_json(REPORT, report)
        st.session_state["report"] = report
        st.session_state.pop("pending", None)
        st.rerun()

# ---- results ----
report = st.session_state.get("report")
if report:
    s = report["summary"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("✅ MATCH", s["match"] + s["match_human_labeled"])
    c2.metric("🔁 RESTATED", s["match_restatement"])
    c3.metric("❌ NO MATCH", s["no_match"])
    c4.metric("⚠️ COLLISION", s["collision"])
    c5.metric("ℹ️ UNCHECKABLE", s["uncheckable"])
    st.caption(f"{s['skipped']} skipped · {s['total']} statistics judged. A NO "
               "MATCH means no computed statistic reproduced the published "
               "value — read the provenance before concluding the paper is "
               "wrong.")

    st.subheader("Verdicts")
    rows = [{"#": i, "statistic": r.get("row", ""),
             "column": r.get("column", ""), "value": r.get("value"),
             "verdict": r["verdict"],
             "provenance": (f"{r['matches'][0]['item']} → "
                            f"{r['matches'][0]['computed_from']}"
                            if r.get("matches") else "")}
            for i, r in enumerate(report["results"])]
    green = "background-color:#d1fae5"
    colors = {"MATCH": green, "MATCH (restatement)": green,
              "MATCH (human-selected)": green, "MATCH (human-labeled)": green,
              "NO MATCH IN BATTERY": "background-color:#fee2e2",
              "UNRESOLVED COLLISION": "background-color:#fef3c7"}
    st.dataframe(pd.DataFrame(rows).style.map(lambda v: colors.get(v, ""),
                                              subset=["verdict"]),
                 use_container_width=True, hide_index=True)

    d1, d2 = st.columns(2)
    d1.download_button("⬇ audit report (JSON)",
                       json.dumps(report, ensure_ascii=False, indent=2),
                       "audit_report.json", use_container_width=True)
    d2.download_button("⬇ extracted values (JSON)", EXTRACTED.read_text(),
                       "extracted_values.json", use_container_width=True)

with st.expander("ℹ️ About and limitations"):
    st.markdown(open("README.md").read())
