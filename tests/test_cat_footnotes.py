"""`cat --footnotes`: native footnotes as [^n] references and definitions."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.api.docs import flatten_tabs, get_tab_text
from gdoc.cli import cmd_cat
from gdoc.util import GdocError


@pytest.fixture(autouse=True)
def _doc_mime(doc_mime):
    """Keep spreadsheet detection on the Docs path for this module."""


def _args(**overrides):
    defaults = {
        "command": "cat", "doc": "abc123", "plain": False, "comments": False,
        "all": False, "tab": None, "all_tabs": False, "no_images": False,
        "json": False, "verbose": False, "quiet": True, "footnotes": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _run(text, **style):
    return {"textRun": {"content": text, "textStyle": style}}


def _ref(footnote_id, number):
    return {"footnoteReference": {"footnoteId": footnote_id,
                                  "footnoteNumber": number, "textStyle": {}}}


def _para(*elements):
    return {"paragraph": {"elements": list(elements)}}


def _footnote(*paragraphs):
    # Docs starts each footnote with a space after its number.
    return {"content": [_para(_run(text + "\n")) if not isinstance(text, list)
                        else _para(*text) for text in paragraphs]}


def _first_tab():
    """Footnotes after bold text, in a table cell and in a list item."""
    return {
        "tabProperties": {"tabId": "t.0", "title": "Tab 1", "index": 0},
        "documentTab": {
            "body": {"content": [
                _para(_run("A "), _run("bold", bold=True), _ref("fa", "1"),
                      _run(" claim.\n")),
                {"table": {"rows": 1, "columns": 2, "tableRows": [
                    {"tableCells": [
                        {"content": [_para(_run("1\n"))]},
                        {"content": [_para(_run("2"), _ref("fb", "2"),
                                           _run("\n"))]},
                    ]},
                ]}},
                {"paragraph": {
                    "elements": [_run("item"), _ref("fc", "3"), _run("\n")],
                    "bullet": {"listId": "l1"},
                }},
            ]},
            "lists": {"l1": {"listProperties": {"nestingLevels": [
                {"glyphSymbol": "●"}]}}},
            "footnotes": {
                "fa": _footnote([
                    _run(" See the "),
                    _run("source", link={"url": "https://example.org/a"}),
                    _run(".\n"),
                ]),
                "fb": _footnote(" A cell note."),
                "fc": _footnote(" A list note."),
            },
        },
    }


def _second_tab():
    """Two footnotes, one with two paragraphs; Docs numbers them from 1."""
    return {
        "tabProperties": {"tabId": "t.two", "title": "Second", "index": 1},
        "documentTab": {
            "body": {"content": [
                _para(_run("Second tab"), _ref("fd", "1"), _run(".\n")),
                _para(_run("More"), _ref("fe", "2"), _run("\n")),
            ]},
            "footnotes": {
                "fd": _footnote(" First paragraph.", "Second paragraph."),
                "fe": _footnote(" Short."),
            },
        },
    }


def _document():
    return {"revisionId": "r1", "tabs": [_first_tab(), _second_tab()]}


FIRST = (
    "A **bold**[^1] claim.\n"
    "| 1 | 2[^2] |\n"
    "| --- | --- |\n"
    "- item[^3]\n"
    "\n"
    "[^1]: See the [source](https://example.org/a).\n"
    "\n"
    "[^2]: A cell note.\n"
    "\n"
    "[^3]: A list note.\n"
)


def _second(first, second):
    return (
        f"Second tab[^{first}].\n"
        f"More[^{second}]\n"
        "\n"
        f"[^{first}]: First paragraph.\n"
        "\n"
        "    Second paragraph.\n"
        "\n"
        f"[^{second}]: Short.\n"
    )


class TestRenderer:
    def test_references_and_definitions(self):
        tab = flatten_tabs([_first_tab()])[0]
        assert get_tab_text(tab, markdown=True, footnotes=[]) == FIRST

    def test_default_read_is_unchanged(self):
        """Without footnotes, references are left out as before."""
        tab = flatten_tabs([_first_tab()])[0]
        text = get_tab_text(tab, markdown=True)
        assert "[^" not in text
        assert text == "A **bold** claim.\n| 1 | 2 |\n| --- | --- |\n- item\n"

    def test_numbering_continues_in_a_shared_list(self):
        first, second = flatten_tabs([_first_tab(), _second_tab()])
        numbered = []
        get_tab_text(first, markdown=True, footnotes=numbered)
        assert get_tab_text(second, markdown=True, footnotes=numbered) \
            == _second(4, 5)
        assert numbered == ["fa", "fb", "fc", "fd", "fe"]

    def test_plain_text_ignores_footnotes(self):
        tab = flatten_tabs([_first_tab()])[0]
        assert get_tab_text(tab, markdown=False, footnotes=[]) \
            == get_tab_text(tab, markdown=False)

    def test_tab_without_footnotes(self):
        tab = flatten_tabs([{
            "tabProperties": {"tabId": "t.0", "title": "Tab 1"},
            "documentTab": {"body": {"content": [_para(_run("Plain.\n"))]}},
        }])[0]
        assert get_tab_text(tab, markdown=True, footnotes=[]) == "Plain.\n"


@patch("gdoc.cli._record_read")
@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.docs.get_document_with_tabs", return_value=_document())
class TestCatFootnotes:
    def test_tab_numbers_from_one(self, _get, _pf, _update, record, capsys):
        assert cmd_cat(_args(tab="Second")) == 0
        assert capsys.readouterr().out == _second(1, 2)
        record.assert_not_called()

    def test_default_reads_first_tab(self, _get, _pf, _update, record, capsys):
        assert cmd_cat(_args()) == 0
        assert capsys.readouterr().out == FIRST

    def test_all_tabs_number_continuously(self, _get, _pf, _update, record,
                                          capsys):
        assert cmd_cat(_args(all_tabs=True)) == 0
        assert capsys.readouterr().out == (
            "=== Tab: Tab 1 ===\n" + FIRST
            + "=== Tab: Second ===\n" + _second(4, 5)
        )
        record.assert_not_called()

    def test_json_scope(self, _get, _pf, _update, record, capsys):
        assert cmd_cat(_args(tab="Second", json=True)) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["content"] == _second(1, 2)
        assert data["tab"] == "Second"
        # Footnotes are shown, so not omitted; still not a write baseline.
        assert "omitted" not in data["scope"]
        assert data["scope"]["complete"] is False

    def test_without_flag_footnotes_are_omitted(self, _get, _pf, _update,
                                                record, capsys):
        assert cmd_cat(_args(tab="Second", footnotes=False, json=True)) == 0
        data = json.loads(capsys.readouterr().out)
        assert "[^" not in data["content"]
        assert data["scope"]["omitted"] == ["footnotes"]


@pytest.mark.parametrize("flags, clash", [
    ({"comments": True}, "--comments"),
    ({"plain": True}, "--plain"),
    ({"revision": "head"}, "--revision"),
])
def test_conflicting_flags(flags, clash):
    with pytest.raises(GdocError, match=f"--footnotes and {clash}") as error:
        cmd_cat(_args(**flags))
    assert error.value.exit_code == 3


@patch("gdoc.notify.pre_flight", return_value=None)
def test_spreadsheet_refused(_pf, monkeypatch):
    monkeypatch.setattr("gdoc.cli._file_mime", lambda doc_id, info:
                        "application/vnd.google-apps.spreadsheet")
    with pytest.raises(GdocError, match="only supported for documents"):
        cmd_cat(_args())


def test_flag_parses_and_reaches_mcp():
    from gdoc import mcp
    from gdoc.cli import build_parser

    args = build_parser().parse_args(["cat", "DOC", "--tab", "X", "--footnotes"])
    assert args.footnotes is True
    schema = mcp.build_tools()["gdoc_cat"]["inputSchema"]["properties"]
    assert schema["footnotes"]["type"] == "boolean"
