"""Tests for the `gdoc diff` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_diff
from gdoc.util import GdocError


def _make_args(**overrides):
    """Build a SimpleNamespace mimicking parsed diff args."""
    defaults = {
        "command": "diff",
        "doc": "abc123",
        "file": "/tmp/local.md",
        "plain": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _doc(text, bold=False):
    """A one-tab native snapshot whose body is one paragraph of ``text``."""
    style = {"bold": True} if bold else {}
    return {"revisionId": "r1", "tabs": [{
        "tabProperties": {"tabId": "t1", "title": "Tab"},
        "documentTab": {"body": {"content": [{"paragraph": {
            "elements": [{"textRun": {"content": text.rstrip("\n"),
                                      "textStyle": style}},
                         {"textRun": {"content": "\n"}}],
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
        }}]}},
    }]}


def _version_data(version=42):
    return {"version": version, "modifiedTime": "2026-01-01T00:00:00Z"}


class TestDiffIdentical:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello world\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_identical_returns_zero(
        self, _pf, _export, _drv, _ver, _update, capsys, tmp_path,
    ):
        local = tmp_path / "doc.md"
        local.write_text("Hello world\n")
        args = _make_args(file=str(local))
        rc = cmd_diff(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK identical" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello world\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_identical_json(self, _pf, _export, _drv, _ver, _update, capsys, tmp_path):
        local = tmp_path / "doc.md"
        local.write_text("Hello world\n")
        args = _make_args(file=str(local), json=True)
        rc = cmd_diff(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["identical"] is True
        assert data["diff"] == ""


class TestDiffDifferent:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello world\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_different_returns_one(
        self, _pf, _export, _drv, _ver, _update, capsys, tmp_path,
    ):
        local = tmp_path / "doc.md"
        local.write_text("Hello universe\n")
        args = _make_args(file=str(local))
        rc = cmd_diff(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "---" in out
        assert "+++" in out
        assert "-Hello world" in out
        assert "+Hello universe" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello world\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_different_json(self, _pf, _export, _drv, _ver, _update, capsys, tmp_path):
        local = tmp_path / "doc.md"
        local.write_text("Hello universe\n")
        args = _make_args(file=str(local), json=True)
        rc = cmd_diff(args)
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["identical"] is False
        assert "-Hello world" in data["diff"]
        assert "+Hello universe" in data["diff"]


class TestDiffPlainText:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs",
           return_value=_doc("Hello world\n", bold=True))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_plain_compares_plain_text(
        self, _pf, _doc_get, _drv, _ver, _update, tmp_path,
    ):
        local = tmp_path / "doc.txt"
        local.write_text("Hello world\n")
        assert cmd_diff(_make_args(file=str(local), plain=True)) == 0

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs",
           return_value=_doc("Hello world\n", bold=True))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_default_compares_native_markdown(
        self, _pf, _doc_get, _drv, _ver, _update, tmp_path,
    ):
        local = tmp_path / "doc.md"
        local.write_text("**Hello world**\n")
        assert cmd_diff(_make_args(file=str(local))) == 0


class TestDiffErrors:
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_missing_local_file(self, _pf, _export):
        args = _make_args(file="/nonexistent/path.md")
        with pytest.raises(GdocError, match="file not found") as exc_info:
            cmd_diff(args)
        assert exc_info.value.exit_code == 3

    def test_invalid_doc_id(self):
        args = _make_args(doc="!!invalid!!")
        with pytest.raises(GdocError) as exc_info:
            cmd_diff(args)
        assert exc_info.value.exit_code == 3


class TestDiffAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_preflight_called(self, mock_pf, _export, _drv, _ver, _update, tmp_path):
        local = tmp_path / "doc.md"
        local.write_text("Hello\n")
        args = _make_args(file=str(local))
        cmd_diff(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data())
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_quiet_passed_to_preflight(
        self, mock_pf, _export, _drv, _ver, _update, tmp_path,
    ):
        local = tmp_path / "doc.md"
        local.write_text("Hello\n")
        args = _make_args(file=str(local), quiet=True)
        cmd_diff(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_version_data(99))
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_doc("Hello\n"))
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_state_updated_with_version(
        self, _pf, _export, _drv, _ver, mock_update, tmp_path,
    ):
        local = tmp_path / "doc.md"
        local.write_text("Hello\n")
        args = _make_args(file=str(local))
        cmd_diff(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="diff",
            quiet=False, command_version=99,
        )
