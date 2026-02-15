"""Tests for tab-related functions in gdoc.api.docs."""

from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.docs import (
    _extract_paragraphs_text,
    flatten_tabs,
    get_document_tabs,
    get_tab_text,
)
from gdoc.util import AuthError, GdocError


class TestExtractParagraphsText:
    def test_single_paragraph(self):
        content = [{"paragraph": {"elements": [
            {"textRun": {"content": "Hello world\n"}}
        ]}}]
        assert _extract_paragraphs_text(content) == "Hello world\n"

    def test_multiple_paragraphs(self):
        content = [
            {"paragraph": {"elements": [{"textRun": {"content": "Line 1\n"}}]}},
            {"paragraph": {"elements": [{"textRun": {"content": "Line 2\n"}}]}},
        ]
        assert _extract_paragraphs_text(content) == "Line 1\nLine 2\n"

    def test_empty_content(self):
        assert _extract_paragraphs_text([]) == ""

    def test_no_text_run(self):
        content = [{"paragraph": {"elements": [{"inlineObjectElement": {}}]}}]
        assert _extract_paragraphs_text(content) == ""

    def test_non_paragraph_elements_skipped(self):
        content = [
            {"table": {"tableRows": []}},
            {"paragraph": {"elements": [{"textRun": {"content": "text\n"}}]}},
        ]
        assert _extract_paragraphs_text(content) == "text\n"

    def test_multiple_elements_in_paragraph(self):
        content = [{"paragraph": {"elements": [
            {"textRun": {"content": "Hello "}},
            {"textRun": {"content": "world\n"}},
        ]}}]
        assert _extract_paragraphs_text(content) == "Hello world\n"


class TestFlattenTabs:
    def test_single_tab(self):
        tabs = [{
            "tabProperties": {"tabId": "t1", "title": "Tab 1", "index": 0},
            "documentTab": {"body": {"content": []}},
        }]
        result = flatten_tabs(tabs)
        assert len(result) == 1
        assert result[0] == {
            "id": "t1", "title": "Tab 1", "index": 0,
            "nesting_level": 0, "body": {"content": []},
        }

    def test_multiple_tabs(self):
        tabs = [
            {
                "tabProperties": {"tabId": "t1", "title": "Tab 1", "index": 0},
                "documentTab": {"body": {}},
            },
            {
                "tabProperties": {"tabId": "t2", "title": "Tab 2", "index": 1},
                "documentTab": {"body": {}},
            },
        ]
        result = flatten_tabs(tabs)
        assert len(result) == 2
        assert result[0]["id"] == "t1"
        assert result[1]["id"] == "t2"

    def test_nested_tabs(self):
        tabs = [{
            "tabProperties": {"tabId": "t1", "title": "Parent", "index": 0},
            "documentTab": {"body": {}},
            "childTabs": [{
                "tabProperties": {"tabId": "t2", "title": "Child", "index": 0},
                "documentTab": {"body": {}},
            }],
        }]
        result = flatten_tabs(tabs)
        assert len(result) == 2
        assert result[0]["nesting_level"] == 0
        assert result[1]["nesting_level"] == 1
        assert result[1]["id"] == "t2"

    def test_deeply_nested_tabs(self):
        tabs = [{
            "tabProperties": {"tabId": "t1", "title": "L0", "index": 0},
            "documentTab": {"body": {}},
            "childTabs": [{
                "tabProperties": {"tabId": "t2", "title": "L1", "index": 0},
                "documentTab": {"body": {}},
                "childTabs": [{
                    "tabProperties": {"tabId": "t3", "title": "L2", "index": 0},
                    "documentTab": {"body": {}},
                }],
            }],
        }]
        result = flatten_tabs(tabs)
        assert len(result) == 3
        assert [t["nesting_level"] for t in result] == [0, 1, 2]

    def test_empty_tabs(self):
        assert flatten_tabs([]) == []

    def test_missing_properties(self):
        tabs = [{"tabProperties": {}, "documentTab": {}}]
        result = flatten_tabs(tabs)
        assert result[0]["id"] == ""
        assert result[0]["title"] == ""
        assert result[0]["index"] == 0
        assert result[0]["body"] == {}


