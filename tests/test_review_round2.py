"""Regressions for the round-2 review of the Markdown contract release."""

import pytest

from gdoc.api.docs import get_tab_text
from gdoc.mdparse import parse_inline, parse_markdown
from tests.test_markdown_roundtrip import _paragraph, _table


def _cells(table):
    return [[parse_inline(cell)[0] for cell in row] for row in table.rows]


@pytest.mark.parametrize("row", [["-", "-"], [":-", "-:"], ["--", ":--:"]])
def test_dash_only_data_row_stays_in_its_table(row):
    rows = [["Name", "Score"], ["Ann", "5"], row, ["Bo", "7"]]
    exported = get_tab_text({"body": {"content": [_table(rows)]}}, markdown=True)
    parsed = parse_markdown(exported)
    assert [_cells(t) for t in parsed.tables] == [rows]
    # Handwritten GFM with such a row is one table too.
    handwritten = parse_markdown(
        "| Name | Score |\n| --- | --- |\n| Ann | 5 |\n| "
        + " | ".join(row) + " |\n"
    )
    assert [_cells(t) for t in handwritten.tables] == [rows[:3]]


@pytest.mark.parametrize("between, blanks", [([], 0), ([None], 1), ([None, None], 2)])
def test_adjacent_tables_use_one_separating_blank_line(between, blanks):
    content = [_table([["A"], ["1"]])]
    content += [_paragraph([]) for _ in between]
    content += [_table([["B"], ["2"]])]
    exported = get_tab_text({"body": {"content": content}}, markdown=True)
    assert exported.count("|\n\n") == 1
    parsed = parse_markdown(exported)
    assert [_cells(t) for t in parsed.tables] == [[["A"], ["1"]], [["B"], ["2"]]]
    # Each table is one paragraph slot; the remaining lines are blank paragraphs.
    assert parsed.plain_text == "\n" * (2 + blanks)


def test_adjacent_quoted_tables_separate_inside_the_quote():
    parsed = parse_markdown("> | A |\n> | - |\n>\n> | B |\n> | - |\n")
    assert [t.path for t in parsed.tables] == [("q",), ("q",)]
    assert parsed.plain_text == "\n\n"


@pytest.mark.parametrize("body", [
    "---\nStatus: draft\n---\nBody\n",
    "---\ngdoc: literal\ntitle: x\n---\n",
    "---\n---\nTwo rules\n",
    "---\n",
    "Intro\n---\n",
])
def test_protected_bodies_survive_metadata_parsing(body):
    from gdoc.frontmatter import add_frontmatter, parse_frontmatter, protect_body

    assert parse_frontmatter(protect_body(body)) == ({}, body)
    # A pull file's own metadata block is removed exactly once.
    pulled = add_frontmatter(body, {"gdoc": "DOC", "title": "T"})
    assert parse_frontmatter(pulled) == ({"gdoc": "DOC", "title": "T"}, body)


@pytest.mark.parametrize("markdown, expected", [
    ("- a\n\n  1. x\n\n  2. y", [1, 2]),
    ("- a\n  1. x\n\n  2. y\n\n  3. z", [1, 2, 3]),
    ("- a\n  1. x\n  2. y", [1, 2]),
    ("- a\n  1. x\n     - deep\n  2. y", [1, 2]),
    ("- a\n\n  1. x\n\n     - deep\n\n  2. y", [1, 2]),
    ("- a\n  1. x\n- b\n  1. y", [1, 1]),
    ("- a\n\n  1. x\n\n- b\n\n  1. y\n\n  2. z", [1, 1, 2]),
    ("* a\n\n  1. x\n\n     1. d1\n\n     2. d2\n\n  2. y", [1, 1, 2, 2]),
    ("1. a\n\n   - b\n\n     1. x\n\n     2. y", [1, 1, 2]),
])
def test_loose_numbered_lists_nested_in_mixed_lists_keep_numbering(markdown, expected):
    from gdoc.api.docs import _native_docs_requests, _strip_trailing_newline_unless_hr
    from gdoc.mdparse import to_docs_requests
    from tests.test_list_request_semantics import apply_list_requests

    for builder in (to_docs_requests, _native_docs_requests):
        parsed = parse_markdown(markdown)
        _strip_trailing_newline_unless_hr(parsed)
        counters, numbers = {}, []
        for paragraph in apply_list_requests(builder(parsed, 1, "t")):
            if paragraph["list"] is None:
                continue
            key = paragraph["list"], paragraph["depth"]
            counters[key] = counters.get(key, 0) + 1
            if paragraph["preset"].startswith("NUMBERED"):
                numbers.append(counters[key])
        assert numbers == expected, builder
        assert not parsed.non_default_list_starts


