"""Tests for get_document, find_text_in_document, replace_formatted."""

from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.docs import (
    find_text_in_document,
    get_document,
    replace_formatted,
)
from gdoc.util import AuthError, GdocError


def _mock_document(text_runs, revision_id="rev123"):
    """Build a minimal document dict for testing.

    text_runs is a list of (startIndex, content) tuples.
    """
    elements = []
    for start, content in text_runs:
        elements.append({
            "startIndex": start,
            "endIndex": start + len(content),
            "textRun": {"content": content},
        })
    return {
        "revisionId": revision_id,
        "body": {
            "content": [
                {"paragraph": {"elements": elements}},
            ]
        },
    }


def _mock_document_multi_para(paragraphs, revision_id="rev123"):
    """Build a document with multiple paragraphs.

    paragraphs: list of lists of (startIndex, content) tuples.
    """
    content = []
    for para_runs in paragraphs:
        elements = []
        for start, text in para_runs:
            elements.append({
                "startIndex": start,
                "endIndex": start + len(text),
                "textRun": {"content": text},
            })
        content.append({"paragraph": {"elements": elements}})
    return {
        "revisionId": revision_id,
        "body": {"content": content},
    }


def _docs_chain(mock_svc):
    """Shorthand for the mock service call chain."""
    return mock_svc.return_value.documents.return_value


class TestGetDocument:
    @patch("gdoc.api.docs.get_docs_service")
    def test_returns_document(self, mock_svc):
        doc = {"revisionId": "abc", "body": {"content": []}}
        chain = _docs_chain(mock_svc)
        chain.get.return_value.execute.return_value = doc
        result = get_document("doc123")
        assert result == doc
        chain.get.assert_called_once_with(documentId="doc123")

    @patch("gdoc.api.docs.get_docs_service")
    def test_translates_404(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock(status=404)
        chain = _docs_chain(mock_svc)
        chain.get.return_value.execute.side_effect = (
            HttpError(resp, b"not found")
        )
        with pytest.raises(GdocError, match="Document not found"):
            get_document("doc123")

    @patch("gdoc.api.docs.get_docs_service")
    def test_translates_401(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock(status=401)
        chain = _docs_chain(mock_svc)
        chain.get.return_value.execute.side_effect = (
            HttpError(resp, b"unauthorized")
        )
        with pytest.raises(AuthError, match="Authentication expired"):
            get_document("doc123")


class TestFindTextInDocument:
    def test_single_match(self):
        doc = _mock_document([(1, "hello world\n")])
        matches = find_text_in_document(doc, "hello")
        assert len(matches) == 1
        assert matches[0] == {"startIndex": 1, "endIndex": 6}

    def test_multiple_matches(self):
        doc = _mock_document([(1, "hello and hello\n")])
        matches = find_text_in_document(doc, "hello")
        assert len(matches) == 2
        assert matches[0]["startIndex"] == 1
        assert matches[1]["startIndex"] == 11

    def test_no_match(self):
        doc = _mock_document([(1, "hello world\n")])
        matches = find_text_in_document(doc, "zzz")
        assert matches == []

    def test_case_insensitive(self):
        doc = _mock_document([(1, "Hello World\n")])
        result = find_text_in_document(doc, "hello", match_case=False)
        assert len(result) == 1

    def test_case_sensitive_no_match(self):
        doc = _mock_document([(1, "Hello World\n")])
        result = find_text_in_document(doc, "hello", match_case=True)
        assert result == []

    def test_case_sensitive_match(self):
        doc = _mock_document([(1, "Hello World\n")])
        result = find_text_in_document(doc, "Hello", match_case=True)
        assert len(result) == 1

    def test_cross_textrun_match(self):
        """Text spans two textRun elements."""
        doc = _mock_document([(1, "hel"), (4, "lo world\n")])
        matches = find_text_in_document(doc, "hello")
        assert len(matches) == 1
        assert matches[0] == {"startIndex": 1, "endIndex": 6}

    def test_empty_document(self):
        doc = {"body": {"content": []}}
        assert find_text_in_document(doc, "anything") == []

    def test_multi_paragraph(self):
        doc = _mock_document_multi_para([
            [(1, "first paragraph\n")],
            [(18, "second paragraph\n")],
        ])
        matches = find_text_in_document(doc, "paragraph")
        assert len(matches) == 2


class TestReplaceFormatted:
    @patch("gdoc.api.docs.get_docs_service")
    def test_single_plain_replacement(self, mock_svc):
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 5, "endIndex": 10}]
        result = replace_formatted("d1", matches, "hello", "r1")
        assert result == 1
        call_args = chain.batchUpdate.call_args
        body = call_args[1]["body"]
        assert body["writeControl"]["requiredRevisionId"] == "r1"
        assert "deleteContentRange" in body["requests"][0]
        assert "insertText" in body["requests"][1]

    @patch("gdoc.api.docs.get_docs_service")
    def test_multi_match_order(self, mock_svc):
        """Matches processed last-to-first."""
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [
            {"startIndex": 5, "endIndex": 10},
            {"startIndex": 20, "endIndex": 25},
        ]
        result = replace_formatted("d1", matches, "x", "r1")
        assert result == 2
        body = chain.batchUpdate.call_args[1]["body"]
        first_del = body["requests"][0]
        assert first_del["deleteContentRange"]["range"]["startIndex"] == 20

    @patch("gdoc.api.docs.get_docs_service")
    def test_overlapping_matches_are_rejected(self, mock_svc):
        """`aa` in `aaa` matches [1,3) and [2,4): the last-to-first plan
        would land on shifted text, so refuse — same guard as suggest."""
        chain = _docs_chain(mock_svc)
        matches = [
            {"startIndex": 1, "endIndex": 3},
            {"startIndex": 2, "endIndex": 4},
        ]
        with pytest.raises(GdocError, match="overlap each other") as exc:
            replace_formatted("d1", matches, "b", "r1")
        assert exc.value.exit_code == 3
        chain.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_formatted_replacement(self, mock_svc):
        """Markdown generates style requests."""
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute = MagicMock()
        matches = [{"startIndex": 5, "endIndex": 10}]
        result = replace_formatted("d1", matches, "**bold**", "r1")
        assert result == 1
        body = chain.batchUpdate.call_args[1]["body"]
        req_types = [list(r.keys())[0] for r in body["requests"]]
        assert "deleteContentRange" in req_types
        assert "insertText" in req_types
        assert "updateTextStyle" in req_types

    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_matches_returns_zero(self, mock_svc):
        result = replace_formatted("d1", [], "text", "r1")
        assert result == 0
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.assert_not_called()

    @patch("gdoc.api.docs.get_docs_service")
    def test_translates_http_error(self, mock_svc):
        from googleapiclient.errors import HttpError
        resp = MagicMock(status=403)
        chain = _docs_chain(mock_svc)
        chain.batchUpdate.return_value.execute.side_effect = (
            HttpError(resp, b"forbidden")
        )
        matches = [{"startIndex": 5, "endIndex": 10}]
        with pytest.raises(GdocError, match="Permission denied"):
            replace_formatted("d1", matches, "text", "r1")


