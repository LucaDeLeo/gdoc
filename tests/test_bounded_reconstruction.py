"""Synthetic regressions for tab state, nested lists and bounded full uploads."""

from copy import deepcopy

import pytest
from test_list_request_semantics import apply_list_requests

from gdoc.api.docs import insert_markdown_into_tab
from gdoc.cli import (
    _comparable_markdown,
    build_parser,
    cmd_push,
    cmd_write,
)
from gdoc.lossy import _IMPORT_PAGE_SETUP, check_markdown_replacement
from gdoc.mdparse import parse_markdown, to_docs_requests, utf16_len
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


def _tab(body):
    return {"tabProperties": {"tabId": "draft", "title": "Draft"},
            "documentTab": {"body": body}}


def _rewrite(mocker, markdown, paragraph=None):
    body = {"content": [{"startIndex": 1, "endIndex": 5, "paragraph": {
        "elements": [{"textRun": {"content": "old\n"}}],
        **(paragraph or {}),
    }}]}
    doc = {"revisionId": "rev", "tabs": [_tab(body), {
        "tabProperties": {"tabId": "witness", "title": "Reference"},
        "documentTab": {"body": {"content": [{"paragraph": {"elements": [
            {"inlineObjectElement": {"inlineObjectId": "image"}},
            {"footnoteReference": {"footnoteId": "note"}},
        ]}}]}},
    }]}
    original = deepcopy(doc)
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", "Draft", markdown, replace=True)
    assert doc == original
    batch = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    assert batch["writeControl"] == {"requiredRevisionId": "rev"}
    for request in batch["requests"]:
        value = next(iter(request.values()))
        assert value.get("range", value.get("location"))["tabId"] == "draft"
    return batch["requests"]


@pytest.mark.parametrize("paragraph", [
    {"bullet": {"listId": "old-list", "nestingLevel": 2}},
    {"paragraphStyle": {"namedStyleType": "HEADING_2", "alignment": "CENTER",
                        "lineSpacing": 200}},
    {"paragraphStyle": {"indentStart": {"magnitude": 72, "unit": "PT"},
                        "spaceAbove": {"magnitude": 24, "unit": "PT"}}},
])
def test_f7_reset_retained_paragraph_before_inserting_new_content(mocker, paragraph):
    requests = _rewrite(mocker, "# Heading\nPlain\n- Item\nClosing", paragraph)
    # Reset after deletion (at the retained mark) and before insertion: all new
    # paragraphs start from this state, then receive only requested Markdown.
    assert [next(iter(r)) for r in requests[:5]] == [
        "deleteContentRange", "deleteParagraphBullets", "updateParagraphStyle",
        "updateTextStyle", "insertText",
    ]
    assert requests[0]["deleteContentRange"]["range"]["endIndex"] == 4
    for req in requests[1:4]:
        assert next(iter(req.values()))["range"] == {
            "startIndex": 1, "endIndex": 2, "tabId": "draft",
        }
    reset = requests[2]["updateParagraphStyle"]
    assert reset["fields"] == "*"
    assert reset["paragraphStyle"] == {"namedStyleType": "NORMAL_TEXT"}
    assert requests[3]["updateTextStyle"]["textStyle"] == {}
    assert requests[3]["updateTextStyle"]["fields"] == "*"
    bullets = [r["createParagraphBullets"] for r in requests
               if "createParagraphBullets" in r]
    assert [b["bulletPreset"] for b in bullets] == ["BULLET_DISC_CIRCLE_SQUARE"]


@pytest.mark.parametrize("source,expected", [
    ("# Heading\n\nBody", "Heading\n\nBody\n"),
    ("# Heading\n\nBody\n", "Heading\n\nBody\n"),
    ("Body\n\n", "Body\n\n"),
    ("", "\n"), ("\n", "\n"), ("---\n", "\n"),
    ("Body\n---\n", "Body\n\n"),
])
def test_r4_f001_uses_native_terminal_mark_once(mocker, source, expected):
    requests = _rewrite(mocker, source)
    inserted = "".join(r["insertText"]["text"] for r in requests if "insertText" in r)
    assert inserted + "\n" == expected
    if "---" in source:
        borders = [r["updateParagraphStyle"] for r in requests
                   if "borderBottom" in r.get("updateParagraphStyle", {})
                   .get("paragraphStyle", {})]
        assert len(borders) == 1
        assert borders[0]["range"]["endIndex"] == 1 + utf16_len(expected)


def _append(mocker, markdown, existing="old\n"):
    end = 1 + utf16_len(existing)
    content = [{"startIndex": 1, "endIndex": end, "paragraph": {
        "elements": [{"startIndex": 1, "endIndex": end,
                      "textRun": {"content": existing}}],
    }}]
    doc = {"revisionId": "rev", "tabs": [_tab({"content": content})]}
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=doc)
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    insert_markdown_into_tab("doc", "Draft", markdown, position="end")
    batch = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    return batch["requests"]


