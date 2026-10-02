"""R6-2..R6-5, R6-20b: container context and separator accounting.

Every block below sits in a list item or quote container that the parser
tracks as a path of quote markers and item indents. Each case is written
through CLI and MCP, must read back byte for byte, and must survive several
successive real wording changes with its native structure intact.
"""

import pytest

from gdoc.frontmatter import parse_frontmatter
from gdoc.mdparse import parse_markdown
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _shape(doc):
    """Named style, list shape (lists numbered by first appearance), container
    indent and gdoc range names: what a wording change must leave alone."""
    lists, paragraphs = {}, []
    for start, mark in doc.paragraphs():
        unit = doc.units[mark]
        bullet = unit.bullet and (lists.setdefault(unit.bullet["list"], len(lists)),
                                  unit.bullet["nest"])
        indent = unit.ps.get("indentStart", {}).get("magnitude", 0)
        paragraphs.append((unit.ps.get("namedStyleType"), bullet, indent))
    return paragraphs, sorted(name for name, *_ in doc.named if name)


def _check(route, markdown, edits, tables=None):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    assert _read(route) == markdown
    if tables is not None:
        assert len(parse_markdown(markdown).tables) == tables
    text = markdown
    for old, new in edits:
        changed = text.replace(old, new, 1)
        assert changed != text, (old, text)
        shape = _shape(doc)
        batches = len(route.service.batches)
        route.ok("write", text=changed)
        assert len(route.service.batches) > batches
        assert _read(route) == changed
        assert _shape(doc) == shape
        text = changed
    return doc


ADJACENT_IN_QUOTED_ITEM = (
    "- x\n\n  > - y\n  > \n  >   | a |\n  >   | --- |\n  >   | va |\n"
    "  > \n  > \n  >   | b |\n  >   | --- |\n  >   | vb |\n"
    "\n"  # a tab ending in a table keeps Docs' final paragraph
)


def test_adjacent_tables_in_a_quoted_list_inside_an_item(route):
    """R6-2: the paragraph Docs keeps between the tables stays in their quote."""
    _check(route, ADJACENT_IN_QUOTED_ITEM,
           [("x", "x2"), ("vb", "vb😀"), ("y", "y3")], tables=2)


@pytest.mark.parametrize("markdown", [
    "> - a\n> \n>   > q\n> \n>   | t |\n>   | --- |\n>   | vt |\n\n",
    "> - a\n> \n>   > q\n> \n>   ```\n>   code  x\n>   ```\n",
    "> - a\n> \n>   > q\n> - b\n",
    "- x\n\n  > - a\n  > \n  >   > deep\n  > - b\n- z\n",
])
def test_quote_inside_a_quoted_list_item(route, markdown):
    """R6-3: a quote in a quoted item keeps the item's later content inside."""
    doc = _check(route, markdown, [("a", "a1"), ("q" if "q" in markdown
                                                         else "deep", "Q2")])
    names = sorted(name for name, *_ in doc.named if name)
    assert any(name.startswith("gdoc:prefix:v3:") for name in names)


@pytest.mark.parametrize("markdown,tables", [
    ("- a\n\n  para\n\n  | t |\n  | --- |\n  | vt |\n- b\n", 1),
    ("1. a\n\n   para\n\n   > q\n2. b\n", 0),
    ("1. a\n\n   para\n\n   ```\n   code\n   ```\n2. b\n", 0),
    ("- a\n\n  first\n  second\n- b\n", 0),
    ("> - a\n> \n>   para\n> - b\n", 0),
    ("- a\n\n  ## Heading in item\n- b\n", 0),
])
def test_plain_paragraphs_inside_list_items(route, markdown, tables):
    """R6-4: item paragraphs keep the item open for later blocks."""
    _check(route, markdown, [("a", "a1"), ("b\n", "b2\n")], tables=tables)


@pytest.mark.parametrize("blanks", [0, 1, 2])
def test_blank_paragraphs_between_tables_in_an_item(route, blanks):
    """R6-5: user blank paragraphs between item tables survive every write."""
    markdown = ("- a\n\n  | t |\n  | --- |\n  | vt |\n\n\n" + "\n" * blanks
                + "  | u |\n  | --- |\n  | vu |\n- b\n")
    # Two blank lines are the paragraph Docs keeps plus the separator; each
    # further blank line is a user paragraph, and the exact readback keeps it.
    _check(route, markdown, [("vt", "vt2"), ("vu", "vu3"), ("a\n", "a4\n")],
           tables=2)


