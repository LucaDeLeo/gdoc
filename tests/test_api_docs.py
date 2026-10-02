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


class TestGetDocsServiceCaches:
    def test_caches_service(self):
        """The per-account @lru_cache lives on _docs_service; the public
        get_docs_service is a thin wrapper that resolves the account."""
        from gdoc.api.docs import _docs_service
        assert hasattr(_docs_service, "cache_info")


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


class TestReplaceFormattedRequests:
    """Wording edits issue one batch with the delete and insert requests."""

    @staticmethod
    def _run(mock_svc, matches, markdown):
        from gdoc.api.docs import replace_formatted

        captured = _capture_batch_updates(mock_svc)
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}
        count = replace_formatted("doc1", matches, markdown, "rev1")
        assert len(captured) == 1
        return count, captured[0]["requests"]

    @patch("gdoc.api.docs.get_docs_service")
    def test_single_match_requests(self, mock_svc):
        count, reqs = self._run(mock_svc, [{"startIndex": 10, "endIndex": 13}],
                                "foobar")
        assert count == 1
        # Without a body the block path styles the inserted paragraph.
        assert reqs == [
            {"deleteContentRange": {"range": {"startIndex": 10, "endIndex": 13}}},
            {"insertText": {"location": {"index": 10}, "text": "foobar"}},
            {"updateParagraphStyle": {
                "range": {"startIndex": 10, "endIndex": 16},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }},
        ]

    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_match_requests_run_last_to_first(self, mock_svc):
        matches = [
            {"startIndex": 10, "endIndex": 13},
            {"startIndex": 50, "endIndex": 53},
            {"startIndex": 100, "endIndex": 103},
        ]
        count, reqs = self._run(mock_svc, matches, "foobar")
        assert count == 3
        deletes = [r["deleteContentRange"]["range"]["startIndex"]
                   for r in reqs if "deleteContentRange" in r]
        assert deletes == [100, 50, 10]
        assert [r["insertText"]["text"] for r in reqs if "insertText" in r] \
            == ["foobar"] * 3

    @patch("gdoc.api.docs.get_docs_service")
    def test_emoji_replacement_is_inserted_verbatim(self, mock_svc):
        count, reqs = self._run(mock_svc, [{"startIndex": 10, "endIndex": 13}],
                                "\U0001F600ab")
        assert count == 1
        assert [r["insertText"]["text"] for r in reqs if "insertText" in r] \
            == ["\U0001F600ab"]

    @patch("gdoc.api.docs._insert_table")
    @patch("gdoc.api.docs.get_docs_service")
    def test_table_index_after_emoji_is_utf16(self, mock_svc, mock_table):
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        md = "\U0001F600 x\n| a | b |\n|---|---|\n| 1 | 2 |"
        replace_formatted("doc1", [{"startIndex": 5, "endIndex": 6}], md, "rev1")

        # plain text before the table placeholder is "😀 x\n" = 4 code
        # points but 5 UTF-16 units.
        assert mock_table.call_args[0][1] == 5 + 5

    @patch("gdoc.api.docs.get_docs_service")
    def test_same_length_replacement(self, mock_svc):
        matches = [
            {"startIndex": 10, "endIndex": 13},
            {"startIndex": 50, "endIndex": 53},
        ]
        count, reqs = self._run(mock_svc, matches, "bar")
        assert count == 2
        assert [r["insertText"]["text"] for r in reqs if "insertText" in r] \
            == ["bar", "bar"]



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

    @staticmethod
    def _cell(text, start):
        return {"content": [{
            "paragraph": {
                "elements": [{"startIndex": start, "textRun": {"content": text}}],
            },
        }]}

    def test_find_text_in_table_cell(self):
        from gdoc.api.docs import find_text_in_document

        body = {"content": [{
            "table": {"tableRows": [{"tableCells": [
                self._cell("Label\n", 5),
                self._cell("Answer here\n", 20),
            ]}]},
        }]}
        matches = find_text_in_document(None, "Answer", body=body)
        assert len(matches) == 1
        assert matches[0]["startIndex"] == 20
        assert matches[0]["endIndex"] == 26

    def test_find_text_in_nested_table(self):
        from gdoc.api.docs import find_text_in_document

        inner = {"table": {"tableRows": [{"tableCells": [
            self._cell("deep value\n", 50),
        ]}]}}
        body = {"content": [{
            "table": {"tableRows": [{"tableCells": [
                {"content": [inner]},
            ]}]},
        }]}
        matches = find_text_in_document(None, "deep", body=body)
        assert len(matches) == 1
        assert matches[0]["startIndex"] == 50

    def test_match_does_not_span_cells(self):
        from gdoc.api.docs import find_text_in_document

        body = {"content": [{
            "table": {"tableRows": [{"tableCells": [
                self._cell("foo\n", 10),
                self._cell("bar\n", 30),
            ]}]},
        }]}
        # Neither a plain concatenation ("foobar") nor a newline-spanning
        # anchor ("foo\nbar") may match across the cell boundary \u2014 that would
        # yield an invalid cross-cell delete range.
        assert find_text_in_document(None, "foobar", body=body) == []
        assert find_text_in_document(None, "foo\nbar", body=body) == []
        # Each cell is still searchable on its own.
        assert find_text_in_document(None, "foo", body=body)[0]["startIndex"] == 10
        assert find_text_in_document(None, "bar", body=body)[0]["startIndex"] == 30

    def test_paragraph_and_table_coexist(self):
        from gdoc.api.docs import find_text_in_document

        body = {"content": [
            {"paragraph": {"elements": [
                {"startIndex": 1, "textRun": {"content": "hello world\n"}},
            ]}},
            {"table": {"tableRows": [{"tableCells": [
                self._cell("world\n", 20),
            ]}]}},
        ]}
        matches = find_text_in_document(None, "world", body=body)
        assert [m["startIndex"] for m in matches] == [7, 20]

    def test_normalize_matches_smart_quotes(self):
        from gdoc.api.docs import find_text_in_document

        body = {"content": [{
            "paragraph": {"elements": [{
                "startIndex": 1, "textRun": {"content": "JP\u2019s job\n"},
            }]},
        }]}
        assert find_text_in_document(None, "JP's job", body=body) == []
        m = find_text_in_document(None, "JP's job", body=body, normalize=True)
        assert len(m) == 1 and m[0]["startIndex"] == 1


