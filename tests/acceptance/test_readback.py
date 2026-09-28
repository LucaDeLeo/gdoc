"""Bounded request application for paragraph rewrites and one fixed table.

These helpers are deliberately incomplete: no arbitrary mutation, nesting,
image layout or general table emulation. Unexpected request types fail loudly.
"""

from copy import deepcopy

import pytest
from conftest import paragraph, tab
from test_workflows import existing_helper, read, requests

from gdoc.mdparse import parse_inline, parse_markdown, utf16_len
from tests.native_model import NativeDoc


def paragraph_rewrite_readback(original, batch, *, tab_id="draft"):
    """Apply a full rewrite's text batch on the native model, from its insert.

    The requests before the rewrite's one insert at index 1 clear the old
    body; the model starts from the empty tab they leave.
    """
    first = next(i for i, r in enumerate(batch) if "insertText" in r)
    assert batch[first]["insertText"]["location"]["index"] == 1
    doc = NativeDoc()
    for request in batch[first:]:
        data = next(iter(request.values()))
        assert data.get("range", data.get("location", {})).get("tabId") == tab_id
        doc.apply(request)
    result = doc.document_tab(tab_id)
    result["body"]["content"] = [e for e in result["body"]["content"]
                                 if "sectionBreak" not in e]
    return result


COMBINATION = (
    "# Harbor\n"
    "Read ***[Original guide](https://example.invalid/a_(b))***.\n"
    "- Cargo\n"
    "> Keep the note\n"
    "```\n`closed span`\n# literal heading\n\nlast line\n```\n"
    "---\n"
    "Closing text.\n"
)


def assert_combination_meaning(markdown, adjective):
    parsed = parse_markdown(markdown)
    assert parsed.plain_text == (
        f"Harbor\nRead {adjective} guide.\nCargo\nKeep the note\n"
        "`closed span`\n# literal heading\n\nlast line\n\nClosing text.\n"
    )
    assert len(parsed.code_blocks) == 1
    code = parsed.code_blocks[0]
    assert (
        parsed.plain_text[code.start : code.end]
        == "`closed span`\n# literal heading\n\nlast line\n"
    )
    assert any(s.type == "bullets" for s in parsed.styles)
    assert any(s.style.get("namedStyleType") == "HEADING_1" for s in parsed.styles)
    assert any("borderBottom" in s.style for s in parsed.styles)
    phrase_start = len("Harbor\nRead ")
    phrase_end = phrase_start + len(adjective + " guide")
    for key in ("bold", "italic", "link"):
        assert any(
            s.start == phrase_start and s.end == phrase_end and key in s.style
            for s in parsed.styles
        )
    assert "> Keep the note" in markdown


def test_t04_changed_combination_has_request_applied_native_readback(scenario):
    read(scenario)
    before = deepcopy(scenario.document["tabs"][0]["documentTab"]["body"])
    scenario.ok("write", tab="draft", text=COMBINATION)
    result = paragraph_rewrite_readback(before, requests(scenario))
    scenario.document["tabs"][0]["documentTab"] = result
    scenario.document["revisionId"] = "r2"
    first = read(scenario)
    assert_combination_meaning(first, "Original")
    scenario.service.documents.return_value.batchUpdate.reset_mock()
    scenario.ok("write", tab="draft", text=first)
    assert not scenario.batches
    assert_combination_meaning(read(scenario), "Original")
    before = deepcopy(result["body"])
    scenario.ok("write", tab="draft", text=first.replace("Original", "Revised"))
    assert scenario.batches[0]["writeControl"] == {"requiredRevisionId": "r2"}
    result = paragraph_rewrite_readback(before, requests(scenario))
    scenario.document["tabs"][0]["documentTab"] = result
    assert_combination_meaning(read(scenario), "Revised")
    scenario.record["evidence"] = (
        "request_applied_paragraph_text_styles_bullets_code_quote_rule_then_public_native_read"
    )
    scenario.record["gap"] = (
        "Table/image combination and physical Google behavior are separate evidence"
    )


