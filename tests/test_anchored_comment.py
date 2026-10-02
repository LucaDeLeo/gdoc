"""Tests for anchored comments: insert_comment API + cmd_comment fallback.

The Docs API insertComment request (Workspace Developer Preview) creates a
real anchored comment. A definite preview or capability rejection allows a
Drive quotedFileContent fallback; ambiguous targets and uncertain writes refuse
further creation.
"""

import json
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.docs import (
    _PERMISSION_REASONS,
    CommentRevisionConflictError,
    find_text_in_document,
    insert_comment,
)
from gdoc.cli import CommentAnchorResult, _try_anchored_comment, cmd_comment
from gdoc.util import AuthError, GdocError, PreviewUnavailableError


def _http_error(status, content=b""):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Error"
    return HttpError(resp, content, uri="")


def _mock_docs_service(batch_response=None, batch_error=None):
    """Docs service mock whose batchUpdate().execute() returns or raises."""
    service = MagicMock()
    execute = service.documents.return_value.batchUpdate.return_value.execute
    if batch_error is not None:
        execute.side_effect = batch_error
    else:
        execute.return_value = batch_response or {}
    return service


def _google_403(message, reason, domain, status="PERMISSION_DENIED"):
    return {"error": {
        "code": 403, "message": message, "status": status,
        "errors": [{"reason": reason, "domain": domain, "message": message}],
    }}


_NON_PERMISSION_403S = [
    _google_403(
        "Quota exceeded for quota metric 'Write requests'", "rateLimitExceeded",
        "usageLimits", status="RESOURCE_EXHAUSTED",
    ),
    _google_403(
        "User Rate Limit Exceeded", "userRateLimitExceeded", "usageLimits",
        status="RESOURCE_EXHAUSTED",
    ),
    _google_403(
        "Google Docs API has not been used in project 1 before or it is disabled",
        "accessNotConfigured", "usageLimits",
    ),
    _google_403("Daily Limit Exceeded", "dailyLimitExceeded", "usageLimits"),
    {"error": {
        "code": 403, "message": "The caller does not have permission",
        "status": "PERMISSION_DENIED",
        "errors": [
            {"reason": "forbidden", "domain": "global", "message": "x"},
            {"reason": "serviceDisabled", "domain": "global", "message": "y"},
        ],
    }},
]


_OK_RESPONSE = {
    "commentUpdateState": "ALL_SAVED",
    "replies": [
        {"insertComment": {"commentThread": {"commentId": "c_anchor"}}}
    ],
}


