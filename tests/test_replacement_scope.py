"""Offline replacements across independent tab, header and footnote indexes."""

import json
from types import SimpleNamespace

import pytest

from gdoc.api.docs import find_text_in_document, replace_formatted, suggest_replacement
from gdoc.cli import build_parser, cmd_edit, cmd_suggest
from gdoc.util import GdocError


def _content(start=1, style=None, implicit_zero=False):
    run = {"startIndex": start, "endIndex": start + 5,
           "textRun": {"content": "TOKEN", "textStyle": style or {}}}
    element = {"startIndex": start, "endIndex": start + 6, "paragraph": {
        "elements": [run, {"startIndex": start + 5, "endIndex": start + 6,
                          "textRun": {"content": "\n", "textStyle": {}}}],
        "paragraphStyle": {"namedStyleType": "HEADING_2"},
    }}
    if implicit_zero:
        del run["startIndex"]
        del element["startIndex"]
    return {"content": [element]}


def _document():
    first = {"tabProperties": {"tabId": "first", "title": "Overview"},
             "documentTab": {
                 "body": _content(style={"bold": True}),
                 "headers": {"shared-header": _content(0, {"italic": True}, True)},
                 "footers": {"footer": _content(0, {"underline": True}, True)},
                 "footnotes": {"note": _content(0, {"strikethrough": True}, True)},
             }}
    second = {"tabProperties": {"tabId": "second", "title": "Appendix"},
              "documentTab": {
                  "body": _content(style={"underline": True}),
                  "headers": {"shared-header": _content(0, {"bold": True}, True)},
              }}
    # Child tabs must participate just like top-level tabs.
    first["childTabs"] = [second]
    return {"revisionId": "revision-source", "tabs": [first]}


def _args(**overrides):
    args = dict(doc="doc-one", old_text="TOKEN", new_text="REPLACED", all=True,
                quiet=True, tab=None, json=False, plain=False, verbose=False)
    return SimpleNamespace(**(args | overrides))


@pytest.fixture
def services(mocker):
    document = _document()
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=document)
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    mocker.patch("gdoc.state.update_state_after_command")
    mocker.patch("gdoc.api.docs.check_suggest_preview_access")
    mocker.patch("gdoc.api.docs._token_identity", return_value=("client", "token"))
    readback = _document()
    run = readback["tabs"][0]["documentTab"]["body"]["content"][0]
    run["paragraph"]["elements"][0]["textRun"]["suggestedInsertionIds"] = ["s.new"]
    mocker.patch("gdoc.api.docs.get_document_structure",
                 side_effect=[document, readback])
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "commentUpdateState": "ALL_SAVED",
        "suggestionResponses": [{"createdSuggestionIds": ["s.new"]}],
    }
    return document, service.documents.return_value.batchUpdate


def _address(request):
    operation = next(iter(request.values()))
    address = operation.get("range", operation.get("location"))
    return address.get("tabId"), address.get("segmentId")


@pytest.mark.parametrize("output", ["terse", "json", "plain"])
def test_edit_all_reaches_every_container_and_reports_tab_counts(
    services, capsys, output,
):
    _, batch = services
    assert cmd_edit(_args(**{output: True})) == 0
    batch.assert_called_once()
    payload = batch.call_args.kwargs["body"]
    assert payload["writeControl"] == {"requiredRevisionId": "revision-source"}
    requests = payload["requests"]
    expected = {("first", None): {"bold": True},
                ("first", "shared-header"): {"italic": True},
                ("first", "footer"): {"underline": True},
                ("first", "note"): {"strikethrough": True},
                ("second", None): {"underline": True},
                ("second", "shared-header"): {"bold": True}}
    # Same numerical offsets and even shared segment IDs never mix style contexts.
    assert {_address(r): r["updateTextStyle"]["textStyle"]
            for r in requests
            if r.get("updateTextStyle", {}).get("textStyle")} == expected
    for operation in ("deleteContentRange", "insertText"):
        assert {_address(r) for r in requests if operation in r} == set(expected)
    assert not any("updateParagraphStyle" in r for r in requests)
    out = capsys.readouterr().out
    if output == "json":
        assert json.loads(out) == {"ok": True, "replaced": 6,
                                   "tabs": {"first": 4, "second": 2}}
    elif output == "plain":
        assert "tab\tfirst\t4\ntab\tsecond\t2\n" in out
    else:
        assert "OK replaced 6 occurrences\n  tab first: 4\n  tab second: 2\n" == out


