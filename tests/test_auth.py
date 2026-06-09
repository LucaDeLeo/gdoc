"""Tests for gdoc.auth: OAuth2 flow, token management, and credential storage."""

import contextlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gdoc.auth import (
    _load_token,
    _save_token,
    authenticate,
    configure_default_account,
    get_credentials,
    list_accounts,
)
from gdoc.util import (
    AuthError,
    get_default_account,
    get_token_path,
    set_active_account,
    set_default_account,
)

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


class TestAuthenticate:
    def test_missing_credentials_json(self, tmp_path):
        fake_creds = tmp_path / "credentials.json"
        with patch("gdoc.auth.CREDS_PATH", fake_creds):
            with pytest.raises(AuthError, match="credentials.json not found"):
                authenticate()

    def test_browser_flow(self, tmp_path):
        fake_creds = tmp_path / "credentials.json"
        fake_creds.write_text("{}")
        fake_token = tmp_path / "token.json"

        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test"}'
        mock_flow.run_local_server.return_value = mock_creds

        with (
            patch("gdoc.auth.CREDS_PATH", fake_creds),
            patch("gdoc.auth.TOKEN_PATH", fake_token),
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.util.TOKEN_PATH", fake_token),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_config",
                return_value=mock_flow,
            ),
        ):
            result = authenticate(no_browser=False)

        assert result is mock_creds
        mock_flow.run_local_server.assert_called_once_with(port=0)

    def test_headless_flow(self, tmp_path):
        fake_creds = tmp_path / "credentials.json"
        fake_creds.write_text("{}")
        fake_token = tmp_path / "token.json"

        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test"}'
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?...",
            "state",
        )
        mock_flow.credentials = mock_creds

        with (
            patch("gdoc.auth.CREDS_PATH", fake_creds),
            patch("gdoc.auth.TOKEN_PATH", fake_token),
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.util.TOKEN_PATH", fake_token),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_config",
                return_value=mock_flow,
            ),
            patch(
                "builtins.input",
                return_value="http://localhost:1/?code=test-auth-code&scope=test",
            ),
        ):
            result = authenticate(no_browser=True)

        assert result is mock_creds
        assert mock_flow.redirect_uri == "http://localhost:1"
        mock_flow.authorization_url.assert_called_once_with(prompt="consent")
        mock_flow.fetch_token.assert_called_once_with(code="test-auth-code")
        mock_flow.run_local_server.assert_not_called()


    def test_headless_flow_fetch_token_error(self, tmp_path):
        fake_creds = tmp_path / "credentials.json"
        fake_creds.write_text("{}")
        fake_token = tmp_path / "token.json"

        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?...",
            "state",
        )
        mock_flow.fetch_token.side_effect = Exception("invalid_grant")

        with (
            patch("gdoc.auth.CREDS_PATH", fake_creds),
            patch("gdoc.auth.TOKEN_PATH", fake_token),
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.util.TOKEN_PATH", fake_token),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_config",
                return_value=mock_flow,
            ),
            patch(
                "builtins.input",
                return_value="http://localhost:1/?code=bad-code&scope=test",
            ),
        ):
            with pytest.raises(
                AuthError, match="Failed to exchange authorization code"
            ):
                authenticate(no_browser=True)

    def test_named_auth_sets_default_account_when_missing(self, tmp_path):
        fake_creds = tmp_path / "credentials.json"
        fake_creds.write_text("{}")
        fake_config = tmp_path / "config.json"

        mock_flow = MagicMock()
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test"}'
        mock_flow.run_local_server.return_value = mock_creds

        with (
            patch("gdoc.auth.CREDS_PATH", fake_creds),
            patch("gdoc.util.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_PATH", fake_config),
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_config",
                return_value=mock_flow,
            ),
        ):
            set_active_account("pete@example.com")
            try:
                authenticate(no_browser=False)
                assert get_default_account() == "pete@example.com"
            finally:
                set_active_account(None)

        assert (tmp_path / "accounts" / "pete@example.com" / "token.json").exists()
        mock_flow.run_local_server.assert_called_once_with(
            port=0, login_hint="pete@example.com"
        )


