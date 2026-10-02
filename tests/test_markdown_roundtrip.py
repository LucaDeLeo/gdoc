"""Synthetic native-to-Markdown-to-native regressions; no service calls."""

from itertools import product

import pytest

from gdoc.api.docs import get_tab_text
from gdoc.mdparse import parse_inline, parse_markdown, to_docs_requests


def _paragraph(runs, named_style="NORMAL_TEXT", bullet=None):
    paragraph = {
        "paragraphStyle": {"namedStyleType": named_style},
        "elements": [
            {"textRun": {"content": text, "textStyle": style}}
            for text, style in runs
        ] + [{"textRun": {"content": "\n"}}],
    }
    if bullet is not None:
        paragraph["bullet"] = bullet
    return {"paragraph": paragraph}


def _assert_roundtrip(paragraphs):
    tab = {"body": {"content": paragraphs}}
    exported = get_tab_text(tab, markdown=True)
    parsed = parse_markdown(exported)
    expected_text = ""
    expected_runs = []
    expected_paragraphs = []
    for element in paragraphs:
        paragraph = element["paragraph"]
        start = len(expected_text)
        for element in paragraph["elements"]:
            run = element["textRun"]
            text = run["content"]
            expected_text += text
            expected_runs.extend([run.get("textStyle", {})] * len(text))
        expected_paragraphs.append((
            start, len(expected_text), paragraph["paragraphStyle"],
        ))
    actual_runs = [{} for _ in parsed.plain_text]
    for style in parsed.styles:
        if style.type == "text_style":
            for offset in range(style.start, style.end):
                actual_runs[offset].update(style.style)
    assert parsed.plain_text == expected_text, exported
    assert actual_runs == expected_runs, exported
    assert [
        (s.start, s.end, s.style) for s in parsed.styles
        if s.type == "paragraph_style"
    ] == expected_paragraphs, exported
    assert not parsed.tables
    assert not [s for s in parsed.styles if s.type == "bullets"]
    return exported, parsed


# Every pair of supported emphasis/link states, including identical styles
# split into separate native runs and opposite transitions (italic→bold etc.).
_RUN_STYLES = [
    {
        **{key: True for key, on in zip(("bold", "italic", "strikethrough"), bits)
           if on},
        **({"link": {"url": "https://example.org/a_(b)"}} if link else {}),
    }
    for *bits, link in product((False, True), repeat=4)
]


@pytest.mark.parametrize("left,right", product(_RUN_STYLES, repeat=2))
def test_touching_run_styles(left, right):
    _assert_roundtrip([_paragraph([
        ("Status: ", {}), ("Draft", left), ("FINAL", right), (" today.", {}),
    ])])


@pytest.mark.parametrize("literal", [
    "---", "> 40% agreed.", "# Not a heading", "###### Not a heading",
    "- Not a bullet", "* Not a bullet", "1. Not numbered", "27. Not numbered",
    "  - Indented marker", "\t1. Indented marker", "   > Indented quote",
    "***", "___", "- - -", "```", "```python", "~~~", "~~~text",
    "[label](https://example.org/a_(b))", "*stars* and _underscores_",
    "case_01_alpha_v2", "Join run_id and doc_id", "3 * 4 * 5",
    "https://example.org/case_01_alpha_v2", r"\*literal\* and C:\notes",
    "<!-- -->", "<!-- gdoc:TITLE --> Literal marker", "| Header |",
    "|---|", "", "😀 before [brackets] and ~~tildes~~",
])
def test_literal_text_stays_normal_paragraph(literal):
    _assert_roundtrip([
        _paragraph([(literal, {})]),
        _paragraph([("|---|", {})]),
        _paragraph([("Following paragraph.", {})]),
    ])


