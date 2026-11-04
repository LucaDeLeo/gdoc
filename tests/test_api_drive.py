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
    update_doc_content,
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

    def test_title_only_search(self, mock_list_files):
        mock_list_files.return_value = []

        search_files("Resume", title_only=True)

        query = mock_list_files.call_args[0][0]
        assert "name contains 'Resume'" in query
        assert "fullText" not in query
        assert "trashed=false" in query

    def test_default_searches_both(self, mock_list_files):
        mock_list_files.return_value = []

        search_files("Resume")

        query = mock_list_files.call_args[0][0]
        assert "name contains 'Resume'" in query
        assert "fullText contains 'Resume'" in query


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


@patch("gdoc.api.docs.get_docs_service", MagicMock())
@patch("gdoc.api.drive.get_drive_service")
class TestUpdateDocContent:
    def test_success(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().update().execute.return_value = {
            "version": "42",
        }

        result = update_doc_content("abc123", "# Hello")
        assert result == 42

    def test_request_params(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().update().execute.return_value = {
            "version": "10",
        }

        update_doc_content("abc123", "# Hello")

        call_kwargs = mock_service.files().update.call_args
        assert call_kwargs is not None
        body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
        assert body["mimeType"] == (
            "application/vnd.google-apps.document"
        )

    def test_http_error_401(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().update().execute.side_effect = (
            _make_http_error(401)
        )

        with pytest.raises(AuthError, match="Authentication expired"):
            update_doc_content("abc123", "content")

    def test_http_error_403(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().update().execute.side_effect = (
            _make_http_error(403, reason="forbidden")
        )

        with pytest.raises(GdocError, match="Permission denied"):
            update_doc_content("abc123", "content")

    def test_http_error_404(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().update().execute.side_effect = (
            _make_http_error(404)
        )

        with pytest.raises(GdocError, match="Document not found"):
            update_doc_content("abc123", "content")


class _FakeGoogleDoc:
    """A Doc whose markdown import resets documentStyle, as Drive's does."""

    IMPORT_DEFAULTS = {
        "documentFormat": {"documentMode": "PAGES"},
        "background": {"color": {}},
        "marginTop": {"magnitude": 72, "unit": "PT"},
    }

    def __init__(self, style):
        self.style = style
        self.version = 10
        self.style_writes = []
        self.fail_style_write = False

        self.docs = MagicMock()
        documents = self.docs.documents.return_value
        documents.get.side_effect = lambda **kwargs: MagicMock(
            execute=lambda: {"documentStyle": dict(self.style)},
        )
        documents.batchUpdate.side_effect = self._batch_update

        self.drive = MagicMock()
        files = self.drive.files.return_value
        files.update.return_value.execute.side_effect = self._import
        files.get.return_value.execute.side_effect = lambda: {
            "version": str(self.version),
        }

    def _import(self):
        self.style = dict(self.IMPORT_DEFAULTS)
        self.version += 1
        return {"version": str(self.version)}

    def _batch_update(self, **kwargs):
        return MagicMock(execute=lambda: self._apply(kwargs["body"]))

    def _apply(self, body):
        if self.fail_style_write:
            raise _make_http_error(500)
        request = body["requests"][0]["updateDocumentStyle"]
        self.style_writes.append(request)
        for name in request["fields"].split(","):
            if name in request["documentStyle"]:
                self.style[name] = request["documentStyle"][name]
            else:
                self.style.pop(name, None)
        self.version += 1
        return {}


@pytest.fixture
def fake_doc():
    """Build a _FakeGoogleDoc wired in as both API services."""
    patches = []

    def build(style):
        doc = _FakeGoogleDoc(style)
        patches.extend([
            patch("gdoc.api.docs.get_docs_service", return_value=doc.docs),
            patch("gdoc.api.drive.get_drive_service", return_value=doc.drive),
        ])
        for started in patches:
            started.start()
        return doc

    yield build
    for started in patches:
        started.stop()


class TestUpdateDocContentKeepsStyle:
    def test_pageless_stays_pageless(self, fake_doc):
        doc = fake_doc({"documentFormat": {"documentMode": "PAGELESS"}})

        update_doc_content("abc123", "# Hello")

        assert doc.style["documentFormat"] == {"documentMode": "PAGELESS"}

    def test_returns_version_after_the_restore(self, fake_doc):
        doc = fake_doc({"documentFormat": {"documentMode": "PAGELESS"}})

        # The import reports 11 and the restore moves the doc to 12; 11
        # would make the next command report our own write as an edit.
        assert update_doc_content("abc123", "# Hello") == 12
        assert doc.version == 12

    def test_restores_other_style_fields(self, fake_doc):
        background = {"color": {"color": {"rgbColor": {"red": 1}}}}
        doc = fake_doc({
            "documentFormat": {"documentMode": "PAGES"},
            "background": background,
            "marginTop": {"magnitude": 20, "unit": "PT"},
            "flipPageOrientation": True,
        })

        update_doc_content("abc123", "# Hello")

        assert doc.style["background"] == background
        assert doc.style["marginTop"] == {"magnitude": 20, "unit": "PT"}
        assert doc.style["flipPageOrientation"] is True
        assert doc.style["documentFormat"] == {"documentMode": "PAGES"}

    def test_field_mask_names_only_what_changed(self, fake_doc):
        doc = fake_doc({
            "documentFormat": {"documentMode": "PAGELESS"},
            "background": {"color": {}},
            "marginTop": {"magnitude": 72, "unit": "PT"},
        })

        update_doc_content("abc123", "# Hello")

        assert [w["fields"] for w in doc.style_writes] == ["documentFormat"]

    def test_field_the_import_set_is_cleared_when_it_was_unset(self, fake_doc):
        doc = fake_doc({"documentFormat": {"documentMode": "PAGELESS"}})

        update_doc_content("abc123", "# Hello")

        assert "marginTop" not in doc.style

    def test_read_only_fields_are_not_written(self, fake_doc):
        doc = fake_doc({
            **_FakeGoogleDoc.IMPORT_DEFAULTS,
            "useCustomHeaderFooterMargins": True,
            "defaultHeaderId": "kix.header",
        })

        update_doc_content("abc123", "# Hello")

        assert doc.style_writes == []

    @pytest.mark.parametrize("background", [
        {"color": {}},
        {"color": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}},
    ])
    def test_unchanged_style_makes_no_extra_write(self, fake_doc, background):
        doc = fake_doc({
            **_FakeGoogleDoc.IMPORT_DEFAULTS,
            "background": background,
        })

        # A default doc keeps the version files.update returned atomically.
        assert update_doc_content("abc123", "# Hello") == 11
        assert doc.style_writes == []

    def test_black_background_is_not_mistaken_for_blank(self, fake_doc):
        black = {"color": {"color": {"rgbColor": {}}}}
        doc = fake_doc({**_FakeGoogleDoc.IMPORT_DEFAULTS, "background": black})

        update_doc_content("abc123", "# Hello")

        assert doc.style["background"] == black

    def test_failed_style_read_writes_nothing(self, fake_doc):
        doc = fake_doc({"documentFormat": {"documentMode": "PAGELESS"}})
        documents = doc.docs.documents.return_value
        documents.get.side_effect = _make_http_error(403)

        with pytest.raises(GdocError, match="Permission denied"):
            update_doc_content("abc123", "# Hello")

        doc.drive.files.return_value.update.assert_not_called()

    def test_failed_restore_warns_and_reports_the_import(
        self, fake_doc, capsys,
    ):
        doc = fake_doc({"documentFormat": {"documentMode": "PAGELESS"}})
        doc.fail_style_write = True

        # The content landed, so the caller must still record that write.
        assert update_doc_content("abc123", "# Hello") == 11
        assert "could not be restored" in capsys.readouterr().err

    def test_failed_version_refresh_falls_back_to_the_import(self, fake_doc):
        doc = fake_doc({"documentFormat": {"documentMode": "PAGELESS"}})
        doc.drive.files.return_value.get.return_value.execute.side_effect = (
            _make_http_error(500)
        )

        assert update_doc_content("abc123", "# Hello") == 11
        assert doc.style["documentFormat"] == {"documentMode": "PAGELESS"}
