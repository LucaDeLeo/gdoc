"""Behavioral regressions for the BLOCK review of revision-safe writes."""

import http.client
import io
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.http import HttpRequest

from gdoc.api import docs, drive
from gdoc.cli import cmd_push, cmd_write
from gdoc.notify import ChangeInfo
from gdoc.state import DocState
from gdoc.util import GdocError


def paragraph(text, start=1):
    end = start + len(text.encode("utf-16-le")) // 2
    return {"startIndex": start, "endIndex": end, "paragraph": {"elements": [
        {"startIndex": start, "endIndex": end, "textRun": {"content": text}},
    ]}}


def snapshot(revision="r1", text="Original\n"):
    return {"revisionId": revision, "tabs": [{
        "tabProperties": {"tabId": "t1", "title": "Notes"},
        "documentTab": {"body": {"content": [paragraph(text)]}},
    }]}


def response(revision):
    return {"writeControl": {"requiredRevisionId": revision}}


def rejection():
    from googleapiclient.errors import HttpError
    return HttpError(httplib2.Response({"status": "400"}),
                     b'{"error":{"message":"required revision does not match"}}')


def args_for(tmp_path, quiet=False, force=False, collapse=False):
    path = tmp_path / "body.md"
    path.write_text("---\ngdoc: synthetic\ngdoc-revision: r1\n---\nNew body")
    return SimpleNamespace(doc="synthetic", file=str(path), quiet=quiet, force=force,
                           tab=None, force_collapse_tabs=collapse, json=False,
                           plain=False, verbose=False, allow_lossy=True)


@pytest.mark.parametrize("command", [cmd_write, cmd_push])
@pytest.mark.parametrize("quiet,force", [(False, False), (False, True),
                                        (True, False), (True, True)])
@pytest.mark.parametrize("collapse", [False, True])
def test_first_docs_snapshot_is_the_only_whole_write_authority(
    mocker, tmp_path, command, quiet, force, collapse,
):
    mocker.patch.object(drive, "export_doc", return_value="Original")
    service = MagicMock()
    api = service.documents.return_value
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    reads = mocker.patch.object(docs, "get_document_with_tabs", side_effect=[
        snapshot(), snapshot("editor", "COLLAB Original\n"),
    ])
    mocker.patch("gdoc.notify.pre_flight", return_value=ChangeInfo(
        current_version=10, last_read_version=10))
    mocker.patch.object(drive, "get_file_version", return_value={"version": 10})
    mocker.patch("gdoc.state.load_state", return_value=DocState(
        last_read_version=10, read_revision_ids={"t1": "r1"},
    ))
    state = mocker.patch("gdoc.state.update_state_after_command")

    def execute():
        batch = api.batchUpdate.call_args.kwargs["body"]
        if batch["writeControl"]["requiredRevisionId"] != "editor":
            raise rejection()
        return response("overwrote-editor")

    api.batchUpdate.return_value.execute.side_effect = execute
    with pytest.raises(GdocError, match="conflict") as caught:
        command(args_for(tmp_path, quiet, force, collapse))
    assert caught.value.exit_code == 3
    assert reads.call_count == 1
    assert api.batchUpdate.call_args.kwargs["body"]["writeControl"] == {
        "requiredRevisionId": "r1",
    }
    state.assert_not_called()


