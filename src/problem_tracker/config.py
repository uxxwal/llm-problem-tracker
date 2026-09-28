"""config.py — environment loading and configuration checks.

Reads a local ``.env`` file (if present) into the environment, so the LLM
credentials do not have to be re-exported in every shell. The .env file is
gitignored and must never be committed.

Format (one KEY=value per line, # for comments):

    LLM_BASE_URL=https://api.openai.com/v1
    LLM_API_KEY=sk-...
    LLM_MODEL=gpt-4o-mini
"""

from __future__ import annotations

import os
from typing import List, Optional

__all__ = ["load_env", "missing_llm_config", "require_llm_config"]

REQUIRED = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


def load_env(path: str = ".env") -> bool:
    """Load KEY=value pairs from ``path`` into os.environ.

    Existing environment variables win, so an explicit ``export`` overrides the
    file. Returns True if a file was found and read.
    """
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    return True


def missing_llm_config() -> List[str]:
    """Return the names of any required LLM settings that are not set."""
    return [name for name in REQUIRED if not os.getenv(name)]


def require_llm_config() -> None:
    """Abort with a clear message if the LLM is not configured.

    Called at start-up by anything that will talk to a real model, so a
    misconfiguration fails immediately and loudly rather than silently
    degrading to a local default mid-session.
    """
    missing = missing_llm_config()
    if not missing:
        return
    raise SystemExit(
        "LLM is not configured — missing: " + ", ".join(missing) + "\n\n"
        "Set them in the shell:\n"
        '    export LLM_BASE_URL="https://api.openai.com/v1"\n'
        '    export LLM_API_KEY="sk-..."\n'
        '    export LLM_MODEL="gpt-4o-mini"\n\n'
        "or create a .env file in the project root (see .env.example).\n"
        "To try the interface without a model, use:  python study/app.py --dry-run"
    )