class TestInsertComment:
    @patch("gdoc.api.docs.get_docs_service")
    def test_happy_path_returns_thread_id(self, mock_svc):
        service = _mock_docs_service(batch_response=_OK_RESPONSE)
        mock_svc.return_value = service

        result = insert_comment("doc1", "hello", 10, 25)

        assert result == "c_anchor"
        call = service.documents.return_value.batchUpdate.call_args
        assert call.kwargs["documentId"] == "doc1"
        assert call.kwargs["body"] == {
            "requests": [
                {
                    "insertComment": {
                        "content": "hello",
                        "range": {"startIndex": 10, "endIndex": 25},
                    }
                }
            ]
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_id_and_revision_in_request(self, mock_svc):
        service = _mock_docs_service(batch_response=_OK_RESPONSE)
        mock_svc.return_value = service

        insert_comment(
            "doc1", "hello", 10, 25, tab_id="t2", revision_id="rev9",
        )

        body = service.documents.return_value.batchUpdate.call_args.kwargs[
            "body"
        ]
        assert body["writeControl"] == {"requiredRevisionId": "rev9"}
        assert body["requests"][0]["insertComment"]["range"] == {
            "startIndex": 10, "endIndex": 25, "tabId": "t2",
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_revision_mismatch_400_is_a_conflict(self, mock_svc):
        content = (
            b'{"error": {"code": 400, "message": "The provided revision ID '
            b'does not match the latest revision of the document."}}'
        )
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, content),
        )
        with pytest.raises(CommentRevisionConflictError):
            insert_comment("doc1", "hello", 10, 25, revision_id="rev9")

    @patch("gdoc.api.docs.get_docs_service")
    def test_unknown_name_400_raises_preview_unavailable(self, mock_svc):
        content = (
            b'{"error": {"code": 400, "message": "Invalid JSON payload '
            b'received. Unknown name \\"insertComment\\" at '
            b"'requests[0]': Cannot find field.\"}}"
        )
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, content),
        )
        with pytest.raises(PreviewUnavailableError):
            insert_comment("doc1", "hello", 10, 25)

    @patch("gdoc.api.docs.get_docs_service")
    def test_no_request_set_400_raises_preview_unavailable(self, mock_svc):
        # Live-observed non-enrolled behavior (2026-08): the server drops
        # the unrecognized insertComment field and rejects the now-empty
        # request union.
        content = (
            b'{"error": {"code": 400, "message": '
            b'"Invalid requests[0]: No request set."}}'
        )
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, content),
        )
        with pytest.raises(PreviewUnavailableError):
            insert_comment("doc1", "hello", 10, 25)

    @patch("gdoc.api.docs.get_docs_service")
    def test_403_raises_preview_unavailable(self, mock_svc):
        # Comment-only access can't batchUpdate but can comment via Drive.
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(403, b"forbidden"),
        )
        with pytest.raises(PreviewUnavailableError):
            insert_comment("doc1", "hello", 10, 25)

    @pytest.mark.parametrize("reason", sorted(_PERMISSION_REASONS))
    @patch("gdoc.api.docs.get_docs_service")
    def test_structured_403_permission_reason_permits_fallback(
        self, mock_svc, reason,
    ):
        content = json.dumps({"error": {
            "code": 403, "message": "The caller does not have permission",
            "status": "PERMISSION_DENIED",
            "errors": [{"reason": reason, "domain": "global", "message": "x"}],
        }}).encode()
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(403, content),
        )
        with pytest.raises(PreviewUnavailableError):
            insert_comment("doc1", "hello", 10, 25)

    @pytest.mark.parametrize("payload", _NON_PERMISSION_403S)
    @patch("gdoc.api.docs.get_docs_service")
    def test_non_permission_403_refuses_without_fallback(self, mock_svc, payload):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(403, json.dumps(payload).encode()),
        )
        with pytest.raises(GdocError, match="no fallback comment was created") as exc:
            insert_comment("doc1", "hello", 10, 25)
        assert not isinstance(exc.value, PreviewUnavailableError)
        assert exc.value.exit_code == 1
        assert payload["error"]["message"] in str(exc.value)

    @patch("gdoc.api.docs.get_docs_service")
    def test_other_400_raises_gdoc_error(self, mock_svc):
        content = b'{"error": {"code": 400, "message": "Invalid range"}}'
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, content),
        )
        with pytest.raises(GdocError) as exc_info:
            insert_comment("doc1", "hello", 10, 25)
        assert not isinstance(exc_info.value, PreviewUnavailableError)

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_raises_not_found(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(404, b"not found"),
        )
        with pytest.raises(GdocError, match="Document not found"):
            insert_comment("doc1", "hello", 10, 25)

    @patch("gdoc.api.docs.get_docs_service")
    def test_401_raises_auth_error(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(401, b"unauthorized"),
        )
        with pytest.raises(AuthError):
            insert_comment("doc1", "hello", 10, 25)

    @patch("gdoc.api.docs.get_docs_service")
    def test_failed_update_state_refuses_fallback(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_response={
            "commentUpdateState": "ALL_FAILED_UNKNOWN_REASON",
            "replies": [{}],
        })
        with pytest.raises(GdocError, match="No fallback") as exc:
            insert_comment("doc1", "hello", 10, 25)
        assert not isinstance(exc.value, PreviewUnavailableError)

    @patch("gdoc.api.docs.get_docs_service")
    def test_missing_thread_id_refuses_fallback(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_response={
            "commentUpdateState": "ALL_SAVED",
            "replies": [{"insertComment": {}}],
        })
        with pytest.raises(GdocError, match="No fallback") as exc:
            insert_comment("doc1", "hello", 10, 25)
        assert not isinstance(exc.value, PreviewUnavailableError)
        assert exc.value.exit_code == 1


def _tab(tab_id, text, start=1):
    """Build a Docs API tab dict with one paragraph of text."""
    return {
        "tabProperties": {"tabId": tab_id, "title": tab_id, "index": 0},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": start,
                                    "textRun": {"content": text},
                                }
                            ]
                        }
                    }
                ]
            }
        },
    }


_DOC_WITH_TABS = {
    "revisionId": "rev1",
    "tabs": [_tab("t1", "The quick brown fox\n")],
}


