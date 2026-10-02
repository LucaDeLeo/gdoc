"""Offline regressions for the independent PR72 review findings."""

import io
import json
from types import SimpleNamespace

import pytest
from test_list_request_semantics import apply_list_requests, labels

from gdoc.api.docs import (
    _code_range_requests,
    _native_docs_requests,
    _style_run_markdown,
    get_tab_text,
)
from gdoc.frontmatter import (
    body_fingerprint,
    parse_frontmatter,
    preserve_and_replace,
    update_frontmatter_value,
)
from gdoc.mdparse import parse_inline, parse_markdown, to_docs_requests, utf16_len


@pytest.mark.parametrize("builder", [to_docs_requests, _native_docs_requests])
@pytest.mark.parametrize("child", ["1.", "-"])
def test_loose_nested_lists_keep_parent_numbering(builder, child):
    parsed = parse_markdown(f"1. a\n\n   {child} b\n\n2. c\n\n3. d")
    paragraphs = apply_list_requests(builder(parsed, 1, "t"))
    assert labels(paragraphs) == [1, 1, 2, 3]
    assert not parsed.non_default_list_starts


@pytest.mark.parametrize("reference", ["[the report][1]", "[1][]", "[1]"])
def test_reference_links_keep_url_and_visible_label(reference):
    parsed = parse_markdown(f"See {reference}.\n\n[1]: https://example.org/report")
    expected = "the report" if "the report" in reference else "1"
    # The blank line only separated the trailing definition from the text.
    assert parsed.plain_text == f"See {expected}.\n"
    assert any(
        s.style == {"link": {"url": "https://example.org/report"}}
        and parsed.plain_text[s.start : s.end] == expected
        for s in parsed.styles
    )


def test_reference_syntax_keeps_literal_notes_and_code_and_image_references():
    source = (
        "[Note]: important\n`[1]` and ![map][1]\n"
        "[inline](https://example.org/[1])\n\n[1]: https://example.org/map.png"
    )
    parsed = parse_markdown(source)
    assert parsed.plain_text.startswith("[Note]: important\n[1] and  \ninline\n")
    assert parsed.images[0].uri == "https://example.org/map.png"
    assert any(
        s.style.get("link", {}).get("url") == "https://example.org/[1]"
        for s in parsed.styles
    )


def test_reference_links_work_in_table_cells():
    parsed = parse_markdown("| [guide][g] |\n| -- |\n\n[g]: https://example.org/guide")
    plain, styles = parse_inline(parsed.tables[0].rows[0][0])
    assert plain == "guide"
    assert styles[0].style == {"link": {"url": "https://example.org/guide"}}


def test_short_alignment_delimiters():
    parsed = parse_markdown("| A | B | C |\n| :-- | --: | :-: |\n| x | y | z |")
    assert parsed.tables[0].alignments == ["START", "END", "CENTER"]


@pytest.mark.parametrize("indent", [3, 4, 6])
@pytest.mark.parametrize("quote", ["", "> ", "> > "])
def test_list_fences_preserve_code_and_continuation(indent, quote):
    padding = " " * indent
    source = "\n".join(
        quote + line
        for line in [
            "1. item",
            padding + "```",
            padding + "x = a*b*c",
            padding + "  indented",
            padding + "```",
            "2. next",
        ]
    )
    parsed = parse_markdown(source)
    assert parsed.plain_text == "item\nx = a*b*c\n  indented\nnext\n"
    assert len(parsed.code_blocks) == 1
    assert not any(s.style.get("italic") for s in parsed.styles)
    paragraphs = apply_list_requests(_native_docs_requests(parsed, 1, "t"))
    assert labels(paragraphs) == [1, 2]
    assert not parsed.non_default_list_starts


