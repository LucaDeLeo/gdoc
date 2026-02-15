"""Tests for gdoc ls command."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from gdoc.cli import cmd_ls

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
    """Build a SimpleNamespace with ls-command defaults."""
    defaults = {
        "command": "ls",
        "folder_id": None,
        "type": "all",
        "json": False,
        "verbose": False,
        "plain": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.list_files")
class TestLsTerse:
    def test_ls_terse_output(self, mock_list, mock_svc, capsys):
        mock_list.return_value = MOCK_FILES
        args = _make_args()
        rc = cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 2
        assert "doc1\tMeeting Notes\t2025-01-15" in lines[0]
        assert "sheet1\tBudget 2025\t2025-01-14" in lines[1]

    def test_ls_empty_result(self, mock_list, mock_svc, capsys):
        mock_list.return_value = []
        args = _make_args()
        rc = cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == "No files."

    def test_ls_default_query_root(self, mock_list, mock_svc):
        mock_list.return_value = []
        args = _make_args()
        cmd_ls(args)
        query = mock_list.call_args[0][0]
        assert "'root' in parents" in query
        assert "trashed=false" in query


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.list_files")
class TestLsTypeFilter:
    def test_ls_type_docs(self, mock_list, mock_svc):
        mock_list.return_value = []
        args = _make_args(type="docs")
        cmd_ls(args)
        query = mock_list.call_args[0][0]
        assert "mimeType='application/vnd.google-apps.document'" in query

    def test_ls_type_sheets(self, mock_list, mock_svc):
        mock_list.return_value = []
        args = _make_args(type="sheets")
        cmd_ls(args)
        query = mock_list.call_args[0][0]
        assert "mimeType='application/vnd.google-apps.spreadsheet'" in query

    def test_ls_type_all(self, mock_list, mock_svc):
        mock_list.return_value = []
        args = _make_args(type="all")
        cmd_ls(args)
        query = mock_list.call_args[0][0]
        assert "mimeType=" not in query


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.list_files")
class TestLsFolderFilter:
    def test_ls_with_folder_id(self, mock_list, mock_svc):
        mock_list.return_value = []
        args = _make_args(folder_id="folder123")
        cmd_ls(args)
        query = mock_list.call_args[0][0]
        assert "'folder123' in parents" in query
        assert "'root' in parents" not in query

    def test_ls_with_folder_url(self, mock_list, mock_svc):
        mock_list.return_value = []
        args = _make_args(
            folder_id="https://drive.google.com/drive/folders/folder123"
        )
        cmd_ls(args)
        query = mock_list.call_args[0][0]
        assert "'folder123' in parents" in query


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.list_files")
class TestLsVerbose:
    def test_ls_verbose_output(self, mock_list, mock_svc, capsys):
        mock_list.return_value = MOCK_FILES
        args = _make_args(verbose=True)
        rc = cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 2
        # 4 columns: ID, TITLE, MODIFIED, TYPE
        parts = lines[0].split("\t")
        assert len(parts) == 4
        assert parts[0] == "doc1"
        assert parts[1] == "Meeting Notes"
        # Full ISO 8601 date, not truncated
        assert parts[2] == "2025-01-15T10:30:00.000Z"
        assert parts[3] == "application/vnd.google-apps.document"


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.list_files")
class TestLsJson:
    def test_ls_json_output(self, mock_list, mock_svc, capsys):
        mock_list.return_value = MOCK_FILES
        args = _make_args(json=True)
        rc = cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert len(data["files"]) == 2
        assert data["files"][0]["id"] == "doc1"
        assert data["files"][1]["id"] == "sheet1"

    def test_ls_json_empty(self, mock_list, mock_svc, capsys):
        mock_list.return_value = []
        args = _make_args(json=True)
        rc = cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["ok"] is True
        assert data["files"] == []


@patch("gdoc.api.get_drive_service")
@patch("gdoc.api.drive.list_files")
class TestLsPlain:
    def test_ls_plain_output(self, mock_list, mock_svc, capsys):
        mock_list.return_value = MOCK_FILES
        args = _make_args(plain=True)
        rc = cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 2
        parts = lines[0].split("\t")
        assert len(parts) == 3
        assert parts[0] == "doc1"
        assert parts[1] == "Meeting Notes"
        assert parts[2] == "application/vnd.google-apps.document"

    def test_ls_plain_empty(self, mock_list, mock_svc, capsys):
        mock_list.return_value = []
        args = _make_args(plain=True)
        rc = cmd_ls(args)
        assert rc == 0
        assert capsys.readouterr().out == ""