@pytest.mark.parametrize("named_style", [
    "NORMAL_TEXT", "TITLE", "SUBTITLE", *[f"HEADING_{n}" for n in range(1, 7)],
])
def test_named_styles_and_linked_emphasis(named_style):
    exported, parsed = _assert_roundtrip([
        _paragraph([
            ("Read ", {}),
            ("VAULT [guide]", {
                "bold": True, "italic": True, "strikethrough": True,
                "link": {"url": "https://example.org/(guide)/unbalanced)"},
            }),
            (" then confirm ", {}), ("SIGIL", {"bold": True}),
        ], named_style),
        _paragraph([("Closing line.", {})]),
    ])
    requests = to_docs_requests(parsed, 1, "selected-tab")
    paragraph_requests = [r["updateParagraphStyle"] for r in requests
                          if "updateParagraphStyle" in r]
    assert paragraph_requests[0]["paragraphStyle"]["namedStyleType"] == named_style
    assert all(r["range"]["tabId"] == "selected-tab" for r in paragraph_requests)
    if named_style in ("TITLE", "SUBTITLE"):
        assert exported.startswith(f"<!-- gdoc:{named_style} --> ")


@pytest.mark.parametrize("named_style", ["TITLE", "SUBTITLE"])
def test_empty_title_and_subtitle(named_style):
    _assert_roundtrip([_paragraph([], named_style)])


@pytest.mark.parametrize("text", [
    "case_01_alpha_v2", "Join the run_id and doc_id columns",
    "Throughput was 3 * 4 * 5", "https://example.org/case_01_alpha_v2",
    "* leading space*", "*trailing space *", "_ leading space_",
    "_trailing space _", "snake_case_word", "a_b_c",
])
def test_handwritten_literal_emphasis(text):
    assert parse_inline(text) == (text, [])


@pytest.mark.parametrize("text,plain,key", [
    ("*italic*", "italic", "italic"),
    ("_italic_", "italic", "italic"),
    ("a*italic*b", "aitalicb", "italic"),
    ("**bold**", "bold", "bold"),
    ("**bold _italic_**", "bold italic", "bold"),
])
def test_handwritten_emphasis_remains_supported(text, plain, key):
    actual, styles = parse_inline(text)
    assert actual == plain
    assert any(s.style.get(key) for s in styles)


@pytest.mark.parametrize("text", ["one", "one\n", "one\n\n", "\n", "\n\n"])
def test_terminal_newline_closes_last_paragraph(text):
    parsed = parse_markdown(text)
    expected = text if text.endswith("\n") else text + "\n"
    assert parsed.plain_text == expected
    assert len([s for s in parsed.styles if s.type == "paragraph_style"]) == len(
        expected.splitlines(),
    )


def test_real_lists_are_not_escaped():
    paragraphs = [
        _paragraph([("First", {"bold": True})], bullet={"listId": "bullets"}),
        _paragraph([("Nested", {"italic": True})],
                   bullet={"listId": "bullets", "nestingLevel": 1}),
    ]
    exported = get_tab_text({"body": {"content": paragraphs}}, markdown=True)
    assert exported == "- **First**\n  - *Nested*\n"
    parsed = parse_markdown(exported)
    assert parsed.plain_text == "First\n\tNested\n"
    assert len([s for s in parsed.styles if s.type == "bullets"]) == 2


@pytest.mark.parametrize("text", ["<!-- -->", "before <!-- --> after"])
def test_run_separator_inside_code_is_literal(text):
    plain, styles = parse_inline(f"`{text}`")
    assert plain == text
    assert len(styles) == 1
    assert styles[0].style == {"weightedFontFamily": {"fontFamily": "Courier New"}}


def test_run_separator_inside_link_destination_is_literal():
    destination = "https://example.org/<!-- -->"
    plain, styles = parse_inline(f"[label]({destination})")
    assert plain == "label"
    assert styles[0].style == {"link": {"url": destination}}



def test_separator_inside_nested_code_keeps_outer_emphasis():
    plain, styles = parse_inline("**before `<!-- -->` after**")
    assert plain == "before <!-- --> after"
    assert [(s.start, s.end, s.style) for s in styles] == [
        (7, 15, {"weightedFontFamily": {"fontFamily": "Courier New"}}),
        (0, len(plain), {"bold": True}),
    ]


@pytest.mark.parametrize("whitespace", ["\t", "\u00a0", "\u2003"])
@pytest.mark.parametrize("side", ["leading", "trailing", "both"])
@pytest.mark.parametrize("middle", [False, True])
def test_italic_boundary_whitespace_keeps_text_and_core_style(
    whitespace, side, middle,
):
    lead = whitespace if side in ("leading", "both") else ""
    trail = whitespace if side in ("trailing", "both") else ""
    runs = [(lead + "Draft" + trail, {"italic": True})]
    if middle:
        runs = [("Before", {}), *runs, ("After", {})]
    exported = get_tab_text({"body": {"content": [_paragraph(runs)]}}, markdown=True)
    parsed = parse_markdown(exported)
    assert parsed.plain_text == "".join(text for text, _ in runs) + "\n"
    start = len("Before" if middle else "") + len(lead)
    assert [(s.start, s.end, s.style) for s in parsed.styles
            if s.type == "text_style"] == [(start, start + 5, {"italic": True})]