class TestDiagnoseNoMatch:
    @staticmethod
    def _para_body(text):
        return {"content": [{
            "paragraph": {"elements": [{
                "startIndex": 1, "textRun": {"content": text},
            }]},
        }]}

    def test_suggests_normalize_on_quote_mismatch(self):
        from gdoc.api.docs import diagnose_no_match

        reason = diagnose_no_match(None, "JP's job", body=self._para_body("JP\u2019s job\n"))
        assert reason is not None and "--normalize" in reason

    def test_reports_whitespace_difference(self):
        from gdoc.api.docs import diagnose_no_match

        reason = diagnose_no_match(
            None, "a b", body=self._para_body("a\nb\n"),
        )
        assert reason is not None and "whitespace" in reason

    def test_no_near_match_returns_none(self):
        from gdoc.api.docs import diagnose_no_match

        assert diagnose_no_match(None, "zzz", body=self._para_body("abc\n")) is None

    def test_already_normalized_skips_quote_suggestion(self):
        from gdoc.api.docs import diagnose_no_match

        reason = diagnose_no_match(
            None, "JP's job", body=self._para_body("JP\u2019s job\n"),
            already_normalized=True,
        )
        assert reason is None or "--normalize" not in reason


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

        # The tab may exist: the error says so rather than inviting a retry.
        with pytest.raises(GdocError, match="outcome is uncertain") as error:
            add_tab("doc1", "Notes")
        assert error.value.exit_code == 1


def _capture_batch_updates(mock_svc):
    """Wire mock_svc so every documents().batchUpdate(...) is captured.

    Returns a list that accumulates each call's body kwarg.
    """
    captured: list = []

    def _bu(documentId, body):
        captured.append(body)
        inner = MagicMock()
        inner.execute.return_value = {}
        return inner

    mock_svc.return_value.documents.return_value \
        .batchUpdate.side_effect = _bu
    return captured


