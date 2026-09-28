"""Independent human-task probes. Failures are release gaps, never xfailed."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import paragraph, tab

from gdoc.api.docs import get_tab_text
from gdoc.mdparse import parse_markdown, utf16_len


def existing_helper(module, name):
    """Reuse small existing test helpers without importing their test cases."""
    path = Path(__file__).parents[1] / (module + ".py")
    spec = importlib.util.spec_from_file_location("acceptance_" + module, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return getattr(loaded, name)


def requests(scenario):
    return [r for batch in scenario.batches for r in batch["requests"]]


def read(scenario):
    return json.loads(scenario.ok("cat", tab="draft", json=True))["content"]


def write(scenario, markdown):
    scenario.ok("write", tab="draft", text=markdown)
    assert scenario.batches
    assert scenario.batches[-1]["writeControl"] == {"requiredRevisionId": "r1"}


def replay_text(scenario, original):
    return existing_helper("test_paragraph_edits", "_apply_text_requests")(
        original, requests(scenario)
    )


def test_t01_targeted_edit_preserves_neighbors(scenario):
    content = [
        paragraph("Harbor plan\n", style="HEADING_2"),
        paragraph("Original sentence.\n", 13),
        paragraph("Linked guide\n", 32),
        paragraph("Nested item\n", 45, bullet={"listId": "list", "nestingLevel": 1}),
    ]
    content[2]["paragraph"]["elements"][0]["textRun"]["textStyle"] = {
        "link": {"url": "https://example.invalid/guide"}
    }
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = content
    before = deepcopy(scenario.document["tabs"][0]["documentTab"]["body"])
    read(scenario)
    scenario.ok(
        "edit", tab="draft", old_text="Original sentence.", new_text="Revised sentence."
    )
    assert (
        replay_text(scenario, before)
        == "Harbor plan\nRevised sentence.\nLinked guide\nNested item\n"
    )
    for request in requests(scenario):
        data = next(iter(request.values()))
        address = data.get("range", data.get("location", {}))
        assert 13 <= address.get("startIndex", address.get("index", 13)) < 32
        assert address.get("endIndex", 31) <= 31
    scenario.record["evidence"] = "text_replay_and_untouched_neighbor_ranges"


def test_t02_split_and_merge_general_route(scenario):
    read(scenario)
    before = deepcopy(scenario.document["tabs"][0]["documentTab"]["body"])
    write(scenario, "Original\nsentence.\n")
    assert replay_text(scenario, before) == "Original\nsentence.\n"
    # Reread the simulated text result through the same public interface.
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [
        paragraph("Original\n"),
        paragraph("sentence.\n", 10),
    ]
    assert read(scenario) == "Original\nsentence.\n"
    scenario.service.documents.return_value.batchUpdate.reset_mock()
    before = deepcopy(scenario.document["tabs"][0]["documentTab"]["body"])
    write(scenario, "Original sentence.\n")
    assert replay_text(scenario, before) == "Original sentence.\n"
    scenario.record["evidence"] = "text_replay_only_paragraph_style_not_simulated"


def test_t03_move_section_insert_section_request_order(scenario):
    read(scenario)
    write(
        scenario,
        "# Second\nMoved paragraph\n- Cargo\n# New\nNew paragraph\n# First\nTail\n",
    )
    # The body's text is one insert; later ones are the list's temporary
    # separator and anchor, removed in the same batch.
    inserted = next(
        r["insertText"]["text"] for r in requests(scenario) if "insertText" in r
    )
    assert inserted == "Second\nMoved paragraph\nCargo\nNew\nNew paragraph\nFirst\nTail"
    assert sum("createParagraphBullets" in r for r in requests(scenario)) == 1
    scenario.record["gap"] = (
        "Section containing a table needs integrated structural readback"
    )


@pytest.mark.parametrize("changed", [False, True], ids=["unchanged", "changed"])
def test_t04_combined_roundtrip_preserves_link_heading_list(scenario, changed):
    p = paragraph("Compass\n", style="HEADING_2")
    q = paragraph("Original sentence.\n", p["endIndex"])
    q["paragraph"]["elements"][0]["textRun"]["textStyle"] = {
        "bold": True,
        "italic": True,
        "link": {"url": "https://example.invalid/a_(b)"},
    }
    r = paragraph("Cargo\n", q["endIndex"], bullet={"listId": "l"})
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [p, q, r]
    source = read(scenario)
    if not changed:
        scenario.ok("write", tab="draft", text=source)
        if not scenario.batches:
            assert read(scenario) == source
            scenario.record["evidence"] = "unchanged_noop_and_native_snapshot_read"
            return
    else:
        write(scenario, source.replace("Original", "Revised"))
    reqs = requests(scenario)
    inserted = next(r["insertText"]["text"] for r in reqs if "insertText" in r)
    assert (
        inserted == f"Compass\n{'Revised' if changed else 'Original'} sentence.\nCargo"
    )
    styles = [r["updateTextStyle"]["textStyle"] for r in reqs if "updateTextStyle" in r]
    assert any(s.get("bold") for s in styles)
    assert any(s.get("italic") for s in styles)
    assert any(
        s.get("link", {}).get("url") == "https://example.invalid/a_(b)" for s in styles
    )
    assert any(
        r.get("updateParagraphStyle", {})
        .get("paragraphStyle", {})
        .get("namedStyleType")
        == "HEADING_2"
        for r in reqs
    )
    assert any("createParagraphBullets" in r for r in reqs)
    scenario.record["gap"] = (
        "Request assertions do not establish native combined roundtrip; "
        "code/table/image combination remains unverified"
    )


@pytest.mark.parametrize(
    "replacement",
    [
        "***[Revised](https://example.invalid/b_(c))***",
        "Revised *literal punctuation?*",
    ],
)
def test_t05_restyle_retarget_and_remove_stale_link(scenario, replacement):
    run = scenario.document["tabs"][0]["documentTab"]["body"]["content"][0][
        "paragraph"
    ]["elements"][0]
    run["textRun"]["textStyle"] = {
        "link": {"url": "https://example.invalid/old"},
        "bold": True,
    }
    read(scenario)
    scenario.ok(
        "edit", tab="draft", old_text="Original sentence.", new_text=replacement
    )
    styles = [
        r["updateTextStyle"] for r in requests(scenario) if "updateTextStyle" in r
    ]
    assert any("link" in s["fields"] for s in styles)
    normalized = deepcopy(requests(scenario))
    for request in normalized:
        data = next(iter(request.values()))
        address = data.get("range", data.get("location", {}))
        if "tabId" in address:
            address["tabId"] = "tab"
    result = existing_helper("test_replacement_styles", "_replacement_styles")(
        normalized, run["textRun"]["textStyle"]
    )
    assert all(
        s.get("link", {}).get("url") != "https://example.invalid/old" for s in result
    )
    if "https://example.invalid/b_(c)" in replacement:
        assert all(
            s.get("link", {}).get("url") == "https://example.invalid/b_(c)"
            for s in result
        )
        assert all(s.get("bold") and s.get("italic") for s in result)
    scenario.record["evidence"] = "text_style_mask_replay"


def test_t06_mixed_lists_empty_item_and_number_start(scenario):
    read(scenario)
    write(scenario, "3. Cargo\n   - Fragile\n4. Dock\n\n7. Restart\n- \n")
    reqs = requests(scenario)
    assert any("createParagraphBullets" in r for r in reqs)
    output = scenario.record["commands"][-1]
    assert "will start at 1" in output["output"] + output["stderr"]
    scenario.record["outcome_kind"] = "agreed_api_best_effort_not_full_numbering"
    scenario.record["gap"] = (
        "New non-1 starts/restarts cannot be claimed as full native support"
    )


def test_t07_table_reorder_styled_cells_request_content(scenario):
    read(scenario)
    # A fixed post-insert empty 2x2 table snapshot; no table emulator.
    make_table = existing_helper("test_native_targets", "table")
    blank = make_table([["\n", "\n"], ["\n", "\n"]], start=2)
    snapshot = deepcopy(scenario.document)
    snapshot["revisionId"] = "r2"
    snapshot["tabs"][0]["documentTab"]["body"]["content"] = [
        paragraph("\n"),
        blank,
        paragraph("\n", blank["endIndex"]),
    ]
    get = scenario.service.documents.return_value.get.return_value.execute
    get.side_effect = lambda **kw: deepcopy(
        snapshot if len(scenario.batches) >= 2 else scenario.document
    )
    scenario.ok(
        "write",
        tab="draft",
        text="| Port | **Load** |\n|:---|---:|\n| East | *Seven* |\n",
    )
    reqs = requests(scenario)
    assert any("insertTable" in r for r in reqs)
    inserted = [r["insertText"]["text"] for r in reqs if "insertText" in r]
    assert {"Port", "Load", "East", "Seven"} <= set(inserted)
    # Alignment is semantic, not merely successful insertion of cell text.
    alignments = {
        r["updateParagraphStyle"]["paragraphStyle"].get("alignment")
        for r in reqs
        if "updateParagraphStyle" in r
    }
    assert {"START", "END"} <= alignments


def test_t08_literal_fence_quote_rule(scenario):
    read(scenario)
    write(
        scenario, "> Harbor note\n\n```text\n`closed`\n# literal\n\nnext\n```\n\n---\n"
    )
    inserted = "".join(
        r["insertText"]["text"] for r in requests(scenario) if "insertText" in r
    )
    assert "`closed`\n# literal\n\nnext\n" in inserted
    assert "Harbor note" in inserted
    assert any(
        "borderBottom" in r.get("updateParagraphStyle", {}).get("paragraphStyle", {})
        for r in requests(scenario)
    )


def test_t09_tab_id_precedes_title_for_read_and_edit(scenario):
    scenario.document["tabs"].insert(
        0, tab("decoy", "draft", [paragraph("Decoy sentence.\n")])
    )
    assert "Original sentence." in read(scenario)
    scenario.ok("edit", tab="draft", old_text="Original", new_text="Revised")
    assert all(
        next(iter(r.values()))
        .get("range", next(iter(r.values())).get("location", {}))
        .get("tabId")
        == "draft"
        for r in requests(scenario)
    )


def test_t10_successive_writes_use_acknowledged_revision(scenario):
    read(scenario)
    write(scenario, "First revision\n")
    scenario.version = 2
    scenario.document["revisionId"] = "r2"
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [
        paragraph("First revision\n")
    ]
    scenario.ok("write", tab="draft", text="Second revision\n")
    assert scenario.batches[-1]["writeControl"] == {"requiredRevisionId": "r2"}


def test_t10_collaborator_change_refuses(scenario):
    read(scenario)
    scenario.version = 2
    scenario.document["revisionId"] = "foreign"
    code, out, err = scenario.call("write", tab="draft", text="Overwrite\n")
    assert code != 0, out + err
    assert not scenario.batches


def test_t10_lost_response_is_not_replayed(scenario):
    read(scenario)
    execute = scenario.service.documents.return_value.batchUpdate.return_value.execute
    execute.side_effect = TimeoutError("synthetic lost response")
    code, out, err = scenario.call("write", tab="draft", text="Uncertain\n")
    assert code != 0
    assert len(scenario.batches) == 1
    assert "uncertain" in (out + err).lower()


def test_t11_raw_and_selected_structure_burden(scenario):
    scenario.document["tabs"].append(
        tab(
            "archive",
            "Archive",
            [
                paragraph("Archive " + str(i) + " " + "x" * 120 + "\n", 1 + i * 140)
                for i in range(400)
            ],
        )
    )
    raw = json.loads(scenario.ok("structure", json=True))
    scoped = json.loads(scenario.ok("structure", tab="draft", json=True))
    assert len(raw["document"]["tabs"]) == 2
    assert scoped["document"]["tab"]["tabProperties"]["tabId"] == "draft"
    assert "Original sentence." in json.dumps(scoped)
    scenario.record["evidence"] = "public_output_comparison"
    scenario.record["gap"] = (
        "Within-one-long-tab target selection still needs candidate comparison"
    )


@pytest.mark.parametrize("scope", ["prefix", "tab", "metadata"])
def test_t12_partial_read_cannot_authorize_whole_overwrite(scenario, scope):
    if scope == "prefix":
        output = scenario.ok("cat", max_bytes=8, json=True)
        # A partial-output marker must be visible to the agent.
        scenario.record["partial_output_marked"] = any(
            word in output.lower() for word in ("truncat", "partial")
        )
    elif scope == "tab":
        scenario.document["tabs"].append(
            tab("unseen", "Unseen", [paragraph("Private sibling\n")])
        )
        scenario.ok("cat", tab="unseen", json=True)
    else:
        scenario.ok("structure", fields="documentId,title,revisionId", json=True)
    code, out, err = scenario.call("write", text="Replacement\n")
    assert code != 0, out + err
    assert not scenario.batches
    # A refusal for lack of read coverage, not incidental tab-collapse protection.
    assert any(word in (out + err).lower() for word in ("baseline", "read", "scope"))


@pytest.mark.parametrize("native", ["inlineObjectElement", "footnoteReference"])
def test_t13_local_edit_beside_native_object(scenario, native):
    p = paragraph("Original sentence.\n")
    p["paragraph"]["elements"].insert(
        0,
        {
            "startIndex": 1,
            "endIndex": 2,
            native: {"inlineObjectId": "image"}
            if native == "inlineObjectElement"
            else {"footnoteId": "note"},
        },
    )
    p["paragraph"]["elements"][1].update(startIndex=2, endIndex=21)
    p["endIndex"] = 21
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [p]
    scenario.ok("edit", tab="draft", old_text="Original", new_text="Revised")
    for r in requests(scenario):
        if "deleteContentRange" in r:
            assert r["deleteContentRange"]["range"]["startIndex"] >= 2
    if native == "inlineObjectElement":
        return  # Explicit image removal belongs to T14, not rich-loss consent.
    count = len(scenario.batches)
    read(scenario)
    code, out, err = scenario.call("write", tab="draft", text="Discard image\n")
    assert code != 0 and len(scenario.batches) == count, out + err


@pytest.mark.parametrize(
    "operation,markdown",
    [
        ("insert", "Before\n![Map](https://example.invalid/map.png)\nAfter\n"),
        ("move", "![Map](https://example.invalid/map.png)\nBefore\nAfter\n"),
        ("replace", "Before\n![Map](https://example.invalid/new-map.png)\nAfter\n"),
    ],
)
def test_t14_image_markdown_route(scenario, operation, markdown):
    if operation != "insert":
        existing_image(scenario)
    read(scenario)
    write(scenario, markdown)
    assert any(
        "insertInlineImage" in r or "replaceImage" in r for r in requests(scenario)
    )
    scenario.record["gap"] = (
        "Request-level only; source retrieval, move/removal "
        "and durable references need integrated native result evidence"
    )


def test_t15_unicode_space_fallback_visible_in_read(scenario, monkeypatch):
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [
        paragraph("Harbor\u00a0cargo\u2009ready\n")
    ]
    comment = {
        "id": "c1",
        "content": "Check manifest",
        "resolved": False,
        "quotedFileContent": {"value": "Harbor cargo ready"},
        "author": {"displayName": "Reviewer"},
        "replies": [],
    }
    monkeypatch.setattr("gdoc.api.comments.list_comments", lambda *a, **kw: [comment])
    output = scenario.ok("cat", comments=True)
    assert "Check manifest" in output
    assert "[anchor deleted]" not in output
    assert "[UNANCHORED]" not in output


def test_t15_invalid_occurrence_is_local(scenario):
    code, out, err = scenario.call(
        "comment", text="Check manifest", quote="Original", occurrence=0
    )
    assert code != 0, out + err
    assert not scenario.service.documents.return_value.get.called
    assert all(mock.call_count == 0 for mock in scenario.boundaries.values())
    assert not scenario.batches


def test_d01_code_crlf_normalization():
    parsed = parse_markdown("```\r\nalpha\r\nbeta\r\n```\r\n")
    assert "\r" not in parsed.plain_text
    assert "alpha\nbeta\n" in parsed.plain_text


def test_d12_closed_span_inside_fence_stays_literal():
    parsed = parse_markdown("```\n`closed`\n# literal\n```\n")
    assert parsed.plain_text == "`closed`\n# literal\n"


def test_d08_literal_list_whitespace_survives_export():
    item = paragraph("  Cargo\n", bullet={"listId": "l"})
    markdown = get_tab_text({"body": {"content": [item]}}, markdown=True)
    assert parse_markdown(markdown).plain_text == "  Cargo\n"


def test_helper_utf16_unit():
    assert utf16_len("a😀b") == 4


def test_t12_prefix_output_explicitly_identifies_partial_content(scenario):
    output = scenario.ok("cat", max_bytes=8, json=True)
    assert any(word in output.lower() for word in ("truncat", "partial"))


def test_t12_full_read_authorizes_known_tab(scenario):
    read(scenario)
    write(scenario, "Known replacement\n")


def test_t15_duplicate_quote_refuses_without_comment(scenario):
    content = [paragraph("Original sentence.\n"), paragraph("Original sentence.\n", 20)]
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = content
    code, output, error = scenario.call(
        "comment", text="Check manifest", quote="Original sentence."
    )
    assert code != 0
    assert "ambiguous" in (output + error).lower()
    assert not scenario.batches


def test_d02_positional_crlf_matches_file_normalization():
    from types import SimpleNamespace

    from gdoc.cli import _resolve_replacement_text

    args = SimpleNamespace(old_text="Cargo\r\n", new_text="Freight\r\n")
    assert _resolve_replacement_text(args, None) == ("Cargo", "Freight")


def test_d07_heading_list_semantics_survive_roundtrip():
    source = paragraph("Cargo\n", style="HEADING_2", bullet={"listId": "l"})
    parsed = parse_markdown(
        get_tab_text({"body": {"content": [source]}}, markdown=True)
    )
    assert parsed.plain_text == "Cargo\n"
    assert any(s.type == "bullets" for s in parsed.styles)
    assert any(s.style.get("namedStyleType") == "HEADING_2" for s in parsed.styles)


def test_d13_image_after_backslash_code_span_is_not_literal():
    # The code span ends before the image: parser and native image planner must agree.
    parsed = parse_markdown("`path\\` ![Map](https://example.invalid/map.png)")
    images = getattr(parsed, "images", [])
    assert len(images) == 1
    assert images[0].uri == "https://example.invalid/map.png"


def test_d15_unmatched_link_openers_have_bounded_parse_time():
    import time

    from gdoc.mdparse import parse_inline

    text = "[" * 4000
    started = time.perf_counter()
    parsed, styles = parse_inline(text)
    assert time.perf_counter() - started < 2.0
    assert parsed == text and not styles


@pytest.mark.parametrize("replacement", ["# literal marker", "`closed span`"])
def test_d05_inline_header_replacement_uses_inline_context(scenario, replacement):
    scenario.document["tabs"][0]["documentTab"]["headers"] = {
        "header": {"content": [paragraph("Prefix TOKEN suffix\n", 0)]}
    }
    scenario.ok("edit", tab="draft", old_text="TOKEN", new_text=replacement)
    assert scenario.batches
    assert all(
        next(iter(r.values()))
        .get("range", next(iter(r.values())).get("location", {}))
        .get("segmentId")
        == "header"
        for r in requests(scenario)
    )


def existing_image(scenario):
    before = paragraph("Before\n")
    image = paragraph("\n", 9)
    image["startIndex"] = 8
    image["paragraph"]["elements"].insert(
        0,
        {
            "startIndex": 8,
            "endIndex": 9,
            "inlineObjectElement": {"inlineObjectId": "map"},
        },
    )
    native = scenario.document["tabs"][0]["documentTab"]
    native["body"]["content"] = [before, image, paragraph("After\n", 10)]
    native["inlineObjects"] = {
        "map": {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "title": "Map",
                    "imageProperties": {
                        "sourceUri": "https://example.invalid/map.png",
                        "contentUri": "https://example.invalid/temporary-map.png",
                    },
                }
            }
        }
    }


def test_t14_remove_image_via_explicit_rewrite(scenario):
    existing_image(scenario)
    read(scenario)
    write(scenario, "Before\nAfter\n")
    assert not any("insertInlineImage" in r for r in requests(scenario))
    assert any("deleteContentRange" in r for r in requests(scenario))
    assert (
        "".join(
            r["insertText"]["text"] for r in requests(scenario) if "insertText" in r
        )
        == "Before\nAfter"
    )


def test_t11_locate_heading_within_long_tab(scenario, monkeypatch):
    content = []
    index = 1
    for i in range(400):
        p = paragraph(f"Archive entry {i}: " + "x" * 120 + "\n", index)
        content.append(p)
        index = p["endIndex"]
    target = paragraph("Departure checklist\n", index, style="HEADING_2")
    target["paragraph"]["paragraphStyle"]["headingId"] = "h.departure"
    content.insert(200, target)
    # Assign monotonic native indices after inserting the target heading.
    index = 1
    for p in content:
        text = p["paragraph"]["elements"][0]["textRun"]["content"]
        p["startIndex"], p["endIndex"] = index, index + utf16_len(text)
        p["paragraph"]["elements"][0].update(startIndex=index, endIndex=p["endIndex"])
        index = p["endIndex"]
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = content
    result = json.loads(scenario.ok("toc", tab="draft", json=True))
    assert result["headings"] == [
        {
            "text": "Departure checklist",
            "level": 2,
            "heading_id": "h.departure",
            "link": "https://docs.google.com/document/d/synthetic/edit?tab=draft#heading=h.departure",
        }
    ]
    raw = json.loads(scenario.ok("structure", tab="draft", json=True))
    paragraphs = raw["document"]["tab"]["documentTab"]["body"]["content"]
    assert (
        paragraphs[200]["paragraph"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    )
    monkeypatch.setattr(
        "gdoc.state.STATE_DIR", scenario.tmp_path / "direct-heading-state"
    )
    focused = json.loads(
        scenario.ok("structure", tab="draft", heading="Departure checklist", json=True)
    )["document"]
    assert focused["content"] == [paragraphs[200]]
    assert focused["scope"]["complete"] is False
    assert focused["revisionId"] == "r1"
    scenario.record["evidence"] = "same_fixture_raw_and_direct_heading_formatting_match"
    scenario.record["alternatives"] = {
        "toc_then_raw": {"command_indices": [0, 1], "task_commands": 2},
        "direct_heading": {"command_indices": [2], "task_commands": 1},
    }
    scenario.record["gap"] = (
        "Offline scripted comparison; no agent-driven latency measurement"
    )


def test_t11_direct_heading_selector_exposes_partial_scope(scenario):
    heading = paragraph("Departure checklist\n", style="HEADING_2")
    other = paragraph("Archive " + "x" * 6000 + "\n", heading["endIndex"])
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [heading, other]
    output = scenario.ok(
        "structure", tab="draft", heading="Departure checklist", json=True
    )
    result = json.loads(output)["document"]
    assert result["revisionId"] == "r1"
    assert result["scope"]["complete"] is False
    assert "Departure checklist" in output
    assert "Archive" not in output
    code, out, err = scenario.call("write", tab="draft", text="Unseen replacement\n")
    assert code != 0, out + err
    assert not scenario.batches


def test_t11_direct_table_selector_exposes_only_requested_table(scenario):
    make_table = existing_helper("test_native_targets", "table")
    first = make_table([["Port\n"], ["East\n"]], 2)
    between = paragraph("\n", first["endIndex"])
    second = make_table([["Load\n"], ["Seven\n"]], between["endIndex"])
    scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [
        paragraph("\n"),
        first,
        between,
        second,
        paragraph("\n", second["endIndex"]),
    ]
    output = scenario.ok("structure", tab="draft", table=2, json=True)
    result = json.loads(output)["document"]
    assert result["revisionId"] == "r1"
    assert result["scope"]["complete"] is False
    assert "Seven" in output and "East" not in output


def test_t11_heading_collision_across_tabs_requires_selection(scenario):
    for tab_id in ("draft", "other"):
        heading = paragraph("Departure checklist\n", style="HEADING_2")
        if tab_id == "draft":
            scenario.document["tabs"][0]["documentTab"]["body"]["content"] = [heading]
        else:
            scenario.document["tabs"].append(tab(tab_id, "Other", [heading]))
    code, output, error = scenario.call(
        "structure", heading="Departure checklist", json=True
    )
    assert code != 0
    assert "ambiguous" in (output + error).lower()
    assert "draft" in output + error and "other" in output + error
    assert not scenario.batches


def test_t12_explicit_force_after_partial_read_remains_revision_pinned(scenario):
    scenario.ok("cat", max_bytes=8, json=True)
    scenario.ok("write", text="Intentional replacement\n", force=True)
    assert scenario.batches[0]["writeControl"] == {"requiredRevisionId": "r1"}
    assert any(
        "Intentional replacement" in r.get("insertText", {}).get("text", "")
        for r in requests(scenario)
    )


def test_t12_image_filtered_read_does_not_authorize_rewrite(scenario):
    existing_image(scenario)
    result = json.loads(scenario.ok("cat", no_images=True, json=True))
    assert result["scope"]["complete"] is False
    code, output, error = scenario.call("write", text="Replacement\n")
    assert code != 0, output + error
    assert not scenario.batches


def test_t14_unrelated_rewrite_uses_fresh_snapshot_image_uri(scenario):
    existing_image(scenario)
    source = read(scenario)
    assert "gdoc-image:map" in source
    native = scenario.document["tabs"][0]["documentTab"]
    native["inlineObjects"]["map"]["inlineObjectProperties"]["embeddedObject"][
        "imageProperties"
    ]["contentUri"] = "https://example.invalid/refreshed-image.png"
    scenario.ok("write", tab="draft", text=source.replace("Before", "Revised"))
    image_requests = [
        r["insertInlineImage"] for r in requests(scenario) if "insertInlineImage" in r
    ]
    assert len(image_requests) == 1
    assert image_requests[0]["uri"] == "https://example.invalid/refreshed-image.png"
    assert image_requests[0]["location"] == {"index": 9, "tabId": "draft"}
    scenario.record["evidence"] = (
        "current_snapshot_reference_resolution_and_native_image_request"
    )