def _styled_body(prefix="Status: ", text="2. Archive the sample", left=None):
    start = 1 + len(prefix)
    elements = []
    if prefix:
        elements.append({"startIndex": 1, "endIndex": start,
                         "textRun": {"content": prefix, "textStyle": left or {}}})
    elements.append({"startIndex": start, "endIndex": start + len(text) + 1,
                     "textRun": {"content": text + "\n", "textStyle": {}}})
    return {"content": [{"startIndex": 1, "endIndex": start + len(text) + 1,
                         "paragraph": {"elements": elements, "paragraphStyle": {
                             "namedStyleType": "HEADING_2", "alignment": "END",
                         }}}]}


@pytest.mark.parametrize("replacement,inserted,baseline,extra", [
    ("closed", "closed", True, []),
    ("1. Archive the sample", "1. Archive the sample", False, []),
    ("# label", "# label", False, []),
    ("- note", "- note", False, []),
    ("**closed**", "closed", True, [({"bold": True}, "bold")]),
    # Partial replacements are inline Markdown only: a fence is a CommonMark
    # code span when closed by an equal backtick string, literal otherwise.
    ("``closed``", "closed", False,
     [({"weightedFontFamily": {"fontFamily": "Courier New"}}, "weightedFontFamily")]),
    ("``` closed", "``` closed", False, []),
])
def test_inline_exact_batch(mocker, replacement, inserted, baseline, extra):
    svc = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    chain = svc.documents.return_value
    body = _styled_body(left={"bold": True} if baseline else {})
    match = {"startIndex": 9, "endIndex": 30}
    target = {"startIndex": 9, "endIndex": 9 + len(inserted), "tabId": "tab-a"}
    requests = [
        {"deleteContentRange": {"range": {**match, "tabId": "tab-a"}}},
        {"insertText": {"location": {"index": 9, "tabId": "tab-a"}, "text": inserted}},
    ]
    if baseline:
        requests.append({"updateTextStyle": {
            "range": target, "textStyle": {}, "fields": "bold",
        }})
    requests.extend({"updateTextStyle": {
        "range": target, "textStyle": style, "fields": fields,
    }} for style, fields in extra)
    assert replace_formatted("sample-doc", [match], replacement, "rev-a",
                             tab_id="tab-a", body=body) == 1
    chain.batchUpdate.assert_called_once_with(documentId="sample-doc", body={
        "requests": requests, "writeControl": {"requiredRevisionId": "rev-a"},
    })


