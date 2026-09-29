"""Round-6 task routes through the CLI and MCP over the native document model.

Each test drives the real handlers, rereads through the same interface and
checks the native paragraphs the requests produced.
"""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles


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


def _native_shape(doc):
    """Paragraph styles, list shape (lists numbered by first appearance) and
    gdoc range names: the structure a wording edit must leave unchanged."""
    lists = {}
    paragraphs = [(style, bullet and (lists.setdefault(bullet[0], len(lists)),
                                      bullet[1]))
                  for _, style, bullet in styles(doc)]
    return paragraphs, sorted(name for name, *_ in doc.named if name)


def _rewrite(route, doc, text, old, new):
    """Write a real wording change; it must send a batch and read back exactly
    while the native structure stays the same."""
    changed = text.replace(old, new)
    assert changed != text, (old, text)
    shape = _native_shape(doc)
    batches = len(route.service.batches)
    route.ok("write", text=changed)
    assert len(route.service.batches) > batches
    assert _read(route) == changed
    assert _native_shape(doc) == shape
    return changed


FENCE = "before\n\n```\nfoo = 1\nbar = 2\n```\n\nafter\n"


@pytest.mark.parametrize("old,new", [
    ("foo = 1", "total = a*b + c*d"),
    ("1", "a*b*c"),
    ("foo = 1", "see [x](y)"),
    ("foo = 1", "**kw** = `y`"),
    ("foo = 1", "# not a heading"),
])
def test_code_line_replacement_is_literal(route, old, new):
    """R5-1: wording typed into a code line stays literal code."""
    _written(route, FENCE)
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route) == FENCE.replace(old, new, 1)


@pytest.mark.parametrize("new,expected", [
    ("a*b*c", "Call `a*b*c` now\n"),
    ("`g_y`", "Call `g_y` now\n"),
    ("[x](y)", "Call `[x](y)` now\n"),
    ("`😀_y`", "Call `😀_y` now\n"),
    ("😀*y*", "Call `😀*y*` now\n"),
])
def test_inline_code_replacement_is_literal(route, new, expected):
    """R5-1: inside inline code, only a whole code span is Markdown."""
    _written(route, "Call `fn_x` now\n")
    route.ok("edit", old_text="fn_x", new_text=new)
    assert _read(route) == expected


def test_prose_replacement_is_still_markdown(route):
    """A match that covers prose keeps inline Markdown formatting."""
    _written(route, "Call `fn_x` now\n")
    route.ok("edit", old_text="now", new_text="*soon*")
    assert _read(route) == "Call `fn_x` *soon*\n"


@pytest.mark.parametrize("markdown", [
    "1. a\n\n   ```\n   \tx\n   ```\n2. b\n",
    "- a\n\n  ```\n  \tt\n  \t\tu\n  ```\n- b\n  - c\n",
    "1. a\n\n   ```\n   \tx\n   ```\n2. b\n  1. c\n\n"
    "      | h |\n      | --- |\n      | v |\n",
])
def test_list_contained_code_keeps_its_tabs(route, markdown):
    """R5-3: bullet requests spanning code never consume its leading tabs."""
    doc = _written(route, markdown)
    first = _read(route)
    assert first.strip("\n") == markdown.strip("\n")
    _rewrite(route, doc, first, "a\n", "a2\n")


def _model_after_delete(start, end):
    doc = NativeDoc(("p", "a", "HEADING_2"), ("p", ""), ("p", ""),
                    ("p", "b", "NORMAL_TEXT", {"preset": "NUMBERED", "list": 1,
                                               "nest": 0}))
    doc.op_delete_content_range({"range": {"startIndex": start, "endIndex": end}})
    return styles(doc)


def test_merge_model_matches_the_observed_rule():
    """Live probes J1-J10, P1a-P1o: the paragraph where a deletion starts
    keeps its style and list, unless the deletion starts at its first index."""
    # Units: 1 'a', 2 LF(H2), 3 LF, 4 LF, 5 'b', 6 LF(list).
    assert _model_after_delete(3, 4)[-1] == ("b", "NORMAL_TEXT", (1, 0))
    # Starting at an empty paragraph removes it whole: 'b' keeps its own.
    assert _model_after_delete(3, 5)[-1] == ("b", "NORMAL_TEXT", (1, 0))
    # Starting inside 'a' (at its newline) keeps 'a's heading, no list.
    assert _model_after_delete(2, 5)[-1] == ("ab", "HEADING_2", None)


