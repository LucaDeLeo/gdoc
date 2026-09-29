"""Independent review round 1 of 39ab543 (R1-xx): regressions through CLI and
MCP on the native model. Each targeted edit is compared with a write of the
Markdown it asks for, natively where lists or containers matter."""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.acceptance.test_round8_insert import _native
from tests.native_model import NativeDoc, styles

T = "| a |\n| --- |\n| 1 |"


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


def _oracle(route, markdown):
    doc = _written(route, markdown)
    return _read(route), _native(doc)


def test_r1_01_quoted_items_reworded_stay_in_the_quote(route):
    doc = _written(route, "> 1. a\n> 2. b\n> 3. c\n")
    route.ok("edit", old_text="b", new_text="2. B\n3. X")
    first = _read(route)
    assert first == "> 1. a\n> 2. B\n> 3. X\n> 4. c\n"
    route.ok("write", text=first.replace("B", "B2"))
    assert _read(route) == "> 1. a\n> 2. B2\n> 3. X\n> 4. c\n"
    assert len({bullet[0] for _, _, bullet in styles(doc) if bullet}) == 1


def test_r1_02_a_pulled_file_writes_again_after_its_own_write(
        monkeypatch, tmp_path):
    """A write of a pulled file advances its provenance as push does; a
    collaborator's edit still makes it stale."""
    from gdoc import cli

    def run(*argv):
        try:
            return cli.run_argv(list(argv), check_updates=False)
        except SystemExit as exc:
            return exc.code

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha."), ("p", "Beta.")))
    pulled = tmp_path / "d.md"
    assert run("pull", "synthetic", str(pulled)) == 0
    for old, new in (("Alpha.", "Alpha one."), ("Beta.", "Beta two.")):
        pulled.write_text(pulled.read_text().replace(old, new))
        assert run("write", "synthetic", str(pulled)) == 0
    pulled.write_text(pulled.read_text().replace("Beta two.", "Beta three."))
    assert run("push", str(pulled)) == 0
    assert _read(route) == "Alpha one.\nBeta three.\n"
    route.service.doc.op_insert_text({"location": {"index": 1}, "text": "X"})
    route.service.revision += 1
    pulled.write_text(pulled.read_text().replace("Beta three.", "Beta four."))
    assert run("write", "synthetic", str(pulled)) == 3


@pytest.mark.parametrize("new,expected", [
    ("2. b2\n  - sub", "1. a\n2. b2\n  - sub\n3. c\n"),
    ("2. b2\n  - sub\n3. b3", "1. a\n2. b2\n  - sub\n3. b3\n4. c\n"),
])
def test_r1_03_mid_list_item_gains_a_sub_item_of_the_other_kind(route, new, expected):
    _written(route, "1. a\n2. b\n3. c\n")
    route.ok("edit", old_text="b", new_text=new)
    assert _read(route) == expected


@pytest.mark.parametrize("markdown,expected", [
    ("A\n\n---\nMid\n\nB\n", f"A\n\n---\n\n{T}\n\nB\n"),
    ("A\n\nMid\n---\n\nB\n", f"A\n\n{T}\n\n---\n\nB\n"),
    ("A\n\n```\ncode\n\n```\nMid\n\nB\n", f"A\n\n```\ncode\n\n```\n\n{T}\n\nB\n"),
    ("A\n\n## \nMid\n\nB\n", f"A\n\n## \n\n{T}\n\nB\n"),
])
def test_r1_04_a_table_beside_non_blank_neighbours_keeps_them(
        route, markdown, expected):
    want = _oracle(route, expected)
    doc = _written(route, markdown)
    route.ok("edit", old_text="Mid", new_text=T)
    assert (_read(route), _native(doc)) == want


def test_r1_05_the_table_path_refuses_a_suggested_blank(route):
    doc = _written(route, "A\n\nMid\n\nB\n")
    blank_after = next(mark for start, mark in doc.paragraphs()
                       if mark > start and doc.units[start].ch == "M") + 1
    doc.units[blank_after].suggested = "suggest.1"
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text="Mid", new_text=T)
    assert code != 0 and "suggest" in output + error
    assert len(route.service.batches) == batches