@pytest.mark.parametrize("command", [cmd_write, cmd_push])
@pytest.mark.parametrize("quiet", [False, True])
def test_second_write_refuses_unseen_edit_after_first_write(mocker, tmp_path,
                                                           command, quiet):
    state = DocState(last_read_version=10, last_version=10,
                     read_revision_ids={"t1": "r1"})
    version = 10
    mocker.patch("gdoc.state.load_state", side_effect=lambda _: deepcopy(state))

    def save(_, updated):
        nonlocal state
        state = deepcopy(updated)

    mocker.patch("gdoc.state.save_state", side_effect=save)
    mocker.patch("gdoc.notify.pre_flight", side_effect=lambda *a, **k: ChangeInfo(
        current_version=version, last_read_version=state.last_read_version))
    mocker.patch.object(drive, "get_file_version", side_effect=lambda _: {
        "version": version,
    })
    service = MagicMock()
    api = service.documents.return_value
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    mocker.patch.object(docs, "get_document_with_tabs", side_effect=lambda _: (
        snapshot() if version == 10 else snapshot("editor", "COLLAB New body\n")))

    def execute():
        nonlocal version
        # Our acknowledged r2/version 11 is followed by an unseen edit before GET.
        version = 12
        return response("r2")

    api.batchUpdate.return_value.execute.side_effect = execute
    args = args_for(tmp_path, quiet=quiet)
    assert command(args) == 0
    assert state.last_version == 12
    assert state.last_read_version == 10
    assert state.read_revision_ids == {"t1": "r2"}
    with pytest.raises(GdocError, match="changed since"):
        command(args)
    assert api.batchUpdate.call_count == 1


class Socket:
    def __init__(self, data):
        self.data = data

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.data)


class LostResponseConnection:
    """Server applies once; httplib2's hidden retry would receive stale 400."""
    sock = object()
    sends = 0
    applied = 0
    live = "r1"

    def connect(self):
        self.sock = object()

    def close(self):
        self.sock = None

    def request(self, method, uri, body, headers):
        self.sends += 1
        required = json.loads(body).get("writeControl", {}).get("requiredRevisionId")
        if required is None or required == self.live:
            self.applied += 1
            self.live = f"r{self.applied + 1}"
            self.status, self.result = 200, response(self.live)
            self.result["version"] = "11"
        else:
            self.status = 400
            self.result = {"error": {"message": "required revision does not match"}}

    def getresponse(self):
        if self.sends == 1:
            raise http.client.RemoteDisconnected("applied, then response lost")
        body = json.dumps(self.result).encode()
        wire = (f"HTTP/1.1 {self.status} Result\r\nContent-Length: {len(body)}\r\n"
                "Content-Type: application/json\r\n\r\n").encode() + body
        result = http.client.HTTPResponse(Socket(wire))
        result.begin()
        return result


@pytest.mark.parametrize("operation", ["table", "native-rewrite"])
def test_wire_disconnect_cannot_be_hidden_by_retry_400(mocker, operation):
    connection = LostResponseConnection()
    # Preserve the installed _conn_request retry loop and real HttpRequest.
    mocker.patch.object(httplib2.Http, "request", autospec=True,
                        side_effect=lambda self, uri, method, body, headers, **k:
                        self._conn_request(connection, uri, method, body, headers))
    auth = AuthorizedHttp(AnonymousCredentials(), http=httplib2.Http())
    service = MagicMock()

    def build(**kwargs):
        return HttpRequest(auth, lambda r, c: json.loads(c),
                           uri="https://offline.invalid/batch", method="POST",
                           body=json.dumps(kwargs["body"]), headers={})

    retry = MagicMock(side_effect=lambda: ([{"insertTable": {}}], connection.live))
    if operation == "table":
        mocker.patch.object(docs, "get_docs_service", return_value=service)
        service.documents.return_value.batchUpdate.side_effect = build

        def mutate():
            with docs._StagedWrite("synthetic") as progress:
                progress.batch("table structure inserted", [{"insertTable": {}}],
                               "r1", retry)
    else:
        mocker.patch.object(docs, "get_docs_service", return_value=service)
        service.documents.return_value.batchUpdate.side_effect = build
        mocker.patch.object(drive, "get_file_version", return_value={"version": 10})
        doc = snapshot()
        other = deepcopy(doc["tabs"][0])
        other["tabProperties"]["tabId"] = "t2"
        doc["tabs"].append(other)
        mocker.patch.object(docs, "get_document_with_tabs", return_value=doc)

        def mutate():
            drive.update_doc_content("synthetic", "New body", expected_version=10)

    with pytest.raises(GdocError, match="completion uncertain") as caught:
        mutate()
    assert caught.value.exit_code == 1
    assert (connection.sends, connection.applied) == (1, 1)
    retry.assert_not_called()


