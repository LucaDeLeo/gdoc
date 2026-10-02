"""Offline fault injection for revision-pinned multi-batch writes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.comment_transport import execute_mutation_request
from gdoc.api.docs import insert_markdown_into_tab, replace_formatted
from gdoc.api.drive import update_doc_content
from gdoc.cli import cmd_push, cmd_write, run_argv
from gdoc.mdparse import utf16_len
from gdoc.notify import ChangeInfo
from gdoc.state import DocState, save_state
from gdoc.util import GdocError

MARKDOWN = "Intro\n| Header |\n|---|\n| Value |"


def response(revision):
    return {"writeControl": {"requiredRevisionId": revision}}


def http_error(status=400, message="required revision does not match"):
    return HttpError(
        httplib2.Response({"status": str(status)}),
        ('{"error":{"message":"' + message + '"}}').encode(),
    )


def paragraph(text, start):
    end = start + utf16_len(text)
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "elements": [
                {"startIndex": start, "endIndex": end, "textRun": {"content": text}}
            ],
        },
    }


def snapshot(revision="r1", prefix="Old\n", table=False, trailing=None):
    """A tab body, optionally just after gdoc inserted an empty 2x1 table.

    A table inserted at the preceding paragraph's mark is followed by that
    paragraph's emptied original mark (``trailing`` empty paragraphs), then
    the tab's final paragraph.
    """
    if trailing is None:
        trailing = 1 if prefix else 0
    content = [paragraph(prefix, 1)] if prefix else []
    index = 1 + utf16_len(prefix)
    if table:
        if not prefix:
            content.append(paragraph("\n", index))
        start = index if prefix else index + 1
        rows = []
        for row in range(2):
            cell_start = start + 2 + 3 * row
            rows.append(
                {
                    "tableCells": [
                        {
                            "startIndex": cell_start,
                            "endIndex": cell_start + 2,
                            "content": [paragraph("\n", cell_start + 1)],
                        }
                    ]
                }
            )
        end = start + 8
        content.append(
            {"startIndex": start, "endIndex": end, "table": {"tableRows": rows}}
        )
        for offset in range(trailing + 1):
            content.append(paragraph("\n", end + offset))
    else:
        content.append(paragraph("\n", index))
    return {
        "revisionId": revision,
        "tabs": [
            {
                "tabProperties": {"tabId": "t1", "title": "Notes"},
                "documentTab": {"body": {"content": content}},
            }
        ],
    }


def body(doc):
    return doc["tabs"][0]["documentTab"]["body"]


@pytest.fixture
def api(mocker):
    service = MagicMock()
    docs = service.documents.return_value
    mocker.patch("gdoc.api.docs.get_docs_service", return_value=service)
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=snapshot())
    docs.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        response("r3"),
        response("r4"),
    ]
    docs.get.return_value.execute.return_value = snapshot("r3", "Intro\n", table=True)
    return docs


def batches(api):
    return [c.kwargs["body"] for c in api.batchUpdate.call_args_list]


def write():
    return insert_markdown_into_tab("synthetic", "Notes", MARKDOWN, replace=True)


def test_all_table_stages_use_their_own_revision(api):
    write()
    assert [b["writeControl"] for b in batches(api)] == [
        {"requiredRevisionId": r} for r in ("r1", "r2", "r3")
    ]
    assert "insertTable" in batches(api)[1]["requests"][-1]
    assert {
        r["insertText"]["text"]
        for r in batches(api)[2]["requests"]
        if "insertText" in r
    } == {"Header", "Value"}


def test_replacement_table_uses_response_revision(api):
    replace_formatted(
        "synthetic", [{"startIndex": 1, "endIndex": 4}], MARKDOWN, "r1", tab_id="t1"
    )
    assert [b["writeControl"]["requiredRevisionId"] for b in batches(api)] == [
        "r1",
        "r2",
        "r3",
    ]


def test_table_only_empty_tab_pins_the_first_table_batch(api, mocker):
    mocker.patch(
        "gdoc.api.docs.get_document_with_tabs", return_value=snapshot(prefix="")
    )
    api.get.return_value.execute.return_value = snapshot("r3", "", table=True)
    insert_markdown_into_tab(
        "synthetic", "Notes", "| A |\n|---|\n| B |", position="end"
    )
    assert "insertTable" in batches(api)[1]["requests"][0]
    assert [b["writeControl"]["requiredRevisionId"] for b in batches(api)] == [
        "r1",
        "r2",
        "r3",
    ]


@pytest.mark.parametrize("stage", [0, 1, 2])
@pytest.mark.parametrize("failure", ["before_send", "after_apply"])
def test_faults_do_not_replay_acknowledged_or_uncertain_mutations(api, stage, failure):
    applied = []
    count = 0

    def batch_update(**kwargs):
        nonlocal count
        current = count
        count += 1
        if current == stage and failure == "before_send":
            raise OSError("request construction failed")

        def execute():
            applied.append(current)
            if current == stage:
                raise OSError("response lost after server applied write")
            return response(f"r{current + 2}")

        return SimpleNamespace(execute=execute)

    api.batchUpdate.side_effect = batch_update
    with pytest.raises(GdocError) as caught:
        write()
    assert caught.value.exit_code == 1
    message = str(caught.value)
    assert ("Partial completion" if stage else "Write failed") in message
    assert (
        "completion uncertain" if failure == "after_apply" else "not applied"
    ) in message
    assert "remaining stages not attempted" in message
    assert applied == list(range(stage + (failure == "after_apply")))
    assert count == stage + 1
    if stage:
        assert "applied: tab text and formatting applied" in message
    if stage == 2:
        assert "table structure inserted" in message


def test_readback_failure_reports_applied_table_without_claiming_cells(api):
    api.get.return_value.execute.side_effect = OSError("read disconnected")
    with pytest.raises(GdocError, match="Partial completion") as caught:
        write()
    assert caught.value.exit_code == 1
    assert "table structure inserted" in str(caught.value)
    assert "reading inserted table cells: not applied" in str(caught.value)
    assert len(batches(api)) == 2


def test_missing_response_revision_refuses_unpinned_followup(api):
    api.batchUpdate.return_value.execute.side_effect = [{}]
    with pytest.raises(GdocError, match="missing revision") as caught:
        write()
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 1


def test_interleaved_editor_before_table_rejects_then_recomputes_utf16_index(api):
    live_revision = "r1"
    sent_indices = []
    applied = []

    def execute():
        nonlocal live_revision
        batch = batches(api)[-1]
        required = batch["writeControl"]["requiredRevisionId"]
        if "insertTable" in batch["requests"][-1]:
            sent_indices.append(
                batch["requests"][-1]["insertTable"]["location"]["index"]
            )
        if required != live_revision:
            raise http_error()
        applied.append(batch)
        if len(applied) == 1:
            # Our main batch applies as r2; another editor then prepends text.
            live_revision = "editor"
            return response("r2")
        live_revision = f"r{len(applied) + 2}"
        return response(live_revision)

    api.batchUpdate.return_value.execute.side_effect = execute
    api.get.return_value.execute.side_effect = [
        snapshot("editor", "🙂 Added\nIntro\n"),
        snapshot("r4", "🙂 Added\nIntro\n", table=True),
    ]
    write()
    assert sent_indices == [6, 15]
    assert [b["writeControl"]["requiredRevisionId"] for b in batches(api)] == [
        "r1",
        "r2",
        "editor",
        "r4",
    ]
    assert len(applied) == 3
    assert api.get.call_count == 2


@pytest.mark.parametrize("prefix", ["Intro\n\nIntro\n", "Changed\n", "Intro\nAdded"])
def test_ambiguous_or_changed_insertion_anchor_refuses_after_one_read(api, prefix):
    api.batchUpdate.return_value.execute.side_effect = [response("r2"), http_error()]
    api.get.return_value.execute.return_value = snapshot("editor", prefix)
    with pytest.raises(GdocError, match="cannot uniquely relocate") as caught:
        write()
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 2
    assert api.get.call_count == 1


def test_table_only_conflict_is_not_guessed(api):
    api.batchUpdate.return_value.execute.side_effect = [response("r2"), http_error()]
    api.get.return_value.execute.return_value = snapshot("editor", "Other editor\n")
    with pytest.raises(GdocError, match="conflict") as caught:
        insert_markdown_into_tab(
            "synthetic", "Notes", "| A |\n|---|\n| B |", replace=True
        )
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 2
    assert api.get.call_count == 1


def test_second_revision_rejection_stops_without_replaying_first_batch(api):
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        http_error(),
        http_error(),
    ]
    api.get.return_value.execute.return_value = snapshot("editor", "Added\nIntro\n")
    with pytest.raises(GdocError, match="conflict") as caught:
        write()
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 3
    assert api.get.call_count == 1


def test_interleaved_editor_before_fill_relocates_unchanged_table(api):
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        response("r3"),
        http_error(),
        response("r5"),
    ]
    api.get.return_value.execute.side_effect = [
        snapshot("r3", "Intro\n", table=True),
        snapshot("editor", "Added\nIntro\n", table=True),
    ]
    write()
    old, new = batches(api)[2:]
    assert old["writeControl"] == {"requiredRevisionId": "r3"}
    assert new["writeControl"] == {"requiredRevisionId": "editor"}
    old_indices = [
        r["insertText"]["location"]["index"]
        for r in old["requests"]
        if "insertText" in r
    ]
    new_indices = [
        r["insertText"]["location"]["index"]
        for r in new["requests"]
        if "insertText" in r
    ]
    assert new_indices == [i + 6 for i in old_indices]
    assert api.get.call_count == 2


def test_relocated_fill_never_removes_a_collaborators_paragraph(api):
    """Text a collaborator types after the table is never scaffolding."""
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        response("r3"),
        http_error(),
    ]
    edited = snapshot("editor", "Intro\n", table=True)
    content = body(edited)["content"]
    content[2] = paragraph("Note\n", content[2]["startIndex"])
    content[3] = paragraph("\n", content[2]["endIndex"])
    api.get.return_value.execute.side_effect = [
        snapshot("r3", "Intro\n", table=True), edited,
    ]
    with pytest.raises(GdocError, match="after the inserted table changed"):
        write()
    assert len(batches(api)) == 3  # The relocated fill was never sent.


@pytest.mark.parametrize(
    "change", ["cell_edited", "duplicate_table", "deleted_table", "tab_deleted"]
)
def test_fill_recovery_refuses_changed_or_ambiguous_table(api, change):
    original = snapshot("r3", "Intro\n", table=True)
    changed = deepcopy(original)
    changed["revisionId"] = "editor"
    table = next(e for e in body(changed)["content"] if "table" in e)
    if change == "cell_edited":
        table["table"]["tableRows"][0]["tableCells"][0]["content"][0] = paragraph(
            "Editor\n", 11
        )
    elif change == "duplicate_table":
        body(changed)["content"].append(deepcopy(table))
    elif change == "deleted_table":
        body(changed)["content"].remove(table)
    else:
        changed["tabs"][0]["tabProperties"]["tabId"] = "different-id"
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        response("r3"),
        http_error(),
    ]
    api.get.return_value.execute.side_effect = [original, changed]
    with pytest.raises(GdocError, match="conflict") as caught:
        write()
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 3
    assert api.get.call_count == 2


def test_editor_between_table_creation_and_readback_is_not_adopted(api):
    api.get.return_value.execute.return_value = snapshot(
        "editor", "Added\nIntro\n", table=True
    )
    with pytest.raises(GdocError, match="changed before the inserted table") as caught:
        write()
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 2


def test_nonrevision_400_is_not_retried(api):
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        http_error(message="Invalid JSON payload: required_revision_id"),
    ]
    with pytest.raises(GdocError) as caught:
        write()
    assert caught.value.exit_code == 1
    assert len(batches(api)) == 2
    api.get.assert_not_called()


def test_cli_prints_partial_completion_and_exits_one(api, mocker, capsys):
    mocker.patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(
            current_version=10,
            last_read_version=10,
        ),
    )
    mocker.patch(
        "gdoc.api.docs.get_document",
        return_value={
            "revisionId": "r1",
            "body": {},
        },
    )
    mocker.patch(
        "gdoc.api.docs.find_text_in_document",
        return_value=[
            {"startIndex": 1, "endIndex": 4},
        ],
    )
    mocker.patch(
        "gdoc.api.drive.get_file_version",
        return_value={
            "version": 10,
            "mimeType": "application/vnd.google-apps.document",
        },
    )
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        OSError("lost response"),
    ]
    assert (
        run_argv(["edit", "synthetic", "Old", MARKDOWN, "--quiet"], check_updates=False)
        == 1
    )
    out, err = capsys.readouterr()
    assert "Partial completion" in err and "completion uncertain" in err
    assert "OK" not in out and "OK" not in err


@pytest.fixture
def drive_api(mocker):
    save_state("synthetic", DocState(last_read_version=10,
                                      read_revision_ids={"t1": "r1"}))
    service = MagicMock()
    mocker.patch("gdoc.api.drive.get_drive_service", return_value=service)
    version = mocker.patch(
        "gdoc.api.drive.get_file_version", return_value={"version": 10}
    )
    read = mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=snapshot())
    return service.files.return_value, version, read


def test_single_tab_whole_write_uses_guard_snapshot_and_never_uploads(api, drive_api):
    files, version, read = drive_api
    version.side_effect = [{"version": 10}, {"version": 11}]
    assert update_doc_content("synthetic", "New body", expected_version=10) == 11
    files.update.assert_not_called()
    read.assert_called_once_with("synthetic")
    assert batches(api)[0]["writeControl"] == {"requiredRevisionId": "r1"}
    assert len(batches(api)) == 1


def test_single_tab_editor_after_last_version_read_is_caught_atomically(api, drive_api):
    files, _, _ = drive_api
    api.batchUpdate.return_value.execute.side_effect = http_error()
    with pytest.raises(GdocError, match="conflict") as caught:
        update_doc_content("synthetic", "New body", expected_version=10)
    assert caught.value.exit_code == 3
    assert len(batches(api)) == 1
    assert batches(api)[0]["writeControl"] == {"requiredRevisionId": "r1"}
    files.update.assert_not_called()


@pytest.mark.parametrize("command", ["write", "push"])
@pytest.mark.parametrize(
    "quiet,force", [(False, False), (False, True), (True, False), (True, True)]
)
def test_cli_carries_pre_guard_version_even_when_forced(
    api,
    drive_api,
    mocker,
    tmp_path,
    command,
    quiet,
    force,
):
    files, version, _ = drive_api
    mocker.patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(
            current_version=10,
            last_read_version=10,
        ),
    )
    mocker.patch(
        "gdoc.state.load_state",
        return_value=DocState(last_read_version=10, read_revision_ids={"t1": "r1"}),
    )
    update_state = mocker.patch("gdoc.state.update_state_after_command")
    mocker.patch("gdoc.api.docs.count_document_tabs", return_value=1)
    # The collaborator changes content after preflight and before the write.
    version.side_effect = [{"version": 11}]
    path = tmp_path / "body.md"
    path.write_text("---\ngdoc: synthetic\ngdoc-revision: r1\n---\nNew body")
    args = SimpleNamespace(
        doc="synthetic",
        file=str(path),
        quiet=quiet,
        force=force,
        tab=None,
        force_collapse_tabs=False,
        allow_lossy=True,
        json=False,
        plain=False,
        verbose=False,
    )
    with pytest.raises(GdocError, match="document changed") as caught:
        (cmd_write if command == "write" else cmd_push)(args)
    assert caught.value.exit_code == 3
    api.batchUpdate.assert_not_called()
    files.update.assert_not_called()
    update_state.assert_not_called()


def multi_tab(read):
    document = snapshot()
    other = deepcopy(document["tabs"][0])
    other["tabProperties"] = {"tabId": "t2", "title": "Other"}
    document["tabs"].append(other)
    read.return_value = document


def test_native_collapse_checks_version_and_pins_each_batch(api, drive_api):
    files, version, read = drive_api
    multi_tab(read)
    version.side_effect = [{"version": 10}, {"version": 11}]
    assert update_doc_content("synthetic", "New body", expected_version=10,
                              collapse_tabs=True) == 11
    assert [batch["writeControl"] for batch in batches(api)] == [
        {"requiredRevisionId": "r1"}, {"requiredRevisionId": "r2"},
    ]
    assert batches(api)[1]["requests"] == [{"deleteTab": {"tabId": "t2"}}]
    files.update.assert_not_called()


@pytest.mark.parametrize("current", [11, None])
def test_drive_collapse_refuses_changed_or_missing_version(drive_api, current):
    files, version, read = drive_api
    multi_tab(read)
    version.return_value = {"version": current}
    with pytest.raises(GdocError, match="content was not uploaded") as caught:
        update_doc_content("synthetic", "New body", expected_version=10)
    assert caught.value.exit_code == 3
    files.update.return_value.execute.assert_not_called()


def test_native_collapse_lost_response_is_uncertain_and_not_retried(api, drive_api):
    files, _, read = drive_api
    multi_tab(read)
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"), OSError("response lost"),
    ]
    with pytest.raises(
        GdocError, match="authorized sibling tabs removed: completion uncertain",
    ) as caught:
        update_doc_content("synthetic", "New body", expected_version=10,
                           collapse_tabs=True)
    assert caught.value.exit_code == 1
    assert "first tab content replaced" in str(caught.value)
    assert api.batchUpdate.call_count == 2
    files.update.assert_not_called()


def test_native_write_version_read_failure_reports_known_completion(api, drive_api):
    files, version, _ = drive_api
    version.side_effect = [{"version": 10}, OSError("version read failed")]
    details = {}
    assert update_doc_content(
        "synthetic", "New body", expected_version=10, result_details=details,
    ) is None
    assert details["acknowledged_revision_id"]
    assert len(batches(api)) == 1
    files.update.assert_not_called()


def test_api_without_preflight_captures_version_before_document_read(api, drive_api):
    files, version, read = drive_api
    events = []
    version.side_effect = lambda doc_id: events.append("version") or {"version": 10}
    read.side_effect = lambda doc_id: events.append("document") or snapshot()
    update_doc_content("synthetic", "New body")
    assert events[:3] == ["version", "document", "version"]
    files.update.assert_not_called()


def test_two_tables_carry_revision_from_previous_cell_fill(api):
    markdown = "First\n| A |\n|---|\n| B |\nSecond\n| C |\n|---|\n| D |"
    api.batchUpdate.return_value.execute.side_effect = [
        response(f"r{i}") for i in range(2, 7)
    ]
    api.get.return_value.execute.side_effect = [
        snapshot("r3", "First\n\nSecond\n", table=True),
        # The first table also leaves its placeholder before "Second".
        snapshot("r5", "First\n", table=True, trailing=2),
    ]
    insert_markdown_into_tab("synthetic", "Notes", markdown, replace=True)
    assert [b["writeControl"]["requiredRevisionId"] for b in batches(api)] == [
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
    ]
    assert [
        b["requests"][-1]["insertTable"]["location"]["index"]
        for b in batches(api)
        if "insertTable" in b["requests"][-1]
    ] == [14, 6]


def test_conflict_reread_failure_refuses_without_retry(api):
    api.batchUpdate.return_value.execute.side_effect = [response("r2"), http_error()]
    api.get.return_value.execute.side_effect = OSError("read failed")
    with pytest.raises(GdocError, match="conflict: cannot safely recompute") as caught:
        write()
    assert caught.value.exit_code == 1
    assert api.get.call_count == 1
    assert len(batches(api)) == 2


def test_fill_retry_second_conflict_stops(api):
    api.batchUpdate.return_value.execute.side_effect = [
        response("r2"),
        response("r3"),
        http_error(),
        http_error(),
    ]
    api.get.return_value.execute.side_effect = [
        snapshot("r3", "Intro\n", table=True),
        snapshot("editor", "Added\nIntro\n", table=True),
    ]
    with pytest.raises(GdocError, match="conflict") as caught:
        write()
    assert caught.value.exit_code == 1
    assert api.get.call_count == 2
    assert len(batches(api)) == 4


def transport_request(execute):
    """A request whose execute stands in for AuthorizedHttp's pre-send work."""
    http = SimpleNamespace(
        http=SimpleNamespace(timeout=5), credentials=object(), _request=None
    )
    return SimpleNamespace(http=http, execute=lambda **kwargs: execute())


