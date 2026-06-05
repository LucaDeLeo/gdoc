"""Shared test fixtures. Added as needed."""

import pytest

DOC_MIME = "application/vnd.google-apps.document"


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
