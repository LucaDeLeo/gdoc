"""A Google API disabled in the OAuth client's Cloud project is named as such.

Google answers 403 both for a disabled API and for a file the caller can't
access. Every 403 handler must name the disabled API and its enable link
instead of "Permission denied", and leave other 403s alone.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api import comments, docs, drive, revisions, sheets
from gdoc.api.errors import service_disabled_message
from gdoc.util import AuthError, GdocError, PreviewUnavailableError

# Synthetic: no real project or file identifiers.
URL = ("https://console.developers.google.com/apis/api/sheets.googleapis.com/"
       "overview?project=123456789012")
GOOGLE_MESSAGE = (
    "Google Sheets API has not been used in project 123456789012 before or it "
    f"is disabled. Enable it by visiting {URL} then retry."
)
NOTE = "(if you just enabled it, wait a few minutes and retry)"
EXPECTED = ("Google Sheets API is disabled for this OAuth client's Cloud "
            f"project. Enable it: {URL} {NOTE}")

# The shape Google returns (captured live), trimmed to what gdoc reads.
DISABLED = {"error": {
    "code": 403, "message": GOOGLE_MESSAGE, "status": "PERMISSION_DENIED",
    "details": [{
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "SERVICE_DISABLED", "domain": "googleapis.com",
        "metadata": {"serviceTitle": "Google Sheets API", "activationUrl": URL},
    }],
}}
LEGACY = {"error": {"code": 403, "message": GOOGLE_MESSAGE, "errors": [
    {"reason": "accessNotConfigured", "domain": "usageLimits"}]}}
PLAIN = {"error": {"code": 403, "message": "The caller does not have permission",
                   "status": "PERMISSION_DENIED"}}


def _http_error(body) -> HttpError:
    return HttpError(httplib2.Response({"status": "403"}), json.dumps(body).encode())


def _response(body) -> MagicMock:
    """A requests.Response stand-in, as AuthorizedSession returns."""
    resp = MagicMock(status_code=403, content=json.dumps(body).encode(),
                     text=json.dumps(body), reason="Forbidden")
    resp.json.return_value = body
    return resp


class TestMessage:
    def test_names_api_and_link_from_metadata(self):
        assert service_disabled_message(json.dumps(DISABLED).encode()) == EXPECTED

    def test_legacy_reason_keeps_googles_message(self):
        assert service_disabled_message(LEGACY) == (
            "A Google API is disabled for this OAuth client's Cloud project. "
            f"Google said: {GOOGLE_MESSAGE} {NOTE}"
        )

    def test_generic_without_metadata_or_message(self):
        body = {"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}
        assert service_disabled_message(body).startswith(
            "A Google API this command needs is disabled"
        )

    @pytest.mark.parametrize("body", [
        PLAIN,
        {"error": {"message": "Rate Limit Exceeded",
                   "errors": [{"reason": "rateLimitExceeded"}]}},
        {"error": {"message": GOOGLE_MESSAGE}},  # wording alone never counts
        b"", b"not json", b"\xff", {"error": "x"},
        {"error": {"details": [{"reason": ["unhashable"]}]}},
    ])
    def test_other_and_malformed_bodies_are_not_disabled(self, body):
        assert service_disabled_message(body) is None


@pytest.mark.parametrize("translate", [
    drive._translate_http_error, docs._translate_http_error,
    sheets._translate_http_error, comments._translate_http_error,
    revisions._translate_http_error,
], ids=["drive", "docs", "sheets", "comments", "revisions"])
@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "Permission denied: file123"),
])
def test_translators(translate, body, expected):
    with pytest.raises(GdocError) as exc:
        translate(_http_error(body), "file123")
    assert str(exc.value) == expected
    assert exc.value.exit_code == 1
    assert not isinstance(exc.value, AuthError)


def _docs_service(body) -> MagicMock:
    service = MagicMock()
    batch = service.documents.return_value.batchUpdate.return_value
    batch.execute.side_effect = _http_error(body)
    return service


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED + " (no fallback comment was created)"),
    (PLAIN, "insertComment not permitted"),
])
@patch("gdoc.api.docs.get_docs_service")
def test_insert_comment(mock_svc, body, expected):
    mock_svc.return_value = _docs_service(body)
    with pytest.raises(GdocError) as exc:
        docs.insert_comment("doc1", "hello", 10, 25)
    assert str(exc.value).startswith(expected)
    # A disabled API never permits the Drive-comment fallback.
    assert isinstance(exc.value, PreviewUnavailableError) == (body is PLAIN)


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "Docs API rejected the request (403)"),
])
@patch("gdoc.api.docs.get_docs_service")
def test_thread_request(mock_svc, body, expected):
    mock_svc.return_value = _docs_service(body)
    with pytest.raises(GdocError) as exc:
        docs._run_thread_request("doc1", {"replyToComment": {"commentId": "c1"}})
    assert str(exc.value).startswith(expected)


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "Permission denied: doc1 (suggesting"),
])
def test_suggest_write(body, expected):
    with pytest.raises(GdocError) as exc:
        docs._classify_suggest_error(_http_error(body), "doc1")
    assert str(exc.value).startswith(expected)


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "Permission denied: doc1 (reading suggestions"),
])
def test_suggest_preview_check(body, expected):
    with patch("gdoc.api.docs._comments_view_get", return_value=_response(body)):
        with pytest.raises(GdocError) as exc:
            docs.check_suggest_preview_access("doc1")
    assert str(exc.value).startswith(expected)


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "permission denied (403)"),
])
def test_comment_anchors_still_degrade(body, expected):
    # cat --comments falls back to quoted text (WARN, exit 0) on any 403 here;
    # the warning now says why.
    with patch("gdoc.api.docs._comments_view_get", return_value=_response(body)):
        with pytest.raises(PreviewUnavailableError) as exc:
            docs.get_comment_anchors("doc1")
    assert str(exc.value).startswith(expected)


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "Permission denied: cannot accept suggestion"),
])
@patch("gdoc.api.docs.get_docs_service")
def test_decide_suggestion(mock_svc, body, expected):
    mock_svc.return_value = _docs_service(body)
    with pytest.raises(GdocError) as exc:
        docs.decide_suggestion("doc1", "suggest.a", "accept", "rev1")
    assert str(exc.value).startswith(expected)


@pytest.mark.parametrize("body, expected", [
    (DISABLED, EXPECTED), (PLAIN, "Permission denied: file123"),
])
@patch("gdoc.api.revisions._get_session")
def test_revision_export_download(mock_session, body, expected):
    mock_session.return_value.get.return_value = _response(body)
    with pytest.raises(GdocError) as exc:
        revisions.export_revision(
            "file123", "7", export_links={"text/markdown": "https://x"},
        )
    assert str(exc.value) == expected


def test_suggest_gets_no_access_hint_for_a_disabled_api():
    """cli.py appends "needs comment or edit access" only to Permission denied."""
    from gdoc.cli import cmd_suggest

    def refuse(*_args, **_kwargs):
        docs._translate_http_error(_http_error(DISABLED), "abc123")

    args = SimpleNamespace(
        command="suggest", doc="abc123", old_text="hello", new_text="world",
        old_file=None, new_file=None, all=False, case_sensitive=False,
        normalize=False, json=False, verbose=False, plain=False, quiet=False,
        tab=None,
    )
    with patch("gdoc.notify.pre_flight", return_value=None), \
            patch("gdoc.api.docs.get_document_structure", side_effect=refuse):
        with pytest.raises(GdocError) as exc:
            cmd_suggest(args)
    assert str(exc.value) == EXPECTED


@pytest.mark.parametrize("argv", [
    ["cat", "--quiet", "sheet123"],
    ["tabs", "--quiet", "sheet123"],
    ["info", "--quiet", "sheet123"],
    ["cells", "--quiet", "sheet123", "A1", "-v", "1"],
])
def test_sheet_commands_end_to_end(capsys, monkeypatch, argv):
    from gdoc.cli import run_argv

    sheet = "application/vnd.google-apps.spreadsheet"
    monkeypatch.setattr("gdoc.cli._file_mime", lambda doc_id, change_info: sheet)
    monkeypatch.setattr("gdoc.api.drive.get_file_info",
                        lambda doc_id: {"mimeType": sheet})
    service = MagicMock()
    service.spreadsheets.side_effect = _http_error(DISABLED)
    with patch("gdoc.api.sheets.get_sheets_service", return_value=service):
        assert run_argv(argv, check_updates=False) == 1
    err = capsys.readouterr().err
    assert f"ERR: {EXPECTED}" in err
    assert "Permission denied" not in err
