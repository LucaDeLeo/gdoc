"""Tests for `gdoc suggest` — find/replace as a suggested edit (SUGGEST mode).

Covers the API wrapper (`suggest_replacement` and its helpers) with mocked
Docs services asserting the exact JSON sent to Google, and the CLI handler
(`cmd_suggest`) with the API layer mocked. No fallback path exists: every
failure must surface as an error, never as a direct edit.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.docs import (
    SUGGESTIONS_INLINE,
    SuggestionResult,
    check_inline_only_markdown,
    check_suggest_preview_access,
    collect_suggestion_ids,
    find_suggestions_in_range,
    find_text_in_document,
    suggest_replacement,
)
from gdoc.cli import build_parser, cmd_suggest
from gdoc.mdparse import parse_markdown
from gdoc.notify import ChangeInfo
from gdoc.util import AuthError, GdocError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _http_error(status, content=b"", reason="Error"):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = reason
    return HttpError(resp, content, uri="")


def _service(batch_response=None, batch_error=None):
    """Docs service mock whose batchUpdate().execute() returns or raises."""
    service = MagicMock()
    execute = service.documents.return_value.batchUpdate.return_value.execute
    if batch_error is not None:
        execute.side_effect = batch_error
    else:
        execute.return_value = batch_response if batch_response is not None else {}
    return service


def _batch_call(service):
    return service.documents.return_value.batchUpdate.call_args


def _ok_response(created=("suggest.abc",), updated=()):
    return {
        "commentUpdateState": "ALL_SAVED",
        "replies": [{}, {}],
        "suggestionResponses": [
            {
                "createdSuggestionIds": list(created),
                "updatedSummarySuggestionIds": list(updated),
            }
        ],
    }


def _readback(*ids, tab_id="t.0"):
    """A SUGGESTIONS_INLINE documents.get carrying *ids* as insertions."""
    return {
        "revisionId": "rev-after",
        "tabs": [{
            "tabProperties": {"tabId": tab_id, "title": "Tab 1", "index": 0},
            "documentTab": {"body": {"content": [{
                "startIndex": 1, "endIndex": 20,
                "paragraph": {"elements": [{
                    "startIndex": 1, "endIndex": 20,
                    "textRun": {
                        "content": "hello brave new world\n",
                        "suggestedInsertionIds": list(ids),
                    },
                }]},
            }]}},
        }],
    }


def _run(text="hello", start=1, end=6):
    return {
        "startIndex": start, "endIndex": end,
        "textRun": {"content": text, "textStyle": {}},
    }


def _para(*elements, **extra):
    start = elements[0]["startIndex"]
    end = elements[-1]["endIndex"]
    para = {"elements": list(elements)}
    para.update(extra)
    return {"startIndex": start, "endIndex": end, "paragraph": para}


def _body(*structural):
    return {"content": list(structural)}


MATCH = [{"startIndex": 1, "endIndex": 6}]


@pytest.fixture(autouse=True)
def _preview_gate_passes():
    """The non-mutating enrollment gate is exercised by TestPreviewGate;
    everywhere else it is assumed to pass so the write path is what's
    under test."""
    with patch("gdoc.api.docs.check_suggest_preview_access") as gate:
        yield gate


def _gate_response(status, body=None, text="", reason="Error"):
    resp = MagicMock()
    resp.status_code = status
    resp.reason = reason
    resp.text = text if text else json.dumps(body or {})
    if body is not None:
        resp.json.return_value = body
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


# ---------------------------------------------------------------------------
# suggest_replacement: request shape
# ---------------------------------------------------------------------------


class TestSuggestRequestShape:
    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_write_control_has_revision_and_suggest_mode(self, mock_svc, _rb):
        service = _service(_ok_response())
        mock_svc.return_value = service

        result = suggest_replacement("doc1", MATCH, "world", "rev123", tab_id="t.0")

        call = _batch_call(service)
        assert call.kwargs["documentId"] == "doc1"
        body = call.kwargs["body"]
        assert body["writeControl"] == {
            "requiredRevisionId": "rev123",
            "writeMode": "SUGGEST",
        }
        assert body["requests"] == [
            {"deleteContentRange": {"range": {
                "startIndex": 1, "endIndex": 6, "tabId": "t.0",
            }}},
            {"insertText": {
                "location": {"index": 1, "tabId": "t.0"}, "text": "world",
            }},
        ]
        assert result.occurrences == 1
        assert result.suggestion_ids == ["suggest.abc"]

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_no_paragraph_style_requests_in_suggest_batch(self, mock_svc, _rb):
        """Plain paragraphs must not become suggested NORMAL_TEXT style changes."""
        mock_svc.return_value = _service(_ok_response())
        suggest_replacement("doc1", MATCH, "plain text", "rev", tab_id="t.0")
        reqs = _batch_call(mock_svc.return_value).kwargs["body"]["requests"]
        kinds = [next(iter(r)) for r in reqs]
        assert "updateParagraphStyle" not in kinds
        assert kinds == ["deleteContentRange", "insertText"]

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_inline_styles_stay_in_the_same_batch(self, mock_svc, _rb):
        service = _service(_ok_response())
        mock_svc.return_value = service

        suggest_replacement(
            "doc1", MATCH, "**bold** ~~gone~~ [link](https://x.test)", "rev",
            tab_id="t.0",
        )

        assert service.documents.return_value.batchUpdate.call_count == 1
        reqs = _batch_call(service).kwargs["body"]["requests"]
        styles = [r["updateTextStyle"] for r in reqs if "updateTextStyle" in r]
        assert len(styles) == 3
        assert all(s["range"]["tabId"] == "t.0" for s in styles)
        assert styles[0]["textStyle"] == {"bold": True}
        assert styles[1]["textStyle"] == {"strikethrough": True}
        assert styles[2]["textStyle"] == {"link": {"url": "https://x.test"}}
        assert "updateParagraphStyle" not in {next(iter(r)) for r in reqs}

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_multiple_matches_last_to_first_with_tab_id(self, mock_svc, _rb):
        service = _service(_ok_response())
        mock_svc.return_value = service
        matches = [
            {"startIndex": 1, "endIndex": 6}, {"startIndex": 40, "endIndex": 45},
        ]

        result = suggest_replacement("doc1", matches, "x", "rev", tab_id="t.9")

        reqs = _batch_call(service).kwargs["body"]["requests"]
        deletes = [
            r["deleteContentRange"]["range"] for r in reqs if "deleteContentRange" in r
        ]
        assert [d["startIndex"] for d in deletes] == [40, 1]
        assert all(d["tabId"] == "t.9" for d in deletes)
        inserts = [r["insertText"]["location"] for r in reqs if "insertText" in r]
        assert inserts == [
            {"index": 40, "tabId": "t.9"}, {"index": 1, "tabId": "t.9"},
        ]
        assert result.occurrences == 2

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_utf16_range_from_find_text(self, mock_svc, _rb):
        """An emoji before the anchor shifts Docs indexes by 2, not 1."""
        mock_svc.return_value = _service(_ok_response())
        body = _body(_para(_run("\U0001F600 hello\n", 1, 10)))
        matches = find_text_in_document(None, "hello", body=body)
        assert matches == [{"startIndex": 4, "endIndex": 9}]

        suggest_replacement("doc1", matches, "x", "rev", tab_id="t.0")

        reqs = _batch_call(mock_svc.return_value).kwargs["body"]["requests"]
        assert reqs[0]["deleteContentRange"]["range"]["startIndex"] == 4
        assert reqs[0]["deleteContentRange"]["range"]["endIndex"] == 9

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_zero_width_match_is_pure_insert(self, mock_svc, _rb):
        mock_svc.return_value = _service(_ok_response())
        suggest_replacement(
            "doc1", [{"startIndex": 5, "endIndex": 5}], "new", "rev", tab_id="t.0",
        )
        reqs = _batch_call(mock_svc.return_value).kwargs["body"]["requests"]
        assert [next(iter(r)) for r in reqs] == ["insertText"]

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_created_id_echoed_as_updated_is_reported_once(self, mock_svc, _rb):
        """Live shape: Google lists a new ID under both created and
        updatedSummarySuggestionIds."""
        mock_svc.return_value = _service(
            _ok_response(created=("suggest.abc",), updated=("suggest.abc",)),
        )
        result = suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert result.created_suggestion_ids == ["suggest.abc"]
        assert result.updated_suggestion_ids == []
        assert result.suggestion_ids == ["suggest.abc"]

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_style_ranges_in_replacement_are_utf16(self, mock_svc, _rb):
        """An emoji inside the replacement shifts later style ranges by 2."""
        mock_svc.return_value = _service(_ok_response())
        suggest_replacement(
            "doc1", [{"startIndex": 10, "endIndex": 15}], "\U0001F600 **bold**",
            "rev", tab_id="t.0",
        )
        reqs = _batch_call(mock_svc.return_value).kwargs["body"]["requests"]
        style = next(r["updateTextStyle"] for r in reqs if "updateTextStyle" in r)
        # plain text is "😀 bold": emoji = 2 units, space = 1 → bold at +3..+7
        assert style["range"] == {"startIndex": 13, "endIndex": 17, "tabId": "t.0"}

    @patch("gdoc.api.docs.get_docs_service")
    def test_overlapping_matches_are_rejected(self, mock_svc):
        """`aa` in `aaa` matches [1,3) and [2,4); replacing both is undefined."""
        service = _service(_ok_response())
        mock_svc.return_value = service
        body = _body(_para(_run("aaa\n", 1, 5)))
        matches = find_text_in_document(None, "aa", body=body)
        assert matches == [
            {"startIndex": 1, "endIndex": 3}, {"startIndex": 2, "endIndex": 4},
        ]
        with pytest.raises(GdocError, match="overlap each other") as exc:
            suggest_replacement("doc1", matches, "b", "rev", tab_id="t.0")
        assert exc.value.exit_code == 3
        service.documents.return_value.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_preview_gate_runs_before_the_write(self, mock_svc, _preview_gate_passes):
        service = _service(_ok_response())
        mock_svc.return_value = service
        _preview_gate_passes.side_effect = GdocError(
            "suggest mode not available: not enrolled",
        )
        with pytest.raises(GdocError, match="not enrolled"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        _preview_gate_passes.assert_called_once_with("doc1")
        service.documents.return_value.batchUpdate.assert_not_called()

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_preview_gate_not_needed_when_nothing_to_send(
        self, mock_svc, _rb, _preview_gate_passes,
    ):
        suggest_replacement("doc1", [{"startIndex": 3, "endIndex": 3}], "", "rev")
        _preview_gate_passes.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_revision_never_sent(self, mock_svc):
        """A missing revisionId must not become `requiredRevisionId: ""`."""
        service = _service(_ok_response())
        mock_svc.return_value = service
        with pytest.raises(GdocError, match="commenter permission"):
            suggest_replacement("doc1", MATCH, "x", "", tab_id="t.0")
        service.documents.return_value.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_client_swap_between_gate_and_write_aborts(self, mock_svc):
        """A re-auth with a different OAuth client landing between the
        enrollment gate and the write would prove enrollment for one
        project and write through another. The write must abort pre-send."""
        mock_svc.return_value = _service(_ok_response())
        with patch(
            "gdoc.api.docs._token_identity",
            side_effect=[("client-a.apps", "rt1"), ("client-b.apps", "rt1")],
        ):
            with pytest.raises(GdocError, match="No change was made"):
                suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        mock_svc.return_value.documents.return_value.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_user_swap_between_gate_and_write_aborts(self, mock_svc):
        """Re-authenticating the same account name to a different Google
        user through the same client keeps client_id equal but mints a new
        refresh_token; the suggestion must not be authored by the
        replacement user."""
        mock_svc.return_value = _service(_ok_response())
        with patch(
            "gdoc.api.docs._token_identity",
            side_effect=[("client-a.apps", "rt1"), ("client-a.apps", "rt2")],
        ):
            with pytest.raises(GdocError, match="No change was made"):
                suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        mock_svc.return_value.documents.return_value.batchUpdate.assert_not_called()

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_routine_token_refresh_between_gate_and_write_is_allowed(
        self, mock_svc, _rb,
    ):
        """The gate's own get_credentials persists a refreshed access token
        (a long-lived process may have refreshed only in memory), rewriting
        the token file with the same client_id and refresh_token. That must
        not read as a credential swap — a file-identity comparison aborted
        the first suggest after token expiry."""
        mock_svc.return_value = _service(_ok_response())
        with patch(
            "gdoc.api.docs._token_identity",
            side_effect=[("client-a.apps", "rt1"), ("client-a.apps", "rt1")],
        ):
            result = suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert result.suggestion_ids == ["suggest.abc"]

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_direct_call_pins_the_resolved_account(
        self, mock_svc, _rb, _preview_gate_passes, monkeypatch,
    ):
        """Called directly (no CLI/MCP pinning), a default-account flip
        between the gate and the service lookup must not split them across
        two accounts — the gate could prove enrollment for one account
        while the write goes through another, possibly unenrolled, one."""
        from gdoc import util

        util.set_active_account(None)
        default = ["acct-a"]
        monkeypatch.setattr(
            "gdoc.util.get_default_account", lambda: default[0],
        )
        seen = []

        def gate_spy(doc_id):
            seen.append(util.resolve_account())
            default[0] = "acct-b"  # the flip lands right after the gate

        _preview_gate_passes.side_effect = gate_spy

        def service_spy():
            seen.append(util.resolve_account())
            return _service(_ok_response())

        mock_svc.side_effect = service_spy

        result = suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert result.suggestion_ids == ["suggest.abc"]
        assert seen == ["acct-a", "acct-a"]

    @patch("gdoc.api.docs.get_docs_service")
    def test_expected_identity_from_before_the_read_is_the_baseline(
        self, mock_svc,
    ):
        """The CLI captures the token identity before the document read and
        passes it in; a re-auth landing anywhere between that read and the
        write must abort pre-send, not just one between gate and write."""
        mock_svc.return_value = _service(_ok_response())
        with patch(
            "gdoc.api.docs._token_identity",
            return_value=("client-a.apps", "rt2"),  # current, post-re-auth
        ):
            with pytest.raises(GdocError, match="No change was made"):
                suggest_replacement(
                    "doc1", MATCH, "x", "rev", tab_id="t.0",
                    expected_token_identity=("client-a.apps", "rt1"),
                )
        mock_svc.return_value.documents.return_value.batchUpdate.assert_not_called()

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_matching_expected_identity_writes(self, mock_svc, _rb):
        mock_svc.return_value = _service(_ok_response())
        with patch(
            "gdoc.api.docs._token_identity",
            return_value=("client-a.apps", "rt1"),
        ):
            result = suggest_replacement(
                "doc1", MATCH, "x", "rev", tab_id="t.0",
                expected_token_identity=("client-a.apps", "rt1"),
            )
        assert result.suggestion_ids == ["suggest.abc"]

    @patch("gdoc.api.docs.get_docs_service")
    def test_no_requests_returns_zero_without_calling_api(self, mock_svc):
        service = _service(_ok_response())
        mock_svc.return_value = service
        result = suggest_replacement(
            "doc1", [{"startIndex": 3, "endIndex": 3}], "", "rev",
        )
        assert result.occurrences == 0
        assert result.suggestion_ids == []
        service.documents.return_value.batchUpdate.assert_not_called()


# ---------------------------------------------------------------------------
# suggest_replacement: response handling
# ---------------------------------------------------------------------------


class TestSuggestResponseIds:
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_ids_flattened_and_deduped_across_responses(self, mock_svc, mock_rb):
        mock_svc.return_value = _service({
            "commentUpdateState": "ALL_SAVED",
            "suggestionResponses": [
                {"createdSuggestionIds": ["s.1", "s.2"]},
                {
                    "createdSuggestionIds": ["s.2"],
                    "updatedSummarySuggestionIds": ["s.3"],
                },
                {},
                {"updatedSummarySuggestionIds": ["s.3", "s.1"]},
            ],
        })
        mock_rb.return_value = _readback("s.1", "s.2", "s.3")

        result = suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

        assert result.created_suggestion_ids == ["s.1", "s.2"]
        # s.1 was created in this batch, so it is not also "updated".
        assert result.updated_suggestion_ids == ["s.3"]
        assert result.suggestion_ids == ["s.1", "s.2", "s.3"]
        assert result.comment_update_state == "ALL_SAVED"

    @patch("gdoc.api.docs.get_document_structure", return_value=_readback("s.open"))
    @patch("gdoc.api.docs.get_docs_service")
    def test_merge_into_existing_suggestion_is_success(self, mock_svc, _rb):
        """Google can fold the edit into the author's open suggestion: no
        created ID, one updated ID. That is still a durable review object."""
        mock_svc.return_value = _service(
            _ok_response(created=(), updated=("s.open",)),
        )
        result = suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert result.created_suggestion_ids == []
        assert result.updated_suggestion_ids == ["s.open"]
        assert result.suggestion_ids == ["s.open"]

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_200_without_ids_is_an_error(self, mock_svc, mock_rb):
        """HTTP 200 + no suggestion IDs = the server may have edited directly."""
        mock_svc.return_value = _service(
            {"commentUpdateState": "ALL_SAVED", "replies": [{}]},
        )
        with pytest.raises(GdocError, match="edited directly"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        mock_rb.assert_not_called()

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_missing_comment_update_state_is_an_error(self, mock_svc, mock_rb):
        """An ordinary (non-suggest) batch returns no commentUpdateState —
        the shape an unenrolled backend produces when it ignores writeMode."""
        mock_svc.return_value = _service({"replies": [{}, {}]})
        with pytest.raises(GdocError, match="commentUpdateState=none"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        mock_rb.assert_not_called()

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_partial_save_failure_is_an_error(self, mock_svc, mock_rb):
        resp = _ok_response()
        resp["commentUpdateState"] = "ALL_FAILED_UNKNOWN_REASON"
        mock_svc.return_value = _service(resp)
        with pytest.raises(GdocError, match="ALL_FAILED_UNKNOWN_REASON"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        mock_rb.assert_not_called()

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_partial_save_failure_names_the_reported_ids(
        self, mock_svc, mock_rb,
    ):
        """A non-ALL_SAVED response that still carries suggestion IDs must
        name them, not just count them: some review objects may exist, and
        a caller deciding whether to retry has to be able to find them."""
        resp = _ok_response(created=("s.maybe1", "s.maybe2"))
        resp["commentUpdateState"] = "ALL_FAILED_UNKNOWN_REASON"
        mock_svc.return_value = _service(resp)
        with pytest.raises(GdocError) as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert "s.maybe1, s.maybe2" in str(exc.value)
        mock_rb.assert_not_called()

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_readback_uses_suggestions_inline(self, mock_svc, mock_rb):
        mock_svc.return_value = _service(_ok_response())
        mock_rb.return_value = _readback("suggest.abc")
        suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        mock_rb.assert_called_once_with(
            "doc1", suggestions_view_mode=SUGGESTIONS_INLINE,
        )
        assert SUGGESTIONS_INLINE == "SUGGESTIONS_INLINE"

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_readback_api_failure_names_the_saved_ids(self, mock_svc, mock_rb):
        """A 5xx on the verification read must not hide that the write
        already succeeded."""
        service = _service(_ok_response(created=("s.saved",)))
        mock_svc.return_value = service
        mock_rb.side_effect = GdocError("API error (503): Backend Error")
        with pytest.raises(GdocError, match="s.saved") as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert "could not be verified" in str(exc.value)
        assert "API error (503)" in str(exc.value)
        assert service.documents.return_value.batchUpdate.call_count == 1

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_readback_transport_failure_names_the_saved_ids(
        self, mock_svc, mock_rb,
    ):
        """Only HttpError is translated inside the verification read; an
        untranslated ConnectionError must still report the IDs the batch
        already saved instead of escaping as a generic error."""
        mock_svc.return_value = _service(_ok_response(created=("s.saved",)))
        mock_rb.side_effect = ConnectionError("connection reset")
        with pytest.raises(GdocError, match=r"s\.saved") as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert "could not be verified" in str(exc.value)
        assert "connection reset" in str(exc.value)
        assert exc.value.exit_code == 1

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_readback_missing_id_is_an_error(self, mock_svc, mock_rb):
        mock_svc.return_value = _service(_ok_response(created=("s.1", "s.2")))
        mock_rb.return_value = _readback("s.1")
        with pytest.raises(GdocError, match=r"read-back: s\.2"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.api.docs.get_docs_service")
    def test_readback_finds_ids_in_style_maps_and_other_tabs(
        self, mock_svc, mock_rb,
    ):
        mock_svc.return_value = _service(_ok_response(created=("s.style",)))
        mock_rb.return_value = {"tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {"body": {"content": []}},
            },
            {"tabProperties": {"tabId": "t.1"}, "documentTab": {"body": {"content": [
                _para({
                    "startIndex": 1, "endIndex": 5,
                    "textRun": {
                        "content": "abcd",
                        "suggestedTextStyleChanges": {
                            "s.style": {"textStyle": {"bold": True}},
                        },
                    },
                }),
            ]}}},
        ]}
        result = suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert result.suggestion_ids == ["s.style"]


class TestSuggestErrors:
    @patch("gdoc.api.docs.get_docs_service")
    def test_5xx_on_the_batch_is_indeterminate(self, mock_svc):
        """A 503 can arrive after Google applied the mutation: like a
        transport failure it must say the outcome is unknown, not read as
        a generic API error inviting a blind retry."""
        mock_svc.return_value = _service(
            batch_error=_http_error(503, reason="Backend Error"),
        )
        with pytest.raises(GdocError) as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        msg = str(exc.value)
        assert "outcome is unknown" in msg
        assert "503" in msg
        assert "Inspect the document" in msg

    @patch("gdoc.api.docs.get_docs_service")
    def test_batch_transport_failure_reports_unknown_outcome(self, mock_svc):
        """A timeout/reset on batchUpdate itself can land after Google has
        accepted the write: the error must say the outcome is unknown and
        point at the document, not surface as a generic unexpected error."""
        mock_svc.return_value = _service(
            batch_error=ConnectionError("connection reset"),
        )
        with pytest.raises(GdocError) as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        msg = str(exc.value)
        assert "outcome is unknown" in msg
        assert "connection reset" in msg
        assert "Inspect the document" in msg
        assert exc.value.exit_code == 1

    @patch("gdoc.api.docs.get_docs_service")
    def test_batch_transport_failure_with_no_message_names_the_type(
        self, mock_svc,
    ):
        """str(TimeoutError()) is empty; the error must fall back to the
        exception type instead of an empty pair of parentheses."""
        mock_svc.return_value = _service(batch_error=TimeoutError())
        with pytest.raises(GdocError, match="TimeoutError"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_batch_refresh_network_failure_is_pre_send(self, mock_svc):
        """TransportError is raised while refreshing the token, before the
        request is sent — the error must say nothing was written, not that
        the outcome is unknown."""
        from google.auth.exceptions import TransportError

        mock_svc.return_value = _service(
            batch_error=TransportError("connection refused"),
        )
        with pytest.raises(GdocError) as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        msg = str(exc.value)
        assert "No change was made" in msg
        assert "outcome is unknown" not in msg
        assert not isinstance(exc.value, AuthError)

    @patch("gdoc.api.docs.get_docs_service")
    def test_batch_refresh_credential_failure_is_auth_error(self, mock_svc):
        """A revoked/expired-beyond-refresh credential fails before the
        request is sent and must keep its AuthError classification
        (exit 2), not be reported as an indeterminate write."""
        from google.auth.exceptions import RefreshError

        mock_svc.return_value = _service(
            batch_error=RefreshError("invalid_grant: Token has been revoked"),
        )
        with pytest.raises(AuthError, match="Run `gdoc auth`"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_400_unknown_name_means_no_preview(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(
            400, b'{"error": {"message": "Invalid JSON payload received. '
                 b'Unknown name \\"write_mode\\" at \'write_control\'"}}',
            reason="Bad Request",
        ))
        with pytest.raises(GdocError, match="not enrolled") as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert "No change was made" in str(exc.value)
        assert exc.value.exit_code == 1

    @patch("gdoc.api.docs.get_docs_service")
    def test_400_no_request_set_means_no_preview(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(
            400, b'{"error": {"message": "Invalid requests[0]: No request set."}}',
        ))
        with pytest.raises(GdocError, match="suggest mode not available"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_400_cannot_find_field_means_no_preview(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(
            400, b'{"error": {"message": "Cannot find field."}}',
        ))
        with pytest.raises(GdocError, match="suggest mode not available"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_400_revision_mismatch_asks_for_rerun(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(
            400, b'{"error": {"message": "The revision ID is stale"}}',
        ))
        with pytest.raises(GdocError, match="re-run it"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_400_malformed_revision_is_not_reported_as_a_race(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(
            400, b'{"error": {"message": "Invalid value at '
                 b'\'write_control.required_revision_id\'"}}',
            reason="Bad Request",
        ))
        with pytest.raises(GdocError) as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        assert "re-run it" not in str(exc.value)

    @patch("gdoc.api.docs.get_docs_service")
    def test_403_reports_both_possibilities(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(403))
        with pytest.raises(GdocError, match="Permission denied: doc1") as exc:
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        msg = str(exc.value)
        assert "comment or edit access" in msg
        assert "Developer Preview" in msg

    @patch("gdoc.api.docs.get_docs_service")
    def test_401_is_auth_error(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(401))
        with pytest.raises(AuthError):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_is_not_found(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(404))
        with pytest.raises(GdocError, match="Document not found: doc1"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @patch("gdoc.api.docs.get_docs_service")
    def test_other_400_is_generic_api_error(self, mock_svc):
        mock_svc.return_value = _service(batch_error=_http_error(
            400, b'{"error": {"message": "Index 99 must be less than end"}}',
            reason="Bad Request",
        ))
        with pytest.raises(GdocError, match=r"API error \(400\)"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")

    @pytest.mark.parametrize("markdown", [
        "# Heading",
        "- item",
        "1. item",
        "---",
        "> quote",
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "text\n\n## Sub\nmore",
    ])
    @patch("gdoc.api.docs.get_docs_service")
    def test_structural_markdown_fails_before_write(self, mock_svc, markdown):
        service = _service(_ok_response())
        mock_svc.return_value = service
        with pytest.raises(GdocError, match="not supported yet") as exc:
            suggest_replacement("doc1", MATCH, markdown, "rev", tab_id="t.0")
        assert exc.value.exit_code == 3
        service.documents.return_value.batchUpdate.assert_not_called()


# ---------------------------------------------------------------------------
# helpers: inline-only check, overlap detection, id collection
# ---------------------------------------------------------------------------


class TestCheckInlineOnly:
    @pytest.mark.parametrize("markdown", [
        "plain",
        "two\nparagraphs",
        "a\n\nb",
        "```\ncode\n```",
        "**bold** and *italic* and ~~strike~~",
        "`code` and [a link](https://example.com)",
        "",
    ])
    def test_inline_markdown_accepted(self, markdown):
        check_inline_only_markdown(parse_markdown(markdown))

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_readback("suggest.abc"),
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_paragraph_replacement_sends_no_paragraph_style(
        self, mock_svc, _rb,
    ):
        """Newlines become suggested paragraph breaks; the new paragraphs
        inherit the anchor's style rather than being reset to NORMAL_TEXT."""
        mock_svc.return_value = _service(_ok_response())
        suggest_replacement("doc1", MATCH, "a\n\nb", "rev", tab_id="t.0")
        reqs = _batch_call(mock_svc.return_value).kwargs["body"]["requests"]
        assert [next(iter(r)) for r in reqs] == ["deleteContentRange", "insertText"]
        assert reqs[1]["insertText"]["text"] == "a\n\nb"

    @pytest.mark.parametrize("markdown", [
        "## Heading", "* bullet", "2. numbered", "***", "> quoted",
        "| h |\n|---|\n| c |",
    ])
    def test_structural_markdown_rejected(self, markdown):
        with pytest.raises(GdocError) as exc:
            check_inline_only_markdown(parse_markdown(markdown))
        assert exc.value.exit_code == 3


