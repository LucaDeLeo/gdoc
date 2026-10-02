"""R6-19: insert resolves old image references like write does.

After an acknowledged write re-creates an image, the agent's old reference
resolves to the copy for both write and insert, but only while the tab's
recorded coverage (complete or limited) is exactly the revision being
written. An insertion's own new copies are never recorded as aliases.
"""

from gdoc.state import load_state, record_content_read
from tests.acceptance.test_round3_workflows import _reply_with_image_ids
from tests.acceptance.test_workflows import existing_image, read, requests


def _recreate(scenario):
    """cat, then a changed write that Google answers by re-creating the image."""
    existing_image(scenario)
    source = read(scenario)
    _reply_with_image_ids(scenario)
    revised = source.replace("Before", "Revised")
    scenario.ok("write", tab="draft", text=revised)
    native = scenario.document["tabs"][0]["documentTab"]
    native["inlineObjects"] = {"copy1": native["inlineObjects"].pop("map")}
    for element in native["body"]["content"][1]["paragraph"]["elements"]:
        if "inlineObjectElement" in element:
            element["inlineObjectElement"]["inlineObjectId"] = "copy1"
    scenario.document["revisionId"] = "r2"
    scenario.service.documents.return_value.batchUpdate.reset_mock()
    return revised


def _images(scenario):
    return [r["insertInlineImage"] for r in requests(scenario)
            if "insertInlineImage" in r]


def test_insert_resolves_an_old_reference_after_a_write(scenario):
    _recreate(scenario)
    aliases = dict(load_state("synthetic").image_reference_ids)
    assert aliases == {"map": "copy1"}
    scenario.ok("insert", tab="draft", text="![Map](gdoc-image:map)")
    assert [image["uri"] for image in _images(scenario)] == [
        "https://example.invalid/temporary-map.png"]
    # The insertion added a copy; the original survives, so no new alias.
    assert load_state("synthetic").image_reference_ids == aliases


def test_insert_resolves_aliases_under_limited_coverage(scenario):
    _recreate(scenario)
    state = load_state("synthetic")
    record_content_read("synthetic", ["draft"], state.read_revision_ids["draft"],
                        limited_tab_ids=["draft"])
    scenario.ok("insert", tab="draft", text="![Map](gdoc-image:map)")
    assert len(_images(scenario)) == 1


def test_insert_does_not_resolve_aliases_after_a_foreign_change(scenario):
    _recreate(scenario)
    scenario.document["revisionId"] = "r3"  # someone else edited since
    code, output, error = scenario.call(
        "insert", tab="draft", text="![Map](gdoc-image:map)", force=True)
    assert code != 0 and "missing or ambiguous" in output + error
    assert _images(scenario) == []


def test_write_and_insert_agree_on_old_references(scenario):
    revised = _recreate(scenario)
    scenario.ok("write", tab="draft",
                text=revised.replace("Revised", "Again") + "\n![Map](gdoc-image:map)\n")
    written = _images(scenario)
    scenario.service.documents.return_value.batchUpdate.reset_mock()
    assert written and all(image["uri"].endswith("temporary-map.png")
                           for image in written)


def test_write_resolves_aliases_under_limited_coverage(scenario):
    revised = _recreate(scenario)
    state = load_state("synthetic")
    record_content_read("synthetic", ["draft"], state.read_revision_ids["draft"],
                        limited_tab_ids=["draft"])
    scenario.ok("write", tab="draft", allow_lossy=True,
                text=revised.replace("Revised", "Again") + "\n![Map](gdoc-image:map)\n")
    assert all(image["uri"].endswith("temporary-map.png")
               for image in _images(scenario)) and _images(scenario)