@pytest.mark.parametrize("command", [cmd_edit, cmd_suggest])
def test_unscoped_ambiguity_and_explicit_tab_are_enforced(services, command):
    _, batch = services
    with pytest.raises(GdocError, match="multiple matches.*--tab") as error:
        command(_args(all=False))
    assert error.value.exit_code == 3
    for label in ("Overview (first)", "Appendix (second)"):
        assert label in str(error.value)
    batch.assert_not_called()


@pytest.mark.parametrize("command", [cmd_edit, cmd_suggest])
def test_same_tab_ambiguity_requires_all_or_more_specific_text(services, command):
    _, batch = services
    with pytest.raises(GdocError, match="multiple matches.*Use --all") as error:
        command(_args(all=False, tab="Overview"))
    assert error.value.exit_code == 3
    assert "--tab" not in str(error.value)
    assert "more specific text" in str(error.value)
    batch.assert_not_called()


@pytest.mark.parametrize("command", [cmd_edit, cmd_suggest])
def test_explicit_tab_only_writes_that_tab(services, command):
    _, batch = services
    assert command(_args(tab="Appendix")) == 0
    requests = batch.call_args.kwargs["body"]["requests"]
    assert {_address(r)[0] for r in requests} == {"second"}
    assert {_address(r)[1] for r in requests} == {None, "shared-header"}


def test_suggest_implicit_zero_segments_keep_addresses_in_suggest_batch(services):
    _, batch = services
    assert cmd_suggest(_args(new_text="**REPLACED**")) == 0
    batch.assert_called_once()
    payload = batch.call_args.kwargs["body"]
    assert payload["writeControl"] == {
        "requiredRevisionId": "revision-source", "writeMode": "SUGGEST",
    }
    requests = payload["requests"]
    expected = {(m.get("tabId"), m.get("segmentId"))
                for m in find_text_in_document(_document(), "TOKEN")}
    for operation in ("deleteContentRange", "insertText", "updateTextStyle"):
        assert {_address(r) for r in requests if operation in r} == expected
    for request in requests:
        if "deleteContentRange" in request:
            address = request["deleteContentRange"]["range"]
            assert address["startIndex"] == (0 if address.get("segmentId") else 1)
    assert not any("updateParagraphStyle" in r for r in requests)


@pytest.mark.parametrize("replace", [replace_formatted, suggest_replacement])
def test_later_container_planning_error_precedes_every_write(services, replace):
    document, batch = services
    matches = find_text_in_document(document, "TOKEN")
    matches[-1]["segmentId"] = "missing-segment"
    with pytest.raises(GdocError, match="container not found") as error:
        replace("doc-one", matches, "REPLACED", "revision-source", body=document)
    assert error.value.exit_code == 3
    batch.assert_not_called()


@pytest.mark.parametrize("replace", [replace_formatted, suggest_replacement])
def test_request_builder_error_on_later_match_precedes_write(services, mocker, replace):
    from gdoc.mdparse import to_docs_requests

    document, batch = services
    calls = []

    def fail_later(*args, **kwargs):
        calls.append(args)
        if len(calls) == 3:
            raise GdocError("synthetic planning failure", exit_code=3)
        return to_docs_requests(*args, **kwargs)

    mocker.patch("gdoc.mdparse.to_docs_requests", side_effect=fail_later)
    with pytest.raises(GdocError, match="synthetic planning failure"):
        replace("doc-one", find_text_in_document(document, "TOKEN"),
                "REPLACED", "revision-source", body=document)
    assert len(calls) == 3
    batch.assert_not_called()


def test_later_tab_suggestion_overlap_blocks_whole_batch(services):
    document, batch = services
    later = document["tabs"][0]["childTabs"][0]["documentTab"]
    run = later["headers"]["shared-header"]["content"][0]["paragraph"]["elements"][0]
    run["textRun"]["suggestedInsertionIds"] = ["s.existing"]
    with pytest.raises(GdocError, match="overlaps existing"):
        cmd_suggest(_args())
    batch.assert_not_called()


