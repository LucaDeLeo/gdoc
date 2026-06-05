"""URL-to-ID extraction, error classes, and constants."""

import json
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

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

TOKEN_PATH = CONFIG_DIR / "token.json"
CREDS_PATH = CONFIG_DIR / "credentials.json"
STATE_DIR = CONFIG_DIR / "state"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Multi-account support: when set, token is stored under a per-account dir
_active_account: str | None = None
_VALID_ACCOUNT = re.compile(r'^[\w.\-@]+$')


def _validate_account_name(account: str) -> None:
    """Validate account name to prevent path traversal."""
    if not _VALID_ACCOUNT.match(account):
        raise GdocError(
            f"Invalid account name: {account!r}. "
            "Use alphanumeric characters, dots, hyphens, underscores, or @.",
            exit_code=3,
        )


def set_active_account(account: str | None) -> None:
    """Set the active account for token resolution."""
    global _active_account
    if account:
        _validate_account_name(account)
    _active_account = account


def get_active_account() -> str | None:
    """Return the active account, if set."""
    return _active_account


def _load_config() -> dict:
    """Load gdoc config with defensive fallback for invalid JSON."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _save_config(config: dict) -> None:
    """Save gdoc config."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")


def get_default_account() -> str | None:
    """Return the configured default named account, if any."""
    account = _load_config().get("default_account")
    if isinstance(account, str) and account:
        _validate_account_name(account)
        return account
    return None


def set_default_account(account: str) -> None:
    """Set the default named account used when --account is omitted."""
    _validate_account_name(account)
    config = _load_config()
    config["default_account"] = account
    _save_config(config)


def get_token_path() -> Path:
    """Return the token path for the active account.

    Configured default accounts resolve to the named account token.
    CONFIG_DIR/token.json is only a legacy fallback.
    Named accounts use CONFIG_DIR/accounts/<account>/token.json.
    """
    if _active_account:
        return CONFIG_DIR / "accounts" / _active_account / "token.json"
    default_account = get_default_account()
    if default_account:
        return CONFIG_DIR / "accounts" / default_account / "token.json"
    return TOKEN_PATH

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


# Smart quotes / dashes -> ASCII. Each entry maps one char to exactly one
# char, so the fold is length-preserving and an index map built on the
# original text stays valid after folding (used by --normalize matching).
# Escapes (not literals) keep the source free of ambiguous Unicode (RUF001).
_TYPOGRAPHY_FOLD = str.maketrans({
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote / apostrophe
    "\u201c": '"',  # left double quote
    "\u201d": '"',  # right double quote
    "\u2032": "'",  # prime
    "\u2033": '"',  # double prime
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
})


def fold_typography(s: str) -> str:
    """Fold smart quotes and en/em dashes to their ASCII equivalents.

    Length-preserving (1:1 per character) so a smart-quote apostrophe
    (U+2019) matches an ASCII apostrophe in a search anchor without
    disturbing any character-index mapping.
    """
    return s.translate(_TYPOGRAPHY_FOLD)


def build_doc_url(doc_id: str, tab_id: str | None = None) -> str:
    """Build a Google Docs URL, optionally pointing at a specific tab."""
    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    if tab_id:
        url += f"?tab={tab_id}"
    return url


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