@pytest.mark.parametrize("markdown", [
    "1. a\n\n   ---\n\n2. b\n",
    "- a\n\n  ---\n- b\n",
    "> 1. a\n> \n>    ---\n> 2. b\n",
])
def test_rules_inside_list_items(route, markdown):
    """R6-20b: a rule written inside an item stays inside it."""
    _check(route, markdown, [("a", "a1"), ("b", "b2")])


@pytest.mark.parametrize("markdown", [
    "- a\n&#32; literal spaces\n",
    "1. a\n&#9;tabbed text\n2. b\n",
    "&#32; top level\n",
])
def test_literal_leading_whitespace_after_a_list_is_text(route, markdown):
    """Raw indentation means item content; literal whitespace is an entity."""
    _check(route, markdown, [("a\n", "a5\n")] if "a\n" in markdown
           else [("top", "top6")])


def test_original_r6_2_trigger_keeps_both_tables(route):
    """The exact R6-2 input: one quoted separator line and no final blank."""
    original = ("- x\n\n  > - y\n  >\n  >   | a |\n  >   | --- |\n  >   | va |\n"
                "  >\n  >   | b |\n  >   | --- |\n  >   | vb |\n")
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=original)
    first = _read(route)
    # Docs keeps a paragraph between the tables (now read inside their quote,
    # beside the separator) and a final paragraph after the last table.
    assert first == ADJACENT_IN_QUOTED_ITEM
    assert len(parse_markdown(first).tables) == 2
    for old, new in [("x", "x2"), ("va", "va3")]:
        changed = first.replace(old, new, 1)
        batches = len(route.service.batches)
        route.ok("write", text=changed)
        assert len(route.service.batches) > batches
        first = _read(route)
        assert first == changed
        assert len(parse_markdown(first).tables) == 2
    assert sum(u.kind == "tstart" for u in doc.units) == 2


@pytest.mark.parametrize("blank_lines,paragraphs", [(1, 1), (2, 1), (3, 2), (4, 3)])
def test_original_r6_5_blank_counts_between_item_tables(
    route, blank_lines, paragraphs,
):
    """User blank lines between item tables: the last is the separator, and
    Docs always keeps at least one paragraph between two tables."""
    original = ("- a\n\n  | t |\n  | --- |\n  | vt |\n" + "\n" * blank_lines
                + "  | u |\n  | --- |\n  | vu |\n")
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=original)

    def between():
        units = doc.units
        end = next(i for i, u in enumerate(units) if u.kind == "tend")
        start = next(i for i, u in enumerate(units) if u.kind == "tstart" and i > end)
        return sum(u.ch == "\n" for u in units[end + 1:start])

    assert between() == paragraphs
    first = _read(route)
    assert first == ("- a\n\n  | t |\n  | --- |\n  | vt |\n" + "\n" * (paragraphs + 1)
                     + "  | u |\n  | --- |\n  | vu |\n\n")
    for old, new in [("vt", "vt2"), ("a\n", "a3\n")]:
        changed = first.replace(old, new, 1)
        route.ok("write", text=changed)
        first = _read(route)
        assert first == changed and between() == paragraphs


@pytest.mark.parametrize("markdown,path", [
    # quote / list item / quote
    ("> - a\n> \n>   > inner\n> - b\n", ("q", 2, "q")),
    # list item / quote / list item content
    ("- a\n\n  > - b\n  > \n  >   inner\n- c\n", (2, "q", 2)),
    # list item / quote / list item / quote
    ("- a\n\n  > - b\n  > \n  >   > inner\n- c\n", (2, "q", 2, "q")),
])
def test_container_order_is_preserved(route, markdown, path):
    doc = _check(route, markdown, [("inner", "inner2"), ("a\n", "a4\n")])
    [styled] = [s for s in parse_markdown(markdown).styles
                if s.type == "markdown_prefix" and s.path == path]
    from gdoc.api.docs import _prefix_range_name
    assert _prefix_range_name(path) in {name for name, *_ in doc.named if name}
    assert styled.path == path