class TestTryAnchoredComment:
    @patch("gdoc.api.docs.insert_comment", return_value="c_anchor")
    @patch(
        "gdoc.api.docs.get_document_with_tabs", return_value=_DOC_WITH_TABS,
    )
    def test_anchors_to_unique_match(self, _get, mock_insert):
        result = _try_anchored_comment("doc1", "note", "quick brown")
        assert result.comment_id == "c_anchor"
        assert result.status == "anchored"
        mock_insert.assert_called_once_with(
            "doc1", "note", 5, 16, tab_id="t1", revision_id="rev1",
        )

    @patch("gdoc.api.docs.insert_comment", return_value="c_anchor")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_finds_quote_in_second_tab(self, mock_get, mock_insert):
        mock_get.return_value = {
            "revisionId": "rev2",
            "tabs": [
                _tab("t1", "Nothing relevant here\n"),
                _tab("t2", "The quick brown fox\n"),
            ],
        }
        result = _try_anchored_comment("doc1", "note", "quick brown")
        assert result.comment_id == "c_anchor"
        assert result.status == "anchored"
        mock_insert.assert_called_once_with(
            "doc1", "note", 5, 16, tab_id="t2", revision_id="rev2",
        )

    @patch("gdoc.api.docs.insert_comment")
    @patch(
        "gdoc.api.docs.get_document_with_tabs", return_value=_DOC_WITH_TABS,
    )
    def test_quote_not_found_is_explicit(self, _get, mock_insert):
        result = _try_anchored_comment("doc1", "note", "missing text")
        assert result.status == "not_found"
        mock_insert.assert_not_called()

    @patch(
        "gdoc.api.docs.insert_comment",
        side_effect=PreviewUnavailableError("not enrolled"),
    )
    @patch(
        "gdoc.api.docs.get_document_with_tabs", return_value=_DOC_WITH_TABS,
    )
    def test_preview_unavailable_is_explicit(self, _get, _insert):
        assert (
            _try_anchored_comment("doc1", "note", "quick brown").status
            == "preview_unavailable"
        )

    @patch("gdoc.api.docs.insert_comment", return_value="c_anchor")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_smart_quote_fallback_match(self, mock_get, mock_insert):
        # Doc has a curly apostrophe; the quote arg has a straight one.
        mock_get.return_value = {
            "revisionId": "rev1",
            "tabs": [_tab("t1", "it’s fine\n")],
        }
        result = _try_anchored_comment("doc1", "note", "it's fine")
        assert result.comment_id == "c_anchor"
        assert result.status == "anchored"


class TestUtf16Offsets:
    def test_match_after_emoji_uses_utf16_indices(self):
        # 🚀 is one Python char but two UTF-16 code units — the Docs API
        # index space. "quick" starts at 1 + 2 (emoji) + 1 (space) = 4.
        body = {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "startIndex": 1,
                                "textRun": {"content": "🚀 quick\n"},
                            }
                        ]
                    }
                }
            ]
        }
        matches = find_text_in_document(None, "quick", body=body)
        assert matches == [{"startIndex": 4, "endIndex": 9}]

    def test_match_ending_in_emoji_widens_end_index(self):
        body = {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "startIndex": 1,
                                "textRun": {"content": "go 🚀 now\n"},
                            }
                        ]
                    }
                }
            ]
        }
        matches = find_text_in_document(None, "go 🚀", body=body)
        # 'g'=1 'o'=2 ' '=3, emoji occupies 4–5 → end index 6.
        assert matches == [{"startIndex": 1, "endIndex": 6}]


