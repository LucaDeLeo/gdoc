"""Tests for anchored comments: insert_comment API + cmd_comment fallback.

The Docs API insertComment request (Workspace Developer Preview) creates a
real anchored comment. Projects not enrolled in the preview — or users with
comment-only access — must transparently fall back to the Drive
quotedFileContent path, so `gdoc comment --quote` works for everyone.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.docs import insert_comment
from gdoc.cli import _try_anchored_comment, cmd_comment
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
    def test_failed_update_state_raises_preview_unavailable(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_response={
            "commentUpdateState": "ALL_FAILED_UNKNOWN_REASON",
            "replies": [{}],
        })
        with pytest.raises(PreviewUnavailableError):
            insert_comment("doc1", "hello", 10, 25)

    @patch("gdoc.api.docs.get_docs_service")
    def test_missing_thread_id_raises_preview_unavailable(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_response={
            "commentUpdateState": "ALL_SAVED",
            "replies": [{"insertComment": {}}],
        })
        with pytest.raises(PreviewUnavailableError):
            insert_comment("doc1", "hello", 10, 25)


_DOC_WITH_TEXT = {
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": 1,
                            "textRun": {"content": "The quick brown fox\n"},
                        }
                    ]
                }
            }
        ]
    }
}


class TestTryAnchoredComment:
    @patch("gdoc.api.docs.insert_comment", return_value="c_anchor")
    @patch("gdoc.api.docs.get_document", return_value=_DOC_WITH_TEXT)
    def test_anchors_to_first_match(self, _get, mock_insert):
        result = _try_anchored_comment("doc1", "note", "quick brown")
        assert result == "c_anchor"
        mock_insert.assert_called_once_with("doc1", "note", 5, 16)

    @patch("gdoc.api.docs.insert_comment")
    @patch("gdoc.api.docs.get_document", return_value=_DOC_WITH_TEXT)
    def test_quote_not_found_returns_empty(self, _get, mock_insert):
        result = _try_anchored_comment("doc1", "note", "missing text")
        assert result == ""
        mock_insert.assert_not_called()

    @patch(
        "gdoc.api.docs.insert_comment",
        side_effect=PreviewUnavailableError("not enrolled"),
    )
    @patch("gdoc.api.docs.get_document", return_value=_DOC_WITH_TEXT)
    def test_preview_unavailable_returns_empty(self, _get, _insert):
        assert _try_anchored_comment("doc1", "note", "quick brown") == ""

    @patch("gdoc.api.docs.insert_comment", return_value="c_anchor")
    @patch("gdoc.api.docs.get_document")
    def test_smart_quote_fallback_match(self, mock_get, mock_insert):
        # Doc has a curly apostrophe; the quote arg has a straight one.
        mock_get.return_value = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": 1,
                                    "textRun": {"content": "it’s fine\n"},
                                }
                            ]
                        }
                    }
                ]
            }
        }
        result = _try_anchored_comment("doc1", "note", "it's fine")
        assert result == "c_anchor"


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


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
class TestCmdCommentAnchored:
    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.cli._try_anchored_comment", return_value="c_anchor")
    def test_anchored_success_skips_drive_path(
        self, mock_try, mock_create, _ver, _pf, _update, capsys,
    ):
        rc = cmd_comment(_make_args(quote="quick brown"))
        assert rc == 0
        mock_try.assert_called_once_with("abc123", "hello", "quick brown")
        mock_create.assert_not_called()
        out = capsys.readouterr().out
        assert "OK comment #c_anchor (anchored)" in out

    @patch(
        "gdoc.api.comments.create_comment", return_value={"id": "c_new"},
    )
    @patch("gdoc.cli._try_anchored_comment", return_value="")
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
        assert "(anchored)" not in out

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
    @patch("gdoc.cli._try_anchored_comment", return_value="c_anchor")
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
    @patch("gdoc.cli._try_anchored_comment", return_value="")
    def test_json_output_anchored_false_on_fallback(
        self, _try, _create, _ver, _pf, _update, capsys,
    ):
        cmd_comment(_make_args(quote="quick brown", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["id"] == "c_new"
        assert data["anchored"] is False

    @patch("gdoc.api.comments.create_comment")
    @patch("gdoc.cli._try_anchored_comment", return_value="c_anchor")
    def test_state_patch_tracks_anchored_id(
        self, _try, _create, _ver, _pf, mock_update, capsys,
    ):
        cmd_comment(_make_args(quote="quick brown"))
        patch_arg = mock_update.call_args.kwargs["comment_state_patch"]
        assert patch_arg == {"add_comment_id": "c_anchor"}
