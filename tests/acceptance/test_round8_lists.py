"""R7-1: a list quoted inside a nested list item is its own native list.

Each case is written through CLI and MCP, then changed
several times. Native list identity, bullet type, nesting and displayed
numbering must match the Markdown, not only the Markdown readback.
"""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _lists(doc):
    """(text, list number by first appearance, kind, nest, displayed label)."""
    ids, counters, out = {}, {}, []
    for start, mark in doc.paragraphs():
        unit = doc.units[mark]
        text = "".join(u.ch for u in doc.units[start:mark] if not u.cont)
        if not unit.bullet:
            continue
        list_id, nest = unit.bullet["list"], unit.bullet["nest"]
        numbered = unit.bullet["preset"].startswith("NUMBERED")
        for key in [k for k in counters if k[0] == list_id and k[1] > nest]:
            del counters[key]
        counters[list_id, nest] = counters.get((list_id, nest), 0) + 1
        label = counters[list_id, nest] if numbered else "-"
        out.append((text, ids.setdefault(list_id, len(ids)),
                    "num" if numbered else "bullet", nest, label))
    return out


# (markdown, expected native lists). List numbers are by first appearance;
# each nesting level is its own native list, as elsewhere in the writer.
CASES = [
    # The exact R7-1 trigger: a quoted bullet list between numbered subitems.
    ("- x\n  1. d\n\n     > - q\n  2. e\n",
     [("x", 0, "bullet", 0, "-"), ("d", 1, "num", 1, 1), ("q", 2, "bullet", 0, "-"),
      ("e", 1, "num", 1, 2)]),
    # The reverse direction: a quoted numbered list between bulleted subitems.
    ("- x\n  - d\n\n    > 1. q\n  - e\n",
     [("x", 0, "bullet", 0, "-"), ("d", 1, "bullet", 1, "-"), ("q", 2, "num", 0, 1),
      ("e", 1, "bullet", 1, "-")]),
    # Same preset inside and outside: the quoted list keeps its own numbering.
    ("- x\n  1. d\n\n     > 1. q\n     > 2. r\n  2. e\n",
     [("x", 0, "bullet", 0, "-"), ("d", 1, "num", 1, 1), ("q", 2, "num", 0, 1),
      ("r", 2, "num", 0, 2), ("e", 1, "num", 1, 2)]),
    # Numbered outer list, nested quoted bullets two levels down.
    ("1. x\n  1. d\n    - f\n\n      > - q\n      >   1. s\n    - g\n  2. e\n",
     [("x", 0, "num", 0, 1), ("d", 1, "num", 1, 1), ("f", 2, "bullet", 2, "-"),
      ("q", 3, "bullet", 0, "-"), ("s", 4, "num", 1, 1), ("g", 2, "bullet", 2, "-"),
      ("e", 1, "num", 1, 2)]),
]


@pytest.mark.parametrize("markdown,expected", CASES)
def test_quoted_list_inside_nested_item_keeps_its_own_identity(
    route, markdown, expected,
):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    assert _read(route) == markdown
    assert _lists(doc) == expected
    text = markdown
    for old, new in [("x", "x2"), ("q", "q😀"), ("e", "e3")]:
        changed = text.replace(old, new, 1)
        assert changed != text
        batches = len(route.service.batches)
        route.ok("write", text=changed)
        assert len(route.service.batches) > batches
        assert _read(route) == changed
        renamed = {"x": "x2", "q": "q😀", "e": "e3"}
        text = changed
        expected = [((renamed[t] if t == old else t), *rest) for t, *rest in expected]
        assert _lists(doc) == expected


# A quoted list that an outer list's range spans, outside any list item.
SPANNED = [
    ("- a\n\n> 1. g\n> 2. h\n\n- b\n",
     [("a", 0, "bullet", 0, "-"), ("g", 1, "num", 0, 1), ("h", 1, "num", 0, 2),
      ("b", 0, "bullet", 0, "-")]),
    ("- a\n\n> 1. g\n\nsep\n\n- b\n",
     [("a", 0, "bullet", 0, "-"), ("g", 1, "num", 0, 1), ("b", 0, "bullet", 0, "-")]),
    ("> - a\n> \n> > 1. g\n> \n> - b\n",
     [("a", 0, "bullet", 0, "-"), ("g", 1, "num", 0, 1), ("b", 0, "bullet", 0, "-")]),
    ("1. a\n\n> 1. g\n\n2. b\n",
     [("a", 0, "num", 0, 1), ("g", 1, "num", 0, 1), ("b", 0, "num", 0, 2)]),
]


@pytest.mark.parametrize("markdown,expected", SPANNED)
def test_quoted_list_spanned_by_an_outer_list_keeps_its_own_identity(
    route, markdown, expected,
):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    assert _read(route) == markdown
    assert _lists(doc) == expected
    changed = markdown.replace("g", "g2", 1)
    batches = len(route.service.batches)
    route.ok("write", text=changed)
    assert len(route.service.batches) > batches
    assert _read(route) == changed
    assert _lists(doc) == [(("g2" if t == "g" else t), *rest) for t, *rest in expected]
