"""Offline paragraph-boundary regressions using synthetic Docs API bodies."""

from copy import deepcopy
from io import StringIO
from types import SimpleNamespace

import pytest

from gdoc.api.docs import (
    find_text_in_document,
    insert_markdown_into_tab,
    replace_formatted,
)
from gdoc.cli import _resolve_replacement_text
from gdoc.mdparse import parse_inline, utf16_len
from gdoc.util import GdocError


def _body(*paragraphs):
    """Build native paragraph ranges with optional list membership."""
    content = []
    index = 1
    for text, style, bullet in paragraphs:
        end = index + utf16_len(text + "\n")
        paragraph = {
            "elements": [{"startIndex": index, "endIndex": end,
                          "textRun": {"content": text + "\n"}}],
            "paragraphStyle": {"namedStyleType": style, "alignment": "CENTER"},
        }
        if bullet:
            paragraph["bullet"] = {"listId": "synthetic-list", "nestingLevel": 2}
        content.append({"startIndex": index, "endIndex": end,
                        "paragraph": paragraph})
        index = end
    return {"content": content}


def _requests(mocker, body, old, new, **kwargs):
    """Run the real search and edit planner with only the service mocked."""
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    matches = find_text_in_document(None, old, body=body)
    assert matches
    result = replace_formatted("synthetic-doc", matches, new, "synthetic-rev",
                               tab_id="synthetic-tab", body=body, **kwargs)
    assert result == len(matches)
    chain = service.documents.return_value
    chain.get.assert_not_called()
    chain.batchUpdate.assert_called_once()
    batch = chain.batchUpdate.call_args.kwargs["body"]
    assert batch["writeControl"] == {"requiredRevisionId": "synthetic-rev"}
    return batch["requests"]


@pytest.mark.parametrize("source", ["positional", "old-stdin", "new-stdin", "files"])
@pytest.mark.parametrize("terminator", ["", "\n", "\n\n"])
def test_replacement_transport_strips_exactly_one_newline(
    mocker, tmp_path, source, terminator,
):
    """Edit and suggest share one resolver; transport cannot change its text."""
    old, new = "Original" + terminator, "Revised" + terminator
    args = SimpleNamespace(old_text=old, new_text=new)
    if source == "old-stdin":
        args.old_text = "-"
        mocker.patch("sys.stdin", StringIO(old))
    elif source == "new-stdin":
        args.new_text = "-"
        mocker.patch("sys.stdin", StringIO(new))
    elif source == "files":
        old_file, new_file = tmp_path / "old.md", tmp_path / "new.md"
        old_file.write_text(old)
        new_file.write_text(new)
        args.old_file, args.new_file = str(old_file), str(new_file)
    assert _resolve_replacement_text(args, None) == (
        old.removesuffix("\n"), new.removesuffix("\n"),
    )


@pytest.mark.parametrize("styles", [
    ("NORMAL_TEXT", "HEADING_2", "NORMAL_TEXT"),
    ("TITLE", "SUBTITLE", "HEADING_1"),
    ("NORMAL_TEXT", "NORMAL_TEXT", "NORMAL_TEXT"),
])
@pytest.mark.parametrize("in_cell", [False, True])
def test_multiline_wording_preserves_each_paragraph_mark(mocker, styles, in_cell):
    """Different named styles and nested list IDs survive without restyling."""
    body = _body(("Alpha", styles[0], False), ("Beta", styles[1], True),
                 ("Gamma", styles[2], False))
    if in_cell:
        body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    original = deepcopy(body)
    requests = _requests(mocker, body, "Alpha\nBeta\nGamma",
                         "😀Long alpha\nRevised beta\nShort")
    assert body == original
    assert [next(iter(req)) for req in requests] == [
        "deleteContentRange", "insertText",
        "deleteContentRange", "insertText",
        "deleteContentRange", "insertText",
    ]
    assert [req["deleteContentRange"]["range"] for req in requests[::2]] == [
        {"startIndex": 12, "endIndex": 17, "tabId": "synthetic-tab"},
        {"startIndex": 7, "endIndex": 11, "tabId": "synthetic-tab"},
        {"startIndex": 1, "endIndex": 6, "tabId": "synthetic-tab"},
    ]
    assert [req["insertText"]["text"] for req in requests[1::2]] == [
        "Short", "Revised beta", "😀Long alpha",
    ]


def test_partial_multiline_context_keeps_prefix_suffix_and_marks(mocker):
    """A context spanning partial endpoints never deletes either separator."""
    body = _body(("Prefix alpha", "TITLE", False),
                 ("beta suffix", "SUBTITLE", True))
    requests = _requests(mocker, body, "alpha\nbeta", "longer alpha\nshort")
    assert [req["deleteContentRange"]["range"] for req in requests[::2]] == [
        {"startIndex": 14, "endIndex": 18, "tabId": "synthetic-tab"},
        {"startIndex": 8, "endIndex": 13, "tabId": "synthetic-tab"},
    ]
    assert not any("updateParagraphStyle" in req for req in requests)


def test_extra_old_file_newline_does_not_consume_next_paragraph(mocker):
    """Even an explicitly matched terminal mark belongs to its paragraph."""
    body = _body(("Label", "HEADING_2", False), ("Neighbor", "NORMAL_TEXT", False))
    requests = _requests(mocker, body, "Label\n", "Renamed")
    assert requests == [
        {"deleteContentRange": {"range": {
            "startIndex": 1, "endIndex": 6, "tabId": "synthetic-tab",
        }}},
        {"insertText": {"location": {"index": 1, "tabId": "synthetic-tab"},
                        "text": "Renamed"}},
    ]