class TestCountDocumentTabs:
    """count_document_tabs requests tab content and counts nested tabs."""

    @patch("gdoc.api.docs.get_docs_service")
    def test_flat_tab_list(self, mock_svc):
        from gdoc.api.docs import count_document_tabs

        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {
                "tabs": [
                    {"tabProperties": {"tabId": "t1"}},
                    {"tabProperties": {"tabId": "t2"}},
                ],
            }
        assert count_document_tabs("doc1") == 2

    @patch("gdoc.api.docs.get_docs_service")
    def test_nested_child_tabs_counted(self, mock_svc):
        from gdoc.api.docs import count_document_tabs

        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {
                "tabs": [
                    {
                        "tabProperties": {"tabId": "t1"},
                        "childTabs": [
                            {"tabProperties": {"tabId": "t1a"}},
                            {"tabProperties": {"tabId": "t1b"}},
                        ],
                    },
                    {"tabProperties": {"tabId": "t2"}},
                ],
            }
        assert count_document_tabs("doc1") == 4

    @patch("gdoc.api.docs.get_docs_service")
    def test_requests_tabs_content_without_fields_mask(self, mock_svc):
        from gdoc.api.docs import count_document_tabs

        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"tabs": []}
        count_document_tabs("doc1")
        call_kwargs = mock_svc.return_value.documents.return_value \
            .get.call_args.kwargs
        assert call_kwargs.get("includeTabsContent") is True
        assert "fields" not in call_kwargs


class TestZeroWidthReplace:
    """Zero-width matches in replace_formatted act as pure inserts \u2014 no
    deleteContentRange is emitted (Docs API rejects empty ranges)."""

    @patch("gdoc.api.docs.get_docs_service")
    def test_zero_width_match_skips_delete(self, mock_svc):
        from gdoc.api.docs import replace_formatted

        captured = _capture_batch_updates(mock_svc)
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        matches = [{"startIndex": 1, "endIndex": 1}]
        replace_formatted("doc1", matches, "hello", "rev1")

        assert captured, "batchUpdate not called"
        reqs = captured[0]["requests"]
        delete_reqs = [r for r in reqs if "deleteContentRange" in r]
        insert_reqs = [r for r in reqs if "insertText" in r]
        assert delete_reqs == []
        assert len(insert_reqs) == 1
        assert insert_reqs[0]["insertText"]["text"] == "hello"


class TestInsertMarkdownIntoTab:
    def _tabs_doc(self, body_content=None):
        return {
            "revisionId": "rev-xyz",
            "tabs": [{
                "tabProperties": {
                    "tabId": "t.todo", "title": "TODO", "index": 0,
                },
                "documentTab": {
                    "body": {"content": body_content or []},
                },
            }],
        }

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_insert_empty_tab_start(self, mock_get, mock_svc):
        from gdoc.api.docs import insert_markdown_into_tab

        mock_get.return_value = self._tabs_doc()
        captured = _capture_batch_updates(mock_svc)

        result = insert_markdown_into_tab(
            "doc1", "TODO", "hello\n", position="start", replace=False,
        )

        assert result["tab_id"] == "t.todo"
        assert result["insert_index"] == 1
        assert len(captured) == 1
        reqs = captured[0]["requests"]
        delete_reqs = [r for r in reqs if "deleteContentRange" in r]
        insert_reqs = [r for r in reqs if "insertText" in r]
        assert delete_reqs == []
        assert len(insert_reqs) == 1
        assert insert_reqs[0]["insertText"]["text"] == "hello"
        assert captured[0]["writeControl"] == {
            "requiredRevisionId": "rev-xyz",
        }
        assert insert_reqs[0]["insertText"]["location"]["tabId"] == "t.todo"

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_insert_nonempty_tab_end(self, mock_get, mock_svc):
        from gdoc.api.docs import insert_markdown_into_tab

        mock_get.return_value = self._tabs_doc(body_content=[
            {"startIndex": 1, "endIndex": 20, "paragraph": {}},
        ])
        captured = _capture_batch_updates(mock_svc)

        result = insert_markdown_into_tab(
            "doc1", "TODO", "tail", position="end", replace=False,
        )

        assert result["insert_index"] == 20
        reqs = captured[0]["requests"]
        insert_reqs = [r for r in reqs if "insertText" in r]
        assert insert_reqs[0]["insertText"]["location"]["index"] == 19
        assert insert_reqs[0]["insertText"]["text"] == "\n"
        assert insert_reqs[1]["insertText"] == {
            "location": {"index": 20, "tabId": "t.todo"}, "text": "tail",
        }

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_replace_tab_body(self, mock_get, mock_svc):
        from gdoc.api.docs import insert_markdown_into_tab

        mock_get.return_value = self._tabs_doc(body_content=[
            {"startIndex": 1, "endIndex": 30, "paragraph": {}},
        ])
        captured = _capture_batch_updates(mock_svc)

        insert_markdown_into_tab(
            "doc1", "TODO", "new content", replace=True,
        )

        reqs = captured[0]["requests"]
        delete_reqs = [r for r in reqs if "deleteContentRange" in r]
        assert len(delete_reqs) == 1
        d_range = delete_reqs[0]["deleteContentRange"]["range"]
        assert d_range["startIndex"] == 1
        assert d_range["endIndex"] == 29
        assert d_range["tabId"] == "t.todo"

    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_replace_empty_tab_no_delete(self, mock_get, mock_svc):
        from gdoc.api.docs import insert_markdown_into_tab

        mock_get.return_value = self._tabs_doc()
        captured = _capture_batch_updates(mock_svc)

        insert_markdown_into_tab(
            "doc1", "TODO", "content", replace=True,
        )

        reqs = captured[0]["requests"]
        delete_reqs = [r for r in reqs if "deleteContentRange" in r]
        assert delete_reqs == []

    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_missing_tab_errors(self, mock_get):
        from gdoc.api.docs import insert_markdown_into_tab

        mock_get.return_value = self._tabs_doc()

        with pytest.raises(GdocError, match="tab not found"):
            insert_markdown_into_tab("doc1", "Not A Real Tab", "hi")