def fill_fixed_table(blank, batch):
    """Replay one 2x1 empty table's fill into its independently fixed API indices."""
    assert blank["table"]["tableRows"]
    rows = blank["table"]["tableRows"]
    assert len(rows) == 2 and all(len(r["tableCells"]) == 1 for r in rows)
    # Scaffolding cleanup comes first and lies after the table (its removal,
    # then restoring the paragraph after it), so it does not move the cells.
    cleanup = 0
    while cleanup < len(batch) and "insertText" not in batch[cleanup]:
        assert next(iter(batch[cleanup].values()))["range"]["startIndex"] >= (
            blank["endIndex"])
        cleanup += 1
    batch = batch[cleanup:]
    assert {next(iter(r)) for r in batch} <= {
        "insertText",
        "updateTextStyle",
        "updateParagraphStyle",
    }
    result = deepcopy(blank)
    inserts = [r["insertText"] for r in batch if "insertText" in r]
    assert len(inserts) == 2
    assert [r["location"]["index"] for r in inserts] == sorted(
        [r["location"]["index"] for r in inserts], reverse=True
    )
    assert all("insertText" in r for r in batch[:2])
    assigned_styles = []
    shift = 0
    for row in result["table"]["tableRows"]:
        cell = row["tableCells"][0]
        original_start = cell["content"][0]["startIndex"]
        matching = [r for r in inserts if r["location"]["index"] == original_start]
        assert len(matching) == 1
        text = matching[0]["text"]
        base = original_start + shift
        end = base + utf16_len(text) + 1
        local = [
            {"insertText": {"location": {"index": 1, "tabId": "tab"}, "text": text}}
        ]
        for index, request in enumerate(batch[2:], 2):
            address = next(iter(request.values()))["range"]
            if base <= address["startIndex"] < address["endIndex"] <= end:
                copied = deepcopy(request)
                local_address = next(iter(copied.values()))["range"]
                local_address["startIndex"] -= base - 1
                local_address["endIndex"] -= base - 1
                local_address["tabId"] = "tab"
                local.append(copied)
                assigned_styles.append(index)
        cell_result = paragraph_rewrite_readback(
            {"content": [paragraph("\n")]}, local, tab_id="tab"
        )
        cell["content"] = cell_result["body"]["content"]
        for p in cell["content"]:
            for node in [p, *p["paragraph"]["elements"]]:
                node["startIndex"] += base - 1
                node["endIndex"] += base - 1
        row.update(startIndex=base - 2, endIndex=end)
        cell.update(startIndex=base - 1, endIndex=end)
        shift += utf16_len(text)
    assert sorted(assigned_styles) == list(range(2, len(batch)))
    result["endIndex"] += shift
    return result


