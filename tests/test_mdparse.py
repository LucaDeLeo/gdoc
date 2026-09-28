"""Tests for the markdown parser and Docs API request builder."""

import pytest

from gdoc.mdparse import parse_inline, parse_markdown, to_docs_requests


class TestParsePlainText:
    def test_empty_string(self):
        result = parse_markdown("")
        assert result.plain_text == ""
        assert result.styles == []

    def test_plain_text_no_formatting(self):
        result = parse_markdown("hello world")
        assert result.plain_text == "hello world\n"
        text_styles = [s for s in result.styles if s.type == "text_style"]
        assert text_styles == []

    def test_multiline_plain_text(self):
        result = parse_markdown("line one\nline two")
        assert result.plain_text == "line one\nline two\n"
        text_styles = [s for s in result.styles if s.type == "text_style"]
        assert text_styles == []

    def test_whitespace_only(self):
        result = parse_markdown("   ")
        assert result.plain_text == "   \n"
        text_styles = [s for s in result.styles if s.type == "text_style"]
        assert text_styles == []


class TestParseBold:
    def test_bold_asterisks(self):
        result = parse_markdown("**bold**")
        assert result.plain_text == "bold\n"
        bold_styles = [s for s in result.styles if s.style.get("bold")]
        assert len(bold_styles) == 1
        assert bold_styles[0].start == 0
        assert bold_styles[0].end == 4
        assert bold_styles[0].type == "text_style"

    def test_bold_underscores(self):
        result = parse_markdown("__bold__")
        assert result.plain_text == "bold\n"
        bold_styles = [s for s in result.styles if s.style.get("bold")]
        assert len(bold_styles) == 1

    def test_bold_in_sentence(self):
        result = parse_markdown("this is **bold** text")
        assert result.plain_text == "this is bold text\n"
        bold_styles = [s for s in result.styles if s.style.get("bold")]
        assert len(bold_styles) == 1
        assert bold_styles[0].start == 8
        assert bold_styles[0].end == 12


class TestParseItalic:
    def test_italic_asterisk(self):
        result = parse_markdown("*italic*")
        assert result.plain_text == "italic\n"
        italic_styles = [s for s in result.styles if s.style.get("italic")]
        assert len(italic_styles) == 1
        assert italic_styles[0].start == 0
        assert italic_styles[0].end == 6

    def test_italic_underscore(self):
        result = parse_markdown("_italic_")
        assert result.plain_text == "italic\n"
        italic_styles = [s for s in result.styles if s.style.get("italic")]
        assert len(italic_styles) == 1

    def test_italic_in_sentence(self):
        result = parse_markdown("this is *italic* text")
        assert result.plain_text == "this is italic text\n"
        italic_styles = [s for s in result.styles if s.style.get("italic")]
        assert len(italic_styles) == 1
        assert italic_styles[0].start == 8
        assert italic_styles[0].end == 14


class TestParseBoldItalic:
    def test_bold_italic(self):
        result = parse_markdown("***both***")
        assert result.plain_text == "both\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        italic = [s for s in result.styles if s.style.get("italic")]
        assert len(bold) == 1
        assert len(italic) == 1
        assert bold[0].start == 0
        assert bold[0].end == 4
        assert italic[0].start == 0
        assert italic[0].end == 4


class TestParseInlineCode:
    def test_inline_code(self):
        result = parse_markdown("`code`")
        assert result.plain_text == "code\n"
        code_styles = [s for s in result.styles
                       if "weightedFontFamily" in s.style]
        assert len(code_styles) == 1
        assert code_styles[0].style["weightedFontFamily"]["fontFamily"] == "Courier New"
        assert code_styles[0].start == 0
        assert code_styles[0].end == 4

    def test_inline_code_in_sentence(self):
        result = parse_markdown("use `print()` here")
        assert result.plain_text == "use print() here\n"
        code_styles = [s for s in result.styles
                       if "weightedFontFamily" in s.style]
        assert len(code_styles) == 1
        assert code_styles[0].start == 4
        assert code_styles[0].end == 11


class TestParseLink:
    def test_link(self):
        result = parse_markdown("[click](https://example.com)")
        assert result.plain_text == "click\n"
        link_styles = [s for s in result.styles if "link" in s.style]
        assert len(link_styles) == 1
        assert link_styles[0].style["link"]["url"] == "https://example.com"
        assert link_styles[0].start == 0
        assert link_styles[0].end == 5

    def test_link_in_sentence(self):
        result = parse_markdown("visit [here](https://example.com) now")
        assert result.plain_text == "visit here now\n"
        link_styles = [s for s in result.styles if "link" in s.style]
        assert len(link_styles) == 1
        assert link_styles[0].start == 6
        assert link_styles[0].end == 10


class TestParseHeadings:
    def test_heading_1(self):
        result = parse_markdown("# Title")
        assert result.plain_text == "Title\n"
        heading_styles = [s for s in result.styles if s.type == "paragraph_style"]
        assert len(heading_styles) == 1
        assert heading_styles[0].style["namedStyleType"] == "HEADING_1"

    def test_heading_2(self):
        result = parse_markdown("## Subtitle")
        assert result.plain_text == "Subtitle\n"
        heading_styles = [s for s in result.styles if s.type == "paragraph_style"]
        assert len(heading_styles) == 1
        assert heading_styles[0].style["namedStyleType"] == "HEADING_2"

    def test_heading_6(self):
        result = parse_markdown("###### Deep")
        assert result.plain_text == "Deep\n"
        heading_styles = [s for s in result.styles if s.type == "paragraph_style"]
        assert heading_styles[0].style["namedStyleType"] == "HEADING_6"

    def test_heading_with_inline_formatting(self):
        result = parse_markdown("# **Bold** title")
        assert result.plain_text == "Bold title\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        heading = [s for s in result.styles if s.type == "paragraph_style"]
        assert len(bold) == 1
        assert len(heading) == 1
        assert bold[0].start == 0
        assert bold[0].end == 4