def native_readback(parsed, batch=None):
    """List request semantics plus final paragraph styles and named ranges.

    This bounded helper does not model Docs itself; it checks the parser/writer/
    exporter ownership agreement, using the existing list request interpreter.
    """
    from copy import deepcopy

    from gdoc.api.docs import _strip_trailing_newline_unless_hr

    parsed = deepcopy(parsed)
    _strip_trailing_newline_unless_hr(parsed)
    requests = _native_docs_requests(parsed, 1, "t") if batch is None else batch
    paragraphs = apply_list_requests(requests)
    content, cursor, lists = [], 1, {}
    for item in paragraphs:
        end = cursor + utf16_len(item["text"])
        para = {"elements": [{"textRun": {"content": item["text"]}}]}
        if item["list"] is not None:
            list_id = str(item["list"])
            para["bullet"] = {"listId": list_id, "nestingLevel": item["depth"]}
            lists[list_id] = {
                "listProperties": {
                    "nestingLevels": [
                        {"glyphType": "DECIMAL"}
                        if item["preset"].startswith("NUMBERED")
                        else {}
                        for _ in range(9)
                    ]
                }
            }
        content.append({"startIndex": cursor, "endIndex": end, "paragraph": para})
        cursor = end
    # Prefix restoration is deliberately last, after the list operations.
    for request in requests:
        if "updateParagraphStyle" in request:
            data = request["updateParagraphStyle"]
            for element in content:
                if (
                    element["startIndex"] < data["range"]["endIndex"]
                    and data["range"]["startIndex"] < element["endIndex"]
                ):
                    element["paragraph"].setdefault("paragraphStyle", {}).update(
                        data["paragraphStyle"]
                    )
    named = {}
    range_requests = (
        _code_range_requests(parsed, 1, "t")
        if batch is None
        else [r for r in batch if "createNamedRange" in r]
    )
    for request in range_requests:
        data = request["createNamedRange"]
        named.setdefault(data["name"], {"namedRanges": []})["namedRanges"].append(
            {"name": data["name"], "ranges": [data["range"]]}
        )
    return {"body": {"content": content}, "lists": lists, "namedRanges": named}


@pytest.mark.parametrize(
    "source",
    [
        "> 1. item\n> 2. next",
        "> - item\n>   - nested",
        "> ```\n> literal *code*\n> ```",
        "1. item\n   ```\n   code\n   ```\n2. next",
        "> 1. item\n>    ```\n>    code\n>    ```\n> 2. next",
    ],
)
def test_container_readback_survives_changed_roundtrip(source):
    parsed = parse_markdown(source)
    native = native_readback(parsed)
    exported = get_tab_text(native, markdown=True)
    changed = parse_markdown(exported.replace("item", "revised"))
    assert changed.plain_text.rstrip("\n") == parsed.plain_text.replace(
        "item", "revised"
    ).rstrip("\n")
    assert len(changed.code_blocks) == len(parsed.code_blocks)
    assert len([s for s in changed.styles if s.type == "bullets"]) == len(
        [s for s in parsed.styles if s.type == "bullets"]
    )
    before = [
        (s.style["quote"], s.style["indent"])
        for s in parsed.styles
        if s.type == "markdown_prefix"
    ]
    after = [
        (s.style["quote"], s.style["indent"])
        for s in changed.styles
        if s.type == "markdown_prefix"
    ]
    assert before == after


def test_hr_preserves_text_and_heading_on_both_sides():
    native = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "HEADING_2"},
                        "elements": [
                            {"textRun": {"content": "Keep this text "}},
                            {"horizontalRule": {}},
                            {"textRun": {"content": "and this\n"}},
                        ],
                    }
                }
            ]
        }
    }
    exported = get_tab_text(native, markdown=True)
    assert exported == "## Keep this text \n---\n## and this\n"
    parsed = parse_markdown(exported)
    assert parsed.plain_text == "Keep this text \n\nand this\n"
    assert sum(s.style.get("namedStyleType") == "HEADING_2" for s in parsed.styles) == 2


def test_link_url_backtick_cannot_consume_later_code():
    source = _style_run_markdown("l", {"link": {"url": "http://x/`y"}}) + " and `z`"
    plain, styles = parse_inline(source)
    assert plain == "l and z"
    assert any(s.style.get("link") == {"url": "http://x/`y"} for s in styles)
    assert any(
        s.style.get("weightedFontFamily") and plain[s.start : s.end] == "z"
        for s in styles
    )


