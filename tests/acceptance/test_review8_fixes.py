"""Round-8 review fixes, checked on the offline native model through CLI/MCP."""

import random
from unittest import mock

import pytest

from gdoc.api import comment_transport
from gdoc.api.docs import get_tab_text, insert_markdown_into_tab
from gdoc.mdparse import parse_markdown
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, NativeService, styles

EMPHASIS = ("bold", "italic", "strikethrough", "link")


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _styled_doc(paragraphs):
    """A NativeDoc whose paragraphs are lists of ``(text, textStyle)`` runs."""
    doc = NativeDoc(*[("p", "".join(text for text, _ in runs)) for runs in paragraphs])
    for (start, mark), runs in zip(doc.paragraphs(), paragraphs):
        index = start
        for text, style in runs:
            for _ in text:
                doc.units[index].ts = dict(style)
                index += 1
        doc.units[mark].ts = dict(runs[-1][1])
    return doc


def _visible_styles(doc, start, mark):
    """Per-character emphasis and link of one native paragraph's visible text."""
    return [(u.ch, {k: u.ts[k] for k in EMPHASIS if u.ts.get(k)}
             if u.ch.strip() else {})
            for u in doc.units[start:mark]]


def _parsed_styles(parsed):
    chars = [{} for _ in parsed.plain_text]
    for span in parsed.styles:
        if span.type == "text_style":
            for offset in range(span.start, span.end):
                chars[offset].update({k: v for k, v in span.style.items()
                                      if k in EMPHASIS and v})
    return [(ch, style if ch.strip() else {})
            for ch, style in zip(parsed.plain_text, chars)]


def _write(service, markdown):
    with mock.patch("gdoc.api.docs.get_docs_service", return_value=service), \
            mock.patch.object(comment_transport, "execute_mutation_request",
                              lambda request, **_: request.execute()):
        insert_markdown_into_tab("synthetic", "Main", markdown, replace=True)


WORDS = ("Owner", "Ana", "ship", "today", "x", "status", "draft", "Q3")
FONTS = ("Arial", "Georgia", "Times New Roman", "Roboto")


def _random_run_style(rng, font):
    style = {"weightedFontFamily": {"fontFamily": font, "weight": 400}}
    for key in ("bold", "italic", "strikethrough"):
        if rng.random() < 0.3:
            style[key] = True
    if rng.random() < 0.15:
        style["link"] = {"url": rng.choice(("https://e.org/a", "https://e.org/b"))}
    return style


def _random_paragraph(rng, font):
    runs = []
    for _ in range(rng.randint(1, 6)):
        text = rng.choice(WORDS) if rng.random() < 0.6 else rng.choice((" ", "  "))
        runs.append((text, _random_run_style(rng, font)))
    if not "".join(text for text, _ in runs).strip():
        runs.append(("word", _random_run_style(rng, font)))
    return runs


def test_whitespace_runs_with_a_font_export_and_rewrite_unchanged():
    """R8-01: a non-monospace font never gives a whitespace run emphasis
    markers or a second copy, on read or on an unrelated changed rewrite."""
    rng = random.Random(8001)
    for _ in range(300):
        font = rng.choice(FONTS)
        paragraphs = [_random_paragraph(rng, font) for _ in range(rng.randint(1, 3))]
        paragraphs.append([("Status draft", {"weightedFontFamily": {"fontFamily": font}})])
        doc = _styled_doc(paragraphs)
        service = NativeService(doc)
        before = [_visible_styles(doc, start, mark) for start, mark in doc.paragraphs()]
        markdown = get_tab_text(service.snapshot()["tabs"][0]["documentTab"],
                                markdown=True)

        parsed = parse_markdown(markdown)
        expected = [pair for paragraph in before for pair in paragraph + [("\n", {})]]
        assert _parsed_styles(parsed) == expected, markdown

        _write(service, markdown.replace("Status draft", "Status final"))
        assert service.batches
        after = [_visible_styles(doc, start, mark) for start, mark in doc.paragraphs()]
        assert after[:-1] == before[:-1], markdown
        assert "".join(ch for ch, _ in after[-1]) == "Status final"


def test_whitespace_run_with_a_font_reads_as_plain_space(route):
    """R8-01 through the commands: `**Owner** *Ana*` with Arial on every run."""
    arial = {"weightedFontFamily": {"fontFamily": "Arial", "weight": 400}}
    route.load(_styled_doc([
        [("Owner", {**arial, "bold": True}), (" ", arial),
         ("Ana", {**arial, "italic": True})],
        [("bold", {**arial, "bold": True}), (" ", {**arial, "italic": True}),
         ("italic", {**arial, "italic": True})],
        [("Status draft", arial)],
    ]))
    read = route.ok("cat")
    assert "**Owner** *Ana*\n" in read
    assert "**bold** *italic*\n" in read
    code, output, error = route.call("write", text=read.replace("draft", "final"))
    assert code == 0, output + error
    assert [t for t, *_ in styles(route.service.doc)] == [
        "Owner Ana", "bold italic", "Status final"]


def test_monospace_whitespace_run_stays_inline_code():
    doc = _styled_doc([[("a", {}), ("  ", {"weightedFontFamily": {
        "fontFamily": "Courier New"}}), ("b", {})]])
    markdown = get_tab_text(NativeService(doc).snapshot()["tabs"][0]["documentTab"],
                            markdown=True)
    assert markdown == "a`  `b\n"


def _pull(route, path, *extra):
    import contextlib
    import io

    from gdoc import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.run_argv(["pull", "synthetic", str(path), *extra],
                            check_updates=False)
    assert code == 0, err.getvalue()
    return path.read_text()


def _collaborator_edit(route):
    route.service.doc = NativeDoc(("p", "Alpha."), ("p", "Beta."),
                                  ("p", "COLLABORATOR LINE."))
    route.service.revision += 1


