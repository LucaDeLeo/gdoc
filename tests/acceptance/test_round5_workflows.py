"""Round-5 task routes through the CLI and MCP over the native document model.

Each test reads the document through the interface, changes the Markdown and
writes it back, then checks the native paragraphs the request sequence
produced, not only the requests' shape.
"""

import contextlib
import io
import json

import pytest

from gdoc import cli, mcp
from gdoc.api import comment_transport, docs, drive
from gdoc.frontmatter import parse_frontmatter
from tests.native_model import NativeDoc, NativeService, styles

TABLE = [["h"], ["v"]]


class NativeRoute:
    def __init__(self, interface, monkeypatch, tmp_path):
        self.interface, self.tmp_path = interface, tmp_path
        self.service = None
        monkeypatch.setattr(docs, "get_docs_service", lambda: self.service)
        monkeypatch.setattr(comment_transport, "execute_mutation_request",
                            lambda request, **_: request.execute())
        monkeypatch.setattr(drive, "get_file_version", lambda *a, **k: {
            "version": self.service.revision,
            "mimeType": "application/vnd.google-apps.document"})
        monkeypatch.setattr(drive, "get_file_info", lambda *a, **k: {
            "name": "Synthetic", "version": self.service.revision})
        monkeypatch.setattr("gdoc.api.comments.list_comments", lambda *a, **k: [])

    def load(self, doc):
        self.service = NativeService(doc)
        return doc

    def call(self, command, **arguments):
        arguments = {"doc": "synthetic", **arguments}
        if self.interface == "mcp":
            response = mcp.MCPServer().dispatch({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "gdoc_" + command, "arguments": arguments},
            })
            result = response.get("result", {})
            blocks = result.get("content", [])
            error = (json.dumps(response["error"]) if "error" in response
                     else "\n".join(b["text"] for b in blocks[1:]))
            code = int(bool("error" in response or result.get("isError")))
            return code, blocks[0]["text"] if blocks else "", error
        prepared = dict(arguments)
        argv = [command, prepared.pop("doc")]
        if command in ("write", "insert"):
            path = self.tmp_path / "input.md"
            path.write_text(prepared.pop("text"))
            argv.append(str(path))
        for key in ("old_text", "new_text"):
            if key in prepared:
                argv.append(prepared.pop(key))
        for key, value in prepared.items():
            flag = "--" + key.replace("_", "-")
            argv += [flag] if value is True else [flag, str(value)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.run_argv(argv, check_updates=False)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def ok(self, command, **arguments):
        code, output, error = self.call(command, **arguments)
        assert code == 0, output + error
        return output


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)



def _bullet(list_id=1, preset="BULLET_DISC_CIRCLE_SQUARE", nest=0):
    return {"preset": preset, "list": list_id, "nest": nest}


NUMBERED = "NUMBERED_DECIMAL_ALPHA_ROMAN"


def _shape(paragraphs, edited):
    """Paragraph structure with lists numbered by first appearance."""
    lists = {}
    return [(text, style, bullet and (lists.setdefault(bullet[0], len(lists)),
                                      bullet[1]))
            for text, style, bullet in paragraphs if text not in edited]


@pytest.mark.parametrize("layout", [
    "heading-table", "item-table", "table-heading", "table-item",
    "heading-table-end", "empty-heading-table",
])
def test_rewrite_keeps_styles_at_table_boundaries(route, layout):
    """F1: exporter-shaped tables directly beside styled paragraphs survive."""
    bullet = _bullet()
    blocks = {
        "heading-table": [("p", "Heading", "HEADING_2"), ("t", TABLE),
                          ("p", "after")],
        "item-table": [("p", "item", "NORMAL_TEXT", bullet), ("t", TABLE),
                       ("p", "after")],
        "table-heading": [("p", "before"), ("t", TABLE),
                          ("p", "Following", "HEADING_2"), ("p", "after")],
        "table-item": [("p", "before"), ("t", TABLE),
                       ("p", "item", "NORMAL_TEXT", bullet), ("p", "after")],
        "heading-table-end": [("p", "after"), ("p", "Heading", "HEADING_2"),
                              ("t", TABLE)],
        "empty-heading-table": [("p", "after"), ("p", "", "HEADING_2"),
                                ("t", TABLE), ("p", "tail")],
    }[layout]
    doc = route.load(NativeDoc(*blocks))
    before = styles(doc)
    markdown = route.ok("cat")
    edited = markdown.replace("after", "after edited")
    assert edited != markdown
    route.ok("write", text=edited)
    expected = [(text.replace("after", "after edited"), style, bool(bullet))
                for text, style, bullet in before]
    assert [(t, s, bool(b)) for t, s, b in styles(doc)] == expected
    assert route.ok("cat") == edited


