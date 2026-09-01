"""Configuration loading.

Shared by the CLI and the API server so both agree on where credentials come from.

Two decisions here are deliberate, and both were made after being bitten:

* **`.env` wins over the shell environment.** This is the opposite of python-dotenv's
  default, and the opposite of the usual convention -- but `.env` is the file the README
  tells you to put your key in, so a key sitting there must be the key that gets used. The
  default behaviour silently ignores it whenever a stale ``GEMINI_API_KEY`` is already
  exported, which is exactly what happened during development: a newly issued key sat
  unused in `.env` while every request was charged to an older one still in the shell.

* **``GOOGLE_API_KEY`` is cleared once ``GEMINI_API_KEY`` is known.** The Google SDK
  prefers ``GOOGLE_API_KEY`` when both are set and prints a warning saying so. Having two
  keys in play, with the SDK picking the one we did not intend, makes quota and auth
  failures very hard to attribute to the right credential.
"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def load_env(start: Path | None = None) -> Path | None:
    """Load the nearest ``.env`` and return the file used, if any.

    Idempotent: safe to call from both the CLI and the API server at import time.
    """
    global _LOADED
    if _LOADED:
        return None

    try:
        from dotenv import load_dotenv
    except ImportError:
        _LOADED = True
        return None

    here = Path(start or __file__).resolve()
    candidates = [
        Path.cwd() / ".env",
        here.parent.parent / ".env",  # repository root, when installed in place
    ]

    used: Path | None = None
    for candidate in candidates:
        if candidate.is_file():
            # override=True is the whole point; see the module docstring.
            load_dotenv(candidate, override=True)
            used = candidate
            break

    # Remove the ambiguity the SDK warns about, so the key we chose is the key that is used.
    if os.environ.get("GEMINI_API_KEY"):
        os.environ.pop("GOOGLE_API_KEY", None)

    _LOADED = True
    return used


def key_fingerprint() -> str:
    """A safe identifier for the active key, for logs and health checks."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return "none"
    return f"...{key.strip()[-6:]}"
