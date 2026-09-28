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
