"""Tests for tab-related functions in gdoc.api.docs."""

from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.docs import (
    _extract_paragraphs_text,
    flatten_tabs,
    get_document_tabs,
    get_tab_text,
    resolve_tab,
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
            "nesting_level": 0, "body": {"content": []}, "lists": {},
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

    def _heading(self, text, style):
        return {"paragraph": {
            "paragraphStyle": {"namedStyleType": style},
            "elements": [{"textRun": {"content": text}}],
        }}

    def test_heading_plain_by_default(self):
        # Default (markdown=False) is the matchable form gdoc edit uses:
        # no "#" prefix, so a heading reads back as its bare text.
        tab = {"body": {"content": [self._heading("Title\n", "HEADING_1")]}}
        assert get_tab_text(tab) == "Title\n"

    def test_heading_markdown_prefix_levels(self):
        tab = {"body": {"content": [
            self._heading("One\n", "HEADING_1"),
            self._heading("Two\n", "HEADING_2"),
            self._heading("Six\n", "HEADING_6"),
        ]}}
        assert get_tab_text(tab, markdown=True) == "# One\n## Two\n###### Six\n"

    def test_heading_markdown_leaves_normal_text(self):
        tab = {"body": {"content": [
            self._heading("Body\n", "NORMAL_TEXT"),
        ]}}
        assert get_tab_text(tab, markdown=True) == "Body\n"

    def test_heading_markdown_collapses_leading_space(self):
        # A stored leading space must not stack into "##  Two"; lstrip
        # keeps the round-trip stable.
        tab = {"body": {"content": [self._heading(" Two\n", "HEADING_2")]}}
        assert get_tab_text(tab, markdown=True) == "## Two\n"

    def test_heading_markdown_ignores_blank_heading(self):
        tab = {"body": {"content": [self._heading("\n", "HEADING_1")]}}
        assert get_tab_text(tab, markdown=True) == "\n"


def _run(text, **style):
    return {"textRun": {"content": text, "textStyle": style}}


def _para(*runs, style="NORMAL_TEXT", bullet=None):
    p = {
        "paragraphStyle": {"namedStyleType": style},
        "elements": list(runs),
    }
    if bullet is not None:
        p["bullet"] = bullet
    return {"paragraph": p}


class TestGetTabTextInlineMarkdown:
    def test_bold(self):
        tab = {"body": {"content": [_para(_run("hi "), _run("there\n", bold=True))]}}
        assert get_tab_text(tab, markdown=True) == "hi **there**\n"

    def test_italic(self):
        tab = {"body": {"content": [_para(_run("a "), _run("b\n", italic=True))]}}
        assert get_tab_text(tab, markdown=True) == "a *b*\n"

    def test_bold_italic(self):
        tab = {"body": {"content": [_para(_run("x\n", bold=True, italic=True))]}}
        assert get_tab_text(tab, markdown=True) == "***x***\n"

    def test_strikethrough(self):
        tab = {"body": {"content": [_para(_run("gone\n", strikethrough=True))]}}
        assert get_tab_text(tab, markdown=True) == "~~gone~~\n"

    def test_link_uses_url_not_underline(self):
        # Docs auto-underlines links; the underline flag must not leak out.
        run = _run("site\n", underline=True, link={"url": "https://e.com"})
        tab = {"body": {"content": [_para(run)]}}
        assert get_tab_text(tab, markdown=True) == "[site](https://e.com)\n"

    def test_spaces_kept_outside_markers(self):
        tab = {"body": {"content": [_para(_run(" b \n", bold=True))]}}
        assert get_tab_text(tab, markdown=True) == " **b** \n"

    def test_plain_mode_ignores_styles(self):
        tab = {"body": {"content": [_para(_run("x\n", bold=True))]}}
        assert get_tab_text(tab) == "x\n"


class TestGetTabTextListMarkdown:
    _BULLETS = {"L1": {"listProperties": {"nestingLevels": [
        {"glyphSymbol": "●"}, {"glyphSymbol": "○"},
    ]}}}
    _ORDERED = {"L2": {"listProperties": {"nestingLevels": [
        {"glyphType": "DECIMAL"}, {"glyphType": "ALPHA"},
    ]}}}

    def test_bullet_list(self):
        tab = {"lists": self._BULLETS, "body": {"content": [
            _para(_run("one\n"), bullet={"listId": "L1"}),
            _para(_run("two\n"), bullet={"listId": "L1"}),
        ]}}
        assert get_tab_text(tab, markdown=True) == "- one\n- two\n"

    def test_ordered_list_counts(self):
        tab = {"lists": self._ORDERED, "body": {"content": [
            _para(_run("a\n"), bullet={"listId": "L2"}),
            _para(_run("b\n"), bullet={"listId": "L2"}),
            _para(_run("c\n"), bullet={"listId": "L2"}),
        ]}}
        assert get_tab_text(tab, markdown=True) == "1. a\n2. b\n3. c\n"

    def test_nested_bullet_indented(self):
        tab = {"lists": self._BULLETS, "body": {"content": [
            _para(_run("top\n"), bullet={"listId": "L1", "nestingLevel": 0}),
            _para(_run("sub\n"), bullet={"listId": "L1", "nestingLevel": 1}),
        ]}}
        assert get_tab_text(tab, markdown=True) == "- top\n  - sub\n"

    def test_ordered_numbering_resets_after_break(self):
        tab = {"lists": self._ORDERED, "body": {"content": [
            _para(_run("a\n"), bullet={"listId": "L2"}),
            _para(_run("b\n"), bullet={"listId": "L2"}),
            _para(_run("\n")),  # blank paragraph ends the list
            _para(_run("a\n"), bullet={"listId": "L2"}),
        ]}}
        assert get_tab_text(tab, markdown=True) == "1. a\n2. b\n\n1. a\n"

    def test_nested_ordered_counters_independent(self):
        tab = {"lists": self._ORDERED, "body": {"content": [
            _para(_run("one\n"), bullet={"listId": "L2", "nestingLevel": 0}),
            _para(_run("a\n"), bullet={"listId": "L2", "nestingLevel": 1}),
            _para(_run("b\n"), bullet={"listId": "L2", "nestingLevel": 1}),
            _para(_run("two\n"), bullet={"listId": "L2", "nestingLevel": 0}),
        ]}}
        assert get_tab_text(tab, markdown=True) == (
            "1. one\n  1. a\n  2. b\n2. two\n"
        )

    def test_lists_ignored_in_plain_mode(self):
        tab = {"lists": self._BULLETS, "body": {"content": [
            _para(_run("one\n"), bullet={"listId": "L1"}),
        ]}}
        assert get_tab_text(tab) == "one\n"


class TestResolveTab:
    def _tabs(self):
        return [
            {"id": "t1", "title": "Tab One", "index": 0,
             "nesting_level": 0, "body": {}},
            {"id": "t2", "title": "Tab Two", "index": 1,
             "nesting_level": 0, "body": {}},
        ]

    def test_match_by_title(self):
        result = resolve_tab(self._tabs(), "Tab One")
        assert result["id"] == "t1"

    def test_match_by_title_case_insensitive(self):
        result = resolve_tab(self._tabs(), "tab one")
        assert result["id"] == "t1"

    def test_match_by_id(self):
        result = resolve_tab(self._tabs(), "t2")
        assert result["id"] == "t2"

    def test_title_priority_over_id(self):
        """When a title matches, it takes priority over ID match."""
        tabs = [
            {"id": "t1", "title": "t2", "index": 0, "nesting_level": 0, "body": {}},
            {"id": "t2", "title": "Other", "index": 1, "nesting_level": 0, "body": {}},
        ]
        result = resolve_tab(tabs, "t2")
        assert result["id"] == "t1"  # title match wins

    def test_not_found_raises(self):
        with pytest.raises(GdocError, match="tab not found: nope") as exc_info:
            resolve_tab(self._tabs(), "nope")
        assert exc_info.value.exit_code == 3

    def test_empty_tabs_raises(self):
        with pytest.raises(GdocError, match="tab not found"):
            resolve_tab([], "anything")

    def test_match_by_title_exact_case(self):
        result = resolve_tab(self._tabs(), "Tab Two")
        assert result["id"] == "t2"
