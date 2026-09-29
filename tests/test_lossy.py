"""Replacement safety uses native structure, never Markdown text heuristics."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from gdoc.api.docs import insert_markdown_into_tab
from gdoc.cli import build_parser, cmd_push, cmd_write
from gdoc.lossy import check_markdown_replacement
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


def body(element):
    return {"content": [{
        "startIndex": 1, "endIndex": 5,
        "paragraph": {"elements": [element]},
    }]}


def tab(name, content):
    return {"tabProperties": {"tabId": name, "title": name},
            "documentTab": {"body": content}}


@pytest.fixture(autouse=True)
def no_export_network(mocker):
    mocker.patch("gdoc.api.drive.export_doc", return_value="remote")
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 2})


def _cli_setup(mocker, scope):
    """Mock service boundaries while running the real native write/loss path."""
    doc = deepcopy(scope)
    if "tabs" not in doc:
        doc = {"tabs": [{"documentTab": doc}]}
    doc["revisionId"] = "before"
    for index, item in enumerate(doc["tabs"]):
        item.setdefault("tabProperties", {"tabId": f"tab-{index}", "title": "Notes"})
    fetch = mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    mocker.patch("gdoc.notify.pre_flight", return_value=ChangeInfo(
        current_version=2, mime_type="application/vnd.google-apps.document",
    ))
    mocker.patch("gdoc.state.update_state_after_command")
    chain = mocker.patch("gdoc.api.docs.get_docs_service").return_value.documents
    chain.return_value.batchUpdate.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": "after"},
    }
    return doc, fetch, chain.return_value.batchUpdate


RICH = body({"person": {"personProperties": {"name": "Example"}}})
PLAIN = body({"textRun": {"content": "old\n"}})


@pytest.mark.parametrize("element", [
    {"person": {}}, {"richLink": {}}, {"dateElement": {}},
    {"inlineObjectElement": {"inlineObjectId": "image"}},
    {"footnoteReference": {"footnoteId": "fn"}}, {"equation": {}},
    {"autoText": {}}, {"pageBreak": {}}, {"columnBreak": {}},
    {"textRun": {"content": "pending", "suggestedInsertionIds": ["s"]}},
    {"textRun": {"textStyle": {"link": {"headingId": "h"}}}},
])
def test_native_elements_refuse(element):
    with pytest.raises(GdocError, match="--allow-lossy") as exc:
        check_markdown_replacement(body(element), tab_body=True)
    assert exc.value.exit_code == 3


def test_ordinary_formatting_and_metadata_allowed():
    check_markdown_replacement({
        "body": body({"textRun": {"content": "hello", "textStyle": {
            "bold": True, "italic": True, "strikethrough": True,
            "link": {"url": "https://example.com"},
        }}}),
        "namedStyles": {}, "namedRanges": {"label": {}},
        "lists": {"list": {"listProperties": {}}},
        "sectionBreak": {"sectionStyle": {}},
    })


@pytest.mark.parametrize("in_tab", [False, True])
@pytest.mark.parametrize("field", [
    "namedStyles", "documentStyle", "lists",
    "suggestedNamedStylesChanges", "suggestedDocumentStyleChanges",
])
@pytest.mark.parametrize("border", [False, True])
def test_style_metadata_does_not_block_or_warn(capsys, in_tab, field, border):
    style = {
        "textStyle": {"fontSize": {"magnitude": 12, "unit": "PT"}},
        "paragraphStyle": {"spaceAbove": {"magnitude": 6, "unit": "PT"}},
        "listProperties": {"nestingLevels": [{"glyphSymbol": "★"}]},
    }
    if border:
        style["paragraphStyle"]["borderBottom"] = {}
    metadata = {
        "namedStyles": {"styles": [style]},
        "documentStyle": style,
        "lists": {"unused": style},
        "suggestedNamedStylesChanges": {"s": {"namedStyles": {"styles": [style]}}},
        "suggestedDocumentStyleChanges": {"s": {"documentStyle": style}},
    }
    scope = {"body": PLAIN, field: metadata[field]}
    if in_tab:
        scope = {"tabs": [{"documentTab": scope}]}
    check_markdown_replacement(scope)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("location", [
    "body", "headers", "footers", "footnotes", "table",
])
def test_content_styles_still_block_and_warn(capsys, location):
    content = body({"textRun": {
        "content": "old\n", "textStyle": {
            "bold": True, "fontSize": {"magnitude": 12, "unit": "PT"},
        },
    }})
    paragraph = content["content"][0]["paragraph"]
    paragraph["paragraphStyle"] = {
        "borderBottom": {"width": {"magnitude": 1, "unit": "PT"}},
        "spaceAbove": {"magnitude": 6, "unit": "PT"},
    }
    paragraph["bullet"] = {"listId": "used"}
    scope = {"lists": {"used": {"listProperties": {
        "nestingLevels": [{"glyphSymbol": "★"}],
    }}}}
    if location == "table":
        scope["body"] = {"content": [{"table": {
            "tableRows": [{"tableCells": [content]}],
        }}]}
    elif location == "body":
        scope["body"] = content
    else:
        scope[location] = {"segment": content}
    with pytest.raises(GdocError, match="border-bottom paragraphs"):
        check_markdown_replacement(scope)
    check_markdown_replacement(scope, allow_lossy=True)
    warning = capsys.readouterr().err
    for loss in ("border-bottom paragraphs", "font size", "paragraph spacing",
                 "list glyphs and list styling"):
        assert loss in warning


@pytest.mark.parametrize("structure", [
    {"paragraph": {"positionedObjectIds": ["obj"]}},
    {"tableOfContents": {}},
    {"table": {"tableRows": [{"tableCells": [{
        "tableCellStyle": {"rowSpan": 2},
    }]}]}},
    {"table": {"tableRows": [{"tableCells": [{
        "content": [{"table": {}}],
    }]}]}},
])
def test_structural_hazards(structure):
    with pytest.raises(GdocError):
        check_markdown_replacement({"content": [structure]})


def test_simple_tables_round_trip_in_both_paths():
    header = body({"textRun": {"content": "header", "textStyle": {"bold": True}}})
    header["content"][0]["paragraph"]["elements"].append(
        {"textRun": {"content": "\n"}},
    )
    empty = body({"textRun": {"content": "\n"}})
    scope = {"content": [{"table": {"tableRows": [
        {"tableCells": [header, empty]}, {"tableCells": [PLAIN, PLAIN]},
    ]}}]}
    check_markdown_replacement(scope)
    check_markdown_replacement(scope, tab_body=True)


@pytest.mark.parametrize("target_body", [PLAIN, {}, {"content": []}])
@pytest.mark.parametrize("selector", ["TARGET", "target"])
def test_tab_scope_ignores_rich_sibling_and_headers(mocker, target_body, selector):
    target = tab("target", target_body)
    target["documentTab"]["headers"] = {"h": RICH}
    target["documentTab"]["footers"] = {"f": RICH}
    target["documentTab"]["inlineObjects"] = {"unreferenced": {}}
    target["childTabs"] = [tab("rich-child", RICH)]
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target, tab("sibling", RICH)],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", selector, "new", replace=True)
    batch = service.documents.return_value.batchUpdate
    batch.assert_called_once()
    assert batch.call_args.kwargs["body"]["writeControl"] == {
        "requiredRevisionId": "r",
    }


@pytest.mark.parametrize("target_body", [RICH, body({"equation": {}})])
@pytest.mark.parametrize("allow_lossy", [False, True])
def test_tab_refusal_precedes_every_mutation(mocker, allow_lossy, target_body):
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [tab("target", target_body)],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    table_insert = mocker.patch("gdoc.api.docs._insert_table")
    if allow_lossy:
        insert_markdown_into_tab(
            "doc", "target", "new", replace=True, allow_lossy=True,
        )
        service.documents.return_value.batchUpdate.assert_called_once()
    else:
        with pytest.raises(GdocError, match="selected tab body"):
            insert_markdown_into_tab("doc", "target", "new", replace=True)
        service.documents.return_value.batchUpdate.assert_not_called()
        table_insert.assert_not_called()


@pytest.mark.parametrize("command", ["write", "push"])
@pytest.mark.parametrize("scope", [
    {"body": RICH},
    {"tabs": [tab("plain", PLAIN), tab("rich", RICH)]},
    {"tabs": [{**tab("parent", PLAIN), "childTabs": [tab("child", RICH)]}]},
    {"headers": {"h": PLAIN}},
    {"tabs": [{"documentTab": {"footers": {"f": PLAIN}}}]},
    {"footnotes": {"f": PLAIN}},
])
@pytest.mark.parametrize("mode", ["refuse", "opt-in", "no-op"])
def test_full_write_scope(mocker, tmp_path, command, scope, mode):
    from gdoc.api.docs import flatten_tabs, get_tab_text

    doc, fetch, batch = _cli_setup(mocker, scope)
    selected = flatten_tabs(doc["tabs"])[0]
    content = get_tab_text(selected, markdown=True) if mode == "no-op" else "new"
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\n---\n" + content, encoding="utf-8")
    args = SimpleNamespace(doc="doc", file=str(path), force=True, quiet=True,
                           allow_lossy=mode == "opt-in")
    handler = cmd_write if command == "write" else cmd_push
    rich_target = selected.get("body") == RICH
    if mode == "refuse" and rich_target:
        with pytest.raises(GdocError, match="selected tab body.*people chips"):
            handler(args)
        batch.assert_not_called()
    else:
        assert handler(args) == 0
        if mode == "no-op":
            batch.assert_not_called()
        else:
            batch.assert_called_once()
            sent = batch.call_args.kwargs["body"]
            assert sent["writeControl"] == {"requiredRevisionId": "before"}
            assert not any("deleteTab" in request for request in sent["requests"])
    # One safety snapshot precedes any mutation. An acknowledged push, or a
    # write of a pulled file, reads once more afterwards to record the file's
    # native tab fingerprint.
    after_push = batch.called
    assert fetch.call_args_list == [mocker.call("doc")] * (1 + after_push)


@pytest.mark.parametrize("command", ["write", "push"])
@pytest.mark.parametrize("mode", ["refuse", "opt-in"])
def test_single_tab_write_uses_body_scope(mocker, tmp_path, command, mode):
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\n---\nnew", encoding="utf-8")
    only = tab("Renamed", RICH if mode == "refuse" else PLAIN)
    only["documentTab"]["headers"] = {"h": RICH}
    _, _, batch = _cli_setup(mocker, {"tabs": [only]})
    args = SimpleNamespace(doc="doc", file=str(path), force=True, quiet=True,
                           allow_lossy=mode == "opt-in")
    handler = cmd_write if command == "write" else cmd_push
    if mode == "refuse":
        with pytest.raises(GdocError, match="selected tab body.*people chips"):
            handler(args)
        batch.assert_not_called()
    else:
        assert handler(args) == 0
        batch.assert_called_once()
        assert not any("deleteTab" in request for request in
                       batch.call_args.kwargs["body"]["requests"])


def test_single_tab_header_only_needs_no_consent(mocker, tmp_path):
    path = tmp_path / "draft.md"
    path.write_text("new", encoding="utf-8")
    only = tab("Renamed", PLAIN)
    only["documentTab"]["headers"] = {"h": RICH}
    _, _, batch = _cli_setup(mocker, {"tabs": [only]})
    assert cmd_write(SimpleNamespace(doc="doc", file=str(path), force=True)) == 0
    batch.assert_called_once()
    assert all("segmentId" not in str(request)
               for request in batch.call_args.kwargs["body"]["requests"])


@pytest.mark.parametrize("command", ["write", "push"])
def test_lossy_opt_in_does_not_allow_tab_collapse(mocker, tmp_path, command):
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\n---\nnew", encoding="utf-8")
    _, _, batch = _cli_setup(mocker, {"tabs": [tab("first", PLAIN), tab("rich", RICH)]})
    args = SimpleNamespace(doc="doc", file=str(path), allow_lossy=True, force=True)
    assert (cmd_write if command == "write" else cmd_push)(args) == 0
    assert not any("deleteTab" in request
                   for request in batch.call_args.kwargs["body"]["requests"])


@pytest.mark.parametrize("argv", [["write", "doc", "file"], ["push", "file"]])
def test_parser_opt_in_is_separate(argv):
    parser = build_parser()
    args = parser.parse_args(argv + ["--allow-lossy"])
    assert args.allow_lossy
    assert not args.force
    assert not args.force_collapse_tabs


def test_structure_read_failure_propagates_before_upload(mocker, tmp_path):
    path = tmp_path / "draft.md"
    path.write_text("new", encoding="utf-8")
    _, fetch, batch = _cli_setup(mocker, {"body": PLAIN})
    fetch.side_effect = GdocError("unavailable")
    with pytest.raises(GdocError, match="unavailable"):
        cmd_write(SimpleNamespace(doc="doc", file=str(path), force=True))
    batch.assert_not_called()


@pytest.mark.parametrize("command", ["write", "push"])
def test_full_write_uses_one_safety_snapshot(mocker, tmp_path, command):
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\n---\nnew", encoding="utf-8")
    doc, fetch, batch = _cli_setup(mocker, {"tabs": [tab("Tab 1", PLAIN)]})
    fetch.side_effect = [doc, {"revisionId": "foreign", "tabs": [tab("new", RICH)]}]
    args = SimpleNamespace(doc="doc", file=str(path), force=True)
    assert (cmd_write if command == "write" else cmd_push)(args) == 0
    # The foreign snapshot is only ever the post-acknowledgement read of a
    # push or pulled-file write; it never feeds the safety check or the write.
    assert fetch.call_count == 2
    batch.assert_called_once()
    assert batch.call_args.kwargs["body"]["writeControl"] == {
        "requiredRevisionId": "before",
    }
    assert "gdoc-tab-sha256: \n" in path.read_text() or \
        "gdoc-tab-sha256:" not in path.read_text()


@pytest.mark.parametrize("text", ["\n", "underlined paragraph\n"])
def test_markdown_horizontal_rule_border_requires_opt_in(mocker, capsys, text):
    from gdoc.api.docs import get_tab_text

    scope = body({"textRun": {"content": text}})
    scope["content"][0]["paragraph"]["paragraphStyle"] = {
        "borderBottom": {"dashStyle": "SOLID", "width": {"magnitude": 1, "unit": "PT"}},
    }
    target = tab("target", scope)
    if text == "\n":
        assert get_tab_text(target["documentTab"], markdown=True) == "---\n"
        check_markdown_replacement(scope, tab_body=True)
        assert capsys.readouterr().err == ""
        return
    assert get_tab_text(target["documentTab"], markdown=True) == text
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    with pytest.raises(GdocError, match="border-bottom paragraphs") as exc:
        insert_markdown_into_tab("doc", "target", "new", replace=True)
    assert exc.value.exit_code == 3
    assert "--allow-lossy" in str(exc.value)
    service.documents.return_value.batchUpdate.assert_not_called()
    with pytest.raises(GdocError, match="border-bottom paragraphs"):
        check_markdown_replacement(scope)
    insert_markdown_into_tab(
        "doc", "target", "new", replace=True, allow_lossy=True,
    )
    service.documents.return_value.batchUpdate.assert_called_once()
    assert "will discard: border-bottom paragraphs" in capsys.readouterr().err


def test_native_horizontal_rule_whole_document_behavior_is_unchanged():
    check_markdown_replacement(body({"horizontalRule": {}}))


@pytest.mark.parametrize("name", [
    "person", "rowSpan", "columnSpan", "sectionBreak", "table", "headers",
    "suggestedAnything", "horizontalRule",
])
@pytest.mark.parametrize("command", ["write", "push"])
def test_named_range_names_are_not_schema_fields(mocker, tmp_path, name, command):
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\n---\nnew", encoding="utf-8")
    scope = {"body": PLAIN, "namedRanges": {
        name: {"namedRanges": [{"name": name, "ranges": [{
            "startIndex": 1, "endIndex": 3,
        }]}]},
    }}
    doc, _, batch = _cli_setup(mocker, scope)
    with pytest.raises(GdocError, match="custom named ranges") as error:
        check_markdown_replacement(doc, tab_body=True)
    assert "people chips" not in str(error.value)
    assert "merged table cells" not in str(error.value)
    args = SimpleNamespace(doc="doc", file=str(path), force=True)
    with pytest.raises(GdocError, match="custom named ranges"):
        (cmd_write if command == "write" else cmd_push)(args)
    batch.assert_not_called()


@pytest.mark.parametrize("style", [
    {"columnProperties": [{}, {}]},
    {"marginTop": {"magnitude": 20, "unit": "PT"}},
    {"sectionType": "NEXT_PAGE"},
    {"flipPageOrientation": True},
])
def test_whole_document_custom_sections_refuse(style):
    with pytest.raises(GdocError, match="section"):
        check_markdown_replacement({"body": {"content": [{
            "endIndex": 1, "sectionBreak": {"sectionStyle": style},
        }]}})


def test_default_section_marker_is_allowed():
    style = {
        "sectionType": "CONTINUOUS", "contentDirection": "LEFT_TO_RIGHT",
        "columnSeparatorStyle": "NONE", "columnProperties": [{}],
        "flipPageOrientation": False,
        "marginTop": {"magnitude": 72, "unit": "PT"},
    }
    check_markdown_replacement({
        "documentStyle": {"marginTop": style["marginTop"]},
        "body": {"content": [{"endIndex": 1, "sectionBreak": {
            "sectionStyle": style,
        }}]},
    })


@pytest.mark.parametrize("blank", [True, False])
def test_tab_initial_section_style_is_outside_deletion(mocker, blank):
    content = [{"endIndex": 1, "sectionBreak": {"sectionStyle": {
        "columnProperties": [{}, {}], "defaultHeaderId": "h",
    }}}]
    if not blank:
        content.extend(PLAIN["content"])
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [tab("target", {"content": content})],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", "target", "new", replace=True)
    service.documents.return_value.batchUpdate.assert_called_once()


def test_tab_internal_section_refuses_before_mutation(mocker):
    content = [*PLAIN["content"], {
        "startIndex": 5, "endIndex": 6,
        "sectionBreak": {"sectionStyle": {"sectionType": "CONTINUOUS"}},
    }, {"startIndex": 6, "endIndex": 8, "paragraph": {
        "elements": [{"textRun": {"content": "x\n"}}],
    }}]
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [tab("target", {"content": content})],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    with pytest.raises(GdocError, match="section"):
        insert_markdown_into_tab("doc", "target", "new", replace=True)
    service.documents.return_value.batchUpdate.assert_not_called()


def test_map_values_are_still_checked():
    with pytest.raises(GdocError, match="people chips"):
        check_markdown_replacement({"headers": {"rowSpan": RICH}})


@pytest.mark.parametrize("custom", [False, True])
def test_section_defaults_follow_owning_tab(custom):
    def section_tab(name, margin, section_margin):
        return {"tabProperties": {"tabId": name}, "documentTab": {
            "documentStyle": {"marginTop": {"magnitude": margin, "unit": "PT"}},
            "body": {"content": [{"endIndex": 1, "sectionBreak": {
                "sectionStyle": {"marginTop": {
                    "magnitude": section_margin, "unit": "PT",
                }},
            }}]},
        }}

    parent = section_tab("parent", 72, 72)
    parent["childTabs"] = [section_tab("child", 18, 1 if custom else 18)]
    scope = {
        "documentStyle": {"marginTop": {"magnitude": 999, "unit": "PT"}},
        "tabs": [parent, section_tab("sibling", 36, 36)],
    }
    if custom:
        with pytest.raises(GdocError, match="section"):
            check_markdown_replacement(scope)
    else:
        with pytest.raises(GdocError, match="page setup") as exc:
            check_markdown_replacement(scope)
        assert "section layout" not in str(exc.value)


@pytest.mark.parametrize("in_cell", [False, True])
@pytest.mark.parametrize("mode", ["refuse", "override", "style-only"])
def test_referenced_list_inventory(mocker, capsys, in_cell, mode):
    content = body({"textRun": {"content": "old\n"}})
    content["content"][0]["paragraph"]["bullet"] = {"listId": "L"}
    if in_cell:
        content["content"][0]["paragraph"]["elements"][0]["textRun"]["textStyle"] = {
            "bold": True,
        }
        content = {"content": [{"startIndex": 1, "endIndex": 9, "table": {
            "tableRows": [{"tableCells": [{"content": content["content"]}]}],
        }}]}
    target = tab("target", content)
    definition = {"listProperties": {"nestingLevels": [{"glyphSymbol": "★"}]}}
    if mode != "style-only":
        definition["suggestedListPropertiesChanges"] = {"s": {
            "listProperties": {"nestingLevels": [{"glyphSymbol": "◆"}]},
            "listPropertiesSuggestionState": {
                "nestingLevelsSuggestionStates": [{"glyphSymbolSuggested": True}],
            },
        }}
    target["documentTab"]["lists"] = {"L": definition}
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    table_insert = mocker.patch("gdoc.api.docs._insert_table")
    markdown = "| New |\n| --- |\n| value |"
    if mode == "refuse" or (in_cell and mode == "style-only"):
        hazard = "pending suggestions" if mode == "refuse" else "list paragraphs"
        with pytest.raises(GdocError, match=hazard) as exc:
            insert_markdown_into_tab("doc", "target", markdown, replace=True)
        assert exc.value.exit_code == 3
        service.documents.return_value.batchUpdate.assert_not_called()
        table_insert.assert_not_called()
    else:
        insert_markdown_into_tab(
            "doc", "target", markdown, replace=True, allow_lossy=mode == "override",
        )
        service.documents.return_value.batchUpdate.assert_called_once()
        table_insert.assert_called_once()
        warning = capsys.readouterr().err
        assert "list glyphs and list styling" in warning
        assert ("will discard: pending suggestions" in warning) == (mode == "override")


@pytest.mark.parametrize("header_only", [False, True])
def test_unreferenced_suggested_lists_are_outside_body(mocker, capsys, header_only):
    target = tab("target", PLAIN)
    target["documentTab"]["lists"] = {"unused": {
        "listProperties": {"nestingLevels": [{"glyphSymbol": "★"}]},
        "suggestedListPropertiesChanges": {"s": {"listProperties": {}}},
    }}
    if header_only:
        header = body({"textRun": {"content": "header\n"}})
        header["content"][0]["paragraph"]["bullet"] = {"listId": "unused"}
        target["documentTab"]["headers"] = {"h": header}
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", "target", "new", replace=True)
    service.documents.return_value.batchUpdate.assert_called_once()
    assert capsys.readouterr().err == ""


def test_keep_lines_together_warns_about_paragraph_layout(capsys):
    check_markdown_replacement({"content": [{"paragraph": {
        "paragraphStyle": {"keepLinesTogether": True},
    }}]}, tab_body=True)
    assert "may reset styles: paragraph layout" in capsys.readouterr().err


def numbered_tab(list_ids, start=1, glyph_type="DECIMAL"):
    target = tab("target", {"content": [{
        "startIndex": 1 + i * 5, "endIndex": 6 + i * 5,
        "paragraph": {
            "elements": [{"textRun": {"content": "item\n"}}],
            "bullet": {"listId": list_id},
        },
    } for i, list_id in enumerate(list_ids)]})
    target["documentTab"]["lists"] = {list_id: {"listProperties": {
        "nestingLevels": [{"glyphType": glyph_type, "startNumber": start}],
    }} for list_id in list_ids}
    return target


def test_adjacent_numbered_restarts_export_separately_without_warning(capsys):
    from gdoc.api.docs import get_tab_text

    target = numbered_tab(["first", "second"])
    markdown = get_tab_text(target["documentTab"], markdown=True)
    assert markdown == "1. item\n1. item\n"
    check_markdown_replacement(target["documentTab"], tab_body=True)
    warning = capsys.readouterr().err
    assert warning == ""


@pytest.mark.parametrize("list_ids,start", [
    (["first", "second", "first"], 1),
    (["first"], 3),
    (["first"], 0),
])
@pytest.mark.parametrize("tab_body", [False, True])
def test_numbering_warning_names_lists_without_opt_in(
    capsys, list_ids, start, tab_body,
):
    scope = numbered_tab(list_ids, start)["documentTab"]
    check_markdown_replacement(scope, tab_body=tab_body)
    warning = capsys.readouterr().err
    assert list_ids[0] in warning
    assert "numbering may reset" in warning
    assert "--allow-lossy" not in warning


@pytest.mark.parametrize("list_ids,glyph_type", [
    (["first", "first", "first"], "DECIMAL"),
    (["first", "second", "first"], "GLYPH_TYPE_UNSPECIFIED"),
])
def test_continuous_numbering_and_bullets_remain_allowed(list_ids, glyph_type):
    scope = numbered_tab(list_ids, glyph_type=glyph_type)["documentTab"]
    check_markdown_replacement(scope, tab_body=True)


@pytest.mark.parametrize("same_list", [False, True])
def test_numbering_after_plain_paragraph(capsys, same_list):
    scope = numbered_tab(["first", "first" if same_list else "second"])["documentTab"]
    scope["body"]["content"].insert(1, PLAIN["content"][0])
    check_markdown_replacement(scope, tab_body=True)
    assert capsys.readouterr().err == ""


def test_numbered_definitions_follow_owning_tab():
    first = numbered_tab(["same"])
    second = numbered_tab(["same"])
    first["tabProperties"]["title"] = second["tabProperties"]["title"] = "Tab 1"
    check_markdown_replacement({"tabs": [first, second]})


def test_only_referenced_numbered_levels_are_checked(capsys):
    scope = numbered_tab(["first"])["documentTab"]
    scope["lists"]["unused"] = {"listProperties": {"nestingLevels": [
        {"glyphType": "DECIMAL", "startNumber": 5},
    ]}}
    scope["lists"]["first"]["listProperties"]["nestingLevels"].append({
        "glyphType": "DECIMAL", "startNumber": 3,
    })
    check_markdown_replacement(scope, tab_body=True)
    scope["body"]["content"][0]["paragraph"]["bullet"]["nestingLevel"] = 1
    assert capsys.readouterr().err == ""
    check_markdown_replacement(scope, tab_body=True)
    assert "numbered list 'first' starts at 3" in capsys.readouterr().err


def test_numbered_restart_override_permits_tab_mutation(mocker, capsys):
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [numbered_tab(["first", "second"])],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", "target", "new", replace=True, allow_lossy=True)
    service.documents.return_value.batchUpdate.assert_called_once()
    warning = capsys.readouterr().err
    assert warning == ""


@pytest.mark.parametrize("cell_text", [" leading\n", "trailing \n", "\tleading\n"])
@pytest.mark.parametrize("tab_body", [False, True])
def test_whitespace_table_needs_no_opt_in(capsys, cell_text, tab_body):
    from gdoc.api.docs import get_tab_text

    scope = {"body": {"content": [{"startIndex": 7, "table": {
        "tableRows": [{"tableCells": [
            body({"textRun": {"content": cell_text}}), PLAIN,
        ]}],
    }}]}}
    from gdoc.mdparse import parse_inline, parse_markdown

    parsed = parse_markdown(get_tab_text(scope, markdown=True))
    assert parse_inline(parsed.tables[0].rows[0][0])[0] == cell_text[:-1]
    check_markdown_replacement(scope, tab_body=tab_body)
    assert capsys.readouterr().err == ""


def test_numbering_resumed_after_unordered_item_has_no_warning(capsys):
    from gdoc.api.docs import get_tab_text

    scope = numbered_tab(["first", "bullet", "first"])["documentTab"]
    scope["lists"]["bullet"]["listProperties"]["nestingLevels"] = [{}]
    assert get_tab_text(scope, markdown=True) == "1. item\n- item\n2. item\n"
    check_markdown_replacement(scope, tab_body=True)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("glyph_type", ["GLYPH_TYPE_UNSPECIFIED", "DECIMAL"])
def test_empty_list_item_is_reconstructed(mocker, glyph_type):
    from gdoc.api.docs import get_tab_text

    target = numbered_tab(["empty"], glyph_type=glyph_type)
    scope = target["documentTab"]
    scope["body"]["content"][0]["paragraph"]["elements"] = [
        {"textRun": {"content": "\n"}},
    ]
    marker = "1." if glyph_type == "DECIMAL" else "-"
    assert get_tab_text(scope, markdown=True) == marker + " \n"
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", "target", marker + " ", replace=True)
    service.documents.return_value.batchUpdate.assert_called_once()
    from gdoc.mdparse import parse_markdown

    parsed = parse_markdown(get_tab_text(scope, markdown=True))
    assert any(style.type == "bullets" for style in parsed.styles)


@pytest.mark.parametrize("header_styles", [({}, {}), ({"bold": True}, {}),
                                           ({"bold": True}, {"bold": False})])
@pytest.mark.parametrize("allow_lossy", [False, True])
def test_plain_or_mixed_table_header_needs_no_opt_in(
    mocker, capsys, header_styles, allow_lossy,
):
    from gdoc.api.docs import get_tab_text

    header = body({"textRun": {"content": "first", "textStyle": header_styles[0]}})
    header["content"][0]["paragraph"]["elements"].append(
        {"textRun": {"content": " second\n", "textStyle": header_styles[1]}},
    )
    scope = {"content": [{"startIndex": 7, "endIndex": 30, "table": {
        "tableRows": [{"tableCells": [header, body({"textRun": {
            "content": "bold\n", "textStyle": {"bold": True},
        }})]}, {"tableCells": [PLAIN, PLAIN]}],
    }}]}
    target = tab("target", scope)
    markdown = get_tab_text(target["documentTab"], markdown=True)
    assert "| --- | --- |" in markdown
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    table_insert = mocker.patch("gdoc.api.docs._insert_table")
    insert_markdown_into_tab(
        "doc", "target", markdown, replace=True, allow_lossy=allow_lossy,
    )
    service.documents.return_value.batchUpdate.assert_called_once()
    table_insert.assert_called_once()
    assert capsys.readouterr().err == ""
    check_markdown_replacement(scope)


@pytest.mark.parametrize("glyph_type,level", [
    (None, 0), ("GLYPH_TYPE_UNSPECIFIED", 0), ("DECIMAL", 0),
    ("GLYPH_TYPE_UNSPECIFIED", 3), ("DECIMAL", 3),
])
@pytest.mark.parametrize("row_index", [0, 1])
def test_table_cell_lists_require_opt_in(mocker, capsys, glyph_type, level, row_index):
    from gdoc.api.docs import get_tab_text

    header = body({"textRun": {
        "content": "header\n", "textStyle": {"bold": True},
    }})
    cell = body({"textRun": {"content": "item\n"}})
    rows = [{"tableCells": [header]}, {"tableCells": [cell]}]
    target = tab("target", {"content": [{
        "startIndex": 7, "endIndex": 30, "table": {"tableRows": rows},
    }]})
    if glyph_type is not None:
        rows[row_index]["tableCells"][0]["content"][0]["paragraph"]["bullet"] = {
            "listId": "L", "nestingLevel": level,
        }
        target["documentTab"]["lists"] = {"L": {"listProperties": {
            "nestingLevels": [{"glyphType": glyph_type}] * (level + 1),
        }}}
    markdown = get_tab_text(target["documentTab"], markdown=True)
    assert markdown == "| **header** |\n| --- |\n| item |\n"
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    table_insert = mocker.patch("gdoc.api.docs._insert_table")
    if glyph_type is not None:
        with pytest.raises(GdocError, match="table at index 7.*list paragraphs") as exc:
            insert_markdown_into_tab("doc", "target", markdown, replace=True)
        assert exc.value.exit_code == 3
        assert "--allow-lossy" in str(exc.value)
        service.documents.return_value.batchUpdate.assert_not_called()
        table_insert.assert_not_called()
        with pytest.raises(GdocError, match="table at index 7.*list paragraphs"):
            check_markdown_replacement(target["documentTab"])
    else:
        check_markdown_replacement(target["documentTab"])
    insert_markdown_into_tab(
        "doc", "target", markdown, replace=True, allow_lossy=glyph_type is not None,
    )
    service.documents.return_value.batchUpdate.assert_called_once()
    table_insert.assert_called_once()
    warning = capsys.readouterr().err
    assert ("table at index 7 (list paragraphs" in warning) == (glyph_type is not None)


@pytest.mark.parametrize("named_style", [
    None, "NORMAL_TEXT", "TITLE", "SUBTITLE",
    "HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6",
])
@pytest.mark.parametrize("row_index", [0, 1])
def test_table_cell_named_styles_require_opt_in(mocker, capsys, named_style, row_index):
    from gdoc.api.docs import get_tab_text

    header = body({"textRun": {
        "content": "header\n", "textStyle": {"bold": True},
    }})
    cell = body({"textRun": {"content": "item\n"}})
    rows = [{"tableCells": [header]}, {"tableCells": [cell]}]
    target = tab("target", {"content": [{
        "startIndex": 7, "endIndex": 30, "table": {"tableRows": rows},
    }]})
    if named_style is not None:
        paragraph = rows[row_index]["tableCells"][0]["content"][0]["paragraph"]
        paragraph["paragraphStyle"] = {"namedStyleType": named_style}
    markdown = get_tab_text(target["documentTab"], markdown=True)
    assert markdown == "| **header** |\n| --- |\n| item |\n"
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r", "tabs": [target],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    table_insert = mocker.patch("gdoc.api.docs._insert_table")
    lossy = named_style not in (None, "NORMAL_TEXT")
    if lossy:
        hazard = "table at index 7.*named paragraph styles"
        with pytest.raises(GdocError, match=hazard) as exc:
            insert_markdown_into_tab("doc", "target", markdown, replace=True)
        assert exc.value.exit_code == 3
        assert "--allow-lossy" in str(exc.value)
        service.documents.return_value.batchUpdate.assert_not_called()
        table_insert.assert_not_called()
        with pytest.raises(GdocError, match="table at index 7.*named paragraph styles"):
            check_markdown_replacement(target["documentTab"])
    else:
        check_markdown_replacement(target["documentTab"])
    insert_markdown_into_tab(
        "doc", "target", markdown, replace=True, allow_lossy=lossy,
    )
    service.documents.return_value.batchUpdate.assert_called_once()
    table_insert.assert_called_once()
    assert ("named paragraph styles" in capsys.readouterr().err) == lossy


def test_native_zero_width_table_paragraph_border_is_not_a_loss():
    check_markdown_replacement({"content": [{"paragraph": {
        "elements": [{"textRun": {"content": "Seven crates\n"}}],
        "paragraphStyle": {"borderBottom": {
            "color": {}, "width": {"unit": "PT"},
            "padding": {"unit": "PT"}, "dashStyle": "SOLID",
        }},
    }}]}, tab_body=True)
