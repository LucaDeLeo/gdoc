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
        "all": False,
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
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    def test_cat_comments_calls_list_with_anchor(
        self, mock_export, _svc, mock_list, _csvc, _pf, _update
    ):
        args = _make_args(comments=True, quiet=True)
        rc = cmd_cat(args)
        assert rc == 0
        mock_list.assert_called_once_with(
            "abc123", include_resolved=False, include_anchor=True,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    def test_cat_comments_all_includes_resolved(
        self, mock_export, _svc, mock_list, _csvc, _pf, _update
    ):
        args = _make_args(comments=True, quiet=True, **{"all": True})
        rc = cmd_cat(args)
        assert rc == 0
        mock_list.assert_called_once_with(
            "abc123", include_resolved=True, include_anchor=True,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Some content here\n")
    def test_cat_comments_output_annotated(
        self, mock_export, _svc, mock_list, _csvc, _pf, _update, capsys
    ):
        mock_list.return_value = [{
            "id": "c1",
            "content": "Nice",
            "author": {"emailAddress": "alice@co.com"},
            "resolved": False,
            "createdTime": "2025-06-15T10:00:00Z",
            "quotedFileContent": {"value": "Some content"},
            "replies": [],
        }]
        args = _make_args(comments=True, quiet=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "     1\t" in out
        assert "[#c1 open]" in out
        assert 'on "Some content"' in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    def test_cat_comments_json_output(
        self, mock_export, _svc, mock_list, _csvc, _pf, _update, capsys
    ):
        args = _make_args(comments=True, json=True, quiet=True)
        rc = cmd_cat(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert "content" in data

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    def test_cat_comments_no_stub_exit_code(
        self, mock_export, _svc, mock_list, _csvc, _pf, _update
    ):
        args = _make_args(comments=True, quiet=True)
        rc = cmd_cat(args)
        assert rc == 0  # not 4 (stub)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    def test_cat_comments_state_update(
        self, mock_export, _svc, mock_list, _csvc, _pf, mock_update
    ):
        args = _make_args(comments=True, quiet=True)
        cmd_cat(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="cat", quiet=True,
        )


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
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    def test_comments_calls_preflight(
        self, _export, _svc, _list, _csvc, mock_pf, mock_update
    ):
        """--comments calls pre_flight and update_state_after_command."""
        args = _make_args(comments=True, quiet=True)
        rc = cmd_cat(args)
        assert rc == 0
        mock_pf.assert_called_once()
        mock_update.assert_called_once()

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
