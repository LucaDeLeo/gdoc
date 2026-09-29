"""Targeted edits touch list items only in place (CLI and MCP).

`edit` and `insert` may reword list items, each keeping its list, kind and
level, or delete whole items. Any other list change is refused with nothing
sent, and `write` of the intended Markdown remains the route for it.
"""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles

BASE = "1. a\n2. b\n3. c\n"


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _written(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    return doc


@pytest.mark.parametrize("base,command,arguments,intended", [
    # another kind
    (BASE, "edit", {"old_text": "b", "new_text": "- b"}, "1. a\n- b\n3. c\n"),
    # another level
    (BASE, "edit", {"old_text": "b", "new_text": "  1. b"}, "1. a\n  1. b\n3. c\n"),
    # a new item
    (BASE, "edit", {"old_text": "b", "new_text": "2. b\n3. x"},
     "1. a\n2. b\n3. x\n4. c\n"),
    # an item leaves the list
    (BASE, "edit", {"old_text": "b\nc", "new_text": "2. b\nprose"},
     "1. a\n2. b\n\nprose\n"),
    (BASE, "edit", {"old_text": "b", "new_text": "## b"}, "1. a\n\n## b\n\n1. c\n"),
    # a paragraph joins a list
    ("1. a\n2. b\n\nTail\n", "edit", {"old_text": "Tail", "new_text": "3. Tail"},
     "1. a\n2. b\n3. Tail\n"),
    # a number the item does not show: a restart
    (BASE, "edit", {"old_text": "b", "new_text": "1. b"}, "1. a\n1. b\n3. c\n"),
    (BASE, "edit", {"old_text": "a", "new_text": "7. a"}, "7. a\n2. b\n3. c\n"),
    # an item moves into a quote
    (BASE, "edit", {"old_text": "b", "new_text": "> 2. b"},
     "1. a\n\n> 2. b\n\n1. c\n"),
    # inserted items joining the list beside them
    (BASE, "insert", {"text": "4. d\n", "tab": "t.0", "position": "end"},
     "1. a\n2. b\n3. c\n4. d\n"),
    (BASE, "insert", {"text": "  - d\n", "tab": "t.0", "position": "end"},
     "1. a\n2. b\n3. c\n  - d\n"),
    (BASE, "insert", {"text": "1. z\n", "tab": "t.0", "position": "start"},
     "1. z\n2. a\n3. b\n4. c\n"),
])
def test_list_restructures_are_refused_and_write_works(
        route, base, command, arguments, intended):
    _written(route, base)
    before, batches = _read(route), len(route.service.batches)
    code, output, error = route.call(command, **arguments)
    assert code != 0 and "list's structure" in output + error
    assert "write" in output + error
    assert len(route.service.batches) == batches and _read(route) == before
    # write of the intended Markdown is the working route.
    route.ok("write", text=intended)
    assert _read(route) != before


@pytest.mark.parametrize("old,new,expected", [
    ("b", "2. B", "1. a\n2. B\n3. c\n"),
    ("b", "B", "1. a\n2. B\n3. c\n"),
    ("b\nc", "2. B\n3. C", "1. a\n2. B\n3. C\n"),
    ("b\nc", "B\nC", "1. a\n2. B\n3. C\n"),
])
def test_items_reworded_in_place_keep_their_list(route, old, new, expected):
    doc = _written(route, BASE)
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route) == expected
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 1


def test_a_whole_item_is_deleted(route):
    _written(route, BASE)
    route.ok("edit", old_text="b\n", new_text="")
    assert _read(route) == "1. a\n2. c\n"


@pytest.mark.parametrize("base,text,position,expected", [
    # another kind, or another container, stays its own list, as in a write
    (BASE, "- d\n", "end", "1. a\n2. b\n3. c\n- d\n"),
    ("> 1. q\n", "1. d\n", "end", "> 1. q\n1. d\n"),
    (BASE, "Prose\n\n", "start", "Prose\n\n1. a\n2. b\n3. c\n"),
])
def test_inserts_beside_a_list_that_do_not_join_it(route, base, text, position,
                                                   expected):
    _written(route, base)
    route.ok("insert", text=text, tab="t.0", position=position)
    assert _read(route) == expected
