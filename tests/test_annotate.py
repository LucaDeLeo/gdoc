"""Tests for the annotation engine (cat --comments)."""

import pytest

from gdoc.annotate import annotate_markdown


def _make_comment(cid="c1", content="test comment", email="alice@co.com",
                  anchor=None, resolved=False, replies=None):
    """Build a comment dict matching API shape with optional anchor."""
    c = {
        "id": cid,
        "content": content,
        "author": {"displayName": "Alice", "emailAddress": email},
        "resolved": resolved,
        "createdTime": "2025-06-15T10:00:00Z",
        "modifiedTime": "2025-06-15T10:00:00Z",
        "replies": replies or [],
    }
    if anchor is not None:
        c["quotedFileContent"] = {"value": anchor}
    return c


class TestSingleMatchAnnotation:
    def test_annotation_placed_after_correct_line(self):
        md = "# Heading\n\nSome content line.\n\nMore content.\n"
        comment = _make_comment(cid="1", content="This needs a citation",
                                anchor="content line")
        result = annotate_markdown(md, [comment])
        lines = result.split("\n")
        # "content line" is on line 3 (1-indexed), annotation follows
        assert any("     3\t" in l and "content line" in l for l in lines)
        assert any('[#1 open]' in l and 'alice@co.com' in l and 'on "content line"' in l for l in lines)
        assert any('"This needs a citation"' in l for l in lines)

    def test_annotation_on_first_line(self):
        md = "First line here\nSecond line\n"
        comment = _make_comment(anchor="First line here")
        result = annotate_markdown(md, [comment])
        lines = result.split("\n")
        # Annotation should follow line 1
        assert lines[0].strip().startswith("1\tFirst line here")
        assert "[#c1 open]" in lines[1]

    def test_annotation_on_last_line(self):
        md = "Line 1\nLine 2\nLast line\n"
        comment = _make_comment(anchor="Last line")
        result = annotate_markdown(md, [comment])
        assert "[#c1 open]" in result
        assert 'on "Last line"' in result


class TestMultipleMatchesAmbiguous:
    def test_ambiguous_anchor_placed_unanchored(self):
        md = "hello world\nhello world\n"
        comment = _make_comment(anchor="hello world")
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result
        assert "[anchor ambiguous]" in result


class TestZeroMatchesDeleted:
    def test_deleted_anchor_placed_unanchored(self):
        md = "Some text\nMore text\n"
        comment = _make_comment(anchor="nonexistent text that was deleted")
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result
        assert "[anchor deleted]" in result


class TestShortAnchorTooShort:
    def test_short_anchor_placed_unanchored(self):
        md = "The cat sat on the mat.\n"
        comment = _make_comment(anchor="cat")
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result
        assert "[anchor too short]" in result

    def test_exactly_four_chars_not_short(self):
        md = "The cats sat on the mat.\n"
        comment = _make_comment(anchor="cats")
        result = annotate_markdown(md, [comment])
        # 4 chars is >= 4, so should be anchored inline
        assert "[UNANCHORED]" not in result
        assert '[#c1 open]' in result


class TestMultilineAnchor:
    def test_multiline_anchor_annotates_after_last_line(self):
        md = "Line 1\nLine 2 start\ncontinued here\nLine 4\n"
        anchor = "Line 2 start\ncontinued here"
        comment = _make_comment(anchor=anchor)
        result = annotate_markdown(md, [comment])
        lines = result.split("\n")
        # Anchor spans lines 2-3, annotation after line 3
        # Find the annotation line
        annotation_idx = None
        for i, l in enumerate(lines):
            if "[#c1 open]" in l:
                annotation_idx = i
                break
        assert annotation_idx is not None
        # Line before annotation should be line 3 content
        assert "     3\t" in lines[annotation_idx - 1]

    def test_multiline_anchor_multiple_matches_ambiguous(self):
        md = "hello\nworld\nhello\nworld\n"
        comment = _make_comment(anchor="hello\nworld")
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result
        assert "[anchor ambiguous]" in result


