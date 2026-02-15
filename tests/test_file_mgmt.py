"""Tests for file management CLI commands: new, cp, share."""

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call

import pytest

from gdoc.cli import cmd_new, cmd_cp, cmd_share
from gdoc.notify import ChangeInfo
from gdoc.util import AuthError, GdocError


def _make_args(command, **overrides):
    """Build a SimpleNamespace mimicking parsed args."""
    defaults = {
        "command": command,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


API_RESULT_NEW = {
    "id": "new_doc_123",
    "name": "Test Doc",
    "version": 1,
    "webViewLink": "https://docs.google.com/document/d/new_doc_123/edit",
}

API_RESULT_COPY = {
    "id": "copy_doc_456",
    "name": "Copy Title",
    "version": 3,
    "webViewLink": "https://docs.google.com/document/d/copy_doc_456/edit",
}


# --- cmd_new tests ---

class TestCmdNew:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_terse_output(self, mock_create, _update, capsys):
        args = _make_args("new", title="Test Doc")
        rc = cmd_new(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "new_doc_123"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_json_output(self, mock_create, _update, capsys):
        args = _make_args("new", title="Test Doc", json=True)
        rc = cmd_new(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["id"] == "new_doc_123"
        assert data["title"] == "Test Doc"
        assert data["url"] == "https://docs.google.com/document/d/new_doc_123/edit"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_verbose_output(self, mock_create, _update, capsys):
        args = _make_args("new", title="Test Doc", verbose=True)
        rc = cmd_new(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Created: Test Doc" in out
        assert "ID: new_doc_123" in out
        assert "URL: https://docs.google.com/document/d/new_doc_123/edit" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_with_folder(self, mock_create, _update):
        args = _make_args("new", title="Test Doc", folder="folder_abc")
        cmd_new(args)
        mock_create.assert_called_once_with("Test Doc", folder_id="folder_abc")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_folder_url_resolved(self, mock_create, _update):
        folder_url = "https://drive.google.com/drive/folders/folder_xyz"
        args = _make_args("new", title="Test Doc", folder=folder_url)
        cmd_new(args)
        mock_create.assert_called_once_with("Test Doc", folder_id="folder_xyz")

    def test_new_invalid_folder_url(self):
        args = _make_args("new", title="Test Doc", folder="not-a-valid-url://bad")
        with pytest.raises(GdocError) as exc_info:
            cmd_new(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", side_effect=GdocError("API error"))
    def test_new_api_error(self, mock_create, _update):
        args = _make_args("new", title="Test Doc")
        with pytest.raises(GdocError, match="API error"):
            cmd_new(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", side_effect=AuthError("Authentication expired"))
    def test_new_auth_error(self, mock_create, _update):
        args = _make_args("new", title="Test Doc")
        with pytest.raises(AuthError):
            cmd_new(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_state_seeded(self, mock_create, mock_update):
        args = _make_args("new", title="Test Doc")
        cmd_new(args)
        mock_update.assert_called_once_with(
            "new_doc_123", None, command="new",
            quiet=False, command_version=1,
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    @patch("gdoc.notify.pre_flight")
    def test_new_no_preflight(self, mock_pf, mock_create, _update):
        args = _make_args("new", title="Test Doc")
        cmd_new(args)
        mock_pf.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_no_folder(self, mock_create, _update):
        args = _make_args("new", title="Test Doc")
        cmd_new(args)
        mock_create.assert_called_once_with("Test Doc", folder_id=None)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc")
    def test_new_state_version_cast(self, mock_create, mock_update):
        mock_create.return_value = {**API_RESULT_NEW, "version": 5}
        args = _make_args("new", title="Test Doc")
        cmd_new(args)
        call_kwargs = mock_update.call_args
        assert call_kwargs[1]["command_version"] == 5
        assert isinstance(call_kwargs[1]["command_version"], int)


# --- cmd_cp tests ---

class TestCmdCp:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_terse_output(self, mock_copy, _pf, _update, capsys):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        rc = cmd_cp(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "copy_doc_456"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_json_output(self, mock_copy, _pf, _update, capsys):
        args = _make_args("cp", doc="src_doc", title="Copy Title", json=True, quiet=True)
        rc = cmd_cp(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["id"] == "copy_doc_456"
        assert data["title"] == "Copy Title"
        assert data["url"] == "https://docs.google.com/document/d/copy_doc_456/edit"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_verbose_output(self, mock_copy, _pf, _update, capsys):
        args = _make_args("cp", doc="src_doc", title="Copy Title", verbose=True, quiet=True)
        rc = cmd_cp(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Copied: Copy Title" in out
        assert "ID: copy_doc_456" in out
        assert "URL: https://docs.google.com/document/d/copy_doc_456/edit" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_preflight_called(self, mock_copy, mock_pf, _update):
        args = _make_args("cp", doc="src_doc", title="Copy Title")
        cmd_cp(args)
        mock_pf.assert_called_once_with("src_doc", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_source_state_updated(self, mock_copy, mock_pf, mock_update):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        cmd_cp(args)
        # First call: source doc state update
        first_call = mock_update.call_args_list[0]
        assert first_call[0][0] == "src_doc"
        assert first_call[1]["command"] == "cp"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_copy_state_seeded(self, mock_copy, mock_pf, mock_update):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        cmd_cp(args)
        # Second call: new copy state seed
        assert mock_update.call_count == 2
        second_call = mock_update.call_args_list[1]
        assert second_call[0][0] == "copy_doc_456"
        assert second_call[0][1] is None  # no change_info for new doc
        assert second_call[1]["command_version"] == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", side_effect=GdocError("Document not found: src_doc"))
    def test_cp_api_error(self, mock_copy, _pf, _update):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        with pytest.raises(GdocError, match="Document not found"):
            cmd_cp(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", side_effect=AuthError("Authentication expired"))
    def test_cp_auth_error(self, mock_copy, _pf, _update):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        with pytest.raises(AuthError):
            cmd_cp(args)

    def test_cp_invalid_doc_id(self):
        args = _make_args("cp", doc="not-a-valid-url://bad", title="Copy Title", quiet=True)
        with pytest.raises(GdocError) as exc_info:
            cmd_cp(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_quiet_skips_preflight_banner(self, mock_copy, mock_pf, _update):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        cmd_cp(args)
        mock_pf.assert_called_once_with("src_doc", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_preflight_on_source_not_copy(self, mock_copy, mock_pf, _update):
        args = _make_args("cp", doc="src_doc", title="Copy Title", quiet=True)
        cmd_cp(args)
        # pre_flight is called with source doc, not the copy
        mock_pf.assert_called_once_with("src_doc", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_title_passed_to_api(self, mock_copy, _pf, _update):
        args = _make_args("cp", doc="src_doc", title="My Copy", quiet=True)
        cmd_cp(args)
        mock_copy.assert_called_once_with("src_doc", "My Copy")


# --- cmd_share tests ---

class TestCmdShare:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_terse_output(self, mock_perm, _pf, _update, capsys):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="reader", quiet=True)
        rc = cmd_share(args)
        assert rc == 0
        assert "OK shared with alice@co.com as reader" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_json_output(self, mock_perm, _pf, _update, capsys):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="writer", json=True, quiet=True)
        rc = cmd_share(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "email": "alice@co.com", "role": "writer", "status": "shared"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_default_role_reader(self, mock_perm, _pf, _update, capsys):
        args = _make_args("share", doc="abc123", email="alice@co.com")
        cmd_share(args)
        mock_perm.assert_called_once_with("abc123", "alice@co.com", "reader")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_writer_role(self, mock_perm, _pf, _update):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="writer", quiet=True)
        cmd_share(args)
        mock_perm.assert_called_once_with("abc123", "alice@co.com", "writer")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_commenter_role(self, mock_perm, _pf, _update):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="commenter", quiet=True)
        cmd_share(args)
        mock_perm.assert_called_once_with("abc123", "alice@co.com", "commenter")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_preflight_called(self, mock_perm, mock_pf, _update):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="reader")
        cmd_share(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_state_updated(self, mock_perm, _pf, mock_update):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="reader", quiet=True)
        cmd_share(args)
        mock_update.assert_called_once_with("abc123", None, command="share", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", side_effect=GdocError("Permission denied: abc123"))
    def test_share_api_error(self, mock_perm, _pf, _update):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="reader", quiet=True)
        with pytest.raises(GdocError, match="Permission denied"):
            cmd_share(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", side_effect=AuthError("Authentication expired"))
    def test_share_auth_error(self, mock_perm, _pf, _update):
        args = _make_args("share", doc="abc123", email="alice@co.com", role="reader", quiet=True)
        with pytest.raises(AuthError):
            cmd_share(args)

    def test_share_invalid_doc_id(self):
        args = _make_args("share", doc="not-a-valid-url://bad", email="alice@co.com", role="reader", quiet=True)
        with pytest.raises(GdocError) as exc_info:
            cmd_share(args)
        assert exc_info.value.exit_code == 3


# --- API wrapper tests ---

class TestCreateDocAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_create_basic(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "new1", "name": "My Doc", "version": "1",
            "webViewLink": "https://docs.google.com/document/d/new1/edit",
        }

        from gdoc.api.drive import create_doc
        result = create_doc("My Doc")
        assert result["id"] == "new1"
        assert result["version"] == 1  # cast to int

    @patch("gdoc.api.drive.get_drive_service")
    def test_create_with_folder(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "new1", "name": "My Doc", "version": "1",
            "webViewLink": "https://docs.google.com/document/d/new1/edit",
        }

        from gdoc.api.drive import create_doc
        create_doc("My Doc", folder_id="folder_abc")

        call_kwargs = mock_service.files().create.call_args
        body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
        assert body["parents"] == ["folder_abc"]

    @patch("gdoc.api.drive.get_drive_service")
    def test_create_no_folder(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "new1", "name": "My Doc", "version": "1",
            "webViewLink": "https://docs.google.com/document/d/new1/edit",
        }

        from gdoc.api.drive import create_doc
        create_doc("My Doc")

        call_kwargs = mock_service.files().create.call_args
        body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
        assert "parents" not in body

    @patch("gdoc.api.drive.get_drive_service")
    def test_create_sets_mime_type(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().create().execute.return_value = {
            "id": "new1", "name": "My Doc", "version": "1",
            "webViewLink": "https://docs.google.com/document/d/new1/edit",
        }

        from gdoc.api.drive import create_doc
        create_doc("My Doc")

        call_kwargs = mock_service.files().create.call_args
        body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
        assert body["mimeType"] == "application/vnd.google-apps.document"

    @patch("gdoc.api.drive.get_drive_service")
    def test_create_404_error(self, mock_get_service):
        from gdoc.api.drive import create_doc
        import httplib2
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        resp = httplib2.Response({"status": "404"})
        error = HttpError(resp, b"")
        error.reason = "Not Found"
        mock_service.files().create().execute.side_effect = error

        with pytest.raises(GdocError, match="Document not found"):
            create_doc("My Doc", folder_id="bad_folder")

    @patch("gdoc.api.drive.get_drive_service")
    def test_create_401_error(self, mock_get_service):
        from gdoc.api.drive import create_doc
        import httplib2
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        resp = httplib2.Response({"status": "401"})
        error = HttpError(resp, b"")
        error.reason = "Unauthorized"
        mock_service.files().create().execute.side_effect = error

        with pytest.raises(AuthError, match="Authentication expired"):
            create_doc("My Doc")


class TestCopyDocAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_copy_basic(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().copy().execute.return_value = {
            "id": "copy1", "name": "Copy", "version": "5",
            "webViewLink": "https://docs.google.com/document/d/copy1/edit",
        }

        from gdoc.api.drive import copy_doc
        result = copy_doc("src_doc", "Copy")
        assert result["id"] == "copy1"
        assert result["version"] == 5  # cast to int

    @patch("gdoc.api.drive.get_drive_service")
    def test_copy_passes_file_id(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().copy().execute.return_value = {
            "id": "copy1", "name": "Copy", "version": "5",
            "webViewLink": "https://docs.google.com/document/d/copy1/edit",
        }

        from gdoc.api.drive import copy_doc
        copy_doc("src_doc", "Copy")

        call_kwargs = mock_service.files().copy.call_args
        assert call_kwargs.kwargs.get("fileId", call_kwargs[1].get("fileId")) == "src_doc"

    @patch("gdoc.api.drive.get_drive_service")
    def test_copy_passes_title(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.files().copy().execute.return_value = {
            "id": "copy1", "name": "My Copy", "version": "5",
            "webViewLink": "https://docs.google.com/document/d/copy1/edit",
        }

        from gdoc.api.drive import copy_doc
        copy_doc("src_doc", "My Copy")

        call_kwargs = mock_service.files().copy.call_args
        body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
        assert body["name"] == "My Copy"

    @patch("gdoc.api.drive.get_drive_service")
    def test_copy_404_error(self, mock_get_service):
        from gdoc.api.drive import copy_doc
        import httplib2
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        resp = httplib2.Response({"status": "404"})
        error = HttpError(resp, b"")
        error.reason = "Not Found"
        mock_service.files().copy().execute.side_effect = error

        with pytest.raises(GdocError, match="Document not found: src_doc"):
            copy_doc("src_doc", "Copy")


class TestCreatePermissionAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_permission_basic(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.permissions().create().execute.return_value = {"id": "perm1"}

        from gdoc.api.drive import create_permission
        result = create_permission("doc1", "alice@co.com", "reader")
        assert result["id"] == "perm1"

    @patch("gdoc.api.drive.get_drive_service")
    def test_permission_passes_body(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.permissions().create().execute.return_value = {"id": "perm1"}

        from gdoc.api.drive import create_permission
        create_permission("doc1", "alice@co.com", "writer")

        call_kwargs = mock_service.permissions().create.call_args
        body = call_kwargs.kwargs.get("body", call_kwargs[1].get("body"))
        assert body == {
            "type": "user",
            "role": "writer",
            "emailAddress": "alice@co.com",
        }

    @patch("gdoc.api.drive.get_drive_service")
    def test_permission_sends_notification(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.permissions().create().execute.return_value = {"id": "perm1"}

        from gdoc.api.drive import create_permission
        create_permission("doc1", "alice@co.com", "reader")

        call_kwargs = mock_service.permissions().create.call_args
        assert call_kwargs.kwargs.get(
            "sendNotificationEmail",
            call_kwargs[1].get("sendNotificationEmail"),
        ) is True

    @patch("gdoc.api.drive.get_drive_service")
    def test_permission_404_error(self, mock_get_service):
        from gdoc.api.drive import create_permission
        import httplib2
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        resp = httplib2.Response({"status": "404"})
        error = HttpError(resp, b"")
        error.reason = "Not Found"
        mock_service.permissions().create().execute.side_effect = error

        with pytest.raises(GdocError, match="Document not found: doc1"):
            create_permission("doc1", "alice@co.com", "reader")

    @patch("gdoc.api.drive.get_drive_service")
    def test_permission_403_error(self, mock_get_service):
        from gdoc.api.drive import create_permission
        import httplib2
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        resp = httplib2.Response({"status": "403"})
        error = HttpError(resp, b"")
        error.reason = "forbidden"
        mock_service.permissions().create().execute.side_effect = error

        with pytest.raises(GdocError, match="Permission denied: doc1"):
            create_permission("doc1", "alice@co.com", "writer")

    @patch("gdoc.api.drive.get_drive_service")
    def test_permission_401_error(self, mock_get_service):
        from gdoc.api.drive import create_permission
        import httplib2
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        resp = httplib2.Response({"status": "401"})
        error = HttpError(resp, b"")
        error.reason = "Unauthorized"
        mock_service.permissions().create().execute.side_effect = error

        with pytest.raises(AuthError, match="Authentication expired"):
            create_permission("doc1", "alice@co.com", "reader")


# --- Plain output tests ---

class TestPlainOutput:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.create_doc", return_value=API_RESULT_NEW)
    def test_new_plain_output(self, mock_create, _update, capsys):
        args = _make_args("new", title="Test Doc", plain=True)
        rc = cmd_new(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "id\tnew_doc_123"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.copy_doc", return_value=API_RESULT_COPY)
    def test_cp_plain_output(self, mock_copy, _pf, _update, capsys):
        args = _make_args(
            "cp", doc="src_doc", title="Copy Title", quiet=True, plain=True,
        )
        rc = cmd_cp(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "id\tcopy_doc_456"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_share_plain_output(self, mock_perm, _pf, _update, capsys):
        args = _make_args(
            "share", doc="abc123", email="alice@co.com",
            role="writer", quiet=True, plain=True,
        )
        rc = cmd_share(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "email\talice@co.com" in out
        assert "role\twriter" in out