def _new_list(item, preset):
    """One level-0 item's list: a temporary separator and anchor fix its
    identity and level, and are removed right after the bullet request."""
    start, end = item["startIndex"], item["endIndex"]
    zero = {"magnitude": 0, "unit": "PT"}
    return [
        {"insertText": {"location": {"index": start}, "text": "\n\n"}},
        {"deleteParagraphBullets": {"range": {"startIndex": start,
                                              "endIndex": end + 2}}},
        {"updateParagraphStyle": {"range": {"startIndex": start, "endIndex": end + 2},
                                  "paragraphStyle": {"indentStart": zero,
                                                     "indentFirstLine": zero},
                                  "fields": "indentStart,indentFirstLine"}},
        {"createParagraphBullets": {"range": {"startIndex": start + 1,
                                              "endIndex": end + 2},
                                    "bulletPreset": preset}},
        {"deleteContentRange": {"range": {"startIndex": start,
                                          "endIndex": start + 2}}},
        {"updateParagraphStyle": {"range": item, "paragraphStyle": {
            "indentStart": {"magnitude": 36, "unit": "PT"},
            "indentFirstLine": {"magnitude": 18, "unit": "PT"}},
            "fields": "indentStart,indentFirstLine"}},
    ]


@pytest.mark.parametrize("replacement,inserted,structural", [
    ("New label", "New label", False),
    ("1. Archive the sample", "Archive the sample", True),
])
def test_complete_heading_exact_batch(mocker, replacement, inserted, structural):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    chain = service.documents.return_value
    chain.get.return_value.execute.return_value = {"body": {"content": []}}
    body = _styled_body(prefix="", text="Old label")
    target = {"startIndex": 1, "endIndex": 1 + len(inserted)}
    requests = [
        {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 10}}},
        {"insertText": {"location": {"index": 1}, "text": inserted}},
    ]
    if structural:
        requests.extend([
            {"updateParagraphStyle": {"range": target,
                                      "paragraphStyle": {
                                          "namedStyleType": "NORMAL_TEXT"},
                                      "fields": "namedStyleType"}},
            *_new_list(target, "NUMBERED_DECIMAL_ALPHA_ROMAN"),
        ])
    replace_formatted("sample-doc", [{"startIndex": 1, "endIndex": 10}],
                      replacement, "rev-a", body=body)
    chain.batchUpdate.assert_called_once_with(documentId="sample-doc", body={
        "requests": requests, "writeControl": {"requiredRevisionId": "rev-a"},
    })


@pytest.mark.parametrize("decor,fields", [
    ({}, "link"),
    ({"underline": True,
      "foregroundColor": {"color": {"rgbColor": {"red": 0.5}}}},
     "foregroundColor,link,underline"),
])
def test_edit_inside_a_link_label_keeps_the_link(mocker, decor, fields):
    # The complete link includes the left neighbour, outside the match, so the
    # match edits part of one link's label: the replacement stays in it.
    link = {"link": {"url": "https://example.com/spec"}, **decor}
    body = _styled_body(left=dict(link))
    body["content"][0]["paragraph"]["elements"][1]["textRun"]["textStyle"] = dict(link)
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    match = {"startIndex": 9, "endIndex": 30}
    replace_formatted("sample-doc", [match], "2. Archive the sample", "rev-a",
                      body=body)
    service.documents.return_value.batchUpdate.assert_called_once_with(
        documentId="sample-doc", body={
            "requests": [
                {"deleteContentRange": {"range": match}},
                {"insertText": {"location": {"index": 9},
                                "text": "2. Archive the sample"}},
                {"updateTextStyle": {
                    "range": {"startIndex": 9, "endIndex": 30},
                    "textStyle": link, "fields": fields,
                }},
            ],
            "writeControl": {"requiredRevisionId": "rev-a"},
        },
    )


