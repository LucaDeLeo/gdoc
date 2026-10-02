"""R6-9, R6-10, R6-11, R6-13 and R6-20a/c/d/e: parser and exporter repairs."""

import pytest

from gdoc import mdparse
from gdoc.annotate import annotate_markdown
from gdoc.api.docs import get_tab_text
from gdoc.frontmatter import parse_frontmatter
from gdoc.mdparse import parse_markdown
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _links(doc):
    return sorted({u.ts["link"]["url"] for u in doc.units if u.ts.get("link")})


CANONICAL_TABLE = "| Name | Qty |\n| --- | --- |\n| apple | 3 |\n| pear | 4 |\n\n"


@pytest.mark.parametrize("source", [
    "| Name | Qty | \n| --- | --- |\n| apple | 3 |\n| pear | 4 |\n",
    "| Name | Qty |\n| --- | --- |\n| apple | 3 |  \n| pear | 4 |\t\n",
    "  | Name | Qty |\n  | --- | --- |\n  | apple | 3 |\n  | pear | 4 |\n",
    "   | Name | Qty |\n | --- | --- |\n| apple | 3 |\n  | pear | 4 | \n",
])
def test_gfm_table_spacing_is_still_a_table(route, source):
    """R6-9: trailing whitespace and up to three spaces of indent."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=source)
    assert sum(u.kind == "tstart" for u in doc.units) == 1
    assert _read(route) == "\n" + CANONICAL_TABLE


@pytest.mark.parametrize("source,url", [
    ('See [docs](https://example.com/guide "User guide").\n',
     "https://example.com/guide"),
    ("See [docs](https://example.com/guide 'User guide').\n",
     "https://example.com/guide"),
    ("See [docs](https://example.com/a_(b) (User guide)).\n",
     "https://example.com/a_(b)"),
    ('See [docs](<https://example.com/a b> "T").\n', "https://example.com/a b"),
    ('See [docs][d].\n\n[d]: https://example.com/ref "Title"\n',
     "https://example.com/ref"),
])
def test_link_titles_never_enter_the_url(route, source, url):
    """R6-10 and R6-20c: a CommonMark title is not part of the destination."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=source)
    assert _links(doc) == [url]
    read = _read(route)
    route.ok("write", text=read.replace("See", "Read"))
    assert _links(doc) == [url]


@pytest.mark.parametrize("url", [
    'https://a/b "c"', "https://a/b 'q'", "https://a/ (t", "https://a/ t)",
    "https://a/<x> y", "https://a/`b` c", "https://e.com/a_(b)", "https://a/b\tc",
])
def test_exported_destinations_read_back_exactly(url):
    tab = {"body": {"content": [{"startIndex": 1, "endIndex": 3, "paragraph": {
        "elements": [
            {"startIndex": 1, "endIndex": 2, "textRun": {
                "content": "x", "textStyle": {"link": {"url": url}}}},
            {"startIndex": 2, "endIndex": 3, "textRun": {"content": "\n"}}]}}]}}
    parsed = parse_markdown(get_tab_text(tab, markdown=True))
    assert [s.style["link"]["url"] for s in parsed.styles
            if s.type == "text_style" and "link" in s.style] == [url]


def _para(*runs):
    return {"paragraph": {"elements": [{"textRun": {"content": text,
                                                    "textStyle": style}}
                                       for text, style in runs]}}


@pytest.mark.parametrize("item", [" --", " - -", "\t---", "  * * *"])
def test_rule_like_list_items_keep_their_text(item):
    """R6-11: the escape sits before the first rule character."""
    tab = {"body": {"content": [{"paragraph": {
        "elements": [{"textRun": {"content": item + "\n"}}],
        "bullet": {"listId": "l", "nestingLevel": 0}}}]},
        "lists": {"l": {"listProperties": {"nestingLevels": [{}]}}}}
    exported = get_tab_text(tab, markdown=True)
    parsed = parse_markdown(exported)
    assert parsed.plain_text == item.replace("\t", "\t") + "\n"
    assert get_tab_text(tab, markdown=True) == exported
    assert [s for s in parsed.styles if s.type == "bullets"]