@pytest.mark.parametrize("collection", ["headers", "footers", "footnotes"])
def test_segment_search_preserves_matching_options_and_cell_boundaries(collection):
    from gdoc.api.docs import find_text_in_document

    def paragraph(start, text):
        return {"paragraph": {"elements": [
            {"startIndex": start, "textRun": {"content": text}},
        ]}}

    scope = {"id": "tab-one", "body": {}, collection: {"segment-one": {
        "content": [paragraph(0, "A\u2019s TOKEN\n"), {"table": {"tableRows": [{
            "tableCells": [
                {"content": [paragraph(20, "Left")]},
                {"content": [paragraph(30, "Right")]},
            ],
        }]}}],
    }}}
    assert find_text_in_document(scope, "A's") == []
    assert find_text_in_document(scope, "token", match_case=True) == []
    assert find_text_in_document(scope, "LeftRight") == []
    assert find_text_in_document(scope, "Left\nRight") == []
    assert find_text_in_document(scope, "a's token", normalize=True) == [{
        "startIndex": 0, "endIndex": 9, "tabId": "tab-one",
        "segmentId": "segment-one", "container": collection[:-1],
    }]


def test_segments_are_ordered_by_id_not_map_insertion_order():
    from gdoc.api.docs import find_text_in_document

    content = {"content": [{"paragraph": {"elements": [
        {"startIndex": 0, "textRun": {"content": "TOKEN"}},
    ]}}]}
    matches = find_text_in_document({"headers": {
        "header-z": content, "header-a": content,
    }}, "TOKEN")
    assert [m["segmentId"] for m in matches] == ["header-a", "header-z"]


class TestBuildCleanupRequests:
    def test_final_empty_heading_is_retained(self):
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
                    "elements": [{"startIndex": 6, "endIndex": 7,
                                  "textRun": {"content": "\n"}}],
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                },
                "startIndex": 6,
                "endIndex": 7,
            },
        ]}
        reqs = _build_cleanup_requests(body, 6)
        assert reqs == []  # Final newline is mandatory.

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
                    "elements": [{"startIndex": 3, "endIndex": 4,
                                  "textRun": {"content": "\n"}}],
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                },
                "startIndex": 3,
                "endIndex": 4,
            },
        ]}
        body["content"].append({"startIndex": 4, "endIndex": 9,
                                "paragraph": {"elements": [
                                    {"textRun": {"content": "next\n"}},
                                ]}})
        reqs = _build_cleanup_requests(body, 3, tab_id="tab1")
        assert reqs == [{"deleteContentRange": {"range": {
            "startIndex": 3, "endIndex": 4, "tabId": "tab1",
        }}}]

    def test_heading_never_promotes_previous_paragraph(self):
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
                    "elements": [{"startIndex": 6, "endIndex": 7,
                                  "textRun": {"content": "\n"}}],
                    "paragraphStyle": {"namedStyleType": "HEADING_3"},
                },
                "startIndex": 6,
                "endIndex": 7,
            },
            {
                "paragraph": {"elements": [{"textRun": {"content": "next\n"}}]},
                "startIndex": 7,
                "endIndex": 12,
            },
        ]}
        reqs = _build_cleanup_requests(body, 6)
        assert reqs == [{"deleteContentRange": {"range": {
            "startIndex": 6, "endIndex": 7,
        }}}]
        assert not any("updateParagraphStyle" in req for req in reqs)


