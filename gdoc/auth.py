"""OAuth2 flow, credential storage, and token refresh."""

import json
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gdoc.util import AuthError, CONFIG_DIR, CREDS_PATH, TOKEN_PATH

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def get_credentials() -> Credentials:
    """Load or refresh credentials. Returns valid Credentials or raises AuthError."""
    creds = _load_token()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception:
            pass

    raise AuthError("Not authenticated. Run `gdoc auth` to authenticate.")


def authenticate(no_browser: bool = False) -> Credentials:
    """Run the full OAuth2 flow. Called by `gdoc auth`."""
    if not CREDS_PATH.exists():
        raise AuthError(
            f"credentials.json not found at {CREDS_PATH}. "
            "Download it from Google Cloud Console and place it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)

    if no_browser:
        print(
            "Visit the URL below to authorize gdoc (copy-paste into browser):\n",
            file=sys.stderr,
        )
        creds = flow.run_local_server(port=0, open_browser=False)
    else:
        creds = flow.run_local_server(port=0)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _save_token(creds)
    print(
        f"OK authenticated successfully. Credentials stored in {TOKEN_PATH}",
        file=sys.stderr,
    )
    return creds


def _load_token() -> Credentials | None:
    """Load token.json with defensive error handling."""
    if not TOKEN_PATH.exists():
        return None

    try:
        return Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except (json.JSONDecodeError, ValueError, KeyError):
        print(
            "ERR: stored credentials are corrupt. "
            "Run `gdoc auth` to re-authenticate.",
            file=sys.stderr,
        )
        TOKEN_PATH.unlink(missing_ok=True)
        return None


def _save_token(creds: Credentials) -> None:
    """Save credentials to token.json with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)