class TestFindSuggestionsInRange:
    def _body_with_insertion(self):
        return _body(_para(
            _run("hello ", 1, 7),
            {
                "startIndex": 7, "endIndex": 12,
                "textRun": {"content": "brave", "suggestedInsertionIds": ["s.ins"]},
            },
            _run(" world\n", 12, 19),
        ))

    def test_range_inside_clean_run_is_clear(self):
        assert find_suggestions_in_range(self._body_with_insertion(), 1, 6) == set()

    def test_range_touching_suggested_insertion(self):
        assert find_suggestions_in_range(self._body_with_insertion(), 5, 9) == {"s.ins"}

    def test_range_adjacent_but_not_overlapping_is_clear(self):
        # [1, 7) ends exactly where the suggested run starts.
        assert find_suggestions_in_range(self._body_with_insertion(), 1, 7) == set()

    def test_suggested_deletion_and_style_change(self):
        body = _body(_para(
            {
                "startIndex": 1, "endIndex": 5,
                "textRun": {"content": "gone", "suggestedDeletionIds": ["s.del"]},
            },
            {
                "startIndex": 5, "endIndex": 10,
                "textRun": {
                    "content": "bold\n",
                    "suggestedTextStyleChanges": {
                        "s.sty": {"textStyle": {"bold": True}},
                    },
                },
            },
        ))
        assert find_suggestions_in_range(body, 2, 3) == {"s.del"}
        assert find_suggestions_in_range(body, 6, 8) == {"s.sty"}
        assert find_suggestions_in_range(body, 1, 10) == {"s.del", "s.sty"}

    def test_paragraph_level_suggestion_counts(self):
        body = _body(_para(
            _run("hello\n", 1, 7),
            suggestedParagraphStyleChanges={"s.para": {}},
        ))
        assert find_suggestions_in_range(body, 2, 4) == {"s.para"}

    def test_other_paragraph_suggestion_ignored(self):
        body = _body(
            _para(_run("hello\n", 1, 7)),
            _para({
                "startIndex": 7, "endIndex": 12,
                "textRun": {"content": "more\n", "suggestedInsertionIds": ["s.far"]},
            }),
        )
        assert find_suggestions_in_range(body, 1, 6) == set()

    def test_table_cell_is_searched(self):
        cell = {"content": [_para({
            "startIndex": 10, "endIndex": 15,
            "textRun": {"content": "cell\n", "suggestedInsertionIds": ["s.cell"]},
        })]}
        body = _body({
            "startIndex": 8, "endIndex": 20,
            "table": {"tableRows": [{"tableCells": [cell]}]},
        })
        assert find_suggestions_in_range(body, 11, 13) == {"s.cell"}
        assert find_suggestions_in_range(body, 1, 5) == set()

    def test_zero_width_insert_inside_suggested_run(self):
        assert find_suggestions_in_range(self._body_with_insertion(), 9, 9) == {"s.ins"}

    def test_suggested_section_break_inside_match_blocks(self):
        """A match can span a section break (segments concatenate across
        it); a suggested break inside the range is a review thread too."""
        body = _body(
            _para(_run("Hello wor", 1, 10)),
            {
                "startIndex": 10, "endIndex": 11,
                "sectionBreak": {"suggestedInsertionIds": ["s.sec"]},
            },
            _para(_run("ld!\n", 11, 15)),
        )
        assert find_suggestions_in_range(body, 7, 13) == {"s.sec"}

    def test_plain_section_break_does_not_block(self):
        body = _body(
            _para(_run("Hello wor", 1, 10)),
            {"startIndex": 10, "endIndex": 11, "sectionBreak": {}},
            _para(_run("ld!\n", 11, 15)),
        )
        assert find_suggestions_in_range(body, 7, 13) == set()

    def test_suggestion_inside_overlapping_toc_blocks(self):
        body = _body({
            "startIndex": 8, "endIndex": 20,
            "tableOfContents": {"content": [_para({
                "startIndex": 9, "endIndex": 19,
                "textRun": {
                    "content": "toc entry\n",
                    "suggestedInsertionIds": ["s.toc"],
                },
            })]},
        })
        assert find_suggestions_in_range(body, 5, 12) == {"s.toc"}
        # A range that never reaches the TOC stays clear.
        assert find_suggestions_in_range(body, 1, 5) == set()