def test_t03_move_table_section_and_insert_section_with_readback(scenario):
    make_table = existing_helper("test_native_targets", "table")
    original_table = make_table([["Port\n"], ["East\n"]], 31)
    initial = [
        paragraph("Departure\n", style="HEADING_2"),
        paragraph("Check loads.\n", 11),
        paragraph("Cargo\n", 24, bullet={"listId": "original-list"}),
        paragraph("\n", 30),
        original_table,
        paragraph("Arrival\n", original_table["endIndex"], style="HEADING_2"),
    ]
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = initial
    sibling = tab("reference", "Reference", [paragraph("Keep sibling\n")])
    scenario.document["tabs"].append(sibling)
    protected = deepcopy(sibling)
    read(scenario)
    target = (
        "## Arrival\n## Departure\nCheck loads.\n- Cargo\n"
        "| Port |\n|:---|\n| **East** |\n## Sign-off\nConfirm.\n"
    )
    # Fixed API response for exactly this operation: the table is inserted at
    # Cargo's mark, so a 2x1 empty table follows Cargo, then Cargo's emptied
    # original mark and the placeholder, then Sign-off and Confirm.
    prefix = "Arrival\nDeparture\nCheck loads.\nCargo\n"
    start = 1 + utf16_len(prefix)
    blank = make_table([["\n"], ["\n"]], start)
    after = blank["endIndex"]
    scaffold = deepcopy(scenario.document)
    scaffold["revisionId"] = "r2"
    scaffold["tabs"][0]["documentTab"]["body"]["content"] = [
        paragraph("Arrival\n", style="HEADING_2"),
        paragraph("Departure\n", 9, style="HEADING_2"),
        paragraph("Check loads.\n", 19),
        paragraph("Cargo\n", 32, bullet={"listId": "rebuilt-list"}),
        blank,
        paragraph("\n", after, bullet={"listId": "rebuilt-list"}),
        paragraph("\n", after + 1),
        paragraph("Sign-off\n", after + 2, style="HEADING_2"),
        paragraph("Confirm.\n", after + 11),
    ]
    get = scenario.service.documents.return_value.get.return_value.execute
    get.side_effect = lambda **kw: deepcopy(
        scaffold if len(scenario.batches) >= 2 else scenario.document
    )
    scenario.ok("write", tab="draft", text=target)
    assert len(scenario.batches) == 3
    table_request = [
        r["insertTable"] for r in scenario.batches[1]["requests"] if "insertTable" in r
    ]
    assert len(table_request) == 1
    assert (table_request[0]["rows"], table_request[0]["columns"]) == (2, 1)
    assert table_request[0]["location"] == {"index": start - 1, "tabId": "draft"}
    assert not any("deleteContentRange" in r for r in scenario.batches[1]["requests"])
    # The fill stage removes exactly the two emptied paragraphs after the
    # table, one single empty paragraph per request.
    assert scenario.batches[2]["requests"][:2] == [{"deleteContentRange": {"range": {
        "startIndex": after, "endIndex": after + 1, "tabId": "draft"}}}] * 2
    # The surrounding text is produced from mutation requests, not copied from
    # target Markdown or the canned scaffold. Table structure is API-shape evidence.
    first_batch = scenario.batches[0]["requests"]
    assert first_batch[0]["deleteContentRange"]["range"] == {
        "startIndex": 1,
        "endIndex": initial[-1]["endIndex"] - 1,
        "tabId": "draft",
    }
    projected = paragraph_rewrite_readback(
        {"content": [paragraph("\n")]}, first_batch[1:]
    )
    filled = fill_fixed_table(blank, scenario.batches[2]["requests"])
    cells = [r["tableCells"][0] for r in filled["table"]["tableRows"]]
    assert [
        "".join(
            e["textRun"]["content"] for e in c["content"][0]["paragraph"]["elements"]
        ).removesuffix("\n")
        for c in cells
    ] == ["Port", "East"]
    elements = projected["body"]["content"]
    marker = next(i for i, p in enumerate(elements) if p["startIndex"] == start)
    delta = filled["endIndex"] - elements[marker]["endIndex"]
    for p in elements[marker + 1 :]:
        for node in [p, *p["paragraph"]["elements"]]:
            node["startIndex"] += delta
            node["endIndex"] += delta
    elements[marker : marker + 1] = [filled]
    scenario.document["tabs"][0]["documentTab"] = projected
    scenario.document["revisionId"] = "r2"
    get.side_effect = lambda **kw: deepcopy(scenario.document)
    result = read(scenario)
    assert (
        result.index("## Arrival")
        < result.index("## Departure")
        < result.index("| Port")
        < result.index("## Sign-off")
    )
    parsed = parse_markdown(result)
    assert [[parse_inline(c)[0] for c in row] for row in parsed.tables[0].rows] == [
        ["Port"],
        ["East"],
    ]
    assert parsed.tables[0].alignments == ["START"]
    assert "**East**" in result
    assert "Check loads.\n- Cargo\n" in result
    assert scenario.document["tabs"][1] == protected
    scenario.record["evidence"] = (
        "request_applied_text_and_cell_styles_with_fixed_API_table_scaffold_then_public_read"
    )


def test_review_quoted_list_and_nested_code_changed_roundtrip(scenario):
    source = '> 1. item\n>    ```\n>    literal *code*\n>    ```\n> 2. next\n'
    apply_requests = existing_helper('test_review_round1', 'native_readback')
    read(scenario)
    scenario.ok('write', tab='draft', text=source)
    result = apply_requests(parse_markdown(source), batch=requests(scenario))
    scenario.document['tabs'][0]['documentTab'] = result
    scenario.document['revisionId'] = 'r2'
    first = read(scenario)
    assert '> 1. item' in first and '> 2. next' in first
    assert '>    literal *code*' in first
    scenario.service.documents.return_value.batchUpdate.reset_mock()
    scenario.ok('write', tab='draft', text=first.replace('item', 'revised'))
    assert scenario.batches[0]['writeControl'] == {'requiredRevisionId': 'r2'}
    result = apply_requests(parse_markdown(first.replace('item', 'revised')),
                            batch=requests(scenario))
    scenario.document['tabs'][0]['documentTab'] = result
    second = read(scenario)
    assert '> 1. revised' in second and '> 2. next' in second
    assert '>    literal *code*' in second
    scenario.record['evidence'] = (
        'request-applied quote/list/code public changed readback'
    )


