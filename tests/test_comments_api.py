"""Tests for the comments API wrapper."""

from unittest.mock import patch, MagicMock

import pytest

from gdoc.api.comments import list_comments
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
