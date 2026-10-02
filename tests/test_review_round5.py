"""Round-5 review regressions: export/parse round trips of supported Markdown."""

import json

import pytest

from gdoc.api.docs import flatten_tabs, get_tab_text
from gdoc.mdparse import parse_markdown

CODE = {"weightedFontFamily": {"fontFamily": "Courier New"}}


def _tab(content, inline_objects=None):
    tab = {"tabProperties": {"tabId": "t.0", "title": "Main"},
           "documentTab": {"body": {"content": content},
                           "inlineObjects": inline_objects or {}}}
    return flatten_tabs([tab])[0]


def _paragraph(*elements, style=None):
    index, built = 1, []
    for element in elements:
        if isinstance(element, tuple):
            text, text_style = element
            built.append({"startIndex": index, "endIndex": index + len(text),
                          "textRun": {"content": text, "textStyle": text_style}})
            index += len(text)
        else:
            built.append({"startIndex": index, "endIndex": index + 1,
                          "inlineObjectElement": {"inlineObjectId": element}})
            index += 1
    return {"startIndex": 1, "endIndex": index, "paragraph": {
        "elements": built,
        "paragraphStyle": {"namedStyleType": style or "NORMAL_TEXT"}}}


@pytest.mark.parametrize("description", [
    "Press ` key", "Chart.\nSource: finance", "tick `a` and\x0bbreak", "a b",
])
def test_image_alt_text_cannot_hide_the_image(description):
    """F3: an image whose alt text has a backtick or line break stays an image."""
    objects = {"obj": {"inlineObjectProperties": {"embeddedObject": {
        "description": description,
        "imageProperties": {"contentUri": "https://example.test/i.png"}}}}}
    tab = _tab([_paragraph(("See ", {}), "obj", (" and ", {}), ("ls", CODE),
                           ("\n", {}))], objects)
    markdown = get_tab_text(tab, markdown=True)
    assert markdown.count("\n") == 1
    parsed = parse_markdown(markdown)
    assert [image.uri for image in parsed.images] == ["gdoc-image:obj"]
    assert parsed.images[0].alt == " ".join(description.split())
    assert parsed.plain_text == "See   and ls\n"
    code = [s for s in parsed.styles if s.type == "text_style"
            and "weightedFontFamily" in s.style]
    assert [(s.start, s.end) for s in code] == [(10, 12)]


def _table(*rows):
    """A native table whose cells each hold one paragraph of styled runs."""
    return {"startIndex": 1, "endIndex": 2, "table": {
        "rows": len(rows), "columns": len(rows[0]),
        "tableRows": [{"tableCells": [{"content": [_paragraph(*cell)]}
                                      for cell in row]} for row in rows]}}


@pytest.mark.parametrize("cell,text", [
    ((("Type ` then ", {}), ("a|b", CODE), ("\n", {})), "Type ` then a|b"),
    ((("a`b\x0bc`d", {}), ("\n", {})), "a`b\x0bc`d"),
])
def test_table_cell_backtick_stays_literal(cell, text):
    """F4: an escaped backtick in a cell never opens a code span.

    The cell must parse back to exactly its native text, so a write sends no
    extra backslash or literal ``<br>`` that the next read would grow.
    """
    from gdoc.mdparse import parse_inline

    tab = _tab([_table([(("H", {}), ("\n", {}))], [cell]), _paragraph(("\n", {}))])
    parsed = parse_markdown(get_tab_text(tab, markdown=True))
    (only,) = parsed.tables[0].rows[1]
    assert parse_inline(only)[0] == text


