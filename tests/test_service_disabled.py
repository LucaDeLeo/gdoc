"""A Google API disabled in the OAuth client's Cloud project is named as such.

Google answers with HTTP 403 when the API isn't enabled in the project, the
same status as a sharing problem. Every 403 handler must report the disabled
API and its enable link instead of "Permission denied".
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
PROJECT = "123456789012"
SHEETS_URL = (
    "https://console.developers.google.com/apis/api/sheets.googleapis.com/"
    f"overview?project={PROJECT}"
)
SHEETS_MESSAGE = (
    f"Google Sheets API has not been used in project {PROJECT} before or it "
    f"is disabled. Enable it by visiting {SHEETS_URL} then retry. If you "
    "enabled this API recently, wait a few minutes for the action to "
    "propagate to our systems and retry."
)
NOTE = "(if you just enabled it, wait a few minutes and retry)"
EXPECTED = (
    "Google Sheets API is disabled for this OAuth client's Cloud project. "
    f"Enable it: {SHEETS_URL} {NOTE}"
)


def _disabled_body(metadata=True, message=SHEETS_MESSAGE) -> dict:
    """The shape Google returns for SERVICE_DISABLED (captured live)."""
    info = {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "SERVICE_DISABLED",
        "domain": "googleapis.com",
    }
    if metadata:
        info["metadata"] = {
            "serviceTitle": "Google Sheets API",
            "activationUrl": SHEETS_URL,
            "consumer": f"projects/{PROJECT}",
            "service": "sheets.googleapis.com",
            "containerInfo": PROJECT,
        }
    return {"error": {
        "code": 403,
        "message": message,
        "status": "PERMISSION_DENIED",
        "details": [
            info,
            {
                "@type": "type.googleapis.com/google.rpc.LocalizedMessage",
                "locale": "en-US",
                "message": message,
            },
            {
                "@type": "type.googleapis.com/google.rpc.Help",
                "links": [{
                    "description": "Google developers console API activation",
                    "url": SHEETS_URL,
                }],
            },
        ],
    }}


def _legacy_body() -> dict:
    """The older v1 shape: only `errors[].reason`."""
    return {"error": {
        "code": 403,
        "message": SHEETS_MESSAGE,
        "errors": [{
            "message": SHEETS_MESSAGE,
            "domain": "usageLimits",
            "reason": "accessNotConfigured",
            "extendedHelp": "https://console.developers.google.com",
        }],
    }}


_PLAIN_403 = {"error": {
    "code": 403,
    "message": "The caller does not have permission",
    "status": "PERMISSION_DENIED",
}}


def _http_error(status: int, body) -> HttpError:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpError(httplib2.Response({"status": str(status)}), content)


def _response(status: int, body) -> MagicMock:
    """A requests.Response stand-in, as AuthorizedSession returns."""
    resp = MagicMock()
    resp.status_code = status
    resp.content = json.dumps(body).encode()
    resp.text = json.dumps(body)
    resp.reason = "Forbidden"
    resp.json.return_value = body
    return resp


class TestMessage:
    def test_names_api_and_link_from_metadata(self):
        assert service_disabled_message(json.dumps(_disabled_body())) == EXPECTED

    def test_accepts_bytes_and_parsed_json(self):
        body = _disabled_body()
        assert service_disabled_message(json.dumps(body).encode()) == EXPECTED
        assert service_disabled_message(body) == EXPECTED

    def test_falls_back_to_message_text_without_metadata(self):
        body = _disabled_body(metadata=False)
        assert service_disabled_message(body) == EXPECTED

    def test_legacy_access_not_configured(self):
        assert service_disabled_message(_legacy_body()) == EXPECTED

    def test_legacy_reason_found_beside_details(self):
        # googleapiclient keeps only one of details/errors; parse the body.
        body = _legacy_body()
        body["error"]["details"] = [{"@type": "x", "locale": "en-US"}]
        assert service_disabled_message(body) == EXPECTED

    def test_generic_when_nothing_extractable(self):
        body = _disabled_body(metadata=False, message="Forbidden")
        message = service_disabled_message(body)
        assert message == (
            "A Google API this command needs is disabled for this OAuth "
            "client's Cloud project. Enable it in the Google Cloud Console "
            f"under APIs & Services {NOTE}"
        )

    def test_classic_access_not_configured_prefix_is_not_the_name(self):
        drive_url = SHEETS_URL.replace("sheets", "drive")
        body = {"error": {
            "code": 403,
            "message": (
                "Access Not Configured. Drive API has not been used in "
                f"project {PROJECT} before or it is disabled. Enable it by "
                f"visiting {drive_url} then retry."
            ),
            "errors": [{"reason": "accessNotConfigured", "domain": "usageLimits"}],
        }}
        assert service_disabled_message(body) == (
            "Drive API is disabled for this OAuth client's Cloud project. "
            f"Enable it: {drive_url} {NOTE}"
        )

    def test_metadata_from_any_matching_entry(self):
        body = _disabled_body(message="x")
        body["error"]["details"].insert(0, {"reason": "SERVICE_DISABLED"})
        assert service_disabled_message(body) == EXPECTED

    def test_service_name_when_no_title(self):
        body = _disabled_body(message="x")
        del body["error"]["details"][0]["metadata"]["serviceTitle"]
        assert service_disabled_message(body).startswith(
            "sheets.googleapis.com is disabled"
        )

    def test_metadata_is_sanitized(self):
        body = _disabled_body(message="x")
        body["error"]["details"][0]["metadata"].update(
            serviceTitle="Google\nSheets  API", activationUrl="javascript:1",
        )
        assert service_disabled_message(body) == (
            "Google Sheets API is disabled for this OAuth client's Cloud "
            "project. Enable it in the Google Cloud Console under APIs & "
            f"Services {NOTE}"
        )

    def test_url_is_never_last(self):
        # Callers wrap the message in parentheses or end it with a period.
        assert not service_disabled_message(_disabled_body()).endswith(SHEETS_URL)

    def test_unhashable_reason_beside_a_valid_one(self):
        body = _disabled_body()
        body["error"]["details"].insert(0, {"reason": ["x"]})
        body["error"]["errors"] = [{"reason": {"a": 1}}]
        assert service_disabled_message(body) == EXPECTED

    @pytest.mark.parametrize("content", [
        b"", b"forbidden", "not json", None, [], {}, {"error": "denied"},
        {"error": {"details": "x", "errors": None}},
        {"error": {"details": [None, 3, {"reason": "SERVICE_DISABLED",
                                         "metadata": "x"}]}},
        [{"error": {"message": "x"}}],
        {"error": {"details": [{"reason": ["x"]}], "errors": [{"reason": {}}]}},
        b"\xff\xfe",
    ])
    def test_never_raises_on_odd_bodies(self, content):
        result = service_disabled_message(content)
        assert result is None or "disabled for this OAuth client" in result

    @pytest.mark.parametrize("body", [
        _PLAIN_403,
        {"error": {"code": 403, "message": "Rate Limit Exceeded", "errors": [
            {"reason": "rateLimitExceeded", "domain": "usageLimits"}]}},
        {"error": {"code": 403, "message": "x", "details": [
            {"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}},
        # Wording alone never classifies: only the structured reason does.
        {"error": {"code": 403, "message": SHEETS_MESSAGE}},
    ])
    def test_other_403s_are_not_disabled(self, body):
        assert service_disabled_message(body) is None


_TRANSLATORS = [
    pytest.param(drive._translate_http_error, id="drive"),
    pytest.param(docs._translate_http_error, id="docs"),
    pytest.param(sheets._translate_http_error, id="sheets"),
    pytest.param(comments._translate_http_error, id="comments"),
    pytest.param(revisions._translate_http_error, id="revisions"),
]


@pytest.mark.parametrize("translate", _TRANSLATORS)
class TestTranslators:
    def test_service_disabled(self, translate):
        with pytest.raises(GdocError) as exc:
            translate(_http_error(403, _disabled_body()), "file123")
        assert str(exc.value) == EXPECTED
        assert exc.value.exit_code == 1
        assert not isinstance(exc.value, AuthError)

    def test_legacy_access_not_configured(self, translate):
        with pytest.raises(GdocError) as exc:
            translate(_http_error(403, _legacy_body()), "file123")
        assert str(exc.value) == EXPECTED

    @pytest.mark.parametrize("body", [_PLAIN_403, b""])
    def test_plain_403_is_still_permission_denied(self, translate, body):
        with pytest.raises(GdocError) as exc:
            translate(_http_error(403, body), "file123")
        assert str(exc.value) == "Permission denied: file123"


class TestDriveExportReason:
    def test_disabled_checked_before_export_hint(self):
        body = _disabled_body()
        body["error"]["message"] = "Export only supports Docs Editors files"
        with pytest.raises(GdocError, match="disabled for this OAuth"):
            drive._translate_http_error(_http_error(403, body), "file123")


def _docs_service(error: HttpError) -> MagicMock:
    service = MagicMock()
    service.documents.return_value.batchUpdate.return_value.execute.side_effect = (
        error
    )
    return service


class TestDocsSpecialPaths:
    @patch("gdoc.api.docs.get_docs_service")
    def test_insert_comment(self, mock_svc):
        mock_svc.return_value = _docs_service(_http_error(403, _disabled_body()))
        with pytest.raises(GdocError) as exc:
            docs.insert_comment("doc1", "hello", 10, 25)
        assert not isinstance(exc.value, PreviewUnavailableError)
        assert str(exc.value).startswith(EXPECTED)
        assert "no fallback comment was created" in str(exc.value)

    @patch("gdoc.api.docs.get_docs_service")
    def test_thread_request(self, mock_svc):
        mock_svc.return_value = _docs_service(_http_error(403, _disabled_body()))
        with pytest.raises(GdocError) as exc:
            docs._run_thread_request(
                "doc1", {"replyToComment": {"commentId": "c1"}},
            )
        assert str(exc.value) == EXPECTED

    @patch("gdoc.api.docs.get_docs_service")
    def test_thread_request_plain_403_keeps_google_message(self, mock_svc):
        mock_svc.return_value = _docs_service(_http_error(403, _PLAIN_403))
        with pytest.raises(GdocError, match="rejected the request \\(403\\)"):
            docs._run_thread_request(
                "doc1", {"replyToComment": {"commentId": "c1"}},
            )

    def test_suggest_write(self):
        with pytest.raises(GdocError) as exc:
            docs._classify_suggest_error(
                _http_error(403, _disabled_body()), "doc1",
            )
        assert str(exc.value) == EXPECTED

    def test_suggest_write_plain_403(self):
        with pytest.raises(GdocError, match="^Permission denied: doc1"):
            docs._classify_suggest_error(_http_error(403, _PLAIN_403), "doc1")

    @pytest.mark.parametrize("body, expected", [
        (_disabled_body(), EXPECTED),
        (_PLAIN_403, "Permission denied: doc1 (reading suggestions"),
    ])
    def test_suggest_preview_check(self, body, expected):
        with patch("gdoc.api.docs._comments_view_get",
                   return_value=_response(403, body)):
            with pytest.raises(GdocError) as exc:
                docs.check_suggest_preview_access("doc1")
        assert str(exc.value).startswith(expected)

    @pytest.mark.parametrize("body, expected", [
        (_disabled_body(), EXPECTED),
        (_PLAIN_403, "permission denied (403)"),
    ])
    def test_comment_anchors_still_degrade(self, body, expected):
        # cat --comments falls back to quoted text on any 403; the warning
        # now says why.
        with patch("gdoc.api.docs._comments_view_get",
                   return_value=_response(403, body)):
            with pytest.raises(PreviewUnavailableError) as exc:
                docs.get_comment_anchors("doc1")
        assert str(exc.value).startswith(expected)

    @pytest.mark.parametrize("body, expected", [
        (_disabled_body(), EXPECTED),
        (_PLAIN_403, "Permission denied: cannot accept suggestion"),
    ])
    @patch("gdoc.api.docs.get_docs_service")
    def test_decide_suggestion(self, mock_svc, body, expected):
        mock_svc.return_value = _docs_service(_http_error(403, body))
        with pytest.raises(GdocError) as exc:
            docs.decide_suggestion("doc1", "suggest.a", "accept", "rev1")
        assert str(exc.value).startswith(expected)


class TestRevisionExport:
    @pytest.mark.parametrize("body, expected", [
        (_disabled_body(), EXPECTED),
        (_PLAIN_403, "Permission denied: file123"),
    ])
    @patch("gdoc.api.revisions._get_session")
    def test_export_download_403(self, mock_session, body, expected):
        mock_session.return_value.get.return_value = _response(403, body)
        with pytest.raises(GdocError) as exc:
            revisions.export_revision(
                "file123", "7", export_links={"text/markdown": "https://x"},
            )
        assert str(exc.value) == expected


class TestCli:
    def test_suggest_inline_read_gets_no_permission_hint(self):
        """The suggestions-view hint must not blame access for a disabled API."""
        from gdoc.cli import cmd_suggest

        def refuse(*_args, **_kwargs):
            docs._translate_http_error(_http_error(403, _disabled_body()), "abc123")

        args = SimpleNamespace(
            command="suggest", doc="abc123", old_text="hello", new_text="world",
            old_file=None, new_file=None, all=False, case_sensitive=False,
            normalize=False, json=False, verbose=False, plain=False,
            quiet=False, tab=None,
        )
        with patch("gdoc.notify.pre_flight", return_value=None), \
                patch("gdoc.api.docs.get_document_structure", side_effect=refuse):
            with pytest.raises(GdocError) as exc:
                cmd_suggest(args)
        assert str(exc.value) == EXPECTED
        assert "comment or edit access" not in str(exc.value)

    def test_cat_spreadsheet_end_to_end(self, capsys, monkeypatch):
        from gdoc.cli import run_argv

        monkeypatch.setattr(
            "gdoc.cli._file_mime",
            lambda doc_id, change_info: "application/vnd.google-apps.spreadsheet",
        )
        service = MagicMock()
        service.spreadsheets.return_value.get.return_value.execute.side_effect = (
            _http_error(403, _disabled_body())
        )
        with patch("gdoc.api.sheets.get_sheets_service", return_value=service):
            code = run_argv(["cat", "--quiet", "sheet123"], check_updates=False)
        assert code == 1
        err = capsys.readouterr().err
        assert f"ERR: {EXPECTED}" in err
        assert "Permission denied" not in err