class TestReplaceFormattedNoSpeculativeCleanup:
    """Wording edits never trigger a second mutation of guessed scaffolding."""

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_single_match_wording_edit_skips_cleanup(self, mock_svc, mock_cleanup):
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        matches = [{"startIndex": 10, "endIndex": 13}]  # 3-char match
        replace_formatted("doc1", matches, "foobar", "rev1")  # 6-char plain_text

        mock_cleanup.assert_not_called()

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_match_wording_edit_skips_cleanup(self, mock_svc, mock_cleanup):
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        # 3 matches of 3-char text, replaced with "foobar" (plain_text
        # is "foobar" = 6 chars after trailing \n strip, delta = 6 - 3 = 3)
        matches = [
            {"startIndex": 10, "endIndex": 13},
            {"startIndex": 50, "endIndex": 53},
            {"startIndex": 100, "endIndex": 103},
        ]
        replace_formatted("doc1", matches, "foobar", "rev1")

        mock_cleanup.assert_not_called()

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_emoji_replacement_skips_cleanup(self, mock_svc, mock_cleanup):
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        matches = [{"startIndex": 10, "endIndex": 13}]
        replace_formatted("doc1", matches, "\U0001F600ab", "rev1")  # 3 chars, 4 units

        mock_cleanup.assert_not_called()

    @patch("gdoc.api.docs._insert_table")
    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_table_replacement_skips_cleanup(
        self, mock_svc, mock_cleanup, mock_table,
    ):
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        md = "\U0001F600 x\n| a | b |\n|---|---|\n| 1 | 2 |"
        replace_formatted("doc1", [{"startIndex": 5, "endIndex": 6}], md, "rev1")

        # The table index itself is covered by TestReplaceFormattedRequests;
        # here only the absence of speculative cleanup matters.
        mock_table.assert_called_once()
        mock_cleanup.assert_not_called()

    @patch("gdoc.api.docs._build_cleanup_requests", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_same_length_replacement_no_drift(self, mock_svc, mock_cleanup):
        """When replacement is same length as original, delta=0."""
        from gdoc.api.docs import replace_formatted

        mock_svc.return_value.documents.return_value \
            .batchUpdate.return_value.execute.return_value = {}
        mock_svc.return_value.documents.return_value \
            .get.return_value.execute.return_value = {"body": {"content": []}}

        # 3-char match, "bar" -> plain_text "bar" (3 chars), delta=0
        matches = [
            {"startIndex": 10, "endIndex": 13},
            {"startIndex": 50, "endIndex": 53},
        ]
        replace_formatted("doc1", matches, "bar", "rev1")

        mock_cleanup.assert_not_called()


@pytest.mark.parametrize("name, options, expected_kwargs", [
    ("get_document", {}, {}),
    ("get_document_tabs", {}, {"includeTabsContent": True}),
    ("get_document_with_tabs", {}, {"includeTabsContent": True}),
    ("get_document_structure", {}, {"includeTabsContent": True}),
    ("get_document_structure", {
        "fields": "documentId,revisionId",
        "suggestions_view_mode": "SUGGESTIONS_INLINE",
    }, {
        "includeTabsContent": True, "fields": "documentId,revisionId",
        "suggestionsViewMode": "SUGGESTIONS_INLINE",
    }),
])
def test_document_reads_request_two_additional_google_client_retries(
    mocker, name, options, expected_kwargs,
):
    """Read wrappers preserve options and request two extra Google-client retries."""
    from gdoc.api import docs

    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    resource = service.documents.return_value
    document = {"documentId": "sample-doc", "tabs": []}
    resource.get.return_value.execute.return_value = document

    result = getattr(docs, name)("sample-doc", **options)

    assert result == ([] if name == "get_document_tabs" else document)
    resource.get.assert_called_once_with(documentId="sample-doc", **expected_kwargs)
    resource.get.return_value.execute.assert_called_once_with(num_retries=2)
    resource.batchUpdate.assert_not_called()


@pytest.mark.parametrize(
    "disconnects, through_cli", [(1, False), (3, False), (3, True)],
)
def test_document_read_google_client_disconnect_retries(
    mocker, capsys, disconnects, through_cli,
):
    """Allow two extra Google-client retries after read disconnects.

    The mocked transport isolates the client loop; counts are not wire sends.
    """
    import json
    from http.client import RemoteDisconnected

    from googleapiclient.http import HttpRequest

    from gdoc.api.docs import get_document

    document = {"documentId": "sample-doc", "body": {"content": []}}
    transport = mocker.Mock()
    transport.request.side_effect = [
        RemoteDisconnected("connection closed") for _ in range(disconnects)
    ] + [(httplib2.Response({"status": "200"}), json.dumps(document).encode())]
    request = HttpRequest(
        transport, lambda response, content: json.loads(content),
        "https://docs.googleapis.com/v1/documents/sample-doc", method="GET",
    )
    mocker.patch.object(request, "_sleep")
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    resource = service.documents.return_value
    resource.get.return_value = request

    if through_cli:
        from gdoc.cli import run_argv
        from gdoc.util import account_context

        mocker.patch("gdoc.notify.pre_flight", return_value=None)
        with account_context("sample-account"):
            assert run_argv([
                "edit", "sample-doc", "apple", "pear", "--quiet",
                "--account", "sample-account",
            ], check_updates=False) == 1
        assert capsys.readouterr().err == (
            "ERR: unexpected error: connection closed\n"
        )
        assert transport.request.call_count == 3
    elif disconnects == 3:
        # The API preserves transport errors for the CLI's unexpected-error handler.
        with pytest.raises(RemoteDisconnected, match="connection closed"):
            get_document("sample-doc")
        assert transport.request.call_count == 3
    else:
        assert get_document("sample-doc") == document
        assert transport.request.call_count == 2
    resource.batchUpdate.assert_not_called()


SINGLE_SEND = {"update_doc_content", "create_comment", "replace_all_text"}


@pytest.mark.parametrize("module, name, resource_name, method, args", [
    ("docs", "replace_all_text", "documents", "batchUpdate",
     ("sample-doc", "apple", "pear")),
    ("drive", "update_doc_content", "documents", "batchUpdate",
     ("sample-doc", "Sample text.")),
    ("drive", "create_doc_from_markdown", "files", "create",
     ("Sample title", "Sample text.")),
    ("comments", "create_comment", "comments", "create",
     ("sample-doc", "Sample comment.")),
])
def test_mutation_disconnect_adds_no_google_client_retries(
    mocker, module, name, resource_name, method, args,
):
    """Mutations add no Google-client retries when the transport raises.

    Synthetic requests isolate execute policy, not generated upload behavior or
    httplib2's internal retries; this does not guarantee a single wire send.
    """
    import json
    from http.client import RemoteDisconnected
    from importlib import import_module

    from googleapiclient.http import HttpRequest

    api = import_module(f"gdoc.api.{module}")
    transport = mocker.Mock()
    transport.request.side_effect = [
        RemoteDisconnected("response lost"),
        (httplib2.Response({"status": "200"}), b'{}'),
    ]
    request = HttpRequest(
        transport, lambda response, content: json.loads(content),
        "https://example.invalid/mutation", method="POST",
    )
    execute = mocker.spy(request, "execute")
    sleep = mocker.patch.object(request, "_sleep")
    factory = "get_docs_service" if module == "docs" else "get_drive_service"
    service = (mocker.patch("gdoc.api.docs.get_docs_service").return_value
               if name == "update_doc_content"
               else mocker.patch.object(api, factory).return_value)
    operation = getattr(getattr(service, resource_name).return_value, method)
    operation.return_value = request

    options = {}
    expected_error = RemoteDisconnected
    expected_message = "response lost"
    if name == "update_doc_content":
        # PR #70 routes single-tab writes through Docs and guards imports.
        options = {"expected_version": 1, "document": {"revisionId": "r1", "tabs": [
            {"tabProperties": {"tabId": "tab-one"}, "documentTab": {}},
            {"tabProperties": {"tabId": "tab-two"}, "documentTab": {}},
        ]}}
        mocker.patch.object(api, "require_write_version")
    if name in SINGLE_SEND:
        # Every mutation goes through the shared single-send transport.
        mocker.patch(
            "gdoc.api.comment_transport._SingleSendHttp", return_value=transport,
        )
        expected_error = GdocError
        expected_message = "outcome is uncertain"

    with pytest.raises(expected_error, match=expected_message):
        getattr(api, name)(*args, **options)

    operation.assert_called_once()
    if name in SINGLE_SEND:
        execute.assert_called_once_with(http=mocker.ANY, num_retries=0)
    else:
        execute.assert_called_once_with()
    transport.request.assert_called_once()
    sleep.assert_not_called()


def test_generated_docs_client_read_retry_boundary_with_real_httplib2(mocker):
    """Reads add two client retries above httplib2's own resends."""
    from http.client import HTTPSConnection, RemoteDisconnected

    from googleapiclient.discovery import build

    from gdoc.api.docs import get_document

    transport = httplib2.Http()
    connection = mocker.Mock(spec=HTTPSConnection)
    connection.sock = mocker.sentinel.socket
    connection.getresponse.side_effect = RemoteDisconnected("response lost")
    transport.connections["https:docs.googleapis.com"] = connection
    client_attempts = mocker.spy(transport, "request")
    sleep = mocker.patch("googleapiclient.http.time.sleep")
    service = build("docs", "v1", http=transport, static_discovery=True)
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)

    with pytest.raises(RemoteDisconnected, match="response lost"):
        get_document("sample-doc")

    assert client_attempts.call_count == 3
    assert sleep.call_count == 2
    # RemoteDisconnected is a BadStatusLine: httplib2 can retry it internally.
    assert connection.request.call_count > client_attempts.call_count
    assert all(call.args[0] == "GET" for call in connection.request.call_args_list)


