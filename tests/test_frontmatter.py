"""Tests for the frontmatter parser."""

import pytest

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

    def test_empty_frontmatter_left_in_place(self):
        # A leading `---\\n\\n---\\n` block with no key:value content
        # isn't treated as frontmatter — otherwise a markdown file that
        # opens with a thematic break would silently lose its first
        # section.
        content = "---\n\n---\nBody here."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_thematic_break_with_prose_not_stripped(self):
        # Innocent markdown that happens to start with `---` and has
        # another `---` later must round-trip unchanged.
        content = "---\n\n# Real heading\n\nFirst paragraph.\n\n---\nFooter"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

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

    def test_newlines_in_values_flattened(self):
        # A newline in a value (e.g. a doc title) must not be able to
        # inject extra frontmatter keys like `gdoc:`.
        result = add_frontmatter(
            "Body", {"title": "Line one\ngdoc: evil-id"},
        )
        metadata, _ = parse_frontmatter(result)
        assert "gdoc" not in metadata
        assert metadata["title"] == "Line one gdoc: evil-id"

    @pytest.mark.parametrize(
        "sep",
        ["\n", "\r\n", "\r", "\x0b", "\x0c", "\x85", "\u2028", "\u2029"],
    )
    def test_every_line_separator_flattened(self, sep):
        # parse_frontmatter uses splitlines(), so every separator it
        # honors must be neutralized on write, not just \r and \n
        result = add_frontmatter(
            "Body", {"title": f"Line one{sep}gdoc: evil-id"},
        )
        metadata, _ = parse_frontmatter(result)
        assert "gdoc" not in metadata

    def test_roundtrip(self):
        original_body = "# Document\n\nContent here.\n"
        original_meta = {"gdoc": "1aBcDeFg", "title": "Project Spec"}
        content = add_frontmatter(original_body, original_meta)
        meta, body = parse_frontmatter(content)
        assert meta == original_meta
        assert body == original_body


class TestLeadingRuleProse:
    """R7-8: a body opening with a rule keeps prose that contains a colon."""

    @pytest.mark.parametrize("content", [
        "---\n\nNote: keep me\n\n---\n\nafter\n",
        "---\n\nSee [docs](https://e.org/).\n\n---\n",
        "---\nSee [docs](https://e.org/).\n---\nafter\n",
        "---\nhttps://example.com\n---\nafter\n",
        "---\ntitle: x\nhttps://example.com\n---\nafter\n",
        "---\nNote\\: keep me\n---\nafter\n",
    ])
    def test_prose_between_rules_stays_in_the_body(self, content):
        assert parse_frontmatter(content) == ({}, content)

    @pytest.mark.parametrize("content,meta", [
        ("---\ngdoc: abc\ntitle: A: B\ntab: t.1\ngdoc-revision: r1\n---\nbody\n",
         {"gdoc": "abc", "title": "A: B", "tab": "t.1", "gdoc-revision": "r1"}),
        ("---\ntitle: x\ntags:\n  - a\n  - name: b\n# c: d\n---\nbody\n",
         {"title": "x", "tags": ""}),
        ("---\r\ngdoc: abc\r\ntitle: T\r\n---\r\nbody\r\n",
         {"gdoc": "abc", "title": "T"}),
    ])
    def test_known_frontmatter_still_parses(self, content, meta):
        assert parse_frontmatter(content)[0] == meta

    def test_key_value_prose_between_rules_is_metadata(self):
        """The documented boundary: a first-line `key: value` is metadata."""
        assert parse_frontmatter("---\nNote: keep me\n---\nafter\n") == (
            {"Note": "keep me"}, "after\n")

    def test_comment_before_keys_is_still_metadata(self):
        assert parse_frontmatter("---\n# keep\ngdoc: a\n---\nb\n")[0] == {"gdoc": "a"}

    @pytest.mark.parametrize("content,meta", [
        ("---\ntitle: Post\nLast updated: 2024\n---\nB\n",
         {"title": "Post", "Last updated": "2024"}),
        ('---\n"title": Post\n---\nB\n', {"title": "Post"}),
        ("---\ntítulo: Post\n---\nB\n", {"título": "Post"}),
        ("---\nkey:value\n---\nB\n", {"key": "value"}),
        ("---\ntitle: x\nurl: http://x\n---\nB\n", {"title": "x", "url": "http://x"}),
    ])
    def test_other_tools_frontmatter_keys_still_parse(self, content, meta):
        """Round-8 recheck: spaced, quoted, non-ASCII and unspaced keys."""
        assert parse_frontmatter(content) == (meta, "B\n")

    @pytest.mark.parametrize("line", [
        "| a | b: c |", "1. step: one", "- item: x", "> quote: x",
        "Time 10:30 meeting", "C:\\path", "_Note_: x", "~~old~~: x",
        "**Note**: bold", "See https://e.org for more",
    ])
    def test_markdown_shaped_lines_stay_in_the_body(self, line):
        """Round-8 final recheck: content between rules is not metadata."""
        content = f"---\n{line}\n---\nafter\n"
        assert parse_frontmatter(content) == ({}, content)