def test_transport_refresh_failure_before_send_is_auth_error():
    from google.auth.exceptions import RefreshError

    from gdoc.util import AuthError

    sent = []

    def execute():
        raise RefreshError("invalid_grant: Token has been revoked")

    with pytest.raises(AuthError, match="Run `gdoc auth`") as caught:
        execute_mutation_request(transport_request(execute), on_send=sent.append)
    assert caught.value.exit_code == 2
    assert sent == []


def test_transport_network_failure_before_send_is_not_uncertain():
    from google.auth.exceptions import TransportError

    def execute():
        raise TransportError("dns failure")

    with pytest.raises(GdocError, match="the write was not sent") as caught:
        execute_mutation_request(transport_request(execute))
    assert caught.value.exit_code == 1
    assert "uncertain" not in str(caught.value)


def test_transport_lost_response_stays_uncertain():
    from unittest.mock import MagicMock

    from googleapiclient.http import HttpRequest

    def execute(**kwargs):
        kwargs["http"].http._sent = True
        raise OSError("response lost")

    request = MagicMock(spec=HttpRequest)
    request.http = MagicMock()
    request.execute.side_effect = execute
    with pytest.raises(GdocError, match="uncertain"):
        execute_mutation_request(request)


def test_staged_batch_refresh_failure_keeps_auth_exit_code(api, mocker):
    from gdoc.util import AuthError

    mocker.patch(
        "gdoc.api.comment_transport.execute_mutation_request",
        side_effect=AuthError("Authentication expired. Run `gdoc auth`."),
    )
    with pytest.raises(AuthError, match="Run `gdoc auth`") as caught:
        write()
    assert caught.value.exit_code == 2
    assert len(batches(api)) == 1