def _texts(route):
    return [t for t, *_ in styles(route.service.doc)]


def test_write_refuses_a_stale_pulled_file_after_a_fresh_read(route, tmp_path):
    """R8-06: a newer read cannot authorize an older pulled file."""
    route.load(NativeDoc(("p", "Alpha."), ("p", "Beta.")))
    pulled = _pull(route, tmp_path / "draft.md")
    edited = pulled.replace("Alpha.", "Alpha edited locally.")
    _collaborator_edit(route)
    assert "COLLABORATOR" in route.ok("cat")
    code, output, error = route.call("write", text=edited)
    assert code == (3 if route.interface == "cli" else 1), output + error
    assert "gdoc-revision is stale" in output + error
    assert "COLLABORATOR LINE." in _texts(route)

    route.ok("write", text=edited, force=True)
    assert _texts(route) == ["Alpha edited locally.", "Beta."]


def test_write_accepts_a_current_pulled_file(route, tmp_path):
    route.load(NativeDoc(("p", "Alpha."), ("p", "Beta.")))
    pulled = _pull(route, tmp_path / "draft.md")
    route.ok("write", text=pulled.replace("Alpha.", "Alpha edited."))
    assert _texts(route) == ["Alpha edited.", "Beta."]


def test_write_refuses_a_past_revision_file_without_force(route, tmp_path):
    route.load(NativeDoc(("p", "Alpha.")))
    route.ok("cat")
    old = "---\nsource: synthetic\nrevision: r0\ntitle: Synthetic\n---\nOld.\n"
    code, output, error = route.call("write", text=old)
    assert code != 0 and "past revision r0" in output + error
    assert _texts(route) == ["Alpha."]
    route.ok("write", text=old, force=True)
    assert _texts(route) == ["Old."]


ZERO_BORDER = {"color": {}, "width": {"unit": "PT"}, "padding": {"unit": "PT"},
               "dashStyle": "SOLID"}


def test_zero_width_bottom_border_is_not_a_rule(route):
    """R8-07: only a visible bottom border on an empty paragraph reads as `---`."""
    doc = route.load(NativeDoc(("p", "Intro"), ("p", ""), ("p", "Next")))
    for _, mark in doc.paragraphs():
        doc.units[mark].ps["borderBottom"] = dict(ZERO_BORDER)
    read = route.ok("cat")
    assert "---" not in read and "Intro\n\nNext" in read
    route.ok("write", text=read.replace("Next", "Later"))
    assert _texts(route) == ["Intro", "", "Later"]
    assert not any(
        (doc.units[mark].ps.get("borderBottom") or {}).get("width", {}).get(
            "magnitude") for _, mark in doc.paragraphs())


def test_in_sync_push_of_an_image_tab_file_records_no_baseline(route, tmp_path):
    """R8-14: without a tab fingerprint (a tab with images), an in-sync file
    at the same revision is no read, so an edited copy still needs one."""
    import shutil

    from gdoc import state
    doc = NativeDoc(("p", "Alpha."), ("p", "Beta."))
    doc.apply({"insertInlineImage": {"location": {"index": 1},
                                     "uri": "https://x/i.png"}})
    route.load(doc)
    pulled = _pull(route, tmp_path / "draft.md")
    shutil.rmtree(state.STATE_DIR)  # another machine: no local state
    route.ok("write", text=pulled)  # in sync
    code, output, error = route.call(
        "write", text=pulled.replace("Beta.", "Beta edited."))
    assert code != 0, output + error
    assert "Beta." in _texts(route)[-1]
    route.ok("cat")
    route.ok("write", text=pulled.replace("Beta.", "Beta edited."))
    assert _texts(route)[-1] == "Beta edited."


def test_plain_in_sync_result_is_tsv(monkeypatch, tmp_path):
    """R8-16: `--plain` (CLI only) reports an in-sync write as TSV."""
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    output = route.ok("write", text="Alpha.\n", plain=True)
    assert output == "id\tsynthetic\ntab_id\tt.0\nstatus\tin_sync\n"


def _cli(argv):
    import contextlib
    import io

    from gdoc import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.run_argv(argv, check_updates=False)
    return code, out.getvalue(), err.getvalue()


def test_diff_ignores_tab_without_gdoc_provenance(monkeypatch, tmp_path):
    """R8-15: `diff DOC FILE` (CLI only; MCP has no local files) ignores
    another tool's `tab` field, as `write` does."""
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    path = tmp_path / "notes.md"
    path.write_text("---\ntitle: Notes\ntab: Overview\n---\nAlpha.\n")
    code, output, error = _cli(["diff", "synthetic", str(path)])
    assert code == 0, error
    path.write_text("---\ngdoc: synthetic\ntab: Overview\n---\nAlpha.\n")
    code, output, error = _cli(["diff", "synthetic", str(path)])
    assert code == 3 and "tab not found: Overview" in error


@pytest.mark.parametrize("command", ["write", "insert", "diff", "push"])
def test_non_utf8_input_file_is_a_usage_error(monkeypatch, tmp_path, command):
    """R8-17: undecodable input exits 3 before any mutation (CLI only)."""
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe bad")
    argv = ([command, str(path)] if command == "push"
            else [command, "synthetic", str(path)])
    if command == "insert":
        argv += ["--tab", "Main"]
    code, output, error = _cli(argv)
    assert code == 3 and "cannot read file" in error
    assert not route.service.batches


def test_written_rule_still_reads_as_rule(route):
    route.load(NativeDoc(("p", "seed")))
    route.ok("cat")
    route.ok("write", text="Intro\n\n---\n\nNext\n")
    assert "Intro\n\n---\n\nNext\n" in route.ok("cat")