@pytest.mark.parametrize("last", ["heading", "item", "numbered"])
def test_appending_a_leading_table_keeps_the_last_paragraph(route, last):
    """F2: the tab's last paragraph keeps its style when a table is appended."""
    final = {
        "heading": [("p", "Heading", "HEADING_2")],
        "item": [("p", "item", "NORMAL_TEXT", _bullet())],
        "numbered": [("p", "one", "NORMAL_TEXT", _bullet(1, NUMBERED)),
                     ("p", "two", "NORMAL_TEXT", _bullet(1, NUMBERED))],
    }[last]
    doc = route.load(NativeDoc(("p", "intro"), *final))
    before = styles(doc)
    route.ok("cat", tab="Main")
    route.ok("insert", text="| a |\n|---|\n| c |\n\nafter", tab="Main",
             position="end")
    after = styles(doc)
    # Exactly the table's two cells, the Markdown's one blank line and "after"
    # are added: no scaffolding paragraph survives.
    assert after == before + [("a", None, None), ("c", None, None),
                              ("", "NORMAL_TEXT", None),
                              ("after", "NORMAL_TEXT", None)]


@pytest.mark.parametrize("markdown", [
    "## Heading\n| h |\n| --- |\n| v |\nafter\n",
    "```\ncode\n```\n| h |\n| --- |\n| v |\nafter\n",
    "> quoted\n| h |\n| --- |\n| v |\nafter\n",
    "- item\n| h |\n| --- |\n| v |\n- next\n",
    "before\n| h |\n| --- |\n| v |\n## Heading\n",
    "## Heading\n| h |\n| --- |\n| v |\n",
    "- item\n| h |\n| --- |\n| v |\n## Heading\n",
    # Adjacent tables read back with the blank paragraph Docs keeps between them.
    "**bold end**\n| h |\n| --- |\n| v |\n\n\n| k |\n| --- |\n| w |\n## After\n",
    "## Heading\n\n| h |\n| --- |\n| v |\n\nafter\n",
])
def test_written_table_layouts_read_back_unchanged(route, markdown):
    """F1: a table directly beside a heading, code, quote or list reads back."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    first = route.ok("cat")
    # A tab ending in a table keeps Docs' final paragraph, read as one blank
    # line; otherwise the Markdown reads back byte for byte.
    ends_in_table = markdown.rstrip("\n").endswith("|")
    assert first == markdown + ("\n" if ends_in_table else "")
    structure = styles(doc)
    top_level = [u for u in doc.units[1:] if u.kind == "tstart" or u.ch == "\n"]
    blank_lines = markdown.count("\n\n") + ends_in_table
    assert sum(1 for s in structure if s[0] == "" and s[1]) == blank_lines, (
        structure)
    assert top_level[-1].ch == "\n"
    # Changed round trips neither grow nor lose paragraphs.
    for turn in range(2):
        changed = route.ok("cat").replace("| v |", f"| v{turn} |")
        route.ok("write", text=changed)
        assert route.ok("cat") == changed
        assert _shape(styles(doc), {"v0", "v1"}) == _shape(structure, {"v"})
    # gdoc's code and container ranges end at their block, never over the table.
    table = next(i for i, u in enumerate(doc.units) if u.kind == "tstart")
    assert all(not start < table < end for name, start, end in doc.named if name)


@pytest.mark.parametrize("layout", ["final", "before-table", "heading-over-item"])
def test_removing_a_boundary_paragraph_keeps_the_one_before(route, layout):
    """P-1: the paragraph whose mark is borrowed keeps its own style."""
    blocks, expected = {
        "final": ([("p", "Body"), ("p", "Obsolete", "HEADING_2")],
                  [("Body", "NORMAL_TEXT", None)]),
        "before-table": (
            [("p", "Intro"), ("p", "Obsolete", "HEADING_2"), ("t", TABLE),
             ("p", "after")],
            [("Intro", "NORMAL_TEXT", None), ("h", None, None), ("v", None, None),
             ("after", "NORMAL_TEXT", None)]),
        "heading-over-item": (
            [("p", "Title", "HEADING_1"), ("p", "Obsolete", "NORMAL_TEXT", _bullet())],
            [("Title", "HEADING_1", None)]),
    }[layout]
    doc = route.load(NativeDoc(*blocks))
    route.ok("cat")
    route.ok("edit", old_text="Obsolete", new_text="")
    assert styles(doc) == expected


def test_removing_a_paragraph_after_a_list_item_keeps_the_item(route):
    """P-1: Docs keeps the item's list on the borrowed-mark merge (observed)."""
    doc = route.load(NativeDoc(("p", "item", "NORMAL_TEXT", _bullet()),
                               ("p", "Obsolete", "HEADING_2")))
    before = styles(doc)
    route.ok("cat")
    route.ok("edit", old_text="Obsolete", new_text="")
    assert styles(doc) == before[:1]


