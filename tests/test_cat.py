"""Tests for the `gdoc cat` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_cat
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


def _make_args(**overrides):
    """Build a SimpleNamespace mimicking parsed cat args."""
    defaults = {
        "command": "cat",
        "doc": "abc123",
        "plain": False,
        "comments": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCatMarkdown:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello World\n")
    def test_cat_default_markdown(self, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args()
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out == "# Hello World\n"
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="content")
    def test_cat_url_input(self, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(doc="https://docs.google.com/document/d/abc123/edit")
        rc = cmd_cat(args)
        assert rc == 0
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")


class TestCatPlain:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello World\n")
    def test_cat_plain(self, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(plain=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out == "Hello World\n"
        mock_export.assert_called_once_with("abc123", mime_type="text/plain")


class TestCatJson:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello")
    def test_cat_json_mode(self, mock_export, _mock_svc, _mock_pf, _mock_update, capsys):
        args = _make_args(json=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data == {"ok": True, "content": "# Hello"}


class TestCatComments:
    def test_cat_comments_stub(self, capsys):
        args = _make_args(comments=True)
        rc = cmd_cat(args)
        assert rc == 4
        err = capsys.readouterr().err
        assert "not yet implemented" in err


class TestCatErrors:
    def test_cat_invalid_doc_id(self):
        args = _make_args(doc="!!invalid!!")
        with pytest.raises(GdocError) as exc_info:
            cmd_cat(args)
        assert exc_info.value.exit_code == 3

    def test_cat_empty_doc_id(self):
        args = _make_args(doc="")
        with pytest.raises(GdocError) as exc_info:
            cmd_cat(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError("Document not found: abc"),
    )
    def test_cat_api_error(self, mock_export, _mock_svc, _mock_pf, _mock_update):
        args = _make_args()
        with pytest.raises(GdocError, match="Document not found"):
            cmd_cat(args)


class TestCatAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="content")
    def test_preflight_called_before_export(self, mock_export, _svc, mock_pf, mock_update):
        """pre_flight is called before export_doc."""
        mock_pf.return_value = ChangeInfo()
        args = _make_args()
        cmd_cat(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)
        mock_export.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="content")
    def test_quiet_skips_preflight(self, mock_export, _svc, mock_pf, mock_update):
        """--quiet passes quiet=True to pre_flight."""
        args = _make_args(quiet=True)
        cmd_cat(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="content")
    def test_state_updated_after_success(self, mock_export, _svc, mock_pf, mock_update):
        """State is updated after successful cat."""
        change_info = ChangeInfo(current_version=10)
        mock_pf.return_value = change_info
        args = _make_args()
        cmd_cat(args)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="cat", quiet=False,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="content")
    def test_state_updated_with_quiet(self, mock_export, _svc, mock_pf, mock_update):
        """State update under --quiet passes quiet=True and change_info=None."""
        mock_pf.return_value = None
        args = _make_args(quiet=True)
        cmd_cat(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="cat", quiet=True,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    def test_comments_stub_skips_preflight(self, mock_pf, mock_update):
        """--comments stub returns before pre_flight is called."""
        args = _make_args(comments=True)
        rc = cmd_cat(args)
        assert rc == 4
        mock_pf.assert_not_called()
        mock_update.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", side_effect=GdocError("API error"))
    def test_no_state_update_on_error(self, mock_export, _svc, mock_pf, mock_update):
        """State is NOT updated when export_doc raises an error."""
        mock_pf.return_value = ChangeInfo()
        args = _make_args()
        with pytest.raises(GdocError):
            cmd_cat(args)
        mock_update.assert_not_called()
