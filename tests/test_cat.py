"""Tests for the `gdoc cat` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_cat
from gdoc.util import GdocError


def _make_args(**overrides):
    """Build a SimpleNamespace mimicking parsed cat args."""
    defaults = {
        "command": "cat",
        "doc": "abc123",
        "plain": False,
        "comments": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCatMarkdown:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello World\n")
    def test_cat_default_markdown(self, mock_export, _mock_svc, capsys):
        args = _make_args()
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out == "# Hello World\n"
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")

    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="content")
    def test_cat_url_input(self, mock_export, _mock_svc, capsys):
        args = _make_args(doc="https://docs.google.com/document/d/abc123/edit")
        rc = cmd_cat(args)
        assert rc == 0
        mock_export.assert_called_once_with("abc123", mime_type="text/markdown")


class TestCatPlain:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="Hello World\n")
    def test_cat_plain(self, mock_export, _mock_svc, capsys):
        args = _make_args(plain=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out == "Hello World\n"
        mock_export.assert_called_once_with("abc123", mime_type="text/plain")


class TestCatJson:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.export_doc", return_value="# Hello")
    def test_cat_json_mode(self, mock_export, _mock_svc, capsys):
        args = _make_args(json=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data == {"ok": True, "content": "# Hello"}


class TestCatComments:
    def test_cat_comments_stub(self, capsys):
        args = _make_args(comments=True)
        rc = cmd_cat(args)
        assert rc == 4
        err = capsys.readouterr().err
        assert "not yet implemented" in err


class TestCatErrors:
    def test_cat_invalid_doc_id(self):
        args = _make_args(doc="!!invalid!!")
        with pytest.raises(GdocError) as exc_info:
            cmd_cat(args)
        assert exc_info.value.exit_code == 3

    def test_cat_empty_doc_id(self):
        args = _make_args(doc="")
        with pytest.raises(GdocError) as exc_info:
            cmd_cat(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.api.drive.get_drive_service")
    @patch(
        "gdoc.api.drive.export_doc",
        side_effect=GdocError("Document not found: abc"),
    )
    def test_cat_api_error(self, mock_export, _mock_svc):
        args = _make_args()
        with pytest.raises(GdocError, match="Document not found"):
            cmd_cat(args)