def _indented(text, named_style="NORMAL_TEXT"):
    element = _paragraph([(text, {})], named_style)
    element["paragraph"]["paragraphStyle"].update({
        "indentStart": {"magnitude": 36, "unit": "PT"},
        "indentFirstLine": {"magnitude": 36, "unit": "PT"},
    })
    return element


@pytest.mark.parametrize("text", ["- not a list", "1. not numbered", "# not heading",
                                  "> x", "| a | b |", "+ plus"])
def test_indented_literal_text_stays_literal_inside_its_quote(text):
    exported = get_tab_text({"body": {"content": [_indented(text)]}}, markdown=True)
    parsed = parse_markdown(exported)
    assert parsed.plain_text == text + "\n"
    assert not [s for s in parsed.styles if s.type == "bullets"]
    assert not parsed.tables
    assert [s.style for s in parsed.styles if s.type == "markdown_prefix"] == [
        {"quote": 1, "indent": 0}]
    assert all(s.style.get("namedStyleType", "NORMAL_TEXT") == "NORMAL_TEXT"
               for s in parsed.styles if s.type == "paragraph_style")


@pytest.mark.parametrize("style, expected", [
    ("HEADING_2", "> ## Section\n"),
    ("TITLE", "> <!-- gdoc:TITLE --> Section\n"),
])
def test_indented_heading_keeps_its_style_inside_its_quote(style, expected):
    exported = get_tab_text({"body": {"content": [_indented("Section", style)]}},
                            markdown=True)
    assert exported == expected
    parsed = parse_markdown(exported)
    assert parsed.plain_text == "Section\n"
    assert any(s.style.get("namedStyleType") == style for s in parsed.styles)


def test_numbered_item_containing_a_rule_stays_one_item():
    from gdoc.api.docs import _native_docs_requests, _strip_trailing_newline_unless_hr
    from tests.test_list_request_semantics import apply_list_requests, labels

    bullet = {"listId": "L", "nestingLevel": 0}
    first = _paragraph([("a", {})], bullet=bullet)
    first["paragraph"]["elements"][1:1] = [{"horizontalRule": {}},
                                           {"textRun": {"content": "b"}}]
    lists = {"L": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}}}
    exported = get_tab_text({"body": {"content": [
        first, _paragraph([("c", {})], bullet=bullet)]}, "lists": lists},
        markdown=True)
    assert exported == "1. a\n---\nb\n2. c\n"
    parsed = parse_markdown(exported)
    _strip_trailing_newline_unless_hr(parsed)
    assert labels(apply_list_requests(_native_docs_requests(parsed, 1, "t"))) == [1, 2]
    assert not parsed.non_default_list_starts