@pytest.mark.parametrize("markdown,edits", [
    ("- a\n\n  ```\n  \tcode\ttab\n  ```\n  | t |\n  | --- |\n  | v |\n- b\n",
     [("code", "code7"), ("- b", "- b8")]),
    ("1. a\n\n   | t |\n   | --- |\n   | v |\n\n   after table\n2. b\n",
     [("after", "after9"), ("2. b", "2. b10")]),
    ("- a\n\n  text\n  ```\n  x\n  ```\n  more\n- b\n",
     [("more", "more11"), ("text", "text12")]),
    ("&#32; literal\n- a\n&#9;tab literal\n",
     [("tab literal", "tab literal13"), ("- a", "- a14")]),
])
def test_neighbouring_contained_blocks_and_literal_whitespace(
    route, markdown, edits,
):
    _check(route, markdown, edits)


def test_legacy_v2_range_reads_as_a_quote_in_an_item():
    from gdoc.api.docs import _parse_prefix_range_name, _prefix_range_name

    assert _parse_prefix_range_name("gdoc:prefix:v2:2:1:0") == (2, "q")
    assert _parse_prefix_range_name("gdoc:prefix:v2:3:2:4") == (3, "q", "q", 4)
    assert _parse_prefix_range_name("gdoc:prefix:v1:1:2") == ("q", 2)
    assert _parse_prefix_range_name("gdoc:prefix:v1:0:3") == (3,)
    assert _parse_prefix_range_name("gdoc:prefix:v3:q.2.q") == ("q", 2, "q")
    for path in [("q",), (3,), ("q", 2), (2, "q"), (2, "q", 2), ("q", 2, "q"),
                 (2, "q", 2, "q"), (2, 4)]:
        assert _parse_prefix_range_name(_prefix_range_name(path)) == path


def test_owned_ranges_and_loss_guard_cover_v3_names():
    import re

    from gdoc.api.docs import _OWNED_RANGE_NAME_RE
    from gdoc.lossy import _PREFIX_NAME

    for name in ["gdoc:prefix:v3:q.2.q", "gdoc:prefix:v3:2.q.2.q"]:
        assert _OWNED_RANGE_NAME_RE.fullmatch(name)
        assert re.fullmatch(_PREFIX_NAME, name)
    assert not _OWNED_RANGE_NAME_RE.fullmatch("gdoc:prefix:v3:")
    assert not _OWNED_RANGE_NAME_RE.fullmatch("gdoc:prefix:v3:x")


def test_removed_container_indent_reads_without_the_container(route):
    """A v3 container whose indent someone removed in Docs no longer applies."""
    markdown = "> - a\n> \n>   > inner\n> - b\n"
    doc = _check(route, markdown, [])
    start = "".join(u.ch for u in doc.units).index("inner") + 1  # section break
    route.service.doc.apply({"updateParagraphStyle": {
        "range": {"startIndex": start, "endIndex": start + 1},
        "paragraphStyle": {"indentStart": {"magnitude": 0, "unit": "PT"},
                           "indentFirstLine": {"magnitude": 0, "unit": "PT"}},
        "fields": "indentStart,indentFirstLine"}})
    route.service.revision += 1
    # Without its indent the paragraph is no longer in the quoted item's quote.
    assert _read(route) == "> - a\n> \ninner\n> - b\n"


def _list_of(doc, text):
    """The native list (numbered by first appearance) holding paragraph *text*."""
    lists, found = {}, None
    for start, mark in doc.paragraphs():
        unit = doc.units[mark]
        if unit.bullet:
            number = lists.setdefault(unit.bullet["list"], len(lists))
            if "".join(u.ch for u in doc.units[start:mark]) == text:
                found = number
    return found


