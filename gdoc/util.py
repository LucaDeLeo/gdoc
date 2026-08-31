"""URL-to-ID extraction, error classes, and constants."""

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
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


class PreviewUnavailableError(GdocError):
    """A Docs API developer-preview feature isn't available to this caller.

    Raised when a preview-gated batchUpdate request (e.g. insertComment) is
    rejected because the Cloud project isn't enrolled in the Workspace
    Developer Preview Program, or the user's access level can't batchUpdate.
    Callers catch this to fall back to a generally-available code path; it
    should never surface to the user as a failure.
    """


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

# Multi-account support: when set, token is stored under a per-account dir.
# A ContextVar rather than a module global so a long-lived multi-account
# process (`gdoc mcp`, a hosted server) can scope the account per call,
# thread, or task; the CLI simply sets it once at startup.
_active_account: ContextVar[str | None] = ContextVar(
    "gdoc_active_account", default=None
)
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
    """Set the active account for the rest of the current context.

    Use account_context() instead to scope the account to a single call.
    """
    if account:
        _validate_account_name(account)
    _active_account.set(account)


def get_active_account() -> str | None:
    """Return the active account, if set."""
    return _active_account.get()


@contextmanager
def account_context(account: str | None):
    """Scope the active account to a with-block.

    Per-request credential injection for long-lived processes: the previous
    account is restored on exit even if the body called set_active_account()
    itself, so one call's account can never leak into the next.
    """
    if account:
        _validate_account_name(account)
    token = _active_account.set(account)
    try:
        yield
    finally:
        _active_account.reset(token)


def resolve_account() -> str | None:
    """The account name the current context's credentials resolve to.

    Explicit active account first, then the configured default; None means
    the legacy un-named token. Service caches key on this value, so an
    unpinned call in a long-lived process picks up a default-account change
    at call time.
    """
    return _active_account.get() or get_default_account()


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


_VALID_PAGE_MODES = ("pageless", "paged")


def get_default_page_mode() -> str | None:
    """Return the configured default page mode for docs created by `gdoc new`.

    'pageless' or 'paged' if the user set one via `gdoc config --page-mode`,
    else None — meaning "no explicit preference". A None result tells
    `gdoc new` to leave the doc as the create path produced it: blank docs
    inherit the account's page-mode default, and markdown-imported docs stay
    paged. Defaulting to None rather than 'paged' avoids silently overriding a
    pageless account default on the blank path.
    """
    mode = _load_config().get("page_mode")
    if mode in _VALID_PAGE_MODES:
        return mode
    return None


def set_default_page_mode(mode: str) -> None:
    """Set the default page mode used by `gdoc new` when no flag is given."""
    if mode not in _VALID_PAGE_MODES:
        raise GdocError(
            f"Invalid page mode: {mode!r}. Use 'pageless' or 'paged'.",
            exit_code=3,
        )
    config = _load_config()
    config["page_mode"] = mode
    _save_config(config)


def token_path_for(account: str | None) -> Path:
    """Token path for a resolved account name (None = legacy token)."""
    if account:
        return CONFIG_DIR / "accounts" / account / "token.json"
    return TOKEN_PATH


def get_token_path() -> Path:
    """Return the token path for the current context's account.

    Configured default accounts resolve to the named account token.
    CONFIG_DIR/token.json is only a legacy fallback.
    Named accounts use CONFIG_DIR/accounts/<account>/token.json.
    """
    return token_path_for(resolve_account())

_PATTERNS = [
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
    re.compile(r"/folders/([a-zA-Z0-9_-]+)"),
]

_BARE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

# Tab ids look like `t.0`, `t.abc123` — the dot distinguishes them from
# doc-id charsets, so it must be included in the capture class. `&amp;` is
# accepted alongside `?`/`&` because a URL copied out of rendered HTML keeps
# the escaped separator: failing to match there would silently downgrade a
# tab-scoped `edit`/`write` to the whole-doc path.
_TAB_PARAM = re.compile(r"(?:[?&]|&amp;)tab=([A-Za-z0-9._-]+)")


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


def extract_doc_ref(input_str: str) -> tuple[str, str | None]:
    """Extract (document ID, tab ID) from a URL or bare ID string.

    The tab ID is the value of a `?tab=`/`&tab=` query param, if present
    (Google's editor deep-links to a tab that way). A bare ID or a URL
    without the param yields a ``None`` tab.

    Raises ValueError if no valid ID can be extracted.
    """
    input_str = input_str.strip()

    if not input_str:
        raise ValueError("Cannot extract document ID from empty string")

    tab_match = _TAB_PARAM.search(input_str)
    tab_id = tab_match.group(1) if tab_match else None

    for pattern in _PATTERNS:
        match = pattern.search(input_str)
        if match:
            return match.group(1), tab_id

    if _BARE_ID.match(input_str):
        return input_str, None

    raise ValueError(f"Cannot extract document ID from: {input_str}")


def extract_doc_id(input_str: str) -> str:
    """Extract document ID from a URL or bare ID string.

    Accepts:
    - Full Google Docs URL: https://docs.google.com/document/d/ID/edit
    - Full Drive URL with query: https://drive.google.com/open?id=ID
    - Bare document ID: 1aBcDeFgHiJkLmNoPqRsTuVwXyZ

    Raises ValueError if no valid ID can be extracted.
    """
    return extract_doc_ref(input_str)[0]
