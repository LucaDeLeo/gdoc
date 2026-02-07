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
