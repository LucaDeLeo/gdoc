"""Offline regressions using invented Docs API structures and UTF-16 indices."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from gdoc.api import docs
from gdoc.cli import cmd_edit
from gdoc.util import GdocError


@pytest.fixture(autouse=True)
def no_network(mocker):
    def forbidden(*args, **kwargs):
        pytest.fail("Native target tests must remain offline")

    mocker.patch("socket.socket.connect", side_effect=forbidden)
    mocker.patch.object(docs, "get_docs_service", side_effect=forbidden)


def paragraph(text, start, style="NORMAL_TEXT"):
    end = start + len(text.encode("utf-16-le")) // 2
    return {"startIndex": start, "endIndex": end, "paragraph": {
        "paragraphStyle": {"namedStyleType": style}, "elements": [
        {"startIndex": start, "endIndex": end, "textRun": {"content": text}},
    ]}}


def table(rows, start=1):
    """Build a synthetic table with the API's table/row/cell index gaps."""
    cursor = start + 1
    table_rows = []
    for values in rows:
        row_start = cursor
        cursor += 1
        cells = []
        for text in values:
            para = paragraph(text, cursor + 1)
            cells.append({"startIndex": cursor, "endIndex": para["endIndex"],
                          "content": [para]})
            cursor = para["endIndex"]
        table_rows.append({"startIndex": row_start, "endIndex": cursor,
                           "tableCells": cells})
    return {"startIndex": start, "endIndex": cursor,
            "table": {"tableRows": table_rows}}


def raw_tab(tab_id, title, children=()):
    return {"tabProperties": {"tabId": tab_id, "title": title},
            "documentTab": {"body": {"content": [paragraph("\n", 1)]}},
            "childTabs": list(children)}


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize(("query", "expected"), [
    ("target-id", "target-id"), ("notes", "lower-id"),
    ("Notes", "target-id"), ("unique", "unique-id"),
])
def test_tab_identity_precedes_title_and_case_fallback(raw, query, expected):
    tabs = [raw_tab("decoy-id", "target-id"),
            raw_tab("target-id", "Notes", [raw_tab("lower-id", "notes")]),
            raw_tab("unique-id", "Unique")]
    before = deepcopy(tabs)
    if raw:
        result = docs.resolve_raw_tab(tabs, query)["tabProperties"]["tabId"]
    else:
        result = docs.resolve_tab(docs.flatten_tabs(tabs), query)["id"]
    assert result == expected
    assert tabs == before


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize(("titles", "query"), [
    (("Notes", "Notes"), "Notes"), (("Notes", "notes"), "NOTES"),
])
def test_ambiguous_tab_lists_every_candidate(raw, titles, query):
    tabs = [raw_tab("parent-id", titles[0], [raw_tab("child-id", titles[1])])]
    resolver = docs.resolve_raw_tab if raw else docs.resolve_tab
    with pytest.raises(GdocError, match="ambiguous tab") as exc:
        resolver(tabs if raw else docs.flatten_tabs(tabs), query)
    assert exc.value.exit_code == 3
    for candidate in (*titles, "parent-id", "child-id"):
        assert candidate in str(exc.value)


def test_value_cell_does_not_identify_its_neighbour():
    body = {"content": [table([["Label\n", "Value\n", "Keep\n"]])]}
    assert docs.resolve_cell_range(body, "Value") is None
    assert docs.resolve_cell_range(body, "Value", col=2) is None
    assert docs.resolve_cell_range(body, "Label", col=2) == {
        "startIndex": 18, "endIndex": 22,
    }


