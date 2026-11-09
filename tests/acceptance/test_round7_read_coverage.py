"""R6-6 and R6-17: reads say what they leave out and which tabs exist.

A Markdown read of a tab with footnotes, chips or other richer content is
reported as incomplete, with the omitted content named, through CLI and MCP.
It still pins the revision it saw, so a rewrite needs explicit --allow-lossy
consent to discard that content and stays revision-protected.
"""

import contextlib
import io
import json

import pytest

from gdoc import cli
from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def with_rich(service, where):
    """Add a footnote reference to the first paragraph, or a person chip to
    the first table cell."""
    snapshot = service.snapshot
    service.rich_from = len(service.batches)

    def rich():
        value = snapshot()
        if len(service.batches) > service.rich_from:
            return value  # a consented rewrite discarded the rich element
        content = value["tabs"][0]["documentTab"]["body"]["content"]
        if where == "cell":
            table = next(e["table"] for e in content if "table" in e)
            paragraph = table["tableRows"][0]["tableCells"][0]["content"][0]
            paragraph["paragraph"]["elements"].insert(0, {"person": {
                "personProperties": {"email": "a@example.org"}}})
        else:
            paragraph = next(e for e in content if "paragraph" in e)
            paragraph["paragraph"]["elements"].insert(0, {
                "footnoteReference": {"footnoteId": "f1"}})
        return value

    service.snapshot = rich


CASES = [("body", ("p", "See it."), "footnotes"),
         ("cell", ("t", [["h"], ["v"]]), "people chips")]


@pytest.mark.parametrize("where,block,label", CASES)
def test_cat_reports_omitted_rich_content(route, where, block, label):
    route.load(NativeDoc(block, ("p", "After.")))
    with_rich(route.service, where)
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is False
    assert scope["omitted"] == [label]
    code, output, notes = route.call("cat")
    assert code == 0 and f"leaves out native content: {label}" in notes


@pytest.mark.parametrize("where,block,label", CASES)
def test_omitting_read_needs_consent_and_keeps_revision_protection(
    route, where, block, label,
):
    route.load(NativeDoc(block, ("p", "After.")))
    with_rich(route.service, where)
    text = parse_frontmatter(route.ok("cat"))[1]
    changed = text.replace("After.", "After, changed.")
    code, output, error = route.call("write", text=changed)
    assert code == 1 or code == 3
    assert "--allow-lossy" in output + error and label in output + error
    batches = len(route.service.batches)
    route.ok("write", text=changed, allow_lossy=True)
    assert len(route.service.batches) > batches
    # A collaborator edit after the read still blocks, consent or not.
    route.ok("cat")
    route.service.doc.apply({"insertText": {"location": {"index": 1},
                                            "text": "X"}})
    route.service.revision += 1
    code, output, error = route.call(
        "write", text=changed.replace("changed", "again"), allow_lossy=True)
    assert code != 0 and "changed since last read" in output + error


def test_complete_read_has_no_omissions(route):
    route.load(NativeDoc(("p", "Plain."), ("t", [["h"], ["v"]])))
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is True and "omitted" not in scope


