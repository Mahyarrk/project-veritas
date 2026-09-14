"""Debug 2: run the setup flow DIRECTLY (no subprocess, no main.py) to see
where it hangs — the hang is inside setup/ensure_setup, not main."""
import sys
sys.path.insert(0, '/Users/mahyar/projects/project-veritas')
import os

# fake the answers via input monkeypatch BEFORE importing setup
ANSWERS = iter([
    "http://localhost:20128/v1",       # chat url
    "ollama/gemma4:31b",               # chat model
    "sk-7a344da494687daa-9hd8gd-2cb52085",  # chat key
    "http://localhost:20128/v1",       # extract url
    "ollama/gemma4:31b",               # extract model
    "sk-7a344da494687daa-9hd8gd-2cb52085",  # extract key
    "http://localhost:1234/v1",        # embed url
    "text-embedding-nomic-embed-text-v1.5",  # embed model
    "none",                            # embed key
    "y",                               # save anyway? (only if failures)
])

import builtins
real_input = builtins.input
def fake_input(prompt=""):
    ans = next(ANSWERS)
    print(f"{prompt}{ans}")
    return ans
builtins.input = fake_input

import setup
data = setup.ensure_setup(force=True)
print("\nSETUP COMPLETE")
print("keys stored:", {r: list(data[r].keys()) for r in data})
setup.inject_env(data)
print("env injected: VERITAS_LLM_URL =", os.environ.get("VERITAS_LLM_URL"))