@pytest.mark.parametrize("separate_tables", [False, True])
def test_duplicate_labels_require_explicit_coordinates(separate_tables):
    rows = [["Label\n", "First\n"], ["Label\n", "Second\n"]]
    content = ([table(rows[:1]), table(rows[1:], start=30)] if separate_tables
               else [table(rows)])
    body = {"content": content}
    with pytest.raises(GdocError, match="ambiguous cell label") as exc:
        docs.resolve_cell_range(body, "Label")
    assert exc.value.exit_code == 3
    assert "table 0 row 0" in str(exc.value)
    assert ("table 1 row 0" if separate_tables else "table 0 row 1") in str(exc.value)
    ti, ri = (1, 0) if separate_tables else (0, 1)
    assert docs.resolve_cell_range(body, f"{ri},1", table_index=ti)
    if separate_tables:
        assert docs.resolve_cell_range(body, "Label", table_index=ti)
    else:
        with pytest.raises(GdocError, match="ambiguous cell label"):
            docs.resolve_cell_range(body, "Label", table_index=ti)


def test_normalized_labels_are_checked_for_ambiguity():
    body = {"content": [table([["Team’s note\n", "First\n"],
                               ["Team's note\n", "Second\n"]])]}
    assert docs.resolve_cell_range(body, "Team's note")
    with pytest.raises(GdocError, match="ambiguous cell label"):
        docs.resolve_cell_range(body, "Team's note", normalize=True)


def edit_args(**kwargs):
    args = dict(doc="synthetic-doc", old_text="replacement", new_text=None,
                old_file=None, new_file=None, cell="Label", col=None, table=None,
                tab=None, quiet=True, plain=False, case_sensitive=False,
                normalize=False, json=False, verbose=False)
    args.update(kwargs)
    return SimpleNamespace(**args)


def test_ambiguous_cell_edit_refuses_before_mutation(mocker):
    document = {"body": {"content": [table([["Label\n", "First\n"],
                                             ["Label\n", "Second\n"]])]}}
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch.object(docs, "get_document", return_value=document)
    replace = mocker.patch.object(docs, "replace_formatted")
    with pytest.raises(GdocError, match="ambiguous cell label") as exc:
        cmd_edit(edit_args())
    assert exc.value.exit_code == 3
    replace.assert_not_called()


@pytest.mark.parametrize("native", [
    {"inlineObjectElement": {"inlineObjectId": "synthetic-image"}},
    {"footnoteReference": {"footnoteId": "synthetic-footnote"}},
])
def test_inline_native_objects_split_search_segments(native):
    document = {"body": {"content": [{"paragraph": {"elements": [
        {"startIndex": 1, "endIndex": 5, "textRun": {"content": "Left"}},
        {"startIndex": 5, "endIndex": 6, **native},
        {"startIndex": 6, "endIndex": 12, "textRun": {"content": "Right\n"}},
    ]}}]}}
    before = deepcopy(document)
    assert docs.find_text_in_document(document, "LeftRight") == []
    assert docs.find_text_in_document(document, "Right") == [
        {"startIndex": 6, "endIndex": 11},
    ]
    assert document == before


@pytest.mark.parametrize("form", ["legacy", "raw-tabs", "flattened-tab"])
@pytest.mark.parametrize("native", [
    {"inlineObjectElement": {"inlineObjectId": "synthetic-image"}},
    {"footnoteReference": {"footnoteId": "synthetic-footnote"}},
])
def test_full_document_anchor_can_span_native_objects(form, native):
    body = {"content": [{"paragraph": {"elements": [
        {"startIndex": 1, "endIndex": 5, "textRun": {"content": "Left"}},
        {"startIndex": 5, "endIndex": 6, **native},
        {"startIndex": 6, "endIndex": 12, "textRun": {"content": "Right\n"}},
    ]}}]}
    document = {"body": body}
    expected = {"startIndex": 1, "endIndex": 11}
    if form != "legacy":
        tab = raw_tab("a", "A")
        tab["documentTab"]["body"] = body
        document = ({"tabs": [tab]} if form == "raw-tabs"
                    else docs.flatten_tabs([tab])[0])
        expected.update(container="body", tabId="a")
    assert docs.find_text_in_document(document, "LeftRight") == []
    assert docs.find_text_in_document(
        document, "LeftRight", allow_native_gaps=True,
    ) == [expected]