@pytest.mark.parametrize("markdown", [
    "```\nx c\n```\n\n| h |\n| --- |\n| x v |\n- x item\n",
    "- x a\n\n  ```\n  x c\n  ```\n\n  | h |\n  | --- |\n  | x v |\n- x b\n",
    "> x q\n> \n> | h |\n> | --- |\n> | x v |\n- x item\n",
    "x p\n\n| h |\n| --- |\n| x v |\n1. x one\n2. x two\n",
])
def test_blank_line_between_a_container_and_a_table_stays_outside(
        route, markdown):
    """R5-2: the blank paragraph before a table never joins a code range."""
    doc = _written(route, markdown)
    assert _read(route) == markdown
    text = _rewrite(route, doc, markdown, "x", "y")
    _rewrite(route, doc, text, "y", "z")


TABLE_MD = "| a |\n| --- |\n| b |\n"


@pytest.mark.parametrize("existing,inserted,expected", [
    ("## H\n", TABLE_MD, "## H\n" + TABLE_MD + "\n"),
    ("- item\n", TABLE_MD, "- item\n" + TABLE_MD + "\n"),
    ("> q\n", TABLE_MD, "> q\n" + TABLE_MD + "\n"),
    ("1. x\n", "para\n\n", "1. x\npara\n\n"),
    ("- b2\n", "# top\n\n", "- b2\n# top\n\n"),
    ("1. x\n", "- y\n\n", "1. x\n- y\n\n"),
    ("## H\n", TABLE_MD + "after\n", "## H\n" + TABLE_MD + "after\n"),
])
def test_appending_leaves_no_stray_styled_paragraph(route, existing, inserted,
                                                    expected):
    """R5-8: the retained final mark takes the Markdown's style, not the old."""
    _written(route, existing)
    route.ok("cat", tab="Main")
    route.ok("insert", text=inserted, tab="Main", position="end")
    assert _read(route) == expected


@pytest.mark.parametrize("markdown,old,expected", [
    ("first\n## H\nx\n", "first", "## H\nx\n"),
    ("x\n## H\n- a\n", "H", "x\n- a\n"),
    ("x\n## H\n1. a\n2. b\n", "H", "x\n1. a\n2. b\n"),
    ("- a\n- gone\n1. b\n", "gone", "- a\n1. b\n"),
])
def test_removing_a_middle_paragraph_keeps_its_successor(route, markdown, old,
                                                         expected):
    """R5-10: a middle paragraph is emptied, then its empty paragraph removed."""
    _written(route, markdown)
    route.ok("edit", old_text=old, new_text="")
    assert _read(route) == expected


@pytest.mark.parametrize("markdown,old,expected", [
    ("Hello\n## world\ntail\n", "lo\nwor", "Helld\ntail\n"),
    ("## Hello\nworld\ntail\n", "lo\nwor", "## Helld\ntail\n"),
    ("Hello\nmid\n## world\n", "lo\nmid\nwor", "Helld\n"),
])
def test_empty_replacement_across_paragraphs_joins_them(route, markdown, old,
                                                        expected):
    """R5-6: the joined paragraph keeps the first paragraph's style."""
    _written(route, markdown)
    route.ok("edit", old_text=old, new_text="")
    assert _read(route) == expected


def test_join_keeps_the_list_item(route):
    """R5-6: Docs keeps the first paragraph's list on a join (observed)."""
    _written(route, "- a b\nc d\n")
    route.ok("edit", old_text="b\nc", new_text="")
    assert _read(route) == "- a  d\n"


def _run(text, start, **extra):
    end = start + len(text.encode("utf-16-le")) // 2
    return {"startIndex": start, "endIndex": end,
            "textRun": {"content": text, "textStyle": {}, **extra}}