def test_staged_batch_refresh_failure_after_apply_is_not_uncertain(api, mocker):
    from gdoc.api import comment_transport
    from gdoc.util import AuthError

    real = comment_transport.execute_mutation_request
    calls = []

    def dispatch(request, **kwargs):
        calls.append(request)
        if len(calls) == 2:
            raise AuthError("Authentication expired. Run `gdoc auth`.")
        return real(request, **kwargs)

    mocker.patch.object(comment_transport, "execute_mutation_request", dispatch)
    with pytest.raises(GdocError, match="Partial completion") as caught:
        write()
    assert caught.value.exit_code == 1
    message = str(caught.value)
    assert "not applied" in message and "completion uncertain" not in message
    assert "Run `gdoc auth`" in message
    assert len(batches(api)) == 2


@pytest.mark.parametrize("markdown", ["Plain prose", "Line one\nLine two", ""])
def test_replacement_clears_bullet_inherited_from_final_paragraph(
    api, mocker, markdown
):
    """Whole-body replacement keeps the final mark, so its bullet must go."""
    doc = snapshot()
    body(doc)["content"][-1]["paragraph"]["bullet"] = {"listId": "kix.list"}
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    # The retained mark is an empty list item, requiring explicit loss consent.
    insert_markdown_into_tab(
        "synthetic", "Notes", markdown, replace=True, allow_lossy=True
    )
    requests = batches(api)[0]["requests"]
    kinds = [next(iter(r)) for r in requests]
    resets = [r["deleteParagraphBullets"]["range"] for r in requests
              if "deleteParagraphBullets" in r]
    assert resets == [
        {"startIndex": 1, "endIndex": 2, "tabId": "t1"}
    ]
    assert kinds[0] == "deleteContentRange"
    if markdown:
        assert kinds[1:5] == ["deleteParagraphBullets", "updateParagraphStyle",
                              "updateTextStyle", "insertText"]
    assert not any("createParagraphBullets" in r for r in requests)


