"""Tests for `gdoc cat --revision` and `gdoc pull --revision`."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_cat, cmd_pull
from gdoc.util import GdocError

REVS = [
    {
        "id": "1", "modifiedTime": "2026-06-01T10:00:00.000Z",
        "exportLinks": {"text/markdown": "https://example.test/1.md",
                        "text/plain": "https://example.test/1.txt"},
    },
    {
        "id": "20", "modifiedTime": "2026-06-08T10:00:00.000Z",
        "exportLinks": {"text/markdown": "https://example.test/20.md",
                        "text/plain": "https://example.test/20.txt"},
    },
    {
        "id": "66", "modifiedTime": "2026-06-10T10:00:00.000Z",
        "exportLinks": {"text/markdown": "https://example.test/66.md",
                        "text/plain": "https://example.test/66.txt"},
    },
]


def _cat_args(**overrides):
    defaults = {
        "command": "cat",
        "doc": "abc123",
        "comments": False,
        "all": False,
        "tab": None,
        "all_tabs": False,
        "max_bytes": 0,
        "no_images": False,
        "revision": None,
        "plain": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _pull_args(**overrides):
    defaults = {
        "command": "pull",
        "doc": "abc123",
        "file": "/tmp/out.md",
        "revision": None,
        "plain": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _doc_mime(doc_mime):
    """Keep spreadsheet detection on the Docs path for this module."""


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.revisions.export_revision", return_value="old content\n")
@patch("gdoc.api.revisions.list_revisions", return_value=list(REVS))
@patch("gdoc.notify.pre_flight", return_value=None)
class TestCatRevision:
    def test_exports_resolved_revision(
        self, _pf, _list, mock_export, _update, capsys,
    ):
        rc = cmd_cat(_cat_args(revision="prev"))
        assert rc == 0
        assert capsys.readouterr().out == "old content\n"
        mock_export.assert_called_once_with(
            "abc123", "20", mime_type="text/markdown",
            export_links=REVS[1]["exportLinks"],
        )

    def test_plain_uses_text_mime(self, _pf, _list, mock_export, _update):
        cmd_cat(_cat_args(revision="66", plain=True))
        assert mock_export.call_args.kwargs["mime_type"] == "text/plain"

    def test_json_includes_revision_id(
        self, _pf, _list, _export, _update, capsys,
    ):
        cmd_cat(_cat_args(revision="latest", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["revision"] == "66"
        assert data["content"] == "old content\n"

    def test_state_does_not_advance_read_baseline(
        self, _pf, _list, _export, mock_update,
    ):
        cmd_cat(_cat_args(revision="1"))
        # command name must not be a read command (cat/info/pull), or
        # update_state_after_command would advance last_read_version
        assert mock_update.call_args.kwargs.get("command") == "cat-revision"

    def test_revision_with_comments_rejected(
        self, _pf, _list, _export, _update,
    ):
        with pytest.raises(GdocError, match="cannot be combined") as exc_info:
            cmd_cat(_cat_args(revision="1", comments=True))
        assert exc_info.value.exit_code == 3

    def test_revision_with_tab_rejected(self, _pf, _list, _export, _update):
        with pytest.raises(GdocError, match="cannot be combined") as exc_info:
            cmd_cat(_cat_args(revision="1", tab="Notes"))
        assert exc_info.value.exit_code == 3

    def test_unknown_revision_errors(self, _pf, _list, _export, _update):
        with pytest.raises(GdocError, match="revision not found") as exc_info:
            cmd_cat(_cat_args(revision="999"))
        assert exc_info.value.exit_code == 3


@patch("gdoc.state.update_state_after_command")
@patch(
    "gdoc.api.drive.get_file_info",
    return_value={"name": "My Doc", "version": 42},
)
@patch("gdoc.api.revisions.export_revision", return_value="old body\n")
@patch("gdoc.api.revisions.list_revisions", return_value=list(REVS))
@patch("gdoc.notify.pre_flight", return_value=None)
class TestPullRevision:
    def test_writes_file_with_revision_frontmatter(
        self, _pf, _list, _export, _info, _update, capsys, tmp_path,
    ):
        out = tmp_path / "old.md"
        rc = cmd_pull(_pull_args(file=str(out), revision="head~1"))
        assert rc == 0
        content = out.read_text()
        assert content.startswith("---\n")
        assert "source: abc123" in content
        assert "revision: 20" in content
        assert "old body" in content
        # No gdoc: key — push and the sync hooks must not pick this up
        assert "\ngdoc:" not in content
        assert "@ rev 20" in capsys.readouterr().out

    def test_json_output(
        self, _pf, _list, _export, _info, _update, capsys, tmp_path,
    ):
        out = tmp_path / "old.md"
        cmd_pull(_pull_args(file=str(out), revision="latest", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["pulled"] is True
        assert data["revision"] == "66"

    def test_state_does_not_advance_read_baseline(
        self, _pf, _list, _export, _info, mock_update, tmp_path,
    ):
        out = tmp_path / "old.md"
        cmd_pull(_pull_args(file=str(out), revision="1"))
        assert mock_update.call_args.kwargs.get("command") == "pull-revision"
        assert mock_update.call_args.kwargs.get("command_version") is None

    def test_default_pull_unchanged(
        self, _pf, _list, mock_export, _info, mock_update, tmp_path,
    ):
        with patch(
            "gdoc.api.drive.export_doc", return_value="current body\n",
        ) as mock_doc_export:
            out = tmp_path / "cur.md"
            rc = cmd_pull(_pull_args(file=str(out)))
            assert rc == 0
            mock_doc_export.assert_called_once()
        mock_export.assert_not_called()
        content = out.read_text()
        assert "gdoc: abc123" in content
        assert mock_update.call_args.kwargs.get("command") == "pull"
