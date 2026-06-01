"""Tests for cell-addressed `gdoc edit` (--cell/--col/--table)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.api.docs import (
    _cell_text_range,
    _parse_coord,
    resolve_cell_range,
)
from gdoc.cli import cmd_edit
from gdoc.util import GdocError


def _cell(text, start):
    return {"content": [{
        "paragraph": {"elements": [
            {"startIndex": start, "textRun": {"content": text}},
        ]},
    }]}


def _table(rows):
    return {"table": {"tableRows": [
        {"tableCells": [_cell(t, s) for (t, s) in row]} for row in rows
    ]}}


# A 2-column label/value grid, plus an empty value cell.
GRID = {"content": [_table([
    [("Label A\n", 5), ("Value A\n", 20)],
    [("Discussion topics from JP\n", 40), ("\n", 70)],
])]}


class TestParseCoord:
    def test_parses_coordinates(self):
        assert _parse_coord("0,1") == (0, 1)
        assert _parse_coord(" 2 , 3 ") == (2, 3)

    def test_labels_are_not_coordinates(self):
        assert _parse_coord("Discussion topics") is None
        assert _parse_coord("1,") is None


class TestCellTextRange:
    def test_non_empty_cell_excludes_final_newline(self):
        r = _cell_text_range(_cell("Value A\n", 20))
        assert r == {"startIndex": 20, "endIndex": 27}

    def test_empty_cell_is_zero_width(self):
        r = _cell_text_range(_cell("\n", 70))
        assert r == {"startIndex": 70, "endIndex": 70}

    def test_multi_paragraph_cell(self):
        cell = {"content": [
            {"paragraph": {"elements": [
                {"startIndex": 5, "textRun": {"content": "a\n"}},
            ]}},
            {"paragraph": {"elements": [
                {"startIndex": 7, "textRun": {"content": "b\n"}},
            ]}},
        ]}
        assert _cell_text_range(cell) == {"startIndex": 5, "endIndex": 8}


class TestResolveCellRange:
    def test_label_targets_cell_to_the_right(self):
        assert resolve_cell_range(GRID, "Label A") == {"startIndex": 20, "endIndex": 27}

    def test_label_with_empty_target_cell(self):
        assert resolve_cell_range(GRID, "Discussion topics from JP") == {
            "startIndex": 70, "endIndex": 70,
        }

    def test_col_override(self):
        # col 0 targets the label cell itself.
        assert resolve_cell_range(GRID, "Label A", col=0) == {
            "startIndex": 5, "endIndex": 12,
        }

    def test_coordinate_mode(self):
        assert resolve_cell_range(GRID, "0,1") == {"startIndex": 20, "endIndex": 27}

    def test_label_not_found(self):
        assert resolve_cell_range(GRID, "Nope") is None

    def test_coordinate_out_of_range(self):
        assert resolve_cell_range(GRID, "9,9") is None
        assert resolve_cell_range(GRID, "0,1", table_index=5) is None

    def test_label_honors_explicit_table(self):
        two = {"content": [
            _table([[("Dup\n", 5), ("first\n", 20)]]),
            _table([[("Dup\n", 40), ("second\n", 55)]]),
        ]}
        # No --table → scan all tables; first match (table 0) wins.
        assert resolve_cell_range(two, "Dup") == {"startIndex": 20, "endIndex": 25}
        # --table 1 selects the matching cell in the second table.
        assert resolve_cell_range(two, "Dup", table_index=1) == {
            "startIndex": 55, "endIndex": 61,
        }
        # Out-of-range table → None even if the label exists elsewhere.
        assert resolve_cell_range(two, "Dup", table_index=9) is None

    def test_normalize_label(self):
        body = {"content": [_table([[("JP\u2019s job\n", 5), ("v\n", 20)]])]}
        assert resolve_cell_range(body, "JP's job") is None
        assert resolve_cell_range(body, "JP's job", normalize=True) == {
            "startIndex": 20, "endIndex": 21,
        }


def _args(**kw):
    base = {
        "command": "edit", "doc": "abc123",
        "old_text": None, "new_text": None,
        "old_file": None, "new_file": None,
        "all": False, "case_sensitive": False, "normalize": False,
        "cell": None, "col": None, "table": None,
        "json": False, "verbose": False, "plain": False,
        "quiet": False, "tab": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _doc():
    return {"revisionId": "rev123", "body": GRID}


def _ver():
    return {"version": 42, "modifiedTime": "2026-01-01T00:00:00Z"}


class TestCmdEditCell:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_ver())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.get_document", return_value=_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_label_edit_succeeds(self, _pf, _doc_, mock_replace, _v, _u, capsys):
        # Single positional carries the replacement in cell mode.
        rc = cmd_edit(_args(cell="Label A", old_text="new value"))
        assert rc == 0
        assert "OK replaced 1 occurrence" in capsys.readouterr().out
        args = mock_replace.call_args[0]
        assert args[1] == [{"startIndex": 20, "endIndex": 27}]
        assert args[2] == "new value"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value=_ver())
    @patch("gdoc.api.docs.replace_formatted", return_value=1)
    @patch("gdoc.api.docs.get_document", return_value=_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_coordinate_edit_succeeds(self, _pf, _doc_, mock_replace, _v, _u):
        rc = cmd_edit(_args(cell="0,1", old_text="x"))
        assert rc == 0
        assert mock_replace.call_args[0][1] == [{"startIndex": 20, "endIndex": 27}]

    @patch("gdoc.api.docs.replace_formatted")
    @patch("gdoc.api.docs.get_document", return_value=_doc())
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_cell_not_found_errors(self, _pf, _doc_, mock_replace):
        with pytest.raises(GdocError, match="cell not found") as exc:
            cmd_edit(_args(cell="Nonexistent", old_text="x"))
        assert exc.value.exit_code == 3
        mock_replace.assert_not_called()

    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_missing_replacement_errors(self, _pf):
        with pytest.raises(GdocError, match="needs replacement text") as exc:
            cmd_edit(_args(cell="Label A"))
        assert exc.value.exit_code == 3