def test_replacement_without_inherited_bullet_resets_retained_mark(api):
    insert_markdown_into_tab("synthetic", "Notes", "Plain prose", replace=True)
    requests = batches(api)[0]["requests"]
    assert [r["deleteParagraphBullets"]["range"] for r in requests
            if "deleteParagraphBullets" in r] == [
                {"startIndex": 1, "endIndex": 2, "tabId": "t1"}]


def test_replacement_ignores_bullet_on_deleted_first_paragraph(api, mocker):
    """Only the retained final paragraph can pass its bullet on."""
    doc = snapshot()
    body(doc)["content"][0]["paragraph"]["bullet"] = {"listId": "kix.list"}
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    insert_markdown_into_tab("synthetic", "Notes", "Plain prose", replace=True)
    requests = batches(api)[0]["requests"]
    assert [r["deleteParagraphBullets"]["range"] for r in requests
            if "deleteParagraphBullets" in r] == [
                {"startIndex": 1, "endIndex": 2, "tabId": "t1"}]


def tab_write_args(path, **overrides):
    args = dict(
        doc="synthetic", file=str(path), quiet=False, force=False, tab="Notes",
        force_collapse_tabs=False, json=False, plain=False, verbose=False,
    )
    args.update(overrides)
    return SimpleNamespace(**args)


