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


def test_single_item_under_a_list_continues_it(route):
    """Round 3 (Claude R3-2): one new item under a list continues it."""
    doc = route.load(NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                               ("p", "P"), ("p", "Tail")))
    route.ok("cat")
    code, output, error = route.call("edit", old_text="P", new_text="2. x")
    assert code == 0 and "start at 1" not in output + error
    assert _read(route) == "1. a\n2. x\nTail\n"
    assert {bullet[0] for _, _, bullet in styles(doc) if bullet} == {1}


def test_first_items_expanded_at_their_level_keep_the_list(route):
    """Round 3 (Claude R3-1): several first items reworded as more items of
    their level keep the list; the untouched items keep their numbers."""
    doc = route.load(NativeDoc(*FOUR))
    route.ok("cat")
    route.ok("edit", old_text="a\nb", new_text="1. A\n2. B\n3. X")
    assert _read(route) == "1. A\n2. B\n3. X\n4. c\n5. d\n"
    assert {bullet for _, _, bullet in styles(doc)} == {(1, 0)}


def test_reshaping_a_first_item_that_would_split_the_list_is_refused(route):
    """Round 3 (Claude R3-1): a nested item added under the first item would
    start a new list before the rest; refused, nothing sent."""
    route.load(NativeDoc(*FOUR))
    route.ok("cat")
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text="a",
                                     new_text="1. a1\n  1. sub")
    assert code != 0 and "renumbering" in output + error
    assert len(route.service.batches) == batches


def test_kept_item_warning_names_the_number_it_shows(route):
    """Round 3 (Claude R3-4)."""
    route.load(NativeDoc(*FOUR))
    route.ok("cat")
    code, output, error = route.call("edit", old_text="b", new_text="7. b2")
    assert code == 0 and "shows 2" in output + error


def test_restart_with_another_number_is_its_own_list(route):
    """Round 3 (GPT 1): a top-level restart that does not continue is a new
    list, reset to 1 with a warning."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    code, output, error = route.call("write", text="1. a\n2. b\n2. restart\n")
    assert code == 0 and "start at 1" in output + error
    assert _read(route) == "1. a\n2. b\n1. restart\n"
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 2


def test_append_after_a_list_with_another_preset_warns(route):
    """Round 3 (GPT 2): createParagraphBullets joins only a matching preset,
    so a UI-made list is not assumed to continue."""
    roman = {"preset": "NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL", "list": 1, "nest": 0}
    route.load(NativeDoc(("p", "a", "NORMAL_TEXT", roman),
                         ("p", "b", "NORMAL_TEXT", roman)))
    route.ok("cat")
    code, output, error = route.call("insert", text="3. c\n", tab="t.0",
                                     position="end")
    assert code == 0 and "start at 1" in output + error


def test_nested_item_continues_a_nested_item_above(route):
    """Round 3 (GPT 3): same-level continuation below a nested item."""
    expected_doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="1. parent\n  1. child\n  2. sibling\n")
    expected = (_read(route), styles(expected_doc))
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="1. parent\n  1. child\n")
    code, output, error = route.call("insert", text="  2. sibling\n", tab="t.0",
                                     position="end")
    assert code == 0 and "start at 1" not in output + error
    assert (_read(route), styles(doc)) == expected


@pytest.mark.parametrize("base,old,new,expected", [
    ("- p\n  - c\n    - a\n", "a", "    - X![](https://example.test/i.png)",
     "- p\n  - c\n    - X![]"),
    ("- p\n  - a\n", "a", "  - A\n  - B![](https://example.test/i.png)",
     "- p\n  - A\n  - B![]"),
])
def test_images_in_kept_nested_items_keep_their_text(route, base, old, new, expected):
    """Round 4 (Codex R4-1): image positions follow the removed nesting tabs."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route).startswith(expected)


