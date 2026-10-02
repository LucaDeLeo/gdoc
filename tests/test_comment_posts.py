"""Tests for native comment assignment and post operations (Docs API preview).

Covers `comment --assign`, `reply --suggestion` / `--reassign`,
`edit-comment`, `edit-suggestion-reply`, `delete-reply` and
`delete-suggestion-reply`. Drive v3 stays the path for ordinary comments;
these commands use Docs-native threads only for what Drive cannot express.

Fixture shapes mirror live responses from a preview-enrolled project:
``comments[]`` threads carry ``commentId``, ``headPost`` and ``replies[]``
of Posts (``postId``, ``content``, ``author.me``, ``commentAction``,
optional ``assigneeEmail``); ``suggestions[]`` threads carry
``suggestionId`` and a generated ``headPost`` whose ``postId`` differs from
the thread ID. Drive reply IDs equal native post IDs.
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from gdoc.api.docs import (
    add_comment_reply,
    delete_comment_reply,
    find_post,
    find_thread,
    get_document_threads,
    head_post_assignee,
    insert_comment,
    post_is_action,
    update_comment_post,
)
from gdoc.cli import (
    _try_anchored_comment,
    cmd_comment,
    cmd_delete_reply,
    cmd_delete_suggestion_reply,
    cmd_edit_comment,
    cmd_edit_suggestion_reply,
    cmd_reply,
)
from gdoc.util import AuthError, GdocError, PreviewUnavailableError

ME = "me@example.com"
OTHER = "other@example.com"


def _http_error(status, message="Error", content=None):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Error"
    if content is None:
        content = json.dumps(
            {"error": {"code": status, "message": message}}
        ).encode()
    return HttpError(resp, content, uri="")


def _post(post_id, content="text", me=True, assignee=None, action=None):
    post = {
        "postId": post_id,
        "content": content,
        "author": {"displayName": "Someone", "me": me},
        "createTime": "2026-08-26T18:00:00Z",
        "updateTime": "2026-08-26T18:00:00Z",
        "commentAction": action or "NO_COMMENT_ACTION_CHANGE",
    }
    if assignee:
        post["assigneeEmail"] = assignee
    return post


def _comment_thread(cid="c1", assignee=None, replies=None):
    return {
        "commentId": cid,
        "anchorId": "kix.abc",
        "headPost": _post(cid, "head", assignee=assignee),
        "replies": replies or [],
        "status": "OPEN",
        "plainTextQuote": "quoted",
    }


def _suggestion_thread(sid="suggest.s1", replies=None):
    head = {
        "postId": "p_head",
        "author": {"displayName": "Someone", "me": True},
        "createTime": "2026-08-26T18:00:00Z",
        "updateTime": "2026-08-26T18:00:00Z",
        "suggestionAction": "NO_SUGGESTION_ACTION_CHANGE",
    }
    return {
        "suggestionId": sid,
        "headPost": head,
        "replies": replies or [],
        "status": "OPEN",
        "summaryText": "Replace: a with b",
    }


def _doc(comments=None, suggestions=None):
    return {
        "documentId": "abc123",
        "revisionId": "rev1",
        "comments": comments or [],
        "suggestions": suggestions or [],
        "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
    }


def _mock_docs_service(batch_response=None, batch_error=None):
    service = MagicMock()
    execute = service.documents.return_value.batchUpdate.return_value.execute
    if batch_error is not None:
        execute.side_effect = batch_error
    else:
        execute.return_value = (
            batch_response if batch_response is not None else {}
        )
    return service


def _batch_body(service):
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"]


def _make_args(command, **overrides):
    defaults = {
        "command": command,
        "doc": "abc123",
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- thread helpers -------------------------------------------------------


class TestThreadHelpers:
    def test_find_thread_keeps_namespaces_separate(self):
        doc = _doc(
            comments=[_comment_thread("shared")],
            suggestions=[_suggestion_thread("shared")],
        )
        found = find_thread(doc, "shared", suggestion=False)
        assert found["commentId"] == "shared"
        found = find_thread(doc, "shared", suggestion=True)
        assert found["suggestionId"] == "shared"
        assert find_thread(doc, "missing", suggestion=False) is None
        assert find_thread(_doc(), "shared", suggestion=True) is None

    def test_find_post_covers_head_and_replies(self):
        thread = _comment_thread("c1", replies=[_post("r1")])
        assert find_post(thread, "c1")["content"] == "head"
        assert find_post(thread, "r1")["postId"] == "r1"
        assert find_post(thread, "nope") is None

    def test_head_post_assignee_from_head_post(self):
        assert head_post_assignee(_comment_thread(assignee=ME)) == ME

    def test_head_post_assignee_is_head_post_only(self):
        # A reply's assigneeEmail records a reassignment event (observed
        # live: the head keeps the original); the preflight fact Google
        # requires is headPost.assigneeEmail.
        thread = _comment_thread(
            assignee=ME,
            replies=[_post("r1", assignee=OTHER), _post("r2")],
        )
        assert head_post_assignee(thread) == ME
        unassigned_head = _comment_thread(replies=[_post("r1", assignee=OTHER)])
        assert head_post_assignee(unassigned_head) == ""

    def test_head_post_assignee_unassigned_is_empty(self):
        assert head_post_assignee(_comment_thread()) == ""
        assert head_post_assignee(_comment_thread(replies=[_post("r1")])) == ""

    def test_head_post_assignee_ignores_unknown_shapes(self):
        # Fail closed: only a string assigneeEmail on a post counts.
        thread = _comment_thread()
        thread["assigneeEmail"] = ME
        thread["headPost"]["assignee"] = {"emailAddress": ME}
        thread["replies"] = [dict(_post("r1"), assigneeEmail={"user": ME})]
        assert head_post_assignee(thread) == ""

    def test_post_is_deleted(self):
        from gdoc.api.docs import post_is_deleted

        assert post_is_deleted(dict(_post("r1"), deleted=True))
        assert not post_is_deleted(_post("r1"))

    def test_post_is_action(self):
        assert post_is_action(_post("r1", assignee=ME))
        assert post_is_action(_post("r1", action="RESOLVE"))
        assert post_is_action(_post("r1", action="REOPEN"))
        assert not post_is_action(_post("r1"))
        assert not post_is_action(
            {"postId": "p", "suggestionAction": "NO_SUGGESTION_ACTION_CHANGE"}
        )


# --- get_document_threads -------------------------------------------------


class TestGetDocumentThreads:
    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_sends_preview_view_modes(self, _svc, mock_raw):
        mock_raw.return_value = {
            "documentId": "abc123",
            "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        }
        doc = get_document_threads("abc123")
        params = mock_raw.call_args.args[2]
        assert params == {
            "includeTabsContent": "true",
            "suggestionsViewMode": "SUGGESTIONS_INLINE",
            "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        }
        assert doc["comments"] == [] and doc["suggestions"] == []

    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_unknown_field_means_preview_unavailable(self, _svc, mock_raw):
        mock_raw.side_effect = _http_error(
            400,
            'Invalid JSON payload received. Unknown name "comments_view_mode"',
        )
        with pytest.raises(GdocError, match="not enrolled") as ei:
            get_document_threads("abc123")
        assert not isinstance(ei.value, PreviewUnavailableError)
        assert ei.value.exit_code == 1

    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_silently_dropped_view_mode_is_preview_unavailable(self, _svc, mock_raw):
        # No 400, but no echo and no thread lists: must not read as "no threads".
        mock_raw.return_value = {"documentId": "abc123", "revisionId": "r"}
        with pytest.raises(GdocError, match="not enrolled"):
            get_document_threads("abc123")

    @patch("gdoc.api.docs.get_docs_service")
    def test_raw_get_builds_preview_uri(self, mock_svc):
        from gdoc.api.docs import _documents_get_raw

        service = MagicMock()
        with patch("googleapiclient.http.HttpRequest") as mock_req:
            mock_req.return_value.execute.return_value = {"documentId": "d"}
            _documents_get_raw(service, "doc/1", {"commentsViewMode": "X"})
        uri = mock_req.call_args.args[2]
        assert uri == "https://docs.googleapis.com/v1/documents/doc%2F1?commentsViewMode=X"
        assert mock_req.call_args.kwargs["method"] == "GET"

    @patch("gdoc.api.docs._documents_get_raw")
    @patch("gdoc.api.docs.get_docs_service")
    def test_404_is_document_not_found(self, _svc, mock_raw):
        mock_raw.side_effect = _http_error(404)
        with pytest.raises(GdocError, match="Document not found"):
            get_document_threads("abc123")


# --- insert_comment with assignee ----------------------------------------


_INSERT_OK = {
    "commentUpdateState": "ALL_SAVED",
    "replies": [{"insertComment": {"commentThread": {"commentId": "c_new"}}}],
}


class TestInsertCommentAssignee:
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_assignee_sent_as_assignee_email_address(self, mock_svc, mock_read):
        service = _mock_docs_service(_INSERT_OK)
        mock_svc.return_value = service
        mock_read.return_value = _doc(comments=[_comment_thread("c_new", assignee=ME)])
        cid = insert_comment(
            "abc123", "hi", 5, 9, tab_id="t.0", revision_id="rev1",
            assignee_email=ME,
        )
        assert cid == "c_new"
        assert _batch_body(service) == {
            "requests": [{"insertComment": {
                "content": "hi",
                "range": {"startIndex": 5, "endIndex": 9, "tabId": "t.0"},
                "assigneeEmailAddress": ME,
            }}],
            "writeControl": {"requiredRevisionId": "rev1"},
        }
        mock_read.assert_called_once_with("abc123")

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_assignee_missing_on_read_back_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_INSERT_OK)
        mock_read.return_value = _doc(comments=[_comment_thread("c_new")])
        with pytest.raises(GdocError, match="does not show it assigned") as ei:
            insert_comment("abc123", "hi", 5, 9, assignee_email=ME)
        assert not isinstance(ei.value, PreviewUnavailableError)

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_no_assignee_leaves_request_unchanged(self, mock_svc, mock_read):
        service = _mock_docs_service(_INSERT_OK)
        mock_svc.return_value = service
        insert_comment("abc123", "hi", 5, 9)
        assert _batch_body(service) == {
            "requests": [{"insertComment": {
                "content": "hi", "range": {"startIndex": 5, "endIndex": 9},
            }}],
        }
        mock_read.assert_not_called()

    @pytest.mark.parametrize("message, expect", [
        ('Unknown name "insert_comment"', "preview not enabled"),
        ("Invalid requests[0]: No request set.", "preview not enabled"),
        ("The revision id does not match", "document changed"),
    ])
    @patch("gdoc.api.docs.get_docs_service")
    def test_distinct_preview_reasons(self, mock_svc, message, expect):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, message)
        )
        with pytest.raises(PreviewUnavailableError, match=expect):
            insert_comment("abc123", "hi", 5, 9, revision_id="rev1")

    @patch("gdoc.api.docs.get_docs_service")
    def test_403_reason(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(403))
        with pytest.raises(PreviewUnavailableError, match="not permitted"):
            insert_comment("abc123", "hi", 5, 9)

    @pytest.mark.parametrize("response", [
        {"replies": _INSERT_OK["replies"]},  # state missing
        dict(_INSERT_OK, commentUpdateState="ALL_FAILED_UNKNOWN_REASON"),
        {"commentUpdateState": "ALL_SAVED", "replies": [{"insertComment": {}}]},
    ])
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_assigned_ambiguous_outcome_says_inspect(
        self, mock_svc, mock_read, response,
    ):
        # 2xx without ALL_SAVED, or ALL_SAVED without a thread id, may or may
        # not have created a comment: never "not created", never a fallback.
        mock_svc.return_value = _mock_docs_service(response)
        with pytest.raises(GdocError, match="outcome uncertain") as ei:
            insert_comment("abc123", "hi", 5, 9, assignee_email=ME)
        assert not isinstance(ei.value, PreviewUnavailableError)
        assert "inspect" in str(ei.value)
        assert "not created" not in str(ei.value).lower()
        mock_read.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_unassigned_tolerates_missing_state(self, mock_svc):
        # Pre-existing contract for the fallback path is unchanged.
        response = {"replies": _INSERT_OK["replies"]}
        mock_svc.return_value = _mock_docs_service(response)
        assert insert_comment("abc123", "hi", 5, 9) == "c_new"

    @patch("gdoc.api.docs.get_docs_service")
    def test_unassigned_partial_failure_still_falls_back(self, mock_svc):
        response = dict(
            _INSERT_OK, commentUpdateState="ALL_FAILED_UNKNOWN_REASON",
        )
        mock_svc.return_value = _mock_docs_service(response)
        with pytest.raises(PreviewUnavailableError, match="ALL_FAILED"):
            insert_comment("abc123", "hi", 5, 9)

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_assignee_on_reply_only_does_not_verify_insert(
        self, mock_svc, mock_read,
    ):
        mock_svc.return_value = _mock_docs_service(_INSERT_OK)
        mock_read.return_value = _doc(comments=[
            _comment_thread("c_new", replies=[_post("r1", assignee=ME)]),
        ])
        with pytest.raises(GdocError, match="does not show it assigned"):
            insert_comment("abc123", "hi", 5, 9, assignee_email=ME)


# --- add_comment_reply / update / delete API wrappers ---------------------

_SAVED_EMPTY = {"commentUpdateState": "ALL_SAVED", "replies": [{}]}


def _reply_ok(post_id="p_new", content="text", assignee=None):
    post = _post(post_id, content, assignee=assignee)
    return {
        "commentUpdateState": "ALL_SAVED",
        "replies": [{"addCommentReply": {"post": post}}],
    }


class TestAddCommentReply:
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_comment_thread_request_and_verification(self, mock_svc, mock_read):
        service = _mock_docs_service(_reply_ok("p_new", "hello"))
        mock_svc.return_value = service
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("p_new", "hello")])]
        )
        post = add_comment_reply("abc123", "c1", content="hello")
        assert post["postId"] == "p_new"
        body = _batch_body(service)
        assert body == {"requests": [
            {"addCommentReply": {"commentId": "c1", "post": {"content": "hello"}}}
        ]}
        assert "writeControl" not in body
        mock_read.assert_called_once_with("abc123")

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_suggestion_thread_uses_suggestion_id(self, mock_svc, mock_read):
        service = _mock_docs_service(_reply_ok("p_new"))
        mock_svc.return_value = service
        mock_read.return_value = _doc(
            suggestions=[_suggestion_thread("suggest.s1", replies=[_post("p_new")])]
        )
        add_comment_reply("abc123", "suggest.s1", content="text", suggestion=True)
        assert _batch_body(service)["requests"] == [
            {"addCommentReply": {
                "suggestionId": "suggest.s1", "post": {"content": "text"},
            }}
        ]

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_reassign_sends_assignee_email_in_same_post(self, mock_svc, mock_read):
        # Event-sourced verification: the head keeps ME, the new reply
        # carries OTHER — that is a verified reassignment.
        service = _mock_docs_service(_reply_ok("p_new", "t", assignee=OTHER))
        mock_svc.return_value = service
        mock_read.return_value = _doc(comments=[_comment_thread(
            "c1", assignee=ME, replies=[_post("p_new", "t", assignee=OTHER)],
        )])
        add_comment_reply("abc123", "c1", content="t", assignee_email=OTHER)
        assert _batch_body(service)["requests"][0]["addCommentReply"]["post"] == {
            "content": "t", "assigneeEmail": OTHER,
        }

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_reassign_not_shown_on_read_back_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(
            _reply_ok("p_new", "t", assignee=OTHER)
        )
        mock_read.return_value = _doc(comments=[_comment_thread(
            "c1", assignee=ME, replies=[_post("p_new", "t")],
        )])
        with pytest.raises(GdocError, match="not show the thread reassigned"):
            add_comment_reply("abc123", "c1", content="t", assignee_email=OTHER)

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_read_back_missing_post_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_reply_ok("p_new"))
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        with pytest.raises(GdocError, match="not on commentId c1"):
            add_comment_reply("abc123", "c1", content="x")

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_read_back_missing_thread_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_reply_ok("p_new"))
        mock_read.return_value = _doc()
        with pytest.raises(GdocError, match="inspect the thread"):
            add_comment_reply("abc123", "suggest.s1", content="x", suggestion=True)

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_200_without_post_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
        with pytest.raises(GdocError, match="no post"):
            add_comment_reply("abc123", "c1", content="x")
        mock_read.assert_not_called()

    @pytest.mark.parametrize("state", ["", "ALL_FAILED_UNKNOWN_REASON",
                                       "NO_UPDATES_REQUESTED"])
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_not_all_saved_is_error(self, mock_svc, mock_read, state):
        response = _reply_ok("p_new")
        if state:
            response["commentUpdateState"] = state
        else:
            del response["commentUpdateState"]
        mock_svc.return_value = _mock_docs_service(response)
        with pytest.raises(GdocError, match="not saved"):
            add_comment_reply("abc123", "c1", content="x")
        mock_read.assert_not_called()

    @pytest.mark.parametrize("message", [
        'Invalid JSON payload received. Unknown name "add_comment_reply"',
        "Invalid requests[0]: No request set.",
        "Cannot find field.",
    ])
    @patch("gdoc.api.docs.get_docs_service")
    def test_preview_unavailable_is_named(self, mock_svc, message):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, message)
        )
        with pytest.raises(GdocError, match="not enrolled") as ei:
            add_comment_reply("abc123", "c1", content="x")
        assert not isinstance(ei.value, PreviewUnavailableError)

    @patch("gdoc.api.docs.get_docs_service")
    def test_author_rule_400_keeps_google_message(self, mock_svc):
        msg = "The comment thread is not assigned to anyone."
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(400, msg))
        with pytest.raises(GdocError, match=msg) as ei:
            add_comment_reply("abc123", "c1", content="x", assignee_email=OTHER)
        assert ei.value.exit_code == 1

    @patch("gdoc.api.docs.get_docs_service")
    def test_403_keeps_google_message(self, mock_svc):
        msg = "The caller does not have permission"
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(403, msg))
        with pytest.raises(GdocError, match=msg):
            add_comment_reply("abc123", "c1", content="x")

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_names_thread_or_document(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(404))
        with pytest.raises(GdocError, match="thread/post"):
            add_comment_reply("abc123", "c1", content="x")

    @patch("gdoc.api.docs.get_docs_service")
    def test_401_is_auth_error(self, mock_svc):
        from gdoc.util import AuthError

        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(401))
        with pytest.raises(AuthError):
            add_comment_reply("abc123", "c1", content="x")


class TestUpdateCommentPost:
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_request_and_read_back(self, mock_svc, mock_read):
        service = _mock_docs_service(_SAVED_EMPTY)
        mock_svc.return_value = service
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1", "new text")])]
        )
        update_comment_post("abc123", "c1", "r1", "new text")
        body = _batch_body(service)
        assert body == {"requests": [{"updateCommentPost": {
            "commentId": "c1", "postId": "r1", "content": "new text",
        }}]}
        assert "writeControl" not in body

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_suggestion_namespace(self, mock_svc, mock_read):
        service = _mock_docs_service(_SAVED_EMPTY)
        mock_svc.return_value = service
        mock_read.return_value = _doc(suggestions=[
            _suggestion_thread("suggest.s1", replies=[_post("r1", "new")])
        ])
        update_comment_post("abc123", "suggest.s1", "r1", "new", suggestion=True)
        request = _batch_body(service)["requests"][0]["updateCommentPost"]
        assert "suggestionId" in request

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_read_back_tolerates_whitespace_normalization(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1", "new text")])]
        )
        update_comment_post("abc123", "c1", "r1", "new text\n")  # no raise

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_read_back_with_old_text_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1", "old text")])]
        )
        with pytest.raises(GdocError, match="does not show the new text"):
            update_comment_post("abc123", "c1", "r1", "new text")

    @patch("gdoc.api.docs.get_docs_service")
    def test_not_author_400_keeps_message(self, mock_svc):
        msg = "Only the author of a post can edit it."
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(400, msg))
        with pytest.raises(GdocError, match=msg):
            update_comment_post("abc123", "c1", "r1", "x")


class TestDeleteCommentReply:
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_request_and_read_back(self, mock_svc, mock_read):
        service = _mock_docs_service(_SAVED_EMPTY)
        mock_svc.return_value = service
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        delete_comment_reply("abc123", "c1", "r1")
        assert _batch_body(service) == {"requests": [{"deleteCommentReply": {
            "commentId": "c1", "postId": "r1",
        }}]}

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_suggestion_namespace(self, mock_svc, mock_read):
        service = _mock_docs_service(_SAVED_EMPTY)
        mock_svc.return_value = service
        mock_read.return_value = _doc(suggestions=[_suggestion_thread("suggest.s1")])
        delete_comment_reply("abc123", "suggest.s1", "r1", suggestion=True)
        assert _batch_body(service)["requests"][0]["deleteCommentReply"] == {
            "suggestionId": "suggest.s1", "postId": "r1",
        }

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_post_still_present_is_error(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1")])]
        )
        with pytest.raises(GdocError, match="still on the thread"):
            delete_comment_reply("abc123", "c1", "r1")

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tombstone_after_delete_counts_as_deleted(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
        mock_read.return_value = _doc(comments=[
            _comment_thread("c1", replies=[dict(_post("r1"), deleted=True)]),
        ])
        delete_comment_reply("abc123", "c1", "r1")  # no raise

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_thread_gone_after_delete_counts_as_deleted(self, mock_svc, mock_read):
        mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
        mock_read.return_value = _doc()
        delete_comment_reply("abc123", "c1", "r1")  # no raise


# --- dispatch failures after the request was sent are mutation-ambiguous ---


_TABS_DOC = {
    "revisionId": "rev1",
    "tabs": [{
        "tabProperties": {"tabId": "t.0", "title": "Tab 1"},
        "documentTab": {"body": {"content": [{
            "startIndex": 1, "endIndex": 20,
            "paragraph": {"elements": [{
                "startIndex": 1, "endIndex": 20,
                "textRun": {"content": "the quick brown fox\n"},
            }]},
        }]}},
    }],
}


def _transport_errors():
    import ssl

    import httplib2

    return [
        TimeoutError("timed out"),  # socket.timeout is an alias since 3.10
        ConnectionResetError(104, "Connection reset by peer"),
        ssl.SSLError("EOF occurred in violation of protocol"),
        httplib2.HttpLib2Error("connection dropped"),
    ]


class TestDispatchFailureIsAmbiguous:
    """A 5xx or transport error from batchUpdate may follow a write Google
    already applied: report uncertainty, never fall back or invite a retry."""

    @pytest.mark.parametrize("error", [
        *[_http_error(status) for status in (500, 502, 503)],
        *_transport_errors(),
    ])
    @pytest.mark.parametrize("operation", ["insert", "insert_assigned", "reply",
                                           "edit", "delete"])
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_5xx_and_transport_errors_say_may_have_succeeded(
        self, mock_svc, mock_read, operation, error,
    ):
        mock_svc.return_value = _mock_docs_service(batch_error=error)
        calls = {
            "insert": lambda: insert_comment("abc123", "hi", 5, 9),
            "insert_assigned": lambda: insert_comment(
                "abc123", "hi", 5, 9, assignee_email=ME,
            ),
            "reply": lambda: add_comment_reply("abc123", "c1", content="x"),
            "edit": lambda: update_comment_post("abc123", "c1", "r1", "x"),
            "delete": lambda: delete_comment_reply("abc123", "c1", "r1"),
        }
        with pytest.raises(GdocError) as ei:
            calls[operation]()
        msg = str(ei.value)
        assert "outcome uncertain" in msg and "may have succeeded" in msg
        assert "inspect" in msg
        # Not the fallback sentinel: comment --quote must not degrade to a
        # Drive comment that could duplicate an anchored one.
        assert not isinstance(ei.value, PreviewUnavailableError)
        assert ei.value.exit_code == 1
        mock_read.assert_not_called()
        assert mock_svc.return_value.documents.return_value.batchUpdate.call_count == 1

    @patch("gdoc.api.docs.get_docs_service")
    def test_4xx_diagnostics_unchanged(self, mock_svc):
        # Definite rejections keep their specific diagnosis.
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, 'Unknown name "insert_comment"')
        )
        with pytest.raises(PreviewUnavailableError, match="preview not enabled"):
            insert_comment("abc123", "hi", 5, 9)
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, "Only the author can edit this post.")
        )
        with pytest.raises(GdocError, match="Only the author") as ei:
            update_comment_post("abc123", "c1", "r1", "x")
        assert "uncertain" not in str(ei.value)
        mock_svc.return_value = _mock_docs_service(batch_error=_http_error(404))
        with pytest.raises(GdocError, match="Not found"):
            add_comment_reply("abc123", "c1", content="x")

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.insert_comment", side_effect=GdocError(
        "insertComment outcome uncertain: … may have succeeded; inspect …"
    ))
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_cmd_comment_quote_does_not_fall_back_on_uncertain_outcome(
        self, _get, _insert, mock_create,
    ):
        with patch("gdoc.notify.pre_flight", return_value=None), \
                patch("gdoc.api.drive.get_file_version"), \
                patch("gdoc.state.update_state_after_command"):
            with pytest.raises(GdocError, match="outcome uncertain"):
                cmd_comment(_make_args("comment", text="p", quote="quick", assign=None))
        mock_create.assert_not_called()


# --- post-write verification failures are mutation-ambiguous ---------------


class TestReadBackFailureIsAmbiguous:
    """After ALL_SAVED, a failing verification read must never claim
    "No change was made": the write may have landed."""

    @pytest.mark.parametrize("error", [
        GdocError(
            "native comment threads are not available: … No change was made."
        ),
        GdocError("API error (503): Backend Error"),
        AuthError("Authentication expired. Run `gdoc auth`."),
        TimeoutError("timed out"),
        ConnectionResetError(104, "Connection reset by peer"),
        __import__("httplib2").HttpLib2Error("connection dropped"),
        RefreshError("token refresh failed"),
        TransportError("credential transport failed"),
    ])
    @pytest.mark.parametrize("operation", ["insert", "reply", "edit", "delete"])
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.get_docs_service")
    def test_read_back_error_says_may_have_succeeded(
        self, mock_svc, mock_read, operation, error,
    ):
        mock_read.side_effect = error
        if operation == "insert":
            mock_svc.return_value = _mock_docs_service(_INSERT_OK)
            call = lambda: insert_comment(  # noqa: E731
                "abc123", "hi", 5, 9, assignee_email=ME,
            )
        elif operation == "reply":
            mock_svc.return_value = _mock_docs_service(_reply_ok("p_new"))
            call = lambda: add_comment_reply("abc123", "c1", content="x")  # noqa: E731
        elif operation == "edit":
            mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
            call = lambda: update_comment_post("abc123", "c1", "r1", "x")  # noqa: E731
        else:
            mock_svc.return_value = _mock_docs_service(_SAVED_EMPTY)
            call = lambda: delete_comment_reply("abc123", "c1", "r1")  # noqa: E731
        with pytest.raises(GdocError) as ei:
            call()
        msg = str(ei.value)
        assert "may have succeeded" in msg and "inspect" in msg
        assert "reported saved" in msg
        assert "No change was made" not in msg.split("verification read failed")[0]
        expected_code = getattr(error, "exit_code", 1)
        assert ei.value.exit_code == expected_code
        assert not isinstance(ei.value, PreviewUnavailableError)
        # The write itself was sent exactly once; nothing is retried.
        assert mock_svc.return_value.documents.return_value.batchUpdate.call_count == 1


# --- cmd_comment --assign -------------------------------------------------




@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
class TestCmdCommentAssign:
    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.insert_comment", return_value="c_assigned")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_assign_passes_assignee_and_never_uses_drive(
        self, _get, mock_insert, mock_create, _ver, _pf, mock_update, capsys,
    ):
        args = _make_args("comment", text="please", quote="quick brown", assign=ME)
        assert cmd_comment(args) == 0
        assert mock_insert.call_args.kwargs["assignee_email"] == ME
        assert mock_insert.call_args.kwargs["revision_id"] == "rev1"
        assert mock_insert.call_args.kwargs["tab_id"] == "t.0"
        mock_create.assert_not_called()
        out = capsys.readouterr().out
        assert f"OK comment #c_assigned (anchored, assigned to {ME})" in out
        assert mock_update.call_args.kwargs["comment_state_patch"] == {
            "add_comment_id": "c_assigned",
        }

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.insert_comment", return_value="c_assigned")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_assign_json_and_plain(
        self, _get, _insert, _create, _ver, _pf, _update, capsys,
    ):
        cmd_comment(
            _make_args("comment", text="p", quote="quick", assign=ME, json=True)
        )
        data = json.loads(capsys.readouterr().out)
        assert data == {
            "ok": True, "id": "c_assigned", "status": "created",
            "anchored": True, "assignee": ME,
        }
        cmd_comment(
            _make_args("comment", text="p", quote="quick", assign=ME, plain=True)
        )
        assert capsys.readouterr().out == (
            f"id\tc_assigned\nanchored\ttrue\nassignee\t{ME}\n"
        )

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_assign_requires_quote(
        self, mock_get, mock_create, _ver, mock_pf, _update, capsys,
    ):
        with pytest.raises(GdocError, match="requires --quote") as ei:
            cmd_comment(_make_args("comment", text="p", quote=None, assign=ME,
                                   quiet=False))
        assert ei.value.exit_code == 3
        mock_pf.assert_not_called()
        mock_get.assert_not_called()
        mock_create.assert_not_called()
        assert capsys.readouterr().out == ""

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.insert_comment")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_assign_quote_not_found_is_usage_error(
        self, _get, mock_insert, mock_create, _ver, _pf, _update,
    ):
        with pytest.raises(GdocError, match="Quote text not found") as ei:
            cmd_comment(_make_args("comment", text="p", quote="zzz", assign=ME))
        assert ei.value.exit_code == 3
        mock_insert.assert_not_called()
        mock_create.assert_not_called()

    @patch("gdoc.api.comments.create_comment")
    @patch(
        "gdoc.api.docs.insert_comment",
        side_effect=PreviewUnavailableError("insertComment not available"),
    )
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_assign_preview_unavailable_is_hard_error(
        self, _get, _insert, mock_create, _ver, _pf, _update,
    ):
        with pytest.raises(GdocError, match="cannot create an assigned comment") as ei:
            cmd_comment(_make_args("comment", text="p", quote="quick", assign=ME))
        assert ei.value.exit_code == 1
        mock_create.assert_not_called()

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_assign_failure_message_keeps_reason(
        self, _get, mock_create, _ver, _pf, _update,
    ):
        for reason in ("document changed since it was read",
                       "comment not saved (commentUpdateState=PARTIAL)",
                       "not permitted for this user"):
            with patch("gdoc.api.docs.insert_comment",
                       side_effect=PreviewUnavailableError(reason)):
                with pytest.raises(GdocError, match=re.escape(reason)):
                    cmd_comment(_make_args("comment", text="p", quote="quick",
                                           assign=ME))
        mock_create.assert_not_called()

    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_drive"})
    @patch(
        "gdoc.api.docs.insert_comment",
        side_effect=PreviewUnavailableError("insertComment not available"),
    )
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_without_assign_fallback_is_unchanged(
        self, _get, mock_insert, mock_create, _ver, _pf, _update, capsys,
    ):
        args = _make_args("comment", text="p", quote="quick", assign=None)
        assert cmd_comment(args) == 0
        assert "assignee_email" not in mock_insert.call_args.kwargs
        mock_create.assert_called_once_with("abc123", "p", quote="quick")
        assert "OK comment #c_drive" in capsys.readouterr().out

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.api.docs.get_document_with_tabs")
    def test_assign_validates_text_before_any_read(
        self, mock_get, mock_create, _ver, _pf, _update,
    ):
        with pytest.raises(GdocError, match="must not be empty") as ei:
            cmd_comment(_make_args("comment", text="  ", quote="quick", assign=ME))
        assert ei.value.exit_code == 3
        too_long = "é" * 1025  # 2050 UTF-8 bytes, 1025 characters
        with pytest.raises(GdocError, match="2050 UTF-8 bytes") as ei:
            cmd_comment(_make_args("comment", text=too_long, quote="quick", assign=ME))
        assert ei.value.exit_code == 3
        mock_get.assert_not_called()


class TestTryAnchoredCommentAssignee:
    @patch("gdoc.api.docs.insert_comment", return_value="c1")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_no_assignee_keeps_legacy_call_signature(self, _get, mock_insert):
        _try_anchored_comment("abc123", "t", "quick")
        assert mock_insert.call_args.kwargs == {"tab_id": "t.0", "revision_id": "rev1"}


# --- cmd_reply native routing ---------------------------------------------


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
class TestCmdReplyNative:
    @patch("gdoc.api.comments.create_reply")
    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.add_comment_reply", return_value=_post("p9", "hi"))
    def test_suggestion_reply_routes_natively_without_preread(
        self, mock_add, mock_read, mock_drive, _ver, _pf, mock_update, capsys,
    ):
        args = _make_args("reply", comment_id="suggest.s1", text="hi", suggestion=True,
                          reassign=None)
        assert cmd_reply(args) == 0
        mock_add.assert_called_once_with(
            "abc123", "suggest.s1", content="hi", suggestion=True, assignee_email=None,
        )
        mock_read.assert_not_called()  # no precondition for a plain reply
        mock_drive.assert_not_called()
        assert "OK reply on suggestion #suggest.s1" in capsys.readouterr().out
        kwargs = mock_update.call_args.kwargs
        assert kwargs["comment_state_patch"] is None
        assert kwargs["command_version"] == 7

    @patch("gdoc.api.docs.add_comment_reply", return_value=_post("p9", "hi"))
    def test_suggestion_reply_json_and_plain(
        self, _add, _ver, _pf, _update, capsys,
    ):
        cmd_reply(_make_args("reply", comment_id="suggest.s1", text="hi",
                             suggestion=True, reassign=None, json=True))
        assert json.loads(capsys.readouterr().out) == {
            "ok": True, "suggestionId": "suggest.s1", "postId": "p9",
            "status": "created",
        }
        cmd_reply(_make_args("reply", comment_id="suggest.s1", text="hi",
                             suggestion=True, reassign=None, plain=True))
        assert capsys.readouterr().out == "suggestionId\tsuggest.s1\npostId\tp9\n"

    @patch("gdoc.api.docs.get_document_threads")
    @patch(
        "gdoc.api.docs.add_comment_reply",
        return_value=_post("p9", "hi", assignee=OTHER),
    )
    def test_reassign_preflights_assignee_then_writes(
        self, mock_add, mock_read, _ver, _pf, mock_update, capsys,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1", assignee=ME)])
        args = _make_args("reply", comment_id="c1", text="over to you",
                          suggestion=False, reassign=OTHER, json=True)
        assert cmd_reply(args) == 0
        mock_read.assert_called_once_with("abc123")
        mock_add.assert_called_once_with(
            "abc123", "c1", content="over to you", suggestion=False,
            assignee_email=OTHER,
        )
        assert json.loads(capsys.readouterr().out) == {
            "ok": True, "commentId": "c1", "replyId": "p9", "postId": "p9",
            "status": "created", "assignee": OTHER,
        }
        assert mock_update.call_args.kwargs["comment_state_patch"] == {
            "add_comment_id": "c1",
        }

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.add_comment_reply")
    def test_reassign_unassigned_parent_fails_before_write(
        self, mock_add, mock_read, _ver, _pf, mock_update, capsys,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        args = _make_args("reply", comment_id="c1", text="t", suggestion=False,
                          reassign=OTHER)
        with pytest.raises(GdocError, match="has no assignee") as ei:
            cmd_reply(args)
        assert ei.value.exit_code == 3
        mock_add.assert_not_called()
        mock_update.assert_not_called()
        assert capsys.readouterr().out == ""

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.add_comment_reply")
    def test_reassign_thread_missing_fails_before_write(
        self, mock_add, mock_read, _ver, _pf, _update,
    ):
        mock_read.return_value = _doc()
        args = _make_args("reply", comment_id="c1", text="t", suggestion=False,
                          reassign=OTHER)
        with pytest.raises(GdocError, match="comment thread not found") as ei:
            cmd_reply(args)
        assert ei.value.exit_code == 3
        mock_add.assert_not_called()

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.add_comment_reply")
    def test_reassign_reads_thread_even_with_quiet(
        self, mock_add, mock_read, _ver, mock_pf, mock_update,
    ):
        # --quiet skips only the awareness pre-flight; the precondition read
        # is a correctness gate and always runs.
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        args = _make_args("reply", comment_id="c1", text="t", suggestion=False,
                          reassign=OTHER, quiet=True)
        with pytest.raises(GdocError):
            cmd_reply(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)
        mock_read.assert_called_once()
        mock_read.return_value = _doc(comments=[_comment_thread("c1", assignee=ME)])
        mock_add.return_value = _post("p9", "t", assignee=OTHER)
        cmd_reply(args)
        assert mock_update.call_args.kwargs["quiet"] is True

    @patch("gdoc.api.docs.add_comment_reply")
    def test_usage_errors_precede_preflight(self, mock_add, _ver, mock_pf, _update):
        for kwargs in (
            {"comment_id": "suggest.s1", "text": "t", "suggestion": True,
             "reassign": OTHER},
            {"comment_id": "suggest.s1", "text": "", "suggestion": True,
             "reassign": None},
        ):
            with pytest.raises(GdocError) as ei:
                cmd_reply(_make_args("reply", quiet=False, **kwargs))
            assert ei.value.exit_code == 3
        mock_pf.assert_not_called()
        mock_add.assert_not_called()

    @patch("gdoc.api.docs.add_comment_reply")
    def test_suggestion_and_reassign_conflict(self, mock_add, _ver, _pf, _update):
        args = _make_args("reply", comment_id="suggest.s1", text="t",
                          suggestion=True, reassign=OTHER)
        with pytest.raises(GdocError, match="comment threads only") as ei:
            cmd_reply(args)
        assert ei.value.exit_code == 3
        mock_add.assert_not_called()

    @patch("gdoc.api.docs.add_comment_reply", return_value=_post("p9", "t"))
    def test_suggestion_ids_are_opaque(self, mock_add, _ver, _pf, _update, capsys):
        # No shape check: the flag alone selects the suggestionId namespace.
        args = _make_args("reply", comment_id="AAACopaque", text="t",
                          suggestion=True, reassign=None, json=True)
        assert cmd_reply(args) == 0
        mock_add.assert_called_once_with(
            "abc123", "AAACopaque", content="t", suggestion=True,
            assignee_email=None,
        )
        assert json.loads(capsys.readouterr().out)["suggestionId"] == "AAACopaque"

    @patch("gdoc.api.docs.add_comment_reply")
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_drive_path_ignores_id_shape(
        self, mock_drive, _svc, mock_add, _ver, _pf, _update,
    ):
        args = _make_args("reply", comment_id="suggest.looking", text="t",
                          suggestion=False, reassign=None)
        assert cmd_reply(args) == 0
        mock_drive.assert_called_once_with("abc123", "suggest.looking", content="t")
        mock_add.assert_not_called()

    @patch("gdoc.api.docs.get_document_threads")
    @patch("gdoc.api.docs.add_comment_reply")
    def test_reassign_requires_head_post_assignee(
        self, mock_add, mock_read, _ver, _pf, _update,
    ):
        # Head unassigned, later reply carries assigneeEmail: still no write.
        mock_read.return_value = _doc(comments=[
            _comment_thread("c1", replies=[_post("r1", assignee=ME)]),
        ])
        args = _make_args("reply", comment_id="c1", text="t", suggestion=False,
                          reassign=OTHER)
        with pytest.raises(GdocError, match="head post") as ei:
            cmd_reply(args)
        assert ei.value.exit_code == 3
        mock_add.assert_not_called()

    @patch("gdoc.api.comments.create_reply")
    @patch("gdoc.api.docs.add_comment_reply")
    def test_blank_reassign_is_usage_error(
        self, mock_add, mock_drive, _ver, mock_pf, _update, capsys,
    ):
        # Whitespace-only --reassign: neither a Drive reply nor a native one.
        for value in ("", "   "):
            args = _make_args("reply", comment_id="c1", text="t", suggestion=False,
                              reassign=value, quiet=False)
            with pytest.raises(GdocError, match="requires an email") as ei:
                cmd_reply(args)
            assert ei.value.exit_code == 3
        mock_add.assert_not_called()
        mock_drive.assert_not_called()
        mock_pf.assert_not_called()
        assert capsys.readouterr().out == ""

    @patch("gdoc.api.docs.add_comment_reply")
    def test_native_reply_validates_text(self, mock_add, _ver, _pf, _update):
        args = _make_args("reply", comment_id="suggest.s1", text="",
                          suggestion=True, reassign=None)
        with pytest.raises(GdocError, match="must not be empty") as ei:
            cmd_reply(args)
        assert ei.value.exit_code == 3
        mock_add.assert_not_called()

    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_plain_reply_still_uses_drive(
        self, mock_drive, _svc, _ver, _pf, mock_update, capsys,
    ):
        args = _make_args("reply", comment_id="c1", text="thanks",
                          suggestion=False, reassign=None, json=True)
        assert cmd_reply(args) == 0
        mock_drive.assert_called_once_with("abc123", "c1", content="thanks")
        assert json.loads(capsys.readouterr().out) == {
            "ok": True, "commentId": "c1", "replyId": "r1", "status": "created",
        }
        assert mock_update.call_args.kwargs["comment_state_patch"] == {
            "add_comment_id": "c1",
        }


# --- post-write Drive version lookup is best-effort -------------------------


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", side_effect=GdocError("API error (503)"))
class TestPostWriteVersionBestEffort:
    """A verified native write must not fail on the awareness version
    lookup: that would invite a duplicate retry."""

    @patch("gdoc.api.docs.insert_comment", return_value="c_assigned")
    @patch("gdoc.api.docs.get_document_with_tabs", return_value=_TABS_DOC)
    def test_assigned_insert(self, _get, _ins, _ver, _pf, mock_update, capsys):
        args = _make_args("comment", text="p", quote="quick", assign=ME, json=True)
        assert cmd_comment(args) == 0
        out, err = capsys.readouterr()
        assert json.loads(out)["id"] == "c_assigned"
        assert "WARN: write succeeded" in err
        kwargs = mock_update.call_args.kwargs
        assert kwargs["command_version"] is None
        assert kwargs["comment_state_patch"] == {"add_comment_id": "c_assigned"}

    @patch("gdoc.api.docs.add_comment_reply", return_value=_post("p9", "hi"))
    def test_native_reply(self, _add, _ver, _pf, mock_update, capsys):
        args = _make_args("reply", comment_id="s1", text="hi", suggestion=True,
                          reassign=None)
        assert cmd_reply(args) == 0
        out, err = capsys.readouterr()
        assert "OK reply on suggestion #s1" in out and "WARN" in err
        assert mock_update.call_args.kwargs["command_version"] is None

    @patch("gdoc.api.docs.update_comment_post")
    @patch("gdoc.api.docs.get_document_threads")
    def test_edit(self, mock_read, _upd, _ver, _pf, mock_update, capsys):
        thread = _comment_thread("c1", replies=[_post("r1")])
        mock_read.return_value = _doc(comments=[thread])
        args = _make_args("edit-comment", thread_id="c1", post_id="r1", text="new")
        assert cmd_edit_comment(args) == 0
        assert "WARN" in capsys.readouterr().err
        kwargs = mock_update.call_args.kwargs
        assert kwargs["command_version"] is None
        assert kwargs["comment_state_patch"] == {"add_comment_id": "c1"}

    @patch("gdoc.api.docs.delete_comment_reply")
    @patch("gdoc.api.docs.get_document_threads")
    def test_delete(self, mock_read, _del, _ver, _pf, mock_update, capsys):
        thread = _comment_thread("c1", replies=[_post("r1")])
        mock_read.return_value = _doc(comments=[thread])
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=True)
        assert cmd_delete_reply(args) == 0
        assert "WARN" in capsys.readouterr().err
        assert mock_update.call_args.kwargs["command_version"] is None

    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_drive"})
    def test_drive_comment_path_unchanged(self, _create, _svc, _ver, _pf, _update):
        # Ordinary Drive comment creation keeps its existing contract.
        with pytest.raises(GdocError, match="API error"):
            cmd_comment(_make_args("comment", text="p", quote=None, assign=None))


# --- edit-comment / edit-suggestion-reply ---------------------------------


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
@patch("gdoc.api.docs.update_comment_post")
@patch("gdoc.api.docs.get_document_threads")
class TestEditPost:
    def test_edit_comment_reply(
        self, mock_read, mock_update_post, _ver, _pf, mock_state, capsys,
    ):
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1", "old")])]
        )
        args = _make_args("edit-comment", thread_id="c1", post_id="r1", text="new")
        assert cmd_edit_comment(args) == 0
        mock_update_post.assert_called_once_with(
            "abc123", "c1", "r1", "new", suggestion=False,
        )
        assert "OK updated post r1 on #c1" in capsys.readouterr().out
        kwargs = mock_state.call_args.kwargs
        assert kwargs["command"] == "edit-comment"
        assert kwargs["comment_state_patch"] == {"add_comment_id": "c1"}

    def test_edit_comment_head_post_is_allowed(
        self, mock_read, mock_update_post, _ver, _pf, _state, capsys,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        args = _make_args("edit-comment", thread_id="c1", post_id="c1", text="new",
                          json=True)
        assert cmd_edit_comment(args) == 0
        assert json.loads(capsys.readouterr().out) == {
            "ok": True, "commentId": "c1", "postId": "c1", "status": "updated",
        }

    def test_edit_suggestion_reply(
        self, mock_read, mock_update_post, _ver, _pf, mock_state, capsys,
    ):
        mock_read.return_value = _doc(suggestions=[
            _suggestion_thread("suggest.s1", replies=[_post("r1", "old")])
        ])
        args = _make_args("edit-suggestion-reply", thread_id="suggest.s1",
                          post_id="r1", text="new", plain=True)
        assert cmd_edit_suggestion_reply(args) == 0
        mock_update_post.assert_called_once_with(
            "abc123", "suggest.s1", "r1", "new", suggestion=True,
        )
        assert capsys.readouterr().out == (
            "suggestionId\tsuggest.s1\npostId\tr1\nstatus\tupdated\n"
        )
        assert mock_state.call_args.kwargs["comment_state_patch"] is None

    def test_edit_suggestion_head_post_refused(
        self, mock_read, mock_update_post, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(suggestions=[_suggestion_thread("suggest.s1")])
        args = _make_args("edit-suggestion-reply", thread_id="suggest.s1",
                          post_id="p_head", text="new")
        with pytest.raises(GdocError, match="head post of a suggestion") as ei:
            cmd_edit_suggestion_reply(args)
        assert ei.value.exit_code == 3
        mock_update_post.assert_not_called()

    def test_edit_post_not_found(
        self, mock_read, mock_update_post, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        args = _make_args("edit-comment", thread_id="c1", post_id="zz", text="new")
        with pytest.raises(GdocError, match="post zz not found") as ei:
            cmd_edit_comment(args)
        assert ei.value.exit_code == 3
        mock_update_post.assert_not_called()

    def test_edit_null_author_does_not_crash(
        self, mock_read, mock_update_post, _ver, _pf, _state,
    ):
        post = dict(_post("r1", "old"), author=None)
        mock_read.return_value = _doc(comments=[_comment_thread("c1", replies=[post])])
        args = _make_args("edit-comment", thread_id="c1", post_id="r1", text="new")
        assert cmd_edit_comment(args) == 0  # authorship enforced by Google

    def test_edit_other_users_post_refused(
        self, mock_read, mock_update_post, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1", me=False)])]
        )
        args = _make_args("edit-comment", thread_id="c1", post_id="r1", text="new")
        with pytest.raises(GdocError, match="another user") as ei:
            cmd_edit_comment(args)
        assert ei.value.exit_code == 3
        mock_update_post.assert_not_called()

    def test_edit_validates_text_before_read(
        self, mock_read, mock_update_post, _ver, _pf, _state,
    ):
        args = _make_args("edit-comment", thread_id="c1", post_id="r1", text="")
        with pytest.raises(GdocError, match="must not be empty"):
            cmd_edit_comment(args)
        mock_read.assert_not_called()

    def test_edit_deleted_post_refused(
        self, mock_read, mock_update_post, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(comments=[
            _comment_thread("c1", replies=[dict(_post("r1"), deleted=True)]),
        ])
        args = _make_args("edit-comment", thread_id="c1", post_id="r1", text="x")
        with pytest.raises(GdocError, match="has been deleted") as ei:
            cmd_edit_comment(args)
        assert ei.value.exit_code == 3
        mock_update_post.assert_not_called()


# --- delete-reply / delete-suggestion-reply -------------------------------


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
@patch("gdoc.api.docs.delete_comment_reply")
@patch("gdoc.api.docs.get_document_threads")
class TestDeletePost:
    def test_delete_reply_force(
        self, mock_read, mock_delete, _ver, _pf, mock_state, capsys,
    ):
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1")])]
        )
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=True)
        assert cmd_delete_reply(args) == 0
        mock_delete.assert_called_once_with("abc123", "c1", "r1", suggestion=False)
        assert "OK deleted reply r1 from #c1" in capsys.readouterr().out
        # The thread survives, so its ID stays known; nothing is removed.
        assert mock_state.call_args.kwargs["comment_state_patch"] == {
            "add_comment_id": "c1",
        }

    def test_delete_suggestion_reply_json(
        self, mock_read, mock_delete, _ver, _pf, mock_state, capsys,
    ):
        mock_read.return_value = _doc(suggestions=[
            _suggestion_thread("suggest.s1", replies=[_post("r1")])
        ])
        args = _make_args("delete-suggestion-reply", thread_id="suggest.s1",
                          post_id="r1", force=True, json=True)
        assert cmd_delete_suggestion_reply(args) == 0
        mock_delete.assert_called_once_with(
            "abc123", "suggest.s1", "r1", suggestion=True,
        )
        assert json.loads(capsys.readouterr().out) == {
            "ok": True, "suggestionId": "suggest.s1", "postId": "r1",
            "status": "deleted",
        }
        assert mock_state.call_args.kwargs["comment_state_patch"] is None

    @patch("sys.stdin")
    def test_delete_without_force_non_interactive(
        self, mock_stdin, mock_read, mock_delete, _ver, _pf, _state,
    ):
        mock_stdin.isatty.return_value = False
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=False)
        with pytest.raises(GdocError, match="without --force") as ei:
            cmd_delete_reply(args)
        assert ei.value.exit_code == 3
        mock_read.assert_not_called()
        mock_delete.assert_not_called()

    def test_delete_head_post_refused(
        self, mock_read, mock_delete, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        args = _make_args("delete-reply", thread_id="c1", post_id="c1", force=True)
        with pytest.raises(GdocError, match="delete-comment") as ei:
            cmd_delete_reply(args)
        assert ei.value.exit_code == 3
        mock_delete.assert_not_called()

    def test_delete_suggestion_head_post_refused(
        self, mock_read, mock_delete, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(suggestions=[_suggestion_thread("suggest.s1")])
        args = _make_args("delete-suggestion-reply", thread_id="suggest.s1",
                          post_id="p_head", force=True)
        with pytest.raises(GdocError, match="head post") as ei:
            cmd_delete_suggestion_reply(args)
        assert ei.value.exit_code == 3
        mock_delete.assert_not_called()

    @pytest.mark.parametrize("post", [
        _post("r1", assignee=OTHER), _post("r1", action="RESOLVE"),
    ])
    def test_delete_action_or_assignment_reply_refused(
        self, mock_read, mock_delete, _ver, _pf, _state, post,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1", replies=[post])])
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=True)
        with pytest.raises(GdocError, match="action or an assignment") as ei:
            cmd_delete_reply(args)
        assert ei.value.exit_code == 3
        mock_delete.assert_not_called()

    def test_delete_null_author_does_not_crash(
        self, mock_read, mock_delete, _ver, _pf, _state,
    ):
        post = dict(_post("r1"), author=None)
        mock_read.return_value = _doc(comments=[_comment_thread("c1", replies=[post])])
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=True)
        assert cmd_delete_reply(args) == 0

    def test_delete_other_users_reply_refused(
        self, mock_read, mock_delete, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(
            comments=[_comment_thread("c1", replies=[_post("r1", me=False)])]
        )
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=True)
        with pytest.raises(GdocError, match="another user"):
            cmd_delete_reply(args)
        mock_delete.assert_not_called()

    def test_delete_post_not_found(
        self, mock_read, mock_delete, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(comments=[_comment_thread("c1")])
        args = _make_args("delete-reply", thread_id="c1", post_id="zz", force=True)
        with pytest.raises(GdocError, match="not found") as ei:
            cmd_delete_reply(args)
        assert ei.value.exit_code == 3
        mock_delete.assert_not_called()

    def test_delete_already_deleted_post_refused(
        self, mock_read, mock_delete, _ver, _pf, _state,
    ):
        mock_read.return_value = _doc(comments=[
            _comment_thread("c1", replies=[dict(_post("r1"), deleted=True)]),
        ])
        args = _make_args("delete-reply", thread_id="c1", post_id="r1", force=True)
        with pytest.raises(GdocError, match="already deleted") as ei:
            cmd_delete_reply(args)
        assert ei.value.exit_code == 3
        mock_delete.assert_not_called()


# --- parser and MCP exposure ---------------------------------------------


class TestParserAndMcp:
    @pytest.fixture(autouse=True)
    def _no_allow_env(self, monkeypatch):
        # build_tools honours GDOC_ALLOW_COMMANDS; a developer shell must
        # not turn these into KeyErrors.
        monkeypatch.delenv("GDOC_ALLOW_COMMANDS", raising=False)

    def test_parser_flags(self):
        from gdoc.cli import build_parser

        parser = build_parser()
        a = parser.parse_args(["comment", "d", "t", "--quote", "q", "--assign", ME])
        assert a.assign == ME and a.quote == "q"
        a = parser.parse_args(["reply", "d", "suggest.s1", "t", "--suggestion"])
        assert a.suggestion is True and a.reassign is None
        a = parser.parse_args(["reply", "d", "c1", "t", "--reassign", OTHER])
        assert a.reassign == OTHER and a.suggestion is False
        a = parser.parse_args(["edit-comment", "d", "c1", "p1", "new"])
        assert (a.thread_id, a.post_id, a.text) == ("c1", "p1", "new")
        assert a.func is cmd_edit_comment
        a = parser.parse_args(["edit-suggestion-reply", "d", "s", "p", "n"])
        assert a.func is cmd_edit_suggestion_reply
        a = parser.parse_args(["delete-reply", "d", "c1", "p1", "--force"])
        assert a.force is True and a.func is cmd_delete_reply
        a = parser.parse_args(["delete-suggestion-reply", "d", "s", "p"])
        assert a.force is False and a.func is cmd_delete_suggestion_reply

    def test_reply_text_still_required(self):
        from gdoc.cli import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["reply", "d", "c1", "--reassign", OTHER])

    def test_mcp_exposes_new_commands_as_writes(self):
        from gdoc.mcp import EXPOSED_COMMANDS, build_tools

        for cmd in ("edit-comment", "edit-suggestion-reply", "delete-reply",
                    "delete-suggestion-reply"):
            assert EXPOSED_COMMANDS[cmd] is False
        names = set(build_tools())
        assert {"gdoc_edit_comment", "gdoc_delete_suggestion_reply"} <= names
        assert "gdoc_delete_reply" not in build_tools(read_only=True)

    def test_mcp_descriptions_state_cross_parameter_rules(self):
        from gdoc.mcp import build_tools

        tools = build_tools()
        assert "`assign` requires `quote`" in tools["gdoc_comment"]["description"]
        assert "mutually exclusive" in tools["gdoc_reply"]["description"]

    def test_mcp_delete_reply_requires_force(self):
        from gdoc.mcp import build_tools, call_command

        tools = build_tools()
        for name in ("gdoc_delete_reply", "gdoc_delete_suggestion_reply",
                     "gdoc_delete_comment"):
            schema = tools[name]["inputSchema"]
            assert "force" in schema["required"]
            assert schema["properties"]["force"]["const"] is True
        assert "const" not in tools["gdoc_edit"]["inputSchema"]["properties"].get(
            "all", {},
        )
        with pytest.raises(ValueError, match="force: true"):
            call_command(
                "delete-reply", {"doc": "d", "thread_id": "c1", "post_id": "p"},
            )
