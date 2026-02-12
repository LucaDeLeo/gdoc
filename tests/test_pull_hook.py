"""Tests for the `gdoc _pull-hook` command handler."""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from gdoc.cli import cmd_pull_hook


def _make_args():
    return SimpleNamespace(command="_pull-hook")


def _stdin_json(file_path):
    data = {"tool_input": {"file_path": file_path}}
    return io.StringIO(json.dumps(data))


class TestPullHookBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": "55"},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Fresh content\n")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 55})
    @patch("gdoc.state.load_state")
    def test_pull_on_version_mismatch(
        self, mock_load, mock_ver, mock_export, mock_info,
        _drv, mock_update, tmp_path, capsys,
    ):
        from gdoc.state import DocState

        mock_load.return_value = DocState(last_version=50)

        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: My Doc\n---\n# Old content\n")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)

        assert rc == 0
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")
        mock_info.assert_called_once_with("abc123")

        # File should be overwritten with fresh content + frontmatter
        content = f.read_text()
        assert "# Fresh content" in content
        assert "gdoc: abc123" in content

        err = capsys.readouterr().err
        assert "SYNC:" in err
        assert "My Doc" in err
        assert "v55" in err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": "55"},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Fresh\n")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 55})
    @patch("gdoc.state.load_state")
    def test_pull_updates_state(
        self, mock_load, mock_ver, mock_export, mock_info,
        _drv, mock_update, tmp_path,
    ):
        from gdoc.state import DocState

        mock_load.return_value = DocState(last_version=50)

        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: T\n---\nOld")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            cmd_pull_hook(args)

        mock_update.assert_called_once_with(
            "abc123", None, command="pull",
            quiet=True, command_version=55,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "Doc", "version": "10"},
    )
    @patch("gdoc.api.drive.export_doc", return_value="# Content\n")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    @patch("gdoc.state.load_state", return_value=None)
    def test_pull_unconditionally_when_no_state(
        self, mock_load, mock_ver, mock_export, mock_info,
        _drv, mock_update, tmp_path,
    ):
        """First time seeing a doc → always pull (no state to compare)."""
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: Doc\n---\nOld")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)

        assert rc == 0
        mock_export.assert_called_once()
        mock_update.assert_called_once()


class TestPullHookSkips:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 42})
    @patch("gdoc.state.load_state")
    def test_skip_when_version_matches(
        self, mock_load, mock_ver, _drv, tmp_path,
    ):
        from gdoc.state import DocState

        mock_load.return_value = DocState(last_version=42)

        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: T\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)

        assert rc == 0
        # Should NOT have called export_doc (no pull needed)
        mock_ver.assert_called_once()

    def test_skip_non_md_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("---\ngdoc: abc\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)
        assert rc == 0

    def test_skip_missing_file(self):
        args = _make_args()
        with patch("sys.stdin", _stdin_json("/nonexistent/file.md")):
            rc = cmd_pull_hook(args)
        assert rc == 0

    def test_skip_no_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("# No frontmatter\nJust text.")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)
        assert rc == 0

    def test_skip_no_gdoc_key(self, tmp_path):
        f = tmp_path / "other.md"
        f.write_text("---\ntitle: Foo\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)
        assert rc == 0

    def test_skip_empty_stdin(self):
        args = _make_args()
        with patch("sys.stdin", io.StringIO("")):
            rc = cmd_pull_hook(args)
        assert rc == 0

    def test_skip_no_file_path_in_json(self):
        args = _make_args()
        with patch("sys.stdin", io.StringIO('{"tool_input": {}}')):
            rc = cmd_pull_hook(args)
        assert rc == 0


class TestPullHookErrorHandling:
    def test_never_raises(self):
        """The pull hook must always return 0, even on errors."""
        args = _make_args()
        with patch("sys.stdin", io.StringIO("not json")):
            rc = cmd_pull_hook(args)
        assert rc == 0

    @patch(
        "gdoc.api.drive.get_file_version",
        side_effect=Exception("API failure"),
    )
    def test_api_error_swallowed(self, _ver, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ntitle: T\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_pull_hook(args)
        assert rc == 0