@pytest.mark.parametrize("existing", ["old\n", "\n"])
@pytest.mark.parametrize("source,appended", [
    ("---", ""), ("---\n", ""), ("Body\n\n---", "Body\n\n"), ("Body", "Body"),
])
def test_r4_f001_end_insert_styles_final_rule_on_retained_mark(
    mocker, existing, source, appended,
):
    # Appending splits the existing final paragraph once; a final rule then
    # borders the retained mark instead of inserting its own newline, which
    # left a blank paragraph after the rule.
    requests = _append(mocker, source, existing)
    inserted = "".join(r["insertText"]["text"] for r in requests if "insertText" in r)
    split = "\n" if existing.strip("\n") else ""
    assert inserted == split + appended
    borders = [r["updateParagraphStyle"]["range"] for r in requests
               if "borderBottom" in r.get("updateParagraphStyle", {})
               .get("paragraphStyle", {})]
    final = 1 + utf16_len(existing) + utf16_len(inserted)
    assert borders == ([{"startIndex": final - 1, "endIndex": final,
                         "tabId": "draft"}] if "---" in source else [])


@pytest.mark.parametrize("marker,preset", [
    ("-", "BULLET_DISC_CIRCLE_SQUARE"),
    ("1.", "NUMBERED_DECIMAL_ALPHA_ROMAN"),
])
@pytest.mark.parametrize("table", [False, True])
def test_r4_f005_three_levels_share_range_and_keep_later_offsets(marker, preset, table):
    source = (f"{marker} Parent 😀\n  {marker} Child\n    {marker} Grandchild\n"
              f"{'2.' if marker == '1.' else marker} Sibling\n\n"
              f"{marker} Next\n  {marker} Nested\n")
    if table:
        source += "\n| Key | Value |\n| --- | --- |\n| A | B |\n"
    parsed = parse_markdown(source)
    before = deepcopy(parsed)
    requests = to_docs_requests(parsed, 7, tab_id="draft")
    assert parsed == before
    bullets = [r["createParagraphBullets"] for r in requests
               if "createParagraphBullets" in r]
    assert len(bullets) == 2
    first = "Parent 😀\n\tChild\n\t\tGrandchild\nSibling\n"
    second = "Next\n\tNested\n"
    assert parsed.plain_text.startswith(first + "\n" + second)
    assert [b["bulletPreset"] for b in bullets] == [preset, preset]
    # Applied from index 1 on the native model: two lists, absolute levels.
    paragraphs = apply_list_requests(to_docs_requests(parse_markdown(source), 1))
    items = [(p["text"].rstrip("\n"), p["depth"], p["list"])
             for p in paragraphs if p["list"] is not None]
    assert [(text, depth) for text, depth, _ in items][:6] == [
        ("Parent 😀", 0), ("Child", 1), ("Grandchild", 2), ("Sibling", 0),
        ("Next", 0), ("Nested", 1)]
    assert len({lst for *_, lst in items[:4]}) == 1
    assert items[4][2] == items[5][2] != items[0][2]
    assert parsed.removed_tabs == 4
    if table:
        assert parsed.tables[0].removed_tabs_before == 4


def test_mixed_list_types_start_separate_ranges():
    requests = to_docs_requests(parse_markdown("- Parent\n  - Child\n1. Number"), 1)
    bullets = [r["createParagraphBullets"] for r in requests
               if "createParagraphBullets" in r]
    assert [r["bulletPreset"] for r in bullets] == [
        "BULLET_DISC_CIRCLE_SQUARE", "NUMBERED_DECIMAL_ALPHA_ROMAN",
    ]
    paragraphs = [p for p in apply_list_requests(requests) if p["list"]]
    assert [(p["text"], p["depth"], p["preset"][:4]) for p in paragraphs] == [
        ("Parent\n", 0, "BULL"), ("Child\n", 1, "BULL"), ("Number\n", 0, "NUMB")]
    assert paragraphs[0]["list"] == paragraphs[1]["list"] != paragraphs[2]["list"]


@pytest.mark.parametrize("native,loss", [
    ({"documentStyle": {"pageSize": {
        "height": {"magnitude": 841.89, "unit": "PT"},
        "width": {"magnitude": 595.28, "unit": "PT"}}}}, "page setup"),
    ({"documentStyle": {"useCustomHeaderFooterMargins": True}}, "page setup"),
    ({"documentStyle": {"documentFormat": {"documentMode": "PAGELESS"}}},
     "page setup"),
    ({"tabs": [_tab({})]}, "tab title 'Draft'"),
])
def test_f6_refuses_or_names_full_import_loss(native, loss, capsys):
    with pytest.raises(GdocError, match=loss) as exc:
        check_markdown_replacement(native)
    assert exc.value.exit_code == 3
    check_markdown_replacement(native, allow_lossy=True)
    warning = capsys.readouterr().err
    assert "WARN: Markdown replacement will discard:" in warning
    assert loss in warning


def test_default_page_setup_does_not_require_override():
    check_markdown_replacement({"documentStyle": deepcopy(_IMPORT_PAGE_SETUP),
                                "tabs": [{"tabProperties": {"title": "Tab 1"}}]})


