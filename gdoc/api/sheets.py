"""Sheets API wrapper functions with error translation."""

from googleapiclient.errors import HttpError

from gdoc.api import get_sheets_service
from gdoc.util import AuthError, GdocError


def _translate_http_error(e: HttpError, spreadsheet_id: str) -> None:
    """Translate a googleapiclient HttpError into GdocError or AuthError."""
    status = int(e.resp.status)
    reason = e.reason if hasattr(e, "reason") and e.reason else ""

    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")

    if status == 403:
        raise GdocError(f"Permission denied: {spreadsheet_id}")

    if status == 404:
        raise GdocError(f"Spreadsheet not found: {spreadsheet_id}")

    if status == 400:
        if "Unable to parse range" in reason:
            raise GdocError(f"Invalid range: {reason}", exit_code=3)
        raise GdocError(f"Sheets API error: {reason}")

    raise GdocError(f"API error ({status}): {reason}")


def get_spreadsheet_meta(spreadsheet_id: str) -> dict:
    """Get spreadsheet title and per-sheet (tab) properties.

    Returns dict with keys: title, sheets — where sheets is a list of
    {id, title, index, rows, cols}.
    """
    try:
        service = get_sheets_service()
        result = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="properties.title,sheets.properties",
            )
            .execute()
        )
    except HttpError as e:
        _translate_http_error(e, spreadsheet_id)

    sheets = []
    for s in result.get("sheets", []):
        props = s.get("properties", {})
        grid = props.get("gridProperties", {})
        sheets.append(
            {
                "id": props.get("sheetId"),
                "title": props.get("title", ""),
                "index": props.get("index", 0),
                "rows": grid.get("rowCount", 0),
                "cols": grid.get("columnCount", 0),
            }
        )
    return {
        "title": result.get("properties", {}).get("title", ""),
        "sheets": sheets,
    }


def get_values(spreadsheet_id: str, range_: str) -> dict:
    """Read a range of cell values.

    Returns dict with keys: range (the resolved A1 range) and values
    (list of rows; trailing empty cells are omitted by the API).
    """
    try:
        service = get_sheets_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_)
            .execute()
        )
        return {
            "range": result.get("range", range_),
            "values": result.get("values", []),
        }
    except HttpError as e:
        _translate_http_error(e, spreadsheet_id)


def batch_get_values(spreadsheet_id: str, ranges: list[str]) -> list[dict]:
    """Read multiple ranges in one round-trip.

    Returns one {range, values} dict per requested range, in order.
    """
    try:
        service = get_sheets_service()
        result = (
            service.spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
            .execute()
        )
        value_ranges = result.get("valueRanges", [])
        return [
            {"range": vr.get("range", rng), "values": vr.get("values", [])}
            for vr, rng in zip(value_ranges, ranges, strict=True)
        ]
    except HttpError as e:
        _translate_http_error(e, spreadsheet_id)


def write_values(
    spreadsheet_id: str,
    range_: str,
    values: list[list],
    user_entered: bool = False,
    append: bool = False,
) -> dict:
    """Write values to a range, or append rows after the table at range_.

    Returns {range, rows, cells} from the API update summary.
    """
    try:
        service = get_sheets_service()
        kwargs = {
            "spreadsheetId": spreadsheet_id,
            "range": range_,
            "valueInputOption": "USER_ENTERED" if user_entered else "RAW",
            "body": {"values": values},
        }
        api = service.spreadsheets().values()
        if append:
            result = api.append(insertDataOption="INSERT_ROWS", **kwargs).execute()
            result = result.get("updates", {})
        else:
            result = api.update(**kwargs).execute()
        return {
            "range": result.get("updatedRange", range_),
            "rows": result.get("updatedRows", 0),
            "cells": result.get("updatedCells", 0),
        }
    except HttpError as e:
        _translate_http_error(e, spreadsheet_id)
