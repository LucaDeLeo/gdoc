"""URL-to-ID extraction, error classes, and constants."""

import re
from pathlib import Path


class GdocError(Exception):
    """Base error for gdoc CLI operations."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class AuthError(GdocError):
    """Authentication error (exit code 2)."""

    def __init__(self, message: str):
        super().__init__(message, exit_code=2)


CONFIG_DIR = Path.home() / ".config" / "gdoc"
_OLD_CONFIG_DIR = Path.home() / ".gdoc"

# Migrate from ~/.gdoc to ~/.config/gdoc
if _OLD_CONFIG_DIR.is_dir() and not CONFIG_DIR.exists():
    CONFIG_DIR.parent.mkdir(parents=True, exist_ok=True)
    _OLD_CONFIG_DIR.rename(CONFIG_DIR)

TOKEN_PATH = CONFIG_DIR / "token.json"
CREDS_PATH = CONFIG_DIR / "credentials.json"
STATE_DIR = CONFIG_DIR / "state"

_PATTERNS = [
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
    re.compile(r"/folders/([a-zA-Z0-9_-]+)"),
]

_BARE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def confirm_destructive(message: str, force: bool = False) -> None:
    """Prompt for confirmation on destructive ops. Raises GdocError on decline."""
    if force:
        return
    import sys

    if not sys.stdin.isatty():
        raise GdocError(
            f"Refusing to {message} without --force (non-interactive)",
            exit_code=3,
        )
    print(f"{message} [y/N]: ", end="", file=sys.stderr, flush=True)
    answer = input().strip().lower()
    if answer not in ("y", "yes"):
        raise GdocError("Cancelled", exit_code=3)


def extract_doc_id(input_str: str) -> str:
    """Extract document ID from a URL or bare ID string.

    Accepts:
    - Full Google Docs URL: https://docs.google.com/document/d/ID/edit
    - Full Drive URL with query: https://drive.google.com/open?id=ID
    - Bare document ID: 1aBcDeFgHiJkLmNoPqRsTuVwXyZ

    Raises ValueError if no valid ID can be extracted.
    """
    input_str = input_str.strip()

    if not input_str:
        raise ValueError("Cannot extract document ID from empty string")

    for pattern in _PATTERNS:
        match = pattern.search(input_str)
        if match:
            return match.group(1)

    if _BARE_ID.match(input_str):
        return input_str

    raise ValueError(f"Cannot extract document ID from: {input_str}")