class TestParseBulletList:
    def test_bullet_dash(self):
        result = parse_markdown("- item one\n- item two")
        assert result.plain_text == "item one\nitem two\n"
        bullets = [s for s in result.styles if s.type == "bullets"]
        assert len(bullets) == 2
        assert all(b.style["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"
                    for b in bullets)

    def test_bullet_asterisk(self):
        result = parse_markdown("* item one\n* item two")
        assert result.plain_text == "item one\nitem two\n"
        bullets = [s for s in result.styles if s.type == "bullets"]
        assert len(bullets) == 2

    def test_bullet_with_inline(self):
        result = parse_markdown("- **bold** item")
        assert result.plain_text == "bold item\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        bullets = [s for s in result.styles if s.type == "bullets"]
        assert len(bold) == 1
        assert len(bullets) == 1


class TestParseNumberedList:
    def test_numbered_list(self):
        result = parse_markdown("1. first\n2. second\n3. third")
        assert result.plain_text == "first\nsecond\nthird\n"
        numbered = [s for s in result.styles if s.type == "bullets"]
        assert len(numbered) == 3
        assert all(n.style["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"
                    for n in numbered)


class TestParseMixed:
    def test_heading_then_paragraph(self):
        result = parse_markdown("# Title\nSome text here")
        assert result.plain_text == "Title\nSome text here\n"
        heading = [s for s in result.styles
                   if s.type == "paragraph_style"
                   and s.style.get("namedStyleType", "").startswith("HEADING")]
        assert len(heading) == 1

    def test_mixed_inline(self):
        result = parse_markdown("**bold** and *italic* and `code`")
        assert result.plain_text == "bold and italic and code\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        italic = [s for s in result.styles if s.style.get("italic")]
        code = [s for s in result.styles if "weightedFontFamily" in s.style]
        assert len(bold) == 1
        assert len(italic) == 1
        assert len(code) == 1

    def test_heading_bullets_paragraph(self):
        md = "# Header\n- item 1\n- item 2\nNormal text"
        result = parse_markdown(md)
        assert "Header" in result.plain_text
        assert "item 1" in result.plain_text
        assert "Normal text" in result.plain_text
        headings = [s for s in result.styles
                    if s.type == "paragraph_style"
                    and s.style.get("namedStyleType", "").startswith("HEADING")]
        bullets = [s for s in result.styles if s.type == "bullets"]
        assert len(headings) == 1
        assert len(bullets) == 2


class TestParseTable:
    def test_simple_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = parse_markdown(md)
        assert len(result.tables) == 1
        t = result.tables[0]
        assert t.num_rows == 2
        assert t.num_cols == 2
        assert t.rows == [["A", "B"], ["1", "2"]]
        # Placeholder newline in plain text
        assert result.plain_text == "\n"

    def test_table_with_surrounding_text(self):
        md = "Before\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter"
        result = parse_markdown(md)
        assert len(result.tables) == 1
        assert "Before" in result.plain_text
        assert "After" in result.plain_text
        t = result.tables[0]
        assert t.rows == [["A", "B"], ["1", "2"]]

    def test_no_separator_not_a_table(self):
        md = "| A | B |\n| 1 | 2 |"
        result = parse_markdown(md)
        assert len(result.tables) == 0
        # Treated as normal lines
        assert "A" in result.plain_text

    def test_uneven_columns_padded(self):
        md = "| A | B | C |\n|---|---|---|\n| 1 |"
        result = parse_markdown(md)
        assert len(result.tables) == 1
        t = result.tables[0]
        assert t.num_cols == 3
        assert t.rows[1] == ["1", "", ""]

    def test_extra_columns_trimmed(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 | 3 | 4 |"
        result = parse_markdown(md)
        t = result.tables[0]
        assert t.num_cols == 2
        assert t.rows[1] == ["1", "2"]

    def test_multiple_tables(self):
        md = (
            "| A |\n|---|\n| 1 |\n"
            "Text\n"
            "| X | Y |\n|---|---|\n| 3 | 4 |"
        )
        result = parse_markdown(md)
        assert len(result.tables) == 2
        assert result.tables[0].num_cols == 1
        assert result.tables[1].num_cols == 2

    def test_table_offset_tracked(self):
        md = "Hello\n| A |\n|---|\n| 1 |"
        result = parse_markdown(md)
        t = result.tables[0]
        # "Hello\n" = 6 chars, table placeholder at offset 6
        assert t.plain_text_offset == 6

    def test_multi_row_data(self):
        md = "| H1 | H2 |\n|---|---|\n| a | b |\n| c | d |\n| e | f |"
        result = parse_markdown(md)
        t = result.tables[0]
        assert t.num_rows == 4  # header + 3 data rows
        assert t.rows[3] == ["e", "f"]


class TestToDocsRequests:
    def test_plain_text_insert(self):
        parsed = parse_markdown("hello")
        reqs = to_docs_requests(parsed, insert_index=10)
        # insertText + updateParagraphStyle (NORMAL_TEXT)
        assert len(reqs) == 2
        assert reqs[0]["insertText"]["location"]["index"] == 10
        assert reqs[0]["insertText"]["text"] == "hello\n"

    def test_bold_generates_update_text_style(self):
        parsed = parse_markdown("**bold**")
        reqs = to_docs_requests(parsed, insert_index=5)
        # insertText + updateParagraphStyle (NORMAL_TEXT) + updateTextStyle
        assert len(reqs) == 3
        insert = reqs[0]
        assert insert["insertText"]["text"] == "bold\n"
        assert insert["insertText"]["location"]["index"] == 5

        style_reqs = [r for r in reqs if "updateTextStyle" in r]
        assert len(style_reqs) == 1
        uts = style_reqs[0]["updateTextStyle"]
        assert uts["range"]["startIndex"] == 5
        assert uts["range"]["endIndex"] == 9
        assert uts["textStyle"] == {"bold": True}
        assert uts["fields"] == "bold"

    def test_heading_generates_paragraph_style(self):
        parsed = parse_markdown("# Title")
        reqs = to_docs_requests(parsed, insert_index=1)
        # insertText + updateParagraphStyle
        para_reqs = [r for r in reqs if "updateParagraphStyle" in r]
        assert len(para_reqs) == 1
        ups = para_reqs[0]["updateParagraphStyle"]
        assert ups["paragraphStyle"]["namedStyleType"] == "HEADING_1"
        assert ups["range"]["startIndex"] == 1
        assert ups["fields"] == "namedStyleType"

    def test_bullet_generates_create_paragraph_bullets(self):
        parsed = parse_markdown("- item")
        reqs = to_docs_requests(parsed, insert_index=1)
        bullet_reqs = [r for r in reqs if "createParagraphBullets" in r]
        assert len(bullet_reqs) == 1
        cpb = bullet_reqs[0]["createParagraphBullets"]
        assert cpb["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"
        # The range starts at the temporary anchor before the item.
        assert cpb["range"]["startIndex"] == 2

    def test_numbered_generates_create_paragraph_bullets(self):
        parsed = parse_markdown("1. item")
        reqs = to_docs_requests(parsed, insert_index=1)
        bullet_reqs = [r for r in reqs if "createParagraphBullets" in r]
        assert len(bullet_reqs) == 1
        cpb = bullet_reqs[0]["createParagraphBullets"]
        assert cpb["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"

    def test_link_generates_update_text_style(self):
        parsed = parse_markdown("[click](https://example.com)")
        reqs = to_docs_requests(parsed, insert_index=0)
        style_reqs = [r for r in reqs if "updateTextStyle" in r]
        assert len(style_reqs) == 1
        uts = style_reqs[0]["updateTextStyle"]
        assert uts["textStyle"]["link"]["url"] == "https://example.com"
        assert uts["fields"] == "link"

    def test_code_generates_update_text_style(self):
        parsed = parse_markdown("`code`")
        reqs = to_docs_requests(parsed, insert_index=0)
        style_reqs = [r for r in reqs if "updateTextStyle" in r]
        assert len(style_reqs) == 1
        uts = style_reqs[0]["updateTextStyle"]
        assert uts["textStyle"]["weightedFontFamily"]["fontFamily"] == "Courier New"
        assert uts["fields"] == "weightedFontFamily"

    def test_index_offset_applied(self):
        parsed = parse_markdown("**bold** text")
        reqs = to_docs_requests(parsed, insert_index=100)
        insert = reqs[0]
        assert insert["insertText"]["location"]["index"] == 100
        style = [r for r in reqs if "updateTextStyle" in r][0]["updateTextStyle"]
        assert style["range"]["startIndex"] == 100
        assert style["range"]["endIndex"] == 104

    def test_empty_parsed_returns_empty(self):
        parsed = parse_markdown("")
        reqs = to_docs_requests(parsed, insert_index=0)
        assert reqs == []

    def test_request_ordering(self):
        """Insert first, then paragraph styles, then text styles, then bullets.

        Paragraph styles come before text styles: applying a ``namedStyleType``
        re-resolves a run's direct character formatting, so bold/italic applied
        before it would be clobbered (the per-tab formatting bug). Bullets come
        last because createParagraphBullets removes the leading tabs that encode
        nesting level, shifting indices — text styles must already be attached.
        """
        parsed = parse_markdown("# **Bold** heading\n- item")
        reqs = to_docs_requests(parsed, insert_index=1)
        types = []
        for r in reqs:
            if "insertText" in r:
                types.append("insert")
            elif "updateTextStyle" in r:
                types.append("text_style")
            elif "updateParagraphStyle" in r:
                types.append("para_style")
            elif "createParagraphBullets" in r:
                types.append("bullets")
        assert types[0] == "insert"
        text_idx = types.index("text_style")
        para_idx = types.index("para_style")
        bullet_idx = types.index("bullets")
        assert para_idx < text_idx < bullet_idx


class TestParseNormalTextEmission:
    """Verify NORMAL_TEXT paragraph_style is emitted for non-heading paragraphs."""

    def test_plain_text_emits_normal(self):
        result = parse_markdown("hello")
        normal = [s for s in result.styles
                  if s.type == "paragraph_style"
                  and s.style.get("namedStyleType") == "NORMAL_TEXT"]
        assert len(normal) == 1

    def test_bullets_emit_normal(self):
        result = parse_markdown("- item 1\n- item 2")
        normal = [s for s in result.styles
                  if s.type == "paragraph_style"
                  and s.style.get("namedStyleType") == "NORMAL_TEXT"]
        assert len(normal) == 2

    def test_numbered_emit_normal(self):
        result = parse_markdown("1. first\n2. second")
        normal = [s for s in result.styles
                  if s.type == "paragraph_style"
                  and s.style.get("namedStyleType") == "NORMAL_TEXT"]
        assert len(normal) == 2

    def test_table_placeholder_emits_normal(self):
        result = parse_markdown("| A |\n|---|\n| 1 |")
        normal = [s for s in result.styles
                  if s.type == "paragraph_style"
                  and s.style.get("namedStyleType") == "NORMAL_TEXT"]
        assert len(normal) == 1

    def test_heading_does_not_emit_normal(self):
        result = parse_markdown("# Title")
        normal = [s for s in result.styles
                  if s.type == "paragraph_style"
                  and s.style.get("namedStyleType") == "NORMAL_TEXT"]
        assert len(normal) == 0

    def test_heading_only_emits_heading(self):
        result = parse_markdown("# Title")
        para = [s for s in result.styles if s.type == "paragraph_style"]
        assert len(para) == 1
        assert para[0].style["namedStyleType"] == "HEADING_1"


class TestToDocsRequestsTabId:
    """Verify tabId is injected into requests when provided."""

    def test_insert_text_has_tab_id(self):
        parsed = parse_markdown("hello")
        reqs = to_docs_requests(parsed, insert_index=1, tab_id="t1")
        insert = reqs[0]
        assert insert["insertText"]["location"]["tabId"] == "t1"

    def test_update_text_style_has_tab_id(self):
        parsed = parse_markdown("**bold**")
        reqs = to_docs_requests(parsed, insert_index=1, tab_id="t1")
        style_reqs = [r for r in reqs if "updateTextStyle" in r]
        assert len(style_reqs) == 1
        assert style_reqs[0]["updateTextStyle"]["range"]["tabId"] == "t1"

    def test_update_paragraph_style_has_tab_id(self):
        parsed = parse_markdown("# Title")
        reqs = to_docs_requests(parsed, insert_index=1, tab_id="t1")
        para_reqs = [r for r in reqs if "updateParagraphStyle" in r]
        assert len(para_reqs) == 1
        assert para_reqs[0]["updateParagraphStyle"]["range"]["tabId"] == "t1"

    def test_create_paragraph_bullets_has_tab_id(self):
        parsed = parse_markdown("- item")
        reqs = to_docs_requests(parsed, insert_index=1, tab_id="t1")
        bullet_reqs = [r for r in reqs if "createParagraphBullets" in r]
        assert len(bullet_reqs) == 1
        assert bullet_reqs[0]["createParagraphBullets"]["range"]["tabId"] == "t1"

    def test_no_tab_id_when_none(self):
        parsed = parse_markdown("hello")
        reqs = to_docs_requests(parsed, insert_index=1, tab_id=None)
        assert "tabId" not in reqs[0]["insertText"]["location"]

    def test_no_tab_id_absent_by_default(self):
        parsed = parse_markdown("**bold**")
        reqs = to_docs_requests(parsed, insert_index=1)
        style_reqs = [r for r in reqs if "updateTextStyle" in r]
        assert "tabId" not in style_reqs[0]["updateTextStyle"]["range"]


class TestParagraphStyleBeforeTextStyle:
    """Regression: per-tab bold/italic was clobbered by a trailing
    updateParagraphStyle. Paragraph styles must precede text styles so the
    named-style reset does not wipe character formatting.
    """

    def _types(self, reqs):
        types = []
        for r in reqs:
            if "updateTextStyle" in r:
                types.append("text")
            elif "updateParagraphStyle" in r:
                types.append("para")
        return types

    def test_bold_paragraph_emits_para_before_text(self):
        parsed = parse_markdown("This has **bold** in it.")
        reqs = to_docs_requests(parsed, insert_index=1)
        types = self._types(reqs)
        assert types == ["para", "text"]

    def test_all_para_styles_precede_all_text_styles(self):
        md = "This has **bold** and *italic* and a [link](https://x.com)."
        parsed = parse_markdown(md)
        reqs = to_docs_requests(parsed, insert_index=1)
        types = self._types(reqs)
        last_para = max(i for i, t in enumerate(types) if t == "para")
        first_text = min(i for i, t in enumerate(types) if t == "text")
        assert last_para < first_text
        # Bold, italic, and link all still emitted.
        assert types.count("text") == 3


class TestBackslashEscapes:
    """Issue 2: backslash escapes must be removed and the escaped marker
    must not take on its markdown meaning.
    """

    def test_escaped_asterisks_not_italic(self):
        result = parse_markdown(r"A literal star \*not italic\* here.")
        assert result.plain_text == "A literal star *not italic* here.\n"
        italic = [s for s in result.styles if s.style.get("italic")]
        assert italic == []

    def test_escaped_brackets_not_link(self):
        result = parse_markdown(r"Not a link: \[click here\](https://x.com).")
        assert result.plain_text == "Not a link: [click here](https://x.com).\n"
        links = [s for s in result.styles if "link" in s.style]
        assert links == []

    def test_escaped_brackets_only(self):
        result = parse_markdown(r"\[brackets\]")
        assert result.plain_text == "[brackets]\n"

    def test_escaped_tilde_backslash_removed(self):
        result = parse_markdown(r"\~tilde\~")
        assert result.plain_text == "~tilde~\n"

    def test_escaped_marker_does_not_break_adjacent_real_formatting(self):
        result = parse_markdown(r"\* and **bold**")
        assert result.plain_text == "* and bold\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        assert len(bold) == 1
        # Bold applies to "bold", offset past the literal "* and ".
        assert result.plain_text[bold[0].start:bold[0].end] == "bold"

    def test_real_and_escaped_italic_coexist(self):
        result = parse_markdown(r"*real* and \*fake\*")
        assert result.plain_text == "real and *fake*\n"
        italic = [s for s in result.styles if s.style.get("italic")]
        assert len(italic) == 1
        assert result.plain_text[italic[0].start:italic[0].end] == "real"

    def test_escaped_backtick_not_code(self):
        result = parse_markdown(r"use \`literal\` backticks")
        assert result.plain_text == "use `literal` backticks\n"
        code = [s for s in result.styles if "weightedFontFamily" in s.style]
        assert code == []

    def test_code_span_keeps_backslashes(self):
        # Code spans are literal — backslashes must NOT be stripped inside
        # them (e.g. a regex). Outside code, escapes still resolve.
        result = parse_markdown(r"regex `\d+\.\d+` and \* outside")
        assert result.plain_text == "regex \\d+\\.\\d+ and * outside\n"
        code = [s for s in result.styles if "weightedFontFamily" in s.style]
        assert len(code) == 1
        assert result.plain_text[code[0].start:code[0].end] == r"\d+\.\d+"

    def test_double_backslash_becomes_single(self):
        result = parse_markdown(r"path C:\\Users")
        assert result.plain_text == "path C:\\Users\n"

    def test_backslash_before_non_punctuation_kept(self):
        # \U is not an escapable char, so the backslash stays literal.
        result = parse_markdown(r"path C:\Users")
        assert result.plain_text == "path C:\\Users\n"

    def test_escape_in_heading(self):
        result = parse_markdown(r"# Title with \*literal\* stars")
        assert result.plain_text == "Title with *literal* stars\n"
        italic = [s for s in result.styles if s.style.get("italic")]
        assert italic == []
        heading = [s for s in result.styles
                   if s.style.get("namedStyleType") == "HEADING_1"]
        assert len(heading) == 1


class TestParseStrikethrough:
    def test_strikethrough(self):
        result = parse_markdown("~~gone~~")
        assert result.plain_text == "gone\n"
        struck = [s for s in result.styles if s.style.get("strikethrough")]
        assert len(struck) == 1
        assert struck[0].start == 0
        assert struck[0].end == 4

    def test_strikethrough_in_sentence(self):
        result = parse_markdown("keep ~~drop~~ keep")
        assert result.plain_text == "keep drop keep\n"
        struck = [s for s in result.styles if s.style.get("strikethrough")]
        assert len(struck) == 1
        assert struck[0].start == 5
        assert struck[0].end == 9


class TestNestedEmphasis:
    def test_italic_inside_bold(self):
        result = parse_markdown("**bold _it_**")
        assert result.plain_text == "bold it\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        italic = [s for s in result.styles if s.style.get("italic")]
        assert len(bold) == 1 and bold[0].start == 0 and bold[0].end == 7
        assert len(italic) == 1 and italic[0].start == 5 and italic[0].end == 7

    def test_italic_inside_strikethrough(self):
        result = parse_markdown("~~struck *it*~~")
        assert result.plain_text == "struck it\n"
        struck = [s for s in result.styles if s.style.get("strikethrough")]
        italic = [s for s in result.styles if s.style.get("italic")]
        assert len(struck) == 1 and struck[0].end == 9
        assert len(italic) == 1 and italic[0].start == 7 and italic[0].end == 9

    def test_bold_inside_link(self):
        result = parse_markdown("[**hi** there](https://x.com)")
        assert result.plain_text == "hi there\n"
        link = [s for s in result.styles if "link" in s.style]
        bold = [s for s in result.styles if s.style.get("bold")]
        assert len(link) == 1 and link[0].start == 0 and link[0].end == 8
        assert len(bold) == 1 and bold[0].start == 0 and bold[0].end == 2

    def test_abutting_bold_then_italic(self):
        # The closing ** of the bold span must not poison the italic span's
        # lookbehind. Regression for the per-position-search boundary bug.
        result = parse_markdown("**a***b*")
        assert result.plain_text == "ab\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        italic = [s for s in result.styles if s.style.get("italic")]
        assert len(bold) == 1
        assert result.plain_text[bold[0].start:bold[0].end] == "a"
        assert len(italic) == 1
        assert result.plain_text[italic[0].start:italic[0].end] == "b"

    def test_abutting_strikethrough_then_italic(self):
        result = parse_markdown("~~a~~*b*")
        assert result.plain_text == "ab\n"
        struck = [s for s in result.styles if s.style.get("strikethrough")]
        italic = [s for s in result.styles if s.style.get("italic")]
        assert len(struck) == 1
        assert result.plain_text[struck[0].start:struck[0].end] == "a"
        assert len(italic) == 1
        assert result.plain_text[italic[0].start:italic[0].end] == "b"


class TestBlockquote:
    def test_blockquote_indented(self):
        result = parse_markdown("> quoted")
        assert result.plain_text == "quoted\n"
        para = [s for s in result.styles if s.type == "paragraph_style"]
        assert len(para) == 1
        assert para[0].style["namedStyleType"] == "NORMAL_TEXT"
        assert "indentStart" in para[0].style

    def test_blockquote_inline_formatting(self):
        result = parse_markdown("> has **bold**")
        assert result.plain_text == "has bold\n"
        assert [s for s in result.styles if s.style.get("bold")]


class TestHorizontalRule:
    def test_hr_dashes(self):
        result = parse_markdown("---")
        assert result.plain_text == "\n"
        para = [s for s in result.styles if s.type == "paragraph_style"]
        assert len(para) == 1
        assert "borderBottom" in para[0].style

    def test_hr_asterisks(self):
        result = parse_markdown("***")
        para = [s for s in result.styles if s.type == "paragraph_style"]
        assert "borderBottom" in para[0].style

    def test_hr_underscores(self):
        result = parse_markdown("___")
        para = [s for s in result.styles if s.type == "paragraph_style"]
        assert "borderBottom" in para[0].style

    def test_triple_star_with_text_is_not_hr(self):
        result = parse_markdown("***both***")
        para = [s for s in result.styles if s.type == "paragraph_style"]
        assert "borderBottom" not in para[0].style
        assert [s for s in result.styles if s.style.get("bold")]


class TestFencedCode:
    def test_fenced_code_block(self):
        result = parse_markdown("```\nline1\nline2\n```")
        assert result.plain_text == "line1\nline2\n"
        code = [s for s in result.styles if "weightedFontFamily" in s.style]
        assert len(code) == 2

    def test_fence_with_language(self):
        result = parse_markdown("```python\nx = 1\n```")
        assert result.plain_text == "x = 1\n"

    def test_fence_content_not_inline_parsed(self):
        result = parse_markdown("```\n**not bold**\n```")
        assert result.plain_text == "**not bold**\n"
        assert not [s for s in result.styles if s.style.get("bold")]

    def test_fence_preserves_indentation(self):
        result = parse_markdown("```\n  indented\n```")
        assert result.plain_text == "  indented\n"


class TestNestedLists:
    def test_nested_bullet_gets_leading_tab(self):
        result = parse_markdown("- a\n  - b")
        assert result.plain_text == "a\n\tb\n"
        bullets = [s for s in result.styles if s.type == "bullets"]
        assert len(bullets) == 2

    def test_four_space_indent_is_two_levels(self):
        result = parse_markdown("- a\n    - b")
        assert result.plain_text == "a\n\t\tb\n"

    def test_numbered_nesting(self):
        result = parse_markdown("1. a\n   1. b")
        assert result.plain_text == "a\n\tb\n"

    def test_inline_styles_offset_past_leading_tabs(self):
        result = parse_markdown("- a\n  - **b**")
        assert result.plain_text == "a\n\tb\n"
        bold = [s for s in result.styles if s.style.get("bold")]
        # "b" sits after the newline + tab → index 3
        assert len(bold) == 1
        assert result.plain_text[bold[0].start:bold[0].end] == "b"


class TestNewToDocsRequests:
    def test_blockquote_fields_include_indent(self):
        reqs = to_docs_requests(parse_markdown("> q"), insert_index=1)
        ups = [r for r in reqs if "updateParagraphStyle" in r][0]
        fields = ups["updateParagraphStyle"]["fields"]
        assert "indentStart" in fields and "namedStyleType" in fields

    def test_hr_fields_include_border(self):
        reqs = to_docs_requests(parse_markdown("---"), insert_index=1)
        ups = [r for r in reqs if "updateParagraphStyle" in r][0]
        assert "borderBottom" in ups["updateParagraphStyle"]["fields"]

    def test_strikethrough_field(self):
        reqs = to_docs_requests(parse_markdown("~~x~~"), insert_index=1)
        uts = [r for r in reqs if "updateTextStyle" in r][0]
        assert uts["updateTextStyle"]["fields"] == "strikethrough"

    def test_bullets_emitted_in_forward_order(self):
        # Ordered lists only number continuously when items are created
        # top-to-bottom, so bullets are emitted in ascending start order.
        reqs = to_docs_requests(
            parse_markdown("- a\n  - b\n- c"), insert_index=1,
        )
        bullets = [r for r in reqs if "createParagraphBullets" in r]
        starts = [
            b["createParagraphBullets"]["range"]["startIndex"] for b in bullets
        ]
        assert starts == sorted(starts)

    def test_nested_items_share_one_list_range(self):
        # Parent and child must be created together to preserve nesting.
        reqs = to_docs_requests(
            parse_markdown("- a\n  - b\n- c"), insert_index=1,
        )
        from tests.test_list_request_semantics import apply_list_requests

        # One range establishes the entire nested list.
        assert sum("createParagraphBullets" in r for r in reqs) == 1
        paragraphs = [p for p in apply_list_requests(reqs) if p["list"]]
        assert [(p["text"], p["depth"]) for p in paragraphs] == [
            ("a\n", 0), ("b\n", 1), ("c\n", 0)]
        assert len({p["list"] for p in paragraphs}) == 1


class TestTableTabAdjustment:
    def test_removed_tabs_before_counts_preceding_nest_tabs(self):
        md = "- a\n  - b\n\n| H |\n|---|\n| x |"
        result = parse_markdown(md)
        assert len(result.tables) == 1
        assert result.tables[0].removed_tabs_before == 1

    def test_no_tabs_before_table_is_zero(self):
        result = parse_markdown("text\n| H |\n|---|\n| x |")
        assert result.tables[0].removed_tabs_before == 0

    def test_total_removed_tabs_tracked(self):
        # 1 tab (level-1) + 2 tabs (level-2) = 3 total.
        result = parse_markdown("- a\n  - b\n    - c")
        assert result.removed_tabs == 3

    def test_total_removed_tabs_zero_without_nesting(self):
        result = parse_markdown("- a\n- b\nplain")
        assert result.removed_tabs == 0


_CODE = {"weightedFontFamily": {"fontFamily": "Courier New"}}
_BOLD = {"bold": True}
_STRIKE = {"strikethrough": True}


@pytest.mark.parametrize("source,text,ranges", [
    # Single, double and triple backtick strings are all code-span delimiters;
    # content is verbatim and only an equal-length string closes the span.
    ("`code`", "code", [(0, 4, _CODE)]),
    ("``co`de``", "co`de", [(0, 5, _CODE)]),
    # Content is normalised per CommonMark 6.1: line endings become spaces
    # and one leading plus one trailing space is dropped when both exist.
    ("`` `x` ``", "`x`", [(0, 3, _CODE)]),
    ("```\ncode\n```", "code", [(0, 4, _CODE)]),
    ("```python\nx = 1\n```", "python x = 1 ", [(0, 13, _CODE)]),
    ("```\n**code** `x`\n```", "**code** `x`", [(0, 12, _CODE)]),
    ("` `", " ", [(0, 1, _CODE)]),
    ("` a`", " a", [(0, 2, _CODE)]),
    ("`a` and ``b`` and ```c```", "a and b and c",
     [(0, 1, _CODE), (6, 7, _CODE), (12, 13, _CODE)]),
    # Escapes are not processed inside a code span (CommonMark 6.1): the first
    # backtick after the opener closes it, the backslash stays, and the
    # trailing backtick is literal text.
    (r"`a\`b`", "a\\b`", [(0, 2, _CODE)]),
    # An unmatched backtick string is literal; the text around it still parses.
    ("``` **b**", "``` b", [(4, 5, _BOLD)]),
    ("```\n**code**", "```\ncode", [(4, 8, _BOLD)]),
    ("``x`", "``x`", []),
    ("`x``", "`x``", []),
    ("``", "``", []),
    # Equal-length strings match wherever they sit, so a mid-line run closes
    # a span opened at the start; what follows parses normally.
    ("```\nfirst ``` literal\n**second**", "first literal\nsecond",
     [(0, 5, _CODE), (14, 20, _BOLD)]),
    # Tildes are strikethrough only in pairs (GFM); longer runs are literal.
    ("~~s~~", "s", [(0, 1, _STRIKE)]),
    ("~~~s~~~", "~~~s~~~", []),
    ("~~~\n**code**\n~~~", "~~~\ncode\n~~~", [(4, 8, _BOLD)]),
    # Block-level syntax is literal inline: inline content cannot start a block.
    ("# heading", "# heading", []),
    ("- item", "- item", []),
    ("1. item", "1. item", []),
    ("> quote", "> quote", []),
    ("---", "---", []),
])
def test_inline_code_spans_and_literal_blocks(source, text, ranges):
    got_text, got_styles = parse_inline(source)
    assert got_text == text
    assert [(s.start, s.end, s.style) for s in got_styles] == ranges
    assert all(s.type == "text_style" for s in got_styles)


class TestBalancedLinkDestinations:
    @pytest.mark.parametrize("destination, expected", [
        ("https://example.org/Inverse_image_(set_theory)",
         "https://example.org/Inverse_image_(set_theory)"),
        ("https://example.org/a_(b_(c))?d=(e)",
         "https://example.org/a_(b_(c))?d=(e)"),
        (r"https://example.org/a_\(b\)", "https://example.org/a_(b)"),
        (r"https://example.org/a\)b", "https://example.org/a)b"),
        (r"https://example.org/a\(b", "https://example.org/a(b"),
        (r"https://example.org/a\\(b)", "https://example.org/a\\(b)"),
    ])
    @pytest.mark.parametrize("suffix", [".", ")", ", next"])
    def test_destination_and_adjacent_punctuation(self, destination, expected, suffix):
        plain, styles = parse_inline(f"See [**inverse image**]({destination}){suffix}")
        assert plain == f"See inverse image{suffix}"
        assert [(s.start, s.end, s.style) for s in styles] == [
            (4, 17, {"bold": True}),
            (4, 17, {"link": {"url": expected}}),
        ]
        requests = to_docs_requests(
            parse_markdown(f"[inverse image]({destination})"), 1,
        )
        links = [r["updateTextStyle"] for r in requests if "updateTextStyle" in r]
        assert links[0]["textStyle"] == {"link": {"url": expected}}
        assert links[0]["range"] == {"startIndex": 1, "endIndex": 14}

    @pytest.mark.parametrize("text", [
        "[label](https://example.org/(unclosed)",
        "[label](https://example.org/unclosed",
        "[label](https://example.org/\nnext)",
        r"\[label](https://example.org/(x))",
    ])
    def test_invalid_or_escaped_link_is_literal(self, text):
        plain, styles = parse_inline(text)
        assert plain == text.removeprefix("\\")
        assert styles == []

    def test_unclosed_link_does_not_hide_later_valid_link(self):
        plain, styles = parse_inline("[bad](unclosed [good](https://example.org/(x))")
        assert plain == "[bad](unclosed good"
        assert styles[0].style == {"link": {"url": "https://example.org/(x)"}}


class TestNativeImages:
    """Images carry native placeholders; incomplete syntax stays literal."""

    def _image(self, text):
        parsed = parse_markdown(text)
        assert len(parsed.images) == 1
        image = parsed.images[0]
        assert parsed.plain_text[image.plain_text_offset] == " "
        assert image.uri.startswith("https://")
        return image

    def test_inline_image_represented(self):
        self._image("Intro\n\n![photo](https://example.com/a.png)\n")

    def test_reference_image_with_definition_represented(self):
        self._image(
            "See ![Logo][logo] here\n\n[LOGO]: https://example.com/l.png\n"
        )

    def test_collapsed_reference_image_represented(self):
        self._image("![logo][]\n\n[logo]: https://example.com/l.png\n")

    def test_nested_bracket_alt_inline_represented(self):
        self._image("![a [nested] label](https://example.com/image.png)\n")

    def test_nested_bracket_alt_reference_represented(self):
        self._image("![a [nested] label][pic]\n\n[pic]: https://example.com/i.png\n")

    def test_bracketed_label_cannot_define_a_reference(self):
        # CommonMark link labels may not contain brackets, so no definition
        # can resolve this shortcut form; it is literal text.
        parsed = parse_markdown(
            "![a [nested] label]\n\n[a [nested] label]: https://x.test/i.png\n"
        )
        assert "![a [nested] label]" in parsed.plain_text

    def test_deeply_nested_alt_inline_represented(self):
        self._image("![a [b [c] d] e](https://example.com/i.png)\n")

    def test_deeply_nested_alt_reference_represented(self):
        self._image("![a [b [c] d] e][pic]\n\n[pic]: https://example.com/i.png\n")

    def test_deeply_nested_alt_without_destination_is_literal(self):
        parsed = parse_markdown("See ![a [b [c] d] e] here\n")
        assert "![a [b [c] d] e]" in parsed.plain_text

    def test_unbalanced_opener_is_not_an_image(self):
        # `![a ` never closes, so the inner `[b](url)` is an ordinary link.
        parsed = parse_markdown("![a [b](https://example.com) tail\n")
        assert "tail" in parsed.plain_text

    def test_unbalanced_destination_is_literal(self):
        parsed = parse_markdown("Malformed ![x](foo(bar) example\n")
        assert "![x](foo(bar)" in parsed.plain_text

    def test_multiline_destination_is_literal(self):
        parsed = parse_markdown("![x](first\nsecond) tail\n")
        assert "tail" in parsed.plain_text

    def test_balanced_parenthesised_destination_represented(self):
        self._image("![x](https://example.com/a_(b).png)\n")

    def test_empty_destination_is_literal(self):
        parsed = parse_markdown("An empty ![x]() marker\n")
        assert "![x]()" in parsed.plain_text

    def test_image_like_text_inside_link_destination_is_a_link(self):
        parsed = parse_markdown("See [link](https://example.com/![a](b)) now\n")
        assert "link" in parsed.plain_text
        assert any(s.style.get("link") for s in parsed.styles)

    def test_linked_image_still_represented(self):
        self._image("[![img](https://x.test/i.png)](https://x.test/)\n")

    def test_image_after_link_still_represented(self):
        self._image("See [x](https://x.test/) then ![a](https://x.test/i.png)\n")

    def test_nested_bracket_without_destination_is_literal(self):
        parsed = parse_markdown("An ![a [nested] label] marker\n")
        assert "![a [nested] label]" in parsed.plain_text

    def test_html_img_requires_markdown_syntax(self):
        from gdoc.util import GdocError

        with pytest.raises(GdocError, match="Markdown image syntax"):
            parse_markdown('Text <img src="x.png"> more\n')

    def test_bare_opener_is_literal(self):
        parsed = parse_markdown("Use ![ literally in prose\n")
        assert parsed.plain_text.startswith("Use ![")

    def test_undefined_reference_is_literal(self):
        parsed = parse_markdown("An unmatched ![alt] marker\n")
        assert "![alt]" in parsed.plain_text

    def test_reference_without_definition_is_literal(self):
        parsed = parse_markdown("![alt][missing] and [other]: https://example.com\n")
        assert "![alt][missing]" in parsed.plain_text

    def test_image_inside_fence_is_literal(self):
        parsed = parse_markdown("```\n![photo](https://example.com/a.png)\n```\n")
        assert "![photo]" in parsed.plain_text

    def test_image_inside_code_span_is_literal(self):
        parsed = parse_markdown("Type `![alt](url)` to embed\n")
        assert "![alt](url)" in parsed.plain_text


class TestSingleLineTripleBacktickSpan:
    """A backtick fence's info string holds no backtick (CommonMark 4.5)."""

    def test_single_line_span_is_inline_code_not_a_fence(self):
        parsed = parse_markdown("```code```\n")
        assert parsed.plain_text == "code\n"
        assert any(s.style == {"weightedFontFamily": {"fontFamily": "Courier New"}}
                   or "fontFamily" in str(s.style) for s in parsed.styles)

    def test_single_line_span_never_swallows_following_content(self):
        parsed = parse_markdown("Intro\n```code```\nAfter\n")
        assert parsed.plain_text == "Intro\ncode\nAfter\n"

    def test_real_fence_still_opens(self):
        parsed = parse_markdown("```python\nx = 1\n```\nAfter\n")
        assert parsed.plain_text == "x = 1\nAfter\n"

    def test_image_after_closed_span_is_represented(self):
        parsed = parse_markdown("```code```\n![a](https://x.test/i.png)\n")
        assert parsed.plain_text == "code\n \n"
        assert parsed.images[0].uri == "https://x.test/i.png"
