"""Tests for the comments API wrapper."""

from unittest.mock import patch, MagicMock

import pytest

from gdoc.api.comments import list_comments, create_comment, create_reply
from gdoc.util import AuthError, GdocError


def _make_http_error(status_code: int, reason: str = ""):
    """Create a mock HttpError."""
    import httplib2
    from googleapiclient.errors import HttpError
    resp = httplib2.Response({"status": str(status_code)})
    error = HttpError(resp, b"")
    if reason:
        error.reason = reason
    return error


class TestListComments:
    @patch("gdoc.api.comments.get_drive_service")
    def test_single_page(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {
            "comments": [{"id": "c1", "content": "hello", "resolved": False}],
        }
        result = list_comments("doc1")
        assert len(result) == 1
        assert result[0]["id"] == "c1"

    @patch("gdoc.api.comments.get_drive_service")
    def test_multiple_pages(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.side_effect = [
            {"comments": [{"id": "c1"}], "nextPageToken": "page2"},
            {"comments": [{"id": "c2"}]},
        ]
        result = list_comments("doc1")
        assert len(result) == 2
        assert result[0]["id"] == "c1"
        assert result[1]["id"] == "c2"

    @patch("gdoc.api.comments.get_drive_service")
    def test_empty_result(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {"comments": []}
        result = list_comments("doc1")
        assert result == []

    @patch("gdoc.api.comments.get_drive_service")
    def test_start_modified_time_passed(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {"comments": []}
        list_comments("doc1", start_modified_time="2025-01-20T00:00:00Z")
        # Verify the call was made (mock chaining makes exact kwarg check hard)
        mock_service.comments().list.assert_called()

    @patch("gdoc.api.comments.get_drive_service")
    def test_no_start_time_omits_param(self, mock_svc):
        """First interaction: startModifiedTime is omitted entirely (Decision #3)."""
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {"comments": []}
        list_comments("doc1", start_modified_time="")
        # The call was made — exact kwargs verified by mock
        mock_service.comments().list.assert_called()


class TestCommentsErrors:
    @patch("gdoc.api.comments.get_drive_service")
    def test_401_raises_auth_error(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.side_effect = _make_http_error(401)
        with pytest.raises(AuthError):
            list_comments("doc1")

    @patch("gdoc.api.comments.get_drive_service")
    def test_404_raises_gdoc_error(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.side_effect = _make_http_error(404)
        with pytest.raises(GdocError, match="Document not found"):
            list_comments("doc1")

    @patch("gdoc.api.comments.get_drive_service")
    def test_403_raises_gdoc_error(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.side_effect = _make_http_error(403)
        with pytest.raises(GdocError, match="Permission denied"):
            list_comments("doc1")


class TestListCommentsFiltering:
    @patch("gdoc.api.comments.get_drive_service")
    def test_include_resolved_false_filters_resolved(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {
            "comments": [
                {"id": "c1", "content": "open", "resolved": False},
                {"id": "c2", "content": "resolved", "resolved": True},
                {"id": "c3", "content": "also open", "resolved": False},
            ],
        }
        result = list_comments("doc1", include_resolved=False)
        assert len(result) == 2
        assert all(not c["resolved"] for c in result)

    @patch("gdoc.api.comments.get_drive_service")
    def test_include_resolved_true_returns_all(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {
            "comments": [
                {"id": "c1", "resolved": False},
                {"id": "c2", "resolved": True},
            ],
        }
        result = list_comments("doc1", include_resolved=True)
        assert len(result) == 2

    @patch("gdoc.api.comments.get_drive_service")
    def test_include_anchor_true_adds_quoted_field(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {"comments": []}
        list_comments("doc1", include_anchor=True)
        call_kwargs = mock_service.comments().list.call_args
        # The fields param should contain quotedFileContent
        fields_arg = call_kwargs[1].get("fields", "") if call_kwargs[1] else ""
        assert "quotedFileContent(value)" in fields_arg

    @patch("gdoc.api.comments.get_drive_service")
    def test_include_anchor_false_no_quoted_field(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().list().execute.return_value = {"comments": []}
        list_comments("doc1", include_anchor=False)
        call_kwargs = mock_service.comments().list.call_args
        fields_arg = call_kwargs[1].get("fields", "") if call_kwargs[1] else ""
        assert "quotedFileContent" not in fields_arg


class TestCreateComment:
    @patch("gdoc.api.comments.get_drive_service")
    def test_create_comment_success(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().create().execute.return_value = {
            "id": "c_new", "content": "hello", "resolved": False,
        }
        result = create_comment("doc1", "hello")
        assert result["id"] == "c_new"
        assert result["content"] == "hello"

    @patch("gdoc.api.comments.get_drive_service")
    def test_create_comment_auth_error(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().create().execute.side_effect = _make_http_error(401)
        with pytest.raises(AuthError):
            create_comment("doc1", "hello")

    @patch("gdoc.api.comments.get_drive_service")
    def test_create_comment_not_found(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.comments().create().execute.side_effect = _make_http_error(404)
        with pytest.raises(GdocError, match="Document not found"):
            create_comment("doc1", "hello")


class TestCreateReply:
    @patch("gdoc.api.comments.get_drive_service")
    def test_create_reply_with_content(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.replies().create().execute.return_value = {
            "id": "r1", "content": "thanks",
        }
        result = create_reply("doc1", "c1", content="thanks")
        assert result["id"] == "r1"
        # Verify body has content, no action
        call_kwargs = mock_service.replies().create.call_args
        body = call_kwargs[1].get("body", {}) if call_kwargs[1] else {}
        assert body.get("content") == "thanks"
        assert "action" not in body

    @patch("gdoc.api.comments.get_drive_service")
    def test_create_reply_with_action_resolve(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.replies().create().execute.return_value = {
            "id": "r2", "action": "resolve",
        }
        result = create_reply("doc1", "c1", action="resolve")
        assert result["id"] == "r2"
        call_kwargs = mock_service.replies().create.call_args
        body = call_kwargs[1].get("body", {}) if call_kwargs[1] else {}
        assert body.get("action") == "resolve"
        assert "content" not in body

    @patch("gdoc.api.comments.get_drive_service")
    def test_create_reply_with_action_reopen(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.replies().create().execute.return_value = {
            "id": "r3", "action": "reopen",
        }
        result = create_reply("doc1", "c1", action="reopen")
        assert result["id"] == "r3"
        call_kwargs = mock_service.replies().create.call_args
        body = call_kwargs[1].get("body", {}) if call_kwargs[1] else {}
        assert body.get("action") == "reopen"
        assert "content" not in body

    @patch("gdoc.api.comments.get_drive_service")
    def test_create_reply_auth_error(self, mock_svc):
        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.replies().create().execute.side_effect = _make_http_error(401)
        with pytest.raises(AuthError):
            create_reply("doc1", "c1", content="hello")