def test_removing_a_paragraph_after_an_empty_list_item_is_refused(route):
    """An empty item is removed whole by the merge; its list cannot return."""
    doc = route.load(NativeDoc(("p", "item", "NORMAL_TEXT", _bullet()),
                               ("p", "", "NORMAL_TEXT", _bullet()),
                               ("p", "Obsolete", "HEADING_2")))
    before = styles(doc)
    route.ok("cat")
    code, output, error = route.call("edit", old_text="Obsolete", new_text="")
    assert code != 0 and "list item before it" in output + error
    assert route.service.batches == []
    assert styles(doc) == before


@pytest.mark.parametrize("markdown", [
    "- \tlit\n\n---\n",
    "1. \tone\n  1. two\n\n```\nc\n```\n\n---\n",
])
def test_literal_list_tabs_survive_a_trailing_rule(route, markdown):
    """F5: a trailing rule keeps the shielded list text, tabs and all."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    first = route.ok("cat")
    assert first == markdown
    route.ok("write", text=first.replace("lit", "lit2").replace("two", "two2"))
    assert route.ok("cat") == first.replace("lit", "lit2").replace("two", "two2")


DIFFERENT_LISTS = "- a\n| h |\n| --- |\n| v |\n1. b\n"


@pytest.mark.parametrize("markdown", [
    "before\n| h |\n| --- |\n| v |\n- item\n- next\n",
    "## Heading\n| h |\n| --- |\n| v |\n1. one\n2. two\n",
    "> quoted\n| h |\n| --- |\n| v |\n- item\n",
    "- a\n| h |\n| --- |\n| v |\n- b\n",
    "```\ncode\n```\n| h |\n| --- |\n| v |\n- item\n",
    "| h |\n| --- |\n| v |\n- item\n\n  | t |\n  | --- |\n  | u |\n",
    "---\n| h |\n| --- |\n| v |\n1. one\n",
    DIFFERENT_LISTS,
])
def test_list_item_after_a_table_keeps_its_list(route, markdown):
    """F1: a list item directly after a table never merges through its mark.

    DIFFERENT_LISTS relies on the one observed merge shape (see
    tests/native_model.py): the fill stage removes each emptied paragraph
    after the table with its own single-paragraph deletion.
    """
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    # A tab starting with a table or rule reads with Docs' leading paragraph
    # or an empty metadata block, and a trailing table with its final one.
    first = parse_frontmatter(route.ok("cat"))[1]
    assert first.strip("\n") == markdown.strip("\n")
    shape = _shape(styles(doc), set())
    changed = first.replace("| v |", "| w |")
    route.ok("write", text=changed)
    assert parse_frontmatter(route.ok("cat"))[1] == changed
    assert _shape(styles(doc), {"w"}) == [p for p in shape if p[0] != "v"]
    assert all(not start < next(i for i, u in enumerate(doc.units)
                                if u.kind == "tstart") < end
               for name, start, end in doc.named if name)


@pytest.mark.parametrize("between", [
    "| x |\n| --- |\n| y |\n", "![](https://example.test/i.png)\n",
])
def test_numbering_continues_across_a_table_or_image(route, between):
    """F6: a numbered list interrupted by a table or image keeps counting."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    markdown = f"1. a\n2. b\n\n{between}\n3. c\n"
    code, output, error = route.call("write", text=markdown)
    assert code == 0, output + error
    assert "start at 1" not in error
    lists = {b for _, _, b in styles(doc) if b}
    assert len({list_id for list_id, _ in lists}) == 1
    assert route.ok("cat").endswith("\n3. c\n")