@pytest.mark.parametrize("replacement,link", [
    ("[new](https://x.example) done", True),
    ("new done", False),
])
def test_shared_decorations_survive_replacement_link(mocker, replacement, link):
    # Decorations the target shares with its neighbour need no restore, but
    # a Markdown link in the replacement resets colour and underline, so the
    # target's values are reapplied after the link; without a link nothing
    # is sent.
    decor = {"underline": True,
             "foregroundColor": {"color": {"rgbColor": {"red": 0.5}}}}
    body = _styled_body(left=dict(decor))
    body["content"][0]["paragraph"]["elements"][1]["textRun"]["textStyle"] = dict(decor)
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    match = {"startIndex": 9, "endIndex": 30}
    replace_formatted("sample-doc", [match], replacement, "rev-a", body=body)
    requests = service.documents.return_value.batchUpdate.call_args.kwargs[
        "body"]["requests"]
    styles = [r["updateTextStyle"] for r in requests if "updateTextStyle" in r]
    if link:
        assert styles == [
            {"range": {"startIndex": 9, "endIndex": 12},
             "textStyle": {"link": {"url": "https://x.example"}}, "fields": "link"},
            {"range": {"startIndex": 9, "endIndex": 12}, "textStyle": decor,
             "fields": "foregroundColor,underline"},
        ]
    else:
        assert styles == []


def test_suggest_refuses_linked_homogeneous_target_before_service_access(mocker):
    # A proposed link reset cannot preserve the native pending insertion style.
    from gdoc.api.docs import suggest_replacement

    link = {"link": {"url": "https://example.com/spec"}}
    body = _styled_body(left=dict(link))
    body["content"][0]["paragraph"]["elements"][1]["textRun"]["textStyle"] = dict(link)
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    gate = mocker.patch("gdoc.api.docs.check_suggest_preview_access")
    match = {"startIndex": 9, "endIndex": 30}
    with pytest.raises(GdocError, match="preserving its pending text style") as error:
        suggest_replacement("sample-doc", [match], "done", "rev-a", body=body)
    assert error.value.exit_code == 3
    service.assert_not_called()
    gate.assert_not_called()


def test_inline_reapplies_link_decorations_after_replacement_link(mocker):
    # A Markdown link in the replacement sets only `link`, which resets colour
    # and underline to the link defaults, so the restored decorations must be
    # applied again after the parsed style requests.
    decor = {"underline": False,
             "foregroundColor": {"color": {"rgbColor": {"red": 1}}}}
    style = {"link": {"url": "https://example.com/old"}, **decor}
    body = _styled_body(left=dict(style))
    body["content"][0]["paragraph"]["elements"][1]["textRun"]["textStyle"] = dict(style)
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    match = {"startIndex": 9, "endIndex": 30}
    whole = {"startIndex": 9, "endIndex": 17, "tabId": "tab-a"}
    replace_formatted("sample-doc", [match], "[new](https://x.example) done",
                      "rev-a", tab_id="tab-a", body=body)
    service.documents.return_value.batchUpdate.assert_called_once_with(
        documentId="sample-doc", body={
            "requests": [
                {"deleteContentRange": {"range": {**match, "tabId": "tab-a"}}},
                {"insertText": {"location": {"index": 9, "tabId": "tab-a"},
                                "text": "new done"}},
                {"updateTextStyle": {
                    "range": whole, "textStyle": {}, "fields": "link",
                }},
                {"updateTextStyle": {
                    "range": {"startIndex": 9, "endIndex": 12, "tabId": "tab-a"},
                    "textStyle": {"link": {"url": "https://x.example"}},
                    "fields": "link",
                }},
                {"updateTextStyle": {
                    "range": {"startIndex": 9, "endIndex": 12, "tabId": "tab-a"},
                    "textStyle": decor, "fields": "foregroundColor,underline",
                }},
            ],
            "writeControl": {"requiredRevisionId": "rev-a"},
        },
    )


