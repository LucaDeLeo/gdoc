"""#80's list fixes (#67), checked on the native document the writer makes.

#80 pinned request shapes of the list compiler #72 replaced, so each of its
cases is written here through CLI and MCP and the native lists are read
back: nesting level, list identity, bullet kind and displayed number.

Nesting is relative, as in CommonMark: an item indented at least two
columns past the item above it is its child, so four-space indents and
sub-items under ``10.`` nest one level. ``cat`` indents a child to its
parent's content column, so every case reads back as it was written.

- AGREE: #80's behavior.
- CONTINUES: #80 kept the two lists separate (so "2. b" displayed 1);
  #72 continues the numbering, so the displayed number matches the
  Markdown.
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
     [("a", 0, "num", 0, 1), ("b", 0, "num", 1, 1)], None),
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
     None),
    # A top-level change of kind starts a new list.
    ("- a\n1. b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 1, "num", 0, 1)], None),
    # A numbered list resuming after a heading continues (4b6e1cb).
    ("1. a\n   - b\n## H\n2. c\n",
     [("a", 0, "num", 0, 1), ("b", 1, "bullet", 1, "-"), ("c", 0, "num", 0, 2)],
     None),
    # A list starting again at 1 stays separate.
    ("1. a\n\np\n\n1. b\n",
     [("a", 0, "num", 0, 1), ("b", 1, "num", 0, 1)], None),
    # Loose nested lists stay nested, and keep the numbering (fee5e1d).
    ("- a\n\n  - b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-")], None),
    ("1. a\n\n   - b\n\n2. c\n",
     [("a", 0, "num", 0, 1), ("b", 1, "bullet", 1, "-"), ("c", 0, "num", 0, 2)],
     None),
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


# Relative nesting (#67): each case reads back as written.
RELATIVE = [
    # Four spaces per level, as Prettier and mdformat emit.
    ("- a\n    - b\n        - c\n",
     [("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-"),
      ("c", 0, "bullet", 2, "-")], "- a\n  - b\n    - c\n"),
    # Sub-items under one- and two-digit numbers.
    ("9. a\n   - b\n10. c\n    - d\n",
     [("a", 0, "num", 0, 1), ("b", 1, "bullet", 1, "-"), ("c", 0, "num", 0, 2),
      ("d", 2, "bullet", 1, "-")], "1. a\n   - b\n2. c\n   - d\n"),
    ("1. a\n2. b\n3. c\n4. d\n5. e\n6. f\n7. g\n8. h\n9. i\n10. j\n"
     "    - k\n",
     [(t, 0, "num", 0, n) for n, t in enumerate("abcdefghij", 1)]
     + [("k", 1, "bullet", 1, "-")], None),
    # #67's report: a four-space child of a numbered item.
    ("1. parent num\n    - child num\n",
     [("parent num", 0, "num", 0, 1), ("child num", 1, "bullet", 1, "-")],
     "1. parent num\n   - child num\n"),
    # A paragraph ends the list, so the indented item starts a new list.
    ("- a\n\np\n\n  - b\n",
     [("a", 0, "bullet", 0, "-"), ("b", 1, "bullet", 0, "-")],
     "- a\n\np\n\n- b\n"),
    # Numbered under numbered: one native list, nested numbering.
    ("1. a\n   1. x\n   2. y\n2. b\n   1. z\n",
     [("a", 0, "num", 0, 1), ("x", 0, "num", 1, 1), ("y", 0, "num", 1, 2),
      ("b", 0, "num", 0, 2), ("z", 0, "num", 1, 1)], None),
]


@pytest.mark.parametrize("markdown,expected,readback", RELATIVE)
def test_relative_nesting(route, markdown, expected, readback):
    doc, back = _write(route, markdown)
    assert _lists(doc) == expected
    assert back == (readback or markdown)
    # The read is canonical: writing it back changes nothing.
    route.ok("write", text=back)
    assert _lists(doc) == expected
    assert parse_frontmatter(route.ok("cat"))[1] == back


# A bullet list continues across blank lines and its items' content, and
# ends at a block of its own container, as in CommonMark.
BOUNDARY = [
    ("- a\n\n- b\n", True),
    ("- a\n\n  > q\n\n- b\n", True),
    ("- a\n\n  ```\n  x\n  ```\n\n- b\n", True),
    ("- a\n\np\n\n- b\n", False),
    ("- a\n\n## H\n\n- b\n", False),
    ("- a\n\n---\n\n- b\n", False),
    ("- a\n\n> q\n\n- b\n", False),
    ("- a\n\n```\nx\n```\n\n- b\n", False),
    ("- a\n\n| H |\n| --- |\n| x |\n\n- b\n", False),
]


@pytest.mark.parametrize("markdown,same", BOUNDARY)
def test_bullet_list_boundary(route, markdown, same):
    doc, back = _write(route, markdown)
    rows = _lists(doc)
    assert [row[0] for row in rows] == ["a", "b"]
    assert (rows[0][1] == rows[1][1]) == same
    assert back == markdown


# The live smoke file: same-type nesting shares one native list in every
# block, even when another block mixes numbered and bullet items.
SMOKE = ("Four-space bullets:\n\n- a\n    - b\n        - c\n\n"
         "Numbered with four-space child:\n\n1. x\n    - y\n2. z\n\n"
         "Two-space bullets:\n\n- p\n  - q\n    - r\n")


def test_smoke_file_levels_and_identity(route):
    doc, back = _write(route, SMOKE)
    assert _lists(doc) == [
        ("a", 0, "bullet", 0, "-"), ("b", 0, "bullet", 1, "-"),
        ("c", 0, "bullet", 2, "-"),
        ("x", 1, "num", 0, 1), ("y", 2, "bullet", 1, "-"), ("z", 1, "num", 0, 2),
        ("p", 3, "bullet", 0, "-"), ("q", 3, "bullet", 1, "-"),
        ("r", 3, "bullet", 2, "-"),
    ]
    assert back == SMOKE.replace("    - b\n        - c", "  - b\n    - c").replace(
        "    - y", "   - y")


BULLET = {"preset": "BULLET_DISC_CIRCLE_SQUARE", "list": 1}


def test_skipped_level_needs_loss_consent(route):
    """A level-3 item directly under a level-1 item (possible in Docs) has
    no Markdown spelling: the read puts it one level below its parent and
    names it, and a rewrite needs --allow-lossy rather than re-leveling it."""
    doc = route.load(NativeDoc(
        ("p", "a", "NORMAL_TEXT", {**BULLET, "nest": 0}),
        ("p", "c", "NORMAL_TEXT", {**BULLET, "nest": 2}),
    ))
    code, out, err = route.call("cat")
    assert code == 0
    assert parse_frontmatter(out)[1] == "- a\n  - c\n"
    assert "nesting level 3" in out + err
    code, out, err = route.call("write", text="- a\n  - c\n- d\n")
    assert code != 0 and "nesting level 3" in out + err
    assert [row[3] for row in _lists(doc)] == [0, 2]
    route.ok("write", text="- a\n  - c\n- d\n", allow_lossy=True)
    assert [row[3] for row in _lists(doc)] == [0, 1, 0]
