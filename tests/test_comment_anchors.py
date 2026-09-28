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
from gdoc.api.docs import get_comment_anchors
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
        return get_comment_anchors("doc1")


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
        assert (anchor["occurrence"], anchor["occurrences"]) == (2, 3)

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

    def test_request_uses_the_preview_view(self):
        session = MagicMock()
        session.get.return_value = _response(body=_document([], {}))
        with patch("gdoc.api.docs.account_cache_key",
                   return_value=("acct", None)), \
             patch("gdoc.auth.get_credentials", return_value="creds"), \
             patch("google.auth.transport.requests.AuthorizedSession",
                   return_value=session):
            assert get_comment_anchors("doc1") == {}
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
    ], ids=["not-enrolled", "no-comment-access", "field-not-applied"])
    def test_unavailable_preview(self, resp):
        with patch("gdoc.api.docs._comments_view_get", return_value=resp):
            with pytest.raises(PreviewUnavailableError):
                get_comment_anchors("doc1")

    @pytest.mark.parametrize("status,error,match", [
        (401, AuthError, "Run `gdoc auth`"),
        (404, GdocError, "Document not found: doc1"),
        (503, GdocError, r"API error \(503\)"),
    ])
    def test_other_errors_are_not_a_fallback(self, status, error, match):
        resp = _response(status, text="x", reason="Service Unavailable")
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
        # Markdown escapes the asterisk, so the live text isn't found.
        doc = _document(
            [_tab("t.1", ["Price is 5*3 today."], {"kix.a": ["5*3 today"]})],
            {"c1": "kix.a"},
        )
        result = annotate_markdown(
            "Price is 5\\*3 today.\n", [_comment("c1", "5*3 today")],
            anchors=_anchors(doc),
        )
        assert "[#c1 open] [attached, location not found]" in result

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
        "gdoc.api.docs.get_comment_anchors", lambda doc_id: anchors,
    )


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
