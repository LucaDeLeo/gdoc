"""Tests for the `gdoc info` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_info
from gdoc.notify import ChangeInfo
from gdoc.util import AuthError, GdocError

MOCK_METADATA = {
    "id": "abc123",
    "name": "Test Document",
    "mimeType": "application/vnd.google-apps.document",
    "modifiedTime": "2025-01-15T10:30:00.000Z",
    "createdTime": "2025-01-10T08:00:00.000Z",
    "owners": [{"displayName": "Alice", "emailAddress": "alice@example.com"}],
    "lastModifyingUser": {"displayName": "Bob", "emailAddress": "bob@example.com"},
    "size": "12345",
}


def _make_args(**overrides):
    """Build a SimpleNamespace mimicking parsed info args."""
    defaults = {
        "command": "info",
        "doc": "abc123",
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _sample_metadata():
    return {
        "name": "Test Doc",
        "owners": [{"emailAddress": "alice@co.com", "displayName": "Alice"}],
        "modifiedTime": "2025-01-20T14:30:00Z",
        "createdTime": "2025-01-15T10:00:00Z",
        "lastModifyingUser": {"emailAddress": "alice@co.com", "displayName": "Alice"},
        "mimeType": "application/vnd.google-apps.document",
        "size": None,
    }


class TestInfoTerse:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_terse_output(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args()
        rc = cmd_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Title: Test Document" in out
        assert "Owner: Alice" in out
        assert "Modified: 2025-01-15" in out
        assert "Words: 3" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_terse_date_truncated(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args()
        cmd_info(args)
        out = capsys.readouterr().out
        for line in out.splitlines():
            if line.startswith("Modified:"):
                date_value = line.split(":", 1)[1].strip()
                assert len(date_value) == 10
                break
        else:
            pytest.fail("Modified line not found in output")


class TestInfoVerbose:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_verbose_output(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(verbose=True)
        rc = cmd_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Title: Test Document" in out
        assert "Owner: Alice" in out
        assert "Created: 2025-01-10T08:00:00.000Z" in out
        assert "Last editor: Bob" in out
        assert "Type: application/vnd.google-apps.document" in out
        assert "Size: 12345" in out
        assert "Words: 3" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="some words")
    @patch("gdoc.api.drive.get_file_info")
    def test_info_verbose_missing_size(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        meta = {k: v for k, v in MOCK_METADATA.items() if k != "size"}
        mock_info.return_value = meta
        args = _make_args(verbose=True)
        cmd_info(args)
        out = capsys.readouterr().out
        assert "Size: N/A" in out


class TestInfoJson:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_json_output(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(json=True)
        rc = cmd_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert data["title"] == "Test Document"
        assert data["owner"] == "Alice"
        assert data["modified"] == "2025-01-15T10:30:00.000Z"
        assert data["words"] == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_json_word_count_type(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(json=True)
        cmd_info(args)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data["words"], int)


class TestInfoOwnerFallback:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="word")
    @patch("gdoc.api.drive.get_file_info")
    def test_info_owner_email_fallback(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        meta = dict(MOCK_METADATA)
        meta["owners"] = [{"emailAddress": "alice@example.com"}]
        mock_info.return_value = meta
        args = _make_args()
        cmd_info(args)
        out = capsys.readouterr().out
        assert "Owner: alice@example.com" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="word")
    @patch("gdoc.api.drive.get_file_info")
    def test_info_owner_unknown(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        meta = dict(MOCK_METADATA)
        meta["owners"] = []
        mock_info.return_value = meta
        args = _make_args()
        cmd_info(args)
        out = capsys.readouterr().out
        assert "Owner: Unknown" in out


class TestInfoNonExportable:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError(
            "Cannot export file as markdown: file is not a Google Docs editor document"
        ),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_non_exportable_shows_na(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args()
        rc = cmd_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Title: Test Document" in out
        assert "Words: N/A" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError(
            "Cannot export file as markdown: file is not a Google Docs editor document"
        ),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_non_exportable_json(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(json=True)
        rc = cmd_info(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["words"] == "N/A"


class TestInfoErrors:
    def test_info_invalid_doc_id(self):
        args = _make_args(doc="!!invalid!!")
        with pytest.raises(GdocError) as exc_info:
            cmd_info(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        side_effect=GdocError("Document not found: abc123"),
    )
    def test_info_api_error(self, mock_info, _mock_svc, _mock_pf, _mock_update):
        args = _make_args()
        with pytest.raises(GdocError, match="Document not found"):
            cmd_info(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError("Permission denied: abc123"),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_export_permission_error_propagates(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update):
        """Permission errors from export_doc should NOT be silently suppressed."""
        args = _make_args()
        with pytest.raises(GdocError, match="Permission denied"):
            cmd_info(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=AuthError("Authentication expired. Run `gdoc auth`."),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_export_auth_error_propagates(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update):
        """Auth errors from export_doc should NOT be silently suppressed."""
        args = _make_args()
        with pytest.raises(AuthError, match="Authentication expired"):
            cmd_info(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError("API error (500): Internal Server Error"),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_export_api_error_propagates(self, mock_info, mock_export, _mock_svc, _mock_pf, _mock_update):
        """Generic API errors from export_doc should NOT be silently suppressed."""
        args = _make_args()
        with pytest.raises(GdocError, match="API error"):
            cmd_info(args)


class TestInfoAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello world")
    @patch("gdoc.api.drive.get_file_info")
    def test_preflight_called(self, mock_info, mock_export, _svc, mock_pf, mock_update):
        mock_info.return_value = _sample_metadata()
        mock_pf.return_value = ChangeInfo()
        args = _make_args()
        cmd_info(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello world")
    @patch("gdoc.api.drive.get_file_info")
    def test_quiet_passes_through(self, mock_info, mock_export, _svc, mock_pf, mock_update):
        mock_info.return_value = _sample_metadata()
        args = _make_args(quiet=True)
        cmd_info(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello world")
    @patch("gdoc.api.drive.get_file_info")
    def test_state_updated_with_version(self, mock_info, mock_export, _svc, mock_pf, mock_update):
        """State update receives version from get_file_info response."""
        mock_info.return_value = {**_sample_metadata(), "version": 42}
        change_info = ChangeInfo()
        mock_pf.return_value = change_info
        args = _make_args()
        cmd_info(args)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="info",
            quiet=False, command_version=42,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello world")
    @patch("gdoc.api.drive.get_file_info")
    def test_quiet_info_still_gets_version(self, mock_info, mock_export, _svc, mock_pf, mock_update):
        """--quiet info still passes command_version from get_file_info (Decision #14)."""
        mock_info.return_value = {**_sample_metadata(), "version": 99}
        args = _make_args(quiet=True)
        cmd_info(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="info",
            quiet=True, command_version=99,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.get_file_info", side_effect=GdocError("not found"))
    def test_no_state_update_on_error(self, mock_info, _svc, mock_pf, mock_update):
        mock_pf.return_value = ChangeInfo()
        args = _make_args()
        with pytest.raises(GdocError):
            cmd_info(args)
        mock_update.assert_not_called()