def test_frontmatter_refresh_preserves_unparsed_bytes():
    raw = ("---\n# comment\ngdoc: doc\ncustom:\n  nested: value\n"
           "gdoc-revision: r1\n---\nBody\n")
    assert update_frontmatter_value(raw, "gdoc-revision", "r2") == raw.replace(
        "r1", "r2"
    )


@pytest.mark.parametrize("race", ["before_move", "after_move", "open_descriptor"])
def test_local_publication_keeps_concurrent_edits(tmp_path, monkeypatch, race):
    import os

    path = tmp_path / "document.md"
    path.write_text("original")
    real_rename, real_link = os.rename, os.link
    opened = path.open("r+") if race == "open_descriptor" else None

    def move(source, destination):
        if race == "before_move":
            path.write_text("concurrent")
        real_rename(source, destination)

    def link(source, destination):
        if str(source).split("/")[-1].startswith(".gdoc-publish-"):
            if race == "after_move":
                path.write_text("concurrent")
            if opened:
                opened.seek(0)
                opened.write("concurrent")
                opened.truncate()
                opened.flush()
        return real_link(source, destination)

    monkeypatch.setattr(os, "rename", move)
    monkeypatch.setattr(os, "link", link)
    try:
        preserve_and_replace(str(path), "published", expected="original")
    finally:
        if opened:
            opened.close()
    retained = [path, *tmp_path.glob("*.gdoc-backup-*")]
    assert any(p.read_text() == "concurrent" for p in retained)
    if race != "open_descriptor":
        assert path.read_text() == "concurrent"


def test_pull_hook_leaves_refused_local_edit_in_place(tmp_path, mocker, capsys):
    from gdoc.cli import cmd_pull_hook

    path = tmp_path / "document.md"
    raw = (
        "---\ngdoc: abc123\ngdoc-revision: r1\n"
        f"gdoc-body-sha256: {body_fingerprint('original')}\n---\nLOCAL EDIT"
    )
    path.write_text(raw)
    read = mocker.patch("gdoc.api.docs.get_document_with_tabs")
    mocker.patch(
        "sys.stdin", io.StringIO(json.dumps({"tool_input": {"file_path": str(path)}}))
    )
    assert cmd_pull_hook(SimpleNamespace()) == 0
    assert path.read_text() == raw
    read.assert_not_called()
    assert "local edits" in capsys.readouterr().err


def test_refresh_acknowledged_revision_preserves_frontmatter(tmp_path):
    from gdoc.cli import _refresh_file_revision

    path = tmp_path / "document.md"
    raw = "---\n# keep this\ngdoc: doc\ngdoc-revision: r1\n---\nBody"
    path.write_text(raw)
    _refresh_file_revision(str(path), raw, {"acknowledged_revision_id": "r2"})
    metadata, body = parse_frontmatter(path.read_text())
    assert "# keep this\n" in path.read_text()
    assert metadata["gdoc-revision"] == "r2"
    assert metadata["gdoc-body-sha256"] == body_fingerprint(body)


def test_frontmatter_refresh_preserves_crlf(tmp_path):
    from gdoc.cli import _refresh_file_revision
    from gdoc.frontmatter import read_local_text

    path = tmp_path / "document.md"
    raw = "---\r\n# keep\r\ngdoc: doc\r\ngdoc-revision: r1\r\n---\r\nBody\r\n"
    path.write_bytes(raw.encode())
    _refresh_file_revision(str(path), raw, {"acknowledged_revision_id": "r2"})
    refreshed = read_local_text(path)
    assert "# keep\r\n" in refreshed
    assert "gdoc-revision: r2\r\n" in refreshed
    assert parse_frontmatter(refreshed)[1] == "Body\r\n"


