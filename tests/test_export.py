"""Tests for `gdoc export` and the export_doc_bytes API wrapper."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.drive import export_doc_bytes
from gdoc.cli import cmd_export
from gdoc.util import GdocError

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document"
)


def _http_error(status, content=b""):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Error"
    return HttpError(resp, content, uri="")


def _make_args(**overrides):
    defaults = {
        "command": "export",
        "doc": "doc123",
        "format": None,
        "out": None,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestExportDocBytes:
    @patch("gdoc.api.drive.get_drive_service")
    def test_returns_raw_bytes(self, mock_svc):
        service = MagicMock()
        export_media = service.files.return_value.export_media
        export_media.return_value.execute.return_value = b"%PDF-1.7 raw\xff"
        mock_svc.return_value = service

        result = export_doc_bytes("doc123", PDF_MIME)

        assert result == b"%PDF-1.7 raw\xff"
        export_media.assert_called_once_with(
            fileId="doc123", mimeType=PDF_MIME,
        )

    @patch("gdoc.api.drive.get_drive_service")
    def test_404_raises_gdoc_error(self, mock_svc):
        service = MagicMock()
        export_media = service.files.return_value.export_media
        export_media.return_value.execute.side_effect = _http_error(404)
        mock_svc.return_value = service

        with pytest.raises(GdocError, match="not found"):
            export_doc_bytes("doc123", PDF_MIME)


class TestCmdExport:
    def test_binary_format_without_out_errors(self):
        args = _make_args(format="pdf")
        with pytest.raises(GdocError, match="--out is required") as exc_info:
            cmd_export(args)
        assert exc_info.value.exit_code == 3

    def test_no_format_no_out_errors(self):
        args = _make_args()
        with pytest.raises(GdocError, match="cannot infer format") as exc_info:
            cmd_export(args)
        assert exc_info.value.exit_code == 3

    def test_unknown_out_extension_errors(self, tmp_path):
        args = _make_args(out=str(tmp_path / "report.xyz"))
        with pytest.raises(GdocError, match="cannot infer format") as exc_info:
            cmd_export(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"%PDF-fake")
    def test_format_inferred_from_out_extension(
        self, mock_export, _update, tmp_path, capsys,
    ):
        out = tmp_path / "report.pdf"
        args = _make_args(out=str(out))
        rc = cmd_export(args)
        assert rc == 0
        mock_export.assert_called_once_with("doc123", PDF_MIME)
        assert out.read_bytes() == b"%PDF-fake"
        assert (
            f"OK exported {out} (pdf, 9 bytes)"
            in capsys.readouterr().out
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"docx-bytes")
    def test_docx_mime_passed(self, mock_export, _update, tmp_path):
        args = _make_args(format="docx", out=str(tmp_path / "r.docx"))
        cmd_export(args)
        mock_export.assert_called_once_with("doc123", DOCX_MIME)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"# Title\n")
    def test_text_format_prints_to_stdout(self, mock_export, _update, capsys):
        args = _make_args(format="md")
        rc = cmd_export(args)
        assert rc == 0
        assert capsys.readouterr().out == "# Title\n"
        mock_export.assert_called_once_with("doc123", "text/markdown")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"<html></html>")
    def test_html_to_stdout_allowed(self, mock_export, _update, capsys):
        args = _make_args(format="html")
        rc = cmd_export(args)
        assert rc == 0
        assert capsys.readouterr().out == "<html></html>"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"12345")
    def test_json_output(self, mock_export, _update, tmp_path, capsys):
        out = tmp_path / "r.pdf"
        args = _make_args(out=str(out), json=True)
        cmd_export(args)
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["path"] == str(out)
        assert data["format"] == "pdf"
        assert data["bytes"] == 5

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"12345")
    def test_plain_output(self, mock_export, _update, tmp_path, capsys):
        out = tmp_path / "r.pdf"
        args = _make_args(out=str(out), plain=True)
        cmd_export(args)
        lines = capsys.readouterr().out.splitlines()
        assert f"path\t{out}" in lines
        assert "format\tpdf" in lines
        assert "bytes\t5" in lines

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"12345")
    def test_verbose_output(self, mock_export, _update, tmp_path, capsys):
        out = tmp_path / "r.pdf"
        args = _make_args(out=str(out), verbose=True)
        cmd_export(args)
        lines = capsys.readouterr().out.splitlines()
        assert "Exported: doc123" in lines
        assert "Format: pdf" in lines
        assert f"Path: {out}" in lines
        assert "Bytes: 5" in lines

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"# Title\n")
    def test_stdout_json_wraps_content(self, mock_export, _update, capsys):
        args = _make_args(format="md", json=True)
        rc = cmd_export(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["format"] == "md"
        assert data["bytes"] == 8
        assert data["content"] == "# Title\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"x")
    def test_unwritable_out_errors(self, mock_export, _update, tmp_path):
        out = tmp_path / "missing-dir" / "r.pdf"
        args = _make_args(out=str(out))
        with pytest.raises(GdocError, match="cannot write") as exc_info:
            cmd_export(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.export_doc_bytes", return_value=b"x")
    def test_state_updated(self, mock_export, mock_update, tmp_path):
        args = _make_args(out=str(tmp_path / "r.pdf"))
        cmd_export(args)
        assert mock_update.call_args.kwargs["command"] == "export"