@pytest.mark.parametrize("markdown,outer,inner", [
    # quote / item / quote: the quoted list is its own list
    ("> - a\n> \n>   > - x\n>   > - y\n> - b\n", ("a", "b"), ("x", "y")),
    # item / quote: the item's list resumes after the quoted list
    ("- a\n\n  > - x\n  > - y\n\n- b\n", ("a", "b"), ("x", "y")),
    ("1. a\n\n   > 1. x\n   > 2. y\n\n2. b\n", ("a", "b"), ("x", "y")),
    # item / quote / item / quote
    ("- a\n\n  > - m\n  > \n  >   > - x\n  >   > - y\n  > - n\n- b\n",
     ("m", "n"), ("x", "y")),
])
def test_alternating_containers_keep_list_identity(route, markdown,
                                                   outer, inner):
    """CodeRabbit on 262a937: a list quoted in an item never joins the list
    around it, and the enclosing list stays one list across it."""
    doc = _check(route, markdown, [(inner[0] + "\n", inner[0] + "2\n"),
                                          (outer[1] + "\n", outer[1] + "3\n")])
    a, b = (_list_of(doc, outer[0]), _list_of(doc, outer[1] + "3"))
    x, y = (_list_of(doc, inner[0] + "2"), _list_of(doc, inner[1]))
    assert a == b and x == y and a != x


@pytest.mark.parametrize("source,expected,before", [
    # The paragraph Docs keeps before a first table reads as a blank line.
    ("| h |\n| --- |\n| v |\nplain\n", "\n| h |\n| --- |\n| v |\nplain\n", 1),
    # A user blank line beyond it is a real paragraph and is kept.
    ("\n\n| h |\n| --- |\n| v |\nplain\n", "\n\n| h |\n| --- |\n| v |\nplain\n", 2),
    # A table ending the tab keeps Docs' final paragraph after it.
    ("plain\n| h |\n| --- |\n| v |\n", "plain\n| h |\n| --- |\n| v |\n\n", 1),
])
def test_table_boundary_paragraphs_are_stable(route, source, expected, before):
    """R6-12: mandatory native paragraphs around tables are explicit and stable;
    user-authored blank paragraphs are never erased."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=source)
    assert _read(route) == expected
    start = next(i for i, u in enumerate(doc.units) if u.kind == "tstart")
    assert sum(u.ch == "\n" and u.kind == "text"
               for u in doc.units[:start]) == before
    text = _read(route)
    for old, new in [("v |", "v0 |"), ("h |", "h1 |")]:
        changed = text.replace(old, new, 1)
        assert changed != text
        batches = len(route.service.batches)
        route.ok("write", text=changed)
        assert len(route.service.batches) > batches
        text = _read(route)
        assert text == changed


def _indent_of(doc, text):
    for start, mark in doc.paragraphs():
        if "".join(u.ch for u in doc.units[start:mark]) == text:
            return doc.units[mark].ps.get("indentStart", {}).get("magnitude", 0)
    raise AssertionError(text)


@pytest.mark.parametrize("markdown,text,indent", [
    ("- parent\n  - child\n\n    continuation\n- next\n", "continuation", 72),
    ("1. a\n  1. b\n\n     para\n2. c\n", "para", 72),
    ("- p\n  - c\n    - g\n\n      deep\n- n\n", "deep", 108),
    ("- p\n  - c\n\n    > quoted\n- n\n", "quoted", 108),
    ("- p\n  - c\n\n    ```\n    code\n    ```\n- n\n", "code", 72),
    ("- p\n  - c\n\n    | t |\n    | --- |\n    | v |\n- n\n", None, None),
])
def test_content_under_nested_items_is_indented_at_its_item(
    route, markdown, text, indent,
):
    """Internal review P1: one container level per enclosing item."""
    last = markdown.rstrip("\n").rsplit("\n", 1)[1]
    edits = [(last, last + "2")]
    if text:
        edits.append((text, text + "3"))
    doc = _check(route, markdown, edits,
                 tables=1 if "| t |" in markdown else None)
    from gdoc.api.docs import _parse_prefix_range_name
    if text:
        assert _indent_of(doc, text + "3") == indent
        # The recorded container path accounts for exactly that indent.
        at = "".join(u.ch for u in doc.units).index(text + "3") + 1
        paths = [_parse_prefix_range_name(name) for name, low, high in doc.named
                 if name and low <= at < high and _parse_prefix_range_name(name)]
        assert [36 * len(path) for path in paths] == [indent]
    else:
        # gdoc gives tables no native indent; a contained table's container
        # is the named range on its first cell, which must record both items.
        start = next(i for i, u in enumerate(doc.units) if u.kind == "tstart")
        paths = [_parse_prefix_range_name(name) for name, low, _ in doc.named
                 if name and low > start and _parse_prefix_range_name(name)]
        assert paths[:1] == [(2, 2)]