class TestUnanchoredComment:
    def test_no_quoted_file_content(self):
        md = "Some content\n"
        comment = _make_comment()  # no anchor
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result
        assert '[#c1 open]' in result

    def test_empty_quoted_value(self):
        md = "Some content\n"
        comment = _make_comment()
        comment["quotedFileContent"] = {"value": ""}
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result


class TestMixedAnchoredAndUnanchored:
    def test_mixed_renders_correctly(self):
        md = "# Title\n\nSome important text here.\n\nConclusion paragraph.\n"
        anchored = _make_comment(cid="1", content="Good point",
                                 anchor="important text")
        unanchored = _make_comment(cid="2", content="General feedback")
        result = annotate_markdown(md, [anchored, unanchored])
        # Anchored inline
        assert '[#1 open]' in result
        assert 'on "important text"' in result
        # Unanchored at bottom
        assert "[UNANCHORED]" in result
        assert '[#2 open]' in result
        # Unanchored section comes after content
        content_end = result.rfind("Conclusion")
        unanchored_pos = result.find("[UNANCHORED]")
        assert unanchored_pos > content_end


class TestEmptyDocumentWithComments:
    def test_empty_doc_all_unanchored(self):
        md = ""
        comment = _make_comment(anchor="some text")
        result = annotate_markdown(md, [comment])
        assert "[UNANCHORED]" in result
        assert "[anchor deleted]" in result


class TestResolvedFilter:
    def test_resolved_shown_with_marker(self):
        md = "Some text\n"
        comment = _make_comment(cid="1", content="Done", resolved=True,
                                anchor="Some text")
        result = annotate_markdown(md, [comment], show_resolved=True)
        assert "[#1 resolved]" in result

    def test_resolved_hidden_by_default(self):
        md = "Some text\n"
        comment = _make_comment(cid="1", content="Done", resolved=True,
                                anchor="Some text")
        result = annotate_markdown(md, [comment], show_resolved=False)
        assert "#1" not in result


class TestLineNumberFormat:
    def test_content_lines_numbered(self):
        md = "Line 1\nLine 2\n"
        result = annotate_markdown(md, [])
        lines = result.split("\n")
        assert lines[0] == "     1\tLine 1"
        assert lines[1] == "     2\tLine 2"

    def test_annotation_lines_unnumbered(self):
        md = "Some content\n"
        comment = _make_comment(anchor="Some content")
        result = annotate_markdown(md, [comment])
        lines = result.split("\n")
        # First line is numbered, annotation lines are not
        assert lines[0].strip().startswith("1\t")
        # Annotation lines start with spaces + tab
        for line in lines[1:]:
            if line.strip() and "[#" in line:
                assert line.startswith("      \t")


class TestRepliesShown:
    def test_replies_displayed_with_prefix(self):
        md = "Some text here\n"
        comment = _make_comment(
            anchor="Some text",
            replies=[
                {"author": {"emailAddress": "bob@co.com"}, "content": "Fixed"},
            ],
        )
        result = annotate_markdown(md, [comment])
        assert '> bob@co.com: "Fixed"' in result

    def test_action_only_replies_hidden(self):
        md = "Some text here\n"
        comment = _make_comment(
            anchor="Some text",
            replies=[
                {"author": {"emailAddress": "bob@co.com"}, "content": "", "action": "resolve"},
            ],
        )
        result = annotate_markdown(md, [comment])
        assert "bob@co.com" not in result or '> bob@co.com' not in result

    def test_unanchored_replies_shown(self):
        md = "Some text\n"
        comment = _make_comment(
            replies=[
                {"author": {"emailAddress": "bob@co.com"}, "content": "I agree"},
            ],
        )
        result = annotate_markdown(md, [comment])
        assert '> bob@co.com: "I agree"' in result


class TestAnchorTextTruncation:
    def test_long_anchor_truncated_in_display(self):
        md = "This is a very long piece of text that should be truncated in the annotation display.\n"
        anchor = "This is a very long piece of text that should be truncated in the annotation display"
        comment = _make_comment(anchor=anchor)
        result = annotate_markdown(md, [comment])
        # Anchor in display should be truncated to 40 chars
        assert '..."' in result