def _make_args(**overrides):
    defaults = {
        "command": "comment",
        "doc": "abc123",
        "text": "hello",
        "quote": None,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_MOCK_VERSION = {"version": 50}
_ANCHORED = CommentAnchorResult(
    "anchored", "c_anchor", [{"tabId": "t1", "tabTitle": "Notes"}],
)


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
class TestCmdCommentAnchored:
    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.cli._try_anchored_comment", return_value=_ANCHORED)
    def test_anchored_success_skips_drive_path(
        self, mock_try, mock_create, _ver, _pf, _update, capsys,
    ):
        rc = cmd_comment(_make_args(quote="quick brown"))
        assert rc == 0
        mock_try.assert_called_once_with(
            "abc123", "hello", "quick brown", tab_name=None, occurrence=None,
        )
        mock_create.assert_not_called()
        out = capsys.readouterr().out
        assert "OK comment #c_anchor (anchored)" in out

    @patch(
        "gdoc.api.comments.create_comment", return_value={"id": "c_new"},
    )
    @patch(
        "gdoc.cli._try_anchored_comment",
        return_value=CommentAnchorResult("preview_unavailable"),
    )
    def test_fallback_uses_drive_quote_path(
        self, mock_try, mock_create, _ver, _pf, _update, capsys,
    ):
        rc = cmd_comment(_make_args(quote="quick brown"))
        assert rc == 0
        mock_create.assert_called_once_with(
            "abc123", "hello", quote="quick brown",
        )
        out = capsys.readouterr().out
        assert "OK comment #c_new" in out
        assert "(unanchored)" in out

    @patch(
        "gdoc.api.comments.create_comment", return_value={"id": "c_new"},
    )
    @patch("gdoc.cli._try_anchored_comment")
    def test_no_quote_skips_anchored_path(
        self, mock_try, mock_create, _ver, _pf, _update, capsys,
    ):
        rc = cmd_comment(_make_args())
        assert rc == 0
        mock_try.assert_not_called()
        mock_create.assert_called_once_with("abc123", "hello", quote="")
        out = capsys.readouterr().out
        assert "OK comment #c_new" in out
        assert "anchored" not in out

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.cli._try_anchored_comment", return_value=_ANCHORED)
    def test_json_output_anchored_true(
        self, _try, _create, _ver, _pf, _update, capsys,
    ):
        cmd_comment(_make_args(quote="quick brown", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "c_anchor"
        assert data["anchored"] is True

    @patch(
        "gdoc.api.comments.create_comment", return_value={"id": "c_new"},
    )
    @patch(
        "gdoc.cli._try_anchored_comment",
        return_value=CommentAnchorResult("preview_unavailable"),
    )
    def test_json_output_anchored_false_on_fallback(
        self, _try, _create, _ver, _pf, _update, capsys,
    ):
        cmd_comment(_make_args(quote="quick brown", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "c_new"
        assert data["anchored"] is False

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.cli._try_anchored_comment", return_value=_ANCHORED)
    def test_state_patch_tracks_anchored_id(
        self, _try, _create, _ver, _pf, mock_update, capsys,
    ):
        cmd_comment(_make_args(quote="quick brown"))
        patch_arg = mock_update.call_args.kwargs["comment_state_patch"]
        assert patch_arg == {"add_comment_id": "c_anchor"}


@pytest.fixture
def comment_command(mocker):
    """Keep CLI tests offline while exercising the real resolver/API wrapper."""
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch("gdoc.state.update_state_after_command")
    mocker.patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    read = mocker.patch(
        "gdoc.api.docs.get_document_with_tabs", return_value=_DOC_WITH_TABS,
    )
    service = _mock_docs_service(batch_response=_OK_RESPONSE)
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)
    drive_create = mocker.patch(
        "gdoc.api.comments.create_comment", return_value={"id": "c_fallback"},
    )
    return read, service.documents.return_value.batchUpdate, drive_create


@pytest.mark.parametrize("spaces", ["\u00a0", "\u202f", "\u2009", "\u2007"])
@pytest.mark.parametrize("space_in_quote", [True, False])
def test_unicode_spaces_fold_on_both_sides(comment_command, spaces, space_in_quote):
    read, batch, fallback = comment_command
    doc_text = "a b" if space_in_quote else f"a{spaces}b"
    quote = f"a{spaces}b" if space_in_quote else "a b"
    read.return_value = {"revisionId": "rev1", "tabs": [_tab("t1", doc_text)]}

    assert cmd_comment(_make_args(quote=quote)) == 0

    range_ = batch.call_args.kwargs["body"]["requests"][0]["insertComment"]["range"]
    assert range_ == {"startIndex": 1, "endIndex": 4, "tabId": "t1"}
    fallback.assert_not_called()


@pytest.mark.parametrize("tabs", [
    [_tab("t1", "echo echo\n")],
    [_tab("t1", "echo\n"), _tab("t2", "echo\n")],
    [_tab("t1", "it's here\n"), _tab("t2", "it\u2019s here\n")],
    [_tab("t1", "it's here it\u2019s here\n")],
])
def test_all_equivalent_matches_count_before_writing(comment_command, tabs):
    read, batch, fallback = comment_command
    read.return_value = {"revisionId": "rev1", "tabs": tabs}
    quote = "echo" if "echo" in str(tabs) else "it's here"

    with pytest.raises(GdocError, match="ambiguous: 2 matches") as exc:
        cmd_comment(_make_args(quote=quote))

    assert exc.value.exit_code == 3
    assert "tab 't1' (t1), segment body, range 1:" in str(exc.value)
    assert "longer quote" in str(exc.value)
    assert "--tab" in str(exc.value)
    assert "--occurrence N" in str(exc.value)
    batch.assert_not_called()
    fallback.assert_not_called()


@pytest.mark.parametrize("tabs,occurrence,expected", [
    ([_tab("t1", "echo echo\n")], 1, {"startIndex": 1, "endIndex": 5, "tabId": "t1"}),
    ([_tab("t1", "echo echo\n")], 2, {"startIndex": 6, "endIndex": 10, "tabId": "t1"}),
    ([_tab("t1", "echo\n"), _tab("t2", "echo\n")], 2,
     {"startIndex": 1, "endIndex": 5, "tabId": "t2"}),
])
def test_occurrence_selects_nth_match_in_document_order(
    comment_command, capsys, tabs, occurrence, expected,
):
    read, batch, fallback = comment_command
    read.return_value = {"revisionId": "rev1", "tabs": tabs}

    assert cmd_comment(_make_args(quote="echo", occurrence=occurrence, json=True)) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["anchored"] is True
    assert result["tabId"] == expected["tabId"]
    range_ = batch.call_args.kwargs["body"]["requests"][0]["insertComment"]["range"]
    assert range_ == expected
    fallback.assert_not_called()


def test_occurrence_survives_conflict_reresolution(comment_command):
    read, batch, fallback = comment_command
    read.side_effect = [
        {"revisionId": "rev1", "tabs": [_tab("t1", "echo echo\n")]},
        {"revisionId": "rev2", "tabs": [_tab("t1", "x echo echo\n")]},
    ]
    batch.return_value.execute.side_effect = [_revision_error(), _OK_RESPONSE]

    assert cmd_comment(_make_args(quote="echo", occurrence=2)) == 0

    ranges = [
        call.kwargs["body"]["requests"][0]["insertComment"]["range"]
        for call in batch.call_args_list
    ]
    assert ranges == [
        {"startIndex": 6, "endIndex": 10, "tabId": "t1"},
        {"startIndex": 8, "endIndex": 12, "tabId": "t1"},
    ]
    fallback.assert_not_called()


def test_occurrence_numbers_segments_by_id_across_reads(comment_command):
    read, batch, fallback = comment_command

    def doc(revision, order):
        tab = _tab("t1", "none\n")
        header = _tab("unused", "echo\n", start=0)["documentTab"]["body"]
        tab["documentTab"]["headers"] = {key: header for key in order}
        return {"revisionId": revision, "tabs": [tab]}

    read.side_effect = [doc("rev1", ["h2", "h1"]), doc("rev2", ["h1", "h2"])]
    batch.return_value.execute.side_effect = [_revision_error(), _OK_RESPONSE]

    assert cmd_comment(_make_args(quote="echo", occurrence=2)) == 0

    segments = [
        call.kwargs["body"]["requests"][0]["insertComment"]["range"]["segmentId"]
        for call in batch.call_args_list
    ]
    assert segments == ["h2", "h2"]
    fallback.assert_not_called()


def test_selector_usage_error_precedes_pre_flight(mocker):
    pre_flight = mocker.patch("gdoc.notify.pre_flight")
    read = mocker.patch("gdoc.api.docs.get_document_with_tabs")
    with pytest.raises(GdocError, match="require --quote") as exc:
        cmd_comment(_make_args(quote=None, tab="Notes", quiet=False))
    assert exc.value.exit_code == 3
    pre_flight.assert_not_called()
    read.assert_not_called()


@pytest.mark.parametrize("occurrence", [3])
def test_occurrence_out_of_range_refuses_without_writing(comment_command, occurrence):
    read, batch, fallback = comment_command
    read.return_value = {"revisionId": "rev1", "tabs": [_tab("t1", "echo echo\n")]}

    with pytest.raises(GdocError, match="out of range: the quote matches 2") as exc:
        cmd_comment(_make_args(quote="echo", occurrence=occurrence))

    assert exc.value.exit_code == 3
    assert "no comment created" in str(exc.value)
    batch.assert_not_called()
    fallback.assert_not_called()


def test_explicit_tab_limits_search_and_is_reported(comment_command, capsys):
    read, batch, fallback = comment_command
    first, second = _tab("t1", "echo\n"), _tab("t2", "echo\n")
    second["tabProperties"]["title"] = "Review"
    read.return_value = {"revisionId": "rev1", "tabs": [first, second]}

    assert cmd_comment(_make_args(quote="echo", tab="Review", json=True)) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": True, "id": "c_anchor", "status": "created",
        "anchored": True, "tabId": "t2",
    }
    range_ = batch.call_args.kwargs["body"]["requests"][0]["insertComment"]["range"]
    assert range_["tabId"] == "t2"
    fallback.assert_not_called()


@pytest.mark.parametrize("requested_tab", ["t1", "Missing tab"])
def test_tab_miss_does_not_search_elsewhere_or_fallback(comment_command, requested_tab):
    read, batch, fallback = comment_command
    read.return_value = {
        "revisionId": "rev1",
        "tabs": [_tab("t1", "other\n"), _tab("t2", "echo\n")],
    }
    with pytest.raises(GdocError) as exc:
        cmd_comment(_make_args(quote="echo", tab=requested_tab))
    assert exc.value.exit_code == 3
    batch.assert_not_called()
    fallback.assert_not_called()


def test_omitted_tab_searches_nested_tabs(comment_command, capsys):
    read, batch, fallback = comment_command
    tab = _tab("t1", "other\n")
    tab["childTabs"] = [_tab("child", "echo\n")]
    read.return_value = {"revisionId": "rev1", "tabs": [tab]}
    assert cmd_comment(_make_args(quote="echo")) == 0
    assert "Tab: child (child)" in capsys.readouterr().out
    assert batch.call_args.kwargs["body"]["requests"][0]["insertComment"][
        "range"
    ]["tabId"] == "child"
    fallback.assert_not_called()


@pytest.mark.parametrize("kind", ["headers", "footers", "footnotes"])
def test_segment_quote_keeps_segment_id_and_zero_start(comment_command, kind):
    read, batch, fallback = comment_command
    tab = _tab("t1", "other\n")
    segment = _tab("unused", "echo\n", start=0)["documentTab"]["body"]
    del segment["content"][0]["paragraph"]["elements"][0]["startIndex"]
    tab["documentTab"][kind] = {"segment1": segment}
    read.return_value = {"revisionId": "rev1", "tabs": [tab]}
    assert cmd_comment(_make_args(quote="echo")) == 0
    assert batch.call_args.kwargs["body"]["requests"][0]["insertComment"][
        "range"
    ] == {"startIndex": 0, "endIndex": 4, "tabId": "t1", "segmentId": "segment1"}
    fallback.assert_not_called()


def test_body_and_header_matches_are_ambiguous(comment_command):
    read, batch, fallback = comment_command
    tab = _tab("t1", "echo\n")
    tab["documentTab"]["headers"] = {
        "header1": _tab("unused", "echo\n", start=0)["documentTab"]["body"],
    }
    read.return_value = {"revisionId": "rev1", "tabs": [tab]}
    with pytest.raises(GdocError, match="ambiguous: 2 matches") as exc:
        cmd_comment(_make_args(quote="echo"))
    assert "segment header1, range 0:4" in str(exc.value)
    batch.assert_not_called()
    fallback.assert_not_called()


def test_legacy_document_without_tabs_can_anchor(comment_command):
    read, batch, fallback = comment_command
    read.return_value = {
        "revisionId": "rev1", "body": _tab("unused", "echo\n")["documentTab"]["body"],
    }
    assert cmd_comment(_make_args(quote="echo")) == 0
    assert batch.call_args.kwargs["body"]["requests"][0]["insertComment"][
        "range"
    ] == {"startIndex": 1, "endIndex": 5}
    fallback.assert_not_called()


def test_missing_revision_refuses_an_unpinned_write(comment_command):
    read, batch, fallback = comment_command
    read.return_value = {"tabs": [_tab("t1", "echo\n")]}
    with pytest.raises(GdocError, match="No revision ID") as exc:
        cmd_comment(_make_args(quote="echo"))
    assert exc.value.exit_code == 3
    batch.assert_not_called()
    fallback.assert_not_called()


def _revision_error():
    return _http_error(400, json.dumps({
        "error": {
            "message": "The provided revision ID does not match the latest revision",
        },
    }).encode())


def test_conflict_rereads_once_and_uses_fresh_coordinates(comment_command):
    read, batch, fallback = comment_command
    read.side_effect = [
        {"revisionId": "rev1", "tabs": [_tab("t1", "echo\n")]},
        {"revisionId": "rev2", "tabs": [_tab("t1", "new echo\n")]},
    ]
    batch.return_value.execute.side_effect = [_revision_error(), _OK_RESPONSE]
    assert cmd_comment(_make_args(quote="echo")) == 0
    assert read.call_count == batch.call_count == 2
    second = batch.call_args_list[1].kwargs["body"]
    assert second["writeControl"] == {"requiredRevisionId": "rev2"}
    assert second["requests"][0]["insertComment"]["range"] == {
        "startIndex": 5, "endIndex": 9, "tabId": "t1",
    }
    fallback.assert_not_called()


def test_second_conflict_refuses_without_fallback(comment_command):
    read, batch, fallback = comment_command
    batch.return_value.execute.side_effect = _revision_error()
    with pytest.raises(GdocError, match="Document changed") as exc:
        cmd_comment(_make_args(quote="quick brown"))
    assert exc.value.exit_code == 3
    assert read.call_count == batch.call_count == 2
    fallback.assert_not_called()


@pytest.mark.parametrize("new_text, message", [
    ("echo echo\n", "ambiguous: 2 matches"),
    ("deleted\n", "Quote not found after Unicode normalization"),
])
def test_conflict_reresolves_instead_of_replaying(comment_command, new_text, message):
    read, batch, fallback = comment_command
    read.side_effect = [
        {"revisionId": "rev1", "tabs": [_tab("t1", "echo\n")]},
        {"revisionId": "rev2", "tabs": [_tab("t1", new_text)]},
    ]
    batch.return_value.execute.side_effect = _revision_error()
    with pytest.raises(GdocError, match=message) as exc:
        cmd_comment(_make_args(quote="echo"))
    assert exc.value.exit_code == 3
    assert read.call_count == 2
    batch.assert_called_once()
    fallback.assert_not_called()


@pytest.mark.parametrize("response", [
    {"commentUpdateState": "ALL_SAVED", "replies": [{}]},
    {"commentUpdateState": "SOME_SAVED", "replies": _OK_RESPONSE["replies"]},
    {"commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"},
    {"commentUpdateState": "NO_UPDATES_REQUESTED", "replies": _OK_RESPONSE["replies"]},
    {"replies": _OK_RESPONSE["replies"]},
    {},
])
def test_uncertain_success_response_never_creates_second_comment(
    comment_command, response,
):
    read, batch, fallback = comment_command
    batch.return_value.execute.return_value = response
    with pytest.raises(GdocError, match="No fallback comment was created"):
        cmd_comment(_make_args(quote="quick brown"))
    read.assert_called_once()
    batch.assert_called_once()
    fallback.assert_not_called()


@pytest.mark.parametrize("error", [
    TimeoutError("response lost after save"),
    ConnectionResetError("connection lost after save"),
    IncompleteRead(b"partial response"),
    httplib2.HttpLib2Error("transport failed after save"),
    _http_error(503, b"temporarily unavailable"),
])
def test_uncertain_write_never_retries_or_falls_back(comment_command, error):
    from googleapiclient.http import HttpRequest

    read, batch, fallback = comment_command
    saved_comments = []

    def save_then_lose_response(**kwargs):
        kwargs["http"].http._sent = True
        saved_comments.append("c_saved")
        raise error

    # #70's shared fixture bypasses transport for API-shape mocks; this case
    # needs the real error handler around the simulated request execution.
    request = MagicMock(spec=HttpRequest)
    request.http = MagicMock()
    request.execute.side_effect = save_then_lose_response
    batch.return_value = request
    with pytest.raises(GdocError, match="outcome is uncertain"):
        cmd_comment(_make_args(quote="quick brown"))
    assert saved_comments == ["c_saved"]
    read.assert_called_once()
    batch.assert_called_once()
    fallback.assert_not_called()


@pytest.mark.parametrize("message", [
    "batchUpdate quota exceeded",
    "batchUpdate API disabled for this project",
    "Rate limit exceeded",
])
def test_unstructured_403_without_permission_wording_never_falls_back(
    comment_command, message,
):
    read, batch, fallback = comment_command
    batch.return_value.execute.side_effect = _http_error(
        403, json.dumps({"error": {"message": message}}).encode(),
    )
    with pytest.raises(GdocError, match="no fallback comment was created") as exc:
        cmd_comment(_make_args(quote="quick brown"))
    assert exc.value.exit_code == 1
    assert message in str(exc.value)
    batch.assert_called_once()
    fallback.assert_not_called()


@pytest.mark.parametrize("content", [
    b"Quota exceeded",
    b"Google Docs API is disabled for this project",
    b"",
    b'{"error":',
])
def test_transport_forbidden_reason_never_creates_fallback(
    comment_command, capsys, content,
):
    read, batch, fallback = comment_command
    resp = httplib2.Response({"status": "403"})
    resp.reason = "Forbidden"
    error = HttpError(resp, content, uri="")
    assert error.reason == "Forbidden"
    batch.return_value.execute.side_effect = error
    with pytest.raises(GdocError, match="no fallback comment was created") as exc:
        cmd_comment(_make_args(quote="quick brown", json=True))
    assert exc.value.exit_code == 1
    read.assert_called_once()
    batch.assert_called_once()
    batch.return_value.execute.assert_called_once()
    fallback.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("selector", [
    {"tab": "Notes"}, {"occurrence": 1}, {"tab": "Notes", "occurrence": 2},
])
def test_selectors_without_quote_are_usage_errors_before_any_write(
    comment_command, selector,
):
    read, batch, fallback = comment_command
    with pytest.raises(GdocError, match="require --quote") as exc:
        cmd_comment(_make_args(quote=None, **selector))
    assert exc.value.exit_code == 3
    for flag in selector:
        assert f"--{flag}" in str(exc.value)
    read.assert_not_called()
    batch.assert_not_called()
    fallback.assert_not_called()


@pytest.mark.parametrize("payload", _NON_PERMISSION_403S)
def test_quota_or_disabled_api_403_never_creates_fallback(
    comment_command, capsys, payload,
):
    read, batch, fallback = comment_command
    batch.return_value.execute.side_effect = _http_error(
        403, json.dumps(payload).encode(),
    )
    with pytest.raises(GdocError, match="no fallback comment was created") as exc:
        cmd_comment(_make_args(quote="quick brown", json=True))
    assert exc.value.exit_code == 1
    read.assert_called_once()
    batch.assert_called_once()
    fallback.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("status, detail", [
    (400, "Invalid requests[0]: No request set."),
    (400, 'Unknown name "insertComment": Cannot find field.'),
    (403, "The caller does not have permission to batchUpdate this document"),
])
@pytest.mark.parametrize("mode", ["terse", "plain", "json"])
def test_definite_preview_rejection_creates_one_honest_fallback(
    comment_command, capsys, status, detail, mode,
):
    read, batch, fallback = comment_command
    batch.return_value.execute.side_effect = _http_error(
        status, json.dumps({"error": {"message": detail}}).encode(),
    )
    assert cmd_comment(_make_args(
        quote="quick brown", json=mode == "json", plain=mode == "plain",
    )) == 0
    read.assert_called_once()
    batch.assert_called_once()
    fallback.assert_called_once_with("abc123", "hello", quote="quick brown")
    captured = capsys.readouterr()
    assert "unanchored" in captured.err
    output = captured.out
    if mode == "json":
        assert json.loads(output)["anchored"] is False
        assert json.loads(output)["reason"] == "preview_unavailable"
    elif mode == "plain":
        assert "anchored\tfalse" in output
        assert "reason\tpreview_unavailable" in output
    else:
        assert "(unanchored)" in output


@pytest.mark.parametrize("status, detail", [
    (400, 'Unknown name "unrelatedField": Cannot find field.'),
    (400, "Invalid range"),
    (401, "Authentication expired"),
    (404, "Document not found"),
    (429, "Quota exceeded"),
])
def test_other_api_rejections_do_not_fallback(comment_command, status, detail):
    _read, batch, fallback = comment_command
    batch.return_value.execute.side_effect = _http_error(
        status, json.dumps({"error": {"message": detail}}).encode(),
    )
    with pytest.raises(GdocError):
        cmd_comment(_make_args(quote="quick brown"))
    batch.assert_called_once()
    fallback.assert_not_called()


def test_comment_parser_accepts_tab_scope():
    from gdoc.cli import build_parser

    args = build_parser().parse_args([
        "comment", "doc1", "note", "--quote", "echo", "--tab", "Review",
    ])
    assert args.tab == "Review"


@pytest.mark.parametrize("text,quote,start,end", [
    ("İstanbul plan\n", "stanbul plan", 2, 14),
])
def test_comment_reuses_case_expansion_offsets(
    comment_command, text, quote, start, end,
):
    read, batch, _fallback = comment_command
    read.return_value = {"revisionId": "r1", "tabs": [_tab("t1", text)]}
    assert cmd_comment(_make_args(quote=quote)) == 0
    body = batch.call_args.kwargs["body"]
    assert body["requests"][0]["insertComment"]["range"] == {
        "startIndex": start, "endIndex": end, "tabId": "t1",
    }


def test_comment_tab_id_wins_over_title(comment_command):
    read, batch, _fallback = comment_command
    decoy = _tab("decoy", "echo\n")
    target = _tab("target", "echo\n")
    decoy["tabProperties"]["title"] = "target"
    target["tabProperties"]["title"] = "Actual"
    read.return_value = {"revisionId": "r1", "tabs": [decoy, target]}
    assert cmd_comment(_make_args(quote="echo", tab="target")) == 0
    assert batch.call_args.kwargs["body"]["requests"][0]["insertComment"][
        "range"
    ]["tabId"] == "target"


def test_comment_duplicate_tab_titles_require_id(comment_command):
    read, batch, fallback = comment_command
    tabs = [_tab("t1", "echo\n"), _tab("t2", "echo\n")]
    for tab in tabs:
        tab["tabProperties"]["title"] = "Notes"
    read.return_value = {"revisionId": "r1", "tabs": tabs}
    with pytest.raises(GdocError) as exc:
        cmd_comment(_make_args(quote="echo", tab="Notes"))
    assert exc.value.exit_code == 3
    assert "t1" in str(exc.value) and "t2" in str(exc.value)
    batch.assert_not_called()
    fallback.assert_not_called()


def test_comment_can_span_inline_native_gap(comment_command):
    read, batch, _fallback = comment_command
    tab = _tab("t1", "before", start=1)
    elements = tab["documentTab"]["body"]["content"][0]["paragraph"]["elements"]
    elements.extend([
        {"startIndex": 7, "endIndex": 8,
         "inlineObjectElement": {"inlineObjectId": "img"}},
        {"startIndex": 8, "textRun": {"content": "after\n"}},
    ])
    read.return_value = {"revisionId": "r1", "tabs": [tab]}
    assert cmd_comment(_make_args(quote="beforeafter")) == 0
    assert batch.call_args.kwargs["body"]["requests"][0]["insertComment"][
        "range"
    ] == {"startIndex": 1, "endIndex": 13, "tabId": "t1"}


@pytest.mark.parametrize("kind,code", [
    ("ambiguous", 3), ("not_found", 3), ("conflict", 3),
    ("uncertain", 1), ("auth", 2),
])
def test_public_comment_exit_codes(comment_command, capsys, mocker, kind, code):
    from gdoc.cli import run_argv

    result = CommentAnchorResult(kind, detail="refused") if code == 3 else None
    error = AuthError("expired") if kind == "auth" else GdocError("uncertain")
    mocker.patch(
        "gdoc.cli._try_anchored_comment", return_value=result,
        side_effect=error if code != 3 else None,
    )
    assert run_argv(
        ["comment", "doc1", "note", "--quote", "echo", "--json", "--quiet"],
        check_updates=False,
    ) == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERR:")


@pytest.mark.parametrize("occurrence", [0, -1])
def test_nonpositive_occurrence_fails_before_preflight(mocker, occurrence):
    preflight = mocker.patch("gdoc.notify.pre_flight")
    read = mocker.patch("gdoc.api.docs.get_document_with_tabs")
    with pytest.raises(GdocError, match="must be positive") as exc:
        cmd_comment(_make_args(quote="echo", occurrence=occurrence, quiet=False))
    assert exc.value.exit_code == 3
    preflight.assert_not_called()
    read.assert_not_called()