CLIENT_FILE_JSON = (
    '{"installed": {"client_id": "abc.apps.googleusercontent.com",'
    ' "client_secret": "xyz",'
    ' "auth_uri": "https://accounts.google.com/o/oauth2/auth",'
    ' "token_uri": "https://oauth2.googleapis.com/token"}}'
)


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@contextlib.contextmanager
def _flow_patches(tmp_path, mock_flow):
    """Patch config paths and the OAuth flow; yields the from_client_config mock."""
    with contextlib.ExitStack() as stack:
        for p in (
            patch("gdoc.auth.CREDS_PATH", tmp_path / "credentials.json"),
            patch("gdoc.auth.TOKEN_PATH", tmp_path / "token.json"),
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.util.TOKEN_PATH", tmp_path / "token.json"),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
        ):
            stack.enter_context(p)
        mock_factory = stack.enter_context(
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_config",
                return_value=mock_flow,
            )
        )
        yield mock_factory


def _mock_flow():
    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "test"}'
    mock_flow.run_local_server.return_value = mock_creds
    return mock_flow


class TestClientConfigSources:
    def test_env_pair_builds_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GDOC_CLIENT_ID", "env-id")
        monkeypatch.setenv("GDOC_CLIENT_SECRET", "env-secret")
        mock_flow = _mock_flow()

        with _flow_patches(tmp_path, mock_flow) as mock_factory:
            authenticate()
            config = mock_factory.call_args[0][0]

        assert config["installed"]["client_id"] == "env-id"
        assert config["installed"]["client_secret"] == "env-secret"

    def test_env_credentials_path(self, tmp_path, monkeypatch):
        client_file = tmp_path / "org-client.json"
        client_file.write_text(CLIENT_FILE_JSON)
        monkeypatch.setenv("GDOC_CLIENT_CREDENTIALS", str(client_file))
        mock_flow = _mock_flow()

        with _flow_patches(tmp_path, mock_flow) as mock_factory:
            authenticate()
            config = mock_factory.call_args[0][0]

        assert config["installed"]["client_id"] == "abc.apps.googleusercontent.com"

    def test_env_credentials_path_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GDOC_CLIENT_CREDENTIALS", str(tmp_path / "nope.json"))

        with patch("gdoc.auth.CREDS_PATH", tmp_path / "credentials.json"):
            with pytest.raises(AuthError, match="does not exist"):
                authenticate()

    def test_setup_url_fetches_and_saves(self, tmp_path, monkeypatch):
        mock_flow = _mock_flow()
        fake_creds = tmp_path / "credentials.json"

        with (
            _flow_patches(tmp_path, mock_flow),
            patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(CLIENT_FILE_JSON.encode()),
            ) as mock_urlopen,
        ):
            authenticate(setup_url="https://internal.example.com/gdoc.json")

        mock_urlopen.assert_called_once()
        assert fake_creds.exists()
        assert oct(os.stat(fake_creds).st_mode & 0o777) == "0o600"

    def test_setup_url_rejects_non_client_json(self, tmp_path):
        with (
            patch("gdoc.auth.CREDS_PATH", tmp_path / "credentials.json"),
            patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(b'{"foo": 1}'),
            ),
        ):
            with pytest.raises(AuthError, match="does not look like"):
                authenticate(setup_url="https://internal.example.com/gdoc.json")

    def test_setup_url_fetch_error(self, tmp_path):
        with (
            patch("gdoc.auth.CREDS_PATH", tmp_path / "credentials.json"),
            patch("urllib.request.urlopen", side_effect=OSError("conn refused")),
        ):
            with pytest.raises(AuthError, match="Failed to fetch"):
                authenticate(setup_url="https://internal.example.com/gdoc.json")

    def test_env_setup_url_used_when_no_creds(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GDOC_SETUP_URL", "https://internal.example.com/gdoc.json")
        mock_flow = _mock_flow()

        with (
            _flow_patches(tmp_path, mock_flow),
            patch(
                "urllib.request.urlopen",
                return_value=_FakeResponse(CLIENT_FILE_JSON.encode()),
            ) as mock_urlopen,
        ):
            authenticate()

        mock_urlopen.assert_called_once()
        assert (tmp_path / "credentials.json").exists()

    def test_env_setup_url_ignored_when_creds_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GDOC_SETUP_URL", "https://internal.example.com/gdoc.json")
        (tmp_path / "credentials.json").write_text(CLIENT_FILE_JSON)
        mock_flow = _mock_flow()

        with (
            _flow_patches(tmp_path, mock_flow),
            patch("urllib.request.urlopen") as mock_urlopen,
        ):
            authenticate()

        mock_urlopen.assert_not_called()


class TestAuthHints:
    def test_domain_flag_passes_hd(self, tmp_path):
        (tmp_path / "credentials.json").write_text(CLIENT_FILE_JSON)
        mock_flow = _mock_flow()

        with _flow_patches(tmp_path, mock_flow):
            authenticate(domain="company.com")

        mock_flow.run_local_server.assert_called_once_with(port=0, hd="company.com")

    def test_domain_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GDOC_AUTH_DOMAIN", "company.com")
        (tmp_path / "credentials.json").write_text(CLIENT_FILE_JSON)
        mock_flow = _mock_flow()

        with _flow_patches(tmp_path, mock_flow):
            authenticate()

        mock_flow.run_local_server.assert_called_once_with(port=0, hd="company.com")

    def test_headless_flow_includes_hints(self, tmp_path):
        (tmp_path / "credentials.json").write_text(CLIENT_FILE_JSON)
        mock_flow = _mock_flow()
        mock_flow.authorization_url.return_value = ("https://auth", "state")
        mock_flow.credentials = mock_flow.run_local_server.return_value

        with (
            _flow_patches(tmp_path, mock_flow),
            patch("builtins.input", return_value="http://localhost:1/?code=c&scope=s"),
        ):
            authenticate(no_browser=True, domain="company.com")

        mock_flow.authorization_url.assert_called_once_with(
            prompt="consent", hd="company.com"
        )


class TestGetCredentials:
    def test_valid_cached_token(self):
        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch("gdoc.auth._load_token", return_value=mock_creds):
            result = get_credentials()

        assert result is mock_creds

    def test_refreshes_expired_token(self):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh-xxx"
        mock_creds.to_json.return_value = '{"token": "refreshed"}'

        with (
            patch("gdoc.auth._load_token", return_value=mock_creds),
            patch("gdoc.auth._save_token") as mock_save,
            patch("gdoc.auth.Request"),
        ):
            result = get_credentials()

        assert result is mock_creds
        mock_creds.refresh.assert_called_once()
        mock_save.assert_called_once()
        assert mock_save.call_args[0][0] is mock_creds

    def test_raises_when_not_authenticated(self):
        with patch("gdoc.auth._load_token", return_value=None):
            with pytest.raises(AuthError, match="Not authenticated"):
                get_credentials()

    def test_raises_when_refresh_fails(self):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh-xxx"
        mock_creds.refresh.side_effect = Exception("revoked")

        with (
            patch("gdoc.auth._load_token", return_value=mock_creds),
            patch("gdoc.auth.Request"),
        ):
            with pytest.raises(AuthError, match="Not authenticated"):
                get_credentials()


class TestDefaultAccount:
    def test_configured_default_resolves_to_named_token(self, tmp_path):
        with (
            patch("gdoc.util.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
            patch("gdoc.util.TOKEN_PATH", tmp_path / "token.json"),
        ):
            set_active_account(None)
            set_default_account("pete@example.com")

            assert get_token_path() == (
                tmp_path / "accounts" / "pete@example.com" / "token.json"
            )

    def test_explicit_account_overrides_configured_default(self, tmp_path):
        with (
            patch("gdoc.util.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
            patch("gdoc.util.TOKEN_PATH", tmp_path / "token.json"),
        ):
            set_default_account("pete@example.com")
            set_active_account("work@example.com")
            try:
                assert get_token_path() == (
                    tmp_path / "accounts" / "work@example.com" / "token.json"
                )
            finally:
                set_active_account(None)

    def test_can_configure_default_to_existing_named_account(self, tmp_path):
        account_token = tmp_path / "accounts" / "pete@example.com" / "token.json"
        account_token.parent.mkdir(parents=True)
        account_token.write_text("{}")

        with (
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
        ):
            configure_default_account("pete@example.com")

            assert get_default_account() == "pete@example.com"

    def test_configure_default_requires_existing_named_account(self, tmp_path):
        with (
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
        ):
            with pytest.raises(AuthError, match="No credentials found"):
                configure_default_account("missing@example.com")

    def test_list_accounts_shows_configured_default_as_alias(self, tmp_path):
        legacy_token = tmp_path / "token.json"
        legacy_token.write_text("{}")
        account_token = tmp_path / "accounts" / "pete@example.com" / "token.json"
        account_token.parent.mkdir(parents=True)
        account_token.write_text("{}")

        with (
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch("gdoc.auth.TOKEN_PATH", legacy_token),
            patch("gdoc.util.CONFIG_DIR", tmp_path),
            patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"),
        ):
            set_default_account("pete@example.com")

            assert list_accounts() == [
                "default -> pete@example.com",
                "pete@example.com",
            ]


class TestLoadToken:
    def test_missing_file(self, tmp_path):
        fake_token = tmp_path / "token.json"
        assert _load_token(fake_token) is None

    def test_corrupt_json(self, tmp_path):
        fake_token = tmp_path / "token.json"
        fake_token.write_text("not valid json{{{")

        result = _load_token(fake_token)

        assert result is None
        assert not fake_token.exists()

    def test_missing_fields(self, tmp_path):
        fake_token = tmp_path / "token.json"
        fake_token.write_text('{"client_id": "x"}')

        with patch(
            "gdoc.auth.Credentials.from_authorized_user_file",
            side_effect=ValueError("missing fields"),
        ):
            result = _load_token(fake_token)

        assert result is None


class TestSaveToken:
    def test_saves_with_restricted_permissions(self, tmp_path):
        fake_token = tmp_path / "token.json"
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test"}'

        _save_token(mock_creds, fake_token)

        assert fake_token.exists()
        assert oct(os.stat(fake_token).st_mode & 0o777) == "0o600"

    def test_saves_atomically_with_restricted_permissions(self, tmp_path):
        """Token file is created with 0o600 from the start (no chmod race)."""
        fake_token = tmp_path / "token.json"
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test"}'

        original_open = os.open

        def spy_open(path, flags, mode=0o777):
            fd = original_open(path, flags, mode)
            # Verify mode is restrictive at creation time
            stat = os.fstat(fd)
            assert oct(stat.st_mode & 0o777) == "0o600"
            return fd

        with patch("gdoc.auth.os.open", side_effect=spy_open):
            _save_token(mock_creds, fake_token)


class TestCmdAuthIntegration:
    def test_exit_code_2_on_missing_creds(self, tmp_path):
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        for var in (
            "GDOC_CLIENT_ID",
            "GDOC_CLIENT_SECRET",
            "GDOC_CLIENT_CREDENTIALS",
            "GDOC_SETUP_URL",
        ):
            env.pop(var, None)

        result = subprocess.run(
            [sys.executable, "-m", "gdoc", "auth"],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO_ROOT,
        )

        assert result.returncode == 2
        assert "credentials.json not found" in result.stderr