def test_multiline_explicit_heading_changes_only_that_paragraph(mocker):
    """Explicit heading Markdown clears that paragraph's old bullet only."""
    body = _body(("Alpha", "TITLE", False), ("Beta", "NORMAL_TEXT", True))
    requests = _requests(mocker, body, "Alpha\nBeta", "Revised\n## Heading")
    styles = [req["updateParagraphStyle"] for req in requests
              if "updateParagraphStyle" in req]
    assert len(styles) == 1
    assert styles[0]["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    assert styles[0]["paragraphStyle"]["indentStart"]["magnitude"] == 0
    assert styles[0]["range"] == {
        "startIndex": 7, "endIndex": 14, "tabId": "synthetic-tab",
    }
    bullets = [req["deleteParagraphBullets"]["range"] for req in requests
               if "deleteParagraphBullets" in req]
    assert bullets == [styles[0]["range"]]


def test_multiline_count_mismatch_refuses_before_service_access(mocker):
    """Ambiguous paragraph removal cannot silently merge a heading or list."""
    body = _body(("Alpha", "TITLE", False), ("Beta", "SUBTITLE", True))
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    matches = find_text_in_document(None, "Alpha\nBeta", body=body)
    with pytest.raises(GdocError, match="paragraph count mismatch") as exc:
        replace_formatted("synthetic-doc", matches, "Combined", "rev", body=body)
    assert exc.value.exit_code == 3
    service.assert_not_called()


def _bullet_resets(requests):
    """Bullet removals outside the list compiler, whose own removal follows
    the temporary separator and anchor it inserts."""
    return [r for i, r in enumerate(requests) if "deleteParagraphBullets" in r
            and not (i and requests[i - 1].get("insertText", {}).get("text") == "\n\n")]


@pytest.mark.parametrize("new", ["Plain", "## Heading", "", "- item"])
def test_cell_replacement_removes_inherited_list_unless_requested(mocker, new):
    """Whole-cell prose removes lists; list Markdown recreates membership."""
    body = _body(("Old", "NORMAL_TEXT", True))
    body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    requests = _requests(mocker, body, "Old", new, replace_paragraphs=True)
    assert len(_bullet_resets(requests)) == 1
    if new == "- item":
        assert any("createParagraphBullets" in req for req in requests)
    else:
        assert not any("createParagraphBullets" in req for req in requests)
    # Deletions after a bullet request remove the list's temporary paragraphs.
    assert all(req["deleteContentRange"]["range"]["endIndex"] == 4
               for i, req in enumerate(requests) if "deleteContentRange" in req
               and not (i and "createParagraphBullets" in requests[i - 1]))


@pytest.mark.parametrize("neighbor", ["normal", "list", "table", "image"])
@pytest.mark.parametrize("last", [False, True])
def test_empty_heading_does_not_mutate_neighbor(mocker, neighbor, last):
    """Heading deletion never promotes a neighbor or deletes the final mark."""
    body = _body(("Neighbor", "NORMAL_TEXT", neighbor == "list"),
                 ("Heading", "HEADING_2", False))
    if neighbor == "table":
        body["content"][0] = {"startIndex": 1, "endIndex": 10, "table": {}}
    elif neighbor == "image":
        body["content"][0]["paragraph"]["elements"].insert(0, {
            "startIndex": 1, "endIndex": 2,
            "inlineObjectElement": {"inlineObjectId": "synthetic-image"},
        })
    if not last:
        following = _body(("Following", "NORMAL_TEXT", False))["content"][0]
        following["startIndex"], following["endIndex"] = 18, 28
        following["paragraph"]["elements"][0].update(startIndex=18, endIndex=28)
        body["content"].append(following)
    if last and neighbor == "table":
        service = mocker.patch("gdoc.api.docs.get_docs_service")
        with pytest.raises(GdocError, match="mandatory final paragraph after a table"):
            matches = find_text_in_document(None, "Heading", body=body)
            replace_formatted("synthetic-doc", matches, "", "synthetic-rev",
                              tab_id="synthetic-tab", body=body)
        service.assert_not_called()
        return
    requests = _requests(mocker, body, "Heading", "")

    def deleted(start, end):
        return {"deleteContentRange": {"range": {
            "startIndex": start, "endIndex": end, "tabId": "synthetic-tab"}}}

    # A non-final heading is emptied, then its one empty paragraph removed,
    # the merge shape observed to keep the following paragraph's style.
    deletion = [deleted(9, 17)] if last else [deleted(10, 17), deleted(10, 11)]
    # A final heading's text moves onto the neighbor's mark's paragraph:
    # Docs keeps the style and list of the paragraph where the deletion
    # starts (observed live), so nothing is restored, even for a list item.
    assert requests == deletion


@pytest.mark.parametrize("position", ["start", "end"])
@pytest.mark.parametrize("markdown", ["Inserted", "Inserted\n", "😀Inserted"])
@pytest.mark.parametrize("bullet", [False, True])
def test_insertion_styles_only_new_paragraphs(mocker, position, markdown, bullet):
    """Start/end insertion separates old headings and never styles their marks."""
    body = _body(("Existing", "HEADING_1", bullet))
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "synthetic-rev", "tabs": [{
            "documentTab": {"body": body},
            "tabProperties": {"tabId": "synthetic-tab", "title": "Notes"},
        }],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("synthetic-doc", "Notes", markdown, position=position)
    requests = service.documents.return_value.batchUpdate.call_args.kwargs[
        "body"]["requests"]
    inserts = [req["insertText"] for req in requests if "insertText" in req]
    text = markdown.removesuffix("\n")
    if position == "start":
        assert inserts == [{"location": {"index": 1, "tabId": "synthetic-tab"},
                            "text": text + "\n"}]
        lower, upper = 1, 1 + utf16_len(text + "\n")
    else:
        assert inserts == [
            {"location": {"index": 9, "tabId": "synthetic-tab"}, "text": "\n"},
            {"location": {"index": 10, "tabId": "synthetic-tab"}, "text": text},
        ]
        lower, upper = 10, 10 + utf16_len(text)
    for req in requests:
        if "insertText" in req:
            continue
        target = next(iter(req.values()))["range"]
        assert lower <= target["startIndex"] < target["endIndex"] <= upper


def test_code_span_closer_ignores_backslash_inside_span():
    """Escapes outside code must not hide a raw backtick closer inside code."""
    text, styles = parse_inline("`a\\`b`")
    assert text == "a\\b`"
    assert [(s.start, s.end, s.style) for s in styles] == [
        (0, 2, {"weightedFontFamily": {"fontFamily": "Courier New"}}),
    ]


@pytest.mark.parametrize("markdown,expected", [
    ("```unclosed", "```unclosed"),
    ("```code```", "code"),
])
def test_single_paragraph_never_uses_block_renderer(mocker, markdown, expected):
    """Whole-paragraph code spans and table source stay on the inline path."""
    body = _body(("Original", "HEADING_2", False))
    table = mocker.patch("gdoc.api.docs._insert_table")
    requests = _requests(mocker, body, "Original", markdown)
    assert requests[1]["insertText"]["text"] == expected
    assert not any("updateParagraphStyle" in req for req in requests)
    table.assert_not_called()


def test_multiple_multiline_matches_keep_reverse_order_and_occurrence_count(mocker):
    """--all counts original matches while each paragraph edit uses old indexes."""
    body = _body(("Alpha", "TITLE", False), ("Beta", "SUBTITLE", True),
                 ("Alpha", "HEADING_1", False), ("Beta", "HEADING_2", True))
    requests = _requests(mocker, body, "Alpha\nBeta", "Longer alpha\nShort")
    starts = [req["deleteContentRange"]["range"]["startIndex"] for req in requests
              if "deleteContentRange" in req]
    assert starts == [18, 12, 7, 1]
    assert not any("updateParagraphStyle" in req for req in requests)


def test_empty_multiline_replacement_retains_only_mandatory_mark(mocker):
    """Deleting complete paragraphs leaves the segment's mandatory final LF."""
    body = _body(("Alpha", "TITLE", False), ("Beta", "HEADING_1", True))
    requests = _requests(mocker, body, "Alpha\nBeta", "")
    assert requests == [{"deleteContentRange": {"range": {
        "startIndex": 1, "endIndex": 11, "tabId": "synthetic-tab",
    }}}]


def test_explicit_multiline_cell_can_remove_paragraphs(mocker):
    """Whole-cell prose collapses list items and removes list membership."""
    body = _body(("Alpha", "NORMAL_TEXT", True), ("Beta", "NORMAL_TEXT", True))
    body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    requests = _requests(mocker, body, "Alpha\nBeta", "Prose", replace_paragraphs=True)
    assert requests[0]["deleteContentRange"]["range"] == {
        "startIndex": 1, "endIndex": 11, "tabId": "synthetic-tab",
    }
    assert len(requests) == 4
    assert requests[1]["insertText"]["text"] == "Prose"


def test_cell_blockquote_keeps_explicit_indent(mocker):
    """Clearing inherited list indents must not overwrite blockquote intent."""
    body = _body(("Old", "NORMAL_TEXT", True))
    requests = _requests(mocker, body, "Old", "> Quote", replace_paragraphs=True)
    style = next(req["updateParagraphStyle"]["paragraphStyle"] for req in requests
                 if "updateParagraphStyle" in req)
    assert style["indentStart"] == {"magnitude": 36, "unit": "PT"}


def _apply_text_requests(body, requests):
    """Replay UTF-16 text operations, rejecting out-of-range edits."""
    text = ''.join(e['textRun']['content'] for p in body['content']
                   for e in p['paragraph']['elements']).encode('utf-16-le')
    for request in requests:
        if 'deleteContentRange' in request:
            r = request['deleteContentRange']['range']
            start, end = (r['startIndex'] - 1) * 2, (r['endIndex'] - 1) * 2
            assert 0 <= start < end <= len(text) - 2
            text = text[:start] + text[end:]
        elif 'insertText' in request:
            insert = request['insertText']
            start = (insert['location']['index'] - 1) * 2
            # Insertion may precede the mandatory final newline, never follow it.
            assert 0 <= start <= len(text) - 2
            text = text[:start] + insert['text'].encode('utf-16-le') + text[start:]
    return text.decode('utf-16-le')


@pytest.mark.parametrize("command", ["edit", "suggest"])
@pytest.mark.parametrize("in_cell", [False, True])
@pytest.mark.parametrize("target", ["Drawing", "Following"])
def test_empty_replacement_preserves_positioned_object_anchor(
    mocker, command, in_cell, target,
):
    body = _body(("Drawing", "NORMAL_TEXT", False),
                 ("Following", "NORMAL_TEXT", False))
    body["content"][0]["paragraph"]["positionedObjectIds"] = ["drawing"]
    text_body = deepcopy(body)
    if in_cell:
        body = {"content": [{"table": {"tableRows": [{"tableCells": [body]}]}}]}
    original = deepcopy(body)
    planner = _requests if command == "edit" else _suggest_requests
    requests = planner(mocker, body, target, "")
    expected = "\nFollowing\n" if target == "Drawing" else "Drawing\n\n"
    assert _apply_text_requests(text_body, requests) == expected
    assert body == original
    deletions = [r["deleteContentRange"]["range"] for r in requests
                 if "deleteContentRange" in r]
    assert len(deletions) == 1
    assert not deletions[0]["startIndex"] <= 8 < deletions[0]["endIndex"]


def _suggest_requests(mocker, body, old, new):
    from gdoc.api.docs import suggest_replacement

    service = mocker.patch('gdoc.api.docs.get_docs_service').return_value
    mocker.patch('gdoc.api.docs.check_suggest_preview_access')
    mocker.patch('gdoc.api.docs._token_identity', return_value=('client', 'token'))
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        'commentUpdateState': 'ALL_SAVED',
        'suggestionResponses': [{'createdSuggestionIds': ['suggest.synthetic']}],
    }
    mocker.patch('gdoc.api.docs.get_document_structure', return_value={})
    mocker.patch('gdoc.api.docs.collect_suggestion_ids',
                 return_value={'suggest.synthetic'})
    matches = find_text_in_document(None, old, body=body)
    suggest_replacement('synthetic-doc', matches, new, 'rev', body=body)
    batch = service.documents.return_value.batchUpdate.call_args.kwargs['body']
    return batch['requests']


