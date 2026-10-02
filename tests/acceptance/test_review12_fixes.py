"""Independent review round 4 of b092848 (R4-xx): simple, conservative rules,
checked through CLI and MCP on the native model."""

import io
import json
from types import SimpleNamespace

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc

TEN = "".join(f"{k}. step {k}\n" for k in range(1, 11))


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _written(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    route.ok("cat")
    return doc


def _refused(route, command, needle, **arguments):
    before, batches = _read(route), len(route.service.batches)
    code, output, error = route.call(command, **arguments)
    assert code != 0 and needle in output + error, output + error
    assert len(route.service.batches) == batches and _read(route) == before


@pytest.mark.parametrize("base,old,intended", [
    # R4-01: nested under a parent numbered 10
    (TEN + "  - tip\n\n    more words\n", "tip\n", TEN + "\n    more words\n"),
    # R4-02: the item's content is a quoted list
    ("1. alpha\n2. bravo\n\n   > - quoted item\n3. charlie\n", "bravo\n",
     "1. alpha\n\n   > - quoted item\n2. charlie\n"),
    # R4-04: the item's content is a rule, or an empty heading
    ("1. a\n2. b\n\n   ---\n3. c\n", "b\n", "1. a\n\n   ---\n2. c\n"),
    ("1. a\n2. b\n\n   #\n3. c\n", "b\n", "1. a\n\n   #\n2. c\n"),
    # a deeper item follows
    ("1. alpha\n2. bravo\n  1. child\n3. charlie\n", "bravo\n",
     "1. alpha\n  1. child\n2. charlie\n"),
])
def test_r4_deleting_an_item_before_other_content_is_refused(
        route, base, old, intended):
    _written(route, base)
    _refused(route, "edit", "list's structure", old_text=old, new_text="")
    route.ok("write", text=intended)  # the working route


@pytest.mark.parametrize("base,old,expected", [
    ("1. a\n2. b\n3. c\n", "b\n", "1. a\n2. c\n"),
    ("- a\n- b\n\nProse.\n", "b\n", "- a\n\nProse.\n"),
    ("- a\n- b\n", "b", "- a\n"),
    ("1. a\n2. b\n\n   detail\n3. c\n", "b\n\ndetail\n", "1. a\n2. c\n"),
])
def test_r4_plain_deletions_still_work(route, base, old, expected):
    _written(route, base)
    route.ok("edit", old_text=old, new_text="")
    assert _read(route) == expected


@pytest.mark.parametrize("header", [
    # R4-03: an indented gdoc line after a readable key
    "---\ntitle: T\n  gdoc: synthetic\ngdoc-version: 1\n---\n",
    # R4-07: closers the parser doesn't read, and no closer
    "---\ngdoc: synthetic\ntitle: T\n--- \n",
    "---\ngdoc: synthetic\ntitle: T\n...\n",
    "---\ngdoc: synthetic\ntitle: T\n",
    # a comment line that mentions gdoc
    "---\n# gdoc: synthetic\ntitle: T\n---\n",
])
def test_r4_an_unreadable_gdoc_header_is_refused(route, header):
    _written(route, "Alpha.\n")
    _refused(route, "write", "may be a pulled file", text=header + "Changed.\n")


def test_r4_08_push_and_the_sync_hook_refuse_it_too(monkeypatch, tmp_path, capsys):
    from gdoc import cli

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    pulled = tmp_path / "d.md"
    pulled.write_text("---\ngdoc: synthetic\nhttps://example.com/ref\n---\nChanged.\n")
    assert cli.run_argv(["push", str(pulled)], check_updates=False) == 3
    assert "may be a pulled file" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(pulled)}})))
    assert cli.cmd_sync_hook(SimpleNamespace()) == 2
    assert "may be a pulled file" in capsys.readouterr().err
    assert not route.service.batches


def test_r4_06_a_sublist_then_an_item_of_the_list_is_refused(route):
    _written(route, "1. a\n2. b\n")
    _refused(route, "insert", "list's structure",
             text="  - X\n3. Z\n", tab="t.0", position="end")


def test_r4_12_nest_is_not_advised_for_an_item_with_sub_items(route):
    _written(route, "1. a\n2. b\n   1. c\n3. d\n")
    code, output, error = route.call("edit", old_text="b", new_text="  1. b")
    assert code != 0 and "gdoc nest" not in output + error
    assert "write" in output + error


def _old_style(monkeypatch, tmp_path, body, export="Alpha.\n", tabs=1):
    from gdoc import cli

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    monkeypatch.setattr("gdoc.api.drive.export_doc", lambda *a, **k: export)
    pulled = tmp_path / "d.md"
    pulled.write_text(f"---\ngdoc: synthetic\ntitle: T\n"
                      f"gdoc-version: {route.service.revision - 1}\n---\n{body}")
    return cli, route, pulled