_TABLE_MD = "| H |\n|---|\n| x |"


def test_multiline_table_source_cannot_add_paragraphs_to_partial_matches(mocker):
    # Literal block syntax still cannot bypass native paragraph-count checks.
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    matches = [{"startIndex": 9, "endIndex": 11}, {"startIndex": 12, "endIndex": 19}]
    with pytest.raises(GdocError, match="paragraph count mismatch"):
        replace_formatted("sample-doc", matches, _TABLE_MD, "rev-a",
                          body=_styled_body())
    service.assert_not_called()


def test_table_replacement_rejected_for_multiple_block_matches(mocker):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    matches = [{"startIndex": 1, "endIndex": 4}, {"startIndex": 10, "endIndex": 14}]
    with pytest.raises(GdocError, match="tables not supported with --all") as exc:
        replace_formatted("sample-doc", matches, _TABLE_MD, "rev-a")
    assert exc.value.exit_code == 3
    service.documents.return_value.batchUpdate.assert_not_called()


def test_empty_whole_paragraph_replacement_keeps_final_newline(mocker):
    # Deleting heading text keeps its native mark and never starts cleanup.
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    chain = service.documents.return_value
    chain.get.return_value.execute.return_value = {"body": {"content": [{
        "startIndex": 1, "endIndex": 2, "paragraph": {
            "elements": [{"startIndex": 1, "endIndex": 2,
                          "textRun": {"content": "\n", "textStyle": {}}}],
            "paragraphStyle": {"namedStyleType": "HEADING_2"},
        },
    }]}}
    body = _styled_body(prefix="", text="Old label")
    assert replace_formatted("sample-doc", [{"startIndex": 1, "endIndex": 10}],
                             "", "rev-a", body=body) == 1
    assert chain.batchUpdate.call_args_list == [
        mocker.call(documentId="sample-doc", body={
            "requests": [{"deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": 10}}}],
            "writeControl": {"requiredRevisionId": "rev-a"},
        }),
    ]


@pytest.mark.parametrize("in_cell", [False, True])
def test_inline_restores_direct_fields_with_utf16_range(mocker, in_cell):
    body = _styled_body(left={"bold": True, "italic": True})
    body["content"][0]["paragraph"]["elements"][1]["textRun"]["textStyle"] = {
        "italic": True, "fontSize": {"magnitude": 22, "unit": "PT"},
    }
    if in_cell:
        body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    match = {"startIndex": 9, "endIndex": 30}
    replace_formatted("sample-doc", [match], "😀done", "rev-a", body=body)
    service.documents.return_value.batchUpdate.assert_called_once_with(
        documentId="sample-doc", body={
            "requests": [
                {"deleteContentRange": {"range": match}},
                {"insertText": {"location": {"index": 9}, "text": "😀done"}},
                {"updateTextStyle": {
                    "range": {"startIndex": 9, "endIndex": 15},
                    "textStyle": {"fontSize": {"magnitude": 22, "unit": "PT"}},
                    "fields": "bold,fontSize",
                }},
            ],
            "writeControl": {"requiredRevisionId": "rev-a"},
        },
    )


def test_all_can_mix_inline_and_complete_paragraphs(mocker):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    chain = service.documents.return_value
    chain.get.return_value.execute.return_value = {"body": {"content": []}}
    body = _styled_body(prefix="Status: ", text="old")
    body["content"].append({"paragraph": {"elements": [{
        "startIndex": 13, "endIndex": 17,
        "textRun": {"content": "old\n", "textStyle": {}},
    }]}})
    matches = [{"startIndex": 9, "endIndex": 12},
               {"startIndex": 13, "endIndex": 16}]
    replace_formatted("sample-doc", matches, "1. item", "rev-a", body=body)
    chain.batchUpdate.assert_called_once_with(documentId="sample-doc", body={
        "requests": [
            {"deleteContentRange": {"range": matches[1]}},
            {"insertText": {"location": {"index": 13}, "text": "item"}},
            {"updateParagraphStyle": {
                "range": {"startIndex": 13, "endIndex": 17},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }},
            *_new_list({"startIndex": 13, "endIndex": 17},
                       "NUMBERED_DECIMAL_ALPHA_ROMAN"),
            {"deleteContentRange": {"range": matches[0]}},
            {"insertText": {"location": {"index": 9}, "text": "1. item"}},
        ],
        "writeControl": {"requiredRevisionId": "rev-a"},
    })


