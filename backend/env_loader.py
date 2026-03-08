"""
Environment loader for local development.

Loads repo-level and backend-level env files before modules read os.getenv.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_LOADED = False


def load_project_env() -> None:
    global _LOADED
    if _LOADED:
        return

    backend_dir = Path(__file__).resolve().parent
    repo_root = backend_dir.parent

    candidates = [
        repo_root / ".env.local",
        backend_dir / ".env",
        repo_root / "frontend" / ".env.local",
    ]

    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)

    # Let shell-provided env always win.
    for key, value in os.environ.items():
        os.environ[key] = value

    _LOADED = True
