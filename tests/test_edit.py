"""Tests for the `gdoc edit` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import build_parser, cmd_edit
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
        "plain": False,
        "quiet": False,
        "tab": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _version_data(version=42):
    return {"version": version, "modifiedTime": "2026-01-01T00:00:00Z"}


def _mock_doc(revision_id="rev123"):
    return {"revisionId": revision_id, "body": {"content": []}}


def _single_match():
    return [{"startIndex": 1, "endIndex": 6}]


def _multi_match(n=3):
    return [{"startIndex": i * 10, "endIndex": i * 10 + 5} for i in range(n)]


class TestEditBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_single_match(self, _pf, _doc, _find, _replace, _ver, _update, capsys):
        args = _make_args()
        rc = cmd_edit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK replaced 1 occurrence" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_calls_replace_formatted(
        self, _pf, _doc, _find, mock_replace, _ver, _update,
    ):
        args = _make_args()
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", _single_match(), "world", "rev123", tab_id=None,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_case_sensitive(self, _pf, _doc, mock_find, _replace, _ver, _update):
        args = _make_args(old_text="Hello", case_sensitive=True)
        cmd_edit(args)
        mock_find.assert_called_once()
        call_kwargs = mock_find.call_args
        assert call_kwargs[0][1] == "Hello"
        assert call_kwargs[1]["match_case"] is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_url_input(self, _pf, mock_doc, _find, mock_replace, _ver, _update):
        args = _make_args(doc="https://docs.google.com/document/d/abc123/edit")
        cmd_edit(args)
        mock_doc.assert_called_once_with("abc123")
        mock_replace.assert_called_once()


class TestEditAll:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=5)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_multi_match(5))
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_multiple_matches(
        self, _pf, _doc, _find, _replace, _ver, _update, capsys,
    ):
        args = _make_args(all=True)
        rc = cmd_edit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK replaced 5 occurrences" in out

    @patch("gdoc.api.docs.replace_formatted")
    @patch("gdoc.api.docs.find_text_in_document", return_value=[])
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_zero_matches(self, _pf, _doc, _find, mock_replace):
        args = _make_args(all=True)
        with pytest.raises(GdocError, match="no match found") as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3
        mock_replace.assert_not_called()


class TestEditPrecheck:
    @patch("gdoc.api.docs.replace_formatted")
    @patch("gdoc.api.docs.find_text_in_document", return_value=[])
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_no_match(self, _pf, _doc, _find, mock_replace):
        args = _make_args(old_text="zzz")
        with pytest.raises(GdocError, match="no match found") as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3
        mock_replace.assert_not_called()

    @patch("gdoc.api.docs.replace_formatted")
    @patch("gdoc.api.docs.find_text_in_document", return_value=_multi_match(3))
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_multiple_matches_without_all(self, _pf, _doc, _find, mock_replace):
        args = _make_args(old_text="hello")
        match = r"multiple matches \(3 found\). Use --all"
        with pytest.raises(GdocError, match=match) as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3
        mock_replace.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_case_insensitive_single_match(
        self, _pf, _doc, _find, _replace, _ver, _update,
    ):
        """Single match found case-insensitively → success."""
        args = _make_args(old_text="hello")
        rc = cmd_edit(args)
        assert rc == 0


class TestEditJson:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_json_output(self, _pf, _doc, _find, _replace, _ver, _update, capsys):
        args = _make_args(json=True)
        rc = cmd_edit(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "replaced": 1}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=3)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_multi_match(3))
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_json_output(
        self, _pf, _doc, _find, _replace, _ver, _update, capsys,
    ):
        args = _make_args(all=True, json=True)
        rc = cmd_edit(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "replaced": 3}


class TestEditConflict:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight")
    def test_edit_conflict_warns_but_proceeds(
        self, mock_pf, _doc, _find, _replace, _ver, _update, capsys,
    ):
        change_info = ChangeInfo(current_version=10, last_read_version=5)
        mock_pf.return_value = change_info
        args = _make_args()
        rc = cmd_edit(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARN: doc changed since last read" in err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight")
    def test_edit_no_conflict_no_warning(
        self, mock_pf, _doc, _find, _replace, _ver, _update, capsys,
    ):
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
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_preflight_called(self, mock_pf, _doc, _find, _replace, _ver, _update):
        args = _make_args()
        cmd_edit(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_quiet_skips_preflight(self, mock_pf, _doc, _find, _replace, _ver, _update):
        args = _make_args(quiet=True)
        cmd_edit(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data(42))
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_state_updated_with_version(
        self, _pf, _doc, _find, _replace, _ver, mock_update,
    ):
        args = _make_args()
        cmd_edit(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="edit",
            quiet=False, command_version=42,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.replace_formatted")
    @patch("gdoc.api.docs.find_text_in_document", return_value=[])
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_no_state_update_on_no_match(self, _pf, _doc, _find, _replace, mock_update):
        args = _make_args(old_text="zzz")
        with pytest.raises(GdocError):
            cmd_edit(args)
        mock_update.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.replace_formatted", side_effect=GdocError("API error"))
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_no_state_update_on_api_error(
        self, _pf, _doc, _find, _replace, mock_update,
    ):
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

    @patch("gdoc.api.docs.replace_formatted",
           side_effect=GdocError("Permission denied: abc123"))
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_api_permission_denied(self, _pf, _doc, _find, _replace):
        args = _make_args()
        with pytest.raises(GdocError, match="Permission denied"):
            cmd_edit(args)

    @patch("gdoc.api.docs.replace_formatted",
           side_effect=AuthError("Authentication expired"))
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_api_auth_error(self, _pf, _doc, _find, _replace):
        args = _make_args()
        with pytest.raises(AuthError, match="Authentication expired"):
            cmd_edit(args)

    @patch("gdoc.api.docs.get_document",
           side_effect=GdocError("Document not found: abc123"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_doc_not_found(self, _pf, _doc):
        args = _make_args()
        with pytest.raises(GdocError, match="Document not found"):
            cmd_edit(args)


class TestEditFileInput:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_file_flags_read_content(
        self, _pf, _doc, _find, mock_replace, _ver, _update, tmp_path,
    ):
        old_f = tmp_path / "old.txt"
        new_f = tmp_path / "new.txt"
        old_f.write_text("hello")
        new_f.write_text("world")
        args = _make_args(
            old_text=None, new_text=None,
            old_file=str(old_f), new_file=str(new_f),
        )
        rc = cmd_edit(args)
        assert rc == 0
        mock_replace.assert_called_once_with(
            "abc123", _single_match(), "world", "rev123", tab_id=None,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_file_flags_strip_trailing_newline(
        self, _pf, _doc, _find, mock_replace, _ver, _update, tmp_path,
    ):
        old_f = tmp_path / "old.txt"
        new_f = tmp_path / "new.txt"
        old_f.write_text("hello\n")
        new_f.write_text("world\n")
        args = _make_args(
            old_text=None, new_text=None,
            old_file=str(old_f), new_file=str(new_f),
        )
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", _single_match(), "world", "rev123", tab_id=None,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_file_flags_override_positional(
        self, _pf, _doc, _find, mock_replace, _ver, _update, tmp_path,
    ):
        old_f = tmp_path / "old.txt"
        new_f = tmp_path / "new.txt"
        old_f.write_text("hello")
        new_f.write_text("world")
        # Positional args set but file flags take precedence
        args = _make_args(
            old_text="ignored", new_text="ignored",
            old_file=str(old_f), new_file=str(new_f),
        )
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", _single_match(), "world", "rev123", tab_id=None,
        )

    def test_missing_new_file_flag(self, tmp_path):
        old_f = tmp_path / "old.txt"
        old_f.write_text("hello")
        args = _make_args(
            old_text=None, new_text=None,
            old_file=str(old_f), new_file=None,
        )
        msg = "--old-file and --new-file must be used together"
        with pytest.raises(GdocError, match=msg) as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3

    def test_missing_old_file_flag(self, tmp_path):
        new_f = tmp_path / "new.txt"
        new_f.write_text("world")
        args = _make_args(
            old_text=None, new_text=None,
            old_file=None, new_file=str(new_f),
        )
        msg = "--old-file and --new-file must be used together"
        with pytest.raises(GdocError, match=msg) as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3

    def test_file_not_found(self, tmp_path):
        missing = str(tmp_path / "nope.txt")
        args = _make_args(
            old_text=None, new_text=None,
            old_file=missing, new_file=missing,
        )
        with pytest.raises(GdocError, match="file not found"):
            cmd_edit(args)

    @patch("builtins.open", side_effect=OSError("disk error"))
    @patch("os.path.isfile", return_value=True)
    def test_file_read_error(self, _isfile, _open):
        args = _make_args(
            old_text=None, new_text=None,
            old_file="/tmp/old.txt",
            new_file="/tmp/new.txt",
        )
        with pytest.raises(GdocError, match="cannot read file"):
            cmd_edit(args)


class TestEditValidation:
    def test_missing_positional_args_without_files(self):
        args = _make_args(
            old_text=None, new_text=None,
            old_file=None, new_file=None,
        )
        msg = "old_text and new_text required"
        with pytest.raises(GdocError, match=msg) as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3

    def test_missing_new_text_positional(self):
        args = _make_args(
            old_text="hello", new_text=None,
            old_file=None, new_file=None,
        )
        msg = "old_text and new_text required"
        with pytest.raises(GdocError, match=msg) as exc_info:
            cmd_edit(args)
        assert exc_info.value.exit_code == 3


class TestEditHelpText:
    def test_edit_epilog_mentions_plain(self):
        parser = build_parser()
        for action in parser._subparsers._actions:
            if hasattr(action, "_parser_class"):
                for name, subparser in action.choices.items():
                    if name == "edit":
                        assert "cat --plain" in subparser.epilog
                        assert "raw document text" in subparser.epilog
                        assert "markdown formatting" in subparser.epilog
                        return
        pytest.fail("edit subparser not found")


class TestEditFormatted:
    """Tests verifying markdown replacement is passed through correctly."""

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_markdown_new_text_passed_to_replace(
        self, _pf, _doc, _find, mock_replace, _ver, _update,
    ):
        args = _make_args(new_text="**bold** replacement")
        cmd_edit(args)
        mock_replace.assert_called_once_with(
            "abc123", _single_match(), "**bold** replacement", "rev123", tab_id=None,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_revision_id_from_document(
        self, _pf, mock_doc, _find, mock_replace, _ver, _update,
    ):
        """Revision ID from get_document is passed to replace_formatted."""
        mock_doc.return_value = _mock_doc(revision_id="custom_rev")
        args = _make_args()
        cmd_edit(args)
        assert mock_replace.call_args[0][3] == "custom_rev"


class TestEditPlain:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_single_match())
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_plain_output(self, _pf, _doc, _find, _replace, _ver, _update, capsys):
        args = _make_args(plain=True)
        rc = cmd_edit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tabc123\n" in out
        assert "status\tupdated\n" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=3)
    @patch("gdoc.api.docs.find_text_in_document", return_value=_multi_match(3))
    @patch("gdoc.api.docs.get_document", return_value=_mock_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_edit_all_plain_output(
        self, _pf, _doc, _find, _replace, _ver, _update, capsys,
    ):
        args = _make_args(all=True, plain=True)
        rc = cmd_edit(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tabc123\n" in out
        assert "status\tupdated\n" in out


def _mock_tabs_doc(revision_id="rev_tab"):
    """Build a tabs-aware document dict with one tab."""
    return {
        "revisionId": revision_id,
        "tabs": [{
            "tabProperties": {"tabId": "t1", "title": "Notes", "index": 0},
            "documentTab": {
                "body": {
                    "content": [{
                        "paragraph": {
                            "elements": [{
                                "startIndex": 1,
                                "textRun": {"content": "hello world\n"},
                            }],
                        },
                    }],
                },
            },
        }],
    }


class TestEditTab:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_mock_tabs_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_tab_passes_tab_id(
        self, _pf, mock_get_tabs, mock_replace, _ver, _update, capsys,
    ):
        args = _make_args(tab="Notes")
        rc = cmd_edit(args)
        assert rc == 0
        mock_replace.assert_called_once()
        call_kwargs = mock_replace.call_args
        assert call_kwargs[1]["tab_id"] == "t1"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_mock_tabs_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_tab_searches_tab_body(
        self, _pf, mock_get_tabs, mock_replace, _ver, _update,
    ):
        """find_text_in_document is called with the tab body, finding 'hello'."""
        args = _make_args(tab="Notes", old_text="hello")
        rc = cmd_edit(args)
        assert rc == 0
        # The match should have been found in the tab body
        call_args = mock_replace.call_args[0]
        matches = call_args[1]
        assert len(matches) == 1
        assert matches[0]["startIndex"] == 1

    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_mock_tabs_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_tab_not_found(self, _pf, _get_tabs):
        args = _make_args(tab="Nonexistent")
        with pytest.raises(GdocError, match="tab not found"):
            cmd_edit(args)

    @patch("gdoc.api.docs.get_document_with_tabs",
           side_effect=GdocError("Document not found: abc123"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_tab_http_error_translated(self, _pf, _get_tabs):
        args = _make_args(tab="Notes")
        with pytest.raises(GdocError, match="Document not found"):
            cmd_edit(args)