SECTIONED = [
    {"startIndex": 0, "endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
    {"startIndex": 1, "endIndex": 6, "paragraph": {"elements": [
        {"startIndex": 1, "endIndex": 6, "textRun": {"content": "Page\n"}}]}},
    {"startIndex": 6, "endIndex": 7, "sectionBreak": {
        "sectionStyle": {"sectionType": "NEXT_PAGE"}}},
    {"startIndex": 7, "endIndex": 12, "paragraph": {"elements": [
        {"startIndex": 7, "endIndex": 12, "textRun": {"content": "Land\n"}}]}},
]


@pytest.mark.parametrize("consent", [False, True])
def test_section_breaks_flatten_only_with_consent(mocker, consent):
    """F9: the advertised --allow-lossy route rewrites the tab as one section."""
    from gdoc.api.docs import insert_markdown_into_tab
    from gdoc.util import GdocError

    doc = {"revisionId": "r1", "tabs": [{
        "tabProperties": {"tabId": "t1", "title": "Tab 1"},
        "documentTab": {"body": {"content": SECTIONED}}}]}
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    batch = service.documents.return_value.batchUpdate
    batch.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": "r2"}}
    mocker.patch("gdoc.api.comment_transport.execute_mutation_request",
                 side_effect=lambda request, **_: request.execute())
    if not consent:
        with pytest.raises(GdocError, match="--allow-lossy") as error:
            insert_markdown_into_tab("d", "t1", "Page\n\nLand\n", replace=True,
                                     document=doc)
        assert error.value.exit_code == 3
        batch.assert_not_called()
        return
    insert_markdown_into_tab("d", "t1", "Page\n\nLand\n", replace=True,
                             allow_lossy=True, document=doc)
    requests = batch.call_args.kwargs["body"]["requests"]
    # One deletion spans the break and the newline before it (Docs deletes a
    # section break only together with that newline).
    assert requests[0] == {"deleteContentRange": {"range": {
        "startIndex": 1, "endIndex": 11, "tabId": "t1"}}}
    assert batch.call_args.kwargs["body"]["writeControl"] == {
        "requiredRevisionId": "r1"}


@pytest.mark.parametrize("text", ["--", "---", "- -", "-", "- - -"])
@pytest.mark.parametrize("nest", [0, 1])
def test_a_bullet_of_dashes_stays_a_list_item(text, nest):
    """F11: an item whose text is dashes never reads back as a rule."""
    paragraph = _paragraph((text + "\n", {}))
    paragraph["paragraph"]["bullet"] = {"listId": "L", "nestingLevel": nest}
    tab = _tab([paragraph])
    tab["lists"] = {"L": {"listProperties": {"nestingLevels": [
        {"glyphSymbol": "●"}] * 3}}}
    parsed = parse_markdown(get_tab_text(tab, markdown=True))
    assert parsed.plain_text.lstrip("\t") == text + "\n"
    assert [s.type for s in parsed.styles].count("bullets") == 1
    assert not any("borderBottom" in s.style for s in parsed.styles)


@pytest.mark.parametrize("reply", [
    None, {}, {"replies": []}, {"replies": [{}]},
    {"replies": [{"addDocumentTab": {}}]},
    {"replies": [{"addDocumentTab": {"tabProperties": {}}}]},
    {"replies": [{"addDocumentTab": {"tabProperties": {"tabId": ""}}}]},
    {"replies": [{"addDocumentTab": {"tabProperties": "t.1"}}]},
])
def test_unreadable_add_tab_reply_is_uncertain_and_not_resent(mocker, reply):
    """F20: a sent tab creation with an unreadable reply may have succeeded."""
    from gdoc.api.docs import add_tab
    from gdoc.util import GdocError

    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    send = mocker.patch("gdoc.api.comment_transport.execute_mutation_request",
                        return_value=reply)
    with pytest.raises(GdocError) as error:
        add_tab("d", "Notes")
    assert "outcome is uncertain" in str(error.value)
    assert "list the document's tabs before retrying" in str(error.value)
    assert error.value.exit_code == 1
    assert send.call_count == 1
    service.documents.return_value.batchUpdate.assert_called_once()


@pytest.mark.parametrize("interface", ["cli", "mcp"])
def test_add_tab_route_reports_an_unreadable_reply_as_uncertain(
    mocker, monkeypatch, tmp_path, interface,
):
    """F20: both interfaces surface the uncertain outcome, exit 1."""
    import contextlib
    import io

    from gdoc import cli, mcp, state

    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("gdoc.util.get_default_account", lambda: None)
    mocker.patch("gdoc.api.docs.get_docs_service")
    mocker.patch("gdoc.api.comment_transport.execute_mutation_request",
                 return_value={"replies": [{}]})
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    if interface == "mcp":
        response = mcp.MCPServer().dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "gdoc_add_tab",
                       "arguments": {"doc": "d", "title": "Notes"}}})
        text = json.dumps(response)
        assert response["result"]["isError"]
    else:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            assert cli.run_argv(["add-tab", "d", "Notes"], check_updates=False) == 1
        text = err.getvalue()
    assert "list the document's tabs before retrying" in text


