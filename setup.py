"""
setup.py — first-run endpoint setup. Runs BEFORE any other import.

The user's LLM setup is theirs: different people run different models on
different endpoints (local LM Studio, a hosted provider, anything
OpenAI-compatible). This module asks once, verifies the answer with a live
connection test, stores it in ~/.veritas/setup.json, and injects the
values as environment variables — so config.yaml's own endpoint values
become fallbacks, never requirements.

Design rule (user-directed): the model is assigned BEFORE anything starts.
No module that talks to an LLM may be imported until setup is complete.
main.py imports setup and calls ensure_setup() first; only then does it
import chat/extract/etc. Chat mode cannot change the model mid-session —
that breaks the system.

Stored file (~/.veritas/setup.json) holds:
  llm_url, llm_model            (RAG chat)
  extract_url, extract_key, extract_model   (audit/extraction)
  embed_url, embed_model        (retrieval)
Keys are stored in the user's home directory, NOT in the repo.
"""

import json
import os
from pathlib import Path

SETUP_PATH = Path.home() / ".veritas" / "setup.json"

# The three roles the user must fill. Each is one OpenAI-compatible
# endpoint: base_url + api_key + model.
ROLES = ["chat", "extract", "embed"]

# Map our roles to the env-var names ingest.py's override system reads.
# (llm->chat role; cloudflare->extract role; embeddings->embed role)
ENV_MAP = {
    "chat": {
        "url": "VERITAS_LLM_URL",
        "model": "VERITAS_LLM_MODEL",
        "key": "VERITAS_LLM_KEY",
    },
    "extract": {
        "url": "VERITAS_CLOUDFLARE_URL",
        "model": "VERITAS_CLOUDFLARE_MODEL",
        "key": "VERITAS_CLOUDFLARE_KEY",
    },
    "embed": {
        "url": "VERITAS_EMBEDDINGS_URL",
        "model": "VERITAS_EMBEDDINGS_MODEL",
        "key": "VERITAS_EMBEDDINGS_KEY",
    },
}


def load_setup() -> dict | None:
    """Stored setup, or None if first run."""
    if SETUP_PATH.exists():
        data = json.loads(SETUP_PATH.read_text())
        if all(r in data for r in ROLES):
            return data
    return None


def save_setup(data: dict) -> None:
    SETUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETUP_PATH.write_text(json.dumps(data, indent=2))
    os.chmod(SETUP_PATH, 0o600)   # contains API keys


def inject_env(data: dict) -> None:
    """Push stored values into the environment (ingest.py reads these).
    Must run BEFORE importing ingest/chat/extract."""
    for role in ROLES:
        for field, env_name in ENV_MAP[role].items():
            if data.get(role, {}).get(field):
                os.environ[env_name] = data[role][field]


def _ask(role: str, label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  {label}{suffix}: ").strip()
    return val or default


def _test_connection(url: str, key: str, model: str) -> tuple[bool, str]:
    """One live call, 15s timeout. Returns (ok, message). No silent failure."""
    from openai import OpenAI
    try:
        c = OpenAI(base_url=url, api_key=key or "none",
                   timeout=15.0, max_retries=0)
        r = c.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        return True, f"responded: {r.choices[0].message.content[:30]!r}"
    except Exception as exc:   # noqa: BLE001 — report any failure honestly
        return False, f"{exc.__class__.__name__}: {str(exc)[:120]}"


def ensure_setup(force: bool = False) -> dict:
    """Entry point: returns working setup dict, asking interactively if
    needed. Call before importing ingest/chat/extract."""
    data = None if force else load_setup()
    if data:
        inject_env(data)
        print(f"setup: loaded from {SETUP_PATH}")
        return data

    print("=" * 60)
    print("First-run setup — your LLM endpoints (OpenAI-compatible)")
    print("=" * 60)
    print("project-veritas needs three endpoints. Any provider works:\n"
          "local (LM Studio, Ollama) or hosted (OpenRouter, etc.).\n")

    data = {r: {} for r in ROLES}

    # chat (RAG) — often local, key optional
    print("-- 1/3 CHAT model (RAG conversation) --")
    data["chat"]["url"] = _ask("chat", "base URL",
                               "http://localhost:1234/v1")
    data["chat"]["model"] = _ask("chat", "model name")
    data["chat"]["key"] = _ask("chat", "API key (empty if local)",
                               "")

    # extract (audit) — the workhorse
    print("\n-- 2/3 EXTRACTION model (the auditor's workhorse) --")
    def_ext = data["chat"]["url"]   # same endpoint is a common default
    data["extract"]["url"] = _ask("extract", "base URL", def_ext)
    data["extract"]["model"] = _ask("extract", "model name")
    data["extract"]["key"] = _ask("extract", "API key (empty if local)",
                                  data["chat"]["key"])

    # embed — retrieval
    print("\n-- 3/3 EMBEDDING model (retrieval for RAG) --")
    def_emb_url = "http://localhost:1234/v1"
    data["embed"]["url"] = _ask("embed", "base URL", def_emb_url)
    data["embed"]["model"] = _ask("embed", "model name",
                                  "text-embedding-nomic-embed-text-v1.5")
    data["embed"]["key"] = _ask("embed", "API key (empty if local)",
                                "")

    # live verification of all three — before anything is saved
    print("\nverifying endpoints...")
    from openai import OpenAI
    all_ok = True
    for role in ROLES:
        ok, msg = _test_connection(data[role]["url"],
                                   data[role]["key"],
                                   data[role]["model"])
        print(f"  {role}: {'OK' if ok else 'FAILED'} — {msg}")
        all_ok = all_ok and ok
    if not all_ok:
        retry = input("\nsome endpoints failed. Save anyway and continue? "
                      "[y/N]: ").strip().lower()
        if retry != "y":
            print("setup aborted. Rerun to try again.")
            sys_exit = __import__("sys")
            sys_exit.exit(1)

    save_setup(data)
    inject_env(data)
    print(f"setup saved: {SETUP_PATH}")
    return data


def reconfigure() -> dict:
    """Force the setup prompts again (GUI settings panel / --setup flag)."""
    return ensure_setup(force=True)
