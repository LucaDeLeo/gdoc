"""R6-8 and R6-16: targeted edits respect suggestions and name their tab."""

import json

import pytest

from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def suggested(route, start, end, kind="suggestedDeletionIds"):
    """Mark [start, end) as a collaborator's pending suggestion in reads."""
    snapshot = route.service.snapshot

    def with_suggestion():
        value = snapshot()
        content = value["tabs"][0]["documentTab"]["body"]["content"]
        for element in content:
            runs = element.get("paragraph", {}).get("elements", [])
            for position, run in enumerate(list(runs)):
                if "textRun" not in run or not (
                        run["startIndex"] < end and run["endIndex"] > start):
                    continue
                text = run["textRun"]["content"]
                a, b = max(start, run["startIndex"]), min(end, run["endIndex"])
                pieces = []
                for low, high, marked in [(run["startIndex"], a, False),
                                          (a, b, True), (b, run["endIndex"], False)]:
                    if low < high:
                        piece = {"startIndex": low, "endIndex": high, "textRun": {
                            "content": text[low - run["startIndex"]:
                                            high - run["startIndex"]]}}
                        if marked:
                            piece["textRun"][kind] = ["s1"]
                        pieces.append(piece)
                runs[position:position + 1] = pieces
                return value
        return value

    route.service.snapshot = with_suggestion


def test_edit_over_a_pending_suggestion_is_refused(route):
    doc = route.load(NativeDoc(("p", "Alpha beta gamma."), ("p", "Second.")))
    suggested(route, 7, 12)  # "beta "
    route.ok("cat")
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text="beta", new_text="BETA")
    assert code != 0
    assert "overlaps existing suggestion(s) s1" in output + error
    assert "accept or reject them in Docs first" in output + error
    assert len(route.service.batches) == batches
    assert "beta" in "".join(u.ch for u in doc.units)


def test_edit_elsewhere_is_not_blocked_by_a_suggestion(route):
    doc = route.load(NativeDoc(("p", "Alpha beta gamma."), ("p", "Second.")))
    suggested(route, 7, 12)
    route.ok("cat")
    batches = len(route.service.batches)
    route.ok("edit", old_text="Second", new_text="Third")
    assert len(route.service.batches) > batches
    assert "Third." in "".join(u.ch for u in doc.units)


def _two_tabs(route):
    other = NativeDoc(("p", "Summary here.")).document_tab("t.1")
    route.load(NativeDoc(("p", "Intro text is long enough."), ("p", "More.")))
    route.service.extra_tabs = [{"tabProperties": {
        "tabId": "t.1", "title": "Appendix", "index": 1}, "documentTab": other}]


def test_single_match_in_another_tab_names_that_tab(route):
    _two_tabs(route)
    output = route.ok("edit", old_text="Summary", new_text="Overview")
    assert "OK replaced 1 occurrence" in output and "tab t.1: 1" in output


def test_json_edit_reports_the_changed_tab(route):
    _two_tabs(route)
    result = json.loads(route.ok("edit", old_text="Intro", new_text="Opening",
                                 json=True))
    assert result["replaced"] == 1 and result["tabs"] == {"t.0": 1}


@pytest.mark.parametrize("value_key", ["new_text", "old_text"])
def test_cell_edit_value_is_accepted_in_either_slot(route, value_key):
    """R6-18: CLI and MCP cell edits take the value as the one positional."""
    doc = route.load(NativeDoc(("t", [["Name", "Value"], ["Owner", "Ann"]])))
    route.ok("cat")
    route.ok("edit", cell="1,1", tab="Main", **{value_key: "Bob"})
    assert "Bob" in "".join(u.ch for u in doc.units)
    assert "Ann" not in "".join(u.ch for u in doc.units)


def test_mcp_edit_tool_carries_the_cli_usage_rules():
    from gdoc import mcp

    tools = mcp.build_tools()
    description = tools["gdoc_edit"]["description"]
    assert "cat --plain" in description


def test_image_revision_refusal_is_a_clean_exit_3():
    """R6-15: a refused pinned revision applied nothing, as for edits."""
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    from gdoc.api.docs import _raise_if_stale_revision
    from gdoc.util import GdocError

    error = HttpError(Response({"status": 400}),
                      b'{"error": {"message": "The revision ID is stale"}}')
    with pytest.raises(GdocError, match="re-run it") as exc:
        _raise_if_stale_revision(error)
    assert exc.value.exit_code == 3
