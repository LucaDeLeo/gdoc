"""R7-2: `write` honours a pulled file's source document and tab.

A file pulled from one tab is never silently written into another: without
--tab it replaces the tab it came from, and a contradicting --tab, a different
document or a missing source tab is refused before any mutation. Metadata-free
input keeps the documented first-tab default.
"""

import json

import pytest

from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _two_tabs(route):
    """The modeled tab is Main (t.0); Appendix (t.1) is a static sibling."""
    route.load(NativeDoc(("p", "Main tab content.")))
    appendix = NativeDoc(("p", "Appendix content.")).document_tab("t.1")
    route.service.extra_tabs = [{
        "tabProperties": {"tabId": "t.1", "title": "Appendix", "index": 1},
        "documentTab": appendix,
    }]


def _pulled(route, tab_id="t.1", doc="synthetic", body="Appendix revised.\n"):
    route.ok("cat", tab="Main")
    route.ok("cat", tab="Appendix")
    return (f"---\ngdoc: {doc}\ntitle: Synthetic\ntab: {tab_id}\n"
            f"gdoc-revision: r1\n---\n{body}")


def _targets(route, since):
    """The tab IDs addressed by the batches sent after *since* batches."""
    found = set()
    for batch in route.service.batches[since:]:
        text = json.dumps(batch)
        found.update(t for t in ("t.0", "t.1") if f'"tabId": "{t}"' in text)
    return found


def test_pulled_tab_file_replaces_its_own_tab_without_tab_flag(route):
    _two_tabs(route)
    text = _pulled(route)
    code, output, error = route.call("write", text=text)
    assert code == 0, output + error
    assert "Appendix" in output and "t.1" in output
    assert route.service.batches and _targets(route, 0) == {"t.1"}


def test_contradicting_tab_flag_is_refused_before_mutation(route):
    _two_tabs(route)
    text = _pulled(route)
    code, output, error = route.call("write", text=text, tab="Main")
    assert code != 0
    assert "pulled from tab 'Appendix'" in output + error
    assert route.service.batches == []


def test_matching_tab_flag_writes_that_tab(route):
    _two_tabs(route)
    text = _pulled(route)
    code, output, error = route.call("write", text=text, tab="Appendix")
    assert code == 0, output + error
    assert _targets(route, 0) == {"t.1"}


def test_file_from_another_document_is_refused(route):
    _two_tabs(route)
    text = _pulled(route, doc="another-document")
    code, output, error = route.call("write", text=text)
    assert code != 0
    assert "pulled from document another-document" in output + error
    assert route.service.batches == []


def test_missing_source_tab_needs_an_explicit_tab(route):
    _two_tabs(route)
    text = _pulled(route, tab_id="t.9")
    code, output, error = route.call("write", text=text)
    assert code != 0
    assert "no longer has" in output + error
    assert route.service.batches == []
    code, output, error = route.call("write", text=text, tab="Main")
    assert code == 0, output + error


def test_collapse_refuses_a_file_pulled_from_a_later_tab(route):
    _two_tabs(route)
    text = _pulled(route)
    code, output, error = route.call("write", text=text, force_collapse_tabs=True)
    assert code != 0
    assert "--force-collapse-tabs replaces the first tab" in output + error
    assert route.service.batches == []


@pytest.mark.parametrize("text", [
    "New first tab text.\n",
    "---\n---\nNew first tab text.\n",
    "---\ntitle: notes\n---\nNew first tab text.\n",
])
def test_metadata_free_input_keeps_the_first_tab_default(route, text):
    _two_tabs(route)
    route.ok("cat", tab="Main")
    code, output, error = route.call("write", text=text)
    assert code == 0, output + error
    assert "'Main' (t.0)" in output
    assert route.service.doc.paragraphs()


@pytest.mark.parametrize("tool", ["gdoc_write", "gdoc_insert"])
def test_write_tools_state_the_paragraph_format(tool):
    """R7-7: the line-per-paragraph format is stated where agents look."""
    from gdoc import mcp

    description = mcp.build_tools()[tool]["description"]
    assert "each line is one paragraph" in description
    assert "empty paragraph" in description