def test_json_scope_counts_all_tabs(route):
    """R6-17: JSON reads carry the tab count that the stderr note gives."""
    route.load(NativeDoc(("p", "Main text.")))
    route.service.extra_tabs = [{
        "tabProperties": {"tabId": "t.1", "title": "Notes", "index": 1},
        "documentTab": {"body": {"content": [
            {"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]}}}]
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["tab_ids"] == ["t.0"] and scope["tab_count"] == 2


def _run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.run_argv(list(argv), check_updates=False)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("where,block,label", CASES)
def test_pull_and_markdown_export_report_omissions(
    monkeypatch, tmp_path, where, block, label,
):
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(block, ("p", "After.")))
    with_rich(route.service, where)
    path = tmp_path / "doc.md"
    code, out, err = _run("--json", "pull", "synthetic", str(path))
    assert code == 0 and label in err
    result = json.loads(out)
    assert result["complete"] is False and result["omitted"] == [label]
    code, out, err = _run("export", "synthetic", "--out", str(tmp_path / "x.md"))
    assert code == 0 and f"leaves out native content: {label}" in err


def _state():
    from gdoc.state import load_state
    return load_state("synthetic")


@pytest.mark.parametrize("where,block,label", CASES)
def test_omitting_reads_record_limited_coverage_only(route, where, block, label):
    route.load(NativeDoc(block, ("p", "After.")))
    with_rich(route.service, where)
    text = parse_frontmatter(route.ok("cat"))[1]
    state = _state()
    assert state.limited_read_revision_ids == {"t.0": "r1"}
    assert "t.0" not in state.read_revision_ids
    # An unchanged write-back neither writes nor upgrades the coverage.
    assert "already in sync" in route.ok("write", text=text)
    assert _state().read_revision_ids.get("t.0") is None
    # Targeted insertion keeps the omitted content, so limited coverage allows it.
    batches = len(route.service.batches)
    route.ok("insert", text="Inserted.", tab="Main", position="end")
    assert len(route.service.batches) > batches
    route.service.rich_from = len(route.service.batches)  # insertion keeps it
    assert _state().limited_read_revision_ids == {"t.0": f"r{route.service.revision}"}
    # A consented replacement writes exactly the Markdown: complete coverage.
    text = parse_frontmatter(route.ok("cat"))[1]
    code, output, error = route.call("write", text=text.replace("After.", "Later."))
    assert code != 0 and label in output + error
    route.ok("write", text=text.replace("After.", "Later."), allow_lossy=True)
    state = _state()
    assert state.read_revision_ids == {"t.0": f"r{route.service.revision}"}
    assert state.limited_read_revision_ids == {}


@pytest.mark.parametrize("where,block,label", CASES)
def test_pulled_file_with_omissions_needs_consent_even_after_sibling_edits(
    monkeypatch, tmp_path, where, block, label,
):
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(block, ("p", "After.")))
    route.service.extra_tabs = [{
        "tabProperties": {"tabId": "t.1", "title": "Notes", "index": 1},
        "documentTab": {"body": {"content": [
            {"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]}}}]
    with_rich(route.service, where)
    path = tmp_path / "doc.md"
    assert _run("pull", "synthetic", str(path))[0] == 0
    assert _state().limited_read_revision_ids == {"t.0": "r1"}
    # A sibling edit changes the revision; the tab's native content is the same.
    route.service.extra_tabs[0]["documentTab"]["body"]["content"].append(
        {"startIndex": 1, "endIndex": 3, "paragraph": {"elements": [
            {"startIndex": 1, "endIndex": 3, "textRun": {"content": "N\n"}}]}})
    route.service.revision += 1
    path.write_text(path.read_text().replace("After.", "Later."))
    code, out, err = _run("push", str(path))
    assert code == 3 and label in err
    assert _state().limited_read_revision_ids == {"t.0": "r2"}
    code, out, err = _run("push", str(path), "--allow-lossy")
    assert code == 0, err
    assert _state().read_revision_ids["t.0"] == f"r{route.service.revision}"


def test_markdown_export_records_limited_coverage(monkeypatch, tmp_path):
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "See it."), ("p", "After.")))
    with_rich(route.service, "body")
    assert _run("export", "synthetic", "--out", str(tmp_path / "x.md"))[0] == 0
    state = _state()
    assert state.limited_read_revision_ids == {"t.0": "r1"}
    assert "t.0" not in state.read_revision_ids


def with_drawing(service):
    snapshot = service.snapshot

    def drawing():
        value = snapshot()
        tab = value["tabs"][0]["documentTab"]
        paragraph = next(e for e in tab["body"]["content"] if "paragraph" in e)
        paragraph["paragraph"]["elements"].insert(0, {
            "inlineObjectElement": {"inlineObjectId": "drawing"}})
        tab["inlineObjects"] = {"drawing": {"inlineObjectProperties": {
            "embeddedObject": {"embeddedDrawingProperties": {}}}}}
        return value

    service.snapshot = drawing


def test_drawings_are_reported_as_omitted(route):
    """Internal review P2: only images read faithfully as references."""
    route.load(NativeDoc(("p", "See it."), ("p", "After.")))
    with_drawing(route.service)
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is False
    assert scope["omitted"] == ["inline images or embedded objects"]
    assert _state().limited_read_revision_ids == {"t.0": "r1"}


def test_images_are_not_omissions(route):
    route.load(NativeDoc(("p", "See it."), ("p", "After.")))
    route.service.doc.apply({"insertInlineImage": {
        "location": {"index": 1}, "uri": "https://example.org/i.png"}})
    scope = json.loads(route.ok("cat", json=True))["scope"]
    assert scope["complete"] is True and "omitted" not in scope


@pytest.mark.parametrize("to_file", [False, True])
def test_markdown_export_json_reports_coverage(monkeypatch, tmp_path, to_file):
    """Internal review P2: export --json carries complete and omitted."""
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "See it."), ("p", "After.")))
    with_rich(route.service, "body")
    argv = ["--json", "export", "synthetic", "--format", "md"]
    if to_file:
        argv += ["--out", str(tmp_path / "x.md")]
    code, out, err = _run(*argv)
    result = json.loads(out)
    assert code == 0 and result["complete"] is False
    assert result["omitted"] == ["footnotes"]