def test_an_arbitrary_start_after_a_table_still_warns(route):
    """F6: only the documented numbering-start gap remains, and it warns."""
    route.load(NativeDoc())
    route.ok("cat")
    code, output, error = route.call(
        "write", text="Intro\n\n| x |\n| --- |\n| y |\n\n5. five\n6. six\n")
    assert code == 0, output + error
    assert "cannot set arbitrary native list starts" in output + error


@pytest.mark.parametrize("position", ["start", "end"])
@pytest.mark.parametrize("existing", [
    "> quoted\n", "---\n", "**bold**\n", "[link](https://example.test/)\n",
    "`code`\n", "## \n",
])
def test_inserted_markdown_does_not_inherit_its_neighbor(
    route, existing, position, ):
    """F7, F17, F18: inserted text is only what its Markdown says."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=existing)
    route.ok("cat", tab="Main")
    route.ok("insert", text="plain *em*\n\n> q", tab="Main", position=position)
    added = "plain *em*\n\n> q\n"
    expected = added + existing if position == "start" else existing + added
    if position == "end" and existing == "## \n":
        expected = "## \n" + added  # The empty heading stays its own paragraph.
    # A tab starting with a rule reads after an empty metadata block.
    assert parse_frontmatter(route.ok("cat"))[1] == expected
    assert len(doc.paragraphs()) == expected.count("\n")


@pytest.mark.parametrize("old,new,expected", [
    ("report", "reporter", "L [Annual reporter](https://example.test/r) right\n"),
    ("report", "report", "L [Annual report](https://example.test/r) right\n"),
    ("report right", "report left", "L [Annual report](https://example.test/r) left\n"),
    ("Annual report", "summary", "L summary right\n"),
])
def test_editing_link_labels_through_the_interfaces(route, old, new, expected):
    """F8: label edits keep the link; unrelated replacements do not get it."""
    route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text="L [Annual report](https://example.test/r) right\n")
    route.ok("edit", old_text=old, new_text=new)
    assert route.ok("cat") == expected


@pytest.mark.parametrize("markdown", [
    "> | a |\n> | --- |\n> | x |\n\n> | b |\n> | --- |\n> | y |\n",
    "| a |\n| --- |\n| x |\n> \n| b |\n| --- |\n| y |\n",
])
def test_tables_around_a_blank_line_are_stable_across_round_trips(route, markdown):
    """F12: after the first read, rewrites neither add nor remove paragraphs."""
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    first, shape = route.ok("cat"), styles(doc)
    for turn in range(3):
        changed = first.replace("| x |", f"| x{turn} |")
        route.ok("write", text=changed)
        assert route.ok("cat") == changed
        assert len(styles(doc)) == len(shape)