class TestGetDocumentTabs:
    @patch("gdoc.api.docs.get_docs_service")
    def test_api_call_with_include_tabs(self, mock_svc):
        mock_doc = {
            "tabs": [{
                "tabProperties": {"tabId": "t1", "title": "Tab 1", "index": 0},
                "documentTab": {"body": {"content": []}},
            }]
        }
        docs = mock_svc.return_value.documents.return_value
        docs.get.return_value.execute.return_value = mock_doc

        result = get_document_tabs("doc123")

        call_kwargs = docs.get.call_args
        assert call_kwargs == ((), {"documentId": "doc123", "includeTabsContent": True})
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    @patch("gdoc.api.docs.get_docs_service")
    def test_returns_flat_list(self, mock_svc):
        mock_doc = {
            "tabs": [{
                "tabProperties": {"tabId": "t1", "title": "Parent", "index": 0},
                "documentTab": {"body": {}},
                "childTabs": [{
                    "tabProperties": {"tabId": "t2", "title": "Child", "index": 0},
                    "documentTab": {"body": {}},
                }],
            }]
        }
        docs = mock_svc.return_value.documents.return_value
        docs.get.return_value.execute.return_value = mock_doc

        result = get_document_tabs("doc123")
        assert len(result) == 2

    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_tabs(self, mock_svc):
        mock_doc = {"tabs": []}
        docs = mock_svc.return_value.documents.return_value
        docs.get.return_value.execute.return_value = mock_doc
        assert get_document_tabs("doc123") == []

    @patch("gdoc.api.docs.get_docs_service")
    def test_http_404_error(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        resp.status = 404
        err = HttpError(resp, b"not found", uri="")
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.side_effect = err
        with pytest.raises(GdocError, match="Document not found"):
            get_document_tabs("doc123")

    @patch("gdoc.api.docs.get_docs_service")
    def test_http_401_error(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        resp.status = 401
        err = HttpError(resp, b"unauthorized", uri="")
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.side_effect = err
        with pytest.raises(AuthError):
            get_document_tabs("doc123")


class TestGetTabText:
    def test_single_paragraph(self):
        tab = {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}
        ]}}
        assert get_tab_text(tab) == "Hello\n"

    def test_multiple_paragraphs(self):
        tab = {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {"content": "A\n"}}]}},
            {"paragraph": {"elements": [{"textRun": {"content": "B\n"}}]}},
        ]}}
        assert get_tab_text(tab) == "A\nB\n"

    def test_empty_body(self):
        tab = {"body": {"content": []}}
        assert get_tab_text(tab) == ""

    def test_missing_body(self):
        tab = {}
        assert get_tab_text(tab) == ""

    def test_table_elements(self):
        def _cell(text):
            return {"content": [
                {"paragraph": {"elements": [
                    {"textRun": {"content": text}},
                ]}},
            ]}

        tab = {"body": {"content": [
            {"table": {"tableRows": [
                {"tableCells": [_cell("A"), _cell("B")]},
                {"tableCells": [_cell("C"), _cell("D")]},
            ]}}
        ]}}
        result = get_tab_text(tab)
        assert "A\tB\n" in result
        assert "C\tD\n" in result

    def test_mixed_content(self):
        def _cell(text):
            return {"content": [
                {"paragraph": {"elements": [
                    {"textRun": {"content": text}},
                ]}},
            ]}

        tab = {"body": {"content": [
            {"paragraph": {"elements": [
                {"textRun": {"content": "Before\n"}},
            ]}},
            {"table": {"tableRows": [
                {"tableCells": [_cell("X")]},
            ]}},
            {"paragraph": {"elements": [
                {"textRun": {"content": "After\n"}},
            ]}},
        ]}}
        result = get_tab_text(tab)
        assert result.startswith("Before\n")
        assert "X\n" in result
        assert result.endswith("After\n")

    def test_no_text_run(self):
        tab = {"body": {"content": [
            {"paragraph": {"elements": [{"inlineObjectElement": {}}]}}
        ]}}
        assert get_tab_text(tab) == ""
