"""Final-review regressions: an edit that turns several whole paragraphs
into list items makes one native list, and a numbered start the API cannot
set warns, through CLI and MCP on the native model."""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


@pytest.mark.parametrize("new,expected", [
    ("1. Alpha\n2. Beta", "1. Alpha\n2. Beta\nTail\n"),
    ("- Alpha\n- Beta", "- Alpha\n- Beta\nTail\n"),
    ("1. Alpha\n  1. Beta", "1. Alpha\n  1. Beta\nTail\n"),
])
def test_paragraphs_become_one_list(route, new, expected):
    doc = route.load(NativeDoc(("p", "A"), ("p", "B"), ("p", "Tail")))
    route.ok("cat")
    route.ok("edit", old_text="A\nB", new_text=new)
    assert _read(route) == expected
    lists = [bullet for _, _, bullet in styles(doc) if bullet]
    assert len({list_id for list_id, _ in lists}) == 1
    assert [level for _, level in lists] == [0, int("  1." in new)]


def test_intentional_restart_stays_two_lists(route):
    doc = route.load(NativeDoc(("p", "A"), ("p", "B"), ("p", "Tail")))
    route.ok("cat")
    route.ok("edit", old_text="A\nB", new_text="1. Alpha\n\n1. Beta")
    lists = [bullet for _, _, bullet in styles(doc) if bullet]
    assert len({list_id for list_id, _ in lists}) == 2


def test_edit_warns_when_a_list_start_resets(route):
    route.load(NativeDoc(("p", "Original"), ("p", "Tail")))
    route.ok("cat")
    code, output, error = route.call("edit", old_text="Original", new_text="7. New")
    assert code == 0
    assert "start at 1" in output + error
    assert _read(route) == "1. New\nTail\n"


def test_wording_like_a_list_start_does_not_warn(route):
    route.load(NativeDoc(("p", "Step Original here"), ("p", "Tail")))
    route.ok("cat")
    code, output, error = route.call("edit", old_text="Original", new_text="7. New")
    assert code == 0 and "start at 1" not in output + error


NUMBERED = {"preset": "NUMBERED_DECIMAL_ALPHA_ROMAN", "list": 1, "nest": 0}


@pytest.mark.parametrize("target,new,expected", [
    ("b", "2. b2", "1. a\n2. b2\n3. c\n"),
    ("b", "1. b2", "1. a\n2. b2\n3. c\n"),
    ("b", "**b2**", "1. a\n2. **b2**\n3. c\n"),
    ("a", "1. a2", "1. a2\n2. b\n3. c\n"),
    ("a", "- a2", None),
])
def test_rewording_an_item_keeps_its_list(route, target, new, expected):
    """Final review F1: an item reworded as an item of its own kind and level
    keeps its native bullet, list and numbering, first item included."""
    doc = route.load(NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                               ("p", "b", "NORMAL_TEXT", NUMBERED),
                               ("p", "c", "NORMAL_TEXT", NUMBERED)))
    route.ok("cat")
    route.ok("edit", old_text=target, new_text=new)
    lists = {bullet[0] for _, _, bullet in styles(doc) if bullet}
    if expected is None:
        # Another kind of item is a new list.
        assert _read(route) == "- a2\n1. b\n2. c\n" and len(lists) == 2
        return
    assert _read(route) == expected
    assert lists == {1}


def test_items_replacing_an_item_continue_its_list(route):
    doc = route.load(NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                               ("p", "b", "NORMAL_TEXT", NUMBERED),
                               ("p", "c", "NORMAL_TEXT", NUMBERED)))
    route.ok("cat")
    route.ok("edit", old_text="b", new_text="2. b2\n3. b3\n  1. sub")
    assert _read(route) == "1. a\n2. b2\n3. b3\n  1. sub\n4. c\n"
    assert [bullet for _, _, bullet in styles(doc)] == [
        (1, 0), (1, 0), (1, 0), (1, 1), (1, 0)]


@pytest.mark.parametrize("blocks,old,expected", [
    ((("p", "Hello"),), "Hello", "\n"),
    ((("p", "Hello"), ("p", "Tail")), "Hello\nTail", "\n"),
    ((("p", "Hello"), ("t", [["x"]])), "Hello", None),
])
def test_removing_from_the_body_start(route, blocks, old, expected):
    """Final review F2: the body's leading section break holds no mark to
    borrow; the segment's only paragraphs keep one, a table refuses."""
    route.load(NativeDoc(*blocks))
    route.ok("cat")
    code, output, error = route.call("edit", old_text=old, new_text="")
    assert "unexpected error" not in output + error
    if expected is None:
        assert code != 0 and "before a table" in output + error
        assert not route.service.batches
    else:
        assert code == 0 and _read(route) == expected


def test_image_destination_whitespace_is_not_part_of_the_uri():
    """Final review F3: as for links, whitespace and a title are dropped."""
    from gdoc.mdparse import parse_markdown

    for source in ("![a]( https://x.org/i.png )", '![a]( https://x.org/i.png "t")'):
        assert parse_markdown(source).images[0].uri == "https://x.org/i.png"


def test_new_from_a_non_utf8_file_exits_3(tmp_path, capsys):
    """Final review F5: undecodable input is a usage error for new --file."""
    from gdoc.cli import run_argv

    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe bad")
    try:
        code = run_argv(["new", "Title", "--file", str(path)], check_updates=False)
    except SystemExit as exc:
        code = exc.code
    assert code == 3 and "cannot read file" in capsys.readouterr().err


