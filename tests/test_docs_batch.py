"""Tests for get_document, find_text_in_document, replace_formatted."""

from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.docs import (
    find_text_in_document,
    get_document,
    replace_formatted,
)
from gdoc.util import AuthError, GdocError


def _mock_document(text_runs, revision_id="rev123"):
    """Build a minimal document dict for testing.

    text_runs is a list of (startIndex, content) tuples.
    """
    elements = []
    for start, content in text_runs:
        elements.append({
            "startIndex": start,
            "endIndex": start + len(content),
            "textRun": {"content": content},
        })
    return {
        "revisionId": revision_id,
        "body": {
            "content": [
                {"paragraph": {"elements": elements}},
            ]
        },
    }


def _mock_document_multi_para(paragraphs, revision_id="rev123"):
    """Build a document with multiple paragraphs.

    paragraphs: list of lists of (startIndex, content) tuples.
    """
    content = []
    for para_runs in paragraphs:
        elements = []
        for start, text in para_runs:
            elements.append({
                "startIndex": start,
                "endIndex": start + len(text),
                "textRun": {"content": text},
            })
        content.append({"paragraph": {"elements": elements}})
    return {
        "revisionId": revision_id,
        "body": {"content": content},
    }


def _docs_chain(mock_svc):
    """Shorthand for the mock service call chain."""
    return mock_svc.return_value.documents.return_value