@pytest.mark.parametrize(
    "quiet,force", [(False, False), (False, True), (True, False), (True, True)]
)
def test_tab_write_foreign_snapshot_requires_explicit_force(
    api, drive_api, mocker, tmp_path, quiet, force,
):
    """A new snapshot needs consent; even consent cannot bypass revision pins."""
    _, _, read = drive_api
    read.return_value = snapshot("editor", "Foreign words\n")
    mocker.patch("gdoc.notify.pre_flight", return_value=ChangeInfo(
        current_version=11, last_read_version=10,
    ))
    update_state = mocker.patch("gdoc.state.update_state_after_command")
    path = tmp_path / "body.md"
    path.write_text("New body")
    if force:
        assert cmd_write(tab_write_args(path, quiet=quiet, force=True)) == 0
        assert batches(api)[0]["writeControl"] == {"requiredRevisionId": "editor"}
        update_state.assert_called_once()
    else:
        with pytest.raises(GdocError, match="changed since last read") as caught:
            cmd_write(tab_write_args(path, quiet=quiet))
        assert caught.value.exit_code == 3
        api.batchUpdate.assert_not_called()
        update_state.assert_not_called()
    read.assert_called_once_with("synthetic")


def test_tab_write_pins_the_guarded_snapshot(api, drive_api, mocker, tmp_path):
    """The checked snapshot is the one the replacement is pinned to."""
    _, version, read = drive_api
    mocker.patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(current_version=10, last_read_version=10),
    )
    mocker.patch("gdoc.state.update_state_after_command")
    version.side_effect = [{"version": 10}, {"version": 11}]
    path = tmp_path / "body.md"
    path.write_text("New body")
    assert cmd_write(tab_write_args(path)) == 0
    read.assert_called_once_with("synthetic")
    assert batches(api)[0]["writeControl"] == {"requiredRevisionId": "r1"}