@pytest.mark.parametrize("allow_native_gaps", [False, True])
def test_table_splits_outer_paragraphs_and_cells(allow_native_gaps):
    middle = table([["Cell\n"]], start=6)
    end = middle["endIndex"]
    document = {"body": {"content": [paragraph("Left\n", 1), middle,
                                       paragraph("Right\n", end)]}}
    kwargs = {"allow_native_gaps": allow_native_gaps}
    for needle in ("Left\nRight", "Left\nCell", "Cell\nRight"):
        assert docs.find_text_in_document(document, needle, **kwargs) == []
    assert docs.find_text_in_document(document, "Right", **kwargs) == [
        {"startIndex": end, "endIndex": end + 5},
    ]
    assert docs.find_text_in_document(document, "Cell", **kwargs) == [
        {"startIndex": 9, "endIndex": 13},
    ]


def test_nested_table_splits_surrounding_cell_text():
    nested = table([["Nested\n"]], start=10)
    end = nested["endIndex"]
    cell = {"content": [paragraph("Before\n", 3), nested, paragraph("After\n", end)]}
    document = {"body": {"content": [{"table": {"tableRows": [
        {"tableCells": [cell]},
    ]}}]}}
    assert docs.find_text_in_document(document, "Before\nAfter") == []
    assert docs.find_text_in_document(document, "Nested") == [
        {"startIndex": 13, "endIndex": 19},
    ]
    with pytest.raises(GdocError, match="non-text content"):
        docs._cell_text_range(cell)


@pytest.mark.parametrize("explicit_block", [False, True])
def test_structural_gaps_cannot_be_searched_through(explicit_block):
    content = [paragraph("Left\n", 1)]
    if explicit_block:
        content.append({"startIndex": 6, "endIndex": 7,
                        "sectionBreak": {"sectionStyle": {}}})
    content.append(paragraph("Right\n", 7))
    assert docs.find_text_in_document({"body": {"content": content}},
                                      "Left\nRight") == []


def test_adjacent_paragraphs_and_style_runs_remain_searchable():
    first = paragraph("Left\n", 1)
    first["paragraph"]["elements"] = [
        {"startIndex": 1, "endIndex": 3, "textRun": {"content": "Le"}},
        {"startIndex": 3, "endIndex": 6,
         "textRun": {"content": "ft\n", "textStyle": {"bold": True}}},
    ]
    document = {"body": {"content": [first, paragraph("Right\n", 6)]}}
    assert docs.find_text_in_document(document, "Left\nRight") == [
        {"startIndex": 1, "endIndex": 11},
    ]


@pytest.mark.parametrize("cell_address", ["0,1", "Label"])
@pytest.mark.parametrize("object_type", ["inline", "footnote", "positioned"])
def test_cell_native_object_refuses_before_mutation(mocker, cell_address, object_type):
    grid = table([["Label\n", "Before\n"]])
    cell = grid["table"]["tableRows"][0]["tableCells"][1]
    start = cell["endIndex"]
    para = paragraph("\n", start + 1)
    if object_type == "positioned":
        # Positioned objects consume no native character position.
        para = paragraph("\n", start)
        para["paragraph"]["positionedObjectIds"] = ["synthetic-drawing"]
    else:
        native = ({"inlineObjectElement": {"inlineObjectId": "synthetic-image"}}
                  if object_type == "inline" else
                  {"footnoteReference": {"footnoteId": "synthetic-footnote"}})
        para["startIndex"] = start
        para["paragraph"]["elements"].insert(0, {
            "startIndex": start, "endIndex": start + 1, **native,
        })
    cell["content"].extend([para, paragraph("After\n", para["endIndex"])])
    cell["endIndex"] = cell["content"][-1]["endIndex"]
    grid["table"]["tableRows"][0]["endIndex"] = cell["endIndex"]
    grid["endIndex"] = cell["endIndex"]
    document = {"body": {"content": [grid]}}
    before = deepcopy(document)
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch.object(docs, "get_document", return_value=document)
    replace = mocker.patch.object(docs, "replace_formatted")
    with pytest.raises(GdocError, match="cell contains non-text content") as exc:
        cmd_edit(edit_args(cell=cell_address))
    assert exc.value.exit_code == 3
    replace.assert_not_called()
    assert document == before


