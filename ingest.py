"""
ingest.py — load documents and data files into the store.

Papers (txt/docx) are converted to text, split into overlapping chunks,
and embedded via the configured OpenAI-compatible embeddings endpoint.
Data files (csv/xlsx) are registered with their schema (they are audited
via pandas, not chunked). The store (store.json) is the single artifact
every other module reads.

PDF is deliberately unsupported: PDF text extraction scrambles tables,
which is an unacceptable failure mode for an accuracy-first auditor.
Users convert PDFs to docx/txt themselves.

Run:  uv run ingest.py sources/my_paper.txt
      uv run ingest.py sources/data.xlsx
"""

import csv
import json
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import yaml
from openai import APIConnectionError, OpenAI

CONFIG = yaml.safe_load(Path("config.yaml").read_text())


def _endpoint(provider: str, key: str = "base_url") -> str:
    """Config value with env override.

    Precedence: VERITAS_<PROVIDER>_<KEY> env var > config.yaml value.
    This is how the CLI (and later the GUI) re-point endpoints without
    editing config.yaml — e.g. VERITAS_EXTRACT_MODEL overrides the
    cloudflare block's model.
    """
    env_prefix = f"VERITAS_{provider.upper()}_"
    env_key = env_prefix + key.upper()
    if os.environ.get(env_key):
        return os.environ[env_key]
    return CONFIG[provider][key]


def client(provider: str = "llm") -> OpenAI:
    """OpenAI-compatible client for a provider block in config.yaml,
    with env overrides (see _endpoint).

    provider "llm"        -> chat model (RAG mode); local LM Studio works
    provider "cloudflare" -> extraction/audit model; key from env
    provider "embeddings" -> embedding model for retrieval
    """
    base_url = _endpoint(provider, "base_url")
    model = _endpoint(provider, "model")   # honored by callers via model()
    api_key_env = CONFIG[provider].get("api_key_env", "")
    api_key = os.environ.get(api_key_env, "")
    env_key = f"VERITAS_{provider.upper()}_KEY"
    if os.environ.get(env_key):
        api_key = os.environ[env_key]
    if not api_key or api_key == "none":
        api_key = "none"   # local endpoints ignore the key; cloud needs one
    # Timeout rule: fail fast, never hang. Measured workloads:
    #   ping/extraction JSON: seconds (but dense table chunks on 9router
    #     legitimately run 20-40s — big JSON output from a big table);
    #   chat RAG answers: ~22s on local gemma (4 chunks + generation).
    # So: chat 120s, extraction 120s (user-directed: 2× the previous 60s —
    # slow 9router moments must not kill an otherwise-working audit),
    # everything else 15s. max_retries=0: the SDK's default retry loop
    # otherwise re-queues a 429'd request for minutes (observed: 9router).
    timeout = {"llm": 120.0, "cloudflare": 120.0}.get(provider, 15.0)
    return OpenAI(base_url=base_url, api_key=api_key,
                  timeout=timeout, max_retries=0)


