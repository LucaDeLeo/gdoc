"""Tests for --tab and --all-tabs flags on `gdoc cat`."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_cat
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


@pytest.fixture(autouse=True)
def _doc_mime(doc_mime):
    """Keep spreadsheet detection on the Docs path for this module."""


def _make_args(**overrides):
    defaults = {
        "command": "cat",
        "doc": "abc123",
        "plain": False,
        "comments": False,
        "all": False,
        "tab": None,
        "all_tabs": False,
        "no_images": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _tab(id, title, text="", index=0, level=0):
    body = {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": text}}]}}
    ]} if text else {"content": []}
    return {
        "tabProperties": {"tabId": id, "title": title, "index": index},
        "documentTab": {"body": body},
    }


def _document(tabs):
    return {"revisionId": "r1", "tabs": tabs}



class TestCatTab:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="Tab content\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_by_title(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = _document([_tab("t1", "Notes")])
        args = _make_args(tab="Notes")
        rc = cmd_cat(args)
        assert rc == 0
        assert capsys.readouterr().out == "Tab content\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="content\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_case_insensitive(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        mock_tabs.return_value = _document([_tab("t1", "Notes")])
        args = _make_args(tab="notes")
        rc = cmd_cat(args)
        assert rc == 0
        mock_text.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="content\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_by_id(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = _document([_tab("t.abc", "My Tab")])
        args = _make_args(tab="t.abc")
        rc = cmd_cat(args)
        assert rc == 0
        mock_text.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="title match\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_id_preferred_over_title(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        """Exact IDs have the same priority as write and edit selectors."""
        mock_tabs.return_value = _document([
            _tab("t1", "t2"),  # title is "t2"
            _tab("t2", "Other"),
        ])
        args = _make_args(tab="t2")
        rc = cmd_cat(args)
        assert rc == 0
        # Exact ID selects the second tab despite the first tab title.
        mock_text.assert_called_once()
        called_tab = mock_text.call_args[0][0]
        assert called_tab["id"] == "t2"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_not_found(self, _svc, mock_tabs, _pf, _update):
        mock_tabs.return_value = _document([_tab("t1", "Tab 1")])
        args = _make_args(tab="nonexistent")
        with pytest.raises(GdocError, match="tab not found: nonexistent"):
            cmd_cat(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_json_output(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = _document([_tab("t1", "Notes")])
        args = _make_args(tab="Notes", json=True)
        rc = cmd_cat(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["tab"] == "Notes"
        assert data["content"] == "text\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_uses_docs_api_not_drive(
        self, _svc, mock_tabs, mock_text, _pf, _update,
    ):
        """--tab uses Docs API (get_document_with_tabs) not Drive export."""
        mock_tabs.return_value = _document([_tab("t1", "Tab 1")])
        args = _make_args(tab="Tab 1")
        with patch("gdoc.api.drive.export_doc") as mock_export:
            cmd_cat(args)
            mock_export.assert_not_called()
        mock_tabs.assert_called_once_with("abc123")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_default_requests_markdown(
        self, _svc, mock_tabs, mock_text, _pf, _update,
    ):
        """Default --tab renders markdown so headings survive round-trips."""
        mock_tabs.return_value = _document([_tab("t1", "Notes")])
        cmd_cat(_make_args(tab="Notes"))
        assert mock_text.call_args.kwargs.get("markdown") is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_plain_requests_verbatim(
        self, _svc, mock_tabs, mock_text, _pf, _update,
    ):
        """--plain --tab returns the verbatim, matchable text (no '#')."""
        mock_tabs.return_value = _document([_tab("t1", "Notes")])
        cmd_cat(_make_args(tab="Notes", plain=True))
        assert mock_text.call_args.kwargs.get("markdown") is False


class TestCatAllTabs:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_output(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = _document([
            _tab("t1", "First"),
            _tab("t2", "Second"),
        ])
        mock_text.side_effect = ["Hello\n", "World\n"]
        args = _make_args(all_tabs=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Tab: First ===" in out
        assert "Hello\n" in out
        assert "=== Tab: Second ===" in out
        assert "World\n" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_json(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = _document([_tab("t1", "Tab 1")])
        mock_text.return_value = "content\n"
        args = _make_args(all_tabs=True, json=True)
        rc = cmd_cat(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert "content" in data

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_empty(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = _document([])
        args = _make_args(all_tabs=True)
        with pytest.raises(GdocError, match="no readable tabs"):
            cmd_cat(args)


class TestCatTabMutualExclusivity:
    def test_all_tabs_and_comments_conflict(self):
        args = _make_args(all_tabs=True, comments=True, quiet=True)
        with pytest.raises(GdocError, match="mutually exclusive"):
            cmd_cat(args)


class TestCatTabAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_preflight_and_state(
        self, _svc, mock_tabs, _text, mock_pf, mock_update,
    ):
        change_info = ChangeInfo(current_version=7)
        mock_pf.return_value = change_info
        mock_tabs.return_value = _document([_tab("t1", "Tab 1")])
        args = _make_args(tab="Tab 1")
        rc = cmd_cat(args)
        assert rc == 0
        mock_pf.assert_called_once_with("abc123", quiet=False)
        mock_update.assert_called_once_with(
            "abc123", change_info, command="cat-content", quiet=False,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_preflight_and_state(
        self, _svc, mock_tabs, _text, mock_pf, mock_update,
    ):
        change_info = ChangeInfo(current_version=7)
        mock_pf.return_value = change_info
        mock_tabs.return_value = _document([_tab("t1", "Tab 1")])
        args = _make_args(all_tabs=True)
        rc = cmd_cat(args)
        assert rc == 0
        mock_pf.assert_called_once()
        mock_update.assert_called_once()