def test_whole_cell_refuses_an_unexplained_native_gap():
    cell = {"content": [paragraph("Left\n", 3), paragraph("Right\n", 9)]}
    with pytest.raises(GdocError, match="structural gap"):
        docs._cell_text_range(cell)


def test_cross_table_edit_refuses_before_mutation(mocker):
    middle = table([["Cell\n"]], start=6)
    document = {"body": {"content": [paragraph("Left\n", 1), middle,
                                       paragraph("Right\n", middle["endIndex"])]}}
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch.object(docs, "get_document_with_tabs", return_value=document)
    replace = mocker.patch.object(docs, "replace_formatted")
    with pytest.raises(GdocError, match="no match found") as exc:
        cmd_edit(edit_args(cell=None, old_text="Left\nRight", new_text="Joined"))
    assert exc.value.exit_code == 3
    replace.assert_not_called()


@pytest.mark.parametrize("normalize", [False, True])
@pytest.mark.parametrize("prefix", ["İ", "İİ", "İ😀"])
def test_lowercase_expansion_does_not_shift_paragraph_end(prefix, normalize):
    text = prefix + " PLAN\n"
    first = paragraph(text, 1)
    document = {"body": {"content": [first, paragraph("Next\n", first["endIndex"])]}}
    start = 2 + len(prefix.encode("utf-16-le")) // 2
    assert docs.find_text_in_document(document, "plan", normalize=normalize) == [
        {"startIndex": start, "endIndex": start + 4},
    ]
    # The native range contains only PLAN, never the final paragraph mark.
    native = text.encode("utf-16-le")
    assert native[(start - 1) * 2:(start + 3) * 2].decode("utf-16-le") == "PLAN"


@pytest.mark.parametrize(("needle", "expected"), [
    ("İ", [{"startIndex": 1, "endIndex": 2}]),
    ("i\u0307", [{"startIndex": 1, "endIndex": 2}]),
    ("i", [{"startIndex": 3, "endIndex": 4}]),
    ("\u0307", []),
    ("\u0307 i", []),
])
def test_lowercase_matches_must_cover_whole_original_characters(needle, expected):
    document = {"body": {"content": [paragraph("İ i\n", 1)]}}
    assert docs.find_text_in_document(document, needle) == expected


def test_case_sensitive_matches_keep_original_codepoints():
    document = {"body": {"content": [paragraph("İ PLAN\n", 1)]}}
    assert docs.find_text_in_document(document, "PLAN", match_case=True) == [
        {"startIndex": 3, "endIndex": 7},
    ]
    assert docs.find_text_in_document(document, "i", match_case=True) == []


def test_lowercase_mapping_preserves_contextual_greek_sigma():
    document = {"body": {"content": [paragraph("İ ΟΣ\n", 1)]}}
    assert docs.find_text_in_document(document, "ος") == [
        {"startIndex": 3, "endIndex": 5},
    ]


def test_typography_folding_and_lowercase_expansion_share_native_offsets():
    document = {"body": {"content": [paragraph("İ ‘PLAN’\n", 1)]}}
    assert docs.find_text_in_document(document, "'plan'", normalize=True) == [
        {"startIndex": 3, "endIndex": 9},
    ]


def test_unicode_cell_search_uses_native_range():
    grid = table([["Label\n", "İ PLAN\n"]])
    document = {"body": {"content": [grid]}}
    assert docs.find_text_in_document(document, "plan") == [
        {"startIndex": 13, "endIndex": 17},
    ]


