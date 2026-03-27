"""OAuth2 flow, credential storage, and token refresh."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gdoc.util import AuthError, CONFIG_DIR, CREDS_PATH, TOKEN_PATH, get_token_path

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def get_credentials() -> Credentials:
    """Load or refresh credentials. Returns valid Credentials or raises AuthError."""
    from gdoc.util import get_active_account
    account = get_active_account()
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

    from gdoc.util import get_active_account
    account = get_active_account()
    hint = f" (account: {account})" if account else ""
    raise AuthError(f"Not authenticated{hint}. Run `gdoc auth{' --account ' + account if account else ''}` to authenticate.")


def authenticate(no_browser: bool = False) -> Credentials:
    """Run the full OAuth2 flow. Called by `gdoc auth`."""
    if not CREDS_PATH.exists():
        raise AuthError(
            f"credentials.json not found at {CREDS_PATH}. "
            "Download it from Google Cloud Console and place it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)

    if no_browser:
        flow.redirect_uri = "http://localhost:1"
        auth_url, _ = flow.authorization_url(prompt="consent")
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
        creds = flow.run_local_server(port=0)

    token_path = get_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    _save_token(creds, token_path)
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

    Returns account names, with 'default' for the base token.
    """
    accounts = []
    if TOKEN_PATH.exists():
        accounts.append("default")
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