def test_empty_replacement_normalizes_retained_heading(api, mocker):
    """An empty body write must not leave the final mark as a heading."""
    doc = snapshot()
    body(doc)["content"][-1]["paragraph"]["paragraphStyle"] = {
        "namedStyleType": "HEADING_1", "indentStart": {"magnitude": 36, "unit": "PT"}
    }
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    insert_markdown_into_tab("synthetic", "Notes", "", replace=True)
    requests = batches(api)[0]["requests"]
    styles = [r["updateParagraphStyle"] for r in requests
              if "updateParagraphStyle" in r]
    assert styles == [{
        "range": {"startIndex": 1, "endIndex": 2, "tabId": "t1"},
        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
        "fields": "*",
    }]
    assert [next(iter(r)) for r in requests] == [
        "deleteContentRange", "deleteParagraphBullets", "updateParagraphStyle",
        "updateTextStyle"
    ]


def test_prose_replacement_resets_then_styles_retained_paragraph(api, mocker):
    """#65 resets the retained mark before styling newly inserted prose."""
    doc = snapshot()
    body(doc)["content"][-1]["paragraph"]["paragraphStyle"] = {
        "namedStyleType": "HEADING_1"
    }
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    insert_markdown_into_tab("synthetic", "Notes", "Plain prose", replace=True)
    requests = batches(api)[0]["requests"]
    styles = [r["updateParagraphStyle"] for r in requests
              if "updateParagraphStyle" in r]
    assert len(styles) == 2  # #65 resets before insertion, then styles the prose.
    assert styles[0]["paragraphStyle"] == {"namedStyleType": "NORMAL_TEXT"}
    assert styles[0]["range"]["startIndex"] == 1
    assert styles[1]["range"]["endIndex"] >= 1 + utf16_len("Plain prose")


