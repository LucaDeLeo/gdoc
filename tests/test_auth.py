"""Tests for gdoc.auth: OAuth2 flow, token management, and credential storage."""

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from gdoc.auth import _load_token, _save_token, authenticate, get_credentials
from gdoc.util import AuthError


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
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_secrets_file",
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
        mock_flow.run_local_server.return_value = mock_creds

        with (
            patch("gdoc.auth.CREDS_PATH", fake_creds),
            patch("gdoc.auth.TOKEN_PATH", fake_token),
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
            patch(
                "gdoc.auth.InstalledAppFlow.from_client_secrets_file",
                return_value=mock_flow,
            ),
        ):
            result = authenticate(no_browser=True)

        assert result is mock_creds
        mock_flow.run_local_server.assert_called_once_with(
            port=0, open_browser=False
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
        mock_save.assert_called_once_with(mock_creds)

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


class TestLoadToken:
    def test_missing_file(self, tmp_path):
        fake_token = tmp_path / "token.json"
        with patch("gdoc.auth.TOKEN_PATH", fake_token):
            assert _load_token() is None

    def test_corrupt_json(self, tmp_path):
        fake_token = tmp_path / "token.json"
        fake_token.write_text("not valid json{{{")

        with patch("gdoc.auth.TOKEN_PATH", fake_token):
            result = _load_token()

        assert result is None
        assert not fake_token.exists()

    def test_missing_fields(self, tmp_path):
        fake_token = tmp_path / "token.json"
        fake_token.write_text('{"client_id": "x"}')

        with (
            patch("gdoc.auth.TOKEN_PATH", fake_token),
            patch(
                "gdoc.auth.Credentials.from_authorized_user_file",
                side_effect=ValueError("missing fields"),
            ),
        ):
            result = _load_token()

        assert result is None


class TestSaveToken:
    def test_saves_with_restricted_permissions(self, tmp_path):
        fake_token = tmp_path / "token.json"
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "test"}'

        with (
            patch("gdoc.auth.TOKEN_PATH", fake_token),
            patch("gdoc.auth.CONFIG_DIR", tmp_path),
        ):
            _save_token(mock_creds)

        assert fake_token.exists()
        assert oct(os.stat(fake_token).st_mode & 0o777) == "0o600"


class TestCmdAuthIntegration:
    def test_exit_code_2_on_missing_creds(self, tmp_path):
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "gdoc", "auth"],
            capture_output=True,
            text=True,
            env=env,
            cwd="/Users/luca/dev/gdoc",
        )

        assert result.returncode == 2
        assert "credentials.json not found" in result.stderr
