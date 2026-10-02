"""Tests for suggestion threads: list/info and accept/reject/delete.

The Docs API developer preview exposes native suggestion threads through
documents.get?commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED and decides them
with acceptSuggestion/rejectSuggestion/deleteSuggestion. These tests pin the
exact request shapes, the response shapes observed live (see PR notes),
the derived location map, and the CLI's refusal to report success without
a read-back in the requested state.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc import mcp
from gdoc.api.docs import (
    collect_suggestion_locations,
    decide_suggestion,
    find_suggestion_thread,
    get_document_threads,
    sorted_suggestion_threads,
    summarize_suggestion_thread,
)
from gdoc.cli import (
    cmd_suggestion,
    cmd_suggestions,
)
from gdoc.notify import ChangeInfo
from gdoc.util import SPREADSHEET_MIME, AuthError, GdocError, PreviewUnavailableError


def _http_error(status, content=b"", reason="Error"):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = reason
    return HttpError(resp, content, uri="")


def _thread(
    sid,
    status="OPEN",
    created="2026-08-26T17:23:54.236Z",
    summary="Add: “(added A)”",
    me=True,
    name="Alejo Acelas",
):
    """SuggestionThread as observed live (no range fields)."""
    return {
        "suggestionId": sid,
        "headPost": {
            "postId": "AAACFyBdz8Y",
            "author": {"displayName": name, "me": me, "user": "users/1"},
            "createTime": created,
            "updateTime": created,
            "suggestionAction": "NO_SUGGESTION_ACTION_CHANGE",
        },
        "status": status,
        "summaryText": summary,
        "summaryHtml": "<div>...</div>",
    }


def _text_run(start, end, content, **marks):
    run = {"content": content, "textStyle": {}}
    run.update(marks)
    return {"startIndex": start, "endIndex": end, "textRun": run}


def _tab(tab_id, title, elements, children=None):
    tab = {
        "tabProperties": {"tabId": tab_id, "title": title, "index": 0},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "startIndex": elements[0]["startIndex"],
                        "endIndex": elements[-1]["endIndex"],
                        "paragraph": {"elements": elements},
                    }
                ]
            }
        },
    }
    if children:
        tab["childTabs"] = children
    return tab


# Suggestion A: insertion (+ style map Google adds alongside), tab 1.
# Suggestion B: deletion, tab 1. Suggestion H: insertion in a child tab.
_DOC = {
    "documentId": "doc1",
    "title": "Scratch",
    "revisionId": "rev1",
    "suggestionsViewMode": "SUGGESTIONS_INLINE",
    "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
    "tabs": [
        _tab(
            "t.0",
            "Tab 1",
            [
                _text_run(1, 27, "Intro line \U0001f680 target alpha"),
                _text_run(
                    27,
                    37,
                    " (added A)",
                    suggestedInsertionIds=["suggest.a"],
                    suggestedTextStyleChanges={"suggest.a": {"textStyle": {}}},
                ),
                _text_run(37, 73, " stays here.\nSecond paragraph with "),
                _text_run(
                    73,
                    92,
                    "beta text to delete",
                    suggestedDeletionIds=["suggest.b"],
                ),
            ],
            children=[
                _tab(
                    "t.draft",
                    "Draft",
                    [
                        _text_run(1, 18, "Draft tab epsilon"),
                        _text_run(
                            18,
                            28,
                            " (added H)",
                            suggestedInsertionIds=["suggest.h"],
                        ),
                    ],
                ),
            ],
        ),
    ],
    "suggestions": [
        _thread(
            "suggest.h", created="2026-08-26T17:23:57.504Z", summary="Add: “(added H)”"
        ),
        _thread("suggest.a"),
        _thread(
            "suggest.b",
            created="2026-08-26T17:23:55.448Z",
            summary="Delete: “beta text to delete”",
            me=False,
            name="Alejandro Acelas",
        ),
        _thread(
            "suggest.done",
            status="ACCEPTED",
            created="2026-08-26T17:20:00.000Z",
            summary="Add: old",
        ),
    ],
    "comments": [],
}


# --- get_document_threads ---------------------------------------------------


class TestGetDocumentThreads:
    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_sends_preview_view_modes(self, mock_svc, mock_raw):
        mock_raw.return_value = dict(_DOC)
        doc = get_document_threads("doc1")
        assert doc["suggestions"][1]["suggestionId"] == "suggest.a"
        service, doc_id, params = mock_raw.call_args.args
        assert service is mock_svc.return_value
        assert doc_id == "doc1"
        assert params == {
            "includeTabsContent": "true",
            "suggestionsViewMode": "SUGGESTIONS_INLINE",
            "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        }

    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_missing_suggestions_key_becomes_empty_list(self, _svc, mock_raw):
        mock_raw.return_value = {
            "documentId": "doc1",
            "revisionId": "r",
            "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        }
        doc = get_document_threads("doc1")
        assert doc["suggestions"] == []
        assert doc["comments"] == []

    @pytest.mark.parametrize("mode", [None, "", "DEFAULT_FOR_CURRENT_ACCESS"])
    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_200_without_preview_view_is_unavailable_not_empty(
        self, _svc, mock_raw, mode
    ):
        # An empty thread list is only trustworthy when the server echoes
        # the preview view mode; otherwise "No suggestions." would be a lie.
        doc = {"documentId": "doc1", "revisionId": "r"}
        if mode is not None:
            doc["commentsViewMode"] = mode
        mock_raw.return_value = doc
        with pytest.raises(GdocError, match="not enrolled") as e:
            get_document_threads("doc1")
        assert type(e.value) is GdocError

    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_unknown_field_400_is_preview_unavailable(self, _svc, mock_raw):
        # Live shape from project 856825977485 (not enrolled).
        mock_raw.side_effect = _http_error(
            400,
            b'{"error": {"code": 400, "message": "Invalid JSON payload '
            b'received. Unknown name \\"comments_view_mode\\": Cannot bind '
            b'query parameter.", "status": "INVALID_ARGUMENT"}}',
        )
        with pytest.raises(GdocError, match="not enrolled") as e:
            get_document_threads("doc1")
        # Plain GdocError, not PreviewUnavailableError: nothing may treat
        # a missing thread view as a cue to fall back to Drive.
        assert type(e.value) is GdocError

    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_other_400_is_generic_api_error(self, _svc, mock_raw):
        mock_raw.side_effect = _http_error(400, b'{"error": {"message": "x"}}')
        with pytest.raises(GdocError, match="API error \\(400\\)"):
            get_document_threads("doc1")

    @pytest.mark.parametrize(
        "status,exc,msg",
        [
            (401, AuthError, "Authentication expired"),
            (403, GdocError, "Permission denied: doc1"),
            (404, GdocError, "Document not found: doc1"),
        ],
    )
    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_standard_errors_translate(self, _svc, mock_raw, status, exc, msg):
        mock_raw.side_effect = _http_error(status)
        with pytest.raises(exc, match=msg):
            get_document_threads("doc1")


class TestDocumentsGetRaw:
    def test_builds_authorized_get_with_query(self):
        from gdoc.api.docs import _documents_get_raw

        service = MagicMock()
        with patch("googleapiclient.http.HttpRequest") as mock_req:
            mock_req.return_value.execute.return_value = {"documentId": "d"}
            out = _documents_get_raw(
                service,
                "doc/1",
                {"commentsViewMode": "X", "a": "b"},
            )
        assert out == {"documentId": "d"}
        args, kwargs = mock_req.call_args
        assert args[0] is service._http
        assert args[2] == (
            "https://docs.googleapis.com/v1/documents/doc%2F1?commentsViewMode=X&a=b"
        )
        assert kwargs["method"] == "GET"


# --- derived locations / summaries ----------------------------------------


def _single_tab_doc(body, **document_tab_extras):
    """One tab "Main"/t.0 with the given body content and extra
    documentTab keys (headers, footers, footnotes, inlineObjects, ...)."""
    return {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0", "title": "Main"},
                "documentTab": {"body": {"content": body}, **document_tab_extras},
            }
        ]
    }


def _loc(kind, start, end, segment="", text=""):
    return {
        "tab": "Main",
        "tabId": "t.0",
        "segmentId": segment,
        "kind": kind,
        "startIndex": start,
        "endIndex": end,
        "text": text,
    }


class TestCollectSuggestionLocations:
    def test_insert_delete_style_and_child_tab(self):
        locs = collect_suggestion_locations(_DOC)
        assert locs["suggest.a"] == [
            {
                "tab": "Tab 1",
                "tabId": "t.0",
                "segmentId": "",
                "kind": "insert",
                "startIndex": 27,
                "endIndex": 37,
                "text": " (added A)",
            },
        ]  # the style map mirroring the insertion is not a second location
        assert locs["suggest.b"] == [
            {
                "tab": "Tab 1",
                "tabId": "t.0",
                "segmentId": "",
                "kind": "delete",
                "startIndex": 73,
                "endIndex": 92,
                "text": "beta text to delete",
            },
        ]
        assert locs["suggest.h"] == [
            {
                "tab": "Draft",
                "tabId": "t.draft",
                "segmentId": "",
                "kind": "insert",
                "startIndex": 18,
                "endIndex": 28,
                "text": " (added H)",
            },
        ]
        # An accepted thread has no inline marks left.
        assert "suggest.done" not in locs

    def test_paragraph_style_change_uses_enclosing_paragraph_range(self):
        doc = {
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 1,
                                    "endIndex": 10,
                                    "paragraph": {
                                        "elements": [_text_run(1, 10, "Heading\n")],
                                        "paragraphStyle": {
                                            "namedStyleType": "NORMAL_TEXT"
                                        },
                                        "suggestedParagraphStyleChanges": {
                                            "suggest.p": {"paragraphStyle": {}},
                                        },
                                    },
                                }
                            ]
                        }
                    },
                }
            ],
        }
        assert collect_suggestion_locations(doc) == {
            "suggest.p": [
                {
                    "tab": "Main",
                    "tabId": "t.0",
                    "segmentId": "",
                    "kind": "paragraph-style",
                    "startIndex": 1,
                    "endIndex": 10,
                    "text": "",
                }
            ],
        }

    def test_table_cell_content_is_walked(self):
        doc = {
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 1,
                                    "endIndex": 20,
                                    "table": {
                                        "tableRows": [
                                            {
                                                "tableCells": [
                                                    {
                                                        "content": [
                                                            {
                                                                "startIndex": 2,
                                                                "endIndex": 8,
                                                                "paragraph": {
                                                                    "elements": [
                                                                        _text_run(
                                                                            2,
                                                                            8,
                                                                            "cell\n",
                                                                            suggestedInsertionIds=[
                                                                                "suggest.t"
                                                                            ],
                                                                        )
                                                                    ]
                                                                },
                                                            }
                                                        ],
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    },
                }
            ],
        }
        locs = collect_suggestion_locations(doc)
        assert locs["suggest.t"][0]["startIndex"] == 2
        assert locs["suggest.t"][0]["kind"] == "insert"

    def test_structural_insertion_without_text_run(self):
        doc = {
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 5,
                                    "endIndex": 30,
                                    "suggestedInsertionIds": ["suggest.tbl"],
                                    "table": {"rows": 1, "columns": 1, "tableRows": []},
                                }
                            ]
                        }
                    },
                }
            ],
        }
        assert collect_suggestion_locations(doc)["suggest.tbl"] == [
            {
                "tab": "Main",
                "tabId": "t.0",
                "segmentId": "",
                "kind": "insert",
                "startIndex": 5,
                "endIndex": 30,
                "text": "",
            }
        ]

    def test_pure_style_change_is_reported(self):
        doc = _single_tab_doc(
            body=[
                {
                    "startIndex": 1,
                    "endIndex": 6,
                    "paragraph": {
                        "elements": [
                            _text_run(
                                1,
                                6,
                                "gamma",
                                suggestedTextStyleChanges={"suggest.c": {}},
                            )
                        ]
                    },
                }
            ]
        )
        assert collect_suggestion_locations(doc)["suggest.c"] == [
            _loc("style", 1, 6, text="gamma")
        ]

    def test_header_footer_footnote_carry_segment_id(self):
        # Segment indexes restart at 0: a header range 0-4 must not be
        # confused with body 0-4.
        def seg(start, end, text, **marks):
            return {
                "content": [
                    {
                        "startIndex": start,
                        "endIndex": end,
                        "paragraph": {
                            "elements": [_text_run(start, end, text, **marks)]
                        },
                    }
                ]
            }

        doc = _single_tab_doc(
            body=[
                {
                    "startIndex": 0,
                    "endIndex": 4,
                    "paragraph": {"elements": [_text_run(0, 4, "body")]},
                }
            ],
            headers={"h1": seg(0, 4, "head", suggestedInsertionIds=["suggest.hd"])},
            footers={"f1": seg(0, 3, "foo", suggestedDeletionIds=["suggest.ft"])},
            footnotes={
                "kix.fn": seg(0, 5, "note\n", suggestedInsertionIds=["suggest.fn"])
            },
        )
        locs = collect_suggestion_locations(doc)
        assert locs["suggest.hd"] == [_loc("insert", 0, 4, segment="h1", text="head")]
        assert locs["suggest.ft"] == [_loc("delete", 0, 3, segment="f1", text="foo")]
        assert locs["suggest.fn"] == [
            _loc("insert", 0, 5, segment="kix.fn", text="note\n")
        ]

    def test_object_and_list_maps_singular_id_and_no_fake_range(self):
        doc = _single_tab_doc(
            body=[],
            inlineObjects={
                "kix.img": {
                    "objectId": "kix.img",
                    "suggestedInsertionId": "suggest.img",
                    "suggestedDeletionIds": ["suggest.rm"],
                }
            },
            lists={"kix.l": {"suggestedListPropertiesChanges": {"suggest.lp": {}}}},
            suggestedDocumentStyleChanges={"suggest.ds": {}},
        )
        locs = collect_suggestion_locations(doc)
        assert locs["suggest.img"] == [_loc("insert", None, None)]
        assert locs["suggest.rm"] == [_loc("delete", None, None)]
        assert locs["suggest.lp"] == [_loc("list-properties", None, None)]
        assert locs["suggest.ds"] == [_loc("document-style", None, None)]

    def test_date_properties_and_positioned_object_ids(self):
        doc = _single_tab_doc(
            body=[
                {
                    "startIndex": 1,
                    "endIndex": 12,
                    "paragraph": {
                        "elements": [
                            _text_run(1, 6, "when "),
                            {
                                "startIndex": 6,
                                "endIndex": 7,
                                "dateElement": {
                                    "dateElementProperties": {},
                                    "suggestedDateElementPropertiesChanges": {
                                        "suggest.date": {}
                                    },
                                },
                            },
                            _text_run(7, 12, " ok\n"),
                        ],
                        "positionedObjectIds": [],
                        # API shape: map keyed by suggestion ID.
                        "suggestedPositionedObjectIds": {
                            "suggest.pos": {"objectIds": ["kix.pos"]}
                        },
                    },
                }
            ],
            positionedObjects={
                "kix.pos": {
                    "objectId": "kix.pos",
                    "suggestedInsertionId": "suggest.pos",
                }
            },
        )
        locs = collect_suggestion_locations(doc)
        assert locs["suggest.date"] == [_loc("date-properties", 6, 7)]
        # The paragraph anchor is ranged; the positionedObjects-map mirror
        # (no range, kind insert) is dropped in its favour.
        assert locs["suggest.pos"] == [_loc("positioned-object", 1, 12)]

    def test_object_map_mirror_deduped_but_property_change_kept(self):
        doc = _single_tab_doc(
            body=[
                {
                    "startIndex": 1,
                    "endIndex": 3,
                    "paragraph": {
                        "elements": [
                            {
                                "startIndex": 1,
                                "endIndex": 2,
                                "inlineObjectElement": {
                                    "inlineObjectId": "kix.img",
                                    "suggestedInsertionIds": ["suggest.img"],
                                },
                            },
                            _text_run(2, 3, "\n"),
                        ]
                    },
                }
            ],
            inlineObjects={
                "kix.img": {
                    "objectId": "kix.img",
                    "suggestedInsertionId": "suggest.img",
                    "suggestedInlineObjectPropertiesChanges": {
                        "suggest.img": {},
                        "suggest.props": {},
                    },
                }
            },
        )
        locs = collect_suggestion_locations(doc)
        assert locs["suggest.img"] == [
            _loc("insert", 1, 2),
            _loc("object-properties", None, None),
        ]
        assert locs["suggest.props"] == [_loc("object-properties", None, None)]

    def test_no_tabs_or_marks(self):
        assert collect_suggestion_locations({"tabs": []}) == {}
        assert collect_suggestion_locations({}) == {}


class TestThreadHelpers:
    def test_summarize_reads_only_observed_fields(self):
        s = summarize_suggestion_thread(_DOC["suggestions"][2])
        assert s == {
            "id": "suggest.b",
            "status": "open",
            "author": "Alejandro Acelas",
            "author_is_me": False,
            "created": "2026-08-26T17:23:55.448Z",
            "updated": "2026-08-26T17:23:55.448Z",
            "summary": "Delete: “beta text to delete”",
            "replies": 0,
        }

    def test_summarize_tolerates_missing_fields(self):
        s = summarize_suggestion_thread({"suggestionId": "suggest.x"})
        assert s["status"] == "unknown"
        assert s["author"] == "unknown"
        assert s["summary"] == ""

    def test_summarize_counts_replies_or_posts(self):
        t = {**_thread("suggest.r"), "replies": [{"postId": "p1"}]}
        assert summarize_suggestion_thread(t)["replies"] == 1
        t = {**_thread("suggest.r"), "posts": [{}, {}]}
        assert summarize_suggestion_thread(t)["replies"] == 2

    def test_sorted_by_create_time(self):
        ids = [t["suggestionId"] for t in sorted_suggestion_threads(_DOC)]
        assert ids == ["suggest.done", "suggest.a", "suggest.b", "suggest.h"]

    def test_find_thread(self):
        assert find_suggestion_thread(_DOC, "suggest.b")["status"] == "OPEN"
        assert find_suggestion_thread(_DOC, "suggest.zz") is None


# --- decide_suggestion ------------------------------------------------------


def _mock_docs_service(batch_response=None, batch_error=None):
    service = MagicMock()
    execute = service.documents.return_value.batchUpdate.return_value.execute
    if batch_error is not None:
        execute.side_effect = batch_error
    else:
        execute.return_value = batch_response if batch_response is not None else {}
    return service


def _batch_body(service):
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"]


_ACCEPT_OK = {
    "replies": [{}],
    "writeControl": {"requiredRevisionId": "rev2"},
    "suggestionResponses": [{"acceptedSuggestionIds": ["suggest.a"]}],
    "commentUpdateState": "ALL_SAVED",
}


class TestDecideSuggestion:
    @pytest.mark.parametrize(
        "decision,key",
        [
            ("accept", "acceptSuggestion"),
            ("reject", "rejectSuggestion"),
            ("delete", "deleteSuggestion"),
        ],
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_exact_request_body(self, mock_svc, decision, key):
        response = {
            "replies": [{}],
            "suggestionResponses": [
                {
                    f"{decision}{'e' * (decision != 'delete')}dSuggestionIds": [
                        "suggest.a"
                    ]
                }
            ],
            "commentUpdateState": "ALL_SAVED",
        }
        service = _mock_docs_service(response)
        mock_svc.return_value = service
        result = decide_suggestion("doc1", "suggest.a", decision, "rev1")
        assert result is response
        call = service.documents.return_value.batchUpdate.call_args
        assert call.kwargs["documentId"] == "doc1"
        assert call.kwargs["body"] == {
            "requests": [{key: {"suggestionId": "suggest.a"}}],
            "writeControl": {"requiredRevisionId": "rev1"},
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_accept_without_revision_is_refused_before_any_call(self, mock_svc):
        with pytest.raises(GdocError, match="no revisionId"):
            decide_suggestion("doc1", "suggest.a", "accept", "")
        mock_svc.assert_not_called()

    @pytest.mark.parametrize(
        "decision,key,resp_key",
        [
            ("reject", "rejectSuggestion", "rejectedSuggestionIds"),
            ("delete", "deleteSuggestion", "deletedSuggestionIds"),
        ],
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_reject_delete_without_revision_go_unpinned(
        self, mock_svc, decision, key, resp_key
    ):
        # A commenter-author may reject/delete their own suggestion and may
        # not receive a revisionId; the request then carries no writeControl
        # at all — never requiredRevisionId: "".
        service = _mock_docs_service(
            {
                "replies": [{}],
                "suggestionResponses": [{resp_key: ["suggest.a"]}],
                "commentUpdateState": "ALL_SAVED",
            }
        )
        mock_svc.return_value = service
        decide_suggestion("doc1", "suggest.a", decision, "")
        assert _batch_body(service) == {
            "requests": [{key: {"suggestionId": "suggest.a"}}],
        }

    def test_unknown_decision_is_programming_error(self):
        with pytest.raises(ValueError):
            decide_suggestion("doc1", "suggest.a", "resolve", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_no_request_set_400_is_preview_unavailable(self, mock_svc):
        # Live shape from project 856825977485 for acceptSuggestion.
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(
                400,
                b'{"error": {"code": 400, "message": "Invalid requests[0]: No '
                b'request set.", "status": "INVALID_ARGUMENT"}}',
            )
        )
        with pytest.raises(PreviewUnavailableError, match="acceptSuggestion"):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_unknown_name_400_is_preview_unavailable(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(
                400,
                b'{"error": {"message": "Unknown name \\"rejectSuggestion\\""}}',
            )
        )
        with pytest.raises(PreviewUnavailableError):
            decide_suggestion("doc1", "suggest.a", "reject", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_stale_revision_says_rerun(self, mock_svc):
        # Live shape.
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(
                400,
                b'{"error": {"code": 400, "message": "The required revision ID '
                b"'AIro' does not match the latest revision.\", "
                b'"status": "FAILED_PRECONDITION"}}',
            )
        )
        with pytest.raises(GdocError, match="document changed.*re-run"):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_other_400_carries_google_message(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(
                400,
                b'{"error": {"message": "Suggestion is not open."}}',
                reason="Bad Request",
            )
        )
        with pytest.raises(
            GdocError,
            match="cannot accept suggestion suggest.a: Suggestion is not open.",
        ):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @pytest.mark.parametrize(
        "decision,rule",
        [
            ("accept", "accept requires edit access"),
            ("reject", "reject requires edit access or suggestion authorship"),
            ("delete", "delete requires suggestion authorship"),
        ],
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_403_names_the_permission_rule(self, mock_svc, decision, rule):
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(403))
        with pytest.raises(GdocError, match=rule) as exc:
            decide_suggestion("doc1", "suggest.a", decision, "rev1")
        assert exc.value.exit_code == 1

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_for_suggestion_is_usage_error(self, mock_svc):
        # Live shape: "Suggestion with ID suggest.x does not exist."
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(
                404,
                b'{"error": {"code": 404, "message": "Suggestion with ID '
                b'suggest.a does not exist.", "status": "NOT_FOUND"}}',
            )
        )
        with pytest.raises(GdocError, match="suggestion not found: suggest.a") as e:
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")
        assert e.value.exit_code == 3

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_for_document_stays_document_error(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(
                404,
                b'{"error": {"message": "Requested entity was not found."}}',
            )
        )
        with pytest.raises(GdocError, match="Document not found: doc1"):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_401_is_auth_error(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(401))
        with pytest.raises(AuthError):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_transport_failure_is_indeterminate(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=TimeoutError("timed out")
        )
        with pytest.raises(
            GdocError,
            match=(
                "accept request for suggestion suggest.a failed in transit.*"
                "outcome is unknown.*Inspect `gdoc suggestions --all`"
            ),
        ):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_partial_save_failure_is_an_error(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            {
                "replies": [{}],
                "suggestionResponses": [{}],
                "commentUpdateState": "ALL_FAILED_UNKNOWN_REASON",
            }
        )
        with pytest.raises(
            GdocError,
            match="not saved \\(commentUpdateState=ALL_FAILED_UNKNOWN_REASON\\)",
        ):
            decide_suggestion("doc1", "suggest.a", "reject", "rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_missing_comment_update_state_is_an_error(self, mock_svc):
        # A batch that must save a suggestion thread requires ALL_SAVED.
        mock_svc.return_value = _mock_docs_service(
            {
                "replies": [{}],
                "suggestionResponses": [{"acceptedSuggestionIds": ["suggest.a"]}],
            }
        )
        with pytest.raises(GdocError, match="commentUpdateState=missing"):
            decide_suggestion("doc1", "suggest.a", "accept", "rev1")

    @pytest.mark.parametrize(
        "decision,responses",
        [
            ("accept", [{}]),
            ("accept", [{"acceptedSuggestionIds": ["suggest.other"]}]),
            ("accept", [{"rejectedSuggestionIds": ["suggest.a"]}]),
            ("reject", [{"acceptedSuggestionIds": ["suggest.a"]}]),
            ("delete", None),
        ],
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_response_must_name_the_id_under_the_decision(
        self,
        mock_svc,
        decision,
        responses,
    ):
        body = {"replies": [{}], "commentUpdateState": "ALL_SAVED"}
        if responses is not None:
            body["suggestionResponses"] = responses
        mock_svc.return_value = _mock_docs_service(body)
        with pytest.raises(GdocError, match="did not report suggest.a under"):
            decide_suggestion("doc1", "suggest.a", decision, "rev1")

    @pytest.mark.parametrize(
        "decision,key",
        [
            ("reject", "rejectedSuggestionIds"),
            ("delete", "deletedSuggestionIds"),
        ],
    )
    @patch("gdoc.api.docs.get_docs_service")
    def test_reject_and_delete_response_keys(self, mock_svc, decision, key):
        body = {
            "replies": [{}],
            "commentUpdateState": "ALL_SAVED",
            "suggestionResponses": [{key: ["suggest.a"]}],
        }
        mock_svc.return_value = _mock_docs_service(body)
        assert decide_suggestion("doc1", "suggest.a", decision, "rev1") is body


# --- CLI: suggestions (list / show one) -----------------------------------


def _args(command, **overrides):
    defaults = {
        "command": command,
        "doc": "doc1",
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_LIST_PATCHES = (
    patch("gdoc.state.update_state_after_command"),
    patch("gdoc.notify.pre_flight", return_value=None),
)


class TestCmdSuggestions:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_terse_lists_open_sorted_with_locations(
        self, _get, _pf, mock_update, capsys
    ):
        rc = cmd_suggestions(_args("suggestions", all=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert out == (
            "#suggest.a [open] Alejo Acelas (me) 2026-08-26\n"
            "  Add: “(added A)”\n"
            "  @Tab 1 27-37 insert\n"
            "#suggest.b [open] Alejandro Acelas 2026-08-26\n"
            "  Delete: “beta text to delete”\n"
            "  @Tab 1 73-92 delete\n"
            "#suggest.h [open] Alejo Acelas (me) 2026-08-26\n"
            "  Add: “(added H)”\n"
            "  @Draft 18-28 insert\n"
        )
        assert "suggest.done" not in out
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["command"] == "suggestions"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_all_includes_decided_threads_without_location(self, _get, _pf, _u, capsys):
        cmd_suggestions(_args("suggestions", all=True))
        out = capsys.readouterr().out
        assert out.startswith(
            "#suggest.done [accepted] Alejo Acelas (me) 2026-08-26\n"
            "  Add: old\n  (no inline location found)\n"
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_verbose_shows_full_times_and_snippets(self, _get, _pf, _u, capsys):
        cmd_suggestions(_args("suggestions", all=False, verbose=True))
        out = capsys.readouterr().out
        assert "#suggest.a [open] Alejo Acelas (me) 2026-08-26T17:23:54.236Z" in out
        assert '  @Tab 1 27-37 insert " (added A)"' in out
        assert "  Modified: 2026-08-26T17:23:54.236Z" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_plain_is_tab_separated(self, _get, _pf, _u, capsys):
        cmd_suggestions(_args("suggestions", all=False, plain=True))
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == (
            "suggest.a\topen\tAlejo Acelas\tAdd: “(added A)”\tt.0:27-37:insert"
        )
        assert lines[2] == (
            "suggest.h\topen\tAlejo Acelas\tAdd: “(added H)”\tt.draft:18-28:insert"
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_json_keeps_raw_threads_and_separate_locations(self, _get, _pf, _u, capsys):
        cmd_suggestions(_args("suggestions", all=False, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["revisionId"] == "rev1"
        assert [t["suggestionId"] for t in data["suggestions"]] == [
            "suggest.a",
            "suggest.b",
            "suggest.h",
        ]
        # Raw thread untouched — no synthesized range fields.
        assert data["suggestions"][0] == _thread("suggest.a")
        assert set(data["locations"]) == {"suggest.a", "suggest.b", "suggest.h"}
        assert data["locations"]["suggest.b"][0]["kind"] == "delete"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads")
    def test_missing_status_is_unknown_not_open(self, mock_get, _pf, _u, capsys):
        t = _thread("suggest.ns")
        del t["status"]
        mock_get.return_value = {**_DOC, "suggestions": [t]}
        cmd_suggestions(_args("suggestions", all=False))
        assert capsys.readouterr().out == "No open suggestions.\n"
        cmd_suggestions(_args("suggestions", all=True))
        assert capsys.readouterr().out.startswith("#suggest.ns [unknown]")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads")
    def test_segment_and_no_range_locations_render(self, mock_get, _pf, _u, capsys):
        doc = {
            "revisionId": "r",
            "suggestions": [_thread("suggest.hd"), _thread("suggest.ds")],
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {
                        "body": {"content": []},
                        "suggestedDocumentStyleChanges": {"suggest.ds": {}},
                        "headers": {
                            "h1": {
                                "content": [
                                    {
                                        "startIndex": 0,
                                        "endIndex": 4,
                                        "paragraph": {
                                            "elements": [
                                                _text_run(
                                                    0,
                                                    4,
                                                    "head",
                                                    suggestedInsertionIds=[
                                                        "suggest.hd"
                                                    ],
                                                )
                                            ]
                                        },
                                    }
                                ]
                            }
                        },
                    },
                }
            ],
        }
        mock_get.return_value = doc
        cmd_suggestions(_args("suggestions", all=False))
        out = capsys.readouterr().out
        assert "  @Main h1 0-4 insert\n" in out
        assert "  @Main (no range) document-style\n" in out
        cmd_suggestions(_args("suggestions", all=False, plain=True))
        out = capsys.readouterr().out
        assert "\tt.0/h1:0-4:insert\n" in out
        assert "\tt.0:(no range):document-style\n" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads")
    def test_empty_messages(self, mock_get, _pf, _u, capsys):
        mock_get.return_value = {
            **_DOC,
            "suggestions": [_thread("s", status="REJECTED")],
        }
        cmd_suggestions(_args("suggestions", all=False))
        assert capsys.readouterr().out == "No open suggestions.\n"
        mock_get.return_value = {**_DOC, "suggestions": []}
        cmd_suggestions(_args("suggestions", all=True))
        assert capsys.readouterr().out == "No suggestions.\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch(
        "gdoc.api.docs.get_document_threads",
        side_effect=GdocError("native comment threads are not available: x"),
    )
    def test_preview_unavailable_is_a_failure_not_a_fallback(
        self, _get, _pf, mock_update
    ):
        with pytest.raises(GdocError, match="not available") as e:
            cmd_suggestions(_args("suggestions", all=False))
        assert e.value.exit_code == 1
        mock_update.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    @patch("gdoc.notify.pre_flight")
    def test_rejects_spreadsheets(self, mock_pf, mock_get, _u):
        mock_pf.return_value = ChangeInfo(mime_type=SPREADSHEET_MIME)
        with pytest.raises(GdocError, match="not a Google Doc"):
            cmd_suggestions(_args("suggestions", all=False, quiet=False))
        mock_get.assert_not_called()


class TestCmdSuggestionInfo:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_terse(self, _get, _pf, mock_update, capsys):
        rc = cmd_suggestions(_args("suggestions", suggestion_id="suggest.b"))
        assert rc == 0
        assert capsys.readouterr().out == (
            "#suggest.b [open] Alejandro Acelas 2026-08-26\n"
            "  Delete: “beta text to delete”\n"
            "  @Tab 1 73-92 delete\n"
        )
        assert mock_update.call_args.kwargs["command"] == "suggestions"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_plain(self, _get, _pf, _u, capsys):
        cmd_suggestions(
            _args("suggestions", suggestion_id="suggest.a", plain=True)
        )
        assert capsys.readouterr().out == (
            "id\tsuggest.a\nstatus\topen\nauthor\tAlejo Acelas\n"
            "created\t2026-08-26T17:23:54.236Z\n"
            "summary\tAdd: “(added A)”\n"
            "location\tt.0:27-37:insert\n"
            "replies\t0\n"
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads")
    def test_plain_sanitizes_summary_row(self, mock_get, _pf, _u, capsys):
        thread = {
            **find_suggestion_thread(_DOC, "suggest.a"),
            "summaryText": "first\tpart\nsecond line",
        }
        mock_get.return_value = {**_DOC, "suggestions": [thread]}
        cmd_suggestions(
            _args("suggestions", suggestion_id="suggest.a", plain=True)
        )
        assert "summary\tfirst part second line\n" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_json(self, _get, _pf, _u, capsys):
        cmd_suggestions(
            _args("suggestions", suggestion_id="suggest.h", json=True)
        )
        data = json.loads(capsys.readouterr().out)
        assert data["suggestion"] == _DOC["suggestions"][0]
        assert data["locations"] == [
            {
                "tab": "Draft",
                "tabId": "t.draft",
                "segmentId": "",
                "kind": "insert",
                "startIndex": 18,
                "endIndex": 28,
                "text": " (added H)",
            }
        ]
        assert data["revisionId"] == "rev1"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_threads", return_value=_DOC)
    def test_unknown_id_is_usage_error(self, _get, _pf, mock_update):
        with pytest.raises(GdocError, match="suggestion not found: suggest.zz") as e:
            cmd_suggestions(_args("suggestions", suggestion_id="suggest.zz"))
        assert e.value.exit_code == 3
        mock_update.assert_not_called()


# --- CLI: accept / reject / delete ----------------------------------------


def _after(sid, status):
    """Document read back after a decision: thread in *status*, or gone."""
    threads = [t for t in _DOC["suggestions"] if t["suggestionId"] != sid]
    if status != "gone":
        threads.append(_thread(sid, status=status))
    return {**_DOC, "revisionId": "rev2", "suggestions": threads}


def _decision_patches(after_status, sid="suggest.a", response=None):
    """Patch read (before + after), write, version, state, pre-flight."""
    return (
        patch("gdoc.state.update_state_after_command"),
        patch("gdoc.notify.pre_flight", return_value=None),
        patch("gdoc.api.drive.get_file_version", return_value={"version": 50}),
        patch(
            "gdoc.api.docs.decide_suggestion",
            return_value=response if response is not None else _ACCEPT_OK,
        ),
        patch(
            "gdoc.api.docs.get_document_threads",
            side_effect=[_DOC, _after(sid, after_status)],
        ),
    )


class TestCmdDecisions:
    def test_accept_terse_pins_revision_and_updates_state(self, capsys):
        p = _decision_patches("ACCEPTED")
        with p[0] as mock_update, p[1], p[2], p[3] as mock_decide, p[4] as mock_get:
            rc = cmd_suggestion(
                _args("suggestion", accept=True, suggestion_id="suggest.a")
            )
        assert rc == 0
        assert capsys.readouterr().out == "OK accepted suggestion #suggest.a\n"
        mock_decide.assert_called_once_with("doc1", "suggest.a", "accept", "rev1")
        assert mock_get.call_count == 2
        kw = mock_update.call_args.kwargs
        assert kw["command"] == "suggestion"
        assert kw["command_version"] == 50
        assert "comment_state_patch" not in kw
        assert not kw.get("full_doc_write")

    def test_accept_json_includes_suggestion_responses(self, capsys):
        p = _decision_patches("ACCEPTED")
        with p[0], p[1], p[2], p[3], p[4]:
            cmd_suggestion(
                _args("suggestion", accept=True, suggestion_id="suggest.a", json=True)
            )
        assert json.loads(capsys.readouterr().out) == {
            "ok": True,
            "id": "suggest.a",
            "status": "accepted",
            "threadStatus": "accepted",
            "suggestionResponses": [{"acceptedSuggestionIds": ["suggest.a"]}],
        }

    def test_reject_plain(self, capsys):
        p = _decision_patches(
            "REJECTED",
            response={
                "replies": [{}],
                "suggestionResponses": [{"rejectedSuggestionIds": ["suggest.a"]}],
                "commentUpdateState": "ALL_SAVED",
            },
        )
        with p[0], p[1], p[2], p[3] as mock_decide, p[4]:
            rc = cmd_suggestion(
                _args("suggestion", reject=True, suggestion_id="suggest.a", plain=True)
            )
        assert rc == 0
        assert capsys.readouterr().out == "id\tsuggest.a\nstatus\trejected\n"
        mock_decide.assert_called_once_with("doc1", "suggest.a", "reject", "rev1")

    def test_delete_with_force_expects_thread_gone(self, capsys):
        p = _decision_patches(
            "gone",
            response={
                "replies": [{}],
                "suggestionResponses": [{"deletedSuggestionIds": ["suggest.a"]}],
                "commentUpdateState": "ALL_SAVED",
            },
        )
        with p[0], p[1], p[2], p[3] as mock_decide, p[4]:
            rc = cmd_suggestion(
                _args(
                    "suggestion", delete=True,
                    suggestion_id="suggest.a",
                    force=True,
                    json=True,
                )
            )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "deleted"
        assert data["threadStatus"] == "gone"
        mock_decide.assert_called_once_with("doc1", "suggest.a", "delete", "rev1")

    def test_delete_without_force_non_interactive_writes_nothing(self):
        p = _decision_patches("gone")
        with (
            p[0] as mock_update,
            p[1],
            p[2],
            p[3] as mock_decide,
            p[4] as mock_get,
            patch("sys.stdin") as stdin,
        ):
            stdin.isatty.return_value = False
            with pytest.raises(
                GdocError,
                match="Refusing to delete suggestion #suggest.a without --force",
            ) as e:
                cmd_suggestion(
                    _args("suggestion", delete=True, suggestion_id="suggest.a", force=False)
                )
        assert e.value.exit_code == 3
        mock_decide.assert_not_called()
        mock_get.assert_not_called()
        mock_update.assert_not_called()

    def test_unknown_id_fails_before_write(self):
        p = _decision_patches("ACCEPTED")
        with p[0] as mock_update, p[1], p[2], p[3] as mock_decide, p[4]:
            with pytest.raises(
                GdocError, match="suggestion not found: suggest.zz"
            ) as e:
                cmd_suggestion(
                    _args("suggestion", accept=True, suggestion_id="suggest.zz")
                )
        assert e.value.exit_code == 3
        mock_decide.assert_not_called()
        mock_update.assert_not_called()

    def test_already_decided_fails_before_write(self):
        p = _decision_patches("ACCEPTED")
        with p[0], p[1], p[2], p[3] as mock_decide, p[4]:
            with pytest.raises(
                GdocError, match="suggestion suggest.done is already accepted"
            ) as e:
                cmd_suggestion(
                    _args("suggestion", reject=True, suggestion_id="suggest.done")
                )
        assert e.value.exit_code == 3
        mock_decide.assert_not_called()

    def test_read_back_still_open_is_not_success(self, capsys):
        p = _decision_patches("OPEN")
        with p[0] as mock_update, p[1], p[2], p[3], p[4]:
            with pytest.raises(
                GdocError, match="reads back as open \\(expected accepted\\)"
            ):
                cmd_suggestion(
                    _args("suggestion", accept=True, suggestion_id="suggest.a")
                )
        assert capsys.readouterr().out == ""
        mock_update.assert_not_called()

    def test_failed_read_back_reports_saved_but_unverified(self, capsys):
        p = _decision_patches("ACCEPTED")
        with p[0] as mock_update, p[1], p[2], p[3], p[4] as mock_get:
            mock_get.side_effect = [_DOC, TimeoutError("timed out")]
            with pytest.raises(
                GdocError,
                match=(
                    "suggestion suggest.a was reported saved after accept.*"
                    "verification failed.*may already have been applied"
                ),
            ):
                cmd_suggestion(
                    _args("suggestion", accept=True, suggestion_id="suggest.a")
                )
        assert capsys.readouterr().out == ""
        mock_update.assert_not_called()

    def test_version_failure_warns_after_verified_success(self, capsys):
        p = _decision_patches("ACCEPTED")
        with p[0] as mock_update, p[1], p[2] as mock_version, p[3], p[4]:
            mock_version.side_effect = TimeoutError("timed out")
            rc = cmd_suggestion(
                _args("suggestion", accept=True, suggestion_id="suggest.a")
            )
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out == "OK accepted suggestion #suggest.a\n"
        assert (
            "WARN: suggestion #suggest.a is accepted, but the document "
            "version could not be refreshed: timed out; awareness state "
            "not updated"
        ) in captured.err
        mock_update.assert_not_called()

    def test_state_failure_warns_after_verified_success(self, capsys):
        p = _decision_patches("ACCEPTED")
        with p[0] as mock_update, p[1], p[2], p[3], p[4]:
            mock_update.side_effect = OSError("disk full")
            rc = cmd_suggestion(
                _args("suggestion", accept=True, suggestion_id="suggest.a")
            )
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out == "OK accepted suggestion #suggest.a\n"
        assert (
            "WARN: suggestion #suggest.a is accepted, but awareness state "
            "was not persisted: disk full"
        ) in captured.err

    def test_read_back_wrong_state_is_not_success(self):
        # A reject that reads back as accepted (or a delete that leaves the
        # thread listed) must not be reported as done.
        p = _decision_patches("ACCEPTED")
        with p[0], p[1], p[2], p[3], p[4]:
            with pytest.raises(
                GdocError, match="reads back as accepted \\(expected rejected\\)"
            ):
                cmd_suggestion(
                    _args("suggestion", reject=True, suggestion_id="suggest.a")
                )
        p = _decision_patches("REJECTED")
        with p[0], p[1], p[2], p[3], p[4]:
            with pytest.raises(
                GdocError, match="reads back as rejected \\(expected gone\\)"
            ):
                cmd_suggestion(
                    _args("suggestion", delete=True, suggestion_id="suggest.a", force=True)
                )

    def test_api_permission_error_propagates_without_state_update(self):
        p = _decision_patches("ACCEPTED")
        with p[0] as mock_update, p[1], p[2], p[3] as mock_decide, p[4] as mock_get:
            mock_decide.side_effect = GdocError(
                "Permission denied: cannot accept suggestion suggest.a "
                "(accept requires edit access)"
            )
            with pytest.raises(GdocError, match="accept requires edit access"):
                cmd_suggestion(
                    _args("suggestion", accept=True, suggestion_id="suggest.a")
                )
        assert mock_get.call_count == 1  # no read-back after a failed write
        mock_update.assert_not_called()

    def test_preview_unavailable_read_fails_before_write(self):
        p = _decision_patches("ACCEPTED")
        with p[0], p[1], p[2], p[3] as mock_decide, p[4] as mock_get:
            mock_get.side_effect = GdocError(
                "native comment threads are not available"
            )
            with pytest.raises(GdocError, match="not available"):
                cmd_suggestion(
                    _args("suggestion", accept=True, suggestion_id="suggest.a")
                )
        mock_decide.assert_not_called()

    def test_pre_flight_runs_unless_quiet(self):
        p = _decision_patches("ACCEPTED")
        with p[0], p[1] as mock_pf, p[2], p[3], p[4]:
            cmd_suggestion(
                _args("suggestion", accept=True, suggestion_id="suggest.a", quiet=False)
            )
        mock_pf.assert_called_once_with("doc1", quiet=False)


# --- parser & MCP exposure --------------------------------------------------


class TestParserAndMcp:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["suggestions", "D"], ("cmd_suggestions", {"all": False})),
            (["suggestions", "D", "--all"], ("cmd_suggestions", {"all": True})),
            (
                ["suggestions", "D", "suggest.x"],
                ("cmd_suggestions", {"suggestion_id": "suggest.x"}),
            ),
            (
                ["suggestion", "D", "suggest.x", "--accept"],
                ("cmd_suggestion", {"accept": True, "force": False}),
            ),
            (
                ["suggestion", "D", "suggest.x", "--reject"],
                ("cmd_suggestion", {"reject": True}),
            ),
            (
                ["suggestion", "D", "suggest.x", "--delete", "--force"],
                ("cmd_suggestion", {"delete": True, "force": True}),
            ),
        ],
    )
    def test_parses(self, argv, expected):
        from gdoc.cli import build_parser

        args = build_parser().parse_args(argv)
        func_name, attrs = expected
        assert args.func.__name__ == func_name
        assert args.doc == "D"
        for k, v in attrs.items():
            assert getattr(args, k) == v

    @pytest.mark.parametrize("flags", [[], ["--accept", "--reject"],
                                       ["--accept", "--delete"]])
    def test_suggestion_needs_exactly_one_decision(self, flags):
        from gdoc.cli import build_parser

        with pytest.raises(SystemExit) as e:
            build_parser().parse_args(["suggestion", "D", "suggest.x", *flags])
        assert e.value.code == 3

    @patch("gdoc.notify.pre_flight")
    def test_handler_rechecks_one_decision(self, mock_pf):
        # MCP builds its own argv, so the handler repeats the argparse rule.
        for flags in ({}, {"accept": True, "reject": True}):
            with pytest.raises(GdocError, match="exactly one") as e:
                cmd_suggestion(_args("suggestion", suggestion_id="suggest.a", **flags))
            assert e.value.exit_code == 3
        mock_pf.assert_not_called()

    def test_suggestions_all_with_id_is_usage_error(self):
        with pytest.raises(GdocError, match="--all") as e:
            cmd_suggestions(
                _args("suggestions", suggestion_id="suggest.a", all=True)
            )
        assert e.value.exit_code == 3

    def test_dropped_commands_are_gone(self):
        from gdoc.cli import build_parser

        for cmd in ("suggestion-info", "accept-suggestion",
                    "reject-suggestion", "delete-suggestion"):
            with pytest.raises(SystemExit):
                build_parser().parse_args([cmd, "D", "suggest.x"])
            assert cmd not in mcp.EXPOSED_COMMANDS

    def test_mcp_exposure(self):
        assert mcp.EXPOSED_COMMANDS["suggestions"] is True
        assert mcp.EXPOSED_COMMANDS["suggestion"] is False
        props = mcp.build_tools(allow={"suggestions"})["gdoc_suggestions"][
            "inputSchema"
        ]["properties"]
        assert "suggestion_id" in props

    def test_mcp_suggestion_delete_requires_force(self):
        schema = mcp.build_tools(allow={"suggestion"})["gdoc_suggestion"][
            "inputSchema"
        ]
        assert {"accept", "reject", "delete", "force"} <= set(schema["properties"])
        # force is required for --delete only, so the schema leaves it
        # optional and the call-time guard enforces it.
        assert "force" not in schema.get("required", [])
        assert "const" not in schema["properties"]["force"]
        with pytest.raises(ValueError, match="`force: true` is required"):
            mcp.call_command(
                "suggestion",
                {"doc": "D", "suggestion_id": "s", "delete": True},
            )

    def test_mcp_suggestion_accept_does_not_need_force(self):
        from gdoc.cli import build_parser

        subparser = mcp._subparsers(build_parser())["suggestion"]
        argv = mcp._argv_for(
            "suggestion", {"doc": "D", "suggestion_id": "s", "accept": True},
            subparser,
        )
        assert argv == ["suggestion", "--accept", "--", "D", "s"]