@pytest.mark.parametrize('command', ['edit', 'suggest'])
@pytest.mark.parametrize('source', ['positional', 'old-stdin', 'new-stdin', 'files'])
@pytest.mark.parametrize('terminators', [1, 2])
@pytest.mark.parametrize('count', [1, 2])
def test_transport_through_planner_retains_exact_boundaries(
    mocker, tmp_path, command, source, terminators, count,
):
    old = '\n'.join(['Alpha', 'Beta'][:count]) + '\n' * terminators
    new = '\n'.join(['Revised', 'Changed'][:count]) + '\n' * terminators
    args = SimpleNamespace(old_text=old, new_text=new)
    if source == 'old-stdin':
        args.old_text = '-'
        mocker.patch('sys.stdin', StringIO(old))
    elif source == 'new-stdin':
        args.new_text = '-'
        mocker.patch('sys.stdin', StringIO(new))
    elif source == 'files':
        old_file, new_file = tmp_path / 'old.md', tmp_path / 'new.md'
        old_file.write_text(old)
        new_file.write_text(new)
        args.old_file, args.new_file = str(old_file), str(new_file)
    old, new = _resolve_replacement_text(args, None)
    body = _body(*[(text, 'HEADING_2', False)
                   for text in ['Alpha', 'Beta'][:count]],
                 ('Neighbor', 'NORMAL_TEXT', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, old, new)
    assert _apply_text_requests(body, requests) == (
        '\n'.join(['Revised', 'Changed'][:count]) + '\nNeighbor\n'
    )
    assert not any('updateParagraphStyle' in r for r in requests)


@pytest.mark.parametrize('command', ['edit', 'suggest'])
@pytest.mark.parametrize('code', ['**literal**', '[literal](https://example.org)'])
def test_fenced_code_rendering_is_shared_and_literal(mocker, command, code):
    body = _body(('Alpha', 'TITLE', False), ('Beta', 'SUBTITLE', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'Alpha\nBeta', f'```\n{code}\nsecond\n```')
    assert _apply_text_requests(body, requests) == code + '\nsecond\n'
    styles = [r['updateTextStyle']['textStyle'] for r in requests
              if 'updateTextStyle' in r]
    assert [style for style in styles if style] == [
        {'weightedFontFamily': {'fontFamily': 'Courier New'}}] * 2
    # Direct edits restore each mark; suggestions must not propose mark changes.
    assert styles.count({}) == (2 if command == 'edit' else 0)
    assert not any('updateParagraphStyle' in r for r in requests)


@pytest.mark.parametrize('command', ['edit', 'suggest'])
def test_fence_source_line_count_cannot_hide_rendered_count_mismatch(mocker, command):
    from gdoc.api.docs import suggest_replacement

    body = _body(('Alpha', 'TITLE', False), ('Beta', 'SUBTITLE', False),
                 ('Gamma', 'NORMAL_TEXT', False))
    service = mocker.patch('gdoc.api.docs.get_docs_service')
    gate = mocker.patch('gdoc.api.docs.check_suggest_preview_access')
    matches = find_text_in_document(None, 'Alpha\nBeta\nGamma', body=body)
    planner = replace_formatted if command == 'edit' else suggest_replacement
    with pytest.raises(GdocError, match='paragraph count mismatch'):
        planner('doc', matches, '```\n**literal**\n```', 'rev', body=body)
    service.assert_not_called()
    gate.assert_not_called()


@pytest.mark.parametrize('command', ['edit', 'suggest'])
@pytest.mark.parametrize('marker', ['# literal', '- literal', '> literal'])
def test_block_marker_inside_paragraph_is_literal_in_edit_and_suggest(
        mocker, command, marker):
    """A partial-paragraph replacement cannot start a block, so suggest must
    accept the same literal markers edit does instead of refusing upfront."""
    body = _body(('Alpha beta', 'NORMAL_TEXT', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'beta', marker)
    assert _apply_text_requests(body, requests) == f'Alpha {marker}\n'
    assert not any('updateParagraphStyle' in r for r in requests)
    assert not any('createParagraphBullets' in r for r in requests)


@pytest.mark.parametrize('command', ['edit', 'suggest'])
def test_table_rows_inside_partial_paragraphs_are_literal_in_edit_and_suggest(
        mocker, command):
    """A match that only partly covers its end paragraphs cannot become a
    table, so the rows stay literal text on both paths."""
    body = _body(('Alpha', 'NORMAL_TEXT', False), ('Beta', 'NORMAL_TEXT', False),
                 ('Gamma', 'NORMAL_TEXT', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'pha\nBeta\nGam', '| H |\n|---|\n| x |')
    assert _apply_text_requests(body, requests) == 'Al| H |\n|---|\n| x |ma\n'
    assert not any('updateParagraphStyle' in r for r in requests)


@pytest.mark.parametrize('command', ['edit', 'suggest'])
def test_fence_lines_inside_partial_paragraphs_are_literal(mocker, command):
    """A partially covered range cannot turn its paragraphs into code lines,
    so the fence lines are literal and the inline Markdown still parses."""
    body = _body(('Alpha', 'NORMAL_TEXT', False), ('Beta', 'NORMAL_TEXT', False),
                 ('Gamma', 'NORMAL_TEXT', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'pha\nBeta\nGam', '```\n**x**\n```')
    assert _apply_text_requests(body, requests) == 'Al```\nx\n```ma\n'
    styles = [r['updateTextStyle']['textStyle'] for r in requests
              if 'updateTextStyle' in r]
    assert styles == ([{'bold': True}, {}] if command == 'edit'
                      else [{'bold': True}])
    if command == 'edit':
        reset = [r['updateTextStyle'] for r in requests if 'updateTextStyle' in r][-1]
        assert reset['range'] == {'startIndex': 8, 'endIndex': 9,
                                  'tabId': 'synthetic-tab'}
        assert reset['fields'] == 'bold'
    assert not any('updateParagraphStyle' in r for r in requests)


def test_suggest_rejects_table_replacing_whole_paragraphs(mocker):
    from gdoc.api.docs import suggest_replacement

    body = _body(('Alpha', 'NORMAL_TEXT', False), ('Beta', 'NORMAL_TEXT', False),
                 ('Gamma', 'NORMAL_TEXT', False))
    service = mocker.patch('gdoc.api.docs.get_docs_service')
    gate = mocker.patch('gdoc.api.docs.check_suggest_preview_access')
    matches = find_text_in_document(None, 'Alpha\nBeta\nGamma', body=body)
    with pytest.raises(GdocError, match='not supported yet') as exc:
        suggest_replacement('doc', matches, '| H |\n|---|\n| x |', 'rev', body=body)
    assert exc.value.exit_code == 3
    service.assert_not_called()
    gate.assert_not_called()


def test_suggest_still_rejects_structural_whole_paragraph_replacement(mocker):
    from gdoc.api.docs import suggest_replacement

    body = _body(('Alpha', 'NORMAL_TEXT', False))
    service = mocker.patch('gdoc.api.docs.get_docs_service')
    gate = mocker.patch('gdoc.api.docs.check_suggest_preview_access')
    matches = find_text_in_document(None, 'Alpha', body=body)
    with pytest.raises(GdocError, match='not supported yet') as exc:
        suggest_replacement('doc', matches, '# Heading', 'rev', body=body)
    assert exc.value.exit_code == 3
    service.assert_not_called()
    gate.assert_not_called()


@pytest.mark.parametrize('command', ['edit', 'suggest'])
def test_closed_backtick_span_line_does_not_open_fence_branch(mocker, command):
    """```code``` is a code span, not a fence opener (its info string would
    hold backticks), so a multiline edit keeps one line per paragraph."""
    body = _body(('Alpha', 'NORMAL_TEXT', False), ('Beta', 'NORMAL_TEXT', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'Alpha\nBeta', '```code```\nnext')
    assert _apply_text_requests(body, requests) == 'code\nnext\n'
    styles = [r['updateTextStyle'] for r in requests if 'updateTextStyle' in r]
    assert [(s['range']['startIndex'], s['range']['endIndex'], s['textStyle'])
            for s in styles] == [
        (1, 5, {'weightedFontFamily': {'fontFamily': 'Courier New'}}),
        *([(5, 6, {})] if command == 'edit' else []),
    ]
    if command == 'edit':
        assert styles[-1]['fields'] == 'weightedFontFamily'
    assert not any('updateParagraphStyle' in r for r in requests)


@pytest.mark.parametrize('command', ['edit', 'suggest'])
def test_single_line_triple_backtick_span_has_edit_suggest_parity(mocker, command):
    body = _body(('Alpha', 'HEADING_2', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'Alpha', '```code```')
    assert _apply_text_requests(body, requests) == 'code\n'
    assert not any('updateParagraphStyle' in r for r in requests)


@pytest.mark.parametrize("replace", [False, True])
def test_insert_at_end_trailing_rule_reuses_final_newline(mocker, replace):
    """A trailing thematic break borrows the mandatory final newline as its
    mark instead of inserting one more, which left an empty paragraph after
    the rule."""
    body = _body(("Existing", "NORMAL_TEXT", False))
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "synthetic-rev", "tabs": [{
            "documentTab": {"body": body},
            "tabProperties": {"tabId": "synthetic-tab", "title": "Notes"},
        }],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("synthetic-doc", "Notes", "Text\n\n---",
                             position="end", replace=replace)
    requests = service.documents.return_value.batchUpdate.call_args.kwargs[
        "body"]["requests"]
    inserts = [req["insertText"] for req in requests if "insertText" in req]
    first = 1 if replace else 10
    expected = [{"location": {"index": first, "tabId": "synthetic-tab"},
                 "text": "Text\n\n"}]
    if not replace:
        expected.insert(0, {"location": {"index": 9, "tabId": "synthetic-tab"},
                            "text": "\n"})
    assert inserts == expected
    border = next(r["updateParagraphStyle"] for r in requests
                  if "borderBottom" in r.get("updateParagraphStyle", {}).get(
                      "paragraphStyle", {}))
    assert border["range"] == {"startIndex": first + 6, "endIndex": first + 7,
                               "tabId": "synthetic-tab"}


@pytest.mark.parametrize("bullet", [False, True])
def test_insert_table_only_at_end_adds_no_separator(mocker, bullet):
    """InsertTableRequest inserts its own newline before the table, so a
    table-only append must not add a separator paragraph first."""
    body = _body(("Existing", "NORMAL_TEXT", bullet))
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "synthetic-rev", "tabs": [{
            "documentTab": {"body": body},
            "tabProperties": {"tabId": "synthetic-tab", "title": "Notes"},
        }],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_table = mocker.patch("gdoc.api.docs._insert_table")
    insert_markdown_into_tab("synthetic-doc", "Notes", "| A |\n|---|\n| 1 |",
                             position="end")
    service.documents.return_value.batchUpdate.assert_not_called()
    assert insert_table.call_count == 1
    assert insert_table.call_args.args[:2] == ("synthetic-doc", 9)


def _tab_insert(mocker, body, markdown, **kwargs):
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "synthetic-rev", "tabs": [{
            "documentTab": {"body": body},
            "tabProperties": {"tabId": "synthetic-tab", "title": "Notes"},
        }],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_table = mocker.patch("gdoc.api.docs._insert_table")
    insert_markdown_into_tab("synthetic-doc", "Notes", markdown, **kwargs)
    calls = service.documents.return_value.batchUpdate.call_args_list
    requests = calls[0].kwargs["body"]["requests"] if calls else []
    return requests, insert_table


@pytest.mark.parametrize("bullet", [False, True])
def test_insert_leading_table_at_end_needs_no_separator(mocker, bullet):
    """InsertTableRequest adds its own newline, so the placeholder newline
    becomes the existing paragraph's mark: no separator, no style or bullet
    request may touch that paragraph."""
    body = _body(("Existing", "HEADING_2", bullet))
    requests, insert_table = _tab_insert(
        mocker, body, "| A |\n|---|\n| 1 |\n\nAfter", position="end")
    inserts = [req["insertText"] for req in requests if "insertText" in req]
    assert inserts == [{"location": {"index": 9, "tabId": "synthetic-tab"},
                        "text": "\n\nAfter"}]
    assert insert_table.call_args.args[:2] == ("synthetic-doc", 9)
    for req in requests:
        rng = (req.get("updateParagraphStyle") or req.get("deleteParagraphBullets")
               or {}).get("range")
        if rng:
            assert rng["startIndex"] >= 10
    resets = [r["deleteParagraphBullets"]["range"] for r in requests
              if "deleteParagraphBullets" in r]
    assert resets == ([{"startIndex": 10, "endIndex": 16, "tabId": "synthetic-tab"}]
                      if bullet else [])


def test_insert_leading_rule_at_end_keeps_separator(mocker):
    """A thematic break is a paragraph of its own, so its mark must not
    become the existing paragraph's newline."""
    body = _body(("Existing", "NORMAL_TEXT", False))
    requests, _ = _tab_insert(mocker, body, "---\n\nAfter", position="end")
    inserts = [req["insertText"] for req in requests if "insertText" in req]
    assert inserts == [
        {"location": {"index": 9, "tabId": "synthetic-tab"}, "text": "\n"},
        {"location": {"index": 10, "tabId": "synthetic-tab"}, "text": "\n\nAfter"},
    ]
    border = next(r["updateParagraphStyle"] for r in requests
                  if "borderBottom" in r.get("updateParagraphStyle", {}).get(
                      "paragraphStyle", {}))
    assert border["range"] == {"startIndex": 10, "endIndex": 11,
                               "tabId": "synthetic-tab"}


def test_cell_collapse_resets_bullet_of_retained_last_paragraph(mocker):
    """Collapsing a mixed cell keeps only the last paragraph's mark, so its
    bullet must be removed even though the first paragraph had none."""
    body = _body(("Plain intro", "NORMAL_TEXT", False),
                 ("Listed", "NORMAL_TEXT", True))
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    replace_formatted("synthetic-doc", [{"startIndex": 1, "endIndex": 19}],
                      "One line", "rev", body=body, replace_paragraphs=True)
    requests = service.documents.return_value.batchUpdate.call_args.kwargs[
        "body"]["requests"]
    assert _apply_text_requests(body, requests) == "One line\n"
    resets = [r["deleteParagraphBullets"]["range"] for r in requests
              if "deleteParagraphBullets" in r]
    assert resets == [{"startIndex": 1, "endIndex": 9}]
    style = next(r["updateParagraphStyle"] for r in requests
                 if "updateParagraphStyle" in r)
    assert style["range"] == {"startIndex": 1, "endIndex": 9}
    assert style["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"


@pytest.mark.parametrize('multiline', [False, True])
def test_thematic_break_styles_retained_mark_without_inserting_mark(mocker, multiline):
    body = _body(('Alpha', 'HEADING_2', False))
    old, new = 'Alpha', '---'
    if multiline:
        body = _body(('Alpha', 'HEADING_2', False), ('Beta', 'HEADING_1', False))
        old, new = 'Alpha\nBeta', '---\nBeta'
    requests = _requests(mocker, body, old, new)
    assert _apply_text_requests(body, requests) == ('\nBeta\n' if multiline else '\n')
    border = next(r['updateParagraphStyle'] for r in requests
                  if 'borderBottom' in r.get('updateParagraphStyle', {}).get(
                      'paragraphStyle', {}))
    assert border['range']['startIndex'] == 1
    assert border['range']['endIndex'] == 2


@pytest.mark.parametrize('run,terminator', [
    ('20260912T191944-85b094d905', '\n'),
    ('20260912T192053-7725ed4778', '\n\n'),
])
def test_replay_rename_changes_only_target(mocker, run, terminator):
    body = _body(('Intro witness', 'NORMAL_TEXT', False),
                 ('Interim findings', 'HEADING_2', False),
                 ('Neighbor witness', 'NORMAL_TEXT', False))
    args = SimpleNamespace(old_text='Interim findings' + terminator,
                           new_text='Final findings')
    old, new = _resolve_replacement_text(args, None)
    requests = _requests(mocker, body, old, new)
    assert _apply_text_requests(body, requests) == (
        'Intro witness\nFinal findings\nNeighbor witness\n'
    ), run


def test_replay_192037_removes_final_heading_using_preceding_newline(mocker):
    body = _body(('Intro', 'NORMAL_TEXT', False), ('Body', 'NORMAL_TEXT', False),
                 ('Obsolete section', 'HEADING_2', False))
    requests = _requests(mocker, body, 'Obsolete section', '')
    assert _apply_text_requests(body, requests) == 'Intro\nBody\n'
    assert len(requests) == 1
    assert requests[0]['deleteContentRange']['range']['endIndex'] == (
        body['content'][-1]['endIndex'] - 1
    )
    # Docs keeps Body's style on the merge (observed live); nothing restored.


@pytest.mark.parametrize('position', ['start', 'end'])
@pytest.mark.parametrize('markdown', ['Update', '\nUpdate\n'])
def test_replay_insert_preserves_heading_and_avoids_unneeded_indents(
    mocker, position, markdown,
):
    body = _body(('Open questions', 'HEADING_2', False))
    mocker.patch('gdoc.api.docs.get_document_with_tabs', return_value={
        'revisionId': 'rev', 'tabs': [{
            'tabProperties': {'tabId': 'tab', 'title': 'Notes'},
            'documentTab': {'body': body},
        }],
    })
    service = mocker.patch('gdoc.api.docs.get_docs_service').return_value
    insert_markdown_into_tab('doc', 'Notes', markdown, position=position)
    batch = service.documents.return_value.batchUpdate.call_args.kwargs['body']
    requests = batch['requests']
    inserted = markdown.removesuffix('\n') + '\n'
    expected = (inserted + 'Open questions\n' if position == 'start'
                else 'Open questions\n' + inserted)
    assert _apply_text_requests(body, requests) == expected
    assert not any('deleteParagraphBullets' in r for r in requests)
    for request in requests:
        style = request.get('updateParagraphStyle', {}).get('paragraphStyle', {})
        assert 'indentStart' not in style
        assert 'indentFirstLine' not in style


@pytest.mark.parametrize('count', [1, 2])
def test_replay_191840_cell_wording_retains_custom_paragraph_properties(
    mocker, count,
):
    cell = _body(*[('Pending confirmation', 'NORMAL_TEXT', False)] * count)
    for paragraph in cell['content']:
        paragraph['paragraph']['paragraphStyle'].update(
            lineSpacing=100, indentStart={'magnitude': 36, 'unit': 'PT'},
            indentFirstLine={'magnitude': 18, 'unit': 'PT'},
        )
    body = {'content': [{'table': {'tableRows': [{'tableCells': [cell]}]}}]}
    requests = _requests(mocker, body, '\n'.join(['Pending confirmation'] * count),
                         'Confirmed', replace_paragraphs=True)
    assert _apply_text_requests(cell, requests) == 'Confirmed\n'
    assert not any('updateParagraphStyle' in r or 'deleteParagraphBullets' in r
                   for r in requests)


def test_replay_192156_title_subtitle_keep_existing_heading_ids_and_marks(mocker):
    body = _body(('Launch window plan', 'TITLE', False),
                 ('Q4 planning memo', 'SUBTITLE', False),
                 ('Body witness', 'NORMAL_TEXT', False))
    for index, paragraph in enumerate(body['content'][:2]):
        paragraph['paragraph']['paragraphStyle']['headingId'] = f'h.existing{index}'
    original = deepcopy(body)
    requests = _requests(mocker, body, 'Launch window plan\nQ4 planning memo',
                         'Release window plan\nQ4 planning memo')
    assert _apply_text_requests(body, requests) == (
        'Release window plan\nQ4 planning memo\nBody witness\n'
    )
    assert body == original
    assert not any('updateParagraphStyle' in r for r in requests)
    marks = [p['endIndex'] - 1 for p in body['content']]
    for request in requests:
        if 'deleteContentRange' in request:
            r = request['deleteContentRange']['range']
            assert not any(r['startIndex'] <= mark < r['endIndex'] for mark in marks)


@pytest.mark.parametrize('old', ['Gone', 'Gone\nGone'])
def test_adjacent_complete_paragraph_removals_delete_shared_mark_once(mocker, old):
    body = _body(('Keep', 'NORMAL_TEXT', False), ('Gone', 'HEADING_2', False),
                 ('Gone', 'HEADING_2', False))
    requests = _requests(mocker, body, old, '')
    assert _apply_text_requests(body, requests) == 'Keep\n'
    # One deletion; Docs keeps Keep's style on the merge (observed live).
    assert [next(iter(r)) for r in requests] == ['deleteContentRange']


def test_partial_multiline_deletion_removes_fully_covered_final_paragraph(mocker):
    body = _body(('Keep tail', 'NORMAL_TEXT', False), ('Gone', 'HEADING_2', False))
    requests = _requests(mocker, body, ' tail\nGone', '')
    assert _apply_text_requests(body, requests) == 'Keep\n'


@pytest.mark.parametrize('command', ['edit', 'suggest'])
def test_fenced_code_keeps_real_empty_last_line(mocker, command):
    body = _body(('Alpha', 'TITLE', False), ('Beta', 'SUBTITLE', False))
    planner = _requests if command == 'edit' else _suggest_requests
    requests = planner(mocker, body, 'Alpha\nBeta\n', '```\n**code**\n\n```')
    assert _apply_text_requests(body, requests) == '**code**\n\n'


@pytest.mark.parametrize('new', ['Revised\n', 'Revised\n\n'])
def test_single_paragraph_cannot_bypass_unmatched_newline_check(mocker, new):
    body = _body(('Alpha', 'TITLE', False), ('Neighbor', 'NORMAL_TEXT', False))
    service = mocker.patch('gdoc.api.docs.get_docs_service')
    matches = find_text_in_document(None, 'Alpha', body=body)
    with pytest.raises(GdocError, match='paragraph count mismatch'):
        replace_formatted('doc', matches, new, 'rev', body=body)
    service.assert_not_called()


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("new", ["Prose 😀", "", "- Item", "First\nSecond"])
@pytest.mark.parametrize("bullet", [False, True])
def test_whole_cell_list_removal_request_ranges(mocker, count, new, bullet):
    """Prose, empty, Markdown lists and non-list cells keep scoped requests."""
    from gdoc.cli import build_parser, cmd_edit

    cell = _body(*[("Old", "HEADING_2", bullet)] * count)
    neighbor = _body(("Witness", "TITLE", True))
    for paragraph in neighbor["content"]:
        for indexed in [paragraph, *paragraph["paragraph"]["elements"]]:
            indexed["startIndex"] += 100
            indexed["endIndex"] += 100
    body = {"content": [{"table": {"tableRows": [{
        "tableCells": [cell, neighbor],
    }]}}]}
    original = deepcopy(body)
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "rev", "tabs": [{
            "tabProperties": {"tabId": "tab", "title": "Notes", "index": 0},
            "documentTab": {"body": body},
        }],
    })
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 1})
    mocker.patch("gdoc.state.update_state_after_command")
    args = build_parser().parse_args([
        "edit", "--cell", "0,0", "--tab", "Notes", "--", "doc", new,
    ])
    assert cmd_edit(args) == 0
    requests = service.documents.return_value.batchUpdate.call_args.kwargs[
        "body"]["requests"]
    assert body == original
    # A cell holds inline text only: an item marker stays literal (R2-03).
    rendered = new
    assert _apply_text_requests(cell, requests) == rendered + "\n"
    resets = [r["deleteParagraphBullets"]["range"] for r in _bullet_resets(requests)]
    # The list compiler's own indent requests are checked natively elsewhere.
    styles = [r["updateParagraphStyle"] for r in requests
              if "namedStyleType" in r.get("updateParagraphStyle", {})
              .get("paragraphStyle", {})]
    creates = [r for r in requests if "createParagraphBullets" in r]
    assert not creates
    if not bullet and not creates and new:
        assert resets == styles == []
        return
    expected_ranges = ([{"startIndex": 5, "endIndex": 11, "tabId": "tab"},
                        {"startIndex": 1, "endIndex": 6, "tabId": "tab"}]
                       if count == 2 and new == "First\nSecond" else
                       [{"startIndex": 1,
                         "endIndex": 1 + max(1, utf16_len(rendered)),
                         "tabId": "tab"}])
    assert [s["range"] for s in styles] == expected_ranges
    if bullet or not new or count != len(new.split("\n")):
        assert resets == expected_ranges
    for style in styles:
        assert style["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"
        assert "alignment" not in style["fields"]
        if bullet and not creates:
            for field in ("indentStart", "indentEnd", "indentFirstLine"):
                assert field in style["fields"]


def test_delete_nonfinal_heading_leaves_later_inline_image_untouched(mocker):
    """Deleting one heading shifts later objects without consuming their marks."""
    body = _body(("Context", "NORMAL_TEXT", False),
                 ("Heading", "HEADING_2", False),
                 ("Following", "NORMAL_TEXT", False),
                 ("X", "NORMAL_TEXT", False),
                 ("Remote", "NORMAL_TEXT", False))
    image = body["content"][3]
    image["paragraph"]["elements"] = [
        {"startIndex": 27, "endIndex": 28,
         "inlineObjectElement": {"inlineObjectId": "synthetic-image"}},
        {"startIndex": 28, "endIndex": 29, "textRun": {"content": "\n"}},
    ]
    original = deepcopy(body)
    requests = _requests(mocker, body, "Heading", "")
    assert requests == [{"deleteContentRange": {"range": {
        "startIndex": 9, "endIndex": end, "tabId": "synthetic-tab",
    }}} for end in (16, 10)]
    assert body == original
    assert image["startIndex"] == 27


def _tab_insert_requests(mocker, body, markdown, operation):
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "synthetic-rev", "tabs": [{
            "documentTab": {"body": body},
            "tabProperties": {"tabId": "synthetic-tab", "title": "Notes"},
        }],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab(
        "synthetic-doc", "Notes", markdown,
        position="end" if operation == "end" else "start",
        replace=operation == "write",
    )
    batch = service.documents.return_value.batchUpdate
    return batch.call_args.kwargs["body"]["requests"] if batch.called else []


@pytest.mark.parametrize("operation", ["start", "end", "write"])
@pytest.mark.parametrize("existing", ["Existing", ""])
@pytest.mark.parametrize("markdown", [
    "new", "new\n", "new\n\n", "new\n\n\n", "\n", "\n\n", "\n\n\n", "",
])
def test_tab_insertion_preserves_trailing_blank_paragraphs(
    mocker, operation, existing, markdown,
):
    body = _body((existing, "NORMAL_TEXT", False))
    requests = _tab_insert_requests(mocker, body, markdown, operation)
    rendered = markdown if markdown.endswith("\n") else markdown + "\n"
    if operation == "write" or not existing:
        expected = rendered
    elif not markdown:
        expected = existing + "\n"
    elif operation == "start":
        expected = rendered + existing + "\n"
    else:
        expected = existing + "\n" + rendered
    assert _apply_text_requests(body, requests) == expected


@pytest.mark.parametrize("operation", ["start", "end", "write"])
@pytest.mark.parametrize("existing", ["Existing 😀", ""])
@pytest.mark.parametrize("prefix", ["", "Body 😀\n", "  - Nested\n"])
@pytest.mark.parametrize("named_style", ["TITLE", "SUBTITLE"])
def test_tab_empty_final_named_style_targets_its_paragraph_mark(
    mocker, operation, existing, prefix, named_style,
):
    body = _body((existing, "NORMAL_TEXT", False))
    markdown = prefix + f"<!-- gdoc:{named_style} --> \n"
    requests = _tab_insert_requests(mocker, body, markdown, operation)
    prefix_text = "Nested\n" if prefix.startswith("  -") else prefix
    rendered = prefix_text + "\n"
    if operation == "write" or not existing:
        expected = rendered
        target = 1 + utf16_len(prefix_text)
    elif operation == "start":
        expected = rendered + existing + "\n"
        target = 1 + utf16_len(prefix_text)
    else:
        expected = existing + "\n" + rendered
        target = 1 + utf16_len(existing + "\n" + prefix_text)
    # The text-only replayer does not simulate bullet tab removal.
    assert _apply_text_requests(body, requests) == expected.replace(
        "Nested", "\tNested",
    )
    # Account for tabs removed by earlier bullet requests at each style request.
    removed_tabs = 0
    for request in requests:
        if "createParagraphBullets" in request:
            removed_tabs += 1 if prefix.startswith("  -") else 0
        style = request.get("updateParagraphStyle", {})
        if style.get("paragraphStyle", {}).get("namedStyleType") == named_style:
            assert style["range"] == {
                "startIndex": target + (1 if prefix.startswith("  -") else 0)
                - removed_tabs,
                "endIndex": target + (1 if prefix.startswith("  -") else 0)
                - removed_tabs + 1,
                "tabId": "synthetic-tab",
            }
            break
    else:
        pytest.fail("No request applies the final named style")


@pytest.mark.parametrize("command", ["edit", "suggest"])
def test_fenced_replacement_preserves_blank_paragraph_after_closer(mocker, command):
    body = _body(("Alpha", "TITLE", False), ("Beta", "SUBTITLE", False))
    planner = _requests if command == "edit" else _suggest_requests
    requests = planner(mocker, body, "Alpha\nBeta\n", "```\ncode\n```\n\n")
    assert _apply_text_requests(body, requests) == "code\n\n"


def test_cleanup_cannot_delete_image_only_paragraph():
    """An object plus newline is not an empty disposable paragraph."""
    from gdoc.api.docs import _build_cleanup_requests

    body = _body(("", "HEADING_1", False), ("After", "NORMAL_TEXT", False))
    body["content"][0]["paragraph"]["elements"].insert(0, {
        "inlineObjectElement": {"inlineObjectId": "synthetic-image"},
    })
    assert _build_cleanup_requests(body, 1) == []