@pytest.mark.parametrize("allow_lossy", [False, True])
def test_single_tab_whole_write_passes_loss_consent(mocker, drive_api, allow_lossy):
    """--allow-lossy reaches the native single-tab replacement."""
    files, _, _ = drive_api
    insert = mocker.patch("gdoc.api.docs.insert_markdown_into_tab")
    update_doc_content(
        "synthetic", "New body", expected_version=10, allow_lossy=allow_lossy
    )
    insert.assert_called_once()
    assert insert.call_args.kwargs["allow_lossy"] is allow_lossy
    assert insert.call_args.kwargs["replace"] is True
    files.update.assert_not_called()


@pytest.mark.parametrize("command", ["write", "push", "write-tab"])
@pytest.mark.parametrize("allow_lossy", [False, True])
@pytest.mark.parametrize("markdown,hazard", [
    ("42. item", True),
    ("0. item", True),
    ("1. parent\n    42. item", True),
    ("- parent\n    42. item", True),
    ("1. parent\n    1. child\n2. parent\n    42. item", True),
    ("1. earlier\n\n42. item", True),
    ("1. item\n2. next", False),
    ("1. parent\n    1. child\n    2. next\n2. parent", False),
    ("```\n42. item\n```", False),
    (r"42\. item", False),
])
def test_native_replacement_guards_incoming_list_starts(
    api, drive_api, mocker, tmp_path, capsys, command, allow_lossy, markdown, hazard,
):
    files, _, _ = drive_api
    mocker.patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(current_version=10, last_read_version=10),
    )
    update_state = mocker.patch("gdoc.state.update_state_after_command")
    path = tmp_path / "body.md"
    path.write_text("---\ngdoc: synthetic\ngdoc-revision: r1\n---\n" + markdown)
    args = tab_write_args(
        path, tab="Notes" if command == "write-tab" else None,
        allow_lossy=allow_lossy,
    )
    handler = cmd_push if command == "push" else cmd_write
    assert handler(args) == 0
    assert len(batches(api)) == 1
    err = capsys.readouterr().err
    assert ("WARN:" in err) is hazard
    if hazard:
        assert "numbered list" in err and "will start at 1" in err
        assert any("createParagraphBullets" in r for r in batches(api)[0]["requests"])
    update_state.assert_called_once()
    files.update.assert_not_called()
