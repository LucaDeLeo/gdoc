"""Tests for the `gdoc pull` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_pull
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


def _make_args(**overrides):
    defaults = {
        "command": "pull",
        "doc": "abc123",
        "file": "/tmp/test.md",
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestPullBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_pull_success(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path, capsys,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        rc = cmd_pull(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert 'OK pulled "My Doc"' in out
        assert str(f) in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_pull_writes_frontmatter(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_pull(args)
        content = f.read_text()
        assert content.startswith("---\n")
        assert "gdoc: abc123" in content
        assert "title: My Doc" in content
        assert "# Hello\n" in content

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_pull_exports_markdown(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_pull(args)
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_pull_url_input(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(
            doc="https://docs.google.com/document/d/abc123/edit",
            file=str(f),
        )
        cmd_pull(args)
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")


class TestPullOutput:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_pull_json_output(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path, capsys,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), json=True)
        rc = cmd_pull(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["pulled"] is True
        assert data["title"] == "My Doc"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_pull_verbose_output(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path, capsys,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), verbose=True)
        rc = cmd_pull(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "My Doc" in out
        assert "abc123" in out


class TestPullAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_preflight_called(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_pull(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_state_updated_as_read(
        self, mock_pf, mock_export, mock_info, _drv, mock_update,
        tmp_path,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        cmd_pull(args)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="pull",
            quiet=False, command_version=42,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_quiet_skips_preflight(
        self, mock_pf, mock_export, mock_info, _drv, _update,
        tmp_path,
    ):
        f = tmp_path / "out.md"
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), quiet=True)
        cmd_pull(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)


class TestPullErrors:
    def test_invalid_doc_id(self, tmp_path):
        f = tmp_path / "out.md"
        args = _make_args(doc="!!invalid!!", file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_pull(args)
        assert exc.value.exit_code == 3

    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight")
    def test_unwritable_path(
        self, mock_pf, mock_export, mock_info, _drv,
    ):
        change_info = ChangeInfo(current_version=42)
        mock_pf.return_value = change_info
        args = _make_args(file="/nonexistent/dir/out.md")
        with pytest.raises(GdocError, match="cannot write file"):
            cmd_pull(args)


class TestPullPlain:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_info", return_value={
        "name": "My Doc", "version": "5",
    })
    @patch("gdoc.api.drive.export_doc", return_value="# Hello\n")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_drive_service")
    def test_pull_plain_output(
        self, _svc, _pf, _export, _info, _update, capsys, tmp_path,
    ):
        f = tmp_path / "doc.md"
        args = _make_args(file=str(f), plain=True)
        rc = cmd_pull(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert f"path\t{f}" in out