def test_paragraph_start_restores_style_from_deleted_run(mocker):
    body = {"content": [{"paragraph": {"elements": [
        {"startIndex": 1, "endIndex": 4,
         "textRun": {"content": "Old", "textStyle": {"bold": True}}},
        {"startIndex": 4, "endIndex": 11,
         "textRun": {"content": " label\n", "textStyle": {}}},
    ]}}]}
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    match = {"startIndex": 1, "endIndex": 4}
    replace_formatted("sample-doc", [match], "New", "rev-a", body=body)
    service.documents.return_value.batchUpdate.assert_called_once_with(
        documentId="sample-doc", body={
            "requests": [
                {"deleteContentRange": {"range": match}},
                {"insertText": {"location": {"index": 1}, "text": "New"}},
                {"updateTextStyle": {"range": match, "textStyle": {"bold": True},
                                     "fields": "bold"}},
            ],
            "writeControl": {"requiredRevisionId": "rev-a"},
        },
    )


def _segment_scope():
    return {
        "tabProperties": {"tabId": "tab-one", "title": "First"},
        "documentTab": {
            "body": _mock_document([(1, "Body TOKEN\n")])["body"],
            "headers": {"header-one": {
                "headerId": "header-one",
                **_mock_document([(0, "Header TOKEN\n")])["body"],
            }},
            "footers": {"footer-one": {
                "footerId": "footer-one",
                **_mock_document([(0, "Footer TOKEN\n")])["body"],
            }},
            "footnotes": {"note-one": {
                "footnoteId": "note-one",
                **_mock_document([(0, "Footnote TOKEN\n")])["body"],
            }},
        },
    }


def test_find_all_selected_tab_containers():
    from gdoc.api.docs import flatten_tabs

    raw = _segment_scope()
    tab = flatten_tabs([raw])[0]
    for kind in ("headers", "footers", "footnotes"):
        assert tab[kind] == raw["documentTab"][kind]
    assert find_text_in_document(tab, "TOKEN") == [
        {"startIndex": 6, "endIndex": 11, "tabId": "tab-one",
         "container": "body"},
        {"startIndex": 7, "endIndex": 12, "tabId": "tab-one",
         "container": "header", "segmentId": "header-one"},
        {"startIndex": 7, "endIndex": 12, "tabId": "tab-one",
         "container": "footer", "segmentId": "footer-one"},
        {"startIndex": 9, "endIndex": 14, "tabId": "tab-one",
         "container": "footnote", "segmentId": "note-one"},
    ]


def test_find_raw_document_searches_every_tab():
    first = _segment_scope()
    sibling = _segment_scope()
    sibling["tabProperties"]["tabId"] = "tab-two"
    expected = find_text_in_document(
        {"id": "tab-one", **first["documentTab"]}, "TOKEN",
    )
    assert len(expected) == 4
    assert find_text_in_document({"tabs": [first, sibling]}, "TOKEN") == (
        expected + [{**m, "tabId": "tab-two"} for m in expected]
    )


def test_legacy_body_range_shape_is_unchanged():
    assert find_text_in_document(_mock_document([(1, "TOKEN")]), "TOKEN") == [
        {"startIndex": 1, "endIndex": 6},
    ]


def _mixed_matches():
    # Identical numerical ranges are independent across containers.
    return [
        {"startIndex": 1, "endIndex": 6, "tabId": "tab-one",
         "container": "footnote", "segmentId": "note-one"},
        {"startIndex": 1, "endIndex": 6, "tabId": "tab-one",
         "container": "header", "segmentId": "header-one"},
        {"startIndex": 1, "endIndex": 6, "tabId": "tab-one",
         "container": "body"},
    ]


