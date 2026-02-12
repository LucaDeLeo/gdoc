"""Tests for the frontmatter parser."""

from gdoc.frontmatter import add_frontmatter, parse_frontmatter


class TestParseFrontmatter:
    def test_basic(self):
        content = "---\ngdoc: abc123\ntitle: My Doc\n---\n# Hello\n"
        meta, body = parse_frontmatter(content)
        assert meta == {"gdoc": "abc123", "title": "My Doc"}
        assert body == "# Hello\n"

    def test_no_frontmatter(self):
        content = "# Just a heading\nSome text."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_string(self):
        meta, body = parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_incomplete_frontmatter(self):
        content = "---\ngdoc: abc\nno closing"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_only_opening_dashes(self):
        content = "---\nsome text\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_frontmatter(self):
        content = "---\n\n---\nBody here."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == "Body here."

    def test_value_with_colons(self):
        content = "---\nurl: https://example.com:8080/path\n---\nBody"
        meta, body = parse_frontmatter(content)
        assert meta == {"url": "https://example.com:8080/path"}
        assert body == "Body"

    def test_whitespace_in_values(self):
        content = "---\ntitle:   Spaces Everywhere  \n---\nBody"
        meta, body = parse_frontmatter(content)
        assert meta == {"title": "Spaces Everywhere"}

    def test_blank_lines_in_frontmatter(self):
        content = "---\ngdoc: abc\n\ntitle: Test\n---\nBody"
        meta, body = parse_frontmatter(content)
        assert meta == {"gdoc": "abc", "title": "Test"}

    def test_no_value(self):
        content = "---\nkey:\n---\nBody"
        meta, body = parse_frontmatter(content)
        assert meta == {"key": ""}

    def test_no_colon_line_skipped(self):
        content = "---\ngdoc: abc\nbadline\ntitle: T\n---\nBody"
        meta, body = parse_frontmatter(content)
        assert meta == {"gdoc": "abc", "title": "T"}

    def test_frontmatter_not_at_start(self):
        content = "Some text\n---\ngdoc: abc\n---\nBody"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_body_preserved_exactly(self):
        body_text = "Line 1\n\nLine 3\n"
        content = f"---\nk: v\n---\n{body_text}"
        meta, body = parse_frontmatter(content)
        assert body == body_text

    def test_multiline_body(self):
        content = "---\ngdoc: x\n---\n# Title\n\nParagraph 1\n\nParagraph 2\n"
        meta, body = parse_frontmatter(content)
        assert meta == {"gdoc": "x"}
        assert body == "# Title\n\nParagraph 1\n\nParagraph 2\n"


class TestAddFrontmatter:
    def test_basic(self):
        result = add_frontmatter("# Hello", {"gdoc": "abc123", "title": "My Doc"})
        assert result == "---\ngdoc: abc123\ntitle: My Doc\n---\n# Hello"

    def test_empty_body(self):
        result = add_frontmatter("", {"gdoc": "abc"})
        assert result == "---\ngdoc: abc\n---\n"

    def test_empty_metadata(self):
        result = add_frontmatter("Body", {})
        assert result == "---\n---\nBody"

    def test_roundtrip(self):
        original_body = "# Document\n\nContent here.\n"
        original_meta = {"gdoc": "1aBcDeFg", "title": "Project Spec"}
        content = add_frontmatter(original_body, original_meta)
        meta, body = parse_frontmatter(content)
        assert meta == original_meta
        assert body == original_body
