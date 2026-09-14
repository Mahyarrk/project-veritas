"""
gui.py — browser UI for project-veritas (Streamlit).

Page flow (user-directed design):
  1. ENDPOINT SETUP — the first page. URL/key/model for chat, extraction,
     and embedding endpoints. Required on every app start (nothing is
     persisted between program terminations, per the user's design: "this
     info is required each time the program is terminated"). A "test
     connection" button pings each endpoint; "Continue" injects the values
     via ingest's env-override system, then reveals the tabbed UI.
  2. TABS: Audit | Chat | About

Run:  uv run streamlit run gui.py
"""

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# ---- page config first ----
st.set_page_config(page_title="project-veritas", page_icon="🔬",
                   layout="wide")

CONFIG = __import__("yaml").safe_load(Path("config.yaml").read_text())


def endpoint_form() -> None:
    """First page: the three endpoints. Nothing else is reachable."""
    st.title("🔬 project-veritas")
    st.subheader("Endpoint setup")
    st.markdown(
        "Enter the LLM endpoints this session will use. Any "
        "OpenAI-compatible provider works — local (LM Studio, Ollama) or "
        "hosted. **These are required each time the app starts** and are "
        "never persisted to disk by this page.")
    st.caption("Keys are kept in the running process only (in-memory env "
               "injection), and are lost on app exit by design.")

    roles = [("chat", "💬 Chat model (RAG conversation)",
              "http://localhost:1234/v1", "", "google/gemma-4-e4b"),
             ("extract", "🧪 Extraction model (the auditor's workhorse)",
              "http://localhost:20128/v1",
              CONFIG["cloudflare"].get("api_key_env", ""),
              CONFIG["cloudflare"]["model"]),
             ("embed", "🧭 Embedding model (retrieval)",
              "http://localhost:1234/v1", "",
              CONFIG["embeddings"]["model"])]

    values = {}
    cols = st.columns(3)
    for i, (role, label, def_url, def_key_env, def_model) in enumerate(roles):
        with cols[i]:
            st.markdown(f"**{label}**")
            values[role] = {
                "url": st.text_input("Base URL", value=def_url,
                                     key=f"{role}_url"),
                "model": st.text_input("Model", value=def_model,
                                       key=f"{role}_model"),
                "key": st.text_input("API key", value=def_key_env,
                                     type="password", key=f"{role}_key"),
            }

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔎 Test connections", use_container_width=True):
            from openai import OpenAI
            for role in values:
                with st.spinner(f"testing {role}..."):
                    try:
                        c = OpenAI(base_url=values[role]["url"],
                                   api_key=values[role]["key"] or "none",
                                   timeout=15.0, max_retries=0)
                        r = c.chat.completions.create(
                            model=values[role]["model"],
                            messages=[{"role": "user", "content": "ping"}],
                            max_tokens=5)
                        st.success(f"{role}: OK — "
                                   f"{r.choices[0].message.content[:30]!r}")
                    except Exception as exc:   # noqa: BLE001
                        st.error(f"{role}: {type(exc).__name__} — "
                                 f"{str(exc)[:150]}")
    with c2:
        if st.button("✅ Save & continue", type="primary",
                     use_container_width=True):
            # inject via env (ingest.py override system reads these before
            # client creation)
            import os
            env_map = {"chat": ("VERITAS_LLM_URL", "VERITAS_LLM_MODEL",
                                "VERITAS_LLM_KEY"),
                       "extract": ("VERITAS_CLOUDFLARE_URL",
                                   "VERITAS_CLOUDFLARE_MODEL",
                                   "VERITAS_CLOUDFLARE_KEY"),
                       "embed": ("VERITAS_EMBEDDINGS_URL",
                                 "VERITAS_EMBEDDINGS_MODEL",
                                 "VERITAS_EMBEDDINGS_KEY")}
            for role, (u, m, k) in env_map.items():
                os.environ[u] = values[role]["url"]
                os.environ[m] = values[role]["model"]
                os.environ[k] = values[role]["key"]
            st.session_state["endpoints_ready"] = True
            st.session_state["endpoints"] = values
            st.rerun()


