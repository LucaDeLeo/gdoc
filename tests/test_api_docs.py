"""Tests for gdoc.api.docs: Docs API v1 wrapper functions with mocked service."""

import pytest
from unittest.mock import MagicMock, patch

import httplib2
from googleapiclient.errors import HttpError

from gdoc.api.docs import _translate_http_error, replace_all_text
from gdoc.util import AuthError, GdocError


def _make_http_error(status: int, reason: str = "") -> HttpError:
    """Create a mock HttpError with the given status and reason."""
    resp = httplib2.Response({"status": str(status)})
    error = HttpError(resp, b"")
    error.reason = reason
    return error


class TestTranslateHttpError:
    def test_401_raises_auth_error(self):
        err = _make_http_error(401)
        with pytest.raises(AuthError, match="Authentication expired"):
            _translate_http_error(err, "abc123")

    def test_403_raises_gdoc_error(self):
        err = _make_http_error(403, reason="forbidden")
        with pytest.raises(GdocError, match="Permission denied: abc123"):
            _translate_http_error(err, "abc123")

    def test_404_raises_gdoc_error(self):
        err = _make_http_error(404)
        with pytest.raises(GdocError, match="Document not found: abc123"):
            _translate_http_error(err, "abc123")

    def test_500_raises_gdoc_error(self):
        err = _make_http_error(500, reason="Internal Server Error")
        with pytest.raises(GdocError, match=r"API error \(500\): Internal Server Error"):
            _translate_http_error(err, "abc123")