@pytest.mark.parametrize(
    "anchor", ["Q&A session", "snake_case names", "*literal* words"]
)
def test_comments_find_exporter_escaped_anchors(anchor):
    from gdoc.annotate import annotate_markdown

    markdown = _style_run_markdown(anchor, {}) + "\n"
    comment = {"id": "c1", "content": "check", "quotedFileContent": {"value": anchor}}
    result = annotate_markdown(markdown, [comment])
    assert "anchor deleted" not in result
    assert "UNANCHORED" not in result


@pytest.mark.parametrize("error", [OSError("connection refused")])
def test_transport_failure_before_send_is_truthful(error):
    from unittest.mock import MagicMock

    from googleapiclient.http import HttpRequest

    from gdoc.api.comment_transport import execute_mutation_request
    from gdoc.util import GdocError

    request = MagicMock(spec=HttpRequest)
    request.http = MagicMock()
    request.execute.side_effect = error
    with pytest.raises(GdocError, match="write was not sent"):
        execute_mutation_request(request)
    assert request.execute.call_count == 1


@pytest.mark.parametrize(
    "source",
    [
        "> > quote",
        "> > ```\n> > code\n> > ```",
        "> 1. item\n>    ```\n>    code\n>    ```\n> 2. next",
    ],
)
def test_owned_container_ranges_need_no_loss_override(source, capsys):
    from gdoc.lossy import check_markdown_replacement

    native = native_readback(parse_markdown(source))
    check_markdown_replacement(native, tab_body=True)
    assert not capsys.readouterr().err


def test_literal_inspection_header_has_editable_export():
    tab = {
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "=== Tab: Draft ===\n"}},
                        ]
                    }
                }
            ]
        }
    }
    exported = get_tab_text(tab, markdown=True)
    assert exported == "\\=== Tab: Draft ===\n"
    assert parse_markdown(exported).plain_text == "=== Tab: Draft ===\n"


def test_empty_final_quote_prefix_owns_retained_newline():
    from gdoc.api.docs import _strip_trailing_newline_unless_hr

    parsed = parse_markdown('> ')
    _strip_trailing_newline_unless_hr(parsed)
    ranges = _code_range_requests(parsed, 1, 't')
    assert ranges == [{'createNamedRange': {
        'name': 'gdoc:prefix:v1:1:0',
        'range': {'startIndex': 1, 'endIndex': 2, 'tabId': 't'},
    }}]



def test_uncontested_publication_retains_announced_recovery_copy(tmp_path, capsys):
    path = tmp_path / "document.md"
    path.write_text("original")
    assert preserve_and_replace(str(path), "published", expected="original")
    [backup] = tmp_path.glob("*.gdoc-backup-*")
    assert backup.read_text() == "original"
    assert path.read_text() == "published"
    assert str(backup) in capsys.readouterr().err


def test_open_descriptor_write_after_return_is_recoverable(tmp_path):
    path = tmp_path / "document.md"
    path.write_text("original")
    opened = path.open("r+")
    try:
        assert preserve_and_replace(str(path), "published", expected="original")
        # A late editor write through the descriptor opened before publication.
        opened.seek(0)
        opened.write("late concurrent edit")
        opened.truncate()
        opened.flush()
    finally:
        opened.close()
    assert path.read_text() == "published"
    recovered = [p.read_text() for p in tmp_path.glob("*.gdoc-backup-*")]
    assert recovered == ["late concurrent edit"]


@pytest.mark.parametrize(
    ("source", "path"),
    [
        ("> | A | B |\n> | :-- | --: |\n> | x | y |", ("q",)),
        ("> > | A | B |\n> > | - | - |\n> > | x | y |", ("q", "q")),
        ("1. item\n   | A | B |\n   | - | - |\n   | x | y |\n2. next", (3,)),
    ],
)
def test_contained_tables_parse_with_their_container(source, path):
    parsed = parse_markdown(source)
    [table] = parsed.tables
    assert table.path == path
    assert table.rows == [["A", "B"], ["x", "y"]]
    assert "|" not in parsed.plain_text