def _suggested_tab():
    """'The cat ran' with 'cat' suggested for deletion and 'dog' inserted,
    a suggested paragraph break, and a wholly suggested paragraph."""
    runs = [_run("The ", 1), _run("cat", 5, suggestedDeletionIds=["s1"]),
            _run("dog", 8, suggestedInsertionIds=["s1"]), _run(" ran", 11),
            _run("\n", 15)]
    split = [_run("one", 16), _run("\n", 19, suggestedInsertionIds=["s2"])]
    joined = [_run("two\n", 20)]
    added = [_run("new\n", 24, suggestedInsertionIds=["s3"])]
    content = [{"startIndex": 1, "endIndex": 16, "paragraph": {"elements": runs}},
               {"startIndex": 16, "endIndex": 20, "paragraph": {"elements": split}},
               {"startIndex": 20, "endIndex": 24, "paragraph": {"elements": joined}},
               {"startIndex": 24, "endIndex": 28, "paragraph": {"elements": added},
                "suggestedInsertionIds": ["s3"]}]
    return {"documentId": "synthetic", "revisionId": "r1", "tabs": [{
        "tabProperties": {"tabId": "t.0", "title": "Main", "index": 0},
        "documentTab": {"body": {"content": [
            {"startIndex": 0, "endIndex": 1, "sectionBreak": {}}, *content]}},
    }]}


def test_reads_show_text_without_pending_suggestions(route, monkeypatch):
    """R5-5: suggested insertions never read as ordinary text."""
    from gdoc.api import docs

    route.load(NativeDoc())
    monkeypatch.setattr(docs, "get_document_with_tabs",
                        lambda *a, **k: _suggested_tab())
    code, output, error = route.call("cat")
    assert code == 0
    assert parse_frontmatter(output)[1] == "The cat ran\nonetwo\n"
    assert "3 pending suggestions" in output + error
    code, output, _ = route.call("cat", json=True)
    assert '"pending_suggestions": 3' in output
    assert "dog" not in output and "new" not in output


def test_unchanged_write_after_a_suggestion_read_sends_nothing(route, monkeypatch):
    """R5-5: writing the read back keeps the suggestions pending."""
    from gdoc.api import docs

    route.load(NativeDoc())
    monkeypatch.setattr(docs, "get_document_with_tabs",
                        lambda *a, **k: _suggested_tab())
    read = route.ok("cat")
    code, output, error = route.call("write", text=read)
    assert code == 0, output + error
    assert route.service.batches == []
    code, output, error = route.call("write", text=read.replace("ran", "sat"))
    assert code != 0 and "pending suggestions" in output + error
    assert route.service.batches == []


DOCS_LINK = {"link": {"url": "https://example.test/"}, "underline": True,
             "foregroundColor": {"color": {"rgbColor": {
                 "red": 0.06666667, "green": 0.33333334, "blue": 0.8}}}}


@pytest.mark.parametrize("old,new,expected", [
    ("here", "there", "click there now\n"),
    ("click here now", "click here here now", "click here here now\n"),
    ("k he", "k the", "click the[re](https://example.test/) now\n"),
    ("here", "[here](https://new.test/)", "click [here](https://new.test/) now\n"),
])
def test_unlinked_wording_loses_the_link_appearance(route, old, new, expected):
    """R5-7: wording that loses its link also loses Docs' link blue/underline."""
    doc = route.load(NativeDoc(("p", "click here now"), ("p", "x")))
    for unit in doc.units[7:11]:
        unit.ts.update(DOCS_LINK)
    route.ok("cat")
    route.ok("edit", old_text=old, new_text=new)
    assert _read(route) == expected + "x\n"
    plain = [u for u in doc.units if u.kind == "text" and "link" not in u.ts]
    assert not any(u.ts.get("underline") or "foregroundColor" in u.ts
                   for u in plain)


