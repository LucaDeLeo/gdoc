"""Tests for the `gdoc insert` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_insert
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


def _make_args(**overrides):
    defaults = {
        "command": "insert",
        "doc": "abc123",
        "file": "/tmp/content.md",
        "tab": "TODO",
        "position": "start",
        "force": False,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _insert_result(tab_id="t.todo", tab_title="TODO", insert_index=1):
    return {
        "tab_id": tab_id,
        "tab_title": tab_title,
        "insert_index": insert_index,
    }


def _preflight_ok():
    return ChangeInfo(current_version=10, last_read_version=10)


class TestInsertBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_terse_output(
        self, _pf, mock_insert, _ver, _update, tmp_path, capsys,
    ):
        f = tmp_path / "content.md"
        f.write_text("# Hello")
        args = _make_args(file=str(f))
        rc = cmd_insert(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert 'OK inserted into "TODO"' in out
        mock_insert.assert_called_once_with(
            "abc123", "TODO", "# Hello",
            position="start", replace=False,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_json_output(
        self, _pf, _mock_insert, _ver, _update, tmp_path, capsys,
    ):
        f = tmp_path / "content.md"
        f.write_text("hi")
        args = _make_args(file=str(f), json=True)
        rc = cmd_insert(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["inserted"] is True
        assert data["tab_id"] == "t.todo"
        assert data["tab_title"] == "TODO"
        assert data["version"] == 11

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_position_end_is_forwarded(
        self, _pf, mock_insert, _ver, _update, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("tail")
        args = _make_args(file=str(f), position="end")
        cmd_insert(args)
        mock_insert.assert_called_once_with(
            "abc123", "TODO", "tail",
            position="end", replace=False,
        )


class TestInsertFrontmatterStrip:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_frontmatter_stripped(
        self, _pf, mock_insert, _ver, _update, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text(
            "---\ngdoc: abc123\ntitle: Whatever\n---\n# Real content\n",
        )
        args = _make_args(file=str(f))
        cmd_insert(args)
        body = mock_insert.call_args.args[2]
        assert body.startswith("# Real content")
        assert "---" not in body
        assert "gdoc:" not in body


class TestInsertFileErrors:
    def test_file_not_found(self):
        args = _make_args(file="/nonexistent/missing.md")
        with pytest.raises(GdocError, match="file not found") as exc:
            cmd_insert(args)
        assert exc.value.exit_code == 3

    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_empty_file_after_frontmatter_strip(self, _pf, tmp_path):
        f = tmp_path / "content.md"
        f.write_text("---\ngdoc: abc123\n---\n\n\n")
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no content") as exc:
            cmd_insert(args)
        assert exc.value.exit_code == 3


class TestInsertConflict:
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab")
    @patch("gdoc.notify.pre_flight")
    def test_blocks_on_conflict(
        self, mock_pf, mock_insert, _ver, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("hi")
        mock_pf.return_value = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="doc changed"):
            cmd_insert(args)
        mock_insert.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight")
    def test_force_bypasses_conflict(
        self, mock_pf, mock_insert, _ver, _update, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("hi")
        mock_pf.return_value = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        args = _make_args(file=str(f), force=True)
        rc = cmd_insert(args)
        assert rc == 0
        mock_insert.assert_called_once()