def test_list_numbering_continues_after_indented_table():
    parsed = parse_markdown("1. item\n   | A |\n   | - |\n   | x |\n2. next")
    paragraphs = apply_list_requests(_native_docs_requests(parsed, 1, "t"))
    assert labels(paragraphs) == [1, 2]


def test_table_container_range_is_on_first_cell_and_owned():
    from gdoc.api.docs import _table_prefix_requests
    from gdoc.lossy import check_markdown_replacement

    [table] = parse_markdown("> | A |\n> | - |\n> | x |").tables
    [request] = _table_prefix_requests([[5], [9]], table, "t")
    assert request == {"createNamedRange": {
        "name": "gdoc:prefix:v1:1:0",
        "range": {"startIndex": 5, "endIndex": 7, "tabId": "t"},
    }}
    assert _table_prefix_requests([[5]], parse_markdown("| A |\n| - |").tables[0],
                                  "t") == []
    native = {
        "body": {"content": [{"startIndex": 1, "endIndex": 12, "table": {
            "tableRows": [{"tableCells": [{"startIndex": 4, "endIndex": 7, "content": [{
                "startIndex": 5, "endIndex": 7,
                "paragraph": {"elements": [{"textRun": {"content": "A\n"}}]},
            }]}]}, {"tableCells": [{"startIndex": 8, "endIndex": 11, "content": [{
                "startIndex": 9, "endIndex": 11,
                "paragraph": {"elements": [{"textRun": {"content": "x\n"}}]},
            }]}]}],
        }}]},
        "namedRanges": {"gdoc:prefix:v1:1:0": {"namedRanges": [
            {"name": "gdoc:prefix:v1:1:0",
             "ranges": [request["createNamedRange"]["range"]]},
        ]}},
    }
    assert get_tab_text(native, markdown=True).startswith("> | A |\n> | --- |\n> | x |")
    check_markdown_replacement(native, tab_body=True)

    # A neighbouring container range that spreads over the whole table does
    # not start inside the first cell, so it does not quote the table.
    native["namedRanges"] = {"gdoc:prefix:v1:1:0": {"namedRanges": [
        {"name": "gdoc:prefix:v1:1:0",
         "ranges": [{"startIndex": 0, "endIndex": 12}]},
    ]}}
    assert get_tab_text(native, markdown=True).startswith("| A |\n| --- |\n| x |")


@pytest.mark.parametrize(
    ("cell", "length"), [("😀 **big**", 6), ("", 0), ("a😀", 3)],
)
def test_table_container_range_covers_first_cell_text_and_terminator(cell, length):
    from gdoc.api.docs import _table_cell_requests, _table_prefix_requests

    [table] = parse_markdown(f"> | {cell} | B |\n> | - | - |\n> | x | y |").tables
    [request] = _table_prefix_requests([[5, 9], [13, 17]], table, "t")
    assert request["createNamedRange"]["range"] == {
        "startIndex": 5, "endIndex": 5 + length + 1, "tabId": "t",
    }
    # Same parsed text as the cell insertion itself.
    inserted = [r["insertText"] for r in _table_cell_requests(
        [[5, 9], [13, 17]], table, "t") if "insertText" in r
        and r["insertText"]["location"]["index"] == 5]
    assert utf16_len("".join(i["text"] for i in inserted)) == length


def test_adjacent_quoted_tables_and_paragraphs_keep_separate_containers():
    parsed = parse_markdown(
        "> | A |\n> | - |\n> | 1 |\n>\n> | B |\n> | - |\n> | 2 |\n"
        "> > | C |\n> > | - |\n> > | 3 |\nplain\n| D |\n| - |\n| 4 |"
    )
    assert [t.path for t in parsed.tables] == [("q",), ("q",), ("q", "q"), ()]
    assert [t.rows for t in parsed.tables] == [
        [["A"], ["1"]], [["B"], ["2"]], [["C"], ["3"]], [["D"], ["4"]],
    ]
    assert "plain" in parsed.plain_text