@pytest.mark.parametrize("markdown,edits", [
    ("1. item\n\n   > quoted\n2. next\n", [("quoted", "quoted two")]),
    ("- item\n\n  > quoted\n- next\n", [("quoted", "said"), ("next", "then")]),
    ("- item\n  > quoted\n", [("quoted", "cited")]),
    ("- a\n  - b\n\n    > deep\n", [("deep", "deeper")]),
    ("1. a\n  1. b\n\n     > q\n2. c\n", [("q\n", "quote\n"), ("c\n", "see\n")]),
    ("- a\n\n  > one\n  > \n  > two\n- b\n", [("two", "three")]),
    ("- a\n\n  > > deep\n- b\n", [("deep", "deeper")]),
    ("1. a\n\n   > 1. x\n   > 2. y\n2. b\n", [("y\n", "why\n"), ("b\n", "be\n")]),
    ("- a\n\n  > 1. x\n  > 2. y\n- b\n", [("x\n", "ex\n")]),
    ("1. a\n\n   > ```\n   > code\n   > ```\n2. b\n", [("code", "code two")]),
    ("1. a\n\n   > | h |\n   > | --- |\n   > | v |\n2. b\n",
     [("| v |", "| w |"), ("b\n", "be\n")]),
])
def test_quote_inside_a_list_item_stays_in_the_item(route, markdown, edits):
    """R5-4: a quote nested in a list item keeps the item's indent and list."""
    doc = _written(route, markdown)
    assert _read(route) == markdown
    text = markdown
    for old, new in edits:
        text = _rewrite(route, doc, text, old, new)


def test_quote_in_item_numbering_is_one_list(route):
    """The item's list continues around the quote; the quoted list is its own."""
    doc = _written(route, "1. a\n\n   > 1. x\n   > 2. y\n2. b\n")
    lists = {text: bullet[0] for text, _, bullet in styles(doc) if bullet}
    assert lists["a"] == lists["b"] != lists["x"] == lists["y"]


@pytest.mark.parametrize("separator", ["\x0b", " ", "\x0c"])
def test_a_soft_break_before_a_tab_header_can_be_written_back(route, separator):
    """R5-11: only a line of its own is an --all-tabs tab header."""
    doc = route.load(NativeDoc(("p", f"intro{separator}=== Tab: Notes ===")))
    read = route.ok("cat")
    batches = len(route.service.batches)
    route.ok("write", text=read.replace("intro", "intro2"))
    assert len(route.service.batches) > batches
    assert styles(doc)[0][0] == f"intro2{separator}=== Tab: Notes ==="


@pytest.mark.parametrize("markdown,expected", [
    ("---\n```\n```\n", "---\n```\n\n```\n"),
    ("> ---\n```\n", "> ---\n```\n\n```\n"),
    ("a\n---\n```\n```\n", "a\n---\n```\n\n```\n"),
])
def test_a_rule_before_a_final_empty_code_block_is_written(route, markdown,
                                                           expected):
    """R5-12: the empty code line owns the final mark; the rule keeps its own."""
    doc = _written(route, markdown)
    assert _read(route) == expected
    _rewrite(route, doc, expected, "```\n\n```", "```\nfilled\n```")


@pytest.mark.parametrize("markdown,old,new", [
    ("- \t[x](http://a.example/)\n", "[x]", "[xy]"),
    ("- \t~~`b`~~\n", "`b`", "`bc`"),
    ("1. \t**bold** after\n", "after", "later"),
    ("- a\n\n  ```\n  \tcode\n  ```\n", "code", "code two"),
])
def test_restored_content_tabs_keep_their_own_style(route, markdown, old, new):
    """R5-13: a literal leading tab never joins the link or code after it."""
    def assert_tabs_plain():
        tabs = [u for u in doc.units if u.ch == "\t"]
        assert tabs and all("link" not in u.ts and "bold" not in u.ts
                            and "strikethrough" not in u.ts for u in tabs)

    doc = _written(route, markdown)
    assert _read(route) == markdown
    assert_tabs_plain()
    _rewrite(route, doc, markdown, old, new)
    assert_tabs_plain()


@pytest.mark.parametrize("markdown,expected,old,new", [
    ("> > - a\n> > \n> >   | t |\n> >   | --- |\n> >   | v |\n",
     "> > - a\n> > \n> >   | t |\n> >   | --- |\n> >   | v |\n\n",
     "| v |", "| w |"),
    ("see [x][r]\n\n[r]: https://u.example/\n", "see [x](https://u.example/)\n",
     "see", "saw"),
])
def test_container_blank_and_trailing_definition_round_trip(route, markdown,
                                                            expected, old, new):
    """R5-14: a contained blank keeps its prefix; a definition adds no blank."""
    doc = _written(route, markdown)
    assert _read(route) == expected
    _rewrite(route, doc, expected, old, new)