def test_linked_paragraph_shaped_like_a_definition_survives():
    """F13: escaped brackets never make a paragraph a reference definition."""
    link = {"link": {"url": "https://example.org/spec"}}
    tab = _tab([_paragraph(("[1]: https://example.org/spec", link), ("\n", {}))])
    parsed = parse_markdown(get_tab_text(tab, markdown=True))
    assert parsed.plain_text == "[1]: https://example.org/spec\n"
    assert any(s.style.get("link") == link["link"] for s in parsed.styles)


@pytest.mark.parametrize("text", ["x\xa0", "\xa0x", "x ", "　x"])
def test_table_cell_edge_whitespace_other_than_spaces_is_text(text):
    """F14: only spaces and tabs around a cell are Markdown padding."""
    from gdoc.mdparse import parse_inline

    tab = _tab([_table([(("H", {}), ("\n", {}))], [((text, {}), ("\n", {}))]),
                _paragraph(("\n", {}))])
    parsed = parse_markdown(get_tab_text(tab, markdown=True))
    assert parse_inline(parsed.tables[0].rows[1][0])[0] == text


DEFAULT_BORDER = {"color": {"color": {"rgbColor": {}}},
                  "width": {"magnitude": 1, "unit": "PT"}, "dashStyle": "SOLID"}


@pytest.mark.parametrize("cell_style,table_style,warning", [
    ({"backgroundColor": {"color": {"rgbColor": {"red": 0.9}}}}, {},
     "table cell shading"),
    ({"backgroundColor": {"color": {"rgbColor": {}}}}, {}, "table cell shading"),
    ({"backgroundColor": {"color": {"rgbColor": {"red": 0, "green": 0}}}}, {},
     "table cell shading"),
    ({"borderTop": {**DEFAULT_BORDER, "width": {"magnitude": 3, "unit": "PT"}}}, {},
     "table borders"),
    # The API omits a zero magnitude: this border is 0pt, not the default.
    ({"borderTop": {**DEFAULT_BORDER, "width": {"unit": "PT"}}}, {},
     "table borders"),
    ({"borderTop": {k: v for k, v in DEFAULT_BORDER.items() if k != "width"}},
     {}, None),
    ({}, {"tableColumnProperties": [{"widthType": "FIXED_WIDTH",
                                     "width": {"magnitude": 90, "unit": "PT"}}]},
     "table column widths"),
    ({"backgroundColor": {}, "borderTop": DEFAULT_BORDER},
     {"tableColumnProperties": [{"widthType": "EVENLY_DISTRIBUTED"}]}, None),
])
def test_rich_table_styling_warns_before_a_rewrite(capsys, cell_style,
                                                   table_style, warning):
    """F15: styling a pipe table cannot keep is named; defaults stay quiet."""
    from gdoc.lossy import check_markdown_replacement

    table = _table([(("H", {}), ("\n", {}))])
    table["table"]["tableRows"][0]["tableCells"][0]["tableCellStyle"] = cell_style
    table["table"]["tableStyle"] = table_style
    check_markdown_replacement({"body": {"content": [table]}}, tab_body=True)
    err = capsys.readouterr().err
    if warning:
        assert f"may reset styles: {warning}" in err
    else:
        assert "table" not in err


@pytest.mark.parametrize("quote,status,anchor", [
    ("Apple", "anchored", (1, 6)),       # one exact-case match wins
    ("apple", "anchored", (28, 33)),
    ("apple pie", "anchored", (1, 10)),  # no exact case: case-insensitive
    ("APPLE", "ambiguous", None),        # two matches in other casings
])
def test_comment_quote_prefers_its_own_letter_case(mocker, quote, status, anchor):
    """F19: an exact-case quote is not ambiguous with other casings."""
    from gdoc import cli

    def para(text, start):
        end = start + len(text)
        return {"startIndex": start, "endIndex": end, "paragraph": {"elements": [
            {"startIndex": start, "endIndex": end, "textRun": {"content": text}}]}}

    doc = {"revisionId": "r1", "tabs": [{
        "tabProperties": {"tabId": "t1", "title": "Main"},
        "documentTab": {"body": {"content": [
            para("Apple pie is great.\n", 1), para("I like apple juice.\n", 21)]}}}]}
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    insert = mocker.patch("gdoc.api.docs.insert_comment", return_value="c1")
    result = cli._try_anchored_comment("d", "note", quote)
    assert result.status == status
    if anchor:
        assert insert.call_args.args[2:4] == anchor
    else:
        insert.assert_not_called()
