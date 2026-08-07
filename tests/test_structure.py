"""Tests for `gdoc structure` and its API helpers."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.docs import get_document_structure, resolve_raw_tab
from gdoc.cli import cmd_structure
from gdoc.notify import ChangeInfo
from gdoc.util import SPREADSHEET_MIME, GdocError


def _http_error(status, content=b""):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Error"
    return HttpError(resp, content, uri="")


def _make_args(**overrides):
    defaults = {
        "command": "structure",
        "doc": "doc123",
        "tab": None,
        "fields": None,
        "suggestions_view_mode": None,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _tab(tab_id, title, children=None):
    tab = {
        "tabProperties": {"tabId": tab_id, "title": title, "index": 0},
        "documentTab": {"body": {"content": [{"endIndex": 2}]}},
    }
    if children:
        tab["childTabs"] = children
    return tab


_DOC = {
    "documentId": "doc123",
    "title": "Spec",
    "revisionId": "rev7",
    "tabs": [
        _tab("t1", "Main"),
        _tab("t2", "Notes", children=[_tab("t2c", "Appendix")]),
    ],
}


class TestGetDocumentStructureAPI:
    @patch("gdoc.api.docs.get_docs_service")
    def test_default_requests_tab_content(self, mock_svc):
        service = MagicMock()
        get = service.documents.return_value.get
        get.return_value.execute.return_value = _DOC
        mock_svc.return_value = service

        result = get_document_structure("doc123")

        assert result == _DOC
        assert get.call_args.kwargs == {
            "documentId": "doc123", "includeTabsContent": True,
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_fields_and_view_mode_passed(self, mock_svc):
        service = MagicMock()
        get = service.documents.return_value.get
        get.return_value.execute.return_value = {}
        mock_svc.return_value = service

        get_document_structure(
            "doc123",
            fields="revisionId,tabs(tabProperties)",
            suggestions_view_mode="PREVIEW_SUGGESTIONS_ACCEPTED",
        )

        kwargs = get.call_args.kwargs
        assert kwargs["fields"] == "revisionId,tabs(tabProperties)"
        assert kwargs["suggestionsViewMode"] == "PREVIEW_SUGGESTIONS_ACCEPTED"
        assert kwargs["includeTabsContent"] is True

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_raises_gdoc_error(self, mock_svc):
        service = MagicMock()
        get = service.documents.return_value.get
        get.return_value.execute.side_effect = _http_error(404)
        mock_svc.return_value = service

        with pytest.raises(GdocError, match="not found"):
            get_document_structure("doc123")


class TestResolveRawTab:
    def test_title_match_case_insensitive(self):
        tab = resolve_raw_tab(_DOC["tabs"], "notes")
        assert tab["tabProperties"]["tabId"] == "t2"

    def test_id_match(self):
        tab = resolve_raw_tab(_DOC["tabs"], "t1")
        assert tab["tabProperties"]["title"] == "Main"

    def test_child_tab_found(self):
        tab = resolve_raw_tab(_DOC["tabs"], "Appendix")
        assert tab["tabProperties"]["tabId"] == "t2c"

    def test_title_beats_id(self):
        # A tab titled "t1" must win over the tab whose ID is "t1".
        tabs = [_tab("t1", "Main"), _tab("x9", "t1")]
        tab = resolve_raw_tab(tabs, "t1")
        assert tab["tabProperties"]["tabId"] == "x9"

    def test_not_found_returns_none(self):
        assert resolve_raw_tab(_DOC["tabs"], "missing") is None


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.docs.get_document_structure", return_value=_DOC)
class TestCmdStructure:
    def test_terse_prints_compact_json(self, mock_get, _update, capsys):
        args = _make_args()
        rc = cmd_structure(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert json.loads(out) == _DOC
        assert "\n" not in out.strip()
        assert '"documentId":"doc123"' in out  # compact separators

    def test_verbose_prints_indented(self, mock_get, _update, capsys):
        args = _make_args(verbose=True)
        cmd_structure(args)
        out = capsys.readouterr().out
        assert json.loads(out) == _DOC
        assert out.startswith("{\n  ")

    def test_json_mode_wraps_envelope(self, mock_get, _update, capsys):
        args = _make_args(json=True)
        cmd_structure(args)
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["document"] == _DOC

    def test_tab_narrows_output(self, mock_get, _update, capsys):
        args = _make_args(tab="Notes")
        cmd_structure(args)
        data = json.loads(capsys.readouterr().out)
        assert data["documentId"] == "doc123"
        assert data["revisionId"] == "rev7"
        assert data["tab"]["tabProperties"]["tabId"] == "t2"
        assert "tabs" not in data

    def test_tab_not_found_exits_3(self, mock_get, _update):
        args = _make_args(tab="Nope")
        with pytest.raises(GdocError, match="tab not found") as exc_info:
            cmd_structure(args)
        assert exc_info.value.exit_code == 3

    def test_fields_and_view_mode_forwarded(self, mock_get, _update):
        args = _make_args(
            fields="revisionId",
            suggestions_view_mode="preview_suggestions_accepted",
        )
        cmd_structure(args)
        mock_get.assert_called_once_with(
            "doc123",
            fields="revisionId",
            suggestions_view_mode="PREVIEW_SUGGESTIONS_ACCEPTED",
        )

    def test_requested_view_mode_echoed(self, mock_get, _update, capsys):
        mock_get.return_value = {"documentId": "doc123"}
        args = _make_args(
            suggestions_view_mode="preview_without_suggestions",
        )
        cmd_structure(args)
        data = json.loads(capsys.readouterr().out)
        assert data["suggestionsViewMode"] == "PREVIEW_WITHOUT_SUGGESTIONS"

    def test_state_updated_as_structure(self, mock_get, mock_update):
        args = _make_args()
        cmd_structure(args)
        assert mock_update.call_args.kwargs["command"] == "structure"

    def test_spreadsheet_rejected(self, mock_get, _update):
        change_info = ChangeInfo(mime_type=SPREADSHEET_MIME)
        with patch("gdoc.notify.pre_flight", return_value=change_info):
            args = _make_args(quiet=False)
            with pytest.raises(GdocError, match="not a Google Doc") as e:
                cmd_structure(args)
            assert e.value.exit_code == 3
        mock_get.assert_not_called()
