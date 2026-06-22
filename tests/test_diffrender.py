"""Tests for the terminal and HTML revision-diff renderers."""

from gdoc.diffrender import (
    render_html,
    render_terminal,
    select_visible,
    split_comments,
)
from gdoc.revdiff import attach_comments, build_diff_model, build_hunks

OLD_REV = {"id": "69", "modifiedTime": "2026-06-01T10:00:00.000Z"}
NEW_REV = {"id": "190", "modifiedTime": "2026-06-10T19:23:00.000Z"}


def _model(old_md, new_md, comments=None):
    model = build_diff_model(
        "doc1", "Test Doc", OLD_REV, NEW_REV, old_md, new_md,
    )
    if comments is not None:
        model["comments"] = attach_comments(model["hunks"], comments)
    return model


def _paragraphs(n, prefix="Unchanged paragraph"):
    return "\n\n".join(f"{prefix} number {i}." for i in range(n)) + "\n"


class TestSelectVisible:
    def test_context_window_around_change(self):
        old = _paragraphs(7)
        new = old.replace("number 3.", "number three, fully rewritten.")
        hunks = build_hunks(old, new)
        keep = select_visible(hunks, context=1)
        assert keep == [False, False, True, True, True, False, False]

    def test_headings_always_kept(self):
        old = "# Section\n\n" + _paragraphs(7)
        new = old.replace("number 6.", "number six, fully rewritten.")
        hunks = build_hunks(old, new)
        keep = select_visible(hunks, context=1)
        assert keep[0] is True  # heading far from the change

    def test_commented_hunks_kept(self):
        hunks = build_hunks(_paragraphs(7), _paragraphs(7))
        keep = select_visible(hunks, context=0, comment_hunks={4})
        assert keep[4] is True
        assert keep[1] is False


class TestSplitComments:
    def test_split(self):
        by_hunk, appendix = split_comments([
            {"id": "a", "hunk": 2}, {"id": "b", "hunk": None},
            {"id": "c", "hunk": 2},
        ])
        assert [c["id"] for c in by_hunk[2]] == ["a", "c"]
        assert [c["id"] for c in appendix] == ["b"]


class TestRenderTerminal:
    def test_plain_uses_word_diff_markers(self):
        old = "The plan is to ship the feature next week to customers.\n"
        new = "The plan is to deliver the product next month to customers.\n"
        out = render_terminal(_model(old, new), color=False)
        assert "[-" in out and "-]" in out
        assert "{+" in out and "+}" in out
        assert "\x1b[" not in out

    def test_plain_collapses_unchanged(self):
        old = _paragraphs(9)
        new = old.replace("number 0.", "number zero, fully rewritten.")
        out = render_terminal(_model(old, new), color=False, context=1)
        assert "unchanged ⋯" in out

    def test_color_uses_ansi(self):
        old = "Alpha original sentence with plenty of words.\n"
        new = "Alpha rewritten sentence with plenty of words.\n"
        out = render_terminal(_model(old, new), color=True)
        assert "\x1b[32m" in out  # green insert
        assert "\x1b[31;9m" in out  # red strikethrough delete

    def test_header_names_revisions(self):
        out = render_terminal(_model("a b c\n", "a b d\n"), color=False)
        assert "rev 69" in out and "rev 190" in out
        assert "Test Doc" in out

    def test_heading_prefix_preserved(self):
        out = render_terminal(
            _model("## Section title here\n", "## Section title here\n"),
            color=False,
        )
        assert "## Section title here" in out

    def test_list_markers_shown(self):
        # Ordered items keep their ordinal; a bullet→numbered switch
        # shows both forms
        out = render_terminal(
            _model(
                "- The same item text here.\n",
                "1\\. The same item text here.\n",
            ),
            color=False,
        )
        assert "• [-The same item text here.-]" in out
        assert "1. {+The same item text here.+}" in out


class TestRenderHtml:
    def test_ins_del_tags(self):
        old = "The plan is to ship the feature next week to customers.\n"
        new = "The plan is to deliver the product next month to customers.\n"
        html = render_html(_model(old, new))
        assert "<ins>" in html and "<del>" in html

    def test_escapes_content(self):
        html = render_html(_model(
            "Safe paragraph.\n", "<script>alert(1)</script> injected.\n",
        ))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_comments_inline_and_appendix(self):
        old = "Stable opening paragraph.\n\nThe rollout plan needs work.\n"
        new = "Stable opening paragraph.\n\nThe rollout plan is finished.\n"
        comments = [
            {
                "id": "c1", "author": {"displayName": "Alice"},
                "createdTime": "2026-06-09T00:00:00Z", "resolved": False,
                "content": "Looks good now",
                "quotedFileContent": {"value": "rollout plan"},
                "replies": [{
                    "author": {"displayName": "Bob"},
                    "content": "Agreed", "createdTime": "2026-06-09T01:00:00Z",
                }],
            },
            {
                "id": "c2", "author": {"displayName": "Carol"},
                "createdTime": "2026-06-09T02:00:00Z", "resolved": True,
                "content": "Anchored to vanished text",
                "quotedFileContent": {"value": "completely absent anchor"},
                "replies": [],
            },
        ]
        html = render_html(_model(old, new, comments))
        assert "Alice" in html and "Agreed" in html
        assert "Other comment threads" in html
        assert "Carol" in html
        assert "(resolved)" in html

    def test_self_contained_document(self):
        html = render_html(_model("a b c\n", "a b c\n"))
        assert html.startswith("<!doctype html>")
        assert "<style>" in html
