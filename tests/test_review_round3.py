"""Regressions for the round-3 review of the Markdown contract release."""

import pytest

from gdoc.api.docs import get_tab_text
from gdoc.mdparse import parse_inline, parse_markdown
from tests.test_markdown_roundtrip import _paragraph

CODE = {"weightedFontFamily": {"fontFamily": "Courier New"}}


def _cell_table(runs_by_cell):
    return {"table": {"tableRows": [
        {"tableCells": [{"content": [_paragraph([("H", {})])]}
                        for _ in runs_by_cell]},
        {"tableCells": [{"content": [_paragraph(runs)]} for runs in runs_by_cell]},
    ]}}


@pytest.mark.parametrize("runs", [
    [("a\\|b", CODE)],
    [("a\\\\|b", CODE)],
    [("x", {}), ("|", CODE)],
    [("C:\\", CODE)],
    [("grep 'a|b' \\| wc", CODE), (" and a\\|b", {})],
])
def test_table_cell_code_with_backslash_pipes_round_trips(runs):
    exported = get_tab_text({"body": {"content": [
        _cell_table([runs, [("z", {})]])]}}, markdown=True)
    table = parse_markdown(exported).tables[0]
    assert len(table.rows[1]) == 2, exported
    text, styles = parse_inline(table.rows[1][0])
    assert text == "".join(t for t, _ in runs)
    code = [(s.start, s.end) for s in styles if "weightedFontFamily" in s.style]
    starts = [0]
    for t, _ in runs:
        starts.append(starts[-1] + len(t))
    assert code == [(starts[i], starts[i + 1]) for i, (_, style) in enumerate(runs)
                    if style]
    assert parse_inline(table.rows[1][1])[0] == "z"


def test_linked_image_in_a_table_cell_keeps_its_link():
    from gdoc.api.docs import _table_cell_requests

    table = parse_markdown(
        "| Pic |\n| --- |\n"
        "| [![](https://example.invalid/i.png)](https://example.invalid/t) |\n"
    ).tables[0]
    requests = _table_cell_requests([[5], [9]], table, "tab-one")
    image = next(i for i, r in enumerate(requests) if "insertInlineImage" in r)
    placed = requests[image]["insertInlineImage"]["location"]["index"]
    assert requests[image + 1] == {"updateTextStyle": {
        "range": {"startIndex": placed, "endIndex": placed + 1, "tabId": "tab-one"},
        "textStyle": {"link": {"url": "https://example.invalid/t"}},
        "fields": "link",
    }}


@pytest.mark.parametrize("markdown, depths", [
    ("- a\n  - b\n    - c\n", [0, 1, 2]),
    ("10. a\n  - b\n", [0, 1]),
    ("1. a\n   - b\n", [0, 1]),
])
def test_documented_list_nesting_spelling(markdown, depths):
    parsed = parse_markdown(markdown)
    assert [s.list_depth for s in parsed.styles if s.type == "bullets"] == depths


def test_documented_emphasis_and_line_break_spellings_round_trip():
    parsed = parse_markdown("**bold _italic_** a\x0bb\n")
    assert parsed.plain_text == "bold italic a\x0bb\n"
    styles = {(s.start, s.end): s.style for s in parsed.styles
              if s.type == "text_style"}
    assert styles == {(0, 11): {"bold": True}, (5, 11): {"italic": True}}
    exported = get_tab_text({"body": {"content": [_paragraph([
        ("bold ", {"bold": True}), ("italic", {"bold": True, "italic": True}),
        (" a\x0bb", {}),
    ])]}}, markdown=True)
    again = parse_markdown(exported)
    assert again.plain_text == parsed.plain_text
    # Export keeps the boundary space outside the delimiters.
    assert {(s.start, s.end) for s in again.styles if s.type == "text_style"} == {
        (0, 4), (5, 11)}


