"""R7-14, R7-15, R7-16: reference definitions and list depth, through CLI/MCP.

Checked on the native paragraphs and links the write produces.
"""

import pytest

from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _write(route, text):
    route.load(NativeDoc(("p", "seed")))
    route.ok("cat")
    code, output, error = route.call("write", text=text)
    assert code == 0, output + error
    return output + error


def _links(doc):
    return {u.ts["link"]["url"] for u in doc.units if u.ts.get("link")}


@pytest.mark.parametrize("definition", [
    "[r]: <https://example.org/a b>",
    "[r]: <https://example.org/a b> \"title\"",
])
def test_reference_definition_with_bracketed_spaces(route, definition):
    """R7-14: as inline links accept `<a b>`, so do definitions."""
    _write(route, f"[guide][r]\n\n{definition}\n")
    assert _links(route.service.doc) == {"https://example.org/a b"}
    assert [t for t, *_ in styles(route.service.doc)] == ["guide"]


@pytest.mark.parametrize("blank_lines,blanks", [(1, 0), (2, 1), (3, 2)])
def test_blank_paragraphs_before_trailing_definitions(route, blank_lines, blanks):
    """R7-15: one blank line separates definitions; more are paragraphs."""
    _write(route, "See [x][r].\n" + "\n" * blank_lines + "[r]: https://example.org/\n")
    assert [t for t, *_ in styles(route.service.doc)] == ["See x."] + [""] * blanks


def test_lists_deeper_than_nine_levels_warn(route):
    """R7-16: deeper items are written at the ninth level, with a warning."""
    text = "".join("  " * level + f"- l{level}\n" for level in range(11))
    notes = _write(route, text)
    assert "at most nine nesting levels" in notes
    assert "'l9'" in notes and "'l10'" in notes and "'l8'" not in notes
    nests = [bullet[1] for _, _, bullet in styles(route.service.doc)]
    assert nests == list(range(9)) + [8, 8]


def test_nine_levels_do_not_warn(route):
    text = "".join("  " * level + f"1. l{level}\n" for level in range(9))
    assert "nesting levels" not in _write(route, text)


def test_no_images_read_keeps_code_and_blank_paragraphs(route):
    """R7-20: --no-images drops native images, not text shaped like one."""
    text = ("![](https://example.org/i.png)\n```\n![a](b)\n```\n"
            "use `![c](d)` here\n\n\nend\n")
    _write(route, text)
    full = route.ok("cat")
    assert "gdoc-image:" in full
    stripped = route.ok("cat", no_images=True)
    assert "gdoc-image:" not in stripped
    assert "```\n![a](b)\n```\n" in stripped
    assert "use `![c](d)` here\n\n\nend\n" in stripped
