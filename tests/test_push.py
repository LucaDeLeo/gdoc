"""Tests for the `gdoc push` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_push
from gdoc.notify import ChangeInfo
from gdoc.state import DocState
from gdoc.util import GdocError


def _make_args(**overrides):
    defaults = {
        "command": "push",
        "file": "/tmp/test.md",
        "force": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


FRONTMATTER = "---\ngdoc: abc123\ntitle: My Doc\n---\n"


class TestPushBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_success(
        self, mock_pf, mock_update_doc, _drv, _update,
        tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "# Hello\n")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        rc = cmd_push(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK pushed" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_strips_frontmatter(
        self, mock_pf, mock_update_doc, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "# Hello\n")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_push(args)
        mock_update_doc.assert_called_once_with("abc123", "# Hello\n")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_json_output(
        self, mock_pf, mock_update_doc, _drv, _update,
        tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "# Hello\n")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), json=True)
        rc = cmd_push(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["pushed"] is True
        assert data["version"] == 42

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_url_in_frontmatter(
        self, mock_pf, mock_update_doc, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "test.md"
        url = "https://docs.google.com/document/d/abc123/edit"
        fm = f"---\ngdoc: {url}\ntitle: T\n---\n"
        f.write_text(fm + "Body")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_push(args)
        mock_update_doc.assert_called_once_with("abc123", "Body")


class TestPushConflict:
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_blocked_on_conflict(
        self, mock_pf, mock_update_doc, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        change_info = ChangeInfo(current_version=10, last_read_version=5)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3
        assert "doc changed since last read" in str(exc.value)
        mock_update_doc.assert_not_called()

    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_blocked_no_prior_read(
        self, mock_pf, mock_update_doc, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        change_info = ChangeInfo(current_version=10, last_read_version=None)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no read baseline"):
            cmd_push(args)
        mock_update_doc.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_force_ignores_conflict(
        self, mock_pf, mock_update_doc, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        change_info = ChangeInfo(current_version=10, last_read_version=5)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), force=True)
        rc = cmd_push(args)
        assert rc == 0
        mock_update_doc.assert_called_once()


class TestPushQuiet:
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_quiet_does_version_check(
        self, mock_pf, mock_update_doc, _drv, _update,
        mock_load, mock_ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        mock_load.return_value = DocState(last_read_version=10)
        mock_ver.return_value = {"version": 10}
        args = _make_args(file=str(f), quiet=True)
        cmd_push(args)
        mock_pf.assert_not_called()
        mock_ver.assert_called_once_with("abc123")

    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_quiet_blocks_version_mismatch(
        self, _pf, mock_update_doc, mock_load, mock_ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        mock_load.return_value = DocState(last_read_version=5)
        mock_ver.return_value = {"version": 10}
        args = _make_args(file=str(f), quiet=True)
        with pytest.raises(GdocError) as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3
        mock_update_doc.assert_not_called()

    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_quiet_force_skips_everything(
        self, mock_pf, mock_update_doc, _drv, _update,
        mock_ver, mock_load, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        args = _make_args(file=str(f), quiet=True, force=True)
        rc = cmd_push(args)
        assert rc == 0
        mock_pf.assert_not_called()
        mock_ver.assert_not_called()
        mock_load.assert_not_called()


class TestPushAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_state_updated_with_version(
        self, mock_pf, _update_doc, _drv, mock_update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_push(args)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="push",
            quiet=False, command_version=42,
        )


class TestPushErrors:
    def test_file_not_found(self):
        args = _make_args(file="/nonexistent/path.md")
        with pytest.raises(GdocError, match="file not found") as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3

    def test_no_frontmatter(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# No frontmatter\nJust body.")
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no gdoc frontmatter") as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3

    def test_frontmatter_missing_gdoc_key(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\ntitle: Foo\n---\nBody")
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no gdoc frontmatter") as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3

    def test_invalid_doc_id_in_frontmatter(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\ngdoc: !!invalid!!\n---\nBody")
        args = _make_args(file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3
