"""Tests for `gdoc toc` — table of contents with deep links."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest  # noqa: F401

from gdoc.cli import cmd_toc
from gdoc.util import GdocError

DOC_ID = "abc123"
BASE_URL = f"https://docs.google.com/document/d/{DOC_ID}/edit"


def _make_args(**overrides):
    defaults = {
        "command": "toc",
        "doc": DOC_ID,
        "tab": None,
        "max_depth": 0,
        "no_links": False,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _headings(*specs):
    """Build heading dicts from (level, heading_id, text) tuples."""
    return [
        {"level": lvl, "heading_id": hid, "text": txt}
        for lvl, hid, txt in specs
    ]


class TestTocBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_terse_output(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = _headings(
            (1, "h.abc", "Introduction"),
            (2, "h.def", "Background"),
        )
        rc = cmd_toc(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert f"- [Introduction]({BASE_URL}#heading=h.abc)" in out
        assert f"  - [Background]({BASE_URL}#heading=h.def)" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_no_headings(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = []
        rc = cmd_toc(_make_args())
        assert rc == 0
        assert capsys.readouterr().out == ""

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_no_links_flag(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = _headings((1, "h.abc", "Title"))
        rc = cmd_toc(_make_args(no_links=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "- Title\n" in out
        assert "http" not in out


class TestTocMaxDepth:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_filters_deep_headings(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = _headings(
            (1, "h.1", "H1"),
            (2, "h.2", "H2"),
            (3, "h.3", "H3"),
        )
        rc = cmd_toc(_make_args(max_depth=2))
        assert rc == 0
        out = capsys.readouterr().out
        assert "H1" in out
        assert "H2" in out
        assert "H3" not in out


class TestTocOutputModes:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_json_output(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = _headings((1, "h.abc", "Title"))
        rc = cmd_toc(_make_args(json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert len(data["headings"]) == 1
        h = data["headings"][0]
        assert h["level"] == 1
        assert h["heading_id"] == "h.abc"
        assert h["text"] == "Title"
        assert h["link"] == f"{BASE_URL}#heading=h.abc"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_plain_output(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = _headings((2, "h.xyz", "Section"))
        rc = cmd_toc(_make_args(plain=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "2\th.xyz\tSection\t" in out
        assert f"{BASE_URL}#heading=h.xyz" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.get_docs_service")
    def test_verbose_shows_count(self, _svc, mock_headings, _pf, _update, capsys):
        mock_headings.return_value = _headings(
            (1, "h.1", "A"), (2, "h.2", "B"),
        )
        rc = cmd_toc(_make_args(verbose=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "(2 headings)" in out


class TestTocTab:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.resolve_tab")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_passes_body(
        self, _svc, mock_tabs, mock_resolve, mock_headings, _pf, _update, capsys,
    ):
        tab_body = {"content": []}
        mock_tabs.return_value = [{"id": "t1", "title": "Notes", "body": tab_body}]
        mock_resolve.return_value = {"id": "t1", "title": "Notes", "body": tab_body}
        mock_headings.return_value = _headings((1, "h.1", "Note Title"))
        rc = cmd_toc(_make_args(tab="Notes"))
        assert rc == 0
        mock_headings.assert_called_once_with(DOC_ID, body=tab_body)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.resolve_tab")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_appends_tab_id_to_links(
        self, _svc, mock_tabs, mock_resolve, mock_headings, _pf, _update, capsys,
    ):
        mock_tabs.return_value = [{"id": "t1", "title": "Notes", "body": {}}]
        mock_resolve.return_value = {"id": "t1", "title": "Notes", "body": {}}
        mock_headings.return_value = _headings((1, "h.abc", "Title"))
        rc = cmd_toc(_make_args(tab="Notes"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "&tab=t.t1" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings")
    @patch("gdoc.api.docs.resolve_tab")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_json_includes_tab_in_links(
        self, _svc, mock_tabs, mock_resolve, mock_headings, _pf, _update, capsys,
    ):
        mock_tabs.return_value = [{"id": "t1", "title": "Notes", "body": {}}]
        mock_resolve.return_value = {"id": "t1", "title": "Notes", "body": {}}
        mock_headings.return_value = _headings((1, "h.abc", "Title"))
        rc = cmd_toc(_make_args(tab="Notes", json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert "&tab=t.t1" in data["headings"][0]["link"]

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.resolve_tab")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_not_found(self, _svc, mock_tabs, mock_resolve, _pf, _update):
        mock_tabs.return_value = [{"id": "t1", "title": "Tab 1", "body": {}}]
        mock_resolve.side_effect = GdocError("tab not found: nope", exit_code=3)
        with pytest.raises(GdocError, match="tab not found"):
            cmd_toc(_make_args(tab="nope"))


class TestTocAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_document_headings", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_preflight_and_state(self, _svc, _headings, mock_pf, mock_update):
        from gdoc.notify import ChangeInfo

        change_info = ChangeInfo(current_version=5)
        mock_pf.return_value = change_info
        rc = cmd_toc(_make_args())
        assert rc == 0
        mock_pf.assert_called_once_with(DOC_ID, quiet=False)
        mock_update.assert_called_once_with(
            DOC_ID, change_info, command="toc", quiet=False,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_headings", return_value=[])
    @patch("gdoc.api.docs.get_docs_service")
    def test_quiet_skips_preflight(self, _svc, _headings, mock_pf, _update):
        cmd_toc(_make_args(quiet=True))
        mock_pf.assert_called_once_with(DOC_ID, quiet=True)


class TestGetDocumentHeadings:
    """Unit tests for the API-layer heading extraction."""

    def test_extracts_headings(self):
        from gdoc.api.docs import get_document_headings

        body = {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1", "headingId": "h.1"},
                "elements": [{"textRun": {"content": "Title\n"}}],
            }},
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": "Body text\n"}}],
            }},
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_2", "headingId": "h.2"},
                "elements": [{"textRun": {"content": "Sub\n"}}],
            }},
        ]}
        result = get_document_headings("doc1", body=body)
        assert len(result) == 2
        assert result[0] == {"level": 1, "heading_id": "h.1", "text": "Title"}
        assert result[1] == {"level": 2, "heading_id": "h.2", "text": "Sub"}

    def test_skips_headings_without_id(self):
        from gdoc.api.docs import get_document_headings

        body = {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
                "elements": [{"textRun": {"content": "No ID\n"}}],
            }},
        ]}
        result = get_document_headings("doc1", body=body)
        assert result == []

    def test_skips_empty_heading_text(self):
        from gdoc.api.docs import get_document_headings

        body = {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1", "headingId": "h.1"},
                "elements": [{"textRun": {"content": "\n"}}],
            }},
        ]}
        result = get_document_headings("doc1", body=body)
        assert result == []

    def test_concatenates_multi_run_text(self):
        from gdoc.api.docs import get_document_headings

        body = {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1", "headingId": "h.1"},
                "elements": [
                    {"textRun": {"content": "Part "}},
                    {"textRun": {"content": "One\n"}},
                ],
            }},
        ]}
        result = get_document_headings("doc1", body=body)
        assert result[0]["text"] == "Part One"

    def test_empty_body(self):
        from gdoc.api.docs import get_document_headings

        result = get_document_headings("doc1", body={"content": []})
        assert result == []