def test_unicode_edit_does_not_delete_paragraph_mark(mocker):
    document = {"body": {"content": [paragraph("İ PLAN\n", 1),
                                       paragraph("Keep\n", 8)]}}
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch.object(docs, "get_document_with_tabs", return_value=document)
    replace = mocker.patch.object(docs, "replace_formatted", return_value=1)
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 1})
    mocker.patch("gdoc.state.update_state_after_command")
    assert cmd_edit(edit_args(cell=None, old_text="plan", new_text="TASK")) == 0
    assert replace.call_args.args[1] == [{"startIndex": 3, "endIndex": 7}]
    assert replace.call_args.args[2] == "TASK"


@pytest.mark.parametrize("tab_id", [None, "vendors-tab"])
@pytest.mark.parametrize("kind", [
    "inlineObjectElement", "footnoteReference", "positioned",
])
def test_every_batch_preserves_native_heading_content(mocker, kind, tab_id):
    # Contextual replacement preserves the native anchor without heuristic cleanup.
    native_width = 0 if kind == "positioned" else 1
    before_heading = paragraph("Old", 1, "HEADING_1")
    after_heading = paragraph("\n", 1 + native_width, "HEADING_1")
    after_heading["startIndex"] = 1
    if kind == "positioned":
        for heading in (before_heading, after_heading):
            heading["paragraph"]["positionedObjectIds"] = ["drawing"]
    else:
        native = ({"inlineObjectId": "image"} if kind == "inlineObjectElement"
                  else {"footnoteId": "note"})
        before_heading["paragraph"]["elements"].append(
            {"startIndex": 4, "endIndex": 5, kind: native})
        after_heading["paragraph"]["elements"].insert(
            0, {"startIndex": 1, "endIndex": 2, kind: native})
    before_heading["paragraph"]["elements"].extend(
        paragraph("\n", 4 + native_width)["paragraph"]["elements"])
    before_heading["endIndex"] = 5 + native_width
    before = {"body": {"content": [
        before_heading, paragraph("Old\n", 5 + native_width, "HEADING_2"),
        paragraph("Keep\n", 9 + native_width),
    ]}}
    after = {"revisionId": "after-edit", "body": {"content": [
        after_heading, paragraph("\n", 2 + native_width, "HEADING_2"),
        paragraph("Keep\n", 3 + native_width),
    ]}}
    service = mocker.MagicMock()
    service.documents().get().execute.return_value = after
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    if tab_id:
        after["tabs"] = [{"tabProperties": {"tabId": tab_id, "title": "Vendors"},
                          "documentTab": {"body": after["body"]}}]
    matches = docs.find_text_in_document(before, "Old")
    assert docs.replace_formatted("synthetic", matches, "", "before-edit", tab_id) == 2
    batches = [call.kwargs["body"]
               for call in service.documents().batchUpdate.call_args_list]
    assert len(batches) == 1
    for stage, batch in enumerate(batches):
        for request in batch["requests"]:
            if "deleteContentRange" not in request:
                continue
            span = request["deleteContentRange"]["range"]
            assert span.get("tabId") == tab_id
            # Native element (or positioned-object paragraph mark) survives
            # every batch, with its position adjusted after the primary edit.
            protected = 4 if stage == 0 else 1
            assert not span["startIndex"] <= protected < span["endIndex"]
        assert batch["writeControl"] == {
            "requiredRevisionId": "before-edit" if stage == 0 else "after-edit",
        }