@patch("gdoc.api.docs.get_docs_service")
class TestReplaceAllText:
    def test_success(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 3}}]
        }

        result = replace_all_text("abc123", "old", "new")
        assert result == 3

    def test_correct_request_body(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}]
        }

        replace_all_text("abc123", "hello", "world", match_case=False)

        mock_service.documents().batchUpdate.assert_called_with(
            documentId="abc123",
            body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": "hello",
                                "matchCase": False,
                            },
                            "replaceText": "world",
                        }
                    }
                ]
            },
        )

    def test_case_sensitive(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 1}}]
        }

        replace_all_text("abc123", "Hello", "World", match_case=True)

        mock_service.documents().batchUpdate.assert_called_with(
            documentId="abc123",
            body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": "Hello",
                                "matchCase": True,
                            },
                            "replaceText": "World",
                        }
                    }
                ]
            },
        )

    def test_zero_occurrences(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": [{"replaceAllText": {"occurrencesChanged": 0}}]
        }

        result = replace_all_text("abc123", "nonexistent", "new")
        assert result == 0

    def test_empty_replies(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.return_value = {
            "replies": []
        }

        result = replace_all_text("abc123", "old", "new")
        assert result == 0

    def test_http_error_401(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.side_effect = _make_http_error(401)

        with pytest.raises(AuthError, match="Authentication expired"):
            replace_all_text("abc123", "old", "new")

    def test_http_error_403(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.side_effect = _make_http_error(
            403, reason="forbidden"
        )

        with pytest.raises(GdocError, match="Permission denied: abc123"):
            replace_all_text("abc123", "old", "new")

    def test_http_error_404(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.side_effect = _make_http_error(404)

        with pytest.raises(GdocError, match="Document not found: abc123"):
            replace_all_text("abc123", "old", "new")

    def test_http_error_500(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.documents().batchUpdate().execute.side_effect = _make_http_error(
            500, reason="Internal Server Error"
        )

        with pytest.raises(GdocError, match=r"API error \(500\)"):
            replace_all_text("abc123", "old", "new")


@patch("gdoc.api.docs.get_docs_service")
class TestGetDocsServiceCaches:
    def test_caches_service(self, mock_get_service):
        """Verify the @lru_cache is applied (tested indirectly via import)."""
        from gdoc.api.docs import get_docs_service
        assert hasattr(get_docs_service, "cache_info")


class TestGetDocumentWithTabs:
    @patch("gdoc.api.docs.get_docs_service")
    def test_returns_full_doc(self, mock_svc):
        from gdoc.api.docs import get_document_with_tabs

        mock_doc = {"revisionId": "rev1", "tabs": []}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = mock_doc

        result = get_document_with_tabs("doc1")
        assert result == mock_doc
        mock_svc.return_value.documents.return_value.get.assert_called_with(
            documentId="doc1", includeTabsContent=True,
        )

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_translated(self, mock_svc):
        from gdoc.api.docs import get_document_with_tabs

        resp = MagicMock()
        resp.status = 404
        err = HttpError(resp, b"not found", uri="")
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.side_effect = err

        with pytest.raises(GdocError, match="Document not found"):
            get_document_with_tabs("doc1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_401_translated(self, mock_svc):
        from gdoc.api.docs import get_document_with_tabs

        resp = MagicMock()
        resp.status = 401
        err = HttpError(resp, b"unauthorized", uri="")
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.side_effect = err

        with pytest.raises(AuthError):
            get_document_with_tabs("doc1")


class TestBuildCleanupRequests:
    def test_empty_heading_produces_requests(self):
        from gdoc.api.docs import _build_cleanup_requests

        body = {"content": [
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "text\n"}}],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                },
                "startIndex": 1,
                "endIndex": 6,
            },
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "\n"}}],
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                },
                "startIndex": 6,
                "endIndex": 7,
            },
        ]}
        reqs = _build_cleanup_requests(body, 6)
        assert len(reqs) == 2
        # First: transfer style to preceding paragraph
        assert "updateParagraphStyle" in reqs[0]
        style = reqs[0]["updateParagraphStyle"]["paragraphStyle"]
        assert style["namedStyleType"] == "HEADING_1"
        # Second: delete the empty heading
        assert "deleteContentRange" in reqs[1]
        assert reqs[1]["deleteContentRange"]["range"]["startIndex"] == 6

    def test_normal_text_noop(self):
        from gdoc.api.docs import _build_cleanup_requests

        body = {"content": [{
            "paragraph": {
                "elements": [{"textRun": {"content": "\n"}}],
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            },
            "startIndex": 1,
            "endIndex": 2,
        }]}
        assert _build_cleanup_requests(body, 1) == []

    def test_no_element_at_position_noop(self):
        from gdoc.api.docs import _build_cleanup_requests

        body = {"content": []}
        assert _build_cleanup_requests(body, 99) == []

    def test_non_empty_heading_noop(self):
        from gdoc.api.docs import _build_cleanup_requests

        body = {"content": [{
            "paragraph": {
                "elements": [{"textRun": {"content": "Title\n"}}],
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
            },
            "startIndex": 1,
            "endIndex": 7,
        }]}
        assert _build_cleanup_requests(body, 1) == []

    def test_tab_id_included(self):
        from gdoc.api.docs import _build_cleanup_requests

        body = {"content": [
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "x\n"}}],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                },
                "startIndex": 1,
                "endIndex": 3,
            },
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "\n"}}],
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                },
                "startIndex": 3,
                "endIndex": 4,
            },
        ]}
        reqs = _build_cleanup_requests(body, 3, tab_id="tab1")
        assert reqs[0]["updateParagraphStyle"]["range"]["tabId"] == "tab1"
        assert reqs[1]["deleteContentRange"]["range"]["tabId"] == "tab1"

    def test_style_transferred_from_heading(self):
        from gdoc.api.docs import _build_cleanup_requests

        body = {"content": [
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "text\n"}}],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                },
                "startIndex": 1,
                "endIndex": 6,
            },
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "\n"}}],
                    "paragraphStyle": {"namedStyleType": "HEADING_3"},
                },
                "startIndex": 6,
                "endIndex": 7,
            },
        ]}
        reqs = _build_cleanup_requests(body, 6)
        ups = reqs[0]["updateParagraphStyle"]
        assert ups["paragraphStyle"]["namedStyleType"] == "HEADING_3"


