"""Shared test fixtures. Added as needed."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--acceptance-results", help="Write offline task diagnostics as JSON"
    )


DOC_MIME = "application/vnd.google-apps.document"

_AUTH_ENV_VARS = [
    "GDOC_CLIENT_ID",
    "GDOC_CLIENT_SECRET",
    "GDOC_CLIENT_CREDENTIALS",
    "GDOC_SETUP_URL",
    "GDOC_AUTH_DOMAIN",
]


@pytest.fixture(autouse=True)
def _isolate_auth_env(monkeypatch):
    """Keep developer-machine GDOC_* auth env vars out of the test suite."""
    for var in _AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def doc_mime(monkeypatch):
    """Pin pre-flight mime detection to a plain Google Doc.

    Patches the Drive boundary that gdoc.cli._file_mime falls back to when
    pre_flight is mocked away, keeping spreadsheet routing on the Docs path.
    """
    monkeypatch.setattr(
        "gdoc.api.drive.get_file_version",
        lambda doc_id: {"mimeType": DOC_MIME, "version": 1, "modifiedTime": ""},
    )


@pytest.fixture(autouse=True)
def _mock_mutation_transport(monkeypatch):
    """Keep API-shape mocks offline; real HttpRequests exercise real transport."""
    from googleapiclient.http import HttpRequest

    from gdoc.api import comment_transport

    execute = comment_transport.execute_mutation_request

    def dispatch(request, **kwargs):
        if isinstance(request, HttpRequest):
            return execute(request, **kwargs)
        if kwargs.get("on_send") is not None:
            kwargs["on_send"]()  # A mocked execute stands for the wire send.
        return request.execute()

    monkeypatch.setattr(comment_transport, "execute_mutation_request", dispatch)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch, tmp_path):
    """Unit tests must never read credentials or mutate live documents by accident."""
    import socket

    monkeypatch.setattr("gdoc.state.STATE_DIR", tmp_path / "state")

    def denied(*args, **kwargs):
        raise AssertionError("Network is prohibited in the offline test suite")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
def doc_with_tabs(*pairs):
    """A get_document_with_tabs() response for the given (id, title) pairs.

    Shared by the insert/write/baseline test modules: the payload encodes
    the API contract (tabProperties.tabId, documentTab.body), so its shape
    lives in one place.
    """
    return {
        "revisionId": "rev1",
        "tabs": [
            {
                "tabProperties": {"tabId": tid, "title": title, "index": i},
                "documentTab": {"body": {"content": []}},
            }
            for i, (tid, title) in enumerate(pairs)
        ],
    }


def tab_read_state(*tab_ids, revision="rev1"):
    """State recording a complete read of each tab at native *revision*.

    Read coverage is per tab at an exact Docs revision (see
    gdoc.state.record_content_read); a whole-document read covers every tab.
    """
    from gdoc.state import DocState

    return DocState(read_revision_ids={tid: revision for tid in tab_ids})