@pytest.mark.parametrize("tab_id", [None, "vendors-tab"])
@pytest.mark.parametrize("first,second", [("i\u0307", "İ"), ("İ", "i\u0307")])
def test_unequal_match_widths_preserve_native_headings(mocker, tab_id, first, second):
    first_para = paragraph(first + "\n", 1, "HEADING_1")
    second_para = paragraph(second + "\n", first_para["endIndex"], "HEADING_2")
    before = {"body": {"content": [
        first_para, second_para, paragraph("\n", 6, "HEADING_3"),
        paragraph("Keep\n", 7),
    ]}}
    after = {"revisionId": "after-edit", "body": {"content": [
        paragraph("X\n", 1), paragraph("\n", 3, "HEADING_1"),
        paragraph("X\n", 4), paragraph("\n", 6, "HEADING_2"),
        paragraph("\n", 7, "HEADING_3"), paragraph("Keep\n", 8),
    ]}}
    if tab_id:
        after["tabs"] = [{"tabProperties": {"tabId": tab_id, "title": "Vendors"},
                          "documentTab": {"body": after["body"]}}]
    service = mocker.MagicMock()
    service.documents().get().execute.return_value = after
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    matches = docs.find_text_in_document(before, "İ")
    count = docs.replace_formatted("synthetic", matches, "X\n", "before-edit", tab_id)
    assert count == 2
    batches = [call.kwargs["body"]
               for call in service.documents().batchUpdate.call_args_list]
    assert len(batches) == 1
    deletions = [[r["deleteContentRange"]["range"] for r in b["requests"]
                  if "deleteContentRange" in r] for b in batches]
    assert [(r["startIndex"], r["endIndex"]) for r in deletions[0]] == [
        (second_para["startIndex"], second_para["endIndex"] - 1),
        (1, first_para["endIndex"] - 1),
    ]
    assert batches[0]["writeControl"] == {"requiredRevisionId": "before-edit"}
    assert all(r.get("tabId") == tab_id for batch in deletions for r in batch)
    service.documents().get.return_value.execute.assert_not_called()



@pytest.mark.parametrize("span", [(2, 3), (1, 3), (None, None)])
def test_cleanup_requires_verified_newline_indices(span):
    heading = paragraph("\n", 1, "HEADING_1")
    element = heading["paragraph"]["elements"][0]
    element["startIndex"], element["endIndex"] = span
    assert docs._build_cleanup_requests({"content": [heading]}, 1) == []


@pytest.mark.parametrize("col", [None, 2])
def test_value_column_label_collision_refuses_cli_before_any_batch(mocker, col):
    # Anonymous reconstruction of the live Vendors table: the earlier row's
    # Partner value repeats the later row's first-column label.
    grid = table([
        ["Vendor\n", "Partner\n", "Status\n"],
        ["Acme Cloud\n", "Datawise\n", "Rechazado\n"],
        ["Datawise\n", "Acme Cloud\n", "Pendiente Q3\n"],
        ["Northwind\n", "n/a\n", "Pendiente Q4\n"],
    ], start=40)
    document = {"revisionId": "before-edit", "tabs": [{
        "tabProperties": {"tabId": "vendors-tab", "title": "Vendors"},
        "documentTab": {"body": {"content": [grid]}},
    }]}
    original = deepcopy(document)
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 1})
    mocker.patch("gdoc.state.update_state_after_command")
    mocker.patch.object(docs, "get_document", return_value=document)
    mocker.patch.object(docs, "get_document_with_tabs", return_value=document)
    service = mocker.MagicMock()
    mocker.patch.object(docs, "get_docs_service", return_value=service)
    with pytest.raises(GdocError, match="ambiguous cell label") as exc:
        cmd_edit(edit_args(cell="Datawise", col=col, table=0, tab="Vendors",
                           old_text="Aprobado ✅"))
    assert exc.value.exit_code == 3
    assert "table 0 row 1 column 1" in str(exc.value)
    assert "table 0 row 2 column 0" in str(exc.value)
    service.documents().batchUpdate.assert_not_called()
    assert document == original
    # Explicit coordinates remain usable for the requested Status cell.
    assert docs.resolve_cell_range(document["tabs"][0]["documentTab"]["body"],
                                   "2,2", table_index=0) == {
        "startIndex": grid["table"]["tableRows"][2]["tableCells"][2]["startIndex"] + 1,
        "endIndex": grid["table"]["tableRows"][2]["tableCells"][2]["endIndex"] - 1,
    }