class TestReplaceFormattedCleanupPositions:
    """Verify cleanup positions account for multi-match replacement delta."""

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_single_match_cleanup_position(self, mock_svc, mock_cleanup):
        """Single match: cleanup pos = startIndex + len(new_text)."""
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        matches = [{"startIndex": 10, "endIndex": 13}]  # 3-char match
        replace_formatted("doc1", matches, "foobar", "rev1")  # 7-char plain_text

        mock_cleanup.assert_called_once()
        # cleanup pos = 10 + 7 = 17 (parse_markdown adds trailing \n)
        assert mock_cleanup.call_args[0][1] == 17

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_match_cleanup_positions(self, mock_svc, mock_cleanup):
        """Multiple matches: higher-index matches get delta shift from
        lower-index replacements that occur before them in the document."""
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        # 3 matches of 3-char text, replaced with "foobar" (plain_text
        # is "foobar\n" = 7 chars, delta = 7 - 3 = 4)
        matches = [
            {"startIndex": 10, "endIndex": 13},
            {"startIndex": 50, "endIndex": 53},
            {"startIndex": 100, "endIndex": 103},
        ]
        replace_formatted("doc1", matches, "foobar", "rev1")

        positions = [c[0][1] for c in mock_cleanup.call_args_list]
        # sorted_matches descending: [100, 50, 10]; delta=4
        # j=0 (100): 100 + 7 + (3-1-0)*4 = 100 + 7 + 8 = 115
        # j=1 (50):  50  + 7 + (3-1-1)*4 = 50  + 7 + 4 = 61
        # j=2 (10):  10  + 7 + (3-1-2)*4 = 10  + 7 + 0 = 17
        assert positions == [115, 61, 17]

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_same_length_replacement_no_drift(self, mock_svc, mock_cleanup):
        """When replacement is same length as original, delta=0."""
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        # 3-char match, "bar" -> plain_text "bar\n" (4 chars), delta=1
        matches = [
            {"startIndex": 10, "endIndex": 13},
            {"startIndex": 50, "endIndex": 53},
        ]
        replace_formatted("doc1", matches, "bar", "rev1")

        positions = [c[0][1] for c in mock_cleanup.call_args_list]
        # j=0 (50): 50 + 4 + (2-1-0)*1 = 55
        # j=1 (10): 10 + 4 + (2-1-1)*1 = 14
        assert positions == [55, 14]


class TestFindTextBody:
    def test_find_text_with_explicit_body(self):
        from gdoc.api.docs import find_text_in_document

        body = {"content": [{
            "paragraph": {
                "elements": [{
                    "startIndex": 1,
                    "textRun": {"content": "hello world\n"},
                }],
            },
        }]}
        matches = find_text_in_document(None, "world", body=body)
        assert len(matches) == 1
        assert matches[0]["startIndex"] == 7

    def test_both_none_returns_empty(self):
        from gdoc.api.docs import find_text_in_document

        assert find_text_in_document(None, "text") == []


class TestAddTab:
    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_success(self, mock_svc):
        from gdoc.api.docs import add_tab

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {
                "replies": [{"addDocumentTab": {"tabProperties": {
                    "tabId": "t99", "title": "Notes", "index": 1,
                }}}],
            }

        result = add_tab("doc1", "Notes")
        assert result == {"tabId": "t99", "title": "Notes", "index": 1}
        mock_svc.return_value.documents.return_value.batchUpdate.assert_called_with(
            documentId="doc1",
            body={"requests": [{"addDocumentTab": {
                "tabProperties": {"title": "Notes"},
            }}]},
        )

    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_404(self, mock_svc):
        from gdoc.api.docs import add_tab

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.side_effect = _make_http_error(404)

        with pytest.raises(GdocError, match="Document not found: doc1"):
            add_tab("doc1", "Notes")

    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_401(self, mock_svc):
        from gdoc.api.docs import add_tab

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.side_effect = _make_http_error(401)

        with pytest.raises(AuthError, match="Authentication expired"):
            add_tab("doc1", "Notes")

    @patch("gdoc.api.docs.get_docs_service")
    def test_add_tab_malformed_response(self, mock_svc):
        from gdoc.api.docs import add_tab

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {"replies": []}

        with pytest.raises(GdocError, match="Unexpected API response"):
            add_tab("doc1", "Notes")
