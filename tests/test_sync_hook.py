"""Tests for the `gdoc _sync-hook` command handler."""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from gdoc.cli import cmd_sync_hook


def _make_args():
    return SimpleNamespace(command="_sync-hook")


def _stdin_json(file_path):
    data = {"tool_input": {"file_path": file_path}}
    return io.StringIO(json.dumps(data))


class TestSyncHookBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    def test_sync_pushes_file(
        self, mock_update_doc, _drv, _update, tmp_path, capsys,
    ):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: My Doc\n---\n# Hello\n")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0
        mock_update_doc.assert_called_once_with("abc123", "# Hello\n")
        err = capsys.readouterr().err
        assert "SYNC:" in err
        assert "My Doc" in err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    def test_sync_strips_frontmatter(
        self, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: T\n---\nBody text")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            cmd_sync_hook(args)
        mock_update_doc.assert_called_once_with("abc123", "Body text")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    def test_sync_updates_state(
        self, mock_update_doc, _drv, mock_update, tmp_path,
    ):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: T\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            cmd_sync_hook(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="push",
            quiet=True, command_version=42,
        )


class TestSyncHookSkips:
    def test_skip_non_md_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("---\ngdoc: abc\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_missing_file(self):
        args = _make_args()
        with patch("sys.stdin", _stdin_json("/nonexistent/file.md")):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_no_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("# No frontmatter\nJust text.")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_no_gdoc_key(self, tmp_path):
        f = tmp_path / "other.md"
        f.write_text("---\ntitle: Foo\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_empty_stdin(self):
        args = _make_args()
        with patch("sys.stdin", io.StringIO("")):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_no_file_path_in_json(self):
        args = _make_args()
        with patch("sys.stdin", io.StringIO('{"tool_input": {}}')):
            rc = cmd_sync_hook(args)
        assert rc == 0


class TestSyncHookErrorHandling:
    def test_never_raises(self):
        """The sync hook must always return 0, even on errors."""
        args = _make_args()
        with patch("sys.stdin", io.StringIO("not json")):
            rc = cmd_sync_hook(args)
        assert rc == 0

    @patch(
        "gdoc.api.drive.update_doc_content",
        side_effect=Exception("API failure"),
    )
    def test_api_error_swallowed(self, _update_doc, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: T\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0