class TestGetDocument:
    @patch("gdoc.api.docs.get_docs_service")
    def test_returns_document(self, mock_svc):
        doc = {"revisionId": "abc", "body": {"content": []}}
        chain = _docs_chain(mock_svc)
        chain.get.return_value.execute.return_value = doc
        result = get_document("doc123")
        assert result == doc
        chain.get.assert_called_once_with(documentId="doc123")

    @patch("gdoc.api.docs.get_docs_service")
    def test_translates_404(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock(status=404)
        chain = _docs_chain(mock_svc)
        chain.get.return_value.execute.side_effect = (
            HttpError(resp, b"not found")
        )
        with pytest.raises(GdocError, match="Document not found"):
            get_document("doc123")

    @patch("gdoc.api.docs.get_docs_service")
    def test_translates_401(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock(status=401)
        chain = _docs_chain(mock_svc)
        chain.get.return_value.execute.side_effect = (
            HttpError(resp, b"unauthorized")
        )
        with pytest.raises(AuthError, match="Authentication expired"):
            get_document("doc123")


class TestFindTextInDocument:
    def test_single_match(self):
        doc = _mock_document([(1, "hello world\n")])
        matches = find_text_in_document(doc, "hello")
        assert len(matches) == 1
        assert matches[0] == {"startIndex": 1, "endIndex": 6}

    def test_multiple_matches(self):
        doc = _mock_document([(1, "hello and hello\n")])
        matches = find_text_in_document(doc, "hello")
        assert len(matches) == 2
        assert matches[0]["startIndex"] == 1
        assert matches[1]["startIndex"] == 11

    def test_no_match(self):
        doc = _mock_document([(1, "hello world\n")])
        matches = find_text_in_document(doc, "zzz")
        assert matches == []

    def test_case_insensitive(self):
        doc = _mock_document([(1, "Hello World\n")])
        result = find_text_in_document(doc, "hello", match_case=False)
        assert len(result) == 1

    def test_case_sensitive_no_match(self):
        doc = _mock_document([(1, "Hello World\n")])
        result = find_text_in_document(doc, "hello", match_case=True)
        assert result == []

    def test_case_sensitive_match(self):
        doc = _mock_document([(1, "Hello World\n")])
        result = find_text_in_document(doc, "Hello", match_case=True)
        assert len(result) == 1

    def test_cross_textrun_match(self):
        """Text spans two textRun elements."""
        doc = _mock_document([(1, "hel"), (4, "lo world\n")])
        matches = find_text_in_document(doc, "hello")
        assert len(matches) == 1
        assert matches[0] == {"startIndex": 1, "endIndex": 6}

    def test_empty_document(self):
        doc = {"body": {"content": []}}
        assert find_text_in_document(doc, "anything") == []

    def test_multi_paragraph(self):
        doc = _mock_document_multi_para([
            [(1, "first paragraph\n")],
            [(18, "second paragraph\n")],
        ])
        matches = find_text_in_document(doc, "paragraph")
        assert len(matches) == 2


class TestReplaceFormatted:
    @patch("gdoc.api.docs.get_docs_service")
    def test_single_plain_replacement(self, mock_svc):
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 5, "endIndex": 10}]
        result = replace_formatted("d1", matches, "hello", "r1")
        assert result == 1
        call_args = chain.batchUpdate.call_args
        body = call_args[1]["body"]
        assert body["writeControl"]["requiredRevisionId"] == "r1"
        assert "deleteContentRange" in body["requests"][0]
        assert "insertText" in body["requests"][1]

    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_match_order(self, mock_svc):
        """Matches processed last-to-first."""
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [
            {"startIndex": 5, "endIndex": 10},
            {"startIndex": 20, "endIndex": 25},
        ]
        result = replace_formatted("d1", matches, "x", "r1")
        assert result == 2
        body = chain.batchUpdate.call_args[1]["body"]
        first_del = body["requests"][0]
        assert first_del["deleteContentRange"]["range"]["startIndex"] == 20

    @patch("gdoc.api.docs.get_docs_service")
    def test_overlapping_matches_are_rejected(self, mock_svc):
        """`aa` in `aaa` matches [1,3) and [2,4): the last-to-first plan
        would land on shifted text, so refuse — same guard as suggest."""
        chain = _docs_chain(mock_svc)
        matches = [
            {"startIndex": 1, "endIndex": 3},
            {"startIndex": 2, "endIndex": 4},
        ]
        with pytest.raises(GdocError, match="overlap each other") as exc:
            replace_formatted("d1", matches, "b", "r1")
        assert exc.value.exit_code == 3
        chain.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_formatted_replacement(self, mock_svc):
        """Markdown generates style requests."""
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 5, "endIndex": 10}]
        result = replace_formatted("d1", matches, "**bold**", "r1")
        assert result == 1
        body = chain.batchUpdate.call_args[1]["body"]
        req_types = [list(r.keys())[0] for r in body["requests"]]
        assert "deleteContentRange" in req_types
        assert "insertText" in req_types
        assert "updateTextStyle" in req_types

    @patch("gdoc.api.docs.get_docs_service")
    def test_inline_replacement_leaves_paragraph_style_alone(self, mock_svc):
        """A NORMAL_TEXT reset covers the whole paragraph and re-resolves its
        runs, stripping bold outside the match and demoting headings — so an
        inline replacement must not send one."""
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 20, "endIndex": 30}]
        replace_formatted("d1", matches, "has a [cap](https://x.y)", "r1")
        reqs = chain.batchUpdate.call_args_list[0][1]["body"]["requests"]
        req_types = [list(r.keys())[0] for r in reqs]
        assert "updateParagraphStyle" not in req_types
        assert "updateTextStyle" in req_types

    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_paragraph_replacement_styles_only_new_paragraphs(
        self, mock_svc,
    ):
        """The first and last paragraphs join existing text and keep its
        style; a wholly new middle paragraph is still set to NORMAL_TEXT."""
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 20, "endIndex": 30}]
        replace_formatted("d1", matches, "first\nmiddle\nlast", "r1")
        reqs = chain.batchUpdate.call_args_list[0][1]["body"]["requests"]
        ups = [r["updateParagraphStyle"] for r in reqs
               if "updateParagraphStyle" in r]
        assert len(ups) == 1
        # "middle\n" spans plain-text offsets 6..13.
        assert ups[0]["range"] == {"startIndex": 26, "endIndex": 33}
        assert ups[0]["paragraphStyle"] == {"namedStyleType": "NORMAL_TEXT"}

    @patch("gdoc.api.docs.get_docs_service")
    def test_explicit_heading_replacement_still_styled(self, mock_svc):
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 5, "endIndex": 10}]
        replace_formatted("d1", matches, "## Title", "r1")
        reqs = chain.batchUpdate.call_args_list[0][1]["body"]["requests"]
        ups = [r["updateParagraphStyle"] for r in reqs
               if "updateParagraphStyle" in r]
        assert [u["paragraphStyle"] for u in ups] == [
            {"namedStyleType": "HEADING_2"},
        ]

    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_matches_returns_zero(self, mock_svc):
        result = replace_formatted("d1", [], "text", "r1")
        assert result == 0
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_translates_http_error(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock(status=403)
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute.side_effect = (
            HttpError(resp, b"forbidden")
        )
        matches = [{"startIndex": 5, "endIndex": 10}]
        with pytest.raises(GdocError, match="Permission denied"):
            replace_formatted("d1", matches, "text", "r1")
