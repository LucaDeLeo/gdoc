"""Synthetic request-level checks for native Markdown replacement."""

import pytest
from test_list_request_semantics import apply_list_requests

from gdoc.api.docs import (
    _code_range_requests,
    _strip_trailing_newline_unless_hr,
    _table_cell_requests,
    insert_markdown_into_tab,
    replace_formatted,
)
from gdoc.mdparse import parse_markdown


def snapshot():
    return {
        "revisionId": "before",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-one", "title": "Notes"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 5,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 5,
                                            "textRun": {"content": "Old\n"},
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                },
            }
        ],
    }


def service(mocker):
    chain = mocker.patch(
        "gdoc.api.docs.get_docs_service"
    ).return_value.documents.return_value
    chain.batchUpdate.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": "after"},
    }
    return chain


def test_targeted_revision_is_from_acknowledgment(mocker):
    service(mocker)
    details = {}
    result = replace_formatted(
        "doc",
        [{"startIndex": 1, "endIndex": 4}],
        "New",
        "before",
        result_details=details,
    )
    assert result == 1
    assert (
        details["input_revision_id"],
        details["acknowledged_revision_id"],
        details["rebased"],
    ) == ("before", "after", False)


def test_general_split_and_reorder_are_native(mocker):
    chain = service(mocker)
    result = insert_markdown_into_tab(
        "doc", "Notes", "## Moved\n\nSplit\n\nApart", replace=True, document=snapshot()
    )
    batch = chain.batchUpdate.call_args.kwargs["body"]
    assert batch["writeControl"] == {"requiredRevisionId": "before"}
    assert [
        r["insertText"]["text"] for r in batch["requests"] if "insertText" in r
    ] == [
        "Moved\n\nSplit\n\nApart",
    ]
    assert result["input_revision_id"] == "before"
    assert result["acknowledged_revision_id"] == "after"
    assert result["rebased"] is False


def test_table_alignment_and_explicit_emphasis_only():
    table = parse_markdown("| Plain | **Bold** |\n| :--- | ---: |\n| a | b |").tables[0]
    requests = _table_cell_requests([[5, 7], [11, 13]], table, "tab-one")
    styles = [r["updateTextStyle"] for r in requests if "updateTextStyle" in r]
    assert len(styles) == 1
    assert styles[0]["range"] == {"startIndex": 12, "endIndex": 16, "tabId": "tab-one"}
    alignments = [
        r["updateParagraphStyle"]["paragraphStyle"]["alignment"]
        for r in requests
        if "updateParagraphStyle" in r
    ]
    assert alignments == ["START", "END", "START", "END"]


def test_code_markers_include_final_mark_and_ignore_code_tabs():
    parsed = parse_markdown("- parent\n  - child\n\n```\n\tliteral 😀\n```")
    _strip_trailing_newline_unless_hr(parsed)
    requests = _code_range_requests(parsed, 1, "tab-one")
    assert requests == [
        {
            "createNamedRange": {
                "name": "gdoc:code:v1",
                "range": {
                    "startIndex": 15,
                    "endIndex": 27,
                    "tabId": "tab-one",
                },
            }
        }
    ]


def test_non_default_start_is_explicit_best_effort(mocker, capsys):
    chain = service(mocker)
    insert_markdown_into_tab(
        "doc", "Notes", "7. Seven", replace=True, document=snapshot()
    )
    assert "will start at 1" in capsys.readouterr().err
    assert any(
        "createParagraphBullets" in r
        for r in chain.batchUpdate.call_args.kwargs["body"]["requests"]
    )


def test_multi_tab_default_preserves_siblings_and_uses_native_revision(mocker):
    from copy import deepcopy

    from gdoc.api.drive import update_doc_content

    document = snapshot()
    sibling = deepcopy(document["tabs"][0])
    sibling["tabProperties"] = {"tabId": "sibling", "title": "Other"}
    document["tabs"].append(sibling)
    chain = service(mocker)
    drive = mocker.patch("gdoc.api.drive.get_drive_service")
    mocker.patch("gdoc.api.drive.require_write_version")
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 99})
    details = {}
    assert (
        update_doc_content(
            "doc", "New", document=document, expected_version=1, result_details=details
        )
        == 99
    )
    drive.assert_not_called()
    chain.batchUpdate.assert_called_once()
    assert details["acknowledged_revision_id"] == "after"
    assert not any(
        "deleteTab" in r for r in chain.batchUpdate.call_args.kwargs["body"]["requests"]
    )