def test_r4_09_edge_whitespace_is_not_in_sync(monkeypatch, tmp_path, capsys):
    cli, route, pulled = _old_style(monkeypatch, tmp_path, "    Alpha.\n")
    assert cli.run_argv(["write", "synthetic", str(pulled)], check_updates=False) == 3
    assert "already in sync" not in capsys.readouterr().out


def test_r4_10_in_sync_reports_the_tab(monkeypatch, tmp_path, capsys):
    cli, route, pulled = _old_style(monkeypatch, tmp_path, "Alpha.\n")
    assert cli.run_argv(["write", "synthetic", str(pulled), "--json"],
                        check_updates=False) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["in_sync"] is True and data["tab_id"] and "revision_id" in data


def test_r4_10_collapse_is_not_skipped(monkeypatch, tmp_path, capsys):
    cli, route, pulled = _old_style(monkeypatch, tmp_path, "Alpha.\n")
    code = cli.run_argv(["write", "synthetic", str(pulled), "--force-collapse-tabs"],
                        check_updates=False)
    # Without the shortcut, the stale stamp is refused as before.
    assert code == 3 and "already in sync" not in capsys.readouterr().out


@pytest.mark.parametrize("base,inserted,intended", [
    # deeper items above with nothing at the new item's level
    ("- a\n\nPara\n\n  - deep\n", "- c\n", "- a\n\nPara\n\n  - deep\n- c\n"),
    ("Para\n\n   1. deep\n", "1. c\n", "Para\n\n   1. deep\n1. c\n"),
    # the item above is inside another item's content
    ("1. a\n\n   > 1. q\n", "2. c\n", "1. a\n\n   > 1. q\n2. c\n"),
    ("- a\n\n  > - q\n", "- c\n", "- a\n\n  > - q\n- c\n"),
    # a number that continues an earlier numbered list across other blocks
    ("1. a\n\n> - quoted\n", "2. next\n", "1. a\n\n> - quoted\n2. next\n"),
])
def test_r4_06_inserts_that_may_join_a_list_are_refused(route, base, inserted,
                                                        intended):
    _written(route, base)
    _refused(route, "insert", "list's structure", text=inserted, tab="t.0",
             position="end")
    route.ok("write", text=intended)


# Codex review of the round-4 fixes (F7-01 to F7-03).

@pytest.mark.parametrize("value", ["1. ```bash", "1. ~~~", "- ```"])
def test_f7_01_a_marker_fence_in_a_cell_stays_its_text(route, value):
    _written(route, "| h |\n| --- |\n| old |\n")
    route.ok("edit", cell="1,0", tab="Main", new_text=value)
    got = _read(route)
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=f"| h |\n| --- |\n| {value} |\n")
    assert got == _read(route)


@pytest.mark.parametrize("inserted", ["Paragraph\n\n3. c\n", "- x\n3. c\n"])
def test_f7_02_a_later_numbered_item_is_checked_too(route, inserted):
    _written(route, "1. a\n2. b\n")
    _refused(route, "insert", "list's structure", text=inserted, tab="t.0",
             position="end")
    route.ok("write", text="1. a\n2. b\n" + inserted)


def test_f7_03_an_empty_sub_item_counts(route):
    _written(route, "1. a\n2. b\n  1. \n3. d\n")
    code, output, error = route.call("edit", old_text="b", new_text="  1. b")
    assert code != 0 and "gdoc nest" not in output + error


@pytest.mark.parametrize("inserted", [
    "> 1. quoted\n3. c\n", "Paragraph\n\n  1. child\n3. c\n", "Paragraph\n\n3.\n",
])
def test_f7b_any_other_number_is_refused(route, inserted):
    _written(route, "1. a\n2. b\n")
    _refused(route, "insert", "list's structure", text=inserted, tab="t.0",
             position="end")
    route.ok("write", text="1. a\n2. b\n" + inserted)


# Round-4 repro rerun at 7ac21a8: the two remaining defects.

def test_an_unclosed_fence_in_an_item_is_refused(route):
    _written(route, "Alpha.\n")
    _refused(route, "write", "code fence gdoc doesn't read",
             text="1. a\n\n   ```\n   x\n2. b\n\n## H\n\nprose\n")
    route.ok("write", text="1. a\n\n   ```\n   x\n   ```\n2. b\n\n## H\n\nprose\n")


def test_deleting_an_item_before_unseparated_text_is_refused(route):
    _written(route, "- parent\n  1. DELETE\nDETAIL\n- next\n")
    _refused(route, "edit", "list's structure", old_text="DELETE\n", new_text="")
    route.ok("write", text="- parent\nDETAIL\n- next\n")
