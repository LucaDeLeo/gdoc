"""Tests for the `gdoc insert` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_insert
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError
from tests.conftest import doc_with_tabs as _tabs_doc
from tests.conftest import whole_doc_read_state as _read_state


def _make_args(**overrides):
    defaults = {
        "command": "insert",
        "doc": "abc123",
        "file": "/tmp/content.md",
        "tab": "TODO",
        "position": "start",
        "force": False,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _insert_result(tab_id="t.todo", tab_title="TODO", insert_index=1):
    return {
        "tab_id": tab_id,
        "tab_title": tab_title,
        "insert_index": insert_index,
    }


def _preflight_ok():
    return ChangeInfo(current_version=10, last_read_version=10)


class TestInsertBasic:
    @patch("gdoc.state.load_state", return_value=_read_state())
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_terse_output(
        self, _pf, mock_insert, _ver, _update, mock_doc, _state,
        tmp_path, capsys,
    ):
        f = tmp_path / "content.md"
        f.write_text("# Hello")
        args = _make_args(file=str(f))
        rc = cmd_insert(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert 'OK inserted into "TODO"' in out
        mock_insert.assert_called_once_with(
            "abc123", "TODO", "# Hello",
            position="start", replace=False, doc=mock_doc.return_value,
        )

    @patch("gdoc.state.load_state", return_value=_read_state())
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_json_output(
        self, _pf, _mock_insert, _ver, _update, _doc, _state,
        tmp_path, capsys,
    ):
        f = tmp_path / "content.md"
        f.write_text("hi")
        args = _make_args(file=str(f), json=True)
        rc = cmd_insert(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["inserted"] is True
        assert data["tab_id"] == "t.todo"
        assert data["tab_title"] == "TODO"
        assert data["version"] == 11

    @patch("gdoc.state.load_state", return_value=_read_state())
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_position_end_is_forwarded(
        self, _pf, mock_insert, _ver, _update, mock_doc, _state, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("tail")
        args = _make_args(file=str(f), position="end")
        cmd_insert(args)
        mock_insert.assert_called_once_with(
            "abc123", "TODO", "tail",
            position="end", replace=False, doc=mock_doc.return_value,
        )


class TestInsertFrontmatterStrip:
    @patch("gdoc.state.load_state", return_value=_read_state())
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_frontmatter_stripped(
        self, _pf, mock_insert, _ver, _update, _doc, _state, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text(
            "---\ngdoc: abc123\ntitle: Whatever\n---\n# Real content\n",
        )
        args = _make_args(file=str(f))
        cmd_insert(args)
        body = mock_insert.call_args.args[2]
        assert body.startswith("# Real content")
        assert "---" not in body
        assert "gdoc:" not in body


class TestInsertFileErrors:
    def test_file_not_found(self):
        args = _make_args(file="/nonexistent/missing.md")
        with pytest.raises(GdocError, match="file not found") as exc:
            cmd_insert(args)
        assert exc.value.exit_code == 3

    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_empty_file_after_frontmatter_strip(self, _pf, tmp_path):
        f = tmp_path / "content.md"
        f.write_text("---\ngdoc: abc123\n---\n\n\n")
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="no content") as exc:
            cmd_insert(args)
        assert exc.value.exit_code == 3


class TestInsertUrlTab:
    @patch("gdoc.state.load_state", return_value=_read_state())
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.second", "Second"), ("t.x", "X")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_url_tab_satisfies_requirement(
        self, _pf, mock_insert, _ver, _update, mock_doc, _state, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("# Hello")
        args = _make_args(
            file=str(f),
            doc="https://docs.google.com/document/d/abc123/edit?tab=t.second",
            tab=None,
        )
        rc = cmd_insert(args)
        assert rc == 0
        mock_insert.assert_called_once_with(
            "abc123", "t.second", "# Hello",
            position="start", replace=False, doc=mock_doc.return_value,
        )

    def test_no_tab_anywhere_errors(self, tmp_path):
        f = tmp_path / "content.md"
        f.write_text("# Hello")
        args = _make_args(file=str(f), doc="abc123", tab=None)
        with pytest.raises(GdocError, match="--tab is required") as exc:
            cmd_insert(args)
        assert exc.value.exit_code == 3

    def test_url_t0_errors_with_ambient_hint(self, tmp_path):
        # ?tab=t.0 collapses to "no tab", but the error names t.0 as the
        # ignored ambient default rather than telling the user to pass a URL
        # ?tab= (which they already did).
        f = tmp_path / "content.md"
        f.write_text("# Hello")
        args = _make_args(
            file=str(f),
            doc="https://docs.google.com/document/d/abc123/edit?tab=t.0",
            tab=None,
        )
        with pytest.raises(GdocError, match="ambient default") as exc:
            cmd_insert(args)
        assert exc.value.exit_code == 3

    @patch("gdoc.state.load_state", return_value=_read_state())
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.e", "Explicit"), ("t.second", "Second")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight", return_value=_preflight_ok())
    def test_flag_overrides_url_tab(
        self, _pf, mock_insert, _ver, _update, _doc, _state, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("# Hello")
        args = _make_args(
            file=str(f),
            doc="https://docs.google.com/document/d/abc123/edit?tab=t.second",
            tab="Explicit",
        )
        cmd_insert(args)
        assert mock_insert.call_args.args[1] == "Explicit"


class TestInsertConflict:
    @patch("gdoc.state.load_state", return_value=_read_state(version=5))
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab")
    @patch("gdoc.notify.pre_flight")
    def test_blocks_on_conflict(
        self, mock_pf, mock_insert, _ver, _doc, _state, tmp_path,
    ):
        # Per-tab check: the effective baseline (5) trails the current
        # version (10), so the insert conflicts.
        f = tmp_path / "content.md"
        f.write_text("hi")
        mock_pf.return_value = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        args = _make_args(file=str(f))
        with pytest.raises(GdocError, match="may have changed"):
            cmd_insert(args)
        mock_insert.assert_not_called()

    @patch("gdoc.state.load_state", return_value=_read_state(version=5))
    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 11})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value=_insert_result())
    @patch("gdoc.notify.pre_flight")
    def test_force_bypasses_conflict(
        self, mock_pf, mock_insert, _ver, _update, _doc, _state, tmp_path,
    ):
        f = tmp_path / "content.md"
        f.write_text("hi")
        mock_pf.return_value = ChangeInfo(
            current_version=10, last_read_version=5,
        )
        args = _make_args(file=str(f), force=True)
        rc = cmd_insert(args)
        assert rc == 0
        mock_insert.assert_called_once()

    @patch(
        "gdoc.api.docs.get_document_with_tabs",
        return_value=_tabs_doc(("t.todo", "TODO"), ("t.b", "B")),
    )
    @patch("gdoc.api.docs.insert_markdown_into_tab")
    @patch("gdoc.notify.pre_flight")
    def test_tab_read_satisfies_insert(
        self, mock_pf, mock_insert, _doc, tmp_path,
    ):
        # The regression the per-tab check fixes: `cat --tab X` (which
        # stamps only tab_read_versions) must satisfy `insert --tab X` —
        # the whole-doc baseline stays None.
        from gdoc.state import DocState

        f = tmp_path / "content.md"
        f.write_text("hi")
        mock_pf.return_value = ChangeInfo(current_version=11)
        state = DocState(tab_read_versions={"t.todo": 11})
        with patch("gdoc.state.load_state", return_value=state), \
             patch("gdoc.state.update_state_after_command"), \
             patch(
                 "gdoc.api.drive.get_file_version",
                 return_value={"version": 11},
             ):
            mock_insert.return_value = _insert_result()
            rc = cmd_insert(_make_args(file=str(f)))
        assert rc == 0
        mock_insert.assert_called_once()