@pytest.mark.parametrize("base", ["- a\n1. b\n", "1. a\n\n1. b\n"])
def test_items_from_separate_lists_become_one_list(route, base):
    """Round 4 (Codex R4-2)."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    old = "a\n\nb" if "\n\n" in base else "a\nb"
    route.ok("edit", old_text=old, new_text="1. A\n\n2. B" if "\n\n" in base
             else "1. A\n2. B")
    assert "1. A" in _read(route) and "2. B" in _read(route)
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 1


def test_expansion_with_a_restart_keeps_the_restart(route):
    """Round 4 (Codex R4-3)."""
    doc = route.load(NativeDoc(("p", "one", "NORMAL_TEXT", NUMBERED), ("p", "Tail")))
    route.ok("cat")
    route.ok("edit", old_text="one", new_text="1. A\n1. B")
    assert _read(route) == "1. A\n1. B\nTail\n"
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 2


def test_kept_nested_item_with_its_own_number_does_not_warn(route):
    """Round 4 (Codex R4-4)."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="1. p\n  1. one\n  2. two\n")
    code, output, error = route.call("edit", old_text="two", new_text="  2. TWO")
    assert code == 0 and "start at 1" not in output + error
    assert _read(route) == "1. p\n  1. one\n  2. TWO\n"


@pytest.mark.parametrize("base,old,new,expected", [
    ("- a\n- b\n- c\n", "a\nb\nc", "1. x\n2. y\n3. z", "1. x\n2. y\n3. z\n"),
    ("1. a\n2. b\n3. c\n4. d\n", "b\nc", "- x\n- y", None),
    ("1. a\n  1. b\n  2. c\n2. d\n", "b\nc", "2. x\n3. y", "1. a\n2. x\n3. y\n4. d\n"),
])
def test_same_count_edits_changing_kind_or_level_make_one_list(
        route, base, old, new, expected):
    """Round 4 (Claude R4-1): a same-count edit that changes the items' kind
    or level compiles as one list."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("edit", old_text=old, new_text=new)
    if expected:
        assert _read(route) == expected
    lists = {}
    for text, _, bullet in styles(doc):
        if bullet:
            lists.setdefault(text in ("x", "y", "z"), set()).add(bullet[0])
    assert len(lists[True]) == 1


def test_append_after_a_parenthesized_list_warns(route):
    """Round 4 (Claude R4-3): a 1) list is not gdoc's preset; no join is
    assumed, and the reset warns."""
    parens = {"preset": "NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS", "list": 1, "nest": 0}
    route.load(NativeDoc(("p", "a", "NORMAL_TEXT", parens),
                         ("p", "b", "NORMAL_TEXT", parens)))
    route.ok("cat")
    code, output, error = route.call("insert", text="3. c\n", tab="t.0",
                                     position="end")
    assert code == 0 and "start at 1" in output + error


@pytest.mark.parametrize("inserted,warns", [("7. c\n", False), ("3. c\n", True)])
def test_continuation_counts_from_the_list_start(route, inserted, warns):
    """Round 4 (GPT 2): a list starting at 5 (set in the Docs UI) shows 5, 6;
    an appended 7 continues it, and a 3 warns."""
    doc = NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                    ("p", "b", "NORMAL_TEXT", NUMBERED))
    doc.list_starts[1] = 5
    route.load(doc)
    assert _read(route) == "5. a\n6. b\n"
    code, output, error = route.call("insert", text=inserted, tab="t.0",
                                     position="end")
    assert code == 0 and ("start at 1" in output + error) == warns
    if not warns:
        assert _read(route) == "5. a\n6. b\n7. c\n"


@pytest.mark.parametrize("base,old,new,expected", [
    ("1. a\n2. b\n", "a\nb", "1. A\n1. B", "1. A\n1. B\n"),
    ("1. p\n  1. a\n  2. b\n", "a\nb", "  1. A\n  1. B", "1. p\n  1. A\n  1. B\n"),
])
def test_same_count_restart_is_kept(route, base, old, new, expected):
    """Round 5 (Codex R5-1): a restart between replaced items stays."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route) == expected