@pytest.mark.parametrize("definition_indent, paragraph_indent, marker", [
    (54, None, "1. item"),     # Custom list definition, inherited indent.
    (None, 54, "1. item"),     # Custom paragraph indent on a first-level item.
    (None, 72, "  1. item"),   # gdoc's own nested child list at one level deep.
])
def test_custom_first_level_indent_is_not_nesting(definition_indent, paragraph_indent,
                                                  marker):
    level = {"glyphType": "DECIMAL"}
    if definition_indent:
        level["indentStart"] = {"magnitude": definition_indent, "unit": "PT"}
    lists = {"L": {"listProperties": {"nestingLevels": [level] + [
        {"glyphType": "ALPHA"}] * 8}}}
    item = _paragraph([("item", {})], bullet={"listId": "L", "nestingLevel": 0})
    if paragraph_indent:
        item["paragraph"]["paragraphStyle"]["indentStart"] = {
            "magnitude": paragraph_indent, "unit": "PT"}
    exported = get_tab_text({"body": {"content": [item]}, "lists": lists},
                            markdown=True)
    assert exported == marker + "\n"


@pytest.mark.parametrize("data_alignment, lossy", [
    ("CENTER", False), (None, True), ("END", True)])
def test_cell_alignment_unlike_its_header_needs_loss_consent(data_alignment, lossy):
    from gdoc.lossy import check_markdown_replacement
    from gdoc.util import GdocError

    table = _table([["H"], ["v"]])
    for row, alignment in ((0, "CENTER"), (1, data_alignment)):
        style = table["table"]["tableRows"][row]["tableCells"][0]["content"][0][
            "paragraph"]["paragraphStyle"]
        if alignment:
            style["alignment"] = alignment
    scope = {"body": {"content": [table]}}
    if lossy:
        with pytest.raises(GdocError, match="alignment differs from its column"):
            check_markdown_replacement(scope, tab_body=True)
        check_markdown_replacement(scope, tab_body=True, allow_lossy=True)
    else:
        check_markdown_replacement(scope, tab_body=True)


@pytest.mark.parametrize("markdown, found", [
    ("Prose a\\*bc here\n", True),
    ("Code `a\\*bc` here\n", False),
    ("```\na\\*bc\n```\n", False),
    ("> ```\n> a\\*bc\n> ```\n", False),
    ("Code `` a\\*bc ` `` then a\\*bc\n", True),
])
def test_comment_anchors_decode_prose_escapes_but_not_code(markdown, found):
    from gdoc.annotate import annotate_markdown

    comment = {"id": "c1", "content": "check", "quotedFileContent": {"value": "a*bc"}}
    result = annotate_markdown(markdown, [comment])
    assert ("quoted text not found" not in result) is found


def test_unchanged_publication_keeps_the_inode_and_makes_no_copy(tmp_path):
    from gdoc.frontmatter import preserve_and_replace

    path = tmp_path / "document.md"
    path.write_text("same")
    inode = path.stat().st_ino
    assert preserve_and_replace(str(path), "same", expected="same")
    assert path.stat().st_ino == inode
    assert not list(tmp_path.glob("*.gdoc-backup-*"))
    # The conflict check still runs first.
    assert not preserve_and_replace(str(path), "same", expected="other")


def test_publication_without_hard_links_leaves_the_original(tmp_path, monkeypatch):
    import os

    from gdoc.frontmatter import preserve_and_replace

    path = tmp_path / "document.md"
    path.write_text("original")

    def no_links(*args):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    with pytest.raises(OSError, match="does not support hard links"):
        preserve_and_replace(str(path), "published", expected="original")
    assert path.read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["document.md"]


def test_publication_through_a_symlink_keeps_the_link(tmp_path):
    from gdoc.frontmatter import preserve_and_replace

    real = tmp_path / "real.md"
    real.write_text("original")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    assert preserve_and_replace(str(link), "published", expected="original")
    assert link.is_symlink() and real.read_text() == "published"
    [backup] = tmp_path.glob("real.md.gdoc-backup-*")
    assert backup.read_text() == "original"


def test_new_file_gets_ordinary_permissions(tmp_path):
    import os

    from gdoc.frontmatter import preserve_and_replace

    umask = os.umask(0o022)
    try:
        path = tmp_path / "new.md"
        assert preserve_and_replace(str(path), "content")
        assert path.stat().st_mode & 0o777 == 0o644
    finally:
        os.umask(umask)
