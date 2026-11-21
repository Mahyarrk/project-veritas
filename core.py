"""
core.py — the one place that reads config, builds clients and loads files.

Every other module imports from here. There is exactly one config load, one
OpenAI client factory, one paper loader and one dataset loader in the
project; anything that needs them calls this module.

Endpoints: config.yaml is the default, and VERITAS_<PROVIDER>_URL / _MODEL /
_KEY override it at call time. The key has no config field on purpose —
nothing secret lives in the repo.
"""

import csv
import json
import os
import zipfile
from pathlib import Path

import pandas as pd
import yaml
from openai import OpenAI

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text())


def setting(provider: str, key: str):
    """Config value for a provider block, with env override.

    The override name follows config.yaml's documented convention:
    VERITAS_<PROVIDER>_URL / _MODEL / _KEY — note URL, not BASE_URL, so the
    variable a user exports on the command line is the one documented.
    """
    name = {"base_url": "URL"}.get(key, key.upper())
    env = os.environ.get(f"VERITAS_{provider.upper()}_{name}")
    return env if env else CONFIG[provider][key]


def client(provider: str = "extract") -> OpenAI:
    """OpenAI-compatible client. Extraction is the only slow path: 120 s."""
    return OpenAI(
        base_url=setting(provider, "base_url"),
        api_key=os.environ.get(f"VERITAS_{provider.upper()}_KEY") or "none",
        timeout=120.0 if provider == "extract" else 15.0,
        max_retries=0,   # the SDK's retry loop re-queues 429s for minutes
    )


def model(provider: str = "extract") -> str:
    return setting(provider, "model")


def load_paper(path: Path) -> str:
    """Plain text from a txt/docx paper. PDF is unsupported by design:
    PDF extraction scrambles tables, an unacceptable failure for an auditor."""
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == "docx":
        import docx   # python-docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    raise ValueError(f"unsupported paper format: {suffix}")


def chunk_text(text: str) -> list[dict]:
    """Overlapping chunks with char offsets, so provenance survives chunking."""
    size = CONFIG["retrieval"]["chunk_size"]
    step = size - CONFIG["retrieval"]["chunk_overlap"]
    chunks = []
    for i, start in enumerate(range(0, len(text), step)):
        piece = text[start:start + size]
        if piece.strip():
            chunks.append({"offset": start, "index": i, "text": piece})
        if start + size >= len(text):
            break
    return chunks


def load_dataframe(path: Path) -> pd.DataFrame:
    """Read a csv/xlsx dataset, refusing the shapes that silently corrupt
    statistics: ragged rows (pandas pads them with NaN) and encrypted xlsx."""
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "csv":
        try:
            df = pd.read_csv(path)
        except UnicodeDecodeError as exc:
            raise ValueError(f"not UTF-8 text ({exc}); re-save as CSV UTF-8") from exc
        except pd.errors.EmptyDataError as exc:
            raise ValueError("the CSV file is empty (no rows found)") from exc
        except pd.errors.ParserError as exc:
            raise ValueError(f"CSV structure could not be parsed ({exc})") from exc
        with open(path, encoding="utf-8", errors="replace") as fh:
            width = len(next(csv.reader([fh.readline()])))
            ragged = sum(1 for line in fh
                         if line.strip() and len(next(csv.reader([line]))) != width)
        if ragged:
            raise ValueError(
                f"{ragged} row(s) differ in column count from the header "
                f"({width}); silent padding would corrupt the statistics")
        return df
    if suffix == "xlsx":
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise ValueError(f"not a readable xlsx ({exc})") from exc
        if "EncryptionInfo" in names or "EncryptedPackage" in names:
            raise ValueError("this xlsx is password-protected; remove the password")
        return pd.read_excel(path)
    raise ValueError(f"unsupported data format: {suffix}")


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
