"""Tests for spreadsheet handling in `gdoc cat`/`tabs`/`cells`."""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import (
    _format_sheet_table,
    _format_sheet_tsv,
    _quote_sheet_title,
    cmd_cat,
    cmd_cells,
    cmd_tabs,
)
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError

SHEET_MIME = "application/vnd.google-apps.spreadsheet"


@pytest.fixture(autouse=True)
def _sheet_mime(monkeypatch):
    """Pin spreadsheet detection to the Sheets path for this module."""
    monkeypatch.setattr(
        "gdoc.cli._file_mime", lambda doc_id, change_info: SHEET_MIME
    )
    # cmd_cells re-fetches the version after writing
    monkeypatch.setattr(
        "gdoc.api.drive.get_file_version",
        lambda doc_id: {"mimeType": SHEET_MIME, "version": 7, "modifiedTime": ""},
    )


def _make_args(**overrides):
    defaults = {
        "command": "cat",
        "doc": "sheet123",
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
        "tab": None,
        "all_tabs": False,
        "range": None,
        "comments": False,
        "max_bytes": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _meta(*sheets):
    return {"title": "Test", "sheets": list(sheets)}


def _sheet(id=0, title="Sheet1", index=0, rows=100, cols=26):
    return {"id": id, "title": title, "index": index, "rows": rows, "cols": cols}


class TestFormatters:
    def test_quote_sheet_title(self):
        assert _quote_sheet_title("Sheet1") == "'Sheet1'"
        assert _quote_sheet_title("It's") == "'It''s'"

    def test_tsv_pads_and_cleans(self):
        out = _format_sheet_tsv([["a", "b\tc"], ["d"]])
        assert out == "a\tb c\nd\t\n"

    def test_table_first_row_is_header(self):
        out = _format_sheet_table([["Name", "Y/N"], ["Ada", "Y"]])
        lines = out.splitlines()
        assert lines[0] == "| Name | Y/N |"
        assert lines[1] == "| ---- | --- |"
        assert lines[2] == "| Ada  | Y   |"

    def test_table_escapes_pipes(self):
        out = _format_sheet_table([["a|b"], ["c"]])
        assert "a\\|b" in out

    def test_table_empty(self):
        assert _format_sheet_table([]) == "(no values)\n"


class TestCatSheet:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_default_first_sheet_markdown(
        self, mock_meta, mock_values, _pf, _update, capsys
    ):
        mock_meta.return_value = _meta(_sheet())
        mock_values.return_value = {
            "range": "Sheet1!A1:B2",
            "values": [["Name", "OK"], ["Ada", "Y"]],
        }
        rc = cmd_cat(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "| Name | OK" in out
        assert "| Ada " in out
        mock_values.assert_called_once_with("sheet123", "'Sheet1'")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_multi_tab_hint_on_stderr(
        self, mock_meta, mock_values, _pf, _update, capsys
    ):
        mock_meta.return_value = _meta(_sheet(), _sheet(id=1, title="Two", index=1))
        mock_values.return_value = {"range": "Sheet1!A1", "values": [["x"]]}
        cmd_cat(_make_args())
        err = capsys.readouterr().err
        assert "2 tabs" in err
        assert "--all-tabs" in err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_tab_and_range(self, mock_meta, mock_values, _pf, _update, capsys):
        mock_meta.return_value = _meta(_sheet(), _sheet(id=7, title="Data", index=1))
        mock_values.return_value = {"range": "Data!B2:C3", "values": [["1", "2"]]}
        cmd_cat(_make_args(tab="data", range="B2:C3"))
        mock_values.assert_called_once_with("sheet123", "'Data'!B2:C3")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_tab_by_sheet_id(self, mock_meta, mock_values, _pf, _update):
        mock_meta.return_value = _meta(_sheet(), _sheet(id=7, title="Data", index=1))
        mock_values.return_value = {"range": "Data!A1", "values": []}
        cmd_cat(_make_args(tab="7"))
        mock_values.assert_called_once_with("sheet123", "'Data'")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_tab_not_found(self, mock_meta, _pf, _update):
        mock_meta.return_value = _meta(_sheet())
        with pytest.raises(GdocError, match="tab not found: Nope"):
            cmd_cat(_make_args(tab="Nope"))

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_plain_tsv(self, mock_meta, mock_values, _pf, _update, capsys):
        mock_meta.return_value = _meta(_sheet())
        mock_values.return_value = {
            "range": "Sheet1!A1:B2",
            "values": [["Name", "OK"], ["Ada"]],
        }
        cmd_cat(_make_args(plain=True))
        out = capsys.readouterr().out
        assert out == "Name\tOK\nAda\t\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_json_output(self, mock_meta, mock_values, _pf, _update, capsys):
        mock_meta.return_value = _meta(_sheet())
        mock_values.return_value = {"range": "Sheet1!A1", "values": [["x"]]}
        cmd_cat(_make_args(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "range": "Sheet1!A1", "values": [["x"]]}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.batch_get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_all_tabs(self, mock_meta, mock_batch, _pf, _update, capsys):
        mock_meta.return_value = _meta(_sheet(), _sheet(id=1, title="Two", index=1))
        mock_batch.return_value = [
            {"range": "Sheet1!A1", "values": [["a"]]},
            {"range": "Two!A1", "values": [["b"]]},
        ]
        cmd_cat(_make_args(all_tabs=True))
        out = capsys.readouterr().out
        assert "=== Tab: Sheet1 ===" in out
        assert "=== Tab: Two ===" in out
        mock_batch.assert_called_once_with("sheet123", ["'Sheet1'", "'Two'"])

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_all_tabs_with_range_rejected(self, mock_meta, _pf, _update):
        mock_meta.return_value = _meta(_sheet())
        with pytest.raises(GdocError, match="mutually exclusive"):
            cmd_cat(_make_args(all_tabs=True, range="A1:B2"))

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_comments_rejected(self, _pf, _update):
        with pytest.raises(GdocError, match="not supported for spreadsheets"):
            cmd_cat(_make_args(comments=True))

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.sheets.get_values")
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_mime_from_preflight(
        self, mock_meta, mock_values, mock_pf, _update, capsys, monkeypatch
    ):
        # Use the real _file_mime: detection comes from ChangeInfo.mime_type.
        from gdoc.cli import _file_mime

        monkeypatch.setattr("gdoc.cli._file_mime", _file_mime)
        mock_pf.return_value = ChangeInfo(mime_type=SHEET_MIME)
        mock_meta.return_value = _meta(_sheet())
        mock_values.return_value = {"range": "Sheet1!A1", "values": [["x"]]}
        rc = cmd_cat(_make_args())
        assert rc == 0


class TestCatDocRangeRejected:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_range_on_doc_rejected(self, _pf, _update, monkeypatch):
        monkeypatch.setattr(
            "gdoc.cli._file_mime",
            lambda doc_id, change_info: "application/vnd.google-apps.document",
        )
        with pytest.raises(GdocError, match="only supported for spreadsheets"):
            cmd_cat(_make_args(range="A1:B2"))


class TestTabsSheet:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_terse_lists_dims(self, mock_meta, _pf, _update, capsys):
        mock_meta.return_value = _meta(
            _sheet(), _sheet(id=7, title="Data", index=1, rows=5, cols=3)
        )
        rc = cmd_tabs(_make_args(command="tabs"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "0\tSheet1\t100x26" in out
        assert "7\tData\t5x3" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_json(self, mock_meta, _pf, _update, capsys):
        mock_meta.return_value = _meta(_sheet())
        cmd_tabs(_make_args(command="tabs", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["tabs"][0]["title"] == "Sheet1"
        assert data["tabs"][0]["rows"] == 100

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.get_spreadsheet_meta")
    def test_plain(self, mock_meta, _pf, _update, capsys):
        mock_meta.return_value = _meta(_sheet())
        cmd_tabs(_make_args(command="tabs", plain=True))
        assert capsys.readouterr().out == "0\tSheet1\n"


def _cells_args(**overrides):
    defaults = {
        "command": "cells",
        "doc": "sheet123",
        "range": "B2",
        "value": None,
        "file": None,
        "stdin": False,
        "append": False,
        "user_entered": False,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCells:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_single_value(self, mock_update, _pf, _state, capsys):
        mock_update.return_value = {"range": "Sheet1!B2", "rows": 1, "cells": 1}
        rc = cmd_cells(_cells_args(value=["Y"]))
        assert rc == 0
        mock_update.assert_called_once_with(
            "sheet123", "B2", [["Y"]], user_entered=False, append=False
        )
        assert "Updated Sheet1!B2 (1 cells)" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_multiple_values_one_row(self, mock_update, _pf, _state):
        mock_update.return_value = {"range": "Sheet1!B2:C2", "rows": 1, "cells": 2}
        cmd_cells(_cells_args(range="B2:C2", value=["Y", "quote"]))
        assert mock_update.call_args.args[2] == [["Y", "quote"]]

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_stdin_tsv(self, mock_update, _pf, _state, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("a\tb\nc\td\n"))
        mock_update.return_value = {"range": "Sheet1!A1:B2", "rows": 2, "cells": 4}
        cmd_cells(_cells_args(range="A1", stdin=True))
        assert mock_update.call_args.args[2] == [["a", "b"], ["c", "d"]]

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_csv_file(self, mock_update, _pf, _state, tmp_path):
        f = tmp_path / "vals.csv"
        f.write_text('a,"b,c"\nd,e\n', encoding="utf-8")
        mock_update.return_value = {"range": "Sheet1!A1:B2", "rows": 2, "cells": 4}
        cmd_cells(_cells_args(range="A1", file=str(f)))
        assert mock_update.call_args.args[2] == [["a", "b,c"], ["d", "e"]]

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_append(self, mock_write, _pf, _state, capsys):
        mock_write.return_value = {"range": "Sheet1!A5", "rows": 1, "cells": 1}
        cmd_cells(_cells_args(range="A1", value=["new"], append=True))
        assert mock_write.call_args.kwargs["append"] is True
        assert "Appended" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_user_entered(self, mock_update, _pf, _state):
        mock_update.return_value = {"range": "Sheet1!B2", "rows": 1, "cells": 1}
        cmd_cells(_cells_args(value=["=SUM(A:A)"], user_entered=True))
        assert mock_update.call_args.kwargs["user_entered"] is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_no_value_source_rejected(self, _pf, _state):
        with pytest.raises(GdocError, match="exactly one of"):
            cmd_cells(_cells_args())

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    def test_two_value_sources_rejected(self, _pf, _state):
        with pytest.raises(GdocError, match="exactly one of"):
            cmd_cells(_cells_args(value=["x"], stdin=True))

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(mime_type="application/vnd.google-apps.document"),
    )
    def test_not_a_spreadsheet(self, _pf, _state):
        with pytest.raises(GdocError, match="not a spreadsheet"):
            cmd_cells(_cells_args(value=["x"]))

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(
            mime_type=SHEET_MIME, current_version=5, last_read_version=3
        ),
    )
    @patch("gdoc.api.sheets.write_values")
    def test_conflict_warns_but_writes(self, mock_update, _pf, _state, capsys):
        mock_update.return_value = {"range": "Sheet1!B2", "rows": 1, "cells": 1}
        rc = cmd_cells(_cells_args(value=["Y"]))
        assert rc == 0
        captured = capsys.readouterr()
        assert "WARN: doc changed since last read" in captured.err
        assert "Updated" in captured.out
        mock_update.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_records_post_write_version(self, mock_update, _pf, mock_state):
        mock_update.return_value = {"range": "Sheet1!B2", "rows": 1, "cells": 1}
        cmd_cells(_cells_args(value=["Y"]))
        assert mock_state.call_args.kwargs["command_version"] == 7

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.sheets.write_values")
    def test_json_output(self, mock_update, _pf, _state, capsys):
        mock_update.return_value = {"range": "Sheet1!B2", "rows": 1, "cells": 1}
        cmd_cells(_cells_args(value=["Y"], json=True))
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "range": "Sheet1!B2", "rows": 1, "cells": 1}