@pytest.mark.parametrize("call", [
    lambda docs: docs.replace_all_text("sample-doc", "apple", "pear"),
    lambda docs: docs.add_tab("sample-doc", "Notes"),
    lambda docs: docs.set_page_mode("sample-doc", True),
], ids=["replace_all_text", "add_tab", "set_page_mode"])
def test_docs_mutations_send_once_with_real_httplib2(mocker, call):
    """A lost mutation response is uncertain; httplib2 never resends it."""
    from http.client import HTTPSConnection, RemoteDisconnected

    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    from gdoc.api import comment_transport, docs

    connection = mocker.Mock(spec=HTTPSConnection)
    connection.sock = mocker.sentinel.socket
    connection.getresponse.side_effect = RemoteDisconnected("response lost")
    single = comment_transport._SingleSendHttp()
    single.connections["https:docs.googleapis.com"] = connection
    mocker.patch.object(comment_transport, "_SingleSendHttp", return_value=single)
    credentials = Credentials(token="synthetic-token")
    service = build("docs", "v1", static_discovery=True,
                    http=AuthorizedHttp(credentials, http=httplib2.Http()))
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)

    with pytest.raises(GdocError, match="uncertain"):
        call(docs)
    assert connection.request.call_count == 1


@pytest.mark.parametrize("name", [
    "get_document", "get_document_tabs", "get_document_with_tabs",
    "get_document_structure",
])
@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("failures", [2, 3], ids=["recovery", "exhaustion"])
def test_document_reads_google_client_status_retries(mocker, name, status, failures):
    """All read wrappers allow two extra client retries for 429 and 5xx errors."""
    from googleapiclient.discovery import build

    from gdoc.api import docs

    transport = mocker.Mock(spec=httplib2.Http)
    transport.request.side_effect = [
        (httplib2.Response({"status": str(status)}), b'{}')
        for _ in range(failures)
    ] + [(httplib2.Response({"status": "200"}), b'{"tabs": []}')]
    sleep = mocker.patch("googleapiclient.http.time.sleep")
    service = build("docs", "v1", http=transport, static_discovery=True)
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)

    if failures == 3:
        with pytest.raises(GdocError, match=rf"API error \({status}\)"):
            getattr(docs, name)("sample-doc")
    else:
        assert getattr(docs, name)("sample-doc") == (
            [] if name == "get_document_tabs" else {"tabs": []}
        )

    assert transport.request.call_count == 3
    assert sleep.call_count == 2