def test_authorized_collapse_pins_sibling_deletion_to_ack(mocker):
    from copy import deepcopy

    from gdoc.api.drive import update_doc_content

    document = snapshot()
    sibling = deepcopy(document["tabs"][0])
    sibling["tabProperties"] = {"tabId": "sibling", "title": "Other"}
    document["tabs"].append(sibling)
    chain = service(mocker)
    chain.batchUpdate.return_value.execute.side_effect = [
        {"writeControl": {"requiredRevisionId": "after"}},
        {"writeControl": {"requiredRevisionId": "collapsed"}},
    ]
    mocker.patch("gdoc.api.drive.require_write_version")
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 99})
    details = {}
    update_doc_content(
        "doc",
        "New",
        document=document,
        expected_version=1,
        collapse_tabs=True,
        result_details=details,
    )
    assert chain.batchUpdate.call_args.kwargs["body"] == {
        "requests": [{"deleteTab": {"tabId": "sibling"}}],
        "writeControl": {"requiredRevisionId": "after"},
    }
    assert details["acknowledged_revision_id"] == "collapsed"


def test_image_reuse_and_external_insertion_share_first_atomic_batch(mocker, capsys):
    document = snapshot()
    document["tabs"][0]["documentTab"]["inlineObjects"] = {
        "original": {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "imageProperties": {
                        "contentUri": "https://example.org/current-image"
                    },
                }
            },
        }
    }
    chain = service(mocker)
    insert_markdown_into_tab(
        "doc",
        "Notes",
        "😀 ![old](gdoc-image:original) ![](https://example.org/new)",
        replace=True,
        document=document,
    )
    batch = chain.batchUpdate.call_args.kwargs["body"]
    images = [
        r["insertInlineImage"] for r in batch["requests"] if "insertInlineImage" in r
    ]
    assert images == [
        {
            "location": {"index": 4, "tabId": "tab-one"},
            "uri": "https://example.org/current-image",
        },
        {
            "location": {"index": 6, "tabId": "tab-one"},
            "uri": "https://example.org/new",
        },
    ]
    assert batch["requests"][0]["deleteContentRange"]["range"]["endIndex"] == 4
    assert "cannot set image alt text" in capsys.readouterr().err
    chain.batchUpdate.assert_called_once()


def test_invalid_image_source_cannot_send_deletion(mocker):
    import pytest

    from gdoc.util import GdocError

    chain = service(mocker)
    for source in ("file:///private/image.png", "gdoc-image:missing", "javascript:x"):
        with pytest.raises(GdocError):
            insert_markdown_into_tab(
                "doc", "Notes", f"![]({source})", replace=True, document=snapshot()
            )
    chain.batchUpdate.assert_not_called()


def test_inline_target_image_has_width_one_and_preserves_neighbor_ranges(mocker):
    chain = service(mocker)
    body = snapshot()["tabs"][0]["documentTab"]["body"]
    replace_formatted(
        "doc",
        [{"startIndex": 2, "endIndex": 3}],
        "![sample](https://example.org/image)",
        "before",
        body=body,
    )
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert requests[0] == {
        "deleteContentRange": {"range": {"startIndex": 2, "endIndex": 3}}
    }
    assert next(
        r["insertInlineImage"] for r in requests if "insertInlineImage" in r
    ) == {
        "location": {"index": 2},
        "uri": "https://example.org/image",
    }


def test_images_in_cells_are_native_objects_not_text_styles():
    table = parse_markdown(
        "| ![](https://example.org/image) |\n| --- |\n| words |"
    ).tables[0]
    requests = _table_cell_requests([[5], [9]], table, "tab-one")
    assert any("insertInlineImage" in r for r in requests)
    assert not any(
        "uri" in r.get("updateTextStyle", {}).get("textStyle", {}) for r in requests
    )


