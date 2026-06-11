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
        "force_collapse_tabs": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


FRONTMATTER = "---\ngdoc: abc123\ntitle: My Doc\n---\n"


@pytest.fixture(autouse=True)
def _stub_single_tab():
    """Default `count_document_tabs` to `1` for the whole test module.

    Mirrors the pattern from `test_write.py`: legacy push tests assume
    a single-tab doc, and the new multi-tab safety check would
    otherwise call the real Docs API. Tests that need a different
    count stack their own `@patch("gdoc.api.docs.count_document_tabs",
    ...)` on top.
    """
    with patch("gdoc.api.docs.count_document_tabs", return_value=1):
        yield


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
    @patch("gdoc.api.drive.export_doc", return_value="something else entirely")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_blocked_on_conflict(
        self, mock_pf, mock_update_doc, _export, tmp_path,
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

    @patch("gdoc.api.drive.export_doc", return_value="something else entirely")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_blocked_no_prior_read(
        self, mock_pf, mock_update_doc, _export, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        change_info = ChangeInfo(current_version=10, last_read_version=None)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no read baseline"):
            cmd_push(args)
        mock_update_doc.assert_not_called()


class TestPushInSync:
    """Version drifted but content already matches — skip the upload."""

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 12})
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc", return_value="Body")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_noop_when_doc_matches(
        self, mock_pf, mock_update_doc, mock_export, mock_state, _ver,
        tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        mock_pf.return_value = ChangeInfo(current_version=12, last_read_version=5)
        args = _make_args(file=str(f))
        rc = cmd_push(args)
        assert rc == 0
        mock_update_doc.assert_not_called()
        assert "already in sync" in capsys.readouterr().out
        assert mock_state.call_args.kwargs["command_version"] == 12
        assert mock_state.call_args.kwargs["command"] == "push"
        assert mock_state.call_args.kwargs["full_doc_write"] is True

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 12})
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc", return_value="Body")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_noop_without_baseline_when_doc_matches(
        self, mock_pf, mock_update_doc, _export, _state, _ver, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        mock_pf.return_value = ChangeInfo(current_version=12, last_read_version=None)
        args = _make_args(file=str(f))
        assert cmd_push(args) == 0
        mock_update_doc.assert_not_called()

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 12})
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc", return_value="Body")
    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.notify.pre_flight")
    def test_push_noop_json_output(
        self, mock_pf, mock_update_doc, _export, _state, _ver,
        tmp_path, capsys,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "Body")
        mock_pf.return_value = ChangeInfo(current_version=12, last_read_version=5)
        args = _make_args(file=str(f), json=True)
        assert cmd_push(args) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["in_sync"] is True
        assert data["version"] == 12

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
            quiet=False, command_version=42, full_doc_write=True,
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

    def test_revision_pull_file_rejected_specifically(self, tmp_path):
        f = tmp_path / "old.md"
        f.write_text(
            "---\nsource: abc123\nrevision: 20\ntitle: Foo\n---\nBody"
        )
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="past revision") as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3

    def test_invalid_doc_id_in_frontmatter(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\ngdoc: !!invalid!!\n---\nBody")
        args = _make_args(file=str(f))
        with pytest.raises(GdocError) as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3


class TestPushPlain:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.notify.pre_flight")
    def test_push_plain_output(
        self, mock_pf, mock_update_doc, _drv, _update, capsys, tmp_path,
    ):
        f = tmp_path / "test.md"
        f.write_text(FRONTMATTER + "# Hello\n")
        change_info = ChangeInfo(current_version=10, last_read_version=10)
        mock_pf.return_value = change_info
        args = _make_args(file=str(f), plain=True)
        rc = cmd_push(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tabc123" in out
        assert "status\tupdated" in out


class TestPushCollapseSafety:
    """Pushes against multi-tab docs without --force-collapse-tabs fail."""

    @patch("gdoc.api.drive.update_doc_content")
    @patch("gdoc.api.docs.count_document_tabs", return_value=3)
    @patch("gdoc.notify.pre_flight")
    def test_refuses_multi_tab_without_flag(
        self, mock_pf, _mock_count, mock_update, tmp_path,
    ):
        f = tmp_path / "doc.md"
        f.write_text(FRONTMATTER + "# Hello\n")
        mock_pf.return_value = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        args = _make_args(file=str(f), force_collapse_tabs=False)
        with pytest.raises(GdocError, match="collapse 3 tabs") as exc:
            cmd_push(args)
        assert exc.value.exit_code == 3
        msg = str(exc.value)
        assert "--force-collapse-tabs" in msg
        assert "--tab" in msg
        assert "insert" in msg
        # Critical: the destructive write must not have fired.
        mock_update.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    @patch("gdoc.api.docs.count_document_tabs")
    @patch("gdoc.notify.pre_flight")
    def test_force_collapse_bypasses_check(
        self, mock_pf, mock_count, mock_update, _u, tmp_path,
    ):
        f = tmp_path / "doc.md"
        f.write_text(FRONTMATTER + "# Hello\n")
        mock_pf.return_value = ChangeInfo(
            current_version=10, last_read_version=10,
        )
        args = _make_args(file=str(f), force_collapse_tabs=True)
        rc = cmd_push(args)
        assert rc == 0
        # With the opt-in flag, no count lookup happens at all.
        mock_count.assert_not_called()
        mock_update.assert_called_once()
