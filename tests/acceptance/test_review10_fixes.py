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


def _read_after_write(route, markdown):
    _written(route, markdown)
    return _read(route)


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
    # A same-kind item after the sub-item stays refused, as at 39ab543.
    ("2. b2\n  - sub\n3. b3", None),
])
def test_r1_03_mid_list_item_gains_a_sub_item_of_the_other_kind(route, new, expected):
    _written(route, "1. a\n2. b\n3. c\n")
    code, output, error = route.call("edit", old_text="b", new_text=new)
    if expected is None:
        assert code != 0 and "new list" in output + error
    else:
        assert code == 0 and _read(route) == expected


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
        assert code != 0 and "splitting that list" in output + error
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


@pytest.mark.parametrize("new,expected", [
    ("# h", "| S | # h |"), ("- l", "| S | - l |"), ("1. x", "| S | 1. x |"),
    ("> q", "| S | > q |"), ("**b** `c`", "| S | **b** `c` |"),
])
def test_r2_03_cell_content_is_inline_only(route, new, expected):
    """Round-2 review R2-03: a cell's own spelling writes literal text."""
    doc = _written(route, "| k | v |\n| --- | --- |\n| S | old |\n")
    route.ok("edit", cell="1,1", tab="Main", new_text=new)
    assert expected in _read(route)
    assert all(style in (None, "NORMAL_TEXT") and not bullet
               for _, style, bullet in styles(doc))


def test_r2_02_cell_keeps_an_image_after_br(route):
    """Round-2 review R2-02: as at 39ab543, `<br>` in a --cell replacement
    stays literal and an image after it is inserted."""
    _written(route, "| k | v |\n| --- | --- |\n| S | old |\n")
    route.ok("edit", cell="1,1", tab="Main", new_text="a<br>![i](http://x/i.png)")
    assert "gdoc-image:" in _read(route)


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


@pytest.mark.parametrize("markdown,old,new", [
    ("> quote\n> more\n\nout\n", "quote", "- X\ntext"),
    ("> quote\n> more\n\nout\n", "quote", "text\n- X"),
    ("1. a\n\n   para\n   more\n2. b\n", "para", "- X\ntext"),
])
def test_r2_01_one_item_plus_text_in_a_container_is_refused(route, markdown, old, new):
    """Round-2 review R2-01: as at 39ab543, a one-item multi-line replacement
    of a container paragraph is refused (paragraph count), not restructured."""
    _written(route, markdown)
    batches = len(route.service.batches)
    code, output, error = route.call("edit", old_text=old, new_text=new)
    assert code != 0 and "paragraph count" in output + error
    assert len(route.service.batches) == batches


def _pull(monkeypatch, tmp_path, *blocks):
    from gdoc import cli

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(*blocks))
    pulled = tmp_path / "d.md"
    assert cli.run_argv(["pull", "synthetic", str(pulled)], check_updates=False) == 0
    return route, pulled


def test_r2_05_mcp_write_of_pulled_text_creates_no_files(monkeypatch, tmp_path):
    import tempfile

    route, pulled = _pull(monkeypatch, tmp_path, ("p", "Alpha."))
    route.interface = "mcp"
    server = tmp_path / "server-tmp"
    server.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(server))
    route.ok("write", text=pulled.read_text().replace("Alpha.", "Alpha one."))
    assert list(server.iterdir()) == []
    assert _read(route) == "Alpha one.\n"


def test_r2_15_another_tabs_file_says_so(monkeypatch, tmp_path):
    from gdoc import cli

    route, pulled = _pull(monkeypatch, tmp_path, ("p", "Alpha."))
    text = pulled.read_text()
    fingerprint = next(line for line in text.splitlines()
                       if line.startswith("gdoc-tab-sha256:"))
    pulled.write_text(text.replace(fingerprint, "gdoc-tab-sha256: " + "0" * 64)
                      .replace("Alpha.", "Other."))
    import contextlib
    import io

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = cli.run_argv(["write", "synthetic", str(pulled)], check_updates=False)
    assert code == 3 and "another tab" in err.getvalue()


def test_r2_18_a_pulled_file_saved_with_a_bom_writes_twice(monkeypatch, tmp_path):
    """Round-2 review R2-18: a byte-order mark hides neither the provenance
    nor its refresh, and the mark is kept."""
    from gdoc import cli

    route, pulled = _pull(monkeypatch, tmp_path, ("p", "Alpha."))
    for old, new in (("Alpha.", "Beta."), ("Beta.", "Gamma.")):
        text = pulled.read_text(encoding="utf-8").removeprefix("\ufeff")
        pulled.write_text("\ufeff" + text.replace(old, new), encoding="utf-8")
        assert cli.run_argv(["write", "synthetic", str(pulled)],
                            check_updates=False) == 0
        assert pulled.read_text(encoding="utf-8").startswith("\ufeff---\n")
    assert _read(route) == "Gamma.\n"


def test_r2_04_a_table_between_quoted_blanks_matches_write(route):
    """Round-2 review R2-04: the quote's own blanks are the separators."""
    quoted = "\n".join("> " + line for line in T.split("\n"))
    want = _read_after_write(route, f"> A\n>\n{quoted}\n>\n> B\n")
    _written(route, "> A\n>\n> Mid\n>\n> B\n")
    route.ok("edit", old_text="Mid", new_text=T)
    assert _read(route) == want


def test_r2_12_a_styled_blank_before_a_table_is_kept(route):
    """Round-2 review R2-12: a centred blank is content, not a separator."""
    doc = _written(route, "A\n\nMid\n\nB\n")
    blank = next(start for start, mark in doc.paragraphs()
                 if mark > start and doc.units[start].ch == "M") - 1
    doc.units[blank].ps.update({"alignment": "CENTER"})
    route.ok("cat")
    route.ok("edit", old_text="Mid", new_text=T)
    centred = [mark for start, mark in doc.paragraphs()
               if doc.units[mark].ps.get("alignment") == "CENTER"]
    assert len(centred) == 1
