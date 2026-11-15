"""
gui.py — browser UI for project-veritas (Streamlit).

Page flow (user-directed design):
  1. ENDPOINT SETUP — the first page. URL/key/model for chat, extraction,
     and embedding endpoints. Required on every app start: by default the
     values are NOT persisted between program terminations (the user's
     design: "this info is required each time the program is terminated").
     An opt-in "remember on this machine" checkbox persists them to
     ~/.veritas/setup.json (mode 0600) for anyone who would rather not
     retype a key each session.
     Every field starts EMPTY — nothing is prefilled. A "Check models"
     button asks the endpoint what it actually serves and turns the model
     field into a picker over that list (free text still allowed).
     "Save & continue" injects the values via ingest's env-override system,
     then reveals the tabbed UI.
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

# The three endpoint roles, using setup.py's ROLES vocabulary.
ROLE_SPEC = [
    ("chat",    "💬 Chat model (RAG conversation)"),
    ("extract", "🧪 Extraction model (the auditor's workhorse)"),
    ("embed",   "🧭 Embedding model (retrieval)"),
]

# env var names ingest.py reads (VERITAS_<PROVIDER>_*), keyed by role.
# These must match the config.yaml block names: llm / extract / embeddings.
ENV_MAP = {
    "chat":    ("VERITAS_LLM_URL", "VERITAS_LLM_MODEL", "VERITAS_LLM_KEY"),
    "extract": ("VERITAS_EXTRACT_URL", "VERITAS_EXTRACT_MODEL",
                "VERITAS_EXTRACT_KEY"),
    "embed":   ("VERITAS_EMBEDDINGS_URL", "VERITAS_EMBEDDINGS_MODEL",
                "VERITAS_EMBEDDINGS_KEY"),
}


def _client(url: str, key: str, timeout: float = 20.0):
    from openai import OpenAI
    return OpenAI(base_url=url, api_key=key or "none",
                  timeout=timeout, max_retries=0)


def list_models(url: str, key: str) -> tuple[list[str], str | None]:
    """Ask an OpenAI-compatible endpoint which models it serves.

    Returns (model_ids, error). Never raises: the caller shows the error.
    """
    if not url.strip():
        return [], "no base URL given"
    try:
        models = _client(url, key).models.list()
        ids = sorted(m.id for m in (getattr(models, "data", None) or []))
        if not ids:
            return [], "endpoint returned an empty model list"
        return ids, None
    except Exception as exc:                      # noqa: BLE001
        return [], f"{type(exc).__name__}: {str(exc)[:160]}"


def test_role(role: str, url: str, key: str, model_name: str) -> tuple[bool, str]:
    """Live-test one role with the RIGHT modality.

    chat/extract: a tiny chat completion.
    embed: an embeddings call. Testing an embedding model with
           chat.completions always fails, which is what made the old button
           report a false failure for every embedding endpoint.
    """
    if not str(model_name).strip():
        return False, "no model selected"
    try:
        c = _client(url, key)
        if role == "embed":
            r = c.embeddings.create(model=model_name, input=["ping"])
            return True, f"{len(r.data[0].embedding)}-dim vector"
        r = c.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5, temperature=0)
        msg = r.choices[0].message
        text = (msg.content or getattr(msg, "reasoning_content", "") or "")
        return True, f"responded: {text.strip()[:30]!r}"
    except Exception as exc:                      # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def _remember_path() -> Path:
    return Path.home() / ".veritas" / "setup.json"


def endpoint_form() -> None:
    """First page: the three endpoints. Nothing else is reachable."""
    st.title("🔬 project-veritas")
    st.subheader("Endpoint setup")
    st.markdown(
        "Enter the LLM endpoints this session will use. Any "
        "OpenAI-compatible provider works — local (LM Studio, Ollama) or "
        "hosted. **These are required each time the app starts** and are "
        "not written to disk unless you tick *remember on this machine*.")
    st.caption("Keys live in the running process only (in-memory env "
               "injection). Ticking the remember box stores them in "
               "~/.veritas/setup.json with owner-only permissions.")

    # ---- optional restore from a previous opted-in save ----
    saved = {}
    if _remember_path().exists():
        try:
            cand = json.loads(_remember_path().read_text())
            if isinstance(cand, dict):
                saved = cand
        except Exception:                         # noqa: BLE001
            saved = {}
    if saved and st.button("↩︎ Fill from remembered setup"):
        for role, _label in ROLE_SPEC:
            blk = saved.get(role) or {}
            st.session_state[f"{role}_url"] = blk.get("url", "")
            st.session_state[f"{role}_key"] = blk.get("key", "")
            st.session_state[f"{role}_model"] = blk.get("model", "")
            st.session_state.pop(f"{role}_models", None)
            st.session_state.pop(f"{role}_models_err", None)
        st.rerun()

    values = {}
    cols = st.columns(3)
    for i, (role, label) in enumerate(ROLE_SPEC):
        with cols[i]:
            st.markdown(f"**{label}**")
            # No value= or default anywhere: every field starts empty.
            url = st.text_input("Base URL", key=f"{role}_url",
                                placeholder="http://localhost:1234/v1")
            key = st.text_input("API key (blank if local)", key=f"{role}_key",
                                type="password", placeholder="sk-...")

            if st.button("🔍 Check models", key=f"{role}_check",
                         use_container_width=True):
                found, err = list_models(url, key)
                st.session_state[f"{role}_models"] = found
                st.session_state[f"{role}_models_err"] = err

            found = st.session_state.get(f"{role}_models") or []
            err = st.session_state.get(f"{role}_models_err")
            if err:
                st.error(f"model list failed — {err}")
            elif found:
                st.caption(f"{len(found)} model(s) found on this endpoint")

            # Picker over what the endpoint actually serves. Free text stays
            # available because some gateways hide their list.
            if found:
                model_name = st.selectbox(
                    "Model", found, key=f"{role}_model_pick",
                    accept_new_options=True,
                    help="Pick a model the endpoint reported, or type one.")
            else:
                model_name = st.text_input("Model", key=f"{role}_model",
                                           placeholder="press Check models")

            values[role] = {"url": url, "model": model_name, "key": key}

    st.divider()
    remember = st.checkbox(
        "Remember these endpoints on this machine (~/.veritas/setup.json)",
        value=False,
        help="Off by default. When on, the URL, key and model for each role "
             "are written to your home directory with owner-only "
             "permissions and offered back on the next start.")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔎 Test connections", use_container_width=True):
            for role, _label in ROLE_SPEC:
                v = values[role]
                with st.spinner(f"testing {role}..."):
                    ok, msg = test_role(role, v["url"], v["key"], v["model"])
                (st.success if ok else st.error)(f"{role}: {msg}")
    with c2:
        if st.button("✅ Save & continue", type="primary",
                     use_container_width=True):
            missing = [r for r, _ in ROLE_SPEC
                       if not values[r]["url"].strip()
                       or not str(values[r]["model"]).strip()]
            if missing:
                st.error("Fill the base URL and model for: "
                         + ", ".join(missing))
                st.stop()

            # inject via env (ingest.py override system reads these before
            # client creation). This is the only place values are applied.
            import os
            for role, (u, m, k) in ENV_MAP.items():
                v = values[role]
                os.environ[u] = v["url"].strip()
                os.environ[m] = str(v["model"]).strip()
                os.environ[k] = v["key"]

            if remember:
                p = _remember_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(values, indent=2))
                os.chmod(p, 0o600)
                st.toast(f"remembered in {p}")

            st.session_state["endpoints_ready"] = True
            st.session_state["endpoints"] = values
            st.rerun()


# ---- gate: endpoint page first ----
if not st.session_state.get("endpoints_ready"):
    endpoint_form()
    st.stop()

st.sidebar.title("🔬 project-veritas")
st.sidebar.success("Endpoints configured ✓")
for _role, _label in ROLE_SPEC:
    _v = (st.session_state.get("endpoints") or {}).get(_role) or {}
    if _v.get("model"):
        st.sidebar.caption(f"**{_role}** · {_v['model']}")
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
        st.rerun()   # redraw: message appears inside the scroll container

# =====================================================================
# TAB 3: ABOUT
# =====================================================================
with tab_about:
    st.header("About project-veritas")
    st.markdown(open("README.md").read())

# =====================================================================
# TAB 2: CHAT  (implementation detail)
# =====================================================================
