"""R6-1/R6-7: file provenance covers the selected tab's native content.

A pulled or pushed file stays pushable after edits to other tabs, but any
native change to its own tab, including styles and pending suggestions that
Markdown does not show, makes it stale. Matching Markdown never blesses an
unseen revision. These run through the real CLI and sync hook against the
native model.
"""

import contextlib
import io
import json

import pytest

from gdoc import cli
from gdoc.frontmatter import parse_frontmatter
from gdoc.util import GdocError
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc

RED = {"foregroundColor": {"color": {"rgbColor": {"red": 1}}}}


def sibling(text="Reference\n"):
    return {"tabProperties": {"tabId": "t.1", "title": "Notes", "index": 1},
            "documentTab": {"body": {"content": [
                {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
                {"startIndex": 1, "endIndex": 1 + len(text), "paragraph": {
                    "elements": [{"startIndex": 1, "endIndex": 1 + len(text),
                                  "textRun": {"content": text}}]}}]}}}


@pytest.fixture
def env(request, monkeypatch, tmp_path):
    route = NativeRoute("cli", monkeypatch, tmp_path)
    doc = route.load(NativeDoc(("p", "Alpha beta."), ("p", "Second.")))
    route.service.extra_tabs = [sibling()]
    route.doc, route.file = doc, tmp_path / "doc.md"
    return route


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.run_argv(list(argv), check_updates=False)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue() + err.getvalue()


def push(env, *flags):
    return run("push", str(env.file), *flags)


def sync_hook(env):
    stdin = json.dumps({"hook_event_name": "PostToolUse",
                        "tool_input": {"file_path": str(env.file)}})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.stdin", io.StringIO(stdin))
        assert cli.cmd_sync_hook(None) == 0
    return err.getvalue()


def pull(env):
    code, output = run("pull", "synthetic", str(env.file))
    assert code == 0, output


def edit_file(env, old, new):
    text = env.file.read_text()
    assert old in text
    env.file.write_text(text.replace(old, new, 1))


def colour_beta(env):
    env.service.doc.apply({"updateTextStyle": {
        "range": {"startIndex": 7, "endIndex": 11}, "textStyle": RED,
        "fields": "foregroundColor"}})
    env.service.revision += 1


def suggest_gamma(env):
    """A collaborator suggests inserting ' gamma' after 'beta'."""
    env.service.doc.apply({"insertText": {"location": {"index": 11},
                                          "text": " gamma"}})
    env.service.revision += 1
    snapshot = env.service.snapshot

    def with_suggestion():
        value = snapshot()
        for element in value["tabs"][0]["documentTab"]["body"]["content"]:
            for run in element.get("paragraph", {}).get("elements", []):
                text = run.get("textRun", {}).get("content", "")
                if "gamma" in text:
                    start = run["startIndex"]
                    before, rest = text.split(" gamma", 1)
                    run["textRun"]["content"] = before
                    run["endIndex"] = start + len(before)
                    extra = [{"startIndex": start + len(before),
                              "endIndex": start + len(before) + 6,
                              "textRun": {"content": " gamma",
                                          "suggestedInsertionIds": ["s1"]}},
                             {"startIndex": start + len(before) + 6,
                              "endIndex": start + len(text),
                              "textRun": {"content": rest}}]
                    elements = element["paragraph"]["elements"]
                    at = elements.index(run) + 1
                    elements[at:at] = extra
                    return value
        return value

    env.service.snapshot = with_suggestion


def edit_sibling(env, text="Reference changed\n"):
    env.service.extra_tabs = [sibling(text)]
    env.service.revision += 1


def coloured(env):
    return "".join(u.ch for u in env.doc.units if u.ts.get("foregroundColor"))


def body(env):
    return "".join(u.ch for u in env.doc.units if u.kind == "text")


@pytest.mark.parametrize("flags", [(), ("--allow-lossy",)])
def test_colour_on_the_same_tab_makes_the_file_stale(env, flags):
    pull(env)
    colour_beta(env)
    edit_file(env, "Second.", "Second edited.")
    before = len(env.service.batches)
    code, output = push(env, *flags)
    assert code == 3 and "stale" in output
    assert len(env.service.batches) == before
    assert coloured(env) == "beta"


def test_pending_suggestion_on_the_same_tab_makes_the_file_stale(env):
    pull(env)
    suggest_gamma(env)
    edit_file(env, "Second.", "Second edited.")
    before = len(env.service.batches)
    code, output = push(env, "--allow-lossy")
    assert code == 3 and "stale" in output
    assert len(env.service.batches) == before
    assert "gamma" in body(env)


def test_stale_file_is_refused_even_when_global_state_is_current(env):
    pull(env)
    colour_beta(env)
    env.ok("cat")  # a fresh read elsewhere does not update this file
    edit_file(env, "Second.", "Second edited.")
    code, output = push(env)
    assert code == 3 and "stale" in output
    assert coloured(env) == "beta"


def test_sibling_edits_leave_the_file_pushable(env):
    pull(env)
    edit_sibling(env)
    edit_file(env, "Second.", "Second edited.")
    before = len(env.service.batches)
    code, output = push(env)
    assert code == 0, output
    assert len(env.service.batches) > before
    assert env.ok("cat") == "Alpha beta.\nSecond edited.\n"
    assert env.service.extra_tabs == [sibling("Reference changed\n")]


@pytest.mark.parametrize("spelling,canonical", [
    ("Second _em_ here.", "Second *em* here."),
    ("* item one\n* item two", "- item one\n- item two"),
])
def test_successive_pushes_with_noncanonical_input_survive_sibling_edits(
    env, spelling, canonical,
):
    pull(env)
    edit_file(env, "Second.", spelling)
    assert push(env)[0] == 0
    metadata, _ = parse_frontmatter(env.file.read_text())
    assert metadata["gdoc-tab-sha256"]
    assert env.ok("cat") == f"Alpha beta.\n{canonical}\n"
    # Own acknowledged successive push, without rereading.
    edit_file(env, "Alpha", "Alpha2")
    batches = len(env.service.batches)
    assert push(env)[0] == 0
    assert len(env.service.batches) > batches
    # A collaborator edits another tab; the file is still current.
    edit_sibling(env)
    edit_file(env, "Alpha2", "Alpha3")
    batches = len(env.service.batches)
    code, output = push(env)
    assert code == 0, output
    assert len(env.service.batches) > batches
    assert env.ok("cat") == f"Alpha3 beta.\n{canonical}\n"
    # A same-tab colour after the push still makes the file stale.
    colour_beta_at = body(env).index("beta") + 1  # after the section break
    env.service.doc.apply({"updateTextStyle": {
        "range": {"startIndex": colour_beta_at, "endIndex": colour_beta_at + 4},
        "textStyle": RED, "fields": "foregroundColor"}})
    env.service.revision += 1
    edit_file(env, "Alpha3", "Alpha4")
    assert push(env)[0] == 3
    assert coloured(env) == "beta"


def test_sync_hook_follows_the_same_file_provenance(env):
    pull(env)
    colour_beta(env)
    edit_file(env, "Second.", "Second edited.")
    message = sync_hook(env)
    assert "SYNC: skipped" in message and "stale" in message
    assert coloured(env) == "beta" and "Second edited" not in body(env)

    pull(env)
    edit_sibling(env)
    edit_file(env, "Second.", "Second edited.")
    # The colour was in the pulled baseline, so the whole-tab rewrite may reset
    # it (with a warning); only unseen changes are protected.
    assert "SYNC: pushed" in sync_hook(env)
    assert "Second edited" in body(env)


def test_matching_markdown_after_a_same_tab_change_is_not_a_read(env):
    """write: equal Markdown reports in sync but cannot bless the new revision."""
    env.ok("cat")
    colour_beta(env)
    assert "already in sync" in env.ok("write", text="Alpha beta.\nSecond.\n")
    code, out, err = env.call("write", text="Alpha beta.\nSecond edited.\n")
    assert code == 3 and "changed since last read" in err
    assert coloured(env) == "beta"


def test_file_matching_markdown_after_a_same_tab_change_stays_stale(env):
    pull(env)
    original = env.file.read_text()
    colour_beta(env)
    code, output = push(env)
    assert code == 0 and "already in sync" in output
    assert env.file.read_text() == original
    edit_file(env, "Second.", "Second edited.")
    assert push(env)[0] == 3
    assert coloured(env) == "beta"


def test_tab_with_images_has_no_provable_fingerprint(env):
    """contentUri changes per read, and replaced bytes can keep ID and size."""
    from gdoc.api.docs import flatten_tabs, native_tab_fingerprint

    tab = flatten_tabs(env.service.snapshot()["tabs"])[0]
    assert native_tab_fingerprint(tab)
    tab["inlineObjects"] = {"i": {"inlineObjectProperties": {"embeddedObject": {
        "imageProperties": {"contentUri": "https://lh.example/one"}}}}}
    assert native_tab_fingerprint(tab) == ""


def test_image_tab_file_needs_a_fresh_pull_after_any_edit(env):
    env.service.doc.apply({"insertInlineImage": {
        "location": {"index": 1}, "uri": "https://example.org/i.png"}})
    env.service.revision += 1
    pull(env)
    assert parse_frontmatter(env.file.read_text())[0]["gdoc-tab-sha256"] == ""
    edit_sibling(env)
    edit_file(env, "Second.", "Second edited.")
    before = len(env.service.batches)
    code, output = push(env)
    assert code == 3 and "stale" in output
    assert len(env.service.batches) == before


@pytest.mark.parametrize("key,value", [
    ("namedStyles", {"styles": [{"namedStyleType": "NORMAL_TEXT",
                                 "textStyle": {"fontSize": {"magnitude": 20}}}]}),
    ("documentStyle", {"background": {"color": {}}}),
    ("footnotes", {"f": {"content": []}}),
    ("suggestedDocumentStyleChanges", {"s9": {"documentStyle": {}}}),
    ("suggestedNamedStylesChanges", {"s9": {"namedStyles": {}}}),
])
def test_tab_dependencies_outside_the_body_make_the_file_stale(env, key, value):
    pull(env)
    snapshot = env.service.snapshot

    def changed():
        result = snapshot()
        result["tabs"][0]["documentTab"][key] = value
        return result

    env.service.snapshot = changed
    env.service.revision += 1
    edit_file(env, "Second.", "Second edited.")
    code, output = push(env)
    assert code == 3 and "stale" in output


@pytest.mark.parametrize("failure", [
    OSError("reset"), TimeoutError("slow"), GdocError("unreadable")])
def test_failed_post_write_read_keeps_the_saved_outcome(env, monkeypatch, failure):
    from gdoc.api import docs

    pull(env)
    edit_file(env, "Second.", "Second edited.")
    real = docs.get_document_with_tabs
    calls = []

    def flaky(doc_id):
        calls.append(doc_id)
        if len(calls) > 1:
            raise failure
        return real(doc_id)

    monkeypatch.setattr(docs, "get_document_with_tabs", flaky)
    code, output = push(env)
    assert code == 0 and "OK pushed" in output and "fingerprinted" in output
    metadata, _ = parse_frontmatter(env.file.read_text())
    assert metadata["gdoc-revision"] == f"r{env.service.revision}"
    assert metadata["gdoc-tab-sha256"] == ""
    monkeypatch.setattr(docs, "get_document_with_tabs", real)
    # Without a fingerprint, a sibling edit makes the file stale...
    edit_sibling(env)
    edit_file(env, "Alpha", "Alpha2")
    assert push(env)[0] == 3
    # ...while a fresh pull restores the provenance.
    pull(env)
    edit_file(env, "Alpha", "Alpha2")
    assert push(env)[0] == 0


def test_post_write_read_after_a_race_carries_no_fingerprint(env, monkeypatch):
    from gdoc.api import docs

    pull(env)
    edit_file(env, "Second.", "Second edited.")
    real = docs.get_document_with_tabs
    calls = []

    def racing(doc_id):
        calls.append(doc_id)
        if len(calls) > 1:  # a collaborator colours the tab before our read
            colour_beta(env)
        return real(doc_id)

    monkeypatch.setattr(docs, "get_document_with_tabs", racing)
    assert push(env)[0] == 0
    monkeypatch.setattr(docs, "get_document_with_tabs", real)
    metadata, _ = parse_frontmatter(env.file.read_text())
    # The acknowledged revision, never the newer sampled one.
    assert metadata["gdoc-revision"] == f"r{env.service.revision - 1}"
    assert metadata["gdoc-tab-sha256"] == ""
    edit_file(env, "Alpha", "Alpha2")
    code, output = push(env)
    assert code == 3 and "stale" in output
    assert coloured(env) == "beta"


def test_file_at_the_current_revision_is_pushable_without_local_state(env):
    """R7-11: the file's own provenance covers its tab without local state."""
    import shutil

    from gdoc import state

    pull(env)
    shutil.rmtree(state.STATE_DIR, ignore_errors=True)
    edit_file(env, "Second.", "Second, edited.")
    batches = len(env.service.batches)
    code, output = push(env)
    assert code == 0, output
    assert len(env.service.batches) > batches
    assert "Second, edited." in body(env)


def test_same_revision_file_without_fingerprint_still_needs_a_read(env):
    """R7-11 boundary: a revision string alone does not authorize a push."""
    import shutil

    from gdoc import state
    from gdoc.frontmatter import update_frontmatter_value

    pull(env)
    shutil.rmtree(state.STATE_DIR, ignore_errors=True)
    env.file.write_text(update_frontmatter_value(
        env.file.read_text(), "gdoc-tab-sha256", ""))
    edit_file(env, "Second.", "Second, edited.")
    code, output = push(env)
    assert code == 3 and "complete read baseline" in output
    assert env.service.batches == []