@pytest.mark.parametrize("markdown,old,new", [
    ("[**b** *i*](https://u.example/)\n", "*i*", "*it*"),
    ("see [a **b** c](https://u.example/) now\n", "see", "saw"),
    ("[x](https://one.example/)[y](https://two.example/)\n", "[y]", "[yz]"),
])
def test_a_link_with_mixed_styles_stays_one_link(route, markdown, old, new):
    """R5-14: the spaces between differently styled words stay linked."""
    from gdoc.mdparse import parse_markdown

    def linked(doc):
        return [(u.ch, u.ts["link"]["url"]) for u in doc.units if "link" in u.ts]

    def requested(text):
        parsed = parse_markdown(text)
        return [(ch, s.style["link"]["url"]) for s in parsed.styles
                if "link" in s.style for ch in parsed.plain_text[s.start:s.end]]

    doc = _written(route, markdown)
    assert _read(route) == markdown
    assert linked(doc) == requested(markdown)
    changed = _rewrite(route, doc, markdown, old, new)
    assert linked(doc) == requested(changed)


def _cell(text, start, **extra):
    return {"startIndex": start, "endIndex": start + len(text) + 1,
            "content": [{"startIndex": start, "endIndex": start + len(text) + 1,
                         "paragraph": {"elements": [_run(text + "\n", start)]}}],
            **extra}


def _suggested_tables():
    """A kept table with a suggested row and column, then a suggested table."""
    kept = {"startIndex": 1, "endIndex": 40, "table": {
        "rows": 3, "columns": 2, "tableRows": [
            {"tableCells": [_cell("H", 3),
                            _cell("New", 5, suggestedInsertionIds=["col"])]},
            {"tableCells": [_cell("V", 10),
                            _cell("n", 12, suggestedInsertionIds=["col"])]},
            {"suggestedInsertionIds": ["row"],
             "tableCells": [_cell("R", 16),
                            _cell("r", 18, suggestedInsertionIds=["row"])]}]}}
    added = {"startIndex": 41, "endIndex": 50, "table": {
        "suggestedInsertionIds": ["tbl"], "tableRows": [
            {"tableCells": [_cell("S", 43)]}, {"tableCells": [_cell("T", 46)]}]}}
    tab = _suggested_tab()
    body = tab["tabs"][0]["documentTab"]["body"]["content"]
    body[1:1] = [kept, added]
    return tab


def test_suggested_tables_rows_and_columns_are_not_read(route, monkeypatch):
    """R5-5: structural suggestions stay out of the Markdown, like text ones."""
    from gdoc.api import docs

    route.load(NativeDoc())
    monkeypatch.setattr(docs, "get_document_with_tabs",
                        lambda *a, **k: _suggested_tables())
    read = parse_frontmatter(route.ok("cat"))[1]
    assert read.startswith("| H |\n| --- |\n| V |\n")
    for suggested in ("New", "| R", "| S", "| T", "dog"):
        assert suggested not in read


def _tab_of(content, named=None):
    """A one-tab document in the inline suggestion view."""
    body = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}, *content]
    # As in API responses, each inline image has an inlineObjects entry.
    objects = {element["inlineObjectElement"]["inlineObjectId"]
               for block in content for element in block.get("paragraph", {})
               .get("elements", []) if "inlineObjectElement" in element}
    tab = {"body": {"content": body}}
    if objects:
        tab["inlineObjects"] = {object_id: {"inlineObjectProperties": {
            "embeddedObject": {"imageProperties": {
                "contentUri": f"https://example.invalid/{object_id}.png"}}}}
            for object_id in objects}
    if named:
        tab["namedRanges"] = named
    return {"documentId": "synthetic", "revisionId": "r1", "tabs": [{
        "tabProperties": {"tabId": "t.0", "title": "Main", "index": 0},
        "documentTab": tab}]}


def _para(runs, start, **paragraph):
    end = runs[-1]["endIndex"]
    return {"startIndex": start, "endIndex": end,
            "paragraph": {"elements": runs, **paragraph}}


def _image(object_id, start, **extra):
    return {"startIndex": start, "endIndex": start + 1,
            "inlineObjectElement": {"inlineObjectId": object_id, **extra}}