@pytest.mark.parametrize("base,inserted", [
    ("1. parent\n", "  1. child\n"),
    ("1. parent\n", "  1. child\n2. sibling\n"),
    ("- parent\n", "  - child\n"),
    ("1. parent\n", "  - child\n"),
])
def test_appended_nested_item_continues_the_parent_list(route, base, inserted):
    """Final adversarial review: appending a nested item after a top-level
    item gives the native lists a write of the concatenation gives."""
    expected_doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base + inserted)
    expected = (_read(route), styles(expected_doc))
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("insert", text=inserted, tab="t.0", position="end")
    assert (_read(route), styles(doc)) == expected


def test_continuing_items_do_not_warn_about_their_start(route):
    route.load(NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                         ("p", "b", "NORMAL_TEXT", NUMBERED),
                         ("p", "c", "NORMAL_TEXT", NUMBERED)))
    route.ok("cat")
    code, output, error = route.call("edit", old_text="b", new_text="2. b2\n3. b3")
    assert code == 0 and "start at 1" not in output + error
    assert _read(route) == "1. a\n2. b2\n3. b3\n4. c\n"


FOUR = [("p", t, "NORMAL_TEXT", NUMBERED) for t in "abcd"]


@pytest.mark.parametrize("old,new,expected", [
    ("b\nc", "2. b2\n3. c2", "1. a\n2. b2\n3. c2\n4. d\n"),
    ("a", "1. a1\n2. a2", "1. a1\n2. a2\n3. b\n4. c\n5. d\n"),
    ("d", "4. d1\n5. d2", "1. a\n2. b\n3. c\n4. d1\n5. d2\n"),
])
def test_items_reworded_in_place_keep_their_list(route, old, new, expected):
    """Final review round 2: rewording or expanding existing items, first
    item included, keeps one native list and the untouched items' numbers."""
    doc = route.load(NativeDoc(*FOUR))
    route.ok("cat")
    code, output, error = route.call("edit", old_text=old, new_text=new)
    assert code == 0 and "start at 1" not in output + error
    assert _read(route) == expected
    assert {bullet for _, _, bullet in styles(doc)} == {(1, 0)}


@pytest.mark.parametrize("base,command,arguments", [
    ("1. parent\n", "insert",
     {"text": "  7. child\n", "tab": "t.0", "position": "end"}),
    ("1. a\n2. b\n3. c\n", "edit", {"old_text": "b", "new_text": "7. b2\n8. b3"}),
    ("1. a\n2. b\n3. c\n", "edit", {"old_text": "b", "new_text": "2. b2\n  7. sub"}),
])
def test_starts_that_do_not_match_still_warn(route, base, command, arguments):
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    code, output, error = route.call(command, **arguments)
    assert code == 0 and "start at 1" in output + error


@pytest.mark.parametrize("base,inserted", [
    ("1. p\n", "2. q\n"),
    ("- p\n", "- q\n"),
    ("1. p\n", "1. q\n"),
])
def test_appended_items_join_the_list_as_the_concatenation_does(
        route, base, inserted):
    """Final review round 2 (F3): an appended next item continues the list;
    a restart stays its own list."""
    expected_doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base + inserted)
    expected = (_read(route), styles(expected_doc))
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("insert", text=inserted, tab="t.0", position="end")
    assert (_read(route), styles(doc)) == expected


def test_plain_paragraph_under_a_list_continues_it(route):
    doc = route.load(NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                               ("p", "P"), ("p", "Tail")))
    route.ok("cat")
    route.ok("edit", old_text="P", new_text="2. x\n3. y")
    assert _read(route) == "1. a\n2. x\n3. y\nTail\n"
    assert {bullet[0] for _, _, bullet in styles(doc) if bullet} == {1}


def test_top_level_item_after_a_nested_last_item_warns(route):
    """Joining the list below a nested item would need negative tabs, so a
    top-level item appended there is its own list, with the start warning."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="1. p\n  1. c\n")
    code, output, error = route.call("insert", text="2. q\n", tab="t.0",
                                     position="end")
    assert code == 0 and "start at 1" in output + error


def test_mixed_item_and_prose_become_one_list(route):
    """Round 3 (R3-1): an item and a prose paragraph replaced by two items
    make one list."""
    doc = route.load(NativeDoc(("p", "one", "NORMAL_TEXT", NUMBERED),
                               ("p", "two")))
    route.ok("cat")
    route.ok("edit", old_text="one\ntwo", new_text="1. NEW\n2. AGAIN")
    assert _read(route) == "1. NEW\n2. AGAIN\n"
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 1


def test_heading_requested_on_an_item_is_applied(route):
    """Round 3 (R3-2): the keep-bullet shortcut does not drop a heading."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="- one\n- two\n")
    route.ok("edit", old_text="one", new_text="- ## NEW")
    assert _read(route) == "- ## NEW\n- two\n"
    assert styles(doc)[0][1] == "HEADING_2"


def test_restart_inside_an_appended_fragment_still_warns(route):
    """Round 3 (R3-3): only the continuing list's numbers are predicted."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="1. a\n")
    code, output, error = route.call(
        "insert", text="2. b\n1. restart\n4. requested\n", tab="t.0",
        position="end")
    assert code == 0 and "starts at 4" in output + error