class TestPreviewGate:
    """check_suggest_preview_access: the raw preview-only read."""

    def _run(self, resp):
        session = MagicMock()
        session.get.return_value = resp
        with patch("gdoc.api.docs.account_cache_key", return_value=("acct", None)), \
             patch("gdoc.auth.get_credentials", return_value="creds"), \
             patch(
                 "google.auth.transport.requests.AuthorizedSession",
                 return_value=session,
             ):
            check_suggest_preview_access("doc1")
        return session

    def test_registered_project_echoes_the_field(self):
        session = self._run(_gate_response(200, {
            "documentId": "doc1", "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        }))
        call = session.get.call_args
        assert call.args[0] == "https://docs.googleapis.com/v1/documents/doc1"
        assert call.kwargs["params"] == {
            "includeTabsContent": "true",
            "suggestionsViewMode": "SUGGESTIONS_INLINE",
            "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
            "fields": "documentId,commentsViewMode",
        }

    def test_unregistered_project_400_unknown_name(self):
        with pytest.raises(GdocError, match="not enrolled") as exc:
            self._run(_gate_response(
                400,
                text='{"error": {"message": "Invalid JSON payload received. '
                     'Unknown name \\"comments_view_mode\\": Cannot find field."}}',
            ))
        assert "No change was made" in str(exc.value)

    def test_200_without_echo_is_refused(self):
        """A backend that silently drops the preview field can't be trusted
        to honour writeMode either."""
        with pytest.raises(GdocError, match="did not apply"):
            self._run(_gate_response(200, {"documentId": "doc1"}))

    def test_403_is_permission(self):
        with pytest.raises(GdocError, match="Permission denied: doc1"):
            self._run(_gate_response(403, text="forbidden"))

    def test_401_is_auth_error(self):
        with pytest.raises(AuthError):
            self._run(_gate_response(401, text="unauthorized"))

    def test_404_is_not_found(self):
        with pytest.raises(GdocError, match="Document not found: doc1"):
            self._run(_gate_response(404, text="nope"))

    def test_other_status_is_api_error(self):
        with pytest.raises(GdocError, match=r"API error \(503\)"):
            self._run(_gate_response(503, text="backend", reason="Service Unavailable"))

    def test_refresh_failure_is_auth_error(self):
        from google.auth.exceptions import RefreshError

        with patch("gdoc.api.docs.account_cache_key", return_value=("acct", None)), \
             patch("gdoc.auth.get_credentials", return_value="creds"), \
             patch(
                 "google.auth.transport.requests.AuthorizedSession",
                 side_effect=RefreshError("invalid_grant: Token has been revoked"),
             ):
            with pytest.raises(AuthError, match="Run `gdoc auth`"):
                check_suggest_preview_access("doc1")

    def test_network_failure_fails_closed_as_gdoc_error(self):
        from requests.exceptions import ConnectionError as ReqConnectionError

        session = MagicMock()
        session.get.side_effect = ReqConnectionError("dns failure")
        with patch("gdoc.api.docs.account_cache_key", return_value=("acct", None)), \
             patch("gdoc.auth.get_credentials", return_value="creds"), \
             patch(
                 "google.auth.transport.requests.AuthorizedSession",
                 return_value=session,
             ):
            with pytest.raises(GdocError, match="network error") as exc:
                check_suggest_preview_access("doc1")
        assert "No change was made" in str(exc.value)
        assert not isinstance(exc.value, AuthError)

    def test_refresh_network_failure_is_not_an_auth_error(self):
        """google-auth wraps a network failure during token refresh in
        TransportError, a GoogleAuthError subclass — it must take the
        fail-closed network branch, not become "run `gdoc auth`" (exit 2)
        over a Wi-Fi blip."""
        from google.auth.exceptions import TransportError

        session = MagicMock()
        session.get.side_effect = TransportError("connection refused")
        with patch("gdoc.api.docs.account_cache_key", return_value=("acct", None)), \
             patch("gdoc.auth.get_credentials", return_value="creds"), \
             patch(
                 "google.auth.transport.requests.AuthorizedSession",
                 return_value=session,
             ):
            with pytest.raises(GdocError, match="network error") as exc:
                check_suggest_preview_access("doc1")
        assert "No change was made" in str(exc.value)
        assert not isinstance(exc.value, AuthError)

    @patch("gdoc.api.docs.get_docs_service")
    def test_gate_network_failure_blocks_the_write(
        self, mock_svc, _preview_gate_passes,
    ):
        service = _service(_ok_response())
        mock_svc.return_value = service
        _preview_gate_passes.side_effect = GdocError(
            "suggest mode check failed before any write (network error: x)",
        )
        with pytest.raises(GdocError, match="network error"):
            suggest_replacement("doc1", MATCH, "x", "rev", tab_id="t.0")
        service.documents.return_value.batchUpdate.assert_not_called()

    def test_other_400_is_api_error_not_enrollment(self):
        with pytest.raises(GdocError, match=r"API error \(400\)"):
            self._run(_gate_response(400, text="something else", reason="Bad Request"))


class TestTokenIdentity:
    def test_reads_parses_and_fails_soft(self, tmp_path):
        from gdoc.api.docs import _token_identity

        token = tmp_path / "token.json"
        with patch("gdoc.util.token_path_for", return_value=token):
            assert _token_identity("acct") == (None, None)  # missing file
            token.write_text(
                '{"client_id": "abc.apps", "refresh_token": "rt1"}'
            )
            assert _token_identity("acct") == ("abc.apps", "rt1")
            token.write_text('{"client_id": "abc.apps"}')
            assert _token_identity("acct") == ("abc.apps", None)
            token.write_text("not json")
            assert _token_identity("acct") == (None, None)
            token.write_text('["a", "list"]')
            assert _token_identity("acct") == (None, None)


class TestTableContainerSuggestions:
    def _table(self, table_extra=None, row_extra=None, cell_extra=None):
        para = _para(_run("cell\n", 10, 15))
        cell = {"startIndex": 9, "endIndex": 15, "content": [para]}
        cell.update(cell_extra or {})
        row = {"startIndex": 8, "endIndex": 15, "tableCells": [cell]}
        row.update(row_extra or {})
        table = {"tableRows": [row]}
        table.update(table_extra or {})
        return _body({"startIndex": 7, "endIndex": 17, "table": table})

    def test_suggested_table_insertion_blocks_cell_edit(self):
        body = self._table(table_extra={"suggestedInsertionIds": ["s.table"]})
        assert find_suggestions_in_range(body, 11, 13) == {"s.table"}

    def test_suggested_row_deletion_blocks_cell_edit(self):
        body = self._table(row_extra={"suggestedDeletionIds": ["s.row"]})
        assert find_suggestions_in_range(body, 11, 13) == {"s.row"}

    def test_suggested_cell_style_blocks_cell_edit(self):
        body = self._table(cell_extra={
            "suggestedTableCellStyleChanges": {"s.cell": {}},
        })
        assert find_suggestions_in_range(body, 11, 13) == {"s.cell"}

    def test_row_outside_range_is_ignored(self):
        body = self._table(row_extra={"suggestedDeletionIds": ["s.row"]})
        # Second, clean row at a later index holds the match.
        body["content"][0]["table"]["tableRows"].append({
            "startIndex": 15, "endIndex": 22,
            "tableCells": [{"startIndex": 16, "endIndex": 22, "content": [
                _para(_run("other\n", 17, 22)),
            ]}],
        })
        body["content"][0]["endIndex"] = 23
        assert find_suggestions_in_range(body, 18, 20) == set()


class TestCollectSuggestionIds:
    def test_walks_ids_lists_and_changes_maps(self):
        doc = {"tabs": [{"documentTab": {"body": {"content": [
            _para(
                {"startIndex": 1, "endIndex": 3, "textRun": {
                    "content": "ab", "suggestedInsertionIds": ["a"],
                }},
                {"startIndex": 3, "endIndex": 5, "textRun": {
                    "content": "cd", "suggestedDeletionIds": ["b"],
                    "suggestedTextStyleChanges": {"c": {}},
                }},
                suggestedBulletChanges={"d": {}},
            ),
        ]}}}]}
        assert collect_suggestion_ids(doc) == {"a", "b", "c", "d"}

    def test_empty_document(self):
        assert collect_suggestion_ids({"tabs": []}) == set()

    def test_positioned_object_ids_map_is_keyed_by_suggestion_id(self):
        """suggestedPositionedObjectIds is a map despite the *Ids name."""
        para = _para(
            _run("hello\n", 1, 7),
            suggestedPositionedObjectIds={"s.pos": {"objectIds": ["kix.1"]}},
        )
        assert collect_suggestion_ids({"body": _body(para)}) == {"s.pos"}
        assert find_suggestions_in_range(_body(para), 2, 4) == {"s.pos"}


class TestSuggestionResult:
    def test_suggestion_ids_property_dedupes_in_order(self):
        r = SuggestionResult(1, ["b", "a"], ["a", "c"], "ALL_SAVED")
        assert r.suggestion_ids == ["b", "a", "c"]


# ---------------------------------------------------------------------------
# CLI: parser
# ---------------------------------------------------------------------------


def _option_strings(parser, command):
    sub = next(a for a in parser._actions if a.dest == "command")
    p = sub.choices[command]
    return {o for a in p._actions for o in a.option_strings}, [
        a.dest for a in p._actions if not a.option_strings
    ]


class TestSuggestParser:
    def test_parity_with_edit_minus_cell_mode(self):
        parser = build_parser()
        edit_opts, edit_pos = _option_strings(parser, "edit")
        sug_opts, sug_pos = _option_strings(parser, "suggest")
        assert edit_opts - sug_opts == {"--cell", "--col", "--table"}
        assert sug_opts - edit_opts == set()
        assert sug_pos == edit_pos == ["doc", "old_text", "new_text"]

    def test_parses_flags(self):
        args = build_parser().parse_args([
            "suggest", "abc", "old", "new", "--all", "--case-sensitive",
            "--normalize", "--quiet", "--tab", "Draft", "--json",
        ])
        assert args.func is cmd_suggest
        assert (args.all, args.case_sensitive, args.normalize, args.quiet) == (
            True, True, True, True,
        )
        assert args.tab == "Draft"
        assert args.json is True
        assert not hasattr(args, "cell")

    def test_cell_is_rejected(self):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["suggest", "abc", "--cell", "Label", "x"])
        assert exc.value.code == 3


# ---------------------------------------------------------------------------
# CLI: cmd_suggest
# ---------------------------------------------------------------------------


def _args(**overrides):
    defaults = {
        "command": "suggest",
        "doc": "abc123",
        "old_text": "hello",
        "new_text": "world",
        "old_file": None,
        "new_file": None,
        "all": False,
        "case_sensitive": False,
        "normalize": False,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
        "tab": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _structure(revision_id="rev123", tabs=None):
    """A documents.get(includeTabsContent, SUGGESTIONS_INLINE) response."""
    if tabs is None:
        tabs = [("t.first", "Tab 1", _body(_para(_run("hello world\n", 1, 13))))]
    return {
        "revisionId": revision_id,
        "tabs": [
            {
                "tabProperties": {"tabId": tid, "title": title, "index": i},
                "documentTab": {"body": body},
            }
            for i, (tid, title, body) in enumerate(tabs)
        ],
    }


def _result(occurrences=1, created=("suggest.abc",), updated=()):
    return SuggestionResult(
        occurrences, list(created), list(updated), "ALL_SAVED",
    )


_VERSION = {"version": 42, "modifiedTime": "2026-08-26T00:00:00Z"}


class TestCmdSuggest:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_terse_output_names_suggestion(
        self, _pf, _doc, _sug, _ver, _state, capsys,
    ):
        assert cmd_suggest(_args()) == 0
        out = capsys.readouterr().out
        assert out == "OK suggested 1 occurrence (#suggest.abc)\n"

    @patch(
        "gdoc.state.update_state_after_command",
        side_effect=OSError("disk full"),
    )
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_state_persistence_failure_after_write_still_succeeds(
        self, _pf, _doc, _sug, _ver, _state, capsys,
    ):
        """The local state file failing to write after the verified
        suggestion must warn and exit 0 — reporting failure would invite an
        automated caller to retry a mutation that already succeeded."""
        assert cmd_suggest(_args()) == 0
        captured = capsys.readouterr()
        assert "OK suggested 1 occurrence (#suggest.abc)" in captured.out
        assert "WARN: suggestion saved" in captured.err
        assert "disk full" in captured.err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch(
        "gdoc.api.docs.suggest_replacement", return_value=_result(3, ("s.1", "s.2")),
    )
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_terse_output_plural(self, _pf, _doc, _sug, _ver, _state, capsys):
        cmd_suggest(_args(all=True))
        assert capsys.readouterr().out == "OK suggested 3 occurrences (#s.1, #s.2)\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch(
        "gdoc.api.docs.suggest_replacement",
        return_value=_result(1, ("s.new",), ("s.merged",)),
    )
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_json_output(self, _pf, _doc, _sug, _ver, _state, capsys):
        cmd_suggest(_args(json=True))
        assert json.loads(capsys.readouterr().out) == {
            "ok": True,
            "suggested": 1,
            "suggestionIds": ["s.new", "s.merged"],
            "createdSuggestionIds": ["s.new"],
            "updatedSuggestionIds": ["s.merged"],
        }

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch(
        "gdoc.api.docs.suggest_replacement", return_value=_result(2, ("s.1", "s.2")),
    )
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_plain_output(self, _pf, _doc, _sug, _ver, _state, capsys):
        cmd_suggest(_args(plain=True, all=True))
        assert capsys.readouterr().out.splitlines() == [
            "id\tabc123",
            "status\tsuggested",
            "suggested\t2",
            "suggestion_ids\ts.1,s.2",
        ]

    @patch("gdoc.api.docs._token_identity", return_value=("cid.apps", "rt1"))
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_reads_inline_view_and_targets_first_tab(
        self, _pf, mock_doc, mock_sug, _ver, _state, _tid,
    ):
        cmd_suggest(_args(doc="https://docs.google.com/document/d/abc123/edit"))
        mock_doc.assert_called_once_with(
            "abc123", suggestions_view_mode="SUGGESTIONS_INLINE",
        )
        # The token identity captured before the read travels to the write,
        # so a re-auth anywhere between them aborts pre-send.
        mock_sug.assert_called_once_with(
            "abc123", [{"startIndex": 1, "endIndex": 6}], "world", "rev123",
            tab_id="t.first",
            expected_token_identity=("cid.apps", "rt1"),
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_tab_option_resolves_by_title_and_propagates_tab_id(
        self, _pf, mock_doc, mock_sug, _ver, _state,
    ):
        mock_doc.return_value = _structure(tabs=[
            ("t.first", "Tab 1", _body(_para(_run("nothing here\n", 1, 14)))),
            ("t.draft", "Draft", _body(_para(_run("say hello\n", 1, 11)))),
        ])
        cmd_suggest(_args(tab="draft"))
        call = mock_sug.call_args
        assert call.args[1] == [{"startIndex": 5, "endIndex": 10}]
        assert call.kwargs["tab_id"] == "t.draft"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_url_tab_targets_that_tab(
        self, _pf, mock_doc, mock_sug, _ver, _state,
    ):
        # A doc URL's ?tab= drives suggest like --tab does.
        mock_doc.return_value = _structure(tabs=[
            ("t.first", "Tab 1", _body(_para(_run("nothing here\n", 1, 14)))),
            ("t.draft", "Draft", _body(_para(_run("say hello\n", 1, 11)))),
        ])
        cmd_suggest(_args(
            doc="https://docs.google.com/document/d/abc123/edit?tab=t.draft",
        ))
        call = mock_sug.call_args
        assert call.args[1] == [{"startIndex": 5, "endIndex": 10}]
        assert call.kwargs["tab_id"] == "t.draft"

    @patch("gdoc.api.docs._token_identity", return_value=("cid.apps", "rt1"))
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_url_t0_is_ambient_noise_targets_first_tab(
        self, _pf, mock_doc, mock_sug, _ver, _state, _tid,
    ):
        # ?tab=t.0 is the editor's ambient default -> first tab, as with
        # a bare URL.
        cmd_suggest(_args(
            doc="https://docs.google.com/document/d/abc123/edit?tab=t.0",
        ))
        assert mock_sug.call_args.kwargs["tab_id"] == "t.first"

    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_no_match_exit_3(self, _pf, _doc, mock_sug):
        with pytest.raises(GdocError, match="no match found") as exc:
            cmd_suggest(_args(old_text="absent"))
        assert exc.value.exit_code == 3
        mock_sug.assert_not_called()

    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_multiple_matches_need_all(self, _pf, mock_doc, mock_sug):
        mock_doc.return_value = _structure(tabs=[
            ("t.0", "Tab 1", _body(_para(_run("hello hello\n", 1, 13)))),
        ])
        with pytest.raises(GdocError, match="multiple matches") as exc:
            cmd_suggest(_args())
        assert exc.value.exit_code == 3
        mock_sug.assert_not_called()

    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_self_overlapping_all_matches_exit_3(
        self, _pf, mock_doc, mock_svc, mock_ver,
    ):
        mock_doc.return_value = _structure(tabs=[
            ("t.0", "Tab 1", _body(_para(_run("aaa\n", 1, 5)))),
        ])
        service = _service(_ok_response())
        mock_svc.return_value = service
        with pytest.raises(GdocError, match="overlap each other") as exc:
            cmd_suggest(_args(old_text="aa", new_text="b", all=True))
        assert exc.value.exit_code == 3
        service.documents.return_value.batchUpdate.assert_not_called()
        mock_ver.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_version_lookup_failure_after_write_still_reports_ids(
        self, _pf, _doc, _sug, mock_ver, mock_state, capsys,
    ):
        """The suggestion is saved and verified; a Drive hiccup afterwards
        must not hide its ID behind an ordinary error."""
        mock_ver.side_effect = GdocError("API error (503): Backend Error")
        assert cmd_suggest(_args(json=True)) == 0
        out, err = capsys.readouterr()
        assert json.loads(out)["suggestionIds"] == ["suggest.abc"]
        assert "WARN: suggestion saved (#suggest.abc)" in err
        assert "API error (503)" in err
        assert "state not updated" in err
        mock_state.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_version_lookup_transport_error_after_write_still_reports_ids(
        self, _pf, _doc, _sug, mock_ver, mock_state, capsys,
    ):
        """drive.get_file_version only translates HttpError; a raw
        ConnectionError must not escape and hide the saved IDs either."""
        mock_ver.side_effect = ConnectionError("connection reset")
        assert cmd_suggest(_args()) == 0
        out, err = capsys.readouterr()
        assert out == "OK suggested 1 occurrence (#suggest.abc)\n"
        assert "WARN: suggestion saved (#suggest.abc)" in err
        assert "connection reset" in err
        mock_state.assert_not_called()

    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_overlap_with_existing_suggestion_exit_3(self, _pf, mock_doc, mock_sug):
        mock_doc.return_value = _structure(tabs=[("t.0", "Tab 1", _body(_para(
            _run("say ", 1, 5),
            {
                "startIndex": 5, "endIndex": 10,
                "textRun": {"content": "hello", "suggestedInsertionIds": ["suggest.x"]},
            },
            _run("\n", 10, 11),
        )))])
        with pytest.raises(
            GdocError, match=r"overlaps existing suggestion\(s\) suggest\.x",
        ) as exc:
            cmd_suggest(_args())
        assert exc.value.exit_code == 3
        mock_sug.assert_not_called()

    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_overlap_with_suggested_deletion_exit_3(self, _pf, mock_doc, mock_sug):
        mock_doc.return_value = _structure(tabs=[("t.0", "Tab 1", _body(_para(
            {
                "startIndex": 1, "endIndex": 6,
                "textRun": {"content": "hello", "suggestedDeletionIds": ["suggest.d"]},
            },
            _run(" world\n", 6, 13),
        )))])
        with pytest.raises(GdocError, match="suggest.d"):
            cmd_suggest(_args())
        mock_sug.assert_not_called()

    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_structural_markdown_rejected_before_any_api_call(
        self, mock_pf, mock_doc, mock_sug,
    ):
        with pytest.raises(GdocError, match="not supported yet") as exc:
            cmd_suggest(_args(new_text="# Heading"))
        assert exc.value.exit_code == 3
        mock_pf.assert_not_called()
        mock_doc.assert_not_called()
        mock_sug.assert_not_called()

    @pytest.mark.parametrize("new_text", [
        "- bullet", "1. item", "---", "> quote", "| a |\n|---|\n| b |",
    ])
    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_each_structural_form_rejected(self, mock_pf, mock_sug, new_text):
        with pytest.raises(GdocError) as exc:
            cmd_suggest(_args(new_text=new_text))
        assert exc.value.exit_code == 3
        mock_pf.assert_not_called()
        mock_sug.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_inline_markdown_passes_through_verbatim(
        self, _pf, _doc, mock_sug, _ver, _state,
    ):
        cmd_suggest(_args(new_text="**bold** [x](https://x.test)"))
        assert mock_sug.call_args.args[2] == "**bold** [x](https://x.test)"

    @patch("gdoc.api.docs.suggest_replacement")
    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_structure(revision_id=""),
    )
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_missing_revision_id_is_passed_for_api_to_refuse(
        self, _pf, _doc, mock_sug,
    ):
        """A read without revisionId: the CLI hands the empty value to the
        API layer, which refuses rather than sending ""."""
        mock_sug.side_effect = GdocError("cannot suggest: ... commenter permission")
        with pytest.raises(GdocError, match="commenter permission"):
            cmd_suggest(_args())
        assert mock_sug.call_args.args[3] == ""

    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value=_structure(revision_id=""),
    )
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_docs_service")
    def test_missing_revision_id_end_to_end_never_calls_batch_update(
        self, mock_svc, _pf, _doc,
    ):
        service = _service(_ok_response())
        mock_svc.return_value = service
        with pytest.raises(GdocError, match="commenter permission"):
            cmd_suggest(_args())
        service.documents.return_value.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_reader_403_on_inline_read_gets_permission_hint(
        self, _pf, mock_doc, mock_sug,
    ):
        """Live: a reader's SUGGESTIONS_INLINE read is refused with 403."""
        mock_doc.side_effect = GdocError("Permission denied: abc123")
        with pytest.raises(GdocError, match="comment or edit access") as exc:
            cmd_suggest(_args())
        assert str(exc.value).startswith("Permission denied: abc123")
        assert exc.value.exit_code == 1
        mock_sug.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_state_updated_as_partial_write(
        self, _pf, _doc, _sug, _ver, mock_state,
    ):
        change_info = ChangeInfo(current_version=41)
        _pf.return_value = change_info
        cmd_suggest(_args())
        mock_state.assert_called_once_with(
            "abc123", change_info, command="suggest", quiet=False,
            command_version=42,
        )

    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight")
    def test_state_does_not_advance_read_baseline(
        self, mock_pf, _doc, _sug, _ver, tmp_path,
    ):
        from gdoc.state import DocState, load_state, save_state

        mock_pf.return_value = ChangeInfo(current_version=41)
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("abc123", DocState(last_version=40, last_read_version=40))
            cmd_suggest(_args())
            state = load_state("abc123")
        assert state.last_version == 42
        assert state.last_read_version == 40

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_quiet_skips_preflight(self, mock_pf, _doc, _sug, _ver, mock_state):
        cmd_suggest(_args(quiet=True))
        mock_pf.assert_called_once_with("abc123", quiet=True)
        assert mock_state.call_args.kwargs["quiet"] is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_conflict_warning_does_not_block(
        self, mock_pf, _doc, _sug, _ver, _state, capsys,
    ):
        mock_pf.return_value = ChangeInfo(current_version=41, last_read_version=40)
        assert cmd_suggest(_args()) == 0
        assert "WARN: doc changed since last read" in capsys.readouterr().err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_old_and_new_file(self, _pf, _doc, mock_sug, _ver, _state, tmp_path):
        old = tmp_path / "old.txt"
        new = tmp_path / "new.txt"
        old.write_text("hello\n")
        new.write_text("**world**\n")
        cmd_suggest(_args(
            old_text=None, new_text=None, old_file=str(old), new_file=str(new),
        ))
        assert mock_sug.call_args.args[1] == [{"startIndex": 1, "endIndex": 6}]
        assert mock_sug.call_args.args[2] == "**world**"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement", return_value=_result())
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_stdin_dash_for_new_text(
        self, _pf, _doc, mock_sug, _ver, _state, monkeypatch,
    ):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
        cmd_suggest(_args(new_text="-"))
        assert mock_sug.call_args.args[2] == "from stdin"

    def test_missing_text_exit_3(self):
        with pytest.raises(
            GdocError, match="old_text and new_text required",
        ) as exc:
            cmd_suggest(_args(old_text=None, new_text=None))
        assert exc.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_VERSION)
    @patch("gdoc.api.docs.suggest_replacement")
    @patch("gdoc.api.docs.get_document_structure", return_value=_structure())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_api_failure_propagates_without_state_update(
        self, _pf, _doc, mock_sug, mock_ver, mock_state,
    ):
        mock_sug.side_effect = GdocError("suggest mode not available: ...")
        with pytest.raises(GdocError, match="suggest mode not available"):
            cmd_suggest(_args())
        mock_ver.assert_not_called()
        mock_state.assert_not_called()

    @patch("gdoc.api.docs.suggest_replacement")
    @patch(
        "gdoc.api.docs.get_document_structure",
        return_value={"revisionId": "r", "tabs": []},
    )
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_document_without_tabs_is_an_error(self, _pf, _doc, mock_sug):
        with pytest.raises(GdocError, match="no tabs"):
            cmd_suggest(_args())
        mock_sug.assert_not_called()


class TestMcpExposure:
    def test_suggest_is_a_write_tool_with_file_params_hidden(self):
        from gdoc import mcp

        assert mcp.EXPOSED_COMMANDS["suggest"] is False
        assert mcp._LOCAL_PATH_PARAMS["suggest"] == frozenset(
            {"old_file", "new_file"},
        )
        assert "suggest" in mcp._DESCRIPTION_NOTES
