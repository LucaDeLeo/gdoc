"""Independent review round 3 of e300ee6 (R3-xx): regressions through CLI and
MCP on the native model."""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc

TABLE = "| k | v |\n| --- | --- |\n| S | {} |\n"
IMAGE = "![i](http://x/i.png)"


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _written(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    route.ok("cat")
    return doc


def _refused(route, command, needle, **arguments):
    before, batches = _read(route), len(route.service.batches)
    code, output, error = route.call(command, **arguments)
    assert code != 0 and needle in output + error, output + error
    assert len(route.service.batches) == batches and _read(route) == before


@pytest.mark.parametrize("base,old", [
    ("1. alpha\n2. bravo\n\n   detail\n3. charlie\n", "bravo\n"),
    ("1. alpha\n2. bravo\n  1. child\n3. charlie\n", "bravo\n"),
])
def test_r3_01_deleting_an_item_that_owns_content_is_refused(route, base, old):
    _written(route, base)
    _refused(route, "edit", "list's structure", old_text=old, new_text="")


def test_r3_01_an_item_deleted_with_its_content_is_removed(route):
    _written(route, "1. alpha\n2. bravo\n\n   detail\n3. charlie\n")
    route.ok("edit", old_text="bravo\n\ndetail\n", new_text="")
    assert _read(route) == "1. alpha\n2. charlie\n"


@pytest.mark.parametrize("fence", ["```bash", "~~~"])
def test_r3_02_a_fence_on_a_marker_line_stays_in_its_item(route, fence):
    closer = fence[:3]
    markdown = (f"1. {fence}\n   npm install\n   {closer}\n2. Run it\n\n"
                "## Next\n\nMore prose.\n")
    _written(route, markdown)
    got = _read(route)
    # Both fence lines are literal item text; later blocks are untouched.
    fence_text = "\\" + "\\".join(fence[:3]) + fence[3:]
    closer_text = "\\" + "\\".join(closer)
    assert got == (f"1. {fence_text}\n   npm install\n   {closer_text}\n2. Run it"
                   "\n\n## Next\n\nMore prose.\n")
    route.ok("write", text=got)
    assert _read(route) == got


@pytest.mark.parametrize("new", [IMAGE, "x " + IMAGE])
def test_r3_03_collapsing_a_cell_onto_an_image_is_refused(route, new):
    _written(route, TABLE.format("a<br>b"))
    _refused(route, "edit", "cannot include an image",
             cell="1,1", tab="Main", new_text=new)


def test_r3_07_an_encoded_break_beside_an_image_is_refused(route):
    _written(route, TABLE.format("old"))
    _refused(route, "edit", "cannot include an image",
             cell="1,1", tab="Main", new_text=IMAGE + "&#10;")


@pytest.mark.parametrize("extra", ["https://example.com/ref", "path:/tmp/notes"])
def test_r3_04_an_unreadable_pulled_header_is_refused(route, extra):
    _written(route, "Alpha.\nBeta.\n")
    text = (f"---\ngdoc: synthetic\ntitle: T\ngdoc-version: 1\n{extra}\n---\n"
            "Alpha edited.\nBeta.\n")
    _refused(route, "write", "can't be read", text=text)


def test_r3_12_level_change_names_nest_only_when_it_would_work(route):
    _written(route, "1. a\n2. b\n3. c\n")
    code, output, error = route.call("edit", old_text="b", new_text="  1. b")
    assert code != 0 and "gdoc nest" in output + error
    # A loose list is one native list per item, which nest refuses.
    _written(route, "- a\n\n- b\n")
    code, output, error = route.call("edit", old_text="b", new_text="  - b")
    assert code != 0 and "gdoc nest" not in output + error
    assert "write" in output + error


def test_r3_11_nest_after_cat_does_not_warn(route):
    _written(route, "1. a\n2. b\n3. c\n")
    if route.interface == "mcp":
        code, output, error = route.call("nest", text="b")
    else:
        import contextlib
        import io

        from gdoc import cli

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.run_argv(["nest", "synthetic", "b"], check_updates=False)
        output, error = "", err.getvalue()
    assert code == 0 and "doc changed since last read" not in output + error


# Codex review of the round-3 fixes (F6-01 to F6-04).

def test_f6_01_a_marker_fence_does_not_reach_past_its_item(route):
    markdown = "1. ```\n\nOutside.\n\n```\ncode\n```\n\n## Next\n"
    _written(route, markdown)
    got = _read(route)
    assert got.endswith("```\ncode\n```\n\n## Next\n")


def test_f6_02_quoted_provenance_keys_are_recognised(route):
    _written(route, "Alpha.\n")
    text = ('---\n"gdoc": synthetic\n"gdoc-version": 1\nhttps://example.com/ref\n'
            "---\nChanged.\n")
    _refused(route, "write", "can't be read", text=text)


def test_f6_03_a_child_deleted_before_its_parents_content_is_removed(route):
    markdown = "1. parent\n  - child\n\n   parent detail\n2. next\n"
    _written(route, markdown)
    route.ok("edit", old_text="child\n", new_text="")
    got = _read(route)
    assert "child" not in got and "parent detail" in got


def test_f6_04_an_empty_block_before_a_gdoc_line_is_body(route):
    _written(route, "Alpha.\n")
    route.ok("write", text="---\n---\ngdoc: example\n---\nBody.\n")
    assert "gdoc: example" in _read(route)


@pytest.mark.parametrize("indent", [" ", "  "])
def test_f6b_01_content_indented_less_than_the_marker_is_refused(route, indent):
    """Codex F6b-01: the parser accepts item content at any indent, so it is
    the deleted item's unless it provably belongs to an ancestor."""
    _written(route, f"1. alpha\n2. bravo\n\n{indent}detail\n3. charlie\n")
    _refused(route, "edit", "list's structure", old_text="bravo\n", new_text="")


def test_f6b_02_old_style_match_compares_the_target_tab(monkeypatch):
    from gdoc import cli
    from tests.native_model import NativeDoc

    def tab(tid, title, text):
        return {"tabProperties": {"tabId": tid, "title": title},
                "documentTab": NativeDoc(("p", text)).document_tab(tid)}

    monkeypatch.setattr("gdoc.api.docs.get_document_with_tabs", lambda *_: {
        "tabs": [tab("t.0", "Main", "Alpha."), tab("t.1", "Other", "Different.")]})
    monkeypatch.setattr("gdoc.api.drive.export_doc", lambda *a, **k: "x\n")
    assert cli._old_style_matches("doc", "Alpha.\n")
    assert not cli._old_style_matches("doc", "Alpha.\n", "t.1")
    assert not cli._old_style_matches("doc", "Alpha.\n", "Other")
    assert cli._old_style_matches("doc", "Different.\n", "Other")
