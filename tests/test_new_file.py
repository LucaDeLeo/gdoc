"""Tests for `gdoc new --file` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gdoc.cli import cmd_new
from gdoc.util import GdocError


API_RESULT = {
    "id": "new_doc_123",
    "name": "From File",
    "version": 1,
    "webViewLink": "https://docs.google.com/document/d/new_doc_123/edit",
}


def _make_args(**overrides):
    defaults = {
        "command": "new",
        "title": "From File",
        "folder": None,
        "file_path": None,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestNewFromFile:
    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_basic(self, mock_create, _update, tmp_path, capsys):
        md = tmp_path / "doc.md"
        md.write_text("# Hello\n")
        args = _make_args(file_path=str(md))
        rc = cmd_new(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "new_doc_123"
        mock_create.assert_called_once_with(
            "From File", "# Hello\n", folder_id=None,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_with_folder(self, mock_create, _update, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("text")
        args = _make_args(
            file_path=str(md), folder="folder_abc",
        )
        cmd_new(args)
        mock_create.assert_called_once_with(
            "From File", "text", folder_id="folder_abc",
        )

    def test_file_not_found(self):
        args = _make_args(file_path="/no/such/file.md")
        with pytest.raises(GdocError, match="file not found"):
            cmd_new(args)

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_json_output(self, mock_create, _update, tmp_path, capsys):
        md = tmp_path / "doc.md"
        md.write_text("content")
        args = _make_args(file_path=str(md), json=True)
        rc = cmd_new(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["id"] == "new_doc_123"

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_state_seeded(self, mock_create, mock_update, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("hi")
        args = _make_args(file_path=str(md))
        cmd_new(args)
        mock_update.assert_called_once_with(
            "new_doc_123", None, command="new",
            quiet=False, command_version=1,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_no_file_falls_through(self, _create_md, _update):
        """Without --file, cmd_new should use the regular create_doc."""
        with patch("gdoc.api.drive.create_doc", return_value=API_RESULT):
            args = _make_args()
            rc = cmd_new(args)
            assert rc == 0
            _create_md.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_image_extraction_error(
        self, mock_create, _update, tmp_path,
    ):
        md = tmp_path / "doc.md"
        md.write_text("![bad](../../etc/passwd.png)")
        args = _make_args(file_path=str(md))
        with pytest.raises(GdocError, match="path traversal"):
            cmd_new(args)


class TestNewFromFileWithImages:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.drive.delete_file")
    @patch("gdoc.api.drive.upload_temp_image")
    @patch("gdoc.api.docs.get_document")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_local_image_uploaded_and_cleaned(
        self,
        mock_create,
        mock_get_doc,
        mock_upload,
        mock_delete,
        mock_docs_svc,
        _update,
        tmp_path,
        capsys,
    ):
        # Create a local image
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")
        md = tmp_path / "doc.md"
        md.write_text("# Hi\n![photo](photo.png)\n")

        # Mock document with placeholder text
        mock_get_doc.return_value = {
            "body": {
                "content": [{
                    "paragraph": {
                        "elements": [{
                            "startIndex": 1,
                            "textRun": {
                                "content": "Hi\n<<IMG_0>>\n",
                            },
                        }],
                    },
                }],
            },
        }
        mock_upload.return_value = {
            "id": "temp123",
            "webContentLink": "https://drive.google.com/temp123",
        }
        mock_svc = MagicMock()
        mock_docs_svc.return_value = mock_svc
        mock_svc.documents().batchUpdate().execute.return_value = {}

        args = _make_args(file_path=str(md))
        rc = cmd_new(args)
        assert rc == 0

        # Verify temp image cleanup
        mock_delete.assert_called_once_with("temp123")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.docs.get_docs_service")
    @patch("gdoc.api.docs.get_document")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=API_RESULT,
    )
    def test_remote_image_no_upload(
        self,
        mock_create,
        mock_get_doc,
        mock_docs_svc,
        _update,
        tmp_path,
        capsys,
    ):
        md = tmp_path / "doc.md"
        md.write_text("![alt](https://example.com/img.png)\n")

        mock_get_doc.return_value = {
            "body": {
                "content": [{
                    "paragraph": {
                        "elements": [{
                            "startIndex": 1,
                            "textRun": {
                                "content": "<<IMG_0>>\n",
                            },
                        }],
                    },
                }],
            },
        }
        mock_svc = MagicMock()
        mock_docs_svc.return_value = mock_svc
        mock_svc.documents().batchUpdate().execute.return_value = {}

        args = _make_args(file_path=str(md))
        rc = cmd_new(args)
        assert rc == 0

        # Verify insertInlineImage uses remote URL directly
        batch_calls = (
            mock_svc.documents().batchUpdate.call_args_list
        )
        found_insert = False
        for c in batch_calls:
            body = c.kwargs.get("body", {})
            for req in body.get("requests", []):
                if "insertInlineImage" in req:
                    uri = req["insertInlineImage"]["uri"]
                    assert uri == "https://example.com/img.png"
                    found_insert = True
        assert found_insert
