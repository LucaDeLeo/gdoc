"""Tests for the `gdoc edit` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from gdoc.cli import cmd_edit
from gdoc.notify import ChangeInfo
from gdoc.util import AuthError, GdocError


def _make_args(**overrides):
    """Build a SimpleNamespace mimicking parsed edit args."""
    defaults = {
        "command": "edit",
        "doc": "abc123",
        "old_text": "hello",
        "new_text": "world",
        "all": False,
        "case_sensitive": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _version_data(version=42):
    return {"version": version, "modifiedTime": "2026-01-01T00:00:00Z"}


class TestEditBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_single_match(self, _pf, _export, _drv, _replace, _docs, _ver, _update, capsys):
        args = _make_args()
        rc = cmd_edit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK replaced 1 occurrence" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_replaces_text(self, _pf, _export, _drv, mock_replace, _docs, _ver, _update):
        args = _make_args()
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", "hello", "world", match_case=False,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_case_sensitive(self, _pf, mock_export, _drv, mock_replace, _docs, _ver, _update):
        args = _make_args(old_text="Hello", case_sensitive=True)
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", "Hello", "world", match_case=True,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_url_input(self, _pf, mock_export, _drv, mock_replace, _docs, _ver, _update):
        args = _make_args(doc="https://docs.google.com/document/d/abc123/edit")
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", "hello", "world", match_case=False,
        )


class TestEditAll:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=5)
    @patch("gdoc.api.drive.export_doc")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_skips_precheck(self, _pf, mock_export, mock_replace, _docs, _ver, _update):
        args = _make_args(all=True)
        cmd_edit(args)
        mock_export.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=5)
    @patch("gdoc.api.drive.export_doc")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_multiple_matches(self, _pf, _export, mock_replace, _docs, _ver, _update, capsys):
        args = _make_args(all=True)
        rc = cmd_edit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK replaced 5 occurrences" in out

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=0)
    @patch("gdoc.api.drive.export_doc")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_zero_matches(self, _pf, _export, mock_replace, _docs):
        args = _make_args(all=True)
        with pytest.raises(GdocError, match="no match found") as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3


class TestEditPrecheck:
    @patch("gdoc.api.docs.replace_all_text")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="no match here")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_no_match_precheck(self, _pf, _export, _drv, mock_replace):
        args = _make_args(old_text="zzz")
        with pytest.raises(GdocError, match="no match found") as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3
        mock_replace.assert_not_called()

    @patch("gdoc.api.docs.replace_all_text")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello and hello and hello")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_multiple_matches_precheck(self, _pf, _export, _drv, mock_replace):
        args = _make_args(old_text="hello")
        with pytest.raises(GdocError, match=r"multiple matches \(3 found\). Use --all") as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3
        mock_replace.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_case_insensitive_precheck(self, _pf, _export, _drv, _replace, _docs, _ver, _update):
        """'Hello' in text, searching 'hello' case-insensitively → count=1."""
        args = _make_args(old_text="hello")
        rc = cmd_edit(args)
        assert rc == 0

    @patch("gdoc.api.docs.replace_all_text")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_case_sensitive_precheck_no_match(self, _pf, _export, _drv, mock_replace):
        """'Hello' in text, searching 'hello' case-sensitively → count=0."""
        args = _make_args(old_text="hello", case_sensitive=True)
        with pytest.raises(GdocError, match="no match found"):
            cmd_edit(args)
        mock_replace.assert_not_called()


class TestEditReconciliation:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=3)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_api_replaces_more_than_expected(self, _pf, _export, _drv, _replace, _docs, _ver, _update, capsys):
        args = _make_args()
        rc = cmd_edit(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARN: expected 1 match but API replaced 3" in err

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=0)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_api_zero_after_precheck(self, _pf, _export, _drv, _replace, _docs):
        args = _make_args()
        with pytest.raises(GdocError, match="no match found") as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3


class TestEditJson:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_json_output(self, _pf, _export, _drv, _replace, _docs, _ver, _update, capsys):
        args = _make_args(json=True)
        rc = cmd_edit(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "replaced": 1}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=3)
    @patch("gdoc.api.drive.export_doc")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_json_output(self, _pf, _export, _replace, _docs, _ver, _update, capsys):
        args = _make_args(all=True, json=True)
        rc = cmd_edit(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "replaced": 3}


class TestEditConflict:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight")
    def test_edit_conflict_warns_but_proceeds(self, mock_pf, _export, _drv, _replace, _docs, _ver, _update, capsys):
        change_info = ChangeInfo(current_version=10, last_read_version=5)
        mock_pf.return_value = change_info
        args = _make_args()
        rc = cmd_edit(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARN: doc changed since last read" in err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight")
    def test_edit_no_conflict_no_warning(self, mock_pf, _export, _drv, _replace, _docs, _ver, _update, capsys):
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args()
        rc = cmd_edit(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARN: doc changed since last read" not in err


class TestEditAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_preflight_called(self, mock_pf, _export, _drv, _replace, _docs, _ver, _update):
        args = _make_args()
        cmd_edit(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_quiet_skips_preflight(self, mock_pf, _export, _drv, _replace, _docs, _ver, _update):
        args = _make_args(quiet=True)
        cmd_edit(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data(42))
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", return_value=1)
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_state_updated_with_version(self, _pf, _export, _drv, _replace, _docs, _ver, mock_update):
        args = _make_args()
        cmd_edit(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="edit",
            quiet=False, command_version=42,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.replace_all_text")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="no match here")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_no_state_update_on_precheck_error(self, _pf, _export, _drv, _replace, mock_update):
        args = _make_args(old_text="zzz")
        with pytest.raises(GdocError):
            cmd_edit(args)
        mock_update.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", side_effect=GdocError("API error"))
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_no_state_update_on_api_error(self, _pf, _export, _drv, _replace, _docs, mock_update):
        args = _make_args()
        with pytest.raises(GdocError):
            cmd_edit(args)
        mock_update.assert_not_called()


class TestEditErrors:
    def test_edit_invalid_doc_id(self):
        args = _make_args(doc="!!invalid!!")
        with pytest.raises(GdocError) as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", side_effect=GdocError("Permission denied: abc123"))
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_api_permission_denied(self, _pf, _export, _drv, _replace, _docs):
        args = _make_args()
        with pytest.raises(GdocError, match="Permission denied"):
            cmd_edit(args)

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.replace_all_text", side_effect=AuthError("Authentication expired"))
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="hello there")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_api_auth_error(self, _pf, _export, _drv, _replace, _docs):
        args = _make_args()
        with pytest.raises(AuthError, match="Authentication expired"):
            cmd_edit(args)
