"""Synthetic target-style regressions; all Docs transport is mocked."""

from copy import deepcopy

import pytest

from gdoc.api.docs import find_text_in_document, replace_formatted, suggest_replacement
from gdoc.mdparse import utf16_len
from gdoc.util import GdocError

RED = {"foregroundColor": {"color": {"rgbColor": {"red": 0.7}}},
       "underline": False}
LINK = {"link": {"url": "https://example.test/old"}, **RED}


def _body(*runs, cell=False):
    elements = []
    index = 1
    for text, style in runs:
        end = index + utf16_len(text)
        elements.append({"startIndex": index, "endIndex": end,
                         "textRun": {"content": text, "textStyle": style}})
        index = end
    body = {"content": [{"startIndex": 1, "endIndex": index,
                         "paragraph": {"elements": elements}}]}
    if cell:
        return {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    return body


def _batch(mocker, body, old, new, command="edit", **kwargs):
    original = deepcopy(body)
    chain = mocker.patch("gdoc.api.docs.get_docs_service").return_value.documents()
    matches = find_text_in_document(None, old, body=body)
    assert len(matches) == 1
    if command == "suggest":
        mocker.patch("gdoc.api.docs.check_suggest_preview_access")
        mocker.patch("gdoc.api.docs._token_identity", return_value=("client", "token"))
        chain.batchUpdate.return_value.execute.return_value = {
            "commentUpdateState": "ALL_SAVED",
            "suggestionResponses": [{"createdSuggestionIds": ["synthetic"]}],
        }
        mocker.patch("gdoc.api.docs.get_document_structure", return_value={
            "body": {"content": [{"paragraph": {"elements": [{"textRun": {
                "content": "replacement", "suggestedInsertionIds": ["synthetic"],
            }}]}}]},
        })
        suggest_replacement("doc", matches, new, "revision", tab_id="tab",
                            body=body)
    else:
        replace_formatted("doc", matches, new, "revision", tab_id="tab",
                          body=body, **kwargs)
    chain.batchUpdate.assert_called_once()
    batch = chain.batchUpdate.call_args.kwargs["body"]
    assert batch["writeControl"] == {
        "requiredRevisionId": "revision",
        **({"writeMode": "SUGGEST"} if command == "suggest" else {}),
    }
    assert body == original
    return batch["requests"]


def _refused(mocker, body, old, new):
    with pytest.raises(GdocError, match="preserving its pending text style"):
        _batch(mocker, body, old, new, "suggest")
    from gdoc.api.docs import check_suggest_preview_access, get_docs_service
    check_suggest_preview_access.assert_not_called()
    get_docs_service.return_value.documents().batchUpdate.assert_not_called()


def _replacement_styles(requests, inherited):
    """Apply named-field masks to inserted UTF-16 units, including link resets.

    This models the request contract, not Google's suggestion backend.
    Reject out-of-insertion style ranges so original/neighbor runs stay intact.
    """
    insert = next(r["insertText"] for r in requests if "insertText" in r)
    start = insert["location"]["index"]
    styles = [deepcopy(inherited) for _ in range(utf16_len(insert["text"]))]
    for request in requests:
        if "updateTextStyle" not in request:
            continue
        update = request["updateTextStyle"]
        lo, hi = (update["range"][key] - start for key in ("startIndex", "endIndex"))
        if lo == len(styles) and hi == lo + 1:
            continue  # Retained paragraph mark is checked separately below.
        assert 0 <= lo < hi <= len(styles)
        assert update["range"]["tabId"] == "tab"
        for style in styles[lo:hi]:
            fields = update["fields"].split(",")
            if "link" in fields and "link" in update["textStyle"]:
                style.update({"underline": True, "foregroundColor": "link-default"})
            for field in fields:
                if field in update["textStyle"]:
                    style[field] = deepcopy(update["textStyle"][field])
                else:
                    style.pop(field, None)
    return styles


@pytest.mark.parametrize("command", ["edit", "suggest"])
@pytest.mark.parametrize("neighbour", [{"bold": True}, {"underline": True}])
@pytest.mark.parametrize("target", [{}, RED])
def test_target_overrides_neighbour(mocker, command, neighbour, target):
    body = _body(("Left ", neighbour), ("TOKEN", target), (" right\n", {}))
    requests = _batch(mocker, body, "TOKEN", "R🌿", command)
    inherited = target if command == "suggest" else neighbour
    assert _replacement_styles(requests, inherited) == [target] * 3


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_mixed_retained_phrase_preserves_colour_and_plain_suffix(mocker, command):
    body = _body(("Prefix ", {"bold": True}), ("Red label", LINK),
                 (" plus ordinary words.\n", {}))
    replacement = "[Red label](https://example.test/new) plus ordinary words."
    if command == "suggest":
        return _refused(mocker, body, "Red label plus ordinary words.", replacement)
    requests = _batch(mocker, body, "Red label plus ordinary words.",
                      replacement, command)
    styles = _replacement_styles(requests, {"bold": True})
    assert styles[:9] == [{**RED, "link": {"url": "https://example.test/new"}}] * 9
    assert styles[9:] == [{}] * len(" plus ordinary words.")


@pytest.mark.parametrize("command", ["edit", "suggest"])
@pytest.mark.parametrize("cell", [False, True])
def test_unrelated_plain_replacement_removes_stale_link(mocker, command, cell):
    body = _body(("Obsolete value", LINK), ("\n", LINK), cell=cell)
    if command == "suggest":
        return _refused(mocker, body, "Obsolete value", "Confirmed")
    requests = _batch(mocker, body, "Obsolete value", "Confirmed", command,
                      replace_paragraphs=cell)
    assert _replacement_styles(requests, LINK) == [RED] * len("Confirmed")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_mixed_unmappable_replacement_keeps_only_common_fields(mocker, command):
    body = _body(("L ", {"bold": True}), ("red label", LINK),
                 (" short", {"italic": True}), (" plain suffix\n", {}))
    requests = _batch(mocker, body, "red label short plain suffix", "Revised", command)
    inherited = {} if command == "suggest" else {"bold": True}
    assert _replacement_styles(requests, inherited) == [{}] * len("Revised")


@pytest.mark.parametrize("command", ["edit", "suggest"])
@pytest.mark.parametrize("style", [{}, {"bold": True}, RED])
def test_same_style_wording_needs_no_style_requests(mocker, command, style):
    body = _body(("A TOKEN Z\n", style))
    requests = _batch(mocker, body, "TOKEN", "Revised", command)
    assert [next(iter(r)) for r in requests] == ["deleteContentRange", "insertText"]


def _units(*runs):
    result = []
    for text, style in runs:
        raw = text.encode("utf-16-le")
        result.extend((raw[i:i + 2], deepcopy(style)) for i in range(0, len(raw), 2))
    return result


def _project_suggestion(runs, requests, view):
    """Small offline contract model: pending deletions retain their old style.

    Only inserted units may be restyled by this batch. This checks our ranges
    and projections, but cannot establish the preview server's behavior.
    """
    units = [(char, style, "original") for char, style in _units(*runs)]
    for request in requests:
        if "deleteContentRange" in request:
            span = request["deleteContentRange"]["range"]
            lo, hi = span["startIndex"] - 1, span["endIndex"] - 1
            units[lo:hi] = [(char, style, "deleted") for char, style, _ in units[lo:hi]]
        elif "insertText" in request:
            insert = request["insertText"]
            pos = insert["location"]["index"] - 1
            inherited = units[pos - 1][1] if pos else units[pos][1]
            units[pos:pos] = [(char, style, "inserted")
                              for char, style in _units((insert["text"], inherited))]
        elif "updateTextStyle" in request:
            update = request["updateTextStyle"]
            lo = update["range"]["startIndex"] - 1
            hi = update["range"]["endIndex"] - 1
            for _, style, status in units[lo:hi]:
                assert status == "inserted", "style request touched pre-existing text"
                # Proposed style changes do not alter the pending native style.
                if view != "accepted":
                    continue
                for field in update["fields"].split(","):
                    if field in update["textStyle"]:
                        style[field] = deepcopy(update["textStyle"][field])
                    else:
                        style.pop(field, None)
    hidden = {"pending": None, "accepted": "deleted", "rejected": "inserted"}[view]
    return [(char, style) for char, style, status in units if status != hidden]


@pytest.mark.parametrize("view", ["pending", "accepted", "rejected"])
@pytest.mark.parametrize("target", [{}, {"italic": True, **RED}])
def test_suggestion_projection_contract_preserves_target_and_neighbours(
    mocker, view, target,
):
    left = ("Bold 🌿 ", {"bold": True, "underline": True})
    old = ("TOKEN", target)
    right = (" right\n", {"strikethrough": True})
    runs = (left, old, right)
    requests = _batch(mocker, _body(*runs), "TOKEN", "R🌿", "suggest")
    new = ("R🌿", target)
    expected = {"pending": (left, old, new, right),
                "accepted": (left, new, right), "rejected": runs}
    assert _project_suggestion(runs, requests, view) == _units(*expected[view])


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_link_only_follows_retained_label_with_utf16_offsets(mocker, command):
    body = _body(("Left ", {}), ("Label🌿", LINK), (" right\n", {}))
    if command == "suggest":
        return _refused(mocker, body, "Label🌿", "🌿 New Label🌿 suffix")
    requests = _batch(mocker, body, "Label🌿", "🌿 New Label🌿 suffix", command)
    styles = _replacement_styles(requests, {})
    assert styles[:7] == [RED] * 7
    assert styles[7:14] == [LINK] * 7
    assert styles[14:] == [RED] * len(" suffix")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_explicit_emphasis_wins_after_target_baseline(mocker, command):
    body = _body(("Left ", {"bold": True}), ("TOKEN", RED), (" right\n", {}))
    requests = _batch(mocker, body, "TOKEN", "**R🌿**", command)
    inherited = RED if command == "suggest" else {"bold": True}
    assert _replacement_styles(requests, inherited) == [{**RED, "bold": True}] * 3


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_same_colour_neighbour_does_not_hide_markdown_link_reset(mocker, command):
    body = _body(("A TOKEN Z\n", RED))
    requests = _batch(mocker, body, "TOKEN", "[Revised](https://example.test/new)",
                      command)
    assert _replacement_styles(requests, RED) == [
        {**RED, "link": {"url": "https://example.test/new"}},
    ] * len("Revised")


def test_collapsing_cell_removes_link_but_preserves_target_colour(mocker):
    first = _body(("Old", LINK), ("\n", LINK))
    second = _body(("Value", LINK), ("\n", LINK))
    for element in second["content"]:
        element["startIndex"] += 4
        element["endIndex"] += 4
        for run in element["paragraph"]["elements"]:
            run["startIndex"] += 4
            run["endIndex"] += 4
    cell = {"content": first["content"] + second["content"]}
    body = {"content": [{"table": {"tableRows": [{"tableCells": [cell]}]}}]}
    requests = _batch(mocker, body, "Old\nValue", "Confirmed", replace_paragraphs=True)
    assert _replacement_styles(requests, LINK) == [RED] * len("Confirmed")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_duplicate_retained_label_does_not_guess_which_link_to_restore(mocker, command):
    body = _body(("A ", {}), ("Label", LINK), (" Z\n", {}))
    if command == "suggest":
        return _refused(mocker, body, "Label", "Label and Label")
    requests = _batch(mocker, body, "Label", "Label and Label", command)
    assert _replacement_styles(requests, {}) == [RED] * len("Label and Label")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_whole_paragraph_fence_uses_target_style_before_explicit_code_font(
    mocker, command,
):
    body = _body(("TOKEN", RED), ("\n", {}))
    requests = _batch(mocker, body, "TOKEN", "```\nRevised\n```", command)
    inherited = RED if command == "suggest" else {}
    assert _replacement_styles(requests, inherited) == [{
        **RED, "weightedFontFamily": {"fontFamily": "Courier New"},
    }] * len("Revised")


def test_cell_wording_keeps_its_bullet_and_restores_target_style(mocker):
    body = _body(("Old value", LINK), ("\n", LINK))
    paragraph = body["content"][0]["paragraph"]
    paragraph["bullet"] = {"listId": "synthetic-list"}
    body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    requests = _batch(mocker, body, "Old value", "Confirmed", replace_paragraphs=True)
    kinds = [next(iter(request)) for request in requests]
    assert "deleteParagraphBullets" not in kinds
    assert "updateParagraphStyle" not in kinds
    assert _replacement_styles(requests, LINK) == [RED] * len("Confirmed")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_changed_coloured_prefix_does_not_colour_surviving_plain_suffix(
    mocker, command,
):
    body = _body(("L ", {}), ("Red", RED), (" unchanged plain prose\n", {}))
    if command == "suggest":
        return _refused(mocker, body, "Red unchanged plain prose",
                        "Blue unchanged plain prose")
    requests = _batch(mocker, body, "Red unchanged plain prose",
                      "Blue unchanged plain prose", command)
    styles = _replacement_styles(requests, {})
    assert styles[:4] == [RED] * 4
    assert styles[4:] == [{}] * len(" unchanged plain prose")


@pytest.mark.parametrize("command", ["edit", "suggest"])
@pytest.mark.parametrize("label,old,new,linked", [
    # Replacing a whole label needs the label to survive, at word boundaries.
    ("art", "art", "party", ""),
    ("Anual report", "Anual report", "Annual report", ""),
    # An edit inside one link's label is a label edit, even a no-op one.
    ("Annual report", "report", "reporter", "reporter"),
    ("Annual report", "report", "report", "report"),
    ("Annual report", "Annual", "Yearly", "Yearly"),
])
def test_link_follows_label_edits_but_not_whole_replacements(
    mocker, command, label, old, new, linked,
):
    """F8: deterministic label edits; no link over unrelated replacements."""
    body = _body(("L ", {}), (label, LINK), (" right\n", {}))
    if command == "suggest":
        return _refused(mocker, body, old, new)
    requests = _batch(mocker, body, old, new, command)
    inherited = LINK if old != label else {}
    styles = _replacement_styles(requests, inherited)
    assert styles == ([LINK] * len(new) if linked else [RED] * len(new))


@pytest.mark.parametrize("old,new,linked", [
    ("report right", "report left", "report"),
    ("report right", "summary", ""),
])
def test_link_straddling_the_match_follows_its_surviving_words(mocker, old, new,
                                                               linked):
    """F8: only the linked words the match covers carry the link forward."""
    body = _body(("L ", {}), ("Annual report", LINK), (" right\n", {}))
    requests = _batch(mocker, body, old, new)
    styles = _replacement_styles(requests, LINK)
    assert [bool(style.get("link")) for style in styles] == [
        index < len(linked) for index in range(len(new))]


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_retained_link_crosses_nonlink_style_run_boundary(mocker, command):
    body = _body(("L ", {}), ("Annual ", LINK),
                 ("report", {**LINK, "bold": True}), (" right\n", {}))
    if command == "suggest":
        return _refused(mocker, body, "Annual report", "🌿 Annual report")
    requests = _batch(mocker, body, "Annual report", "🌿 Annual report", command)
    styles = _replacement_styles(requests, {})
    assert styles[3:10] == [LINK] * 7
    assert styles[10:] == [{**LINK, "bold": True}] * 6


@pytest.mark.parametrize("mark", [{}, {"italic": True}])
def test_whole_cell_restores_retained_mark_after_all_text_styles(mocker, mark):
    body = _body(("JD on Notion", LINK), ("\n", mark), cell=True)
    requests = _batch(mocker, body, "JD on Notion", "See recruiter instead",
                      replace_paragraphs=True)
    reset = requests[-1]["updateTextStyle"]
    assert reset["range"] == {"startIndex": 22, "endIndex": 23, "tabId": "tab"}
    assert reset["textStyle"] == mark
    assert set(reset["fields"].split(",")) == (
        {"foregroundColor", "link", "underline"} | mark.keys())
    assert _replacement_styles(requests, LINK) == [RED] * 21


@pytest.mark.parametrize("cell", [False, True])
@pytest.mark.parametrize("neighbour", [{"bold": True}, {"underline": True}])
@pytest.mark.parametrize("named_style", ["NORMAL_TEXT", "HEADING_1"])
def test_boundary_suggestion_inherits_target_before_deleting_it(
    mocker, cell, neighbour, named_style,
):
    runs = (("Boundary", neighbour), ("TARGET", {}), (" rest.\n", {}))
    body = _body(*runs)
    body["content"][0]["paragraph"]["paragraphStyle"] = {
        "namedStyleType": named_style,
    }
    if cell:
        body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    requests = _batch(mocker, body, "TARGET", "CHANGED", "suggest")
    assert requests == [
        {"insertText": {"location": {"index": 15, "tabId": "tab"},
                        "text": "CHANGED"}},
        {"deleteContentRange": {"range": {
            "startIndex": 9, "endIndex": 15, "tabId": "tab"}}},
    ]
    assert _project_suggestion(runs, requests, "pending") == _units(
        runs[0], runs[1], ("CHANGED", {}), runs[2])
    assert _project_suggestion(runs, requests, "accepted") == _units(
        runs[0], ("CHANGED", {}), runs[2])
    assert _project_suggestion(runs, requests, "rejected") == _units(*runs)


def test_projection_keeps_proposed_styles_separate_from_pending_native_style():
    runs = (("B", {"bold": True}), ("OLD", {}), (" rest\n", {}))
    requests = [
        {"deleteContentRange": {"range": {"startIndex": 2, "endIndex": 5}}},
        {"insertText": {"location": {"index": 2}, "text": "NEW"}},
        {"updateTextStyle": {"range": {"startIndex": 2, "endIndex": 5},
                             "textStyle": {}, "fields": "bold"}},
    ]
    assert _project_suggestion(runs, requests, "pending") == _units(
        runs[0], ("NEW", {"bold": True}), runs[1], runs[2])
    assert _project_suggestion(runs, requests, "accepted") == _units(
        runs[0], ("NEW", {}), runs[2])
    assert _project_suggestion(runs, requests, "rejected") == _units(*runs)


def test_late_mixed_suggestion_refuses_entire_batch_before_any_write(mocker):
    body = _body(("A", RED), (" plain", {}), (" and SECOND\n", {}))
    matches = find_text_in_document(None, "A plain", body=body)
    matches += find_text_in_document(None, "SECOND", body=body)
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    preview = mocker.patch("gdoc.api.docs.check_suggest_preview_access")
    with pytest.raises(GdocError, match="preserving its pending text style"):
        suggest_replacement("doc", matches, "A replaced", "revision", body=body)
    service.assert_not_called()
    preview.assert_not_called()


def test_link_retarget_preserves_trial_sentence_and_protected_suffix(mocker):
    trial_style = {**LINK, "italic": True, "strikethrough": True}
    body = _body(("The trial ", {}), ("ran for six months", trial_style),
                 (" in three regions. Keep this note.\n", {}))
    requests = _batch(mocker, body, "The trial ran for six months in three regions.",
                      "The trial [ran for six months](https://example.test/new) "
                      "across three regions.")
    styles = _replacement_styles(requests, {})
    assert styles[:10] == [{}] * 10
    assert styles[10:28] == [
        {**trial_style, "link": {"url": "https://example.test/new"}},
    ] * 18
    assert styles[28:] == [{}] * len(" across three regions.")
    # Only matched text is deleted; the protected suffix and LF stay native.
    assert requests[0]["deleteContentRange"]["range"]["endIndex"] == 47
    assert all(r["updateTextStyle"]["range"]["endIndex"] <= 51
               for r in requests if "updateTextStyle" in r)


@pytest.mark.parametrize("style", [{"bold": True}, RED])
@pytest.mark.parametrize("position", ["start", "middle", "end", "whole"])
@pytest.mark.parametrize("cell", [False, True])
def test_mixed_suggestion_checks_native_style_at_every_position(
    mocker, style, position, cell,
):
    left = (("Prefix ", {}),) if position in ("middle", "end") else ()
    right = ((" suffix\n", {}),) if position in ("start", "middle") else (("\n", {}),)
    old = (("Styled", style), (" plain", {}))
    runs = left + old + right
    requests = _batch(mocker, _body(*runs, cell=cell), "Styled plain", "R🌿", "suggest")
    new = (("R🌿", {}),)
    end_first = "insertText" in requests[0]
    if not left:
        # The EDIT baseline mask is empty here; the original first run still
        # supplies SUGGEST insertion style until we choose the target's end.
        assert end_first
    pending = left + (old + new if end_first else new + old) + right
    for view, expected in (("pending", pending), ("accepted", left + new + right),
                           ("rejected", runs)):
        assert _project_suggestion(runs, requests, view) == _units(*expected)
    assert not any("updateTextStyle" in request for request in requests)


@pytest.mark.parametrize("style", [{"bold": True}, RED])
@pytest.mark.parametrize("position", ["start", "middle", "end", "whole"])
def test_mixed_suggestion_refuses_when_neither_boundary_has_required_style(
    mocker, style, position,
):
    left = (("Prefix ", style),) if position in ("middle", "end") else ()
    right = ((" suffix\n", {}),) if position in ("start", "middle") else (("\n", {}),)
    runs = left + (("Styled", style), (" plain", {}), (" tail", style)) + right
    _refused(mocker, _body(*runs), "Styled plain tail", "Revised")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_partial_paragraph_fence_refuses_before_write(mocker, command):
    body = _body(("A ", {"bold": True}), ("TOKEN", RED), (" Z\n", {}))
    with pytest.raises(GdocError, match="paragraph count mismatch"):
        _batch(mocker, body, "TOKEN", "```\nRevised\n```", command)
    from gdoc.api.docs import get_docs_service
    get_docs_service.return_value.documents().batchUpdate.assert_not_called()