def _expected_mixed_requests(body_paragraph=False):
    requests = []
    for segment in (None, "header-one", "note-one"):
        coordinates = {"tabId": "tab-one"}
        if segment:
            coordinates["segmentId"] = segment
        requests.extend([
            {"deleteContentRange": {"range": {
                "startIndex": 1, "endIndex": 6, **coordinates,
            }}},
            {"insertText": {"location": {"index": 1, **coordinates},
                            "text": "REPLACED"}},
        ])
        if body_paragraph and segment is None:
            requests.append({"updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 9, **coordinates},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }})
        requests.extend([
            {"updateTextStyle": {
                "range": {"startIndex": 1, "endIndex": 9, **coordinates},
                "textStyle": {"bold": True}, "fields": "bold",
            }},
            {"updateTextStyle": {
                "range": {"startIndex": 1, "endIndex": 9, **coordinates},
                "textStyle": {"italic": True}, "fields": "italic",
            }},
        ])
    return requests


def test_segment_replacement_builder_exact_requests():
    from gdoc.api.docs import _build_replacement_requests
    from gdoc.mdparse import ParsedMarkdown, StyleRange

    parsed = ParsedMarkdown("REPLACED", styles=[
        StyleRange(0, 8, {"bold": True}, "text_style"),
        StyleRange(0, 8, {"italic": True}, "text_style"),
    ])
    ordered, requests = _build_replacement_requests(parsed, _mixed_matches())
    assert [m["container"] for m in ordered] == ["body", "header", "footnote"]
    assert requests == _expected_mixed_requests()


def test_segment_edit_exact_batch(mocker):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    chain = service.documents.return_value
    assert replace_formatted(
        "doc-one", _mixed_matches(), "***REPLACED***", "revision-one",
        tab_id="tab-one",
    ) == 3
    chain.batchUpdate.assert_called_once_with(documentId="doc-one", body={
        "requests": _expected_mixed_requests(body_paragraph=True),
        "writeControl": {"requiredRevisionId": "revision-one"},
    })


@pytest.mark.parametrize("markdown", [
    "| A |\n| --- |\n| B |", "```\ncode\n```", "First\n\nSecond",
])
def test_segment_structural_markdown_rejected_before_batch(mocker, markdown):
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    with pytest.raises(GdocError) as error:
        replace_formatted("doc-one", [_mixed_matches()[0]], markdown, "revision-one")
    assert error.value.exit_code == 3
    service.assert_not_called()


def test_segment_sort_descends_only_inside_each_container():
    from gdoc.api.docs import _build_replacement_requests
    from gdoc.mdparse import ParsedMarkdown

    matches = _mixed_matches()
    matches.extend([
        {**matches[1], "startIndex": 20, "endIndex": 25},
        {**matches[2], "startIndex": 10, "endIndex": 15},
    ])
    ordered, _ = _build_replacement_requests(ParsedMarkdown("R"), matches)
    assert [(m["container"], m["startIndex"]) for m in ordered] == [
        ("body", 10), ("body", 1), ("header", 20), ("header", 1),
        ("footnote", 1),
    ]


@pytest.mark.parametrize("mode", ["edit", "suggest"])
def test_overlapping_matches_inside_one_segment_still_rejected(mocker, mode):
    from gdoc.api.docs import suggest_replacement

    service = mocker.patch("gdoc.api.docs.get_docs_service")
    match = _mixed_matches()[1]
    matches = [match, {**match, "startIndex": 3, "endIndex": 8}]
    replace = replace_formatted if mode == "edit" else suggest_replacement
    with pytest.raises(GdocError, match="overlap each other") as error:
        replace("doc-one", matches, "R", "revision-one")
    assert error.value.exit_code == 3
    service.assert_not_called()


def test_non_body_only_edit_does_not_read_for_cleanup(mocker):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    tabs = mocker.patch("gdoc.api.docs.get_document_with_tabs")
    assert replace_formatted(
        "doc-one", [_mixed_matches()[1]], "REPLACED", "revision-one",
        tab_id="tab-one",
    ) == 1
    tabs.assert_not_called()
    service.documents.return_value.get.assert_not_called()


@pytest.mark.parametrize("markdown", [
    "Text\n\n", "\n", "a\nb",
])
@pytest.mark.parametrize("mode", ["edit", "suggest"])
def test_non_body_rejects_paragraph_breaks_and_empty_renderings(
    mocker, markdown, mode,
):
    from gdoc.api.docs import suggest_replacement

    replace = replace_formatted if mode == "edit" else suggest_replacement
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    with pytest.raises(GdocError) as error:
        replace("doc-one", [_mixed_matches()[1]], markdown, "revision-one")
    assert error.value.exit_code == 3
    service.assert_not_called()


@pytest.mark.parametrize("markdown,inserted,code_font", [
    # A single line can never be a fenced block: a closed backtick string is
    # an inline code span and an unmatched or indented one stays literal.
    ("```code``` after", "code after", True),
    ("``` not closed", "``` not closed", False),
    ("    ```", "    ```", False),
])
def test_non_body_accepts_single_line_backtick_strings(
    mocker, markdown, inserted, code_font,
):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    assert replace_formatted(
        "doc-one", [_mixed_matches()[1]], markdown, "revision-one",
    ) == 1
    requests = service.documents.return_value.batchUpdate.call_args.kwargs[
        "body"]["requests"]
    assert [r["insertText"]["text"] for r in requests if "insertText" in r] == [
        inserted,
    ]
    fonts = [r["updateTextStyle"]["textStyle"].get("weightedFontFamily")
             for r in requests if "updateTextStyle" in r]
    assert ({"fontFamily": "Courier New"} in fonts) is code_font


def test_edit_uses_match_tab_when_no_fallback_tab_is_given(mocker):
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    assert replace_formatted(
        "doc-one", _mixed_matches(), "REPLACED", "revision-one",
    ) == 3
    service.documents.return_value.get.assert_not_called()


def test_wording_edit_does_not_restore_cleanup_readback(mocker):
    """PR #60 removed speculative cleanup; wording edits need no readback."""
    resource = mocker.patch(
        "gdoc.api.docs.get_docs_service",
    ).return_value.documents.return_value
    resource.get.return_value.execute.return_value = {"body": {"content": []}}

    assert replace_formatted(
        "sample-doc", [{"startIndex": 1, "endIndex": 5}], "pear", "sample-revision",
    ) == 1

    resource.batchUpdate.assert_called_once_with(
        documentId="sample-doc",
        body={
            "requests": [
                {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 5}}},
                {"insertText": {"location": {"index": 1}, "text": "pear"}},
                {"updateParagraphStyle": {
                    "range": {"startIndex": 1, "endIndex": 5},
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "fields": "namedStyleType",
                }},
            ],
            "writeControl": {"requiredRevisionId": "sample-revision"},
        },
    )
    resource.batchUpdate.return_value.execute.assert_called_once_with()
    resource.get.assert_not_called()