def test_mixed_children_keep_parent_batch_and_restore_semantic_indent(mocker):
    chain = service(mocker)
    insert_markdown_into_tab(
        "doc",
        "Notes",
        "1. parent\n  - child\n2. sibling",
        replace=True,
        document=snapshot(),
    )
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    creates = [
        r["createParagraphBullets"] for r in requests if "createParagraphBullets" in r
    ]
    assert len(creates) == 2
    assert creates[0]["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"
    assert creates[1]["bulletPreset"].startswith("BULLET_")
    paragraphs = apply_list_requests(requests)
    assert [(p["text"], p["depth"]) for p in paragraphs] == [
        ("parent\n", 0), ("child\n", 1), ("sibling\n", 0)]
    assert paragraphs[0]["list"] == paragraphs[2]["list"] != paragraphs[1]["list"]


def test_terminal_empty_list_styles_retained_native_mark(mocker):
    chain = service(mocker)
    insert_markdown_into_tab("doc", "Notes", "- ", replace=True, document=snapshot())
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert next(
        r["createParagraphBullets"]["range"]
        for r in requests
        if "createParagraphBullets" in r
    ) == {
        # The temporary anchor precedes the item, which keeps the tab's mark.
        "startIndex": 2,
        "endIndex": 4,
        "tabId": "tab-one",
    }


def test_terminal_empty_item_after_worded_item_is_included(mocker):
    chain = service(mocker)
    insert_markdown_into_tab(
        "doc", "Notes", "- words\n- ", replace=True, document=snapshot()
    )
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert next(
        r["createParagraphBullets"]["range"]
        for r in requests
        if "createParagraphBullets" in r
    ) == {
        "startIndex": 2,
        "endIndex": 10,
        "tabId": "tab-one",
    }


def test_segment_inline_code_space_is_not_an_empty_rendering(mocker):
    chain = service(mocker)
    replace_formatted(
        "doc",
        [{"startIndex": 1, "endIndex": 2, "segmentId": "header"}],
        "` `",
        "before",
    )
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert next(r["insertText"] for r in requests if "insertText" in r) == {
        "location": {"index": 1, "segmentId": "header"},
        "text": " ",
    }


@pytest.mark.parametrize(
    "text", ["# literal", "- literal", "1. literal", "> literal", "---"]
)
def test_segment_block_punctuation_is_literal_inline_text(mocker, text):
    chain = service(mocker)
    replace_formatted(
        "doc", [{"startIndex": 1, "endIndex": 4, "segmentId": "header"}], text, "before"
    )
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert next(r["insertText"]["text"] for r in requests if "insertText" in r) == text
    assert not any(
        "updateParagraphStyle" in r or "createParagraphBullets" in r for r in requests
    )


def _apply_text_and_images(text, requests):
    """Check only character placement in a single batch, not Docs structure/style."""
    data = text.encode("utf-16-le")
    for request in requests:
        if "deleteContentRange" in request:
            span = request["deleteContentRange"]["range"]
            start, end = (span["startIndex"] - 1) * 2, (span["endIndex"] - 1) * 2
            assert 0 <= start < end <= len(data) - 2
            data = data[:start] + data[end:]
        elif "insertText" in request or "insertInlineImage" in request:
            operation = request.get("insertText", request.get("insertInlineImage"))
            start = (operation["location"]["index"] - 1) * 2
            assert 0 <= start <= len(data) - 2
            inserted = operation.get("text", "\ufffc").encode("utf-16-le")
            data = data[:start] + inserted + data[start:]
    return data.decode("utf-16-le")


@pytest.mark.parametrize(
    "markdown,expected,uri",
    [
        (
            "Revised ![](gdoc-image:original)",
            "Revised \ufffc\n",
            "https://example.org/fresh",
        ),
        (
            "![](gdoc-image:original) moved",
            "\ufffc moved\n",
            "https://example.org/fresh",
        ),
        (
            "![](https://example.org/new) replaced",
            "\ufffc replaced\n",
            "https://example.org/new",
        ),
        ("Image removed", "Image removed\n", None),
    ],
)
def test_existing_image_changed_rewrite_preserves_moves_replaces_or_removes(
    mocker,
    markdown,
    expected,
    uri,
):
    document = snapshot()
    tab = document["tabs"][0]["documentTab"]
    tab["inlineObjects"] = {
        "original": {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "imageProperties": {"contentUri": "https://example.org/fresh"}
                },
            }
        }
    }
    tab["body"]["content"] = [
        {
            "startIndex": 1,
            "endIndex": 6,
            "paragraph": {
                "elements": [
                    {"startIndex": 1, "endIndex": 4, "textRun": {"content": "Old"}},
                    {
                        "startIndex": 4,
                        "endIndex": 5,
                        "inlineObjectElement": {"inlineObjectId": "original"},
                    },
                    {"startIndex": 5, "endIndex": 6, "textRun": {"content": "\n"}},
                ],
            },
        }
    ]
    chain = service(mocker)
    insert_markdown_into_tab("doc", "Notes", markdown, replace=True, document=document)
    requests = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert _apply_text_and_images("Old\ufffc\n", requests) == expected
    assert [
        r["insertInlineImage"]["uri"] for r in requests if "insertInlineImage" in r
    ] == ([uri] if uri else [])
    chain.batchUpdate.assert_called_once()


def test_existing_nondefault_list_supports_general_and_targeted_edit(mocker, capsys):
    document = snapshot()
    tab = document["tabs"][0]["documentTab"]
    tab["body"]["content"][0]["paragraph"]["bullet"] = {"listId": "ordered"}
    tab["lists"] = {
        "ordered": {
            "listProperties": {
                "nestingLevels": [
                    {"glyphType": "DECIMAL", "startNumber": 9},
                ]
            }
        }
    }
    chain = service(mocker)
    replace_formatted(
        "doc", [{"startIndex": 1, "endIndex": 4}], "Revised", "before", body=tab["body"]
    )
    targeted = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert not any(
        "createParagraphBullets" in r or "deleteParagraphBullets" in r for r in targeted
    )
    insert_markdown_into_tab(
        "doc", "Notes", "9. Revised", replace=True, document=document
    )
    assert "starts at 9 (reset to 1)" in capsys.readouterr().err
    general = chain.batchUpdate.call_args.kwargs["body"]["requests"]
    assert any("createParagraphBullets" in r for r in general)
