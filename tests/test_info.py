"""Tests for the `gdoc info` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_info
from gdoc.util import GdocError

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


class TestInfoTerse:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_terse_output(self, mock_info, mock_export, _mock_svc, capsys):
        args = _make_args()
        rc = cmd_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Title: Test Document" in out
        assert "Owner: Alice" in out
        assert "Modified: 2025-01-15" in out
        assert "Words: 3" in out

    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_terse_date_truncated(self, mock_info, mock_export, _mock_svc, capsys):
        args = _make_args()
        cmd_info(args)
        out = capsys.readouterr().out
        # Should be YYYY-MM-DD (10 chars), not full ISO
        for line in out.splitlines():
            if line.startswith("Modified:"):
                date_value = line.split(":", 1)[1].strip()
                assert len(date_value) == 10
                break
        else:
            pytest.fail("Modified line not found in output")


class TestInfoVerbose:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_verbose_output(self, mock_info, mock_export, _mock_svc, capsys):
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

    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="some words")
    @patch("gdoc.api.drive.get_file_info")
    def test_info_verbose_missing_size(self, mock_info, mock_export, _mock_svc, capsys):
        meta = {k: v for k, v in MOCK_METADATA.items() if k != "size"}
        mock_info.return_value = meta
        args = _make_args(verbose=True)
        cmd_info(args)
        out = capsys.readouterr().out
        assert "Size: N/A" in out


class TestInfoJson:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_json_output(self, mock_info, mock_export, _mock_svc, capsys):
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

    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello world content")
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_json_word_count_type(self, mock_info, mock_export, _mock_svc, capsys):
        args = _make_args(json=True)
        cmd_info(args)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data["words"], int)


class TestInfoOwnerFallback:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="word")
    @patch("gdoc.api.drive.get_file_info")
    def test_info_owner_email_fallback(self, mock_info, mock_export, _mock_svc, capsys):
        meta = dict(MOCK_METADATA)
        meta["owners"] = [{"emailAddress": "alice@example.com"}]
        mock_info.return_value = meta
        args = _make_args()
        cmd_info(args)
        out = capsys.readouterr().out
        assert "Owner: alice@example.com" in out

    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="word")
    @patch("gdoc.api.drive.get_file_info")
    def test_info_owner_unknown(self, mock_info, mock_export, _mock_svc, capsys):
        meta = dict(MOCK_METADATA)
        meta["owners"] = []
        mock_info.return_value = meta
        args = _make_args()
        cmd_info(args)
        out = capsys.readouterr().out
        assert "Owner: Unknown" in out


class TestInfoNonExportable:
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError(
            "Cannot export file as markdown: file is not a Google Docs editor document"
        ),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_non_exportable_shows_na(self, mock_info, mock_export, _mock_svc, capsys):
        args = _make_args()
        rc = cmd_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Title: Test Document" in out
        assert "Words: N/A" in out

    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError(
            "Cannot export file as markdown: file is not a Google Docs editor document"
        ),
    )
    @patch("gdoc.api.drive.get_file_info", return_value=MOCK_METADATA)
    def test_info_non_exportable_json(self, mock_info, mock_export, _mock_svc, capsys):
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

    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        side_effect=GdocError("Document not found: abc123"),
    )
    def test_info_api_error(self, mock_info, _mock_svc):
        args = _make_args()
        with pytest.raises(GdocError, match="Document not found"):
            cmd_info(args)
