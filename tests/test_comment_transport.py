"""Offline wire-level regressions for httplib2's hidden POST replay."""

import io
import json
import socket
from http.client import HTTPResponse, RemoteDisconnected, ResponseNotReady
from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.http import HttpRequest

from gdoc.api.comments import create_comment
from gdoc.cli import run_argv


@pytest.fixture
def wire(mocker):
    mocker.patch.object(
        socket.socket, "connect", side_effect=AssertionError("Network denied")
    )
    saved = []

    class Connection:
        sock = object()
        count = 0
        responses = []

        def __new__(cls, *args, **kwargs):
            if hasattr(cls, "instance"):
                return cls.instance
            return super().__new__(cls)

        def __init__(self, *args, **kwargs):
            pass

        def set_debuglevel(self, level):
            pass

        def connect(self):
            self.sock = object()

        def close(self):
            self.sock = None

        def request(self, method, uri, body, headers):
            self.count += 1
            saved.append("saved")

        def getresponse(self):
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            status, payload = response
            content = (
                payload if isinstance(payload, bytes)
                else json.dumps(payload).encode()
            )
            raw = (
                (f"HTTP/1.1 {status} Result\r\n"
                 f"Content-Length: {len(content)}\r\n\r\n").encode()
                + content
            )

            class Socket:
                def makefile(self, *args):
                    return io.BytesIO(raw)

            result = HTTPResponse(Socket())
            result.begin()
            return result

    connection = Connection()
    Connection.instance = connection
    mocker.patch.dict(httplib2.SCHEME_TO_CONNECTION, {"https": Connection})
    auth = AuthorizedHttp(AnonymousCredentials(), http=httplib2.Http(timeout=30))

    def request(**kwargs):
        return HttpRequest(
            auth,
            lambda response, content: json.loads(content),
            "https://offline.invalid/batchUpdate",
            method="POST",
            body=json.dumps(kwargs["body"]),
            headers={},
        )

    return connection, saved, request


@pytest.mark.parametrize(
    "rejection",
    [
        (403, {"error": {"message": "Caller no longer has batchUpdate permission"}}),
        (400, {"error": {"message": "The provided revision ID is stale"}}),
    ],
)
@pytest.mark.parametrize("lost", [RemoteDisconnected, ResponseNotReady])
def test_lost_anchor_response_never_replays_or_falls_back(
    wire, mocker, capsys, rejection, lost
):
    connection, saved, request = wire
    connection.responses = [lost("response lost after save"), rejection]
    service = MagicMock()
    service.documents.return_value.batchUpdate.side_effect = request
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)
    read = mocker.patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value={
            "revisionId": "r1",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"startIndex": 1, "textRun": {"content": "echo\n"}},
                            ]
                        }
                    }
                ]
            },
        },
    )
    fallback = mocker.patch("gdoc.api.comments.create_comment")
    mocker.patch("gdoc.notify.pre_flight")
    assert (
        run_argv(
            ["comment", "synthetic", "note", "--quote", "echo", "--quiet", "--json"],
            check_updates=False,
        )
        == 1
    )
    assert "outcome is uncertain" in capsys.readouterr().err
    assert saved == ["saved"]
    assert connection.count == 1
    assert connection.responses == [rejection]
    read.assert_called_once()
    service.documents.return_value.batchUpdate.assert_called_once()
    fallback.assert_not_called()


def test_drive_comment_is_also_single_send(wire, mocker, capsys):
    connection, saved, request = wire
    connection.responses = [RemoteDisconnected("lost"), (200, {"id": "duplicate"})]
    service = MagicMock()
    service.comments.return_value.create.side_effect = request
    mocker.patch("gdoc.api.comments.get_drive_service", return_value=service)
    mocker.patch("gdoc.notify.pre_flight")
    assert (
        run_argv(["comment", "synthetic", "note", "--quiet"], check_updates=False) == 1
    )
    assert "outcome is uncertain" in capsys.readouterr().err
    assert saved == ["saved"]
    assert connection.count == 1