def _write_table_and_project(scenario, target, before, cells, revision):
    """Apply one staged table write: text batch, fixed 2x1 scaffold, fill.

    The table scaffold is fixed API-shape evidence, as in T03. Text, prefix
    ranges and cell text come from the mutation requests themselves.
    """
    make_table = existing_helper("test_native_targets", "table")
    start = 1 + utf16_len(before)
    blank = make_table([["\n"], ["\n"]], start)
    scaffold = deepcopy(scenario.document)
    scaffold["revisionId"] = revision
    # The table is inserted at the preceding paragraph's mark: its emptied
    # original mark and the placeholder follow the table.
    after = blank["endIndex"]
    scaffold["tabs"][0]["documentTab"] = {"body": {"content": [
        paragraph(before, 1), blank, paragraph("\n", after),
        paragraph("\n", after + 1), paragraph("\n", after + 2),
    ]}}
    service = scenario.service.documents.return_value
    service.batchUpdate.reset_mock()
    service.batchUpdate.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": revision}
    }
    service.get.return_value.execute.side_effect = lambda **kw: deepcopy(
        scaffold if len(scenario.batches) >= 2 else scenario.document
    )
    scenario.ok("write", tab="draft", text=target)
    assert len(scenario.batches) == 3
    first_batch = scenario.batches[0]["requests"]
    assert "deleteContentRange" in next(r for r in first_batch
                                        if "deleteNamedRange" not in r)
    projected = paragraph_rewrite_readback(
        {"content": [paragraph("\n")]}, first_batch
    )
    fill = scenario.batches[2]["requests"]
    ranges = [r["createNamedRange"] for r in fill if "createNamedRange" in r]
    filled = fill_fixed_table(blank, [r for r in fill if "createNamedRange" not in r])
    assert [
        "".join(
            e["textRun"]["content"]
            for e in row["tableCells"][0]["content"][0]["paragraph"]["elements"]
        ).removesuffix("\n")
        for row in filled["table"]["tableRows"]
    ] == cells
    elements = projected["body"]["content"]
    marker = next(i for i, p in enumerate(elements) if p["startIndex"] == start)
    delta = filled["endIndex"] - elements[marker]["endIndex"]
    for p in elements[marker + 1 :]:
        for node in [p, *p["paragraph"]["elements"]]:
            node["startIndex"] += delta
            node["endIndex"] += delta
    for group in projected["namedRanges"].values():
        for named in group["namedRanges"]:
            for r in named["ranges"]:
                assert not r["startIndex"] < start < r["endIndex"]
                if r["startIndex"] >= start:
                    r["startIndex"] += delta
                    r["endIndex"] += delta
    elements[marker : marker + 1] = [filled]
    for data in ranges:
        projected["namedRanges"].setdefault(data["name"], {"namedRanges": []})[
            "namedRanges"
        ].append({"name": data["name"], "ranges": [data["range"]]})
    scenario.document["tabs"][0]["documentTab"] = projected
    scenario.document["revisionId"] = revision
    service.get.return_value.execute.side_effect = lambda **kw: deepcopy(
        scenario.document
    )
    return ranges


@pytest.mark.parametrize(
    ("source", "before", "lead"),
    [
        ("> Intro\n> | Port |\n> |:--|\n> | East |\n> Outro\nAfter\n",
         "Intro\n", "> "),
        ("> > Intro\n> > | Port |\n> > |:--|\n> > | East |\nAfter\n",
         "Intro\n", "> > "),
        ("- Intro\n  | Port |\n  |:--|\n  | East |\n- Outro\n", "Intro\n", "  "),
    ],
)
def test_contained_table_changed_roundtrip_through_native_write(
    scenario, source, before, lead
):
    read(scenario)
    ranges = _write_table_and_project(
        scenario, source, before, ["Port", "East"], "r2"
    )
    assert len(ranges) == 1 and ranges[0]["name"].startswith("gdoc:prefix:v1:")
    first = read(scenario)
    assert f"{lead}| Port |\n{lead}| :--- |\n{lead}| East |\n" in first
    assert "After" not in source or "\nAfter\n" in first
    changed = first.replace("East", "West")
    parsed = parse_markdown(changed)
    assert len(parsed.tables) == 1
    assert parsed.tables[0].alignments == ["START"]
    _write_table_and_project(scenario, changed, before, ["Port", "West"], "r3")
    assert scenario.batches[0]["writeControl"] == {"requiredRevisionId": "r2"}
    second = read(scenario)
    assert f"{lead}| Port |\n{lead}| :--- |\n{lead}| West |\n" in second
    assert second.replace("West", "East") == first
    scenario.record["evidence"] = (
        "request-applied contained table with fixed API scaffold, changed readback"
    )