def model(provider: str = "llm") -> str:
    """Model name for a provider, with env override."""
    return _endpoint(provider, "model")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Get one embedding vector per text via the embeddings endpoint."""
    c = client("embeddings")
    resp = c.embeddings.create(
        model=model("embeddings"),
        input=texts,
    )
    return [item.embedding for item in resp.data]


def load_paper(path: Path) -> str:
    """Extract plain text from a paper in txt/docx format."""
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == "docx":
        import docx  # python-docx
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError(f"unsupported paper format: {suffix}")


def chunk_text(text: str) -> list[dict]:
    """Split text into overlapping chunks, keeping char offsets for provenance."""
    size = CONFIG["retrieval"]["chunk_size"]
    overlap = CONFIG["retrieval"]["chunk_overlap"]
    step = size - overlap
    chunks = []
    for i, start in enumerate(range(0, len(text), step)):
        piece = text[start:start + size]
        if piece.strip():
            chunks.append({"offset": start, "index": i, "text": piece})
        if start + size >= len(text):
            break
    return chunks


def describe_dataset(path: Path) -> dict:
    """Schema summary of a csv/xlsx dataset — audited via pandas, not RAG."""
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "csv":
        try:
            df = pd.read_csv(path)
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"could not decode as UTF-8 text: {exc}. "
                "Re-save the CSV as UTF-8 (Excel: Save As → CSV UTF-8)."
            ) from exc
        except pd.errors.EmptyDataError as exc:
            raise ValueError("the CSV file is empty (no rows found).") from exc
        except pd.errors.ParserError as exc:
            raise ValueError(
                f"CSV structure could not be parsed ({exc}). "
                "Check that every row has the same number of columns."
            ) from exc
        # ragged rows (too few fields) are silently padded with NaN by pandas —
        # a data-quality hazard for the auditor, so detect and reject them.
        with open(path, encoding="utf-8", errors="replace") as fh:
            header_cols = len(next(csv.reader([fh.readline()])))
            ragged = sum(
                1 for line in fh
                if line.strip() and len(next(csv.reader([line]))) != header_cols
            )
        if ragged:
            raise ValueError(
                f"{ragged} row(s) have a different number of columns than the "
                f"header ({header_cols}). Fix the CSV rows before auditing — "
                "silent padding would corrupt the statistics."
            )
    elif suffix == "xlsx":
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise ValueError(
                f"not a readable xlsx file ({exc}). The file may be corrupt "
                "or actually a different format renamed to .xlsx."
            ) from exc
        if "EncryptionInfo" in names or "EncryptedPackage" in names:
            raise ValueError(
                "this xlsx is password-protected (encrypted). Remove the "
                "password (File → Info → Protect Workbook) or provide an "
                "unprotected copy."
            )
        df = pd.read_excel(path)
    else:
        raise ValueError(f"unsupported data format: {suffix}")
    return {
        "source": path.name,
        "kind": "data",
        "shape": list(df.shape),
        "columns": list(df.columns.astype(str)),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
    }


def save_store(store_path: Path, store: dict) -> None:
    """Persist the store (called after every successful file — crash-safe)."""
    store_path.write_text(json.dumps(store))


def process_file(path: Path, store: dict) -> bool:
    """Ingest one file. Returns True if ingested, False if skipped/failed."""
    suffix = path.suffix.lower().lstrip(".")
    try:
        if suffix in CONFIG["paper"]["formats"]:
            text = load_paper(path)
            chunks = chunk_text(text)
            vectors = embed_texts([c["text"] for c in chunks])
            for c, v in zip(chunks, vectors):
                c["vector"] = v
            store["chunks"].extend({**c, "source": path.name} for c in chunks)
            store["documents"].append({
                "source": path.name, "kind": "paper", "n_chars": len(text)
            })
            print(f"paper  : {path.name} — {len(text)} chars → {len(chunks)} chunks")
            return True

        if suffix in CONFIG["data"]["formats"]:
            store["datasets"].append(describe_dataset(path))
            print(f"data   : {path.name} — registered (schema in store)")
            return True

        print(f"skip (unsupported format '{suffix}') — supported: "
              f"{CONFIG['paper']['formats']} papers, {CONFIG['data']['formats']} data "
              f"(PDF unsupported: convert to docx/txt)")
        return False

    except APIConnectionError as exc:
        print(f"ERROR: {path.name} — cannot reach the OpenAI-compatible API "
              f"at {CONFIG['llm']['base_url']} ({exc.__class__.__name__})")
        print("       The server is down, unreachable, or refusing connections.")
        print("       Fix: start your local server (e.g. LM Studio: Developer → "
              "Start Server), check the base_url in config.yaml, and retry.")
        return False
    except ConnectionError:
        print("ERROR: cannot reach the OpenAI-compatible API at "
              f"{CONFIG['llm']['base_url']}")
        print("       Start your local server (e.g. LM Studio: Developer → "
              "Start Server) or point config.yaml at another OpenAI-compatible "
              "endpoint, then retry.")
        return False
    except ValueError as exc:
        print(f"ERROR: {path.name} — {exc}")
        print("       Fix the file or provide another one.")
        return False
    except Exception as exc:  # noqa: BLE001 — user-facing boundary, report cleanly
        print(f"ERROR: could not process {path.name}: {exc}")
        print("       Fix the file or provide another one.")
        return False


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: uv run ingest.py <file...>  (txt/docx/csv/xlsx)")

    store_path = Path(CONFIG["paths"]["store"])
    store = json.loads(store_path.read_text()) if store_path.exists() else {
        "documents": [], "chunks": [], "datasets": []
    }

    pending = [Path(a) for a in sys.argv[1:]]

    while pending:
        failed = []
        for path in pending:
            if not path.exists():
                print(f"skip (not found): {path}")
                failed.append(path)
                continue
            ok = process_file(path, store)
            if not ok:
                failed.append(path)
            save_store(store_path, store)  # incremental — crash-safe

        if not failed:
            break
        print(f"\n{len(failed)} file(s) not ingested.")
        try:
            retry = input("Path of a replacement file (Enter to continue without it): ").strip()
        except EOFError:
            # stdin closed (piped/scripted run) — behave as if Enter was pressed
            retry = ""
        if not retry:
            print("Continuing with what was ingested.")
            break
        pending = failed + [Path(retry)]

    print(f"\nstore: {store_path} — {len(store['chunks'])} chunks, "
          f"{len(store['datasets'])} datasets")


if __name__ == "__main__":
    main()