def test_rule_like_list_item_survives_changed_rewrites(route):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="- &#32;--\n- b\n")
    first = _read(route)
    for old, new in [("- b", "- b0"), ("- b0", "- b01")]:
        changed = first.replace(old, new, 1)
        assert changed != first
        batches = len(route.service.batches)
        route.ok("write", text=changed)
        assert len(route.service.batches) > batches
        first = _read(route)
        assert first == changed
    assert "".join(u.ch for u in doc.units if u.kind == "text").startswith(" --\n")


@pytest.mark.parametrize("runs,plain,link", [
    ([("a", {}), ("  ", {"link": {"url": "https://e.org/"}, "bold": True}),
      (" ", {"link": {"url": "https://e.org/"}}), ("b\n", {})], "a   b\n", (1, 4)),
    ([("a", {}), (" ", {"link": {"url": "https://e.org/"}}), ("b\n", {})],
     "a b\n", (1, 2)),
])
def test_whitespace_only_links_are_written_once(runs, plain, link):
    """R6-13: a whitespace link label is kept, never duplicated."""
    parsed = parse_markdown(get_tab_text({"body": {"content": [_para(*runs)]}},
                                         markdown=True))
    assert parsed.plain_text == plain
    assert [(s.start, s.end) for s in parsed.styles
            if s.type == "text_style" and "link" in s.style] == [link]


@pytest.mark.parametrize("source,plain,styles", [
    ("***bold** then italic*", "bold then italic\n",
     [(0, 4, {"bold": True}), (0, 16, {"italic": True})]),
    ("***italic* then bold**", "italic then bold\n",
     [(0, 6, {"italic": True}), (0, 16, {"bold": True})]),
    ("***both***", "both\n", [(0, 4, {"bold": True}), (0, 4, {"italic": True})]),
])
def test_emphasis_opening_together(source, plain, styles):
    """R6-20a."""
    parsed = parse_markdown(source)
    assert parsed.plain_text == plain
    assert [(s.start, s.end, s.style) for s in parsed.styles
            if s.type == "text_style"] == styles


@pytest.mark.parametrize("anchor", ["really important to know", "the guide today"])
def test_comment_anchors_across_formatting_are_found(anchor):
    """R6-20d."""
    markdown = ("This is **really important** to know.\n"
                "See [the guide](https://e.com) today.\n")
    comment = {"id": "c1", "content": "note", "author": {"displayName": "A"},
               "quotedFileContent": {"value": anchor}, "resolved": False}
    annotated = annotate_markdown(markdown, [comment])
    assert "anchor deleted" not in annotated and "UNANCHORED" not in annotated


def test_long_paragraph_links_share_one_delimiter_scan(monkeypatch):
    """R6-20e: one delimiter scan per paragraph, not one per link."""
    calls = []
    original = mdparse._link_pairs
    monkeypatch.setattr(mdparse, "_link_pairs",
                        lambda masked: calls.append(1) or original(masked))
    parsed = parse_markdown("[a](http://e.com/x) " * 500 + "\n")
    assert len([s for s in parsed.styles if "link" in s.style]) == 500
    assert len(calls) == 1


@pytest.mark.parametrize("markdown", [
    "- x\n\n  > ```\n  > a\\*bc **d**\n  > ```\n",
    "> - x\n> \n>   > ```\n>   > a\\*bc **d**\n>   > ```\n",
    "- x\n\n  > - y\n  > \n  >   ```\n  >   a\\*bc **d**\n  >   ```\n",
])
def test_comment_fallback_never_decodes_nested_code(markdown):
    """Code in nested containers is literal, even for the display fallback."""
    for anchor in ("a*bc", "a*bc d"):
        comment = {"id": "c1", "content": "check",
                   "quotedFileContent": {"value": anchor}}
        assert "quoted text not found" in annotate_markdown(markdown, [comment])
    comment = {"id": "c2", "content": "check",
               "quotedFileContent": {"value": "a\\*bc **d**"}}
    assert "quoted text not found" not in annotate_markdown(markdown, [comment])