def test_empty_replacement_does_not_merge_independent_ranges(services):
    _, batch = services
    assert cmd_edit(_args(new_text="")) == 0
    requests = batch.call_args.kwargs["body"]["requests"]
    assert len(requests) == 6
    assert all("deleteContentRange" in r for r in requests)
    assert len({_address(r) for r in requests}) == 6


@pytest.mark.parametrize("segment", ["headers", "footers", "footnotes"])
@pytest.mark.parametrize("implicit_zero", [False, True])
@pytest.mark.parametrize("last", [False, True])
def test_empty_segment_paragraph_removes_its_mark_except_final_lf(
    services, segment, implicit_zero, last,
):
    _, batch = services
    first = _content(0, implicit_zero=implicit_zero)["content"][0]
    second = _content(6)["content"][0]
    content = [first, second]
    scope = {"id": "tab", "body": {}, segment: {"segment": {"content": content}}}
    start, end = (6, 11) if last else (0, 5)
    # Fixed coordinates keep this planner regression independent of the matcher.
    match = {"startIndex": start, "endIndex": end,
             "tabId": "tab", "segmentId": "segment"}
    replace_formatted("doc-one", [match], "", "revision-source", body=scope)
    requests = batch.call_args.kwargs["body"]["requests"]
    expected_start, expected_end = (5, 11) if last else (0, 6)
    # A non-final paragraph is emptied, then its one empty paragraph removed.
    ends = [expected_end] if last else [5, 1]
    assert requests == [{"deleteContentRange": {"range": {
        "startIndex": expected_start, "endIndex": end,
        "tabId": "tab", "segmentId": "segment",
    }}} for end in ends]
    text = "TOKEN\nTOKEN\n"
    assert text[:expected_start] + text[expected_end:] == "TOKEN\n"


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_help_states_scope_and_ambiguity_rule(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([command, "--help"])
    assert exit_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "every tab" in help_text
    assert "require --all for multiple matches" in help_text


@pytest.mark.parametrize("command", [cmd_edit, cmd_suggest])
def test_unique_match_in_later_tab_does_not_require_all(services, command):
    document, batch = services
    first = document["tabs"][0]
    first["documentTab"] = {"body": {"content": []}}
    second = first["childTabs"][0]["documentTab"]
    second.pop("headers")
    assert command(_args(all=False)) == 0
    requests = batch.call_args.kwargs["body"]["requests"]
    assert {_address(r) for r in requests} == {("second", None)}


def test_suggest_preserves_each_containers_direct_style(services):
    document, batch = services
    assert cmd_suggest(_args()) == 0
    requests = batch.call_args.kwargs["body"]["requests"]
    expected = {
        ("first", None): {"bold": True},
        ("first", "shared-header"): {"italic": True},
        ("first", "footer"): {"underline": True},
        ("first", "note"): {"strikethrough": True},
        ("second", None): {"underline": True},
        ("second", "shared-header"): {"bold": True},
    }
    assert len(requests) == 12
    assert {_address(r) for r in requests} == set(expected)
    first = document["tabs"][0]
    tabs = {"first": first["documentTab"],
            "second": first["childTabs"][0]["documentTab"]}
    for insertion, deletion in zip(requests[::2], requests[1::2]):
        address = _address(insertion)
        assert _address(deletion) == address
        insert = insertion["insertText"]
        deleted = deletion["deleteContentRange"]["range"]
        assert insert["text"] == "REPLACED"
        # Insert at the target end before deleting it, inheriting its direct style.
        assert insert["location"]["index"] == deleted["endIndex"]
        tab, segment = address
        scope = tabs[tab]
        content = (scope["body"] if segment is None else next(
            scope[k][segment] for k in ("headers", "footers", "footnotes")
            if segment in scope.get(k, {})))
        run = content["content"][0]["paragraph"]["elements"][0]
        assert deleted["startIndex"] == run.get("startIndex", 0)
        assert deleted["endIndex"] == run["endIndex"]
        assert run["textRun"]["textStyle"] == expected[address]


@pytest.mark.parametrize("tab_id", ["first", "second"])
def test_suggest_table_markdown_uses_matches_own_tab(services, tab_id):
    document, batch = services
    match = {"tabId": tab_id, "container": "body", "startIndex": 1, "endIndex": 6}
    with pytest.raises(GdocError, match="not supported yet"):
        suggest_replacement("doc-one", [match], "| A |\n| --- |\n| B |",
                            "revision-source", body=document)
    batch.assert_not_called()
