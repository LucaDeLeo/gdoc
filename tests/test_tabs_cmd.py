"""Tests for the `gdoc tabs` and `gdoc add-tab` subcommands."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_add_tab, cmd_tabs
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


def _make_args(**overrides):
    defaults = {
        "command": "tabs",
        "doc": "abc123",
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _tab(id, title, index=0, level=0, body=None):
    return {
        "id": id, "title": title, "index": index,
        "nesting_level": level, "body": body or {},
    }


class TestTabsTerse:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_single_tab(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Tab 1")]
        rc = cmd_tabs(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "t1\tTab 1" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_multiple_tabs(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "First"), _tab("t2", "Second")]
        rc = cmd_tabs(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "t1\tFirst" in out
        assert "t2\tSecond" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_nested_indentation(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = [
            _tab("t1", "Parent", level=0),
            _tab("t2", "Child", level=1),
        ]
        rc = cmd_tabs(_make_args())
        assert rc == 0
        lines = capsys.readouterr().out.strip().split("\n")
        assert lines[0] == "t1\tParent"
        assert lines[1] == "  t2\tChild"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_tabs(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = []
        rc = cmd_tabs(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "No tabs." in out


class TestTabsJson:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_json_output(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Tab 1", index=0, level=0)]
        rc = cmd_tabs(_make_args(json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert len(data["tabs"]) == 1
        assert data["tabs"][0] == {
            "id": "t1", "title": "Tab 1", "index": 0, "nesting_level": 0,
        }

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_json_no_body(self, _svc, mock_tabs, _pf, _update, capsys):
        """JSON output should not include body content."""
        mock_tabs.return_value = [
            _tab("t1", "Tab 1", body={"content": [{"paragraph": {}}]}),
        ]
        rc = cmd_tabs(_make_args(json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "body" not in data["tabs"][0]


class TestTabsVerbose:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_verbose_output(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Tab 1", index=0, level=0)]
        rc = cmd_tabs(_make_args(verbose=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "t1\tTab 1\tindex=0\tlevel=0" in out


class TestTabsPlain:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_plain_output(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Tab 1")]
        rc = cmd_tabs(_make_args(plain=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "t1\tTab 1" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_plain_empty(self, _svc, mock_tabs, _pf, _update, capsys):
        """Plain mode prints nothing for empty tabs (no 'No tabs.' message)."""
        mock_tabs.return_value = []
        rc = cmd_tabs(_make_args(plain=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert out == ""


class TestTabsErrors:
    def test_invalid_doc_id(self):
        args = _make_args(doc="!!invalid!!")
        with pytest.raises(GdocError) as exc_info:
            cmd_tabs(args)
        assert exc_info.value.exit_code == 3


class TestTabsAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_document_tabs", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_preflight_and_state_update(self, _svc, _tabs, mock_pf, mock_update):
        change_info = ChangeInfo(current_version=5)
        mock_pf.return_value = change_info
        rc = cmd_tabs(_make_args())
        assert rc == 0
        mock_pf.assert_called_once_with("abc123", quiet=False)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="tabs", quiet=False,
        )


# ── add-tab command ──────────────────────────────────────────────


def _make_add_tab_args(**overrides):
    defaults = {
        "command": "add-tab",
        "doc": "abc123",
        "title": "New Tab",
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_ADD_TAB_RESULT = {"tabId": "t99", "title": "New Tab", "index": 2}


class TestAddTab:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.add_tab", return_value=_ADD_TAB_RESULT)
    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_terse(self, _svc, mock_add, _pf, _ver, _update, capsys):
        rc = cmd_add_tab(_make_add_tab_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "t99\tNew Tab" in out
        mock_add.assert_called_once_with("abc123", "New Tab")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.add_tab", return_value=_ADD_TAB_RESULT)
    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_json(self, _svc, _add, _pf, _ver, _update, capsys):
        rc = cmd_add_tab(_make_add_tab_args(json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["id"] == "t99"
        assert data["title"] == "New Tab"
        assert data["index"] == 2
        assert data["doc_id"] == "abc123"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.add_tab", return_value=_ADD_TAB_RESULT)
    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_verbose(self, _svc, _add, _pf, _ver, _update, capsys):
        rc = cmd_add_tab(_make_add_tab_args(verbose=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Added tab: New Tab" in out
        assert "ID: t99" in out
        assert "Index: 2" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.add_tab", return_value=_ADD_TAB_RESULT)
    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_plain(self, _svc, _add, _pf, _ver, _update, capsys):
        rc = cmd_add_tab(_make_add_tab_args(plain=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tt99" in out
        assert "title\tNew Tab" in out
        assert "index\t2" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.add_tab", return_value=_ADD_TAB_RESULT)
    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_preflight(self, _svc, _add, mock_pf, _ver, mock_update):
        change_info = ChangeInfo(current_version=7)
        mock_pf.return_value = change_info
        rc = cmd_add_tab(_make_add_tab_args())
        assert rc == 0
        mock_pf.assert_called_once_with("abc123", quiet=False)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="add-tab", quiet=False,
            command_version=10,
        )

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.add_tab")
    def test_add_tab_api_error(self, mock_add, _pf, _svc):
        mock_add.side_effect = GdocError("Document not found: abc123")
        with pytest.raises(GdocError, match="Document not found"):
            cmd_add_tab(_make_add_tab_args())
