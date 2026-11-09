"""Concrete final-review regressions at native service boundaries."""
import pytest

from gdoc import mcp
from gdoc.api.docs import _native_docs_requests, _prepare_image_sources
from gdoc.mdparse import parse_markdown


def test_existing_image_reconstruction_keeps_native_size():
    size = {"width": {"magnitude": 120, "unit": "PT"},
            "height": {"magnitude": 80, "unit": "PT"}}
    snapshot = {"inlineObjects": {"image": {"inlineObjectProperties": {
        "embeddedObject": {"size": size, "imageProperties": {
            "contentUri": "https://example.org/fresh.png",
        }},
    }}}}
    parsed = parse_markdown("Changed ![](gdoc-image:image)")
    _prepare_image_sources(parsed, snapshot)
    requests = _native_docs_requests(parsed, 1, "main")
    image = next(r["insertInlineImage"] for r in requests if "insertInlineImage" in r)
    assert image["objectSize"] == size
    assert image["uri"] == "https://example.org/fresh.png"


@pytest.mark.parametrize("command", ["write"])
def test_mcp_rejects_combined_tab_inspection_before_any_service_call(mocker, command):
    fetch = mocker.patch("gdoc.api.docs.get_document_with_tabs")
    _, stderr, code = mcp.call_command(command, {
        "doc": "synthetic", "text": "=== Tab: First ===\nA\n=== Tab: Second ===\nB\n",
        "quiet": True,
    })
    assert code == 3
    assert "each tab separately" in stderr
    fetch.assert_not_called()


def test_image_aliases_follow_only_acknowledged_replacements():
    from gdoc.state import load_state, record_content_read, record_content_write

    record_content_read("doc", ["main"], "r1")
    record_content_write("doc", input_revision_id="r1", acknowledged_revision_id="r2",
                         image_reference_ids={"original": "second"})
    record_content_write("doc", input_revision_id="r2", acknowledged_revision_id="r3",
                         image_reference_ids={"second": "third"})
    assert load_state("doc").image_reference_ids["original"] == "third"
    record_content_write("doc", input_revision_id="r3", acknowledged_revision_id="",
                         image_reference_ids={"third": "uncertain"})
    assert load_state("doc").image_reference_ids["original"] == "third"


def test_native_image_aliases_come_from_actual_batch_replies(mocker):
    from gdoc.api.docs import _StagedWrite

    mocker.patch("gdoc.api.docs.get_docs_service")
    mocker.patch("gdoc.api.comment_transport.execute_mutation_request", return_value={
        "writeControl": {"requiredRevisionId": "r2"},
        "replies": [{}, {"insertInlineImage": {"objectId": "new-image"}}],
    })
    stage = _StagedWrite("doc")
    stage.batch("images", [{"deleteContentRange": {}}, {"insertInlineImage": {
        "uri": "https://example.org/image.png", "location": {"index": 1},
    }}], "r1")
    assert stage.inserted_images == {
        ("https://example.org/image.png", ""): ["new-image"],
    }


def test_default_table_styles_do_not_warn_about_nonexistent_losses(capsys):
    from gdoc.lossy import check_markdown_replacement

    check_markdown_replacement({"content": [{"paragraph": {
        "elements": [{"textRun": {"content": "Cargo\n", "textStyle": {}}}],
        "paragraphStyle": {
            "lineSpacing": 100, "spaceAbove": {"unit": "PT"},
            "spaceBelow": {"unit": "PT"}, "indentStart": {"unit": "PT"},
            "indentEnd": {"unit": "PT"}, "indentFirstLine": {"unit": "PT"},
            "keepWithNext": False, "keepLinesTogether": False,
            "avoidWidowAndOrphan": False, "pageBreakBefore": False,
        },
    }}]}, tab_body=True)
    assert capsys.readouterr().err == ""


def test_file_read_reuses_supplied_snapshot_without_affecting_cat(mocker):
    from gdoc.cli import _read_native_tab

    fetch = mocker.patch("gdoc.api.docs.get_document_with_tabs")
    snapshot = {"revisionId": "r1", "tabs": [{
        "tabProperties": {"tabId": "main", "title": "Main"},
        "documentTab": {"body": {"content": []}},
    }]}
    text, actual, tab = _read_native_tab("doc", document=snapshot)
    assert actual is snapshot and tab["id"] == "main" and text == ""
    fetch.assert_not_called()
