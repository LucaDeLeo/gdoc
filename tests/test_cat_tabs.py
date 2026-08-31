"""Tests for --tab and --all-tabs flags on `gdoc cat`."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_cat
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError


@pytest.fixture(autouse=True)
def _doc_mime(doc_mime):
    """Keep spreadsheet detection on the Docs path for this module."""


def _make_args(**overrides):
    defaults = {
        "command": "cat",
        "doc": "abc123",
        "plain": False,
        "comments": False,
        "all": False,
        "tab": None,
        "all_tabs": False,
        "no_images": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _tab(id, title, text="", index=0, level=0):
    body = {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": text}}]}}
    ]} if text else {"content": []}
    return {
        "id": id, "title": title, "index": index,
        "nesting_level": level, "body": body,
    }


class TestCatTab:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="Tab content\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_by_title(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Notes")]
        args = _make_args(tab="Notes")
        rc = cmd_cat(args)
        assert rc == 0
        assert capsys.readouterr().out == "Tab content\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="content\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_case_insensitive(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        mock_tabs.return_value = [_tab("t1", "Notes")]
        args = _make_args(tab="notes")
        rc = cmd_cat(args)
        assert rc == 0
        mock_text.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="content\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_by_id(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t.abc", "My Tab")]
        args = _make_args(tab="t.abc")
        rc = cmd_cat(args)
        assert rc == 0
        mock_text.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="title match\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_title_preferred_over_id(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        """Title match takes priority over ID match."""
        mock_tabs.return_value = [
            _tab("t1", "t2"),  # title is "t2"
            _tab("t2", "Other"),
        ]
        args = _make_args(tab="t2")
        rc = cmd_cat(args)
        assert rc == 0
        # Should match first tab (title="t2"), not second (id="t2")
        mock_text.assert_called_once()
        called_tab = mock_text.call_args[0][0]
        assert called_tab["id"] == "t1"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_not_found(self, _svc, mock_tabs, _pf, _update):
        mock_tabs.return_value = [_tab("t1", "Tab 1")]
        args = _make_args(tab="nonexistent")
        with pytest.raises(GdocError, match="tab not found: nonexistent"):
            cmd_cat(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_json_output(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Notes")]
        args = _make_args(tab="Notes", json=True)
        rc = cmd_cat(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["tab"] == "Notes"
        assert data["content"] == "text\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_uses_docs_api_not_drive(
        self, _svc, mock_tabs, mock_text, _pf, _update,
    ):
        """--tab uses Docs API (get_document_tabs) not Drive export."""
        mock_tabs.return_value = [_tab("t1", "Tab 1")]
        args = _make_args(tab="Tab 1")
        with patch("gdoc.api.drive.export_doc") as mock_export:
            cmd_cat(args)
            mock_export.assert_not_called()
        mock_tabs.assert_called_once_with("abc123")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_default_requests_markdown(
        self, _svc, mock_tabs, mock_text, _pf, _update,
    ):
        """Default --tab renders markdown so headings survive round-trips."""
        mock_tabs.return_value = [_tab("t1", "Notes")]
        cmd_cat(_make_args(tab="Notes"))
        assert mock_text.call_args.kwargs.get("markdown") is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_plain_requests_verbatim(
        self, _svc, mock_tabs, mock_text, _pf, _update,
    ):
        """--plain --tab returns the verbatim, matchable text (no '#')."""
        mock_tabs.return_value = [_tab("t1", "Notes")]
        cmd_cat(_make_args(tab="Notes", plain=True))
        assert mock_text.call_args.kwargs.get("markdown") is False


class TestCatAllTabs:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_output(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = [
            _tab("t1", "First"),
            _tab("t2", "Second"),
        ]
        mock_text.side_effect = ["Hello\n", "World\n"]
        args = _make_args(all_tabs=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Tab: First ===" in out
        assert "Hello\n" in out
        assert "=== Tab: Second ===" in out
        assert "World\n" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_json(self, _svc, mock_tabs, mock_text, _pf, _update, capsys):
        mock_tabs.return_value = [_tab("t1", "Tab 1")]
        mock_text.return_value = "content\n"
        args = _make_args(all_tabs=True, json=True)
        rc = cmd_cat(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert "content" in data

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_empty(self, _svc, mock_tabs, _pf, _update, capsys):
        mock_tabs.return_value = []
        args = _make_args(all_tabs=True)
        rc = cmd_cat(args)
        assert rc == 0
        assert capsys.readouterr().out == ""


class TestCatTabMutualExclusivity:
    def test_tab_and_comments_conflict(self):
        args = _make_args(tab="Tab 1", comments=True, quiet=True)
        with pytest.raises(GdocError, match="mutually exclusive"):
            cmd_cat(args)

    def test_all_tabs_and_comments_conflict(self):
        args = _make_args(all_tabs=True, comments=True, quiet=True)
        with pytest.raises(GdocError, match="mutually exclusive"):
            cmd_cat(args)


_URL = "https://docs.google.com/document/d/abc123/edit"


class TestCatUrlTab:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="tab body\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_url_tab_routes_to_docs_api(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        """A non-t.0 ?tab= in the URL is honored like --tab."""
        mock_tabs.return_value = [_tab("t.second", "Second")]
        args = _make_args(doc=f"{_URL}?tab=t.second")
        with patch("gdoc.api.drive.export_doc") as mock_export:
            rc = cmd_cat(args)
            mock_export.assert_not_called()
        assert rc == 0
        assert capsys.readouterr().out == "tab body\n"
        mock_tabs.assert_called_once_with("abc123")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_url_tab_t0_stays_on_drive_export(self, _pf, _update, capsys):
        """?tab=t.0 is ambient noise: stay on the high-fidelity Drive path."""
        args = _make_args(doc=f"{_URL}?tab=t.0")
        with patch(
            "gdoc.api.drive.export_doc", return_value="whole doc\n"
        ) as mock_export, patch("gdoc.api.docs.get_document_tabs") as mock_tabs:
            rc = cmd_cat(args)
            mock_export.assert_called_once()
            mock_tabs.assert_not_called()
        assert rc == 0
        assert capsys.readouterr().out == "whole doc\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="flag body\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_flag_overrides_url_tab(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        """An explicit --tab wins over a differing URL tab."""
        mock_tabs.return_value = [_tab("t.second", "Second"), _tab("t.flag", "Flag")]
        args = _make_args(doc=f"{_URL}?tab=t.second", tab="Flag")
        rc = cmd_cat(args)
        assert rc == 0
        called_tab = mock_text.call_args[0][0]
        assert called_tab["id"] == "t.flag"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_overrides_url_tab(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        """--all-tabs wins over a URL tab (no conflict error)."""
        mock_tabs.return_value = [_tab("t1", "First"), _tab("t.second", "Second")]
        mock_text.side_effect = ["a\n", "b\n"]
        args = _make_args(doc=f"{_URL}?tab=t.second", all_tabs=True)
        rc = cmd_cat(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Tab: First ===" in out
        assert "=== Tab: Second ===" in out

    def test_url_tab_and_comments_conflict_mentions_url(self):
        args = _make_args(doc=f"{_URL}?tab=t.second", comments=True, quiet=True)
        with pytest.raises(GdocError, match="URL targets tab") as exc:
            cmd_cat(args)
        assert exc.value.exit_code == 3

    def test_url_tab_and_revision_conflict_mentions_url(self):
        args = _make_args(doc=f"{_URL}?tab=t.second", revision="latest", quiet=True)
        with pytest.raises(GdocError, match="URL targets tab") as exc:
            cmd_cat(args)
        assert exc.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_tab_text", return_value="first tab\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_flag_t0_forces_first_tab(
        self, _svc, mock_tabs, mock_text, _pf, _update, capsys,
    ):
        """Explicit --tab t.0 is the escape hatch: read the literal first tab
        via the Docs API rather than treating t.0 as ambient noise."""
        mock_tabs.return_value = [_tab("t.0", "First"), _tab("t.second", "Second")]
        args = _make_args(tab="t.0")
        with patch("gdoc.api.drive.export_doc") as mock_export:
            rc = cmd_cat(args)
        assert rc == 0
        assert mock_text.call_args[0][0]["id"] == "t.0"
        mock_export.assert_not_called()


class TestCatTabAwareness:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_preflight_and_state(
        self, _svc, mock_tabs, _text, mock_pf, mock_update,
    ):
        change_info = ChangeInfo(current_version=7)
        mock_pf.return_value = change_info
        # Multi-tab doc: a --tab read covers only that tab.
        mock_tabs.return_value = [_tab("t1", "Tab 1"), _tab("t2", "Tab 2")]
        args = _make_args(tab="Tab 1")
        rc = cmd_cat(args)
        assert rc == 0
        mock_pf.assert_called_once_with("abc123", quiet=False)
        # A tab read stamps that tab's baseline, not the whole-doc one.
        mock_update.assert_called_once_with(
            "abc123", change_info, command="cat", quiet=False,
            read_tab_id="t1",
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_single_tab_read_advances_global_baseline(
        self, _svc, mock_tabs, _text, mock_pf, mock_update,
    ):
        """On a single-tab doc, `cat --tab X` reads the whole document, so it
        advances the whole-doc baseline (read_tab_id None) rather than stamping
        a per-tab one — otherwise a following whole-doc write/push would be
        spuriously blocked."""
        change_info = ChangeInfo(current_version=7)
        mock_pf.return_value = change_info
        mock_tabs.return_value = [_tab("t.only", "Only")]
        rc = cmd_cat(_make_args(tab="Only"))
        assert rc == 0
        assert mock_update.call_args.kwargs.get("read_tab_id") is None

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.docs.get_tab_text", return_value="text\n")
    @patch("gdoc.api.docs.get_document_tabs")
    @patch("gdoc.api.docs.get_docs_service")
    def test_all_tabs_preflight_and_state(
        self, _svc, mock_tabs, _text, mock_pf, mock_update,
    ):
        change_info = ChangeInfo(current_version=7)
        mock_pf.return_value = change_info
        mock_tabs.return_value = [_tab("t1", "Tab 1")]
        args = _make_args(all_tabs=True)
        rc = cmd_cat(args)
        assert rc == 0
        mock_pf.assert_called_once()
        mock_update.assert_called_once()
        # --all-tabs is a whole-doc read: no per-tab stamp.
        assert mock_update.call_args.kwargs.get("read_tab_id") is None


class TestCatTabBaselinePersistence:
    """R2 regression lock, against real (tmp) state: on a multi-tab doc a
    single-tab read stamps only that tab's baseline and never advances the
    whole-doc one (which would let a sibling tab be overwritten unseen)."""

    def test_tab_read_does_not_advance_global_baseline(self, tmp_path):
        from gdoc.state import DocState, load_state, save_state

        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("abc123", DocState(last_read_version=50, last_version=50))
            change_info = ChangeInfo(
                current_version=100,
                mime_type="application/vnd.google-apps.document",
            )
            with patch("gdoc.notify.pre_flight", return_value=change_info), \
                 patch("gdoc.api.docs.get_document_tabs",
                       return_value=[
                           _tab("t.notes", "Notes", "body\n"),
                           _tab("t.other", "Other", "other\n"),
                       ]), \
                 patch("gdoc.api.docs.get_tab_text", return_value="body\n"), \
                 patch("gdoc.api.docs.get_docs_service"):
                rc = cmd_cat(_make_args(tab="Notes"))
            assert rc == 0
            state = load_state("abc123")
            assert state.last_read_version == 50  # global untouched
            assert state.tab_read_versions == {"t.notes": 100}