def test_styles_warn_once_with_specific_losses(capsys):
    check_markdown_replacement({"content": [{"paragraph": {
        "paragraphStyle": {"alignment": "CENTER", "lineSpacing": 200},
        "elements": [{"textRun": {"textStyle": {
            "foregroundColor": {"color": {"rgbColor": {"red": 1}}},
        }}}] * 2,
    }}]}, tab_body=True)
    assert capsys.readouterr().err == (
        "WARN: Markdown replacement may reset styles: alignment, colour, line spacing\n"
    )


@pytest.mark.parametrize("local,remote,same", [
    ("Text", "Text\n", True), ("A\r\nB\r\n", "A\nB\n", True),
    ("  Text", "Text", False), ("\nText", "Text", False),
    ("Text\n\n", "Text\n", False), ("Text  ", "Text", False),
])
def test_noop_comparison_preserves_meaningful_whitespace(local, remote, same):
    assert (_comparable_markdown(local) == _comparable_markdown(remote)) is same


@pytest.mark.parametrize("command", ["write", "push"])
@pytest.mark.parametrize("quiet_force", [False, True])
def test_unchanged_upload_skips_even_rich_page_state(
    mocker, tmp_path, capsys, command, quiet_force,
):
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\ngdoc-revision: r1\n---\nSummary\n",
                    encoding="utf-8")
    argv = [command, "doc", str(path)] if command == "write" else [command, str(path)]
    if quiet_force:
        argv += ["--quiet", "--force"]
    args = build_parser().parse_args(argv)
    mocker.patch("gdoc.notify.pre_flight", return_value=ChangeInfo(
        current_version=10, last_read_version=10,
    ))
    version = mocker.patch("gdoc.api.drive.get_file_version",
                           return_value={"version": 10})
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r1", "tabs": [_tab({"content": [{"paragraph": {
            "elements": [{"textRun": {"content": "Summary\n"}}],
        }}]})], "documentStyle": {"useCustomHeaderFooterMargins": True},
    })
    mocker.patch("gdoc.api.docs.count_document_tabs", return_value=1)
    upload = mocker.patch("gdoc.api.drive.update_doc_content")
    state = mocker.patch("gdoc.state.update_state_after_command")
    assert (cmd_write if command == "write" else cmd_push)(args) == 0
    upload.assert_not_called()
    state.assert_not_called()
    from gdoc.state import load_state
    # Matching Markdown is not a read: invisible native changes could differ.
    # Only the pulled file's own provenance at this revision records one;
    # `write` honors it as `push` does.
    known = load_state("doc")
    assert known.read_revision_ids == {"draft": "r1"}
    assert version.call_count == 0
    assert "already in sync" in capsys.readouterr().out


def test_nested_list_table_insertion_uses_post_bullet_coordinates(mocker):
    insert_table = mocker.patch("gdoc.api.docs._insert_table")
    source = ("- Parent 😀\n  - Child\n    - Grandchild\n- Sibling\n\n"
              "| Key | Value |\n| --- | --- |\n| A | B |\n")
    _rewrite(mocker, source)
    insert_table.assert_called_once()
    doc_id, index, table = insert_table.call_args.args
    assert doc_id == "doc"
    assert index == 1 + utf16_len("Parent 😀\nChild\nGrandchild\nSibling\n\n")
    assert table.rows == [["Key", "Value"], ["A", "B"]]
    assert insert_table.call_args.kwargs["tab_id"] == "draft"
    assert insert_table.call_args.kwargs["ordinal"] == 1


@pytest.mark.parametrize("command", ["write", "push"])
@pytest.mark.parametrize("allow_lossy", [False, True])
@pytest.mark.parametrize("page_style", [{}, {"useCustomHeaderFooterMargins": True}])
def test_write_and_push_preserve_page_setup_natively(
    mocker, tmp_path, capsys, command, allow_lossy, page_style,
):
    path = tmp_path / "draft.md"
    path.write_text("---\ngdoc: doc\ngdoc-revision: r1\n---\nNew text\n",
                    encoding="utf-8")
    argv = [command, "doc", str(path)] if command == "write" else [command, str(path)]
    argv += ["--quiet", "--force"]
    if allow_lossy:
        argv += ["--allow-lossy"]
    document = {
        "revisionId": "r1", "tabs": [_tab({"content": [{
            "startIndex": 1, "endIndex": 5, "paragraph": {
                "elements": [{"textRun": {"content": "Old\n"}}],
            },
        }]})], "documentStyle": page_style,
    }
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=document)
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 10})
    service = mocker.patch("gdoc.api.docs.get_docs_service").return_value
    mocker.patch("gdoc.state.update_state_after_command")
    handler = cmd_write if command == "write" else cmd_push
    assert handler(build_parser().parse_args(argv)) == 0
    batch = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    assert batch["writeControl"] == {"requiredRevisionId": "r1"}
    assert not any("updateDocumentStyle" in r for r in batch["requests"])
    assert any(
        r.get("insertText", {}).get("text") == "New text" for r in batch["requests"]
    )
    assert "discard" not in capsys.readouterr().err