# ---- gate: endpoint page first ----
if not st.session_state.get("endpoints_ready"):
    endpoint_form()
    st.stop()

st.sidebar.title("🔬 project-veritas")
st.sidebar.success("Endpoints configured ✓")
if st.sidebar.button("Reconfigure endpoints"):
    st.session_state["endpoints_ready"] = False
    st.rerun()

# lazy imports AFTER endpoints are injected
from ingest import client, model   # noqa: E402

tab_audit, tab_chat, tab_about = st.tabs(["🧪 Audit", "💬 Chat", "ℹ️ About"])

# =====================================================================
# TAB 1: AUDIT
# =====================================================================
with tab_audit:
    st.header("Audit a paper")
    col1, col2 = st.columns(2)
    with col1:
        paper_file = st.file_uploader("Paper (txt/docx)", type=["txt", "docx"])
    with col2:
        data_file = st.file_uploader("Raw data (csv/xlsx)",
                                     type=["csv", "xlsx"])

    if st.button("▶ Run audit", type="primary", disabled=not paper_file,
                 use_container_width=True):
        if not data_file:
            st.warning("No data file — audit requires the raw dataset. "
                       "Chat tab remains available.")
        # persist uploads to disk (pipeline is file-based)
        sources = Path("sources")
        sources.mkdir(exist_ok=True)
        paper_path = sources / paper_file.name
        paper_path.write_bytes(paper_file.getvalue())
        data_path = None
        if data_file:
            data_path = sources / data_file.name
            data_path.write_bytes(data_file.getvalue())

        t0 = time.time()
        # ---- ingest + extract + verify, with progress ----
        import extract as ex
        from verify import audit, load_dataframe

        text = ex.load_paper(paper_path)
        text_norm, offset_map = ex._normalize_with_map(text)
        chunks = ex.chunk_text(text)
        status = st.status(f"Extracting from {len(chunks)} chunks...",
                           expanded=True)
        entries = []
        with status:
            for c in chunks:
                proposals, _ = ex._extract_recursive(c["text"])
                for p in proposals:
                    if isinstance(p, dict):
                        entry = ex._validate(p, text_norm, offset_map)
                        entry["paper"] = "paper"
                        entries.append(entry)
                status.update(label=f"chunk {c['index']}/{len(chunks)}: "
                              f"{len(entries)} entries so far")
        deduped = ex._dedupe(entries)
        status.update(label=f"extraction complete: {len(deduped)} statistics "
                      f"in {time.time()-t0:.0f}s", state="complete",
                      expanded=False)
        Path("extracted_values.json").write_text(
            json.dumps(deduped, ensure_ascii=False, indent=2))

        if data_path:
            df = pd.read_csv(data_path) if data_path.suffix == ".csv" \
                else pd.read_excel(data_path)
            with st.status("Verifying against raw data...", expanded=False):
                report = audit(Path("extracted_values.json"), df,
                               interactive=False)
            Path("audit_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2))
            st.session_state["report"] = report
        else:
            st.session_state.pop("report", None)

    report = st.session_state.get("report")
    if report:
        s = report["summary"]
        # metric row
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("✅ MATCH", s["match"])
        c2.metric("❌ NO MATCH", s["no_match"])
        c3.metric("⚠️ COLLISION", sum(
            1 for r in report["results"]
            if r["verdict"] == "UNRESOLVED COLLISION"))
        c4.metric("ℹ️ UNCHECKABLE", sum(
            1 for r in report["results"]
            if r["verdict"].startswith("UNCHECKABLE")))

        st.subheader("Verdicts")
        rows = []
        for i, r in enumerate(report["results"]):
            prov = ""
            if r.get("matches"):
                m = r["matches"][0]
                prov = f"{m['item']} (computed: {m['computed_from']})"
            rows.append({"#": i, "statistic": r.get("row", "?"),
                         "column": r.get("column", ""), "value": r.get("value"),
                         "verdict": r["verdict"], "provenance": prov})
        dfv = pd.DataFrame(rows)
        colors = {"MATCH": "background-color:#d1fae5",
                  "MATCH (restatement)": "background-color:#d1fae5",
                  "NO MATCH IN BATTERY": "background-color:#fee2e2",
                  "UNRESOLVED COLLISION": "background-color:#fef3c7"}
        styled = dfv.style.map(lambda v: colors.get(v, ""), subset=["verdict"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # download buttons
        d1, d2 = st.columns(2)
        d1.download_button("⬇ audit report (JSON)",
                           json.dumps(report, ensure_ascii=False, indent=2),
                           "audit_report.json", use_container_width=True)
        d2.download_button("⬇ extracted values (JSON)",
                           Path("extracted_values.json").read_text(),
                           "extracted_values.json", use_container_width=True)

        # resolution panel for collisions
        colls = [r for r in report["results"]
                 if r["verdict"] == "UNRESOLVED COLLISION"]
        if colls:
            st.subheader(f"Resolve {len(colls)} collisions")
            st.caption("One published value tied several battery entries. "
                       "Pick the true source for each.")
            with st.form("resolve_form"):
                picks = []
                for i, r in enumerate(colls):
                    st.markdown(f"**{r['row']}** ({r['column']}) = "
                                f"{r['value']}")
                    # find tying candidates from the battery
                    opts = [m["item"] for m in r.get("matches", [])] or \
                        ["(candidates not captured — rerun interactive)"]
                    choice = st.selectbox(f"candidate for #{i}", opts,
                                          key=f"res_{i}")
                    picks.append((i, r, choice))
                submitted = st.form_submit_button("Apply resolutions")
                if submitted:
                    # record user decisions into the report
                    for i, r, choice in picks:
                        r["verdict"] = "MATCH (user-resolved)"
                        r["matches"] = [{"item": choice,
                                         "computed_from": "(user-selected)",
                                         "battery_value": r["value"]}]
                    Path("audit_report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2))
                    st.success("collisions resolved — report updated")
                    st.rerun()

# =====================================================================
# TAB 2: CHAT
# =====================================================================
with tab_chat:
    st.header("Chat about the paper")
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []
    # entries: dicts {role, content, sources?} — sources survive reruns so
    # every answer stays source-checkable at any time.

    # --- scrollable message container (fixed height; input stays pinned) ---
    chat_container = st.container(height=520)
    with chat_container:
        for msg in st.session_state["chat_messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                for s in msg.get("sources", []):
                    with st.expander("sources"):
                        st.caption(f"[{s['source']}, offset {s['offset']}] "
                                   f"(score {s['score']:.2f})")
                        st.text(s["text"][:400])
        # auto-scroll to the newest message: an invisible anchor element
        # with a unique key per message count, scrolled into view by JS
        # injected only when a new message was just added this run.
        if st.session_state.get("_scroll_to_bottom"):
            st.markdown(
                """<script>
                const el = window.parent.document.querySelector(
                    '[data-testid="stVerticalBlockBorderWrapper"]:last-of-type'
                );
                if (el) el.scrollTop = el.scrollHeight;
                </script>""", unsafe_allow_html=True)
            st.session_state["_scroll_to_bottom"] = False

    # --- input pinned below the container ---
    if prompt := st.chat_input("Ask about the paper..."):
        st.session_state["chat_messages"].append(
            {"role": "user", "content": prompt})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)
        with st.spinner("retrieving..."):
            try:
                from chat import answer
                history = [(m["role"], m["content"])
                           for m in st.session_state["chat_messages"]]
                reply, sources_used = answer(prompt, history)
            except Exception as exc:   # noqa: BLE001
                reply = f"ERROR: {type(exc).__name__} — {str(exc)[:200]}"
                sources_used = []
        st.session_state["chat_messages"].append(
            {"role": "assistant", "content": reply,
             "sources": sources_used})
        st.session_state["_scroll_to_bottom"] = True
        st.rerun()   # redraw + auto-scroll to newest message

# =====================================================================
# TAB 3: ABOUT
# =====================================================================
with tab_about:
    st.header("About project-veritas")
    st.markdown(open("README.md").read())

# =====================================================================
# TAB 2: CHAT  (implementation detail)
# =====================================================================