@pytest.mark.parametrize("base,old,new,expected", [
    ("1. a\n2. b\n3. c\n", "a\nb\nc", "1. x\n2. y\n\nprose", "1. x\n2. y\n\nprose\n"),
    ("1. a\n2. b\n3. c\n", "b\nc", "2. b2\n3. c2\n## Head",
     "1. a\n2. b2\n3. c2\n## Head\n"),
    ("1. a\n2. b\npara\n", "b\npara", "2. b2\n3. c", "1. a\n2. b2\n3. c\n"),
])
def test_multi_item_replacements_reset_inherited_bullets(
        route, base, old, new, expected):
    """Round 5 (Claude F1, F3): prose after new items loses the replaced
    items' bullet; replacing the last item and the paragraph after it
    continues the list."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route) == expected


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
    assert code != 0 and "renumbering" in output + error
    assert len(route.service.batches) == batches


def test_customized_deeper_level_is_not_the_default_preset():
    """Round 5 (GPT 1): every level must match gdoc's preset (checked against
    live list definitions)."""
    from gdoc.api.docs import _default_preset

    levels = [{"glyphType": t, "glyphFormat": f"%{k}."}
              for k, t in enumerate(["DECIMAL", "ALPHA", "ROMAN"] * 3)]
    lists = {"L": {"listProperties": {"nestingLevels": levels}}}
    assert _default_preset(lists, "L")
    levels[2] = {"glyphType": "ROMAN", "glyphFormat": "(%2)"}
    assert not _default_preset(lists, "L")


def test_expansion_ending_with_an_empty_item_keeps_the_list(route):
    """Round 5 (GPT 2)."""
    doc = route.load(NativeDoc(("p", "a", "NORMAL_TEXT", NUMBERED),
                               ("p", "b", "NORMAL_TEXT", NUMBERED),
                               ("p", "c", "NORMAL_TEXT", NUMBERED)))
    route.ok("cat")
    route.ok("edit", old_text="a", new_text="1. A\n2. ")
    assert _read(route) == "1. A\n2. \n3. b\n4. c\n"
    assert {bullet for _, _, bullet in styles(doc)} == {(1, 0)}


def test_cell_paragraphs_become_one_list(route):
    """Round 6 (Codex R6-1): equal-count cell replacements make one list."""
    doc = route.load(NativeDoc(("t", [["a\nb"]])))
    route.ok("cat")
    route.ok("edit", cell="0,0", tab="Main", new_text="1. A\n2. B")
    lists = {bullet[0] for _, _, bullet in styles(doc) if bullet}
    assert len(lists) == 1


@pytest.mark.parametrize("base,old,new", [
    ("1. a\n2. b\n3. c\n4. d\n", "b\nc", "2. B\n1. C"),
    ("1. a\n2. b\n3. c\n", "a\nb", "- A\n1. B"),
    ("1. a\n2. b\n3. c\n", "b", "- x\n1. y"),
])
def test_restarts_that_renumber_later_items_are_refused(route, base, old, new):
    """Round 6 (Codex R6-2)."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text=old, new_text=new)
    assert code != 0 and "renumbering" in output + error
    assert len(route.service.batches) == batches


def test_other_numbered_styles_are_not_the_default_preset():
    """Round 6 (Claude R6-1): A. B. and roman lists are not gdoc's preset."""
    from gdoc.api.docs import _default_preset

    for glyph in ("UPPER_ALPHA", "UPPER_ROMAN", "ALPHA"):
        lists = {"L": {"listProperties": {"nestingLevels": [
            {"glyphType": glyph, "glyphFormat": f"%{k}."} for k in range(9)]}}}
        assert not _default_preset(lists, "L")


@pytest.mark.parametrize("base,command,arguments,expected", [
    ("1. a\n2. b\n", "insert", {"text": "3. c\n\n4. d\n", "tab": "t.0",
                                "position": "end"}, "1. a\n2. b\n3. c\n\n4. d\n"),
    ("1. a\n2. b\n3. c\n", "edit", {"old_text": "b", "new_text": "2. b\n\n3. x"},
     "1. a\n2. b\n\n3. x\n4. c\n"),
])
def test_continuation_across_a_blank_line(route, base, command, arguments, expected):
    """Round 6 (Claude R6-2): `3.` then `4.` after a blank line continues."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=base)
    route.ok(command, **arguments)
    assert _read(route) == expected
