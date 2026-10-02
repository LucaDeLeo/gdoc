"""R7-3 and R7-4: richer content a Markdown read cannot keep is reported.

A linked Sheets chart reads as an image reference, but a rewrite could insert
only its rendered image. Block layout inside a table cell (a native rule, an
indented paragraph) has no pipe-table spelling. Both make the read limited and
need explicit --allow-lossy consent to rewrite, while targeted edits elsewhere
proceed. Through CLI and MCP.
"""

import json

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc

CHART = "linked charts (a rewrite keeps only a static image)"


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def with_chart(service):
    """Put a linked Sheets chart before the first paragraph's text."""
    snapshot = service.snapshot
    service.rich_from = len(service.batches)

    def chart():
        value = snapshot()
        if len(service.batches) > service.rich_from:
            return value  # a consented rewrite replaced the chart
        tab = value["tabs"][0]["documentTab"]
        paragraph = next(e for e in tab["body"]["content"] if "paragraph" in e)
        paragraph["paragraph"]["elements"].insert(0, {
            "inlineObjectElement": {"inlineObjectId": "chart1"}})
        tab["inlineObjects"] = {"chart1": {"inlineObjectProperties": {
            "embeddedObject": {
                "imageProperties": {"contentUri": "https://example.org/c.png"},
                "linkedContentReference": {"sheetsChartReference": {
                    "spreadsheetId": "sheet", "chartId": 7}},
            }}}}
        return value

    service.snapshot = chart


def test_linked_chart_read_is_limited(route):
    route.load(NativeDoc(("p", "Chart."), ("p", "After.")))
    with_chart(route.service)
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is False
    assert scope["omitted"] == [CHART]


def test_linked_chart_rewrite_needs_consent(route):
    route.load(NativeDoc(("p", "Chart."), ("p", "After.")))
    with_chart(route.service)
    text = parse_frontmatter(route.ok("cat"))[1]
    changed = text.replace("After.", "After, changed.")
    assert changed != text
    code, output, error = route.call("write", text=changed)
    assert code != 0
    assert "--allow-lossy" in output + error and "linked charts" in output + error
    assert route.service.batches == []
    route.ok("write", text=changed, allow_lossy=True)
    assert route.service.batches


def test_linked_chart_does_not_block_a_targeted_edit(route):
    route.load(NativeDoc(("p", "Chart."), ("p", "After.")))
    with_chart(route.service)
    route.ok("cat")
    route.ok("edit", old_text="After.", new_text="After, edited.")
    assert route.service.batches


def with_cell_block(service, kind):
    """Give the second row's cell an indented paragraph or a native rule."""
    snapshot = service.snapshot
    service.rich_from = len(service.batches)

    def block():
        value = snapshot()
        if len(service.batches) > service.rich_from:
            return value
        content = value["tabs"][0]["documentTab"]["body"]["content"]
        table = next(e["table"] for e in content if "table" in e)
        paragraph = table["tableRows"][1]["tableCells"][0]["content"][0]["paragraph"]
        if kind == "indent":
            paragraph["paragraphStyle"]["indentStart"] = {"magnitude": 36, "unit": "PT"}
        else:
            paragraph["elements"].insert(0, {"horizontalRule": {}})
        return value

    service.snapshot = block


CELL_LABELS = {"indent": "(indented cell paragraphs become plain text)",
               "rule": "(rules inside cells are dropped)"}


@pytest.mark.parametrize("kind", list(CELL_LABELS))
def test_block_layout_in_a_cell_is_reported_and_needs_consent(route, kind):
    route.load(NativeDoc(("p", "Before."), ("t", [["h"], ["v"]]), ("p", "After.")))
    with_cell_block(route.service, kind)
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is False
    assert any(CELL_LABELS[kind] in item for item in scope["omitted"])
    text = parse_frontmatter(route.ok("cat"))[1]
    changed = text.replace("After.", "After, changed.")
    code, output, error = route.call("write", text=changed)
    assert code != 0 and CELL_LABELS[kind] in output + error
    assert route.service.batches == []
    route.ok("write", text=changed, allow_lossy=True)
    assert route.service.batches


@pytest.mark.parametrize("kind", list(CELL_LABELS))
def test_block_layout_in_a_cell_does_not_block_a_targeted_edit(route, kind):
    route.load(NativeDoc(("p", "Before."), ("t", [["h"], ["v"]]), ("p", "After.")))
    with_cell_block(route.service, kind)
    route.ok("cat")
    route.ok("edit", old_text="After.", new_text="After, edited.")
    assert route.service.batches


@pytest.mark.parametrize("markdown", [
    "| h | g |\n| :--- | ---: |\n| v | w |\n\nafter\n",
    "- item\n\n  | h |\n  | --- |\n  | v |\n- next\n",
    "> quote\n> \n> | h |\n> | --- |\n> | v |\n\nafter\n",
    "1. a\n\n   > q\n   > \n   > | h |\n   > | --- |\n   > | v |\n2. b\n",
])
def test_tables_gdoc_writes_read_complete(route, markdown):
    """Ordinary and contained tables carry no cell indent or rule."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is True and "omitted" not in scope


def test_custom_named_range_read_is_limited_and_consistent_with_write(route):
    """R7-9: a read names the range a rewrite would need consent to remove."""
    doc = route.load(NativeDoc(("p", "Alpha."), ("p", "Beta.")))
    doc.named.append(["integration:anchor", 1, 6])
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is False
    assert scope["omitted"] == ["custom named ranges"]
    code, output, error = route.call("write", text="Alpha changed.\nBeta.\n")
    assert code != 0 and "custom named ranges" in output + error
    assert route.service.batches == []
    route.ok("edit", old_text="Beta", new_text="Gamma")
    assert ["integration:anchor", 1, 6] in doc.named
    batches = len(route.service.batches)
    route.ok("write", text="Alpha changed.\nGamma.\n", allow_lossy=True)
    assert len(route.service.batches) > batches
    assert parse_frontmatter(route.ok("cat"))[1] == "Alpha changed.\nGamma.\n"
    # What Docs does with a range whose text is deleted is not modeled here.