def _table(rows):
    return {"table": {"tableRows": [
        {"tableCells": [{"content": [_paragraph([(text, {})])]}
                        for text in row]}
        for row in rows
    ]}}


@pytest.mark.parametrize("rows", [
    [["Name", "Value"], ["Alpha", "Beta"], ["Gamma", "Delta"]],
    [["Only header"]],
    [["", ""], ["", ""]],
    [["a|b", r"c\|d"], ["end\\", "|"], ["*literal*", "<br>"]],
    [["Heading", "Other"], ["first\nsecond", "line\\\nbreak"]],
    [["H"], ["foo"], ["---"], ["bar"]],
    [["---", ":---:"], ["x", "-- -"], ["- - -", ":--:"]],
])
def test_native_rectangular_table_roundtrip(rows):
    table = _table(rows)
    tab = {"body": {"content": [
        _paragraph([("Before", {})]), table, _paragraph([("After", {})]),
    ]}}
    exported = get_tab_text(tab, markdown=True)
    parsed = parse_markdown(exported)
    assert len(parsed.tables) == 1
    result = parsed.tables[0]
    assert (result.num_rows, result.num_cols) == (len(rows), len(rows[0]))
    assert [[parse_inline(cell)[0] for cell in row] for row in result.rows] == rows
    assert parsed.plain_text == "Before\n\nAfter\n"
    assert result.plain_text_offset == len("Before\n")
    assert exported.count("\n") == len(rows) + 3
    assert get_tab_text({"body": {"content": [table]}}) == "".join(
        "\t".join(row) + "\n" for row in rows
    )


def test_table_inline_styles_and_adjacent_tables_roundtrip():
    table = _table([["Header"], ["unused"]])
    table["table"]["tableRows"][1]["tableCells"][0]["content"] = [
        _paragraph([("a|b", {"bold": True, "link": {"url": "https://e.org/a|b"}})]),
        _paragraph([("next", {"italic": True})]),
    ]
    parsed = parse_markdown(get_tab_text(
        {"body": {"content": [table, _table([["Next"]])]}}, markdown=True,
    ))
    assert len(parsed.tables) == 2
    plain, styles = parse_inline(parsed.tables[0].rows[1][0])
    assert plain == "a|b\nnext"
    assert [(s.start, s.end, s.style) for s in styles] == [
        (0, 3, {"bold": True}),
        (0, 3, {"link": {"url": "https://e.org/a|b"}}),
        (4, 8, {"italic": True}),
    ]
    assert parsed.tables[1].rows == [["Next"]]


@pytest.mark.parametrize("kind", ["ragged", "nested", "merged"])
def test_irregular_tables_keep_text_fallback(kind):
    table = _table([["Header", "Other"], ["Value", "Tail"]])
    rows = table["table"]["tableRows"]
    if kind == "ragged":
        rows[1]["tableCells"].pop()
    elif kind == "nested":
        rows[1]["tableCells"][0]["content"].append(_table([["Nested"]]))
    else:
        rows[0]["tableCells"][0]["tableCellStyle"] = {"columnSpan": 2}
    tab = {"body": {"content": [table]}}
    exported = get_tab_text(tab, markdown=True)
    assert exported == get_tab_text(tab)
    tail = "\n" if kind == "ragged" else "\tTail\n"
    assert exported == "Header\tOther\nValue" + tail
    assert not parse_markdown(exported).tables


@pytest.mark.parametrize("cell", [" lead", "trail ", " ", "\t", "a\n ", " \nb"])
def test_boundary_whitespace_cells_roundtrip(cell):
    table = _table([["Header", "Other"], [cell, "Tail"]])
    tab = {"body": {"content": [table]}}
    exported = get_tab_text(tab, markdown=True)
    parsed = parse_markdown(exported)
    assert parse_inline(parsed.tables[0].rows[1][0])[0] == cell