@pytest.mark.parametrize("markdown,supported", [
    ("![Architecture](https://example.org/chart.png)", True),
    ("![][image1]\n\n[image1]: <data:image/png;base64,AAAA>", False),
    ("![Architecture][chart]\n[chart]: https://example.org/chart.png", True),
    ("![chart]\n[chart]: https://example.org/chart.png", True),
    ('<img src="https://example.org/chart.png">', False),
])
def test_image_markdown_atomic_success_beside_invalid_source_refusal(
    mocker, markdown, supported,
):
    service = MagicMock()
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    mocker.patch.object(docs, "get_document_with_tabs", return_value=snapshot())
    mocker.patch.object(drive, "get_file_version", return_value={"version": 10})
    batch = service.documents.return_value.batchUpdate
    batch.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": "r2"},
    }
    if supported:
        assert drive.update_doc_content(
            "synthetic", markdown, expected_version=10,
        ) == 10
        batch.assert_called_once()
        requests = batch.call_args.kwargs["body"]["requests"]
        assert any("deleteContentRange" in r for r in requests)
        assert any("insertInlineImage" in r for r in requests)
    else:
        with pytest.raises(GdocError, match="image") as caught:
            drive.update_doc_content("synthetic", markdown, expected_version=10)
        assert caught.value.exit_code == 3
        batch.assert_not_called()


def test_extra_section_refuses_without_deleting_section_settings(mocker):
    doc = snapshot()
    doc["tabs"][0]["documentTab"]["body"]["content"].append({
        "startIndex": 10, "endIndex": 11,
        "sectionBreak": {"sectionStyle": {"sectionType": "NEXT_PAGE"}},
    })
    service = MagicMock()
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    mocker.patch.object(docs, "get_document_with_tabs", return_value=doc)
    mocker.patch.object(drive, "get_file_version", return_value={"version": 10})
    # Without loss consent nothing is sent; consent flattens (test_review_round5).
    with pytest.raises(GdocError, match="section boundaries.*--allow-lossy"):
        drive.update_doc_content("synthetic", "New body", expected_version=10)
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.parametrize("applied,exit_code", [([], 3), (["text applied"], 1)])
def test_conflict_status_distinguishes_clean_refusal_from_partial(applied, exit_code):
    with pytest.raises(GdocError, match="conflict") as caught:
        with docs._StagedWrite("synthetic", applied=applied):
            raise rejection()
    assert caught.value.exit_code == exit_code


