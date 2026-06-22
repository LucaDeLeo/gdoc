"""Tests for the revisions API wrapper (exportLinks download path)."""

from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.revisions import _EXPORT_TIMEOUT, export_revision, list_revisions
from gdoc.util import AuthError, GdocError

LINKS = {
    "text/markdown": "https://example.test/x.md",
    "text/plain": "https://example.test/x.txt",
}


def _response(status=200, text="body"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


@patch("gdoc.api.revisions._get_session")
class TestExportRevision:
    def test_downloads_with_timeout(self, mock_session):
        mock_session.return_value.get.return_value = _response()
        content = export_revision("doc", "5", export_links=LINKS)
        assert content == "body"
        call = mock_session.return_value.get.call_args
        assert call.args == (LINKS["text/markdown"],)
        assert call.kwargs["timeout"] == _EXPORT_TIMEOUT

    def test_403_is_permission_denied_not_auth(self, mock_session):
        # The session auto-refreshes tokens, so a 403 means the export
        # is denied, not that auth expired.
        mock_session.return_value.get.return_value = _response(403)
        with pytest.raises(GdocError, match="Permission denied") as exc_info:
            export_revision("doc", "5", export_links=LINKS)
        assert exc_info.value.exit_code == 1
        assert not isinstance(exc_info.value, AuthError)

    def test_401_is_auth_error(self, mock_session):
        mock_session.return_value.get.return_value = _response(401)
        with pytest.raises(AuthError):
            export_revision("doc", "5", export_links=LINKS)

    def test_404_is_pruned_error(self, mock_session):
        mock_session.return_value.get.return_value = _response(404)
        with pytest.raises(GdocError, match="pruned") as exc_info:
            export_revision("doc", "5", export_links=LINKS)
        assert exc_info.value.exit_code == 3

    def test_plain_fallback_warns_on_stderr(self, mock_session, capsys):
        mock_session.return_value.get.return_value = _response()
        export_revision(
            "doc", "5",
            export_links={"text/plain": "https://example.test/x.txt"},
        )
        assert "falling back to text/plain" in capsys.readouterr().err

    def test_no_warning_when_markdown_available(self, mock_session, capsys):
        mock_session.return_value.get.return_value = _response()
        export_revision("doc", "5", export_links=LINKS)
        assert capsys.readouterr().err == ""


@patch("gdoc.api.revisions.get_drive_service")
class TestListRevisions:
    def test_sorts_by_modified_time(self, mock_service):
        # Drive documents no ordering guarantee; the selector grammar
        # depends on oldest-first.
        execute = (
            mock_service.return_value.revisions.return_value
            .list.return_value.execute
        )
        execute.return_value = {
            "revisions": [
                {"id": "9", "modifiedTime": "2026-06-10T00:00:00.000Z"},
                {"id": "2", "modifiedTime": "2026-06-01T00:00:00.000Z"},
                {"id": "5", "modifiedTime": "2026-06-05T00:00:00.000Z"},
            ],
        }
        revisions = list_revisions("doc")
        assert [r["id"] for r in revisions] == ["2", "5", "9"]