@pytest.mark.parametrize("tab_id", [None, "tab-one"])
@pytest.mark.parametrize("disconnects", [1, 3])
def test_staged_read_retries_without_replaying_completed_batch(
    mocker, tab_id, disconnects,
):
    """PR #70 staged reads inherit #64 retries without replaying a write."""
    import json
    from http.client import RemoteDisconnected

    import httplib2
    from googleapiclient.http import HttpRequest

    from gdoc.api.docs import _StagedWrite

    resource = mocker.patch(
        "gdoc.api.docs.get_docs_service",
    ).return_value.documents.return_value
    document = {"revisionId": "revision-two", "tabs": []}
    resource.batchUpdate.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": "revision-two"},
    }
    transport = mocker.Mock()
    transport.request.side_effect = [
        RemoteDisconnected("response lost") for _ in range(disconnects)
    ] + [(httplib2.Response({"status": "200"}), json.dumps(document).encode())]
    request = HttpRequest(
        transport, lambda response, content: json.loads(content),
        "https://example.invalid/document", method="GET",
    )
    mocker.patch.object(request, "_sleep")
    resource.get.return_value = request

    def write_then_read():
        with _StagedWrite("sample-doc") as progress:
            progress.batch("text written", [{"insertText": {}}], "revision-one")
            return progress.read("reading inserted cells", tab_id)

    if disconnects == 3:
        with pytest.raises(
            GdocError, match="Partial completion: applied: text written",
        ):
            write_then_read()
    else:
        assert write_then_read() == document
    assert transport.request.call_count == min(disconnects + 1, 3)
    resource.get.assert_called_once_with(
        documentId="sample-doc", **({"includeTabsContent": True} if tab_id else {}),
    )
    resource.batchUpdate.assert_called_once()
    resource.batchUpdate.return_value.execute.assert_called_once_with()