class TableDocument:
    """Small native-index model: insertTable supplies a leading paragraph mark."""
    def __init__(self, collaborator=False):
        self.text = "Original\n"
        self.revision = "r1"
        self.collaborator = collaborator
        self.batches = []
        self.table_layout = None

    def snapshot(self, *, num_retries=0):
        # PR #64 adds the generated-client retry option to PR #70 staged reads.
        assert num_retries in (0, 2)
        content = []
        text = self.text
        index = 1
        while text:
            if text.startswith("¤" * 8):
                rows = [{"tableCells": [{
                    "content": [paragraph("\n", index + 3 + row * 3)],
                }]} for row in range(2)]
                content.append({"startIndex": index, "endIndex": index + 8,
                                "table": {"tableRows": rows}})
                index += 8
                text = text[8:]
            else:
                end = text.index("\n") + 1
                content.append(paragraph(text[:end], index))
                index += end
                text = text[end:]
        doc = snapshot(self.revision)
        doc["tabs"][0]["documentTab"]["body"]["content"] = content
        return doc

    def batch(self, **kwargs):
        batch = kwargs["body"]
        self.batches.append(batch)

        def execute():
            if batch["writeControl"]["requiredRevisionId"] != self.revision:
                raise rejection()
            for request in batch["requests"]:
                if "deleteContentRange" in request:
                    span = request["deleteContentRange"]["range"]
                    start, end = span["startIndex"] - 1, span["endIndex"] - 1
                    assert end < len(self.text)  # Mandatory final LF survives.
                    self.text = self.text[:start] + self.text[end:]
                elif "insertTable" in request:
                    index = request["insertTable"]["location"]["index"] - 1
                    self.text = self.text[:index] + "\n" + "¤" * 8 + self.text[index:]
                    self.table_layout = self.text
                elif "insertText" in request and self.table_layout is None:
                    insert = request["insertText"]
                    index = insert["location"]["index"] - 1
                    self.text = self.text[:index] + insert["text"] + self.text[index:]
            self.revision = f"r{len(self.batches) + 1}"
            acknowledged = self.revision
            if len(self.batches) == 1 and self.collaborator:
                self.text = "COLLAB note. " + self.text
                self.revision = "editor"
            return response(acknowledged)

        return SimpleNamespace(execute=execute)


@pytest.mark.parametrize("collaborator", [False, True])
@pytest.mark.parametrize("suffix", ["Closing line.", ""])
def test_table_layout_consumes_only_owned_scaffolding(mocker, collaborator, suffix):
    model = TableDocument(collaborator)
    service = MagicMock()
    api = service.documents.return_value
    api.batchUpdate.side_effect = model.batch
    api.get.return_value.execute.side_effect = model.snapshot
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    mocker.patch.object(docs, "get_document_with_tabs",
                        side_effect=lambda _: model.snapshot())
    markdown = "Intro line.\n| Header |\n|---|\n| Value |"
    if suffix:
        markdown += "\n" + suffix
    docs.insert_markdown_into_tab("synthetic", "t1", markdown, replace=True)
    expected = ("COLLAB note. " if collaborator else "") + "Intro line.\n" + "¤" * 8
    # The table splits Intro at its mark; only the emptied mark and the
    # placeholder after the table are then removed.
    assert model.table_layout == expected + "\n" * (2 if suffix else 1) + suffix + "\n"
    assert model.text == expected + suffix + "\n"
    table_batches = [b for b in model.batches
                     if any("insertTable" in r for r in b["requests"])]
    assert len(table_batches) == (2 if collaborator else 1)
    assert all(len(b["requests"]) == 1 for b in table_batches)
    if collaborator:
        before, after = (b["requests"][0]["insertTable"]["location"]["index"]
                         for b in table_batches)
        assert after - before == 13


def test_partial_report_identifies_source_table_and_tab(mocker):
    service = MagicMock()
    api = service.documents.return_value
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    mocker.patch.object(docs, "get_document_with_tabs", return_value=snapshot())
    api.batchUpdate.return_value.execute.side_effect = [response(f"r{i}")
                                                       for i in range(2, 6)]
    model = TableDocument()
    model.text = "First\n\nSecond\n" + "¤" * 8 + "\n\n"
    model.revision = "r3"
    api.get.return_value.execute.side_effect = [model.snapshot(), OSError("lost read")]
    with pytest.raises(GdocError, match="Partial completion") as caught:
        docs.insert_markdown_into_tab(
            "synthetic", "t1", "First\n| A |\n|---|\n| B |\n"
            "Second\n| C |\n|---|\n| D |", replace=True,
        )
    assert caught.value.exit_code == 1
    assert "table 2 in tab t1: table structure inserted" in str(caught.value)
    assert "table 2 in tab t1: table cells filled" in str(caught.value)
    assert "table 1 in tab t1: table structure inserted" in str(caught.value)
    assert "table 1 in tab t1: table cells filled" not in str(caught.value)
