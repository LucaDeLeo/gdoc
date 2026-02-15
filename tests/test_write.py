"""Tests for the `gdoc write` command handler."""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from gdoc.cli import cmd_write
from gdoc.notify import ChangeInfo
from gdoc.state import DocState
from gdoc.util import AuthError, GdocError


def _make_args(**overrides):
    """Build a SimpleNamespace mimicking parsed write args."""
    defaults = {
        "command": "write",
        "doc": "abc123",
        "file": "/tmp/test.md",
        "force": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestWriteBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_success(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text("# Hello")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        rc = cmd_write(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK written" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_reads_file_content(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("# My Document\n\nContent here.")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_write(args)
        mock_update_doc.assert_called_once_with(
            "abc123", "# My Document\n\nContent here.",
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_json_output(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text("# Hello")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), json=True)
        rc = cmd_write(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "written": True, "version": 42}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_url_input(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(
            doc="https://docs.google.com/document/d/abc123/edit",
            file=str(f),
        )
        cmd_write(args)
        mock_update_doc.assert_called_once_with("abc123", "content")


class TestWriteFileErrors:
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.update_doc_content")
    def test_write_file_not_found(self, mock_update_doc, _pf):
        args = _make_args(file="/nonexistent/path.md")
        with pytest.raises(GdocError, match="file not found") as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        mock_update_doc.assert_not_called()

    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.drive.update_doc_content")
    def test_write_file_unreadable(
        self, mock_update_doc, _pf, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        f.chmod(0o000)
        args = _make_args(file=str(f))
        try:
            with pytest.raises(GdocError, match="cannot read file"):
                cmd_write(args)
        finally:
            f.chmod(0o644)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_empty_file(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        rc = cmd_write(args)
        assert rc == 0
        mock_update_doc.assert_called_once_with("abc123", "")


class TestWriteConflictNormal:
    """Not quiet, not force."""

    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_blocked_on_conflict(
        self, mock_pf, mock_update_doc, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        assert "doc changed since last read" in str(exc.value)
        mock_update_doc.assert_not_called()

    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_blocked_no_prior_read(
        self, mock_pf, mock_update_doc, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=None,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        mock_update_doc.assert_not_called()

    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_blocked_no_prior_read_correct_message(
        self, mock_pf, mock_update_doc, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=None,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no read baseline"):
            cmd_write(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_proceeds_no_conflict(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        rc = cmd_write(args)
        assert rc == 0
        mock_update_doc.assert_called_once()

    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_conflict_error_message(
        self, mock_pf, mock_update_doc, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_write(args)
        msg = str(exc.value)
        assert "doc changed since last read" in msg
        assert "--force" in msg


class TestWriteConflictForce:
    """Not quiet, force."""

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_force_ignores_conflict(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), force=True)
        rc = cmd_write(args)
        assert rc == 0
        mock_update_doc.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_force_preflight_still_runs(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), force=True)
        cmd_write(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_force_no_prior_read(
        self, mock_pf, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=None,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), force=True)
        rc = cmd_write(args)
        assert rc == 0
        mock_update_doc.assert_called_once()


class TestWriteQuietNoForce:
    """Quiet, not force — lightweight version check."""

    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_skips_full_preflight(
        self, mock_pf, mock_update_doc, _drv, _update,
        mock_load, mock_ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        mock_load.return_value = DocState(last_read_version=10)
        mock_ver.return_value = {"version": 10}
        args = _make_args(file=str(f), quiet=True)
        cmd_write(args)
        mock_pf.assert_not_called()

    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_does_version_check(
        self, mock_pf, mock_update_doc, _drv, _update,
        mock_load, mock_ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        mock_load.return_value = DocState(last_read_version=10)
        mock_ver.return_value = {"version": 10}
        args = _make_args(file=str(f), quiet=True)
        cmd_write(args)
        mock_ver.assert_called_once_with("abc123")

    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_blocks_on_version_mismatch(
        self, _pf, mock_update_doc, mock_load, mock_ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        mock_load.return_value = DocState(last_read_version=5)
        mock_ver.return_value = {"version": 10}
        args = _make_args(file=str(f), quiet=True)
        with pytest.raises(GdocError) as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        mock_update_doc.assert_not_called()

    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_proceeds_version_match(
        self, _pf, mock_update_doc, _drv, _update,
        mock_load, mock_ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        mock_load.return_value = DocState(last_read_version=5)
        mock_ver.return_value = {"version": 5}
        args = _make_args(file=str(f), quiet=True)
        rc = cmd_write(args)
        assert rc == 0
        mock_update_doc.assert_called_once()

    @patch("gdoc.state.load_state", return_value=None)
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_blocks_no_state(
        self, _pf, mock_update_doc, mock_load, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        args = _make_args(file=str(f), quiet=True)
        with pytest.raises(GdocError, match="no read baseline") as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        mock_update_doc.assert_not_called()

    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_blocks_no_read_version(
        self, _pf, mock_update_doc, mock_load, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        mock_load.return_value = DocState(
            last_version=10, last_read_version=None,
        )
        args = _make_args(file=str(f), quiet=True)
        with pytest.raises(GdocError, match="no read baseline") as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        mock_update_doc.assert_not_called()


class TestWriteQuietForce:
    """Quiet + force → skip everything."""

    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_force_skips_everything(
        self, mock_pf, mock_update_doc, _drv, _update,
        mock_ver, mock_load, tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        args = _make_args(file=str(f), quiet=True, force=True)
        rc = cmd_write(args)
        assert rc == 0
        mock_pf.assert_not_called()
        mock_ver.assert_not_called()
        mock_load.assert_not_called()
        mock_update_doc.assert_called_once()

    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_quiet_force_full_savings(
        self, mock_pf, mock_update_doc, _drv, _update,
        mock_ver, mock_load, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        args = _make_args(file=str(f), quiet=True, force=True)
        cmd_write(args)
        # Only update_doc_content should be called (no pre-flight calls)
        mock_pf.assert_not_called()
        mock_ver.assert_not_called()
        mock_load.assert_not_called()


class TestWriteAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_preflight_called_normal(
        self, mock_pf, _update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_write(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_state_updated_with_version(
        self, mock_pf, _update_doc, _drv, mock_update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_write(args)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="write",
            quiet=False, command_version=42,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_state_not_updated_on_conflict_block(
        self, mock_pf, mock_update_doc, mock_update, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError):
            cmd_write(args)
        mock_update.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_state_not_updated_on_file_error(
        self, _pf, mock_update_doc, mock_update,
    ):
        args = _make_args(file="/nonexistent/path.md")
        with pytest.raises(GdocError):
            cmd_write(args)
        mock_update.assert_not_called()

    @patch("gdoc.state.load_state")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_state_updated_quiet_force(
        self, mock_pf, _update_doc, _drv, mock_update,
        mock_ver, mock_load, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        args = _make_args(file=str(f), quiet=True, force=True)
        cmd_write(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="write",
            quiet=True, command_version=42,
        )


class TestWriteErrors:
    def test_write_invalid_doc_id(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("content")
        args = _make_args(doc="!!invalid!!", file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3

    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.update_doc_content",
        side_effect=GdocError("API error"),
    )
    @patch("gdoc.notify.pre_flight")
    def test_write_api_error(
        self, mock_pf, _update_doc, _drv, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="API error"):
            cmd_write(args)

    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.update_doc_content",
        side_effect=AuthError("Authentication expired"),
    )
    @patch("gdoc.notify.pre_flight")
    def test_write_auth_error(
        self, mock_pf, _update_doc, _drv, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text("content")
        change_info = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(AuthError, match="Authentication expired"):
            cmd_write(args)


class TestWritePlain:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_write_plain_output(self, mock_pf, _update_doc, _update, capsys, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("content")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), force=True, quiet=True, plain=True)
        rc = cmd_write(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tabc123" in out
        assert "status\tupdated" in out