def test_paragraph_before_a_table_is_removed_with_the_preceding_mark(mocker):
    from gdoc.api.docs import find_text_in_document, replace_formatted
    from gdoc.util import GdocError
    from tests.test_paragraph_edits import _body, _requests

    body = _body(("Intro", "NORMAL_TEXT", False), ("Obsolete", "HEADING_2", False))
    body["content"].append({"startIndex": 16, "endIndex": 30, "table": {}})
    requests = _requests(mocker, body, "Obsolete", "")
    # Docs refuses to delete the mark before a table; the preceding one goes,
    # and Docs keeps Intro's style on the merge (observed live).
    assert requests == [{"deleteContentRange": {"range": {
        "startIndex": 6, "endIndex": 15, "tabId": "synthetic-tab"}}}]

    first = _body(("Obsolete", "HEADING_2", False))
    first["content"].append({"startIndex": 10, "endIndex": 20, "table": {}})
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    with pytest.raises(GdocError, match="directly before a table") as error:
        replace_formatted("synthetic-doc",
                          find_text_in_document(None, "Obsolete", body=first), "",
                          "synthetic-rev", tab_id="synthetic-tab", body=first)
    assert error.value.exit_code == 3
    service.assert_not_called()


@pytest.mark.parametrize("line, style", [
    ("##", "HEADING_2"), ("## ", "HEADING_2"), ("######", "HEADING_6"),
    ("<!-- gdoc:TITLE -->", "TITLE"), ("<!-- gdoc:SUBTITLE --> ", "SUBTITLE"),
    ("- ##", "HEADING_2"),
])
def test_empty_heading_markers_survive_trimmed_whitespace(line, style):
    parsed = parse_markdown(line + "\nbody\n")
    assert parsed.plain_text == "\nbody\n"
    assert [s.style["namedStyleType"] for s in parsed.styles
            if s.type == "paragraph_style"] == [style, "NORMAL_TEXT"]


@pytest.mark.parametrize("text", ["#", "##", "#######", "#tag", "<!-- gdoc:TITLE -->"])
def test_literal_heading_markers_stay_text(text):
    exported = get_tab_text({"body": {"content": [_paragraph([(text, {})])]}},
                            markdown=True)
    assert parse_markdown(exported).plain_text == text + "\n"


@pytest.mark.parametrize("between", [["   "], ["\t"], [" ", ""], ["", " "]])
def test_whitespace_paragraphs_between_tables_keep_their_text(between):
    from tests.test_markdown_roundtrip import _table

    content = [_table([["h"], ["c"]])]
    content += [_paragraph([(text, {})] if text else []) for text in between]
    content += [_table([["h2"], ["d"]])]
    exported = get_tab_text({"body": {"content": content}}, markdown=True)
    parsed = parse_markdown(exported)
    assert [t.plain_text_offset for t in parsed.tables] == [0, parsed.tables[1]
                                                           .plain_text_offset]
    assert parsed.plain_text == "\n" + "".join(t + "\n" for t in between) + "\n"


def test_documented_leading_rules_spelling_survives_metadata_parsing():
    from gdoc.frontmatter import parse_frontmatter

    _, body = parse_frontmatter("***\n***\nText\n")
    parsed = parse_markdown(body)
    assert parsed.plain_text == "\n\nText\n"
    assert sum("borderBottom" in s.style for s in parsed.styles
               if s.type == "paragraph_style") == 2


def test_bracketed_link_destination_is_unwrapped_like_images():
    _, styles = parse_inline("[x](<https://example.invalid/a_(b)>)")
    assert styles[0].style == {"link": {"url": "https://example.invalid/a_(b)"}}


@pytest.mark.parametrize("style, label", [
    ({"shading": {"backgroundColor": {"color": {"rgbColor": {"red": 1}}}}},
     "paragraph shading"),
    ({"borderLeft": {"width": {"magnitude": 1, "unit": "PT"}}}, "paragraph borders"),
    ({"direction": "RIGHT_TO_LEFT"}, "right-to-left paragraph direction"),
])
def test_rich_paragraph_styles_warn_before_replacement(capsys, style, label):
    from gdoc.lossy import check_markdown_replacement

    paragraph = _paragraph([("Text", {})])
    paragraph["paragraph"]["paragraphStyle"].update(style)
    check_markdown_replacement({"content": [paragraph]}, tab_body=True)
    assert label in capsys.readouterr().err


def test_default_paragraph_direction_and_empty_shading_do_not_warn(capsys):
    from gdoc.lossy import check_markdown_replacement

    paragraph = _paragraph([("Text", {})])
    paragraph["paragraph"]["paragraphStyle"].update(
        direction="LEFT_TO_RIGHT", shading={"backgroundColor": {}},
        borderTop={"width": {"unit": "PT"}},
    )
    check_markdown_replacement({"content": [paragraph]}, tab_body=True)
    assert capsys.readouterr().err == ""
