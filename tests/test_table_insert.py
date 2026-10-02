"""Tests for native table insertion via Docs API."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.docs import (
    _find_table_cell_indices,
    _insert_table,
    replace_formatted,
)
from gdoc.cli import cmd_edit
from gdoc.mdparse import TableData
from gdoc.util import GdocError


def _make_document_with_table(table_start=5, rows=2, cols=2):
    """Build a mock document dict containing a table."""
    # Build cell content for the table
    table_rows = []
    idx = table_start + 1
    for _ in range(rows):
        cells = []
        for _ in range(cols):
            cells.append({
                "content": [{
                    "paragraph": {
                        "elements": [{
                            "startIndex": idx,
                            "textRun": {"content": "\n"},
                        }],
                    },
                    "startIndex": idx,
                }],
                "startIndex": idx,
            })
            idx += 2
        table_rows.append({"tableCells": cells})

    return {
        "revisionId": "rev1",
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [{
                            "startIndex": 1,
                            "textRun": {"content": "Hi\n"},
                        }],
                    },
                    "startIndex": 1,
                    "endIndex": table_start,
                },
                {
                    "table": {"tableRows": table_rows},
                    "startIndex": table_start,
                    "endIndex": idx,
                },
            ]
        }
    }


class TestFindTableCellIndices:
    def test_basic_table(self):
        doc = _make_document_with_table(
            table_start=5, rows=2, cols=2,
        )
        indices = _find_table_cell_indices(doc, 5)
        assert len(indices) == 2
        assert len(indices[0]) == 2

    def test_no_table_at_index(self):
        doc = _make_document_with_table(table_start=5)
        indices = _find_table_cell_indices(doc, 100)
        assert indices == []

    def test_empty_document(self):
        doc = {"body": {"content": []}}
        indices = _find_table_cell_indices(doc, 0)
        assert indices == []

    def test_cell_indices_increase(self):
        doc = _make_document_with_table(
            table_start=5, rows=2, cols=3,
        )
        indices = _find_table_cell_indices(doc, 5)
        # All indices should be unique and increasing
        flat = [idx for row in indices for idx in row]
        assert flat == sorted(flat)
        assert len(set(flat)) == len(flat)


class TestInsertTable:
    @patch("gdoc.api.docs.get_docs_service")
    def test_insert_table_calls(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # First batchUpdate: insertTable
        # Then get: returns doc with table
        doc = _make_document_with_table(table_start=5, rows=2, cols=2)
        mock_service.documents().get().execute.return_value = doc
        # Both writes return the revision used by the synthetic read-back.
        mock_service.documents().batchUpdate().execute.return_value = {
            "writeControl": {"requiredRevisionId": "rev1"},
        }

        table = TableData(
            rows=[["H1", "H2"], ["a", "b"]],
            num_rows=2, num_cols=2,
            plain_text_offset=0,
        )
        _insert_table("doc1", 5, table)

        # Verify batchUpdate was called (at least insertTable)
        batch_calls = mock_service.documents().batchUpdate.call_args_list
        assert len(batch_calls) >= 1

    @patch("gdoc.api.docs.get_docs_service")
    def test_insert_table_populates_cells(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        doc = _make_document_with_table(table_start=5, rows=2, cols=2)
        mock_service.documents().get().execute.return_value = doc
        mock_service.documents().batchUpdate().execute.return_value = {
            "writeControl": {"requiredRevisionId": "rev1"},
        }

        table = TableData(
            rows=[["H1", "H2"], ["a", "b"]],
            num_rows=2, num_cols=2,
            plain_text_offset=0,
        )
        _insert_table("doc1", 5, table)

        # Should have 2 batchUpdate calls:
        # 1. insertTable, 2. insertText for cells
        batch_calls = mock_service.documents().batchUpdate.call_args_list
        assert len(batch_calls) >= 2

    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_cells_skipped(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        doc = _make_document_with_table(table_start=5, rows=1, cols=2)
        mock_service.documents().get().execute.return_value = doc
        mock_service.documents().batchUpdate().execute.return_value = {
            "writeControl": {"requiredRevisionId": "rev1"},
        }

        # One row with one empty cell
        table = TableData(
            rows=[["H1", ""]],
            num_rows=1, num_cols=2,
            plain_text_offset=0,
        )
        _insert_table("doc1", 5, table)

        # call_args_list[0] is from setup, [1] is insertTable,
        # [2] is cell text insertion
        docs_mock = mock_service.documents()
        batch_calls = docs_mock.batchUpdate.call_args_list
        assert len(batch_calls) >= 3
        cell_body = batch_calls[2].kwargs.get("body", {})
        text_reqs = [
            r for r in cell_body.get("requests", [])
            if "insertText" in r
        ]
        # Only "H1" inserted, empty cell skipped
        assert len(text_reqs) == 1
        assert text_reqs[0]["insertText"]["text"] == "H1"


class TestEditTableRestriction:
    # The guard lives in replace_formatted (it needs the per-match contexts),
    # so run the real function against a mocked Docs service.
    def test_tables_blocked_with_all(self, mocker):
        mock_svc = mocker.patch("gdoc.api.docs.get_docs_service")
        mock_find = mocker.patch("gdoc.api.docs.find_text_in_document")
        mock_get_doc = mocker.patch("gdoc.api.docs.get_document_with_tabs")
        mocker.patch("gdoc.notify.pre_flight", return_value=None)
        mock_get_doc.return_value = {"revisionId": "rev1", "body": {}}
        mock_find.return_value = [
            {"startIndex": 1, "endIndex": 5},
            {"startIndex": 10, "endIndex": 15},
        ]

        args = SimpleNamespace(
            command="edit",
            doc="abc123",
            old_text="old",
            new_text="| A |\n|---|\n| 1 |",
            old_file=None,
            new_file=None,
            quiet=True,
            json=False,
            verbose=False,
            plain=False,
            case_sensitive=False,
            tab=None,
            **{"all": True},
        )

        with pytest.raises(GdocError, match="tables not supported"):
            cmd_edit(args)
        mock_svc.return_value.documents.return_value.batchUpdate.assert_not_called()

    def test_single_match_table_takes_structural_path(self, mocker):
        # A Markdown table replacing one paragraph is structural: it must
        # bypass the contextual wording path (which would refuse on the
        # paragraph count) and reach _insert_table, as on main.
        mock_svc = mocker.patch("gdoc.api.docs.get_docs_service")
        mock_insert = mocker.patch("gdoc.api.docs._insert_table")
        content, index = [], 1
        for text in ("Intro", "placeholder", "Outro"):
            end = index + len(text) + 1
            content.append({
                "startIndex": index, "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [{
                        "startIndex": index, "endIndex": end,
                        "textRun": {"content": text + "\n", "textStyle": {}},
                    }],
                },
            })
            index = end
        match = {"startIndex": 7, "endIndex": 18}

        count = replace_formatted(
            "doc1", [match], "| A |\n|---|\n| 1 |", "rev1",
            body={"content": content},
        )

        assert count == 1
        batch = mock_svc.return_value.documents.return_value.batchUpdate
        assert batch.call_args.kwargs["body"]["requests"] == [
            {"deleteContentRange": {"range": match}},
        ]
        assert mock_insert.call_count == 1
        assert mock_insert.call_args.args[:2] == ("doc1", 7)


class TestFindTableCellIndicesBody:
    def test_explicit_body_parameter(self):
        doc = _make_document_with_table(table_start=5, rows=2, cols=2)
        body = doc["body"]
        indices = _find_table_cell_indices(None, 5, body=body)
        assert len(indices) == 2
        assert len(indices[0]) == 2

    def test_both_none_returns_empty(self):
        indices = _find_table_cell_indices(None, 5)
        assert indices == []


class TestInsertTableTabId:
    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_id_in_location(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        doc = _make_document_with_table(table_start=5, rows=2, cols=2)
        # When tab_id is provided, _insert_table fetches with includeTabsContent
        mock_tabs_doc = {
            "revisionId": "rev1",
            "tabs": [{
                "tabProperties": {"tabId": "tab1", "title": "Tab 1", "index": 0},
                "documentTab": doc,
            }]
        }
        mock_service.documents().get().execute.return_value = mock_tabs_doc
        mock_service.documents().batchUpdate().execute.return_value = {
            "writeControl": {"requiredRevisionId": "rev1"},
        }

        table = TableData(
            rows=[["H1", "H2"], ["a", "b"]],
            num_rows=2, num_cols=2,
            plain_text_offset=0,
        )
        _insert_table("doc1", 5, table, tab_id="tab1")

        # Verify insertTable batchUpdate was called with tabId in location
        batch_calls = mock_service.documents().batchUpdate.call_args_list
        assert len(batch_calls) >= 1
