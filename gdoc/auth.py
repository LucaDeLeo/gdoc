"""OAuth2 flow, credential storage, and token refresh."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gdoc.util import (
    CONFIG_DIR,
    CREDS_PATH,
    TOKEN_PATH,
    AuthError,
    get_default_account,
    get_token_path,
    set_default_account,
)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def get_credentials() -> Credentials:
    """Load or refresh credentials. Returns valid Credentials or raises AuthError."""
    from gdoc.util import get_active_account
    account = get_active_account() or get_default_account()
    if not account:
        print("account: default (use --account to switch)", file=sys.stderr)

    token_path = get_token_path()
    creds = _load_token(token_path)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except Exception:
            pass

    hint = f" (account: {account})" if account else ""
    command_hint = f" --account {account}" if account else ""
    raise AuthError(
        f"Not authenticated{hint}. Run `gdoc auth{command_hint}` to authenticate."
    )


def _read_client_file(path: Path) -> dict:
    """Parse an OAuth client file, translating failures into AuthError."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise AuthError(f"Could not read OAuth client file {path}: {e}") from e


def _load_client_config() -> dict | None:
    """Resolve the OAuth client config from env vars or credentials.json.

    Order: GDOC_CLIENT_ID/GDOC_CLIENT_SECRET pair, GDOC_CLIENT_CREDENTIALS
    file path, then CREDS_PATH. Returns None when no source is configured.
    """
    client_id = os.environ.get("GDOC_CLIENT_ID")
    client_secret = os.environ.get("GDOC_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": GOOGLE_AUTH_URI,
                "token_uri": GOOGLE_TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        }

    env_path = os.environ.get("GDOC_CLIENT_CREDENTIALS")
    if env_path:
        path = Path(env_path).expanduser()
        if not path.exists():
            raise AuthError(
                f"GDOC_CLIENT_CREDENTIALS points to {path}, which does not exist."
            )
        return _read_client_file(path)

    if CREDS_PATH.exists():
        return _read_client_file(CREDS_PATH)

    return None


def _fetch_client_credentials(url: str) -> None:
    """Download the org's OAuth client file and store it at CREDS_PATH."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read()
    except Exception as e:
        raise AuthError(f"Failed to fetch client credentials from {url}: {e}") from e

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AuthError(f"Response from {url} is not valid JSON: {e}") from e

    section = parsed.get("installed") or parsed.get("web")
    if not isinstance(section, dict) or not section.get("client_id"):
        raise AuthError(
            f"Response from {url} does not look like a Google OAuth client file "
            "(expected an 'installed' section with a client_id)."
        )

    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CREDS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(raw.decode("utf-8"))
    print(f"OK client credentials saved to {CREDS_PATH}", file=sys.stderr)


def _auth_hints(domain: str | None) -> dict:
    """Build hd/login_hint kwargs for the authorization URL.

    hd pre-filters the Google account chooser to the Workspace domain; it is
    a UI hint, not enforcement — an Internal consent screen enforces domain.
    """
    from gdoc.util import get_active_account

    hints = {}
    domain = domain or os.environ.get("GDOC_AUTH_DOMAIN")
    if domain:
        hints["hd"] = domain
    account = get_active_account() or get_default_account()
    if account and "@" in account:
        hints["login_hint"] = account
    return hints


def authenticate(
    no_browser: bool = False,
    setup_url: str | None = None,
    domain: str | None = None,
) -> Credentials:
    """Run the full OAuth2 flow. Called by `gdoc auth`."""
    if setup_url:
        _fetch_client_credentials(setup_url)

    client_config = _load_client_config()
    if client_config is None and os.environ.get("GDOC_SETUP_URL"):
        _fetch_client_credentials(os.environ["GDOC_SETUP_URL"])
        client_config = _load_client_config()
    if client_config is None:
        raise AuthError(
            f"credentials.json not found at {CREDS_PATH}. Place your org's "
            "OAuth client file there, run `gdoc auth --setup-url <url>`, or "
            "set GDOC_CLIENT_ID and GDOC_CLIENT_SECRET."
        )

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    hints = _auth_hints(domain)

    if no_browser:
        flow.redirect_uri = "http://localhost:1"
        auth_url, _ = flow.authorization_url(prompt="consent", **hints)
        print(
            "Visit this URL to authorize gdoc:\n\n"
            f"{auth_url}\n\n"
            "After authorizing, paste the full redirect URL here:",
            file=sys.stderr,
        )
        redirect_response = input().strip()
        code = parse_qs(urlparse(redirect_response).query).get("code", [None])[0]
        if not code:
            code = redirect_response
        try:
            flow.fetch_token(code=code)
        except Exception as e:
            raise AuthError(f"Failed to exchange authorization code: {e}") from e
        creds = flow.credentials
    else:
        creds = flow.run_local_server(port=0, **hints)

    token_path = get_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    _save_token(creds, token_path)
    from gdoc.util import get_active_account
    account = get_active_account()
    if account and not get_default_account():
        set_default_account(account)
    print(
        f"OK authenticated successfully. Credentials stored in {token_path}",
        file=sys.stderr,
    )
    return creds


def _load_token(token_path: Path | None = None) -> Credentials | None:
    """Load token.json with defensive error handling."""
    if token_path is None:
        token_path = get_token_path()
    if not token_path.exists():
        return None

    try:
        return Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except (json.JSONDecodeError, ValueError, KeyError):
        print(
            "ERR: stored credentials are corrupt. "
            "Run `gdoc auth` to re-authenticate.",
            file=sys.stderr,
        )
        token_path.unlink(missing_ok=True)
        return None


def _save_token(creds: Credentials, token_path: Path | None = None) -> None:
    """Save credentials to token.json with restricted permissions."""
    if token_path is None:
        token_path = get_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def list_accounts() -> list[str]:
    """List all authenticated accounts.

    Returns account names. A configured default is shown as an alias to its named
    account; the base token is shown as a legacy default fallback.
    """
    accounts = []
    default_account = get_default_account()
    if default_account:
        accounts.append(f"default -> {default_account}")
    elif TOKEN_PATH.exists():
        accounts.append("default (legacy)")
    accounts_dir = CONFIG_DIR / "accounts"
    if accounts_dir.is_dir():
        for entry in sorted(accounts_dir.iterdir()):
            if entry.is_dir() and (entry / "token.json").exists():
                accounts.append(entry.name)
    return accounts


def remove_account(account: str) -> None:
    """Remove credentials for a named account."""
    from gdoc.util import _validate_account_name

    if account == "default":
        if not TOKEN_PATH.exists():
            raise AuthError("No default account credentials found.")
        TOKEN_PATH.unlink()
        print("OK removed default account credentials.", file=sys.stderr)
        return

    _validate_account_name(account)
    account_dir = CONFIG_DIR / "accounts" / account
    token = account_dir / "token.json"
    if not token.exists():
        raise AuthError(f"No credentials found for account: {account}")
    token.unlink()
    try:
        account_dir.rmdir()  # only succeeds if empty
    except OSError:
        pass
    print(f"OK removed credentials for account: {account}", file=sys.stderr)


def configure_default_account(account: str) -> None:
    """Configure the default account alias."""
    from gdoc.util import _validate_account_name

    _validate_account_name(account)
    token = CONFIG_DIR / "accounts" / account / "token.json"
    if not token.exists():
        raise AuthError(f"No credentials found for account: {account}")
    set_default_account(account)
    print(f"OK default account set to: {account}", file=sys.stderr)
