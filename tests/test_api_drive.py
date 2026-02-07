"""Tests for gdoc.api.drive: Drive API wrapper functions with mocked service."""

import pytest
from unittest.mock import MagicMock, patch

import httplib2
from googleapiclient.errors import HttpError

from gdoc.api.drive import (
    _escape_query_value,
    _translate_http_error,
    export_doc,
    get_file_info,
    list_files,
    search_files,
)
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

    def test_403_non_exportable_file(self):
        err = _make_http_error(
            403,
            reason="Export only supports Docs Editors files",
        )
        with pytest.raises(GdocError, match="Cannot export file as markdown"):
            _translate_http_error(err, "abc123")

    def test_404_raises_gdoc_error(self):
        err = _make_http_error(404)
        with pytest.raises(GdocError, match="Document not found: abc123"):
            _translate_http_error(err, "abc123")

    def test_500_raises_gdoc_error(self):
        err = _make_http_error(500, reason="Internal Server Error")
        with pytest.raises(GdocError, match=r"API error \(500\): Internal Server Error"):
            _translate_http_error(err, "abc123")


class TestEscapeQueryValue:
    def test_no_special_chars(self):
        assert _escape_query_value("hello") == "hello"

    def test_single_quote(self):
        assert _escape_query_value("it's") == "it\\'s"

    def test_backslash(self):
        assert _escape_query_value("a\\b") == "a\\\\b"

    def test_both_backslash_then_quote(self):
        # Input: it\'s  (contains literal backslash and single quote)
        # After backslash escape: it\\'s
        # After quote escape: it\\\'s
        assert _escape_query_value("it\\'s") == "it\\\\\\'s"


@patch("gdoc.api.drive.get_drive_service")
class TestExportDoc:
    def test_export_markdown_default(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export_media().execute.return_value = b"# Hello"

        result = export_doc("abc123")
        assert result == "# Hello"

    def test_export_plain_text(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export_media().execute.return_value = b"Hello"

        result = export_doc("abc123", mime_type="text/plain")
        assert result == "Hello"

    def test_export_not_found(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export_media().execute.side_effect = _make_http_error(404)

        with pytest.raises(GdocError, match="Document not found"):
            export_doc("abc123")

    def test_export_auth_expired(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export_media().execute.side_effect = _make_http_error(401)

        with pytest.raises(AuthError, match="Authentication expired"):
            export_doc("abc123")

    def test_export_permission_denied(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export_media().execute.side_effect = _make_http_error(
            403, reason="forbidden"
        )

        with pytest.raises(GdocError, match="Permission denied"):
            export_doc("abc123")

    def test_export_non_exportable_file(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().export_media().execute.side_effect = _make_http_error(
            403, reason="Export only supports Docs Editors files"
        )

        with pytest.raises(GdocError, match="Cannot export file as markdown"):
            export_doc("abc123")


@patch("gdoc.api.drive.get_drive_service")
class TestListFiles:
    def test_single_page(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "1", "name": "Doc"}],
        }

        result = list_files("name contains 'test'")
        assert result == [{"id": "1", "name": "Doc"}]

    def test_multiple_pages(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().list().execute.side_effect = [
            {
                "files": [{"id": "1", "name": "Doc1"}],
                "nextPageToken": "token2",
            },
            {
                "files": [{"id": "2", "name": "Doc2"}],
            },
        ]

        result = list_files("name contains 'test'")
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["id"] == "2"

    def test_empty_result(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().list().execute.return_value = {"files": []}

        result = list_files("name contains 'nonexistent'")
        assert result == []


@patch("gdoc.api.drive.list_files")
class TestSearchFiles:
    def test_basic_search(self, mock_list_files):
        mock_list_files.return_value = [{"id": "1", "name": "Doc"}]

        result = search_files("hello")

        mock_list_files.assert_called_once()
        query = mock_list_files.call_args[0][0]
        assert "name contains 'hello'" in query
        assert "fullText contains 'hello'" in query
        assert "trashed=false" in query
        assert result == [{"id": "1", "name": "Doc"}]

    def test_escapes_single_quotes(self, mock_list_files):
        mock_list_files.return_value = []

        search_files("it's")

        query = mock_list_files.call_args[0][0]
        assert "it\\'s" in query

    def test_escapes_backslash_first(self, mock_list_files):
        mock_list_files.return_value = []

        search_files("a\\b")

        query = mock_list_files.call_args[0][0]
        assert "a\\\\b" in query


@patch("gdoc.api.drive.get_drive_service")
class TestGetFileInfo:
    def test_returns_metadata(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        metadata = {
            "id": "abc",
            "name": "My Doc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00Z",
            "owners": [{"emailAddress": "user@example.com", "displayName": "User"}],
        }
        mock_service.files().get().execute.return_value = metadata

        result = get_file_info("abc")
        assert result == metadata

    def test_not_found(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().get().execute.side_effect = _make_http_error(404)

        with pytest.raises(GdocError, match="Document not found"):
            get_file_info("abc")
