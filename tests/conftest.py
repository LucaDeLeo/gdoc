"""Shared test fixtures. Added as needed."""

import pytest

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


def whole_doc_read_state(version=11):
    """State proving a whole-doc read at `version` (0.22 provenance)."""
    from gdoc.state import DocState

    return DocState(last_read_version=version, global_read_covers_doc=True)
