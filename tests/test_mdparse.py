"""Tests for the markdown parser and Docs API request builder."""

from gdoc.mdparse import parse_markdown, to_docs_requests


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
        # insertText + updateTextStyle + updateParagraphStyle (NORMAL_TEXT)
        assert len(reqs) == 3
        insert = reqs[0]
        assert insert["insertText"]["text"] == "bold\n"
        assert insert["insertText"]["location"]["index"] == 5

        style_req = reqs[1]
        assert "updateTextStyle" in style_req
        uts = style_req["updateTextStyle"]
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
        assert cpb["range"]["startIndex"] == 1

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
        style = reqs[1]["updateTextStyle"]
        assert style["range"]["startIndex"] == 100
        assert style["range"]["endIndex"] == 104

    def test_empty_parsed_returns_empty(self):
        parsed = parse_markdown("")
        reqs = to_docs_requests(parsed, insert_index=0)
        assert reqs == []

    def test_request_ordering(self):
        """Insert first, text styles, paragraph styles, bullets."""
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
        # Text styles before paragraph styles before bullets
        text_idx = types.index("text_style")
        para_idx = types.index("para_style")
        bullet_idx = types.index("bullets")
        assert text_idx < para_idx < bullet_idx


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
