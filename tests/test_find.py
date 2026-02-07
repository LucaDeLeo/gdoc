"""Tests for gdoc find command."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from gdoc.cli import cmd_find

MOCK_FILES = [
    {
        "id": "doc1",
        "name": "Meeting Notes",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2025-01-15T10:30:00.000Z",
    },
    {
        "id": "sheet1",
        "name": "Budget 2025",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "modifiedTime": "2025-01-14T09:00:00.000Z",
    },
]


def _make_args(**kwargs):
    """Build a SimpleNamespace with find-command defaults."""
    defaults = {
        "command": "find",
        "query": "meeting",
        "json": False,
        "verbose": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.search_files")
class TestFindBasic:
    def test_find_basic_search(self, mock_search, mock_svc, capsys):
        mock_search.return_value = MOCK_FILES
        args = _make_args(query="meeting")
        rc = cmd_find(args)
        assert rc == 0
        mock_search.assert_called_once_with("meeting")
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 2
        # Tab-separated output
        assert "\t" in lines[0]

    def test_find_empty_result(self, mock_search, mock_svc, capsys):
        mock_search.return_value = []
        args = _make_args(query="nonexistent")
        rc = cmd_find(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out == ""


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.search_files")
class TestFindOutputFormat:
    def test_find_terse_matches_ls(self, mock_search, mock_svc, capsys):
        mock_search.return_value = MOCK_FILES
        args = _make_args()
        cmd_find(args)
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 2
        # 3 columns: ID, TITLE, MODIFIED (truncated)
        parts = lines[0].split("\t")
        assert len(parts) == 3
        assert parts[0] == "doc1"
        assert parts[1] == "Meeting Notes"
        assert parts[2] == "2025-01-15"

    def test_find_verbose(self, mock_search, mock_svc, capsys):
        mock_search.return_value = MOCK_FILES
        args = _make_args(verbose=True)
        cmd_find(args)
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        parts = lines[0].split("\t")
        assert len(parts) == 4
        assert parts[3] == "application/vnd.google-apps.document"

    def test_find_json(self, mock_search, mock_svc, capsys):
        mock_search.return_value = MOCK_FILES
        args = _make_args(json=True)
        cmd_find(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert len(data["files"]) == 2
        assert data["files"][0]["id"] == "doc1"


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.search_files")
class TestFindSpecialChars:
    def test_find_with_quotes(self, mock_search, mock_svc):
        mock_search.return_value = []
        args = _make_args(query="it's")
        cmd_find(args)
        mock_search.assert_called_once_with("it's")

    def test_find_with_backslash(self, mock_search, mock_svc):
        mock_search.return_value = []
        args = _make_args(query="path\\to")
        cmd_find(args)
        mock_search.assert_called_once_with("path\\to")