class _Recorder:
    """Accepts pinned batches without applying them, recording requests."""

    def __init__(self):
        self.revision, self.batches = 1, []

    def documents(self):
        return self

    def batchUpdate(self, documentId, body):  # noqa: N802, N803 (Docs API names)
        recorder = self

        class Request:
            def execute(self, **_):
                recorder.batches.append(body["requests"])
                recorder.revision += 1
                return {"replies": [{} for _ in body["requests"]],
                        "writeControl": {
                            "requiredRevisionId": f"r{recorder.revision}"}}
        return Request()


def _serve(route, monkeypatch, document):
    from gdoc.api import docs

    route.load(NativeDoc())
    route.service = _Recorder()
    monkeypatch.setattr(docs, "get_document_with_tabs", lambda *a, **k: document)


IMAGES = [_para([_run("A ", 1), _image("kept", 3, suggestedDeletionIds=["d"]),
                 _image("new", 4, suggestedInsertionIds=["i"]), _run(" z\n", 5)], 1)]
SAME_FORM_BREAK = [
    _para([_run("one", 1), _run("\n", 4, suggestedInsertionIds=["b"])], 1,
          paragraphStyle={"namedStyleType": "HEADING_2"}),
    _para([_run("two\n", 5)], 5, paragraphStyle={"namedStyleType": "HEADING_2"})]
STYLE_BREAK = [
    _para([_run("one", 1), _run("\n", 4, suggestedInsertionIds=["b"])], 1,
          paragraphStyle={"namedStyleType": "HEADING_2"}),
    _para([_run("two\n", 5)], 5, paragraphStyle={"namedStyleType": "NORMAL_TEXT"})]
LIST_BREAK = [
    _para([_run("one", 1), _run("\n", 4, suggestedInsertionIds=["b"])], 1,
          bullet={"listId": "L"}),
    _para([_run("two\n", 5)], 5)]
CODE_BREAK = [
    _para([_run("code", 1), _run("\n", 5, suggestedInsertionIds=["b"])], 1),
    _para([_run("prose\n", 6)], 6)]
CODE_RANGE = {"gdoc:code:v1": {"name": "gdoc:code:v1", "namedRanges": [
    {"name": "gdoc:code:v1", "ranges": [{"startIndex": 1, "endIndex": 6}]}]}}


@pytest.mark.parametrize("content,named,expected,faithful", [
    (IMAGES, None, "A ![](gdoc-image:kept) z\n", True),
    (SAME_FORM_BREAK, None, "## onetwo\n", True),
    (STYLE_BREAK, None, None, False),
    (LIST_BREAK, None, None, False),
    (CODE_BREAK, CODE_RANGE, None, False),
])
def test_suggestion_preview_is_complete_only_when_faithful(
        route, monkeypatch, content, named, expected, faithful):
    """R5-5: an image, row, cell or break suggestion reads as before it; a
    break joining paragraphs of different forms is an incomplete read."""
    _serve(route, monkeypatch, _tab_of(content, named))
    code, output, error = route.call("cat", json=True)
    assert code == 0
    assert ('"complete": true' in output) == faithful
    code, output, error = route.call("cat")
    if expected is not None:
        assert parse_frontmatter(output)[1] == expected
    assert ("cannot reliably render" in output + error) == (not faithful)
    assert "new" not in parse_frontmatter(output)[1]
    # An incomplete read cannot authorize a rewrite, even with consent.
    code, out, err = route.call("write", text="changed\n", allow_lossy=True)
    assert (code == 0) == faithful, out + err
    if not faithful:
        assert route.service.batches == []


def test_consented_rewrite_discards_suggestions_and_keeps_the_text(
        route, monkeypatch):
    """R5-5: --allow-lossy keeps the pre-suggestion text; nothing suggested
    is applied as a direct edit."""
    _serve(route, monkeypatch, _suggested_tab())
    read = route.ok("cat")
    code, output, error = route.call("write", text=read.replace("ran", "sat"))
    assert code != 0 and "pending suggestions" in output + error
    assert route.service.batches == []
    route.ok("write", text=read.replace("ran", "sat"), allow_lossy=True)
    sent = [r for batch in route.service.batches for r in batch]
    inserted = "".join(r["insertText"]["text"] for r in sent if "insertText" in r)
    assert inserted.startswith("The cat sat\nonetwo")
    assert "dog" not in inserted and "new" not in inserted
