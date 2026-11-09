"""#80's list fixes (#67), checked on the native document #72's writer makes.

#80 pinned request shapes of the list compiler #72 replaced, so each of its
cases is written here through CLI and MCP and the native lists are read
back: nesting level, list identity, bullet kind and displayed number.

- AGREE: #72 already does what #80 asked.
- CONTINUES: #80 kept the two lists separate (so "2. b" displayed 1);
  #72 continues the numbering, so the displayed number matches the
  Markdown. Pinned as #72's behavior.
- DIALECT: #80 parses nesting relative to the parent item (CommonMark);
  #72 maps two leading spaces to one level so depth round-trips exactly.
  Strict xfail until that product decision is made.
"""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.acceptance.test_round8_lists import _lists
from tests.native_model import NativeDoc


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _write(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    return doc, parse_frontmatter(route.ok("cat"))[1]


def _paragraph(doc, text):
    for start, mark in doc.paragraphs():
        if "".join(u.ch for u in doc.units[start:mark] if not u.cont) == text:
            return doc.units[mark]
    raise AssertionError(f"no paragraph {text!r}")


# (markdown, expected native lists, expected readback or None for the same).
AGREE = [
    # A nested list is one native list with levels (#67).
    ("- a\n  - b\n- c\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-"),
      ("c", 0, "bullet", 0, "-")], None),
    # A list after a plain paragraph lands on its own text.
    ("- a\n  - b\n\np\n\n- c\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-"),
      ("c", 1, "bullet", 0, "-")], None),
    # Tab and three-space indents under a numbered item are one level.
    ("- a\n\t- b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-")], "- a\n  - b\n"),
    ("1. a\n   1. b\n",
     [("a", 0, "num", 0, 1), ("b", 0, "num", 1, 1)], "1. a\n  1. b\n"),
    # Dedent returns to the matching level; a one-space indent is a sibling.
    ("- a\n  - b\n    - c\n  - d\n- e\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-"),
      ("c", 0, "bullet", 2, "-"), ("d", 0, "bullet", 1, "-"),
      ("e", 0, "bullet", 0, "-")], None),
    ("- a\n - b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 0, "-")], "- a\n- b\n"),
    # Bullets nested under numbered items keep the numbering going.
    ("1. a\n   - b\n2. c\n",
     [("a", 0, "num", 0, 1), ("b", 1, "bullet", 1, "-"), ("c", 0, "num", 0, 2)],
     "1. a\n  - b\n2. c\n"),
    # A top-level change of kind starts a new list.
    ("- a\n1. b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 1, "num", 0, 1)], None),
    # A numbered list resuming after a heading continues (4b6e1cb).
    ("1. a\n   - b\n## H\n2. c\n",
     [("a", 0, "num", 0, 1), ("b", 1, "bullet", 1, "-"), ("c", 0, "num", 0, 2)],
     "1. a\n  - b\n## H\n2. c\n"),
    # A list starting again at 1 stays separate.
    ("1. a\n\np\n\n1. b\n",
     [("a", 0, "num", 0, 1), ("b", 1, "num", 0, 1)], None),
    # Loose nested lists stay nested, and keep the numbering (fee5e1d).
    ("- a\n\n  - b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-")], None),
    ("1. a\n\n   - b\n\n2. c\n",
     [("a", 0, "num", 0, 1), ("b", 1, "bullet", 1, "-"), ("c", 0, "num", 0, 2)],
     "1. a\n\n  - b\n\n2. c\n"),
]


@pytest.mark.parametrize("markdown,expected,readback", AGREE)
def test_agrees_with_80(route, markdown, expected, readback):
    doc, back = _write(route, markdown)
    assert _lists(doc) == expected
    assert back == (readback or markdown)


def test_gap_paragraph_keeps_blockquote_indent(route):
    doc, back = _write(route, "1. a\n> q\n2. b\n")
    assert _lists(doc) == [("a", 0, "num", 0, 1), ("b", 0, "num", 0, 2)]
    quote = _paragraph(doc, "q")
    assert not quote.bullet
    assert quote.ps.get("indentStart", {}).get("magnitude", 0) > 0
    assert back == "1. a\n> q\n2. b\n"


# #80 kept these lists separate, so "2. b" displayed 1 in Docs (Docs cannot
# set a list's start). #72 continues the list across the gap, so the
# displayed number is the Markdown's; the code block keeps its tab.
CONTINUES = [
    "1. a\n\n- x\n\n2. b\n",
    "1. a\n\n| H |\n| --- |\n| x |\n\n2. b\n",
    "1. a\n\n```\n\tcode\n```\n\n2. b\n",
]


@pytest.mark.parametrize("markdown", CONTINUES)
def test_numbering_continues_across_gap(route, markdown):
    doc, back = _write(route, markdown)
    numbered = [row for row in _lists(doc) if row[2] == "num"]
    assert numbered == [("a", 0, "num", 0, 1), ("b", 0, "num", 0, 2)]
    assert back == markdown


DIALECT = [
    # Four spaces per level, as Prettier and mdformat emit.
    ("- a\n    - b\n        - c\n", [0, 1, 2]),
    # Sub-items under two-digit numbers.
    ("9. a\n   - b\n10. c\n    - d\n", [0, 1, 0, 1]),
    # #67's report: a four-space child of a numbered item.
    ("1. parent num\n    - child num\n", [0, 1]),
    # A paragraph ends the list, so the indented item starts a new top level.
    ("- a\n\np\n\n  - b\n", [0, 0]),
]


@pytest.mark.xfail(strict=True, reason=(
    "#80 CommonMark-relative nesting vs #72 absolute two-space levels: "
    "pending product decision"
))
@pytest.mark.parametrize("markdown,levels", DIALECT)
def test_relative_nesting_from_80(route, markdown, levels):
    doc, _ = _write(route, markdown)
    assert [row[3] for row in _lists(doc)] == levels
