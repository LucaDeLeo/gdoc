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


def test_wording_like_a_list_start_does_not_warn(route):
    route.load(NativeDoc(("p", "Step Original here"), ("p", "Tail")))
    route.ok("cat")
    code, output, error = route.call("edit", old_text="Original", new_text="7. New")
    assert code == 0 and "start at 1" not in output + error


NUMBERED = {"preset": "NUMBERED_DECIMAL_ALPHA_ROMAN", "list": 1, "nest": 0}


@pytest.mark.parametrize("target,new,expected", [
    ("b", "2. b2", "1. a\n2. b2\n3. c\n"),
    # Another number is a restart in Markdown, a list restructure.
    ("b", "1. b2", None),
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
    if expected is None:
        # Another kind of item is a list restructure, left to write.
        code, output, error = route.call("edit", old_text=target, new_text=new)
        assert code != 0 and "list's structure" in output + error
        return
    route.ok("edit", old_text=target, new_text=new)
    lists = {bullet[0] for _, _, bullet in styles(doc) if bullet}
    assert _read(route) == expected
    assert lists == {1}


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


FOUR = [("p", t, "NORMAL_TEXT", NUMBERED) for t in "abcd"]


@pytest.mark.parametrize("old,new,expected", [
    ("b\nc", "2. b2\n3. c2", "1. a\n2. b2\n3. c2\n4. d\n"),
])
def test_items_reworded_in_place_keep_their_list(route, old, new, expected):
    """Final review round 2: rewording existing items keeps one native list
    and the untouched items' numbers."""
    doc = route.load(NativeDoc(*FOUR))
    route.ok("cat")
    code, output, error = route.call("edit", old_text=old, new_text=new)
    assert code == 0 and "start at 1" not in output + error
    assert _read(route) == expected
    assert {bullet for _, _, bullet in styles(doc)} == {(1, 0)}


def test_heading_requested_on_an_item_is_applied(route):
    """Round 3 (R3-2): the keep-bullet shortcut does not drop a heading."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="- one\n- two\n")
    route.ok("edit", old_text="one", new_text="- ## NEW")
    assert _read(route) == "- ## NEW\n- two\n"
    assert styles(doc)[0][1] == "HEADING_2"


def test_reshaping_a_first_item_that_would_split_the_list_is_refused(route):
    """Round 3 (Claude R3-1): a nested item added under the first item would
    start a new list before the rest; refused, nothing sent."""
    route.load(NativeDoc(*FOUR))
    route.ok("cat")
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text="a",
                                     new_text="1. a1\n  1. sub")
    assert code != 0 and "list's structure" in output + error
    assert len(route.service.batches) == batches


def test_restart_with_another_number_is_its_own_list(route):
    """Round 3 (GPT 1): a top-level restart that does not continue is a new
    list, reset to 1 with a warning."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    code, output, error = route.call("write", text="1. a\n2. b\n2. restart\n")
    assert code == 0 and "start at 1" in output + error
    assert _read(route) == "1. a\n2. b\n1. restart\n"
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 2


@pytest.mark.parametrize("base,old,new,expected", [
    ("- p\n  - c\n    - a\n", "a", "    - X![](https://example.test/i.png)",
     "- p\n  - c\n    - X![]"),
])
def test_images_in_kept_nested_items_keep_their_text(route, base, old, new, expected):
    """Round 4 (Codex R4-1): image positions follow the removed nesting tabs."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route).startswith(expected)


def test_kept_nested_item_with_its_own_number_does_not_warn(route):
    """Round 4 (Codex R4-4)."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="1. p\n  1. one\n  2. two\n")
    code, output, error = route.call("edit", old_text="two", new_text="  2. TWO")
    assert code == 0 and "start at 1" not in output + error
    assert _read(route) == "1. p\n  1. one\n  2. TWO\n"


@pytest.mark.parametrize("base,old,new", [
    ("1. a\n2. b\n\nprose\n\n3. c\n", "a\nb", "1. a2\n  1. sub"),
    ("para\n1. a\n2. b\n", "para\na", "1. p\n2. a2"),
])
def test_splits_that_renumber_later_items_are_refused(route, base, old, new):
    """Round 5 (Claude F2)."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text=old, new_text=new)
    assert code != 0 and "list's structure" in output + error
    assert len(route.service.batches) == batches


def test_cell_paragraphs_stay_text(route):
    """Round 6 (Codex R6-1) is superseded by round-2 review R2-03: a cell
    holds inline text only, so item markers stay literal."""
    doc = route.load(NativeDoc(("t", [["a\nb"]])))
    route.ok("cat")
    route.ok("edit", cell="0,0", tab="Main", new_text="1. A\n2. B")
    assert not any(bullet for _, _, bullet in styles(doc))


@pytest.mark.parametrize("base,old,new", [
    ("1. a\n2. b\n3. c\n4. d\n", "b\nc", "2. B\n1. C"),
    ("1. a\n2. b\n3. c\n", "a\nb", "- A\n1. B"),
    ("1. a\n2. b\n3. c\n", "b", "- x\n1. y"),
    ("1. a\n2. b\n3. c\n", "b", "  1. x\n  - y\n2. B"),
    ("1. a\n2. b\n3. c\n", "b", "  1. x\n\n  1. y\n2. B"),
    ("1. a\n2. b\n3. c\n", "b", "  1. child\n1. restart"),
])
def test_restarts_that_renumber_later_items_are_refused(route, base, old, new):
    """Round 6 (Codex R6-2)."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text=old, new_text=new)
    assert code != 0 and "list's structure" in output + error
    assert len(route.service.batches) == batches
