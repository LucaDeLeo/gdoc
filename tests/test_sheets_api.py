"""Tests for gdoc.api.sheets: Sheets API wrapper functions with mocked service."""

from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.sheets import (
    _translate_http_error,
    batch_get_values,
    get_spreadsheet_meta,
    get_values,
    write_values,
)
from gdoc.util import AuthError, GdocError


def _make_http_error(status: int, reason: str = "") -> HttpError:
    """Create a mock HttpError with the given status and reason."""
    resp = httplib2.Response({"status": str(status)})
    error = HttpError(resp, b"")
    error.reason = reason
    return error


def _mock_service():
    return MagicMock()


class TestTranslateHttpError:
    def test_401_raises_auth_error(self):
        err = _make_http_error(401)
        with pytest.raises(AuthError, match="Authentication expired") as exc_info:
            _translate_http_error(err, "abc123")
        assert exc_info.value.exit_code == 2

    def test_403_raises_gdoc_error(self):
        err = _make_http_error(403, reason="forbidden")
        with pytest.raises(GdocError, match="Permission denied: abc123"):
            _translate_http_error(err, "abc123")

    def test_404_raises_gdoc_error(self):
        err = _make_http_error(404)
        with pytest.raises(GdocError, match="Spreadsheet not found: abc123"):
            _translate_http_error(err, "abc123")

    def test_400_bad_range(self):
        err = _make_http_error(400, reason="Unable to parse range: 'Nope'!ZZ")
        with pytest.raises(GdocError, match="Invalid range") as exc_info:
            _translate_http_error(err, "abc123")
        assert exc_info.value.exit_code == 3

    def test_400_other(self):
        err = _make_http_error(400, reason="something else")
        with pytest.raises(GdocError, match="Sheets API error: something else"):
            _translate_http_error(err, "abc123")

    def test_other_status(self):
        err = _make_http_error(500, reason="boom")
        with pytest.raises(GdocError, match=r"API error \(500\): boom"):
            _translate_http_error(err, "abc123")


class TestGetSpreadsheetMeta:
    @patch("gdoc.api.sheets.get_sheets_service")
    def test_returns_title_and_sheets(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        service.spreadsheets().get().execute.return_value = {
            "properties": {"title": "Budget"},
            "sheets": [
                {
                    "properties": {
                        "sheetId": 0,
                        "title": "Sheet1",
                        "index": 0,
                        "gridProperties": {"rowCount": 100, "columnCount": 26},
                    }
                },
                {
                    "properties": {
                        "sheetId": 99,
                        "title": "Data",
                        "index": 1,
                        "gridProperties": {"rowCount": 5, "columnCount": 3},
                    }
                },
            ],
        }
        meta = get_spreadsheet_meta("sheet123")
        assert meta["title"] == "Budget"
        assert meta["sheets"] == [
            {"id": 0, "title": "Sheet1", "index": 0, "rows": 100, "cols": 26},
            {"id": 99, "title": "Data", "index": 1, "rows": 5, "cols": 3},
        ]

    @patch("gdoc.api.sheets.get_sheets_service")
    def test_http_error_translated(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        service.spreadsheets().get().execute.side_effect = _make_http_error(404)
        with pytest.raises(GdocError, match="Spreadsheet not found"):
            get_spreadsheet_meta("sheet123")


class TestGetValues:
    @patch("gdoc.api.sheets.get_sheets_service")
    def test_returns_range_and_values(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        service.spreadsheets().values().get().execute.return_value = {
            "range": "Sheet1!A1:B2",
            "values": [["a", "b"], ["c"]],
        }
        data = get_values("sheet123", "'Sheet1'")
        assert data == {"range": "Sheet1!A1:B2", "values": [["a", "b"], ["c"]]}

    @patch("gdoc.api.sheets.get_sheets_service")
    def test_empty_sheet_has_no_values_key(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        service.spreadsheets().values().get().execute.return_value = {
            "range": "Sheet1!A1:Z100",
        }
        data = get_values("sheet123", "'Sheet1'")
        assert data["values"] == []


class TestWriteValues:
    @patch("gdoc.api.sheets.get_sheets_service")
    def test_update_raw(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        update = service.spreadsheets().values().update
        update().execute.return_value = {
            "updatedRange": "Sheet1!B2:C3",
            "updatedRows": 2,
            "updatedCells": 4,
        }
        update.reset_mock()
        result = write_values("sheet123", "B2:C3", [["a", "b"], ["c", "d"]])
        assert result == {"range": "Sheet1!B2:C3", "rows": 2, "cells": 4}
        kwargs = update.call_args.kwargs
        assert kwargs["valueInputOption"] == "RAW"
        assert kwargs["body"] == {"values": [["a", "b"], ["c", "d"]]}

    @patch("gdoc.api.sheets.get_sheets_service")
    def test_update_user_entered(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        update = service.spreadsheets().values().update
        update().execute.return_value = {}
        update.reset_mock()
        write_values("sheet123", "B2", [["=SUM(A:A)"]], user_entered=True)
        assert update.call_args.kwargs["valueInputOption"] == "USER_ENTERED"

    @patch("gdoc.api.sheets.get_sheets_service")
    def test_append(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        append = service.spreadsheets().values().append
        append().execute.return_value = {
            "updates": {
                "updatedRange": "Sheet1!A5:B5",
                "updatedRows": 1,
                "updatedCells": 2,
            }
        }
        append.reset_mock()
        result = write_values("sheet123", "A1", [["x", "y"]], append=True)
        assert result == {"range": "Sheet1!A5:B5", "rows": 1, "cells": 2}
        assert append.call_args.kwargs["insertDataOption"] == "INSERT_ROWS"


class TestBatchGetValues:
    @patch("gdoc.api.sheets.get_sheets_service")
    def test_one_round_trip_in_order(self, mock_svc):
        service = _mock_service()
        mock_svc.return_value = service
        batch = service.spreadsheets().values().batchGet
        batch().execute.return_value = {
            "valueRanges": [
                {"range": "Sheet1!A1:B2", "values": [["a"]]},
                {"range": "Two!A1:C3"},
            ]
        }
        batch.reset_mock()
        result = batch_get_values("sheet123", ["'Sheet1'", "'Two'"])
        assert result == [
            {"range": "Sheet1!A1:B2", "values": [["a"]]},
            {"range": "Two!A1:C3", "values": []},
        ]
        assert batch.call_args.kwargs["ranges"] == ["'Sheet1'", "'Two'"]
