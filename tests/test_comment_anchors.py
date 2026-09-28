"""Live comment anchors: get_comment_anchors and `cat --comments` labels.

The scenarios mirror what Google Docs does to comments, observed live:
a full-tab rewrite detaches every comment even where its quoted text
survives; a targeted edit keeps a comment while some of its anchored text
survives (rewording inside it, joining paragraphs across it) and detaches
it once all of that text is gone. Drive's quotedFileContent never changes
in any of these cases, so the tests give each comment a stale quote.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gdoc import mcp
from gdoc.annotate import annotate_markdown
from gdoc.api.docs import get_comment_anchors, get_revision_id
from gdoc.cli import cmd_cat
from gdoc.util import AuthError, GdocError, PreviewUnavailableError


def _utf16(text: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _tab(tab_id: str, paragraphs: list[str], anchors: dict[str, list[str]],
         table_cell: str | None = None) -> dict:
    """A preview documentTab: one textRun per paragraph, indices from 1.

    *anchors* maps an anchor ID to the substrings it covers (each located
    at its first occurrence after the previous one); *table_cell* adds a
    one-cell table after the paragraphs.
    """
    content, index, text = [], 1, ""
    for p in paragraphs:
        run = p + "\n"
        content.append({"startIndex": index, "paragraph": {"elements": [
            {"startIndex": index, "textRun": {"content": run}},
        ]}})
        index += _utf16(run)
        text += run
    if table_cell is not None:
        run = table_cell + "\n"
        content.append({"startIndex": index, "table": {"tableRows": [
            {"tableCells": [{"content": [{"paragraph": {"elements": [
                {"startIndex": index + 3, "textRun": {"content": run}},
            ]}}]}]},
        ]}})
        text += "\0\0\0" + run
    comment_anchors = {}
    for anchor_id, pieces in anchors.items():
        ranges, pos = [], 0
        for piece in pieces:
            pos = text.index(piece, pos)
            start = 1 + _utf16(text[:pos])
            ranges.append({"startIndex": start,
                           "endIndex": start + _utf16(piece),
                           "tabId": tab_id})
            pos += len(piece)
        comment_anchors[anchor_id] = {"anchorId": anchor_id, "ranges": ranges}
    return {
        "tabProperties": {"tabId": tab_id},
        "documentTab": {"body": {"content": content},
                        "commentAnchors": comment_anchors},
    }


def _document(tabs: list[dict], comments: dict[str, str]) -> dict:
    """Preview documents.get JSON; *comments* maps comment ID to anchor ID."""
    return {
        "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        "tabs": tabs,
        "comments": [
            {"commentId": cid, "anchorId": aid} for cid, aid in comments.items()
        ],
    }


def _response(status=200, body=None, text="", reason="OK"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text or json.dumps(body or {})
    resp.reason = reason
    resp.json.return_value = body or {}
    return resp


def _anchors(document: dict) -> dict:
    with patch("gdoc.api.docs._comments_view_get",
               return_value=_response(body=document)):
        anchors, _revision = get_comment_anchors("doc1")
    return anchors


def _comment(cid: str, quote: str, content: str = "note") -> dict:
    return {
        "id": cid,
        "content": content,
        "author": {"emailAddress": "alice@example.com"},
        "resolved": False,
        "quotedFileContent": {"value": quote},
        "replies": [],
    }


class TestGetCommentAnchors:
    def test_attached_and_detached(self):
        doc = _document(
            [_tab("t.1", ["Golf has the quick red fox here."],
                  {"kix.a": ["quick red fox"]})],
            {"c1": "kix.a", "c2": "kix.gone"},
        )
        assert _anchors(doc) == {
            "c1": {"text": "quick red fox", "key": "quick red fox",
                   "occurrence": 0, "occurrences": 1},
            "c2": None,
        }

    def test_comment_without_anchor_id_is_left_out(self):
        doc = _document([_tab("t.1", ["Text."], {})], {"c1": ""})
        assert _anchors(doc) == {}

    def test_multi_paragraph_anchor_keys_on_its_last_paragraph(self):
        doc = _document(
            [_tab("t.1", ["Charlie starts the anchor.", "", "Delta ends it."],
                  {"kix.a": ["anchor.\n\nDelta ends"]})],
            {"c1": "kix.a"},
        )
        anchor = _anchors(doc)["c1"]
        assert anchor["text"] == "anchor.\n\nDelta ends"
        assert anchor["key"] == "Delta ends"

    def test_anchor_ending_at_a_paragraph_break_keys_on_its_text(self):
        doc = _document(
            [_tab("t.1", ["Whole paragraph.", "Next."],
                  {"kix.a": ["Whole paragraph.\n"]})],
            {"c1": "kix.a"},
        )
        assert _anchors(doc)["c1"]["key"] == "Whole paragraph."

    def test_occurrence_counts_across_tabs_in_order(self):
        doc = _document(
            [
                _tab("t.1", ["Same words.", "Same words."], {}),
                _tab("t.2", ["Same words."], {"kix.a": ["Same words."]}),
            ],
            {"c1": "kix.a"},
        )
        anchor = _anchors(doc)["c1"]
        # Three body copies plus the two tab titles ("" for both here).
        assert (anchor["occurrence"], anchor["occurrences"]) == (2, 3)

    def test_tab_titles_count_only_with_several_tabs(self):
        # The export heads each tab with its title only when there are
        # several tabs; the default single-tab title never appears.
        first = _tab("t.1", ["Budget"], {"kix.a": ["Budget"]})
        first["tabProperties"]["title"] = "Budget"
        second = _tab("t.2", ["Other"], {})
        second["tabProperties"]["title"] = "Notes"
        doc = _document([first, second], {"c1": "kix.a"})
        anchor = _anchors(doc)["c1"]
        assert (anchor["occurrence"], anchor["occurrences"]) == (1, 2)
        result = annotate_markdown(
            "# Budget\n\nBudget\n\n# Notes\n\nOther\n",
            [_comment("c1", "Budget")], anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 3

    def test_single_tab_title_is_not_counted(self):
        tab = _tab("t.1", ["Revenue grew 1 percent.", "First step"],
                   {"kix.a": ["1"]})
        tab["tabProperties"]["title"] = "Tab 1"
        doc = _document([tab], {"c1": "kix.a"})
        anchor = _anchors(doc)["c1"]
        assert (anchor["occurrence"], anchor["occurrences"]) == (0, 1)
        # The list number is markup, not text, so the only "1" is line 1.
        result = annotate_markdown(
            "Revenue grew 1 percent.\n\n1. First step\n",
            [_comment("c1", "1")], anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 1

    def test_child_tabs_are_searched(self):
        child = _tab("t.child", ["Nested text here."], {"kix.a": ["Nested"]})
        parent = _tab("t.1", ["Parent text."], {})
        parent["childTabs"] = [child]
        doc = _document([parent], {"c1": "kix.a"})
        assert _anchors(doc)["c1"]["text"] == "Nested"

    def test_utf16_indices_after_an_emoji(self):
        doc = _document(
            [_tab("t.1", ["Party \U0001F389 then the anchor text."],
                  {"kix.a": ["anchor text"]})],
            {"c1": "kix.a"},
        )
        assert _anchors(doc)["c1"]["text"] == "anchor text"

    def test_anchor_inside_a_table_cell(self):
        doc = _document(
            [_tab("t.1", ["Before the table."], {"kix.a": ["cell words"]},
                  table_cell="Some cell words.")],
            {"c1": "kix.a"},
        )
        assert _anchors(doc)["c1"]["text"] == "cell words"

    def test_anchor_on_an_image_is_attached_not_detached(self):
        tab = _tab("t.1", [], {})
        tab["documentTab"]["body"]["content"] = [{"paragraph": {"elements": [
            {"startIndex": 1, "inlineObjectElement": {"inlineObjectId": "i"}},
            {"startIndex": 2, "textRun": {"content": "\n"}},
        ]}}]
        tab["documentTab"]["commentAnchors"] = {"kix.a": {"ranges": [
            {"startIndex": 1, "endIndex": 2, "tabId": "t.1"},
        ]}}
        anchors = _anchors(_document([tab], {"c1": "kix.a"}))
        assert anchors["c1"] == {
            "text": "", "key": "", "occurrence": 0, "occurrences": 0,
        }
        result = annotate_markdown(
            "![](image.png)\n", [_comment("c1", "x")], anchors=anchors,
        )
        assert "[#c1 open] [attached, location not found]" in result

    def test_footnote_range_is_attached_without_body_text(self):
        tab = _tab("t.1", ["Body text here."], {})
        tab["documentTab"]["commentAnchors"] = {"kix.a": {"ranges": [
            {"startIndex": 1, "endIndex": 5, "tabId": "t.1",
             "segmentId": "kix.footnote1"},
        ]}}
        anchors = _anchors(_document([tab], {"c1": "kix.a"}))
        assert anchors["c1"]["text"] == ""

    def test_soft_line_break_ends_the_placement_text(self):
        doc = _document(
            [_tab("t.1", ["First line\x0bsecond line"],
                  {"kix.a": ["line\x0bsecond"]})],
            {"c1": "kix.a"},
        )
        assert _anchors(doc)["c1"]["key"] == "second"

    def test_request_uses_the_preview_view(self):
        session = MagicMock()
        session.get.return_value = _response(body=_document([], {}))
        with patch("gdoc.api.docs.account_cache_key",
                   return_value=("acct", None)), \
             patch("gdoc.auth.get_credentials", return_value="creds"), \
             patch("google.auth.transport.requests.AuthorizedSession",
                   return_value=session):
            assert get_comment_anchors("doc1") == ({}, "")
        params = session.get.call_args.kwargs["params"]
        assert params == {
            "includeTabsContent": "true",
            "suggestionsViewMode": "SUGGESTIONS_INLINE",
            "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
        }

    @pytest.mark.parametrize("resp", [
        _response(400, text='Unknown name "comments_view_mode": '
                            "Cannot find field."),
        _response(403, text="forbidden"),
        _response(200, body={"tabs": []}),
        _response(429, text="slow down", reason="Too Many Requests"),
        _response(503, text="backend", reason="Service Unavailable"),
        _response(400, text="Bad request"),
    ], ids=["not-enrolled", "no-comment-access", "field-not-applied",
            "rate-limited", "server-error", "other-400"])
    def test_unavailable_anchors_fall_back(self, resp):
        with patch("gdoc.api.docs._comments_view_get", return_value=resp):
            with pytest.raises(PreviewUnavailableError):
                get_comment_anchors("doc1")

    def test_network_failure_falls_back(self):
        from requests.exceptions import ConnectionError as ReqConnectionError

        with patch("gdoc.api.docs._comments_view_get",
                   side_effect=ReqConnectionError("dns failure")):
            with pytest.raises(PreviewUnavailableError, match="network"):
                get_comment_anchors("doc1")

    @pytest.mark.parametrize("status,error,match", [
        (401, AuthError, "Run `gdoc auth`"),
        (404, GdocError, "Document not found: doc1"),
    ])
    def test_other_errors_are_not_a_fallback(self, status, error, match):
        resp = _response(status, text="x")
        with patch("gdoc.api.docs._comments_view_get", return_value=resp):
            with pytest.raises(error, match=match) as exc:
                get_comment_anchors("doc1")
        assert not isinstance(exc.value, PreviewUnavailableError)


# -- probe scenarios -------------------------------------------------------

# After a full-tab rewrite: Docs detached both comments. The first one's
# quoted text still occurs, so quote matching would show it as attached.
REWRITE_MD = (
    "Alpha paragraph stays exactly the same.\n\n"
    "Bravo paragraph has fresh wording.\n"
)
REWRITE_DOC = _document(
    [_tab("t.1", ["Alpha paragraph stays exactly the same.",
                  "Bravo paragraph has fresh wording."], {})],
    {"survivor": "kix.1", "changed": "kix.2"},
)
REWRITE_COMMENTS = [
    _comment("survivor", "stays exactly the same"),
    _comment("changed", "has old wording"),
]

# After targeted edits. Quotes are what Drive still reports; the live
# anchors are what Docs keeps.
EDITS_MD = (
    "Golf has the quick red fox here.\n\n"
    "Hotel has fresh wording altogether.\n\n"
    "Juliet ends here paragraph starts here.\n\n"
    "Lima has an untouched comment.\n"
)
EDITS_DOC = _document(
    [_tab("t.1", [
        "Golf has the quick red fox here.",
        "Hotel has fresh wording altogether.",
        "Juliet ends here paragraph starts here.",
        "Lima has an untouched comment.",
    ], {
        "kix.reword": ["quick red fox"],
        "kix.join": ["ends here paragrap"],
        "kix.control": ["untouched comment"],
    })],
    {
        "reword": "kix.reword",      # wording changed inside the anchor
        "replaced": "kix.replaced",  # whole anchored text replaced
        "deleted": "kix.deleted",    # its paragraph deleted
        "join": "kix.join",          # paragraphs joined across the anchor
        "control": "kix.control",    # untouched
    },
)
EDITS_COMMENTS = [
    _comment("reword", "quick brown fox"),
    _comment("replaced", "old phrase entirely."),
    _comment("deleted", "India paragraph will be deleted"),
    _comment("join", "ends here.\n\nKilo paragrap"),
    _comment("control", "untouched comment"),
]


def _annotation_line(result: str, cid: str) -> int | None:
    """1-based content line an inline annotation follows (None if listed)."""
    last_line = None
    for line in result.split("\n"):
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            last_line = int(head)
        elif line.strip() == "[UNANCHORED]":
            last_line = None
        elif f"[#{cid} " in line:
            return last_line
    raise AssertionError(f"comment {cid} not in output")


class TestProbeScenarios:
    def test_rewrite_detaches_even_where_the_quote_survives(self):
        result = annotate_markdown(
            REWRITE_MD, REWRITE_COMMENTS, anchors=_anchors(REWRITE_DOC),
        )
        assert _annotation_line(result, "survivor") is None
        assert "[#survivor open] [detached]" in result
        assert "[#changed open] [detached]" in result

    def test_rewording_inside_the_anchor_keeps_it(self):
        result = annotate_markdown(
            EDITS_MD, EDITS_COMMENTS, anchors=_anchors(EDITS_DOC),
        )
        assert _annotation_line(result, "reword") == 1
        assert 'on "quick red fox"' in result

    def test_join_across_the_anchor_keeps_it(self):
        result = annotate_markdown(
            EDITS_MD, EDITS_COMMENTS, anchors=_anchors(EDITS_DOC),
        )
        assert _annotation_line(result, "join") == 5
        assert 'on "ends here paragrap"' in result

    def test_whole_anchor_replaced_detaches(self):
        result = annotate_markdown(
            EDITS_MD, EDITS_COMMENTS, anchors=_anchors(EDITS_DOC),
        )
        assert "[#replaced open] [detached]" in result

    def test_paragraph_deleted_detaches(self):
        result = annotate_markdown(
            EDITS_MD, EDITS_COMMENTS, anchors=_anchors(EDITS_DOC),
        )
        assert "[#deleted open] [detached]" in result

    def test_untouched_comment_stays_inline(self):
        result = annotate_markdown(
            EDITS_MD, EDITS_COMMENTS, anchors=_anchors(EDITS_DOC),
        )
        assert _annotation_line(result, "control") == 7

    def test_repeated_text_uses_the_anchors_own_occurrence(self):
        md = "Same words.\n\nSame words.\n"
        tab = _tab("t.1", ["Same words.", "Same words."], {})
        # The second paragraph starts at index 13 (1 + len("Same words.\n")).
        tab["documentTab"]["commentAnchors"] = {"kix.a": {"ranges": [
            {"startIndex": 13, "endIndex": 24, "tabId": "t.1"},
        ]}}
        doc = _document([tab], {"c1": "kix.a"})
        result = annotate_markdown(
            md, [_comment("c1", "Same")], anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 3

    def test_attached_but_not_in_the_markdown(self):
        # The export adds the tab title, so the markdown has one more
        # "Notes" than the document text and the occurrence can't be mapped.
        doc = _document(
            [_tab("t.1", ["Notes"], {"kix.a": ["Notes"]})],
            {"c1": "kix.a"},
        )
        result = annotate_markdown(
            "# Notes\n\nNotes\n", [_comment("c1", "Notes")],
            anchors=_anchors(doc),
        )
        assert "[#c1 open] [attached, location not found]" in result

    def test_formatted_anchor_is_found_and_link_targets_are_ignored(self):
        # "target" is bold in part; the only raw-markdown match is in a URL.
        md = "tar**get**\n\n[Read more](https://example.com/target)\n"
        doc = _document(
            [_tab("t.1", ["target", "Read more"], {"kix.a": ["target"]})],
            {"c1": "kix.a"},
        )
        result = annotate_markdown(
            md, [_comment("c1", "target")], anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 1

    def test_escaped_text_is_matched_unescaped(self):
        doc = _document(
            [_tab("t.1", ["Price is 5*3 today."], {"kix.a": ["5*3 today"]})],
            {"c1": "kix.a"},
        )
        result = annotate_markdown(
            "Price is 5\\*3 today.\n", [_comment("c1", "5*3 today")],
            anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 1

    @pytest.mark.parametrize("text,markdown", [
        ("user_id", "`user_id`"),
        ("5*3 [x](y) &amp;", "`5*3 [x](y) &amp;`"),
        ("foo_bar", "foo_bar"),
        ("5*3 today", "[5\\*3 today](https://example.com)"),
        ("bold and italic", "**bold** and _italic_"),
        ("~5 minutes", "~5 minutes"),
        ("Q&A <draft>", "Q&amp;A &lt;draft&gt;"),
    ], ids=["code-span", "code-span-literals", "intraword-underscore", "escape-in-link",
            "emphasis", "tilde", "html-entities"])
    def test_literal_characters_survive_the_visible_text(
        self, text, markdown,
    ):
        doc = _document(
            [_tab("t.1", [text], {"kix.a": [text]})], {"c1": "kix.a"},
        )
        result = annotate_markdown(
            markdown + "\n", [_comment("c1", text)], anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 1

    def test_paragraph_spanning_anchor_header_stays_on_one_line(self):
        doc = _document(
            [_tab("t.1", ["Hello world", "Next line"],
                  {"kix.a": ["Hello world\nNext"]})],
            {"c1": "kix.a"},
        )
        result = annotate_markdown(
            "Hello world\n\nNext line\n", [_comment("c1", "x")],
            anchors=_anchors(doc),
        )
        assert '[#c1 open] alice@example.com on "Hello world Next":' in result

    def test_anchor_across_a_footnote_reference_is_placed(self):
        # A footnote reference is its own element in the document (no
        # text run); the export writes it as a [^1] marker.
        tab = _tab("t.1", [], {})
        tab["documentTab"]["body"]["content"] = [{"paragraph": {"elements": [
            {"startIndex": 1, "textRun": {"content": "Claim here"}},
            {"startIndex": 11, "footnoteReference": {"footnoteId": "f1"}},
            {"startIndex": 12, "textRun": {"content": " continues.\n"}},
        ]}}]
        tab["documentTab"]["commentAnchors"] = {"kix.a": {"ranges": [
            {"startIndex": 1, "endIndex": 22, "tabId": "t.1"},
        ]}}
        anchors = _anchors(_document([tab], {"c1": "kix.a"}))
        assert anchors["c1"]["key"] == "Claim here continues"
        result = annotate_markdown(
            "Claim here[^1] continues.\n\n[^1]: A note.\n",
            [_comment("c1", "x")], anchors=anchors,
        )
        assert _annotation_line(result, "c1") == 1

    def test_fenced_code_is_literal(self):
        doc = _document(
            [_tab("t.1", ["x = a*b_c"], {"kix.a": ["x = a*b_c"]})],
            {"c1": "kix.a"},
        )
        result = annotate_markdown(
            "```py\nx = a*b_c\n```\n", [_comment("c1", "x")],
            anchors=_anchors(doc),
        )
        assert _annotation_line(result, "c1") == 2

    def test_visible_text_is_built_once_per_call(self):
        from gdoc import annotate

        anchors = _anchors(EDITS_DOC)
        with patch.object(
            annotate, "_visible_text", wraps=annotate._visible_text,
        ) as spy:
            annotate_markdown(EDITS_MD, EDITS_COMMENTS, anchors=anchors)
        assert spy.call_count == 1

    def test_comment_without_live_anchor_uses_its_quote(self):
        result = annotate_markdown(
            EDITS_MD, [_comment("api", "Lima has")], anchors={},
        )
        assert _annotation_line(result, "api") == 7
        assert "[#api open] [quoted text found]" in result


class TestQuoteFallback:
    """No preview access: every comment is placed by its quoted text."""

    def test_quote_found_is_labelled_a_guess(self):
        result = annotate_markdown(REWRITE_MD, REWRITE_COMMENTS)
        assert _annotation_line(result, "survivor") == 1
        assert "[#survivor open] [quoted text found]" in result

    def test_quote_not_found_is_not_called_deleted(self):
        result = annotate_markdown(EDITS_MD, EDITS_COMMENTS)
        assert (
            "[#reword open] [quoted text not found (edited or detached)]"
            in result
        )
        assert "anchor deleted" not in result


# -- CLI and MCP output ----------------------------------------------------


def _cat_args(**overrides):
    args = {
        "command": "cat", "doc": "doc1", "plain": False, "comments": True,
        "all": False, "tab": None, "all_tabs": False, "max_bytes": 0,
        "no_images": False, "json": False, "verbose": False, "quiet": True,
        "revision": None, "range": None,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


@pytest.fixture
def edited_doc(monkeypatch, doc_mime):
    """The targeted-edits scenario behind the API boundary."""
    monkeypatch.setattr("gdoc.notify.pre_flight", lambda *a, **k: None)
    monkeypatch.setattr(
        "gdoc.state.update_state_after_command", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "gdoc.api.drive.export_doc", lambda doc_id, mime_type: EDITS_MD,
    )
    monkeypatch.setattr(
        "gdoc.api.comments.list_comments", lambda *a, **k: EDITS_COMMENTS,
    )
    anchors = _anchors(EDITS_DOC)
    monkeypatch.setattr(
        "gdoc.api.docs.get_comment_anchors", lambda doc_id: (anchors, "r1"),
    )
    monkeypatch.setattr("gdoc.api.docs.get_revision_id", lambda doc_id: "r1")


def _no_preview(monkeypatch):
    def unavailable(doc_id):
        raise PreviewUnavailableError("not enrolled")

    monkeypatch.setattr("gdoc.api.docs.get_comment_anchors", unavailable)


class TestCatOutput:
    def test_terse(self, edited_doc, capsys):
        assert cmd_cat(_cat_args()) == 0
        captured = capsys.readouterr()
        assert _annotation_line(captured.out, "reword") == 1
        assert "[#deleted open] [detached]" in captured.out
        assert captured.err == ""

    def test_json(self, edited_doc, capsys):
        assert cmd_cat(_cat_args(json=True)) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["anchors"] == "live"
        assert _annotation_line(data["content"], "join") == 5
        assert "[#replaced open] [detached]" in data["content"]

    def test_fallback_warns_and_labels(self, edited_doc, monkeypatch, capsys):
        _no_preview(monkeypatch)
        assert cmd_cat(_cat_args()) == 0
        captured = capsys.readouterr()
        assert captured.err.startswith(
            "WARN: live comment anchors unavailable (not enrolled)"
        )
        assert "[#control open] [quoted text found]" in captured.out
        assert "[quoted text not found (edited or detached)]" in captured.out

    def test_no_comments_skips_the_anchor_read(
        self, edited_doc, monkeypatch, capsys,
    ):
        monkeypatch.setattr(
            "gdoc.api.comments.list_comments", lambda *a, **k: [],
        )
        _no_preview(monkeypatch)  # would WARN if it were called
        assert cmd_cat(_cat_args(json=True)) == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        assert json.loads(captured.out)["anchors"] == "live"

    def test_edit_during_the_read_is_retried(
        self, edited_doc, monkeypatch, capsys,
    ):
        revisions = iter(["r2", "r1"])  # changed once, then stable
        monkeypatch.setattr(
            "gdoc.api.docs.get_revision_id", lambda doc_id: next(revisions),
        )
        assert cmd_cat(_cat_args()) == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        assert _annotation_line(captured.out, "reword") == 1

    def test_document_that_keeps_changing_loses_locations_only(
        self, edited_doc, monkeypatch, capsys,
    ):
        monkeypatch.setattr(
            "gdoc.api.docs.get_revision_id", lambda doc_id: "r9",
        )
        assert cmd_cat(_cat_args()) == 0
        captured = capsys.readouterr()
        assert "WARN: the document changed while it was being read" in (
            captured.err
        )
        assert "[#reword open] [attached, location not found]" in captured.out
        assert "[#deleted open] [detached]" in captured.out

    def test_unconfirmed_revision_loses_locations_only(
        self, edited_doc, monkeypatch, capsys,
    ):
        def fails(doc_id):
            raise GdocError("API error (503): backend")

        monkeypatch.setattr("gdoc.api.docs.get_revision_id", fails)
        assert cmd_cat(_cat_args()) == 0
        captured = capsys.readouterr()
        assert "WARN: the document changed" in captured.err
        assert "[#control open] [attached, location not found]" in (
            captured.out
        )

    @pytest.mark.parametrize("error", [
        TimeoutError("read timed out"),
        __import__("httplib2").ServerNotFoundError("DNS failed"),
        __import__("google.auth.exceptions", fromlist=["x"]).TransportError(
            "refresh connection failed",
        ),
    ], ids=["timeout", "dns", "refresh-transport"])
    def test_revision_check_transport_failure_loses_locations_only(
        self, edited_doc, monkeypatch, capsys, error,
    ):
        from gdoc.api import docs as docs_api

        # The real revision check, with the Docs service failing under it.
        monkeypatch.setattr(docs_api, "get_revision_id", get_revision_id)
        service = MagicMock()
        service.documents.return_value.get.return_value.execute.side_effect = (
            error
        )
        monkeypatch.setattr(docs_api, "get_docs_service", lambda: service)
        assert cmd_cat(_cat_args()) == 0
        captured = capsys.readouterr()
        assert "WARN: the document changed" in captured.err
        assert "[#control open] [attached, location not found]" in (
            captured.out
        )

    def test_revision_check_refresh_failure_is_an_auth_error(self):
        from google.auth.exceptions import RefreshError

        service = MagicMock()
        service.documents.return_value.get.return_value.execute.side_effect = (
            RefreshError("invalid_grant")
        )
        with patch("gdoc.api.docs.get_docs_service", return_value=service):
            with pytest.raises(AuthError):
                get_revision_id("doc1")

    def test_fallback_json(self, edited_doc, monkeypatch, capsys):
        _no_preview(monkeypatch)
        assert cmd_cat(_cat_args(json=True)) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["anchors"] == "quoted_text"


class TestMcp:
    def test_same_output_as_the_cli(self, edited_doc, capsys):
        cmd_cat(_cat_args())
        cli_out = capsys.readouterr().out
        result = mcp.MCPServer().handle_tools_call({
            "name": "gdoc_cat",
            "arguments": {"doc": "doc1", "comments": True, "quiet": True},
        })
        assert result["isError"] is False
        assert result["content"][0]["text"] == cli_out.strip()

    def test_fallback_warning_reaches_the_client(
        self, edited_doc, monkeypatch,
    ):
        _no_preview(monkeypatch)
        result = mcp.MCPServer().handle_tools_call({
            "name": "gdoc_cat",
            "arguments": {"doc": "doc1", "comments": True, "quiet": True},
        })
        assert "live comment anchors unavailable" in result["content"][1]["text"]