def test_line_and_blank_line_paragraph_format(route):
    """R7-7: the documented format, pinned natively."""
    route.load(NativeDoc(("p", "seed")))
    route.ok("cat")
    route.ok("write", text="a\nb\n\nc\n")
    texts = [text for text, *_ in styles(route.service.doc)]
    assert texts == ["a", "b", "", "c"]


@pytest.mark.parametrize("text", [
    "---\n\nNote: keep me\n\n---\n\nafter\n",
    "---\n\nSee [docs](https://e.org/).\n\n---\n",
    "---\nhttps://example.com\n---\n",
    "---\nNote\\: keep me\n---\n",
])
def test_rule_first_body_with_colon_prose_is_written(route, text):
    """R7-8: rules and prose are content, not metadata."""
    route.load(NativeDoc(("p", "seed")))
    route.ok("cat")
    route.ok("write", text=text)
    texts = [t for t, *_ in styles(route.service.doc)]
    assert any(t in ("Note: keep me", "See docs.", "https://example.com")
               for t in texts)
    assert route.ok("cat").startswith("---\n---\n---\n")


def test_generic_tab_metadata_without_gdoc_provenance_is_ignored(route):
    """R7-2 follow-up: another tool's `tab` field never selects the tab."""
    _two_tabs(route)
    route.ok("cat", tab="Main")
    route.ok("cat", tab="Appendix")
    text = "---\ntitle: Release notes\ntab: Appendix\n---\nNotes.\n"
    code, output, error = route.call("write", text=text)
    assert code == 0, output + error
    assert "'Main' (t.0)" in output
    assert "t.1" not in _targets(route, 0)


@pytest.mark.parametrize("failing", ["_insert_images", "_apply_page_mode"])
def test_new_from_file_failure_after_creation_names_the_document(
    monkeypatch, tmp_path, failing,
):
    """A failure after `new --file` created the document reports its ID."""
    import contextlib
    import io
    from unittest.mock import patch

    from gdoc import cli

    source = tmp_path / "doc.md"
    source.write_text("![a](https://example.org/a.png)\n")
    created = {"id": "created_doc_123", "name": "From File", "version": 1,
               "webViewLink": "https://docs.example/created_doc_123"}
    monkeypatch.setattr("gdoc.cli._apply_page_mode", lambda *a, **k: None)
    monkeypatch.setattr("gdoc.cli._insert_images", lambda *a, **k: None)
    monkeypatch.setattr(f"gdoc.cli.{failing}",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lost")))
    out, err = io.StringIO(), io.StringIO()
    creating = patch("gdoc.api.drive.create_doc_from_markdown", return_value=created)
    with creating as create, contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        code = cli.run_argv(["new", "From File", "--file", str(source)],
                            check_updates=False)
    assert create.call_count == 1
    assert code == 1
    assert "created document created_doc_123" in err.getvalue()
    assert "rerunning `new --file`" in err.getvalue()


def test_push_collapse_refuses_a_file_pulled_from_a_later_tab(monkeypatch, tmp_path):
    """Round-8 recheck: push and write agree on --force-collapse-tabs."""
    import contextlib
    import io

    from gdoc import cli

    route = NativeRoute("cli", monkeypatch, tmp_path)
    _two_tabs(route)
    path = tmp_path / "appendix.md"

    def run(*argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return cli.run_argv(list(argv), check_updates=False), out.getvalue()

    assert run("pull", "synthetic", str(path), "--tab", "Appendix")[0] == 0
    route.ok("cat", tab="Main")
    path.write_text(path.read_text().replace("Appendix content.", "Revised."))
    code, output = run("push", str(path), "--force-collapse-tabs")
    assert code == 3 and "--force-collapse-tabs replaces the first tab" in output
    assert route.service.batches == []


def test_collapse_of_a_file_from_a_vanished_tab_gives_usable_advice(
    monkeypatch, tmp_path,
):
    """Final recheck: the refusal does not suggest --tab with a collapse."""
    route = NativeRoute("cli", monkeypatch, tmp_path)
    _two_tabs(route)
    text = _pulled(route, tab_id="t.9")
    code, output, error = route.call("write", text=text, force_collapse_tabs=True)
    assert code == 3 and "Remove the file's frontmatter" in output + error
    assert "Pass --tab" not in output + error
    assert route.service.batches == []