def test_definite_auth_rejection_does_not_resend(wire, mocker, capsys):
    connection, saved, request = wire
    connection.responses = [(401, {"error": {"message": "expired"}})]
    service = MagicMock()
    service.comments.return_value.create.side_effect = request
    mocker.patch("gdoc.api.comments.get_drive_service", return_value=service)
    mocker.patch("gdoc.notify.pre_flight")
    assert (
        run_argv(["comment", "synthetic", "note", "--quiet"], check_updates=False) == 2
    )
    assert connection.count == len(saved) == 1


def test_successful_drive_requests_have_separate_send_budgets(wire, mocker):
    connection, saved, request = wire
    connection.responses = [(200, {"id": "one"}), (200, {"id": "two"})]
    service = MagicMock()
    service.comments.return_value.create.side_effect = request
    mocker.patch("gdoc.api.comments.get_drive_service", return_value=service)
    assert create_comment("synthetic", "one")["id"] == "one"
    assert create_comment("synthetic", "two")["id"] == "two"
    assert connection.count == len(saved) == 2


def test_token_refresh_does_not_consume_comment_send_budget(wire, mocker):
    connection, saved, request = wire
    connection.responses = [(200, {"id": "one"})]
    pending = request(body={"content": "one"})
    refresh = mocker.patch.object(
        pending.http.http,
        "request",
        return_value=(httplib2.Response({"status": "200"}), b"token"),
    )

    def before_request(refresh_request, method, url, headers):
        refresh_request("https://offline.invalid/token", method="POST")

    mocker.patch.object(
        pending.http.credentials,
        "before_request",
        side_effect=before_request,
    )
    service = MagicMock()
    service.comments.return_value.create.return_value = pending
    mocker.patch("gdoc.api.comments.get_drive_service", return_value=service)
    assert create_comment("synthetic", "one")["id"] == "one"
    refresh.assert_called_once()
    assert connection.count == len(saved) == 1


@pytest.mark.parametrize(
    "response",
    [
        (503, {"error": {"message": "temporarily unavailable"}}),
        (200, {}),
        (200, b""),
        (200, b"<html>502 Bad Gateway</html>"),
        (200, b"null"),
        (200, b"[]"),
    ],
)
def test_drive_uncertain_response_requires_inspection(wire, mocker, capsys, response):
    connection, saved, request = wire
    connection.responses = [response]
    service = MagicMock()
    service.comments.return_value.create.side_effect = request
    mocker.patch("gdoc.api.comments.get_drive_service", return_value=service)
    mocker.patch("gdoc.notify.pre_flight")
    assert (
        run_argv(
            ["comment", "synthetic", "note", "--quiet"],
            check_updates=False,
        )
        == 1
    )
    assert "inspect" in capsys.readouterr().err.lower()
    assert connection.count == len(saved) == 1


@pytest.mark.parametrize("body", [
    b"", b"<html>502 Bad Gateway</html>", b"null", b"[]",
])
def test_unparseable_anchor_response_is_uncertain_not_replayed(
    wire, mocker, capsys, body,
):
    connection, saved, request = wire
    connection.responses = [(200, body)]
    service = MagicMock()
    service.documents.return_value.batchUpdate.side_effect = request
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)
    mocker.patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value={
            "revisionId": "r1",
            "body": {"content": [{"paragraph": {"elements": [
                {"startIndex": 1, "textRun": {"content": "echo\n"}},
            ]}}]},
        },
    )
    fallback = mocker.patch("gdoc.api.comments.create_comment")
    mocker.patch("gdoc.notify.pre_flight")
    assert (
        run_argv(
            ["comment", "synthetic", "note", "--quote", "echo", "--quiet", "--json"],
            check_updates=False,
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "outcome is uncertain" in err and "inspect" in err.lower()
    assert connection.count == len(saved) == 1
    fallback.assert_not_called()