@pytest.mark.parametrize("markdown,old,expected", [
    ("A\n\n> Q1\n> Q2\n\nZ\n", "Q1\nQ2",
     "A\n\n> | a |\n> | --- |\n> | 1 |\n\nZ\n"),
    ("1. a\n\n   K1\n\n   K2\n\n2. b\n", "K1\n\nK2",
     "1. a\n\n   | a |\n   | --- |\n   | 1 |\n\n2. b\n"),
])
def test_r1_06_a_table_replacing_container_paragraphs_stays_inside(
        route, markdown, old, expected):
    _written(route, markdown)
    route.ok("edit", old_text=old, new_text=T)
    assert _read(route) == expected


@pytest.mark.parametrize("markdown,old,new,expected", [
    ("1. p\n  1. c\n2. q\n", "q", "  1. x", "1. p\n  1. c\n  1. x\n"),
    ("1. p\n  1. c\n2. q\n", "c", "1. x", None),
])
def test_r1_07_a_level_change_does_not_take_the_slot_silently(
        route, markdown, old, new, expected):
    """A nest or unnest matches a write of the edited Markdown, or is
    refused; it never silently takes the replaced item's number."""
    _written(route, markdown)
    code, output, error = route.call("edit", old_text=old, new_text=new)
    if expected is None:
        assert code != 0 and "renumbering" in output + error
    else:
        assert code == 0
        got = _read(route)
        assert got == _oracle(route, expected)[0]


def test_r1_08_insert_does_not_continue_a_list_across_a_quote(route):
    want = _oracle(route, "> 1. x\n> 2. y\n3. a\n")
    doc = _written(route, "> 1. x\n> 2. y\n")
    route.ok("insert", text="3. a\n", tab="t.0", position="end")
    assert (_read(route), _native(doc)) == want


def test_r1_09_an_empty_final_nested_item_keeps_its_level(route):
    doc = _written(route, "Intro\n\n> - a\n>   - \n")
    first = _read(route)
    for word in ("one", "two"):
        route.ok("write", text=first.replace("Intro", f"Intro {word}"))
    assert [bullet[1] for _, _, bullet in styles(doc) if bullet] == [0, 1]


def test_r1_10_a_bare_line_after_one_item_is_prose(route):
    _written(route, "1. a\n2. b\n3. c\nTail\n")
    route.ok("edit", old_text="b\nc", new_text="2. B\nC")
    assert _read(route) == "1. a\n2. B\nC\nTail\n"


def test_r1_15_a_cell_writes_br_as_a_break(route):
    markdown = "| k | v |\n| --- | --- |\n| Status | a<br>b |\n"
    want = _oracle(route, markdown.replace("a<br>b", "a<br>c"))[0]
    _written(route, markdown)
    route.ok("edit", cell="1,1", tab="Main", new_text="a<br>c")
    assert _read(route) == want


@pytest.mark.parametrize("new,cell", [
    ("a\\<br>b", "a\\<br>b"),
    ("`<br>`", "`<br>`"),
    ("a<br>- b", "a<br>- b"),
])
def test_f1_01_cell_breaks_follow_the_table_rules(route, new, cell):
    """Follow-up review F1-01: escaped and code `<br>` stay literal, and text
    after a break stays inline."""
    markdown = "| k | v |\n| --- | --- |\n| Status | old |\n"
    want = _oracle(route, markdown.replace("old", cell))[0]
    _written(route, markdown)
    route.ok("edit", cell="1,1", tab="Main", new_text=new)
    assert _read(route) == want


def test_f1_02_a_current_pulled_file_collapses_tabs(monkeypatch, tmp_path):
    """Follow-up review F1-02: at the current revision a matching fingerprint
    is current, with --force-collapse-tabs too."""
    from gdoc import cli

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha")))
    pulled = tmp_path / "d.md"
    assert cli.run_argv(["pull", "synthetic", str(pulled)], check_updates=False) == 0
    pulled.write_text(pulled.read_text().replace("Alpha", "Beta"))
    code = cli.run_argv(["write", "synthetic", str(pulled), "--force-collapse-tabs"],
                        check_updates=False)
    assert code == 0 and _read(route) == "Beta\n"
