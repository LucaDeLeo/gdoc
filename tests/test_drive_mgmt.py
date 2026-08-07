"""Tests for Drive file management: mkdir, mv, rename, drives, find --raw,
and the domain/anyone share extensions."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.drive import (
    create_folder,
    create_permission,
    list_shared_drives,
    move_file,
    rename_file,
)
from gdoc.cli import cmd_drives, cmd_find, cmd_mkdir, cmd_mv, cmd_rename, cmd_share
from gdoc.util import GdocError

FOLDER_MIME = "application/vnd.google-apps.folder"


def _http_error(status, content=b""):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Error"
    return HttpError(resp, content, uri="")


def _make_args(command, **overrides):
    defaults = {
        "command": command,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- API wrappers ---

class TestCreateFolderAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_create_basic(self, mock_svc):
        service = MagicMock()
        create = service.files.return_value.create
        create.return_value.execute.return_value = {
            "id": "folder1", "name": "Docs", "webViewLink": "https://f",
        }
        mock_svc.return_value = service

        result = create_folder("Docs")

        assert result["id"] == "folder1"
        body = create.call_args.kwargs["body"]
        assert body == {"name": "Docs", "mimeType": FOLDER_MIME}

    @patch("gdoc.api.drive.get_drive_service")
    def test_parent_in_body(self, mock_svc):
        service = MagicMock()
        create = service.files.return_value.create
        create.return_value.execute.return_value = {"id": "folder1"}
        mock_svc.return_value = service

        create_folder("Docs", parent_id="parent1")

        assert create.call_args.kwargs["body"]["parents"] == ["parent1"]


class TestMoveFileAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_replaces_all_parents(self, mock_svc):
        service = MagicMock()
        files = service.files.return_value
        files.get.return_value.execute.return_value = {
            "parents": ["old1", "old2"],
        }
        files.update.return_value.execute.return_value = {
            "id": "doc1", "name": "Doc", "parents": ["new1"], "version": "9",
        }
        mock_svc.return_value = service

        result = move_file("doc1", "new1")

        update_kwargs = files.update.call_args.kwargs
        assert update_kwargs["addParents"] == "new1"
        assert update_kwargs["removeParents"] == "old1,old2"
        assert result["parents"] == ["new1"]
        assert result["version"] == 9

    @patch("gdoc.api.drive.get_drive_service")
    def test_already_sole_parent_is_noop(self, mock_svc):
        service = MagicMock()
        files = service.files.return_value
        files.get.return_value.execute.return_value = {
            "id": "doc1", "name": "Doc", "parents": ["new1"], "version": "5",
        }
        mock_svc.return_value = service

        result = move_file("doc1", "new1")

        files.update.assert_not_called()
        assert result["parents"] == ["new1"]
        assert result["version"] == 5

    @patch("gdoc.api.drive.get_drive_service")
    def test_destination_kept_out_of_remove_parents(self, mock_svc):
        service = MagicMock()
        files = service.files.return_value
        files.get.return_value.execute.return_value = {
            "id": "doc1", "name": "Doc",
            "parents": ["new1", "old1"], "version": "5",
        }
        files.update.return_value.execute.return_value = {
            "id": "doc1", "name": "Doc", "parents": ["new1"], "version": "6",
        }
        mock_svc.return_value = service

        move_file("doc1", "new1")

        kwargs = files.update.call_args.kwargs
        assert "addParents" not in kwargs  # already a parent
        assert kwargs["removeParents"] == "old1"

    @patch("gdoc.api.drive.get_drive_service")
    def test_404_raises_gdoc_error(self, mock_svc):
        service = MagicMock()
        files = service.files.return_value
        files.get.return_value.execute.side_effect = _http_error(404)
        mock_svc.return_value = service

        with pytest.raises(GdocError, match="not found"):
            move_file("doc1", "new1")


class TestRenameFileAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_rename(self, mock_svc):
        service = MagicMock()
        update = service.files.return_value.update
        update.return_value.execute.return_value = {
            "id": "doc1", "name": "New name", "version": "12",
        }
        mock_svc.return_value = service

        result = rename_file("doc1", "New name")

        assert update.call_args.kwargs["body"] == {"name": "New name"}
        assert result["version"] == 12


class TestListSharedDrivesAPI:
    @patch("gdoc.api.drive.get_drive_service")
    def test_paginates(self, mock_svc):
        service = MagicMock()
        drives_list = service.drives.return_value.list
        drives_list.return_value.execute.side_effect = [
            {
                "drives": [{"id": "d1", "name": "Eng"}],
                "nextPageToken": "tok",
            },
            {"drives": [{"id": "d2", "name": "Ops"}]},
        ]
        mock_svc.return_value = service

        result = list_shared_drives()

        assert [d["id"] for d in result] == ["d1", "d2"]
        assert drives_list.call_count == 2
        assert drives_list.call_args_list[1].kwargs["pageToken"] == "tok"


class TestListFilesCorpora:
    def _service(self, response):
        service = MagicMock()
        files_list = service.files.return_value.list
        files_list.return_value.execute.return_value = response
        return service, files_list

    @patch("gdoc.api.drive.get_drive_service")
    def test_all_drives_sets_corpora(self, mock_svc):
        from gdoc.api.drive import list_files

        service, files_list = self._service({"files": []})
        mock_svc.return_value = service

        list_files("trashed=false", all_drives=True)

        assert files_list.call_args.kwargs["corpora"] == "allDrives"

    @patch("gdoc.api.drive.get_drive_service")
    def test_default_omits_corpora(self, mock_svc):
        from gdoc.api.drive import list_files

        service, files_list = self._service({"files": []})
        mock_svc.return_value = service

        list_files("trashed=false")

        assert "corpora" not in files_list.call_args.kwargs

    @patch("gdoc.api.drive.get_drive_service")
    def test_incomplete_search_warns(self, mock_svc, capsys):
        from gdoc.api.drive import list_files

        service, files_list = self._service(
            {"files": [], "incompleteSearch": True},
        )
        mock_svc.return_value = service

        list_files("trashed=false", all_drives=True)

        assert "incomplete search" in capsys.readouterr().err


class TestCreatePermissionTargets:
    @patch("gdoc.api.drive.get_drive_service")
    def test_domain_share_body(self, mock_svc):
        service = MagicMock()
        create = service.permissions.return_value.create
        create.return_value.execute.return_value = {"id": "perm1"}
        mock_svc.return_value = service

        create_permission("doc1", role="reader", domain="example.org")

        kwargs = create.call_args.kwargs
        assert kwargs["body"] == {
            "type": "domain",
            "role": "reader",
            "domain": "example.org",
            "allowFileDiscovery": False,
        }
        assert "sendNotificationEmail" not in kwargs

    @patch("gdoc.api.drive.get_drive_service")
    def test_anyone_discoverable_body(self, mock_svc):
        service = MagicMock()
        create = service.permissions.return_value.create
        create.return_value.execute.return_value = {"id": "perm1"}
        mock_svc.return_value = service

        create_permission("doc1", role="writer", anyone=True, discoverable=True)

        assert create.call_args.kwargs["body"] == {
            "type": "anyone",
            "role": "writer",
            "allowFileDiscovery": True,
        }

    def test_no_target_raises(self):
        with pytest.raises(GdocError, match="share target"):
            create_permission("doc1", role="reader")


# --- CLI handlers ---

class TestCmdMkdir:
    @patch("gdoc.api.drive.create_folder", return_value={
        "id": "folder1", "name": "Docs", "webViewLink": "https://f",
    })
    def test_terse_prints_id(self, mock_create, capsys):
        args = _make_args("mkdir", title="Docs", parent=None)
        rc = cmd_mkdir(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "folder1"
        mock_create.assert_called_once_with("Docs", parent_id=None)

    @patch("gdoc.api.drive.create_folder", return_value={"id": "folder1"})
    def test_parent_url_resolved(self, mock_create):
        args = _make_args(
            "mkdir", title="Docs",
            parent="https://drive.google.com/drive/folders/folder_xyz",
        )
        cmd_mkdir(args)
        mock_create.assert_called_once_with("Docs", parent_id="folder_xyz")

    @patch("gdoc.api.drive.create_folder", return_value={
        "id": "folder1", "name": "Docs", "webViewLink": "https://f",
    })
    def test_json_output(self, mock_create, capsys):
        args = _make_args("mkdir", title="Docs", parent=None, json=True)
        cmd_mkdir(args)
        data = json.loads(capsys.readouterr().out)
        assert data == {
            "ok": True, "id": "folder1", "name": "Docs", "url": "https://f",
        }


MV_RESULT = {"id": "doc1", "name": "Doc", "parents": ["folder1"], "version": 9}


class TestCmdMv:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.move_file", return_value=MV_RESULT)
    def test_terse_output(self, mock_move, _pf, mock_update, capsys):
        args = _make_args("mv", doc="doc1", folder="folder1")
        rc = cmd_mv(args)
        assert rc == 0
        assert "OK moved to folder1" in capsys.readouterr().out
        mock_move.assert_called_once_with("doc1", "folder1")
        assert mock_update.call_args.kwargs["command_version"] == 9
        assert mock_update.call_args.kwargs["metadata_only_write"] is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.move_file", return_value=MV_RESULT)
    def test_folder_url_resolved(self, mock_move, _pf, _update):
        args = _make_args(
            "mv", doc="doc1",
            folder="https://drive.google.com/drive/folders/folder_xyz",
        )
        cmd_mv(args)
        mock_move.assert_called_once_with("doc1", "folder_xyz")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.move_file", return_value=MV_RESULT)
    def test_json_output(self, mock_move, _pf, _update, capsys):
        args = _make_args("mv", doc="doc1", folder="folder1", json=True)
        cmd_mv(args)
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["parents"] == ["folder1"]


class TestCmdRename:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.rename_file", return_value={
        "id": "doc1", "name": "New name", "version": 12,
    })
    def test_terse_output(self, mock_rename, _pf, mock_update, capsys):
        args = _make_args("rename", doc="doc1", title="New name")
        rc = cmd_rename(args)
        assert rc == 0
        assert "OK renamed to New name" in capsys.readouterr().out
        mock_rename.assert_called_once_with("doc1", "New name")
        assert mock_update.call_args.kwargs["command_version"] == 12
        assert mock_update.call_args.kwargs["metadata_only_write"] is True

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.rename_file", return_value={
        "id": "doc1", "name": "New name",
    })
    def test_json_output(self, mock_rename, _pf, _update, capsys):
        args = _make_args("rename", doc="doc1", title="New name", json=True)
        cmd_rename(args)
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "doc1", "name": "New name"}


class TestCmdDrives:
    @patch("gdoc.api.drive.list_shared_drives", return_value=[
        {"id": "d1", "name": "Eng"}, {"id": "d2", "name": "Ops"},
    ])
    def test_terse_output(self, mock_list, capsys):
        args = _make_args("drives")
        rc = cmd_drives(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "d1  Eng" in out
        assert "d2  Ops" in out

    @patch("gdoc.api.drive.list_shared_drives", return_value=[])
    def test_empty(self, mock_list, capsys):
        args = _make_args("drives")
        cmd_drives(args)
        assert "No shared drives." in capsys.readouterr().out

    @patch("gdoc.api.drive.list_shared_drives", return_value=[
        {"id": "d1", "name": "Eng"},
    ])
    def test_json_output(self, mock_list, capsys):
        args = _make_args("drives", json=True)
        cmd_drives(args)
        data = json.loads(capsys.readouterr().out)
        assert data["drives"] == [{"id": "d1", "name": "Eng"}]

    @patch("gdoc.api.drive.list_shared_drives", return_value=[
        {"id": "d1", "name": "Eng"},
    ])
    def test_plain_output(self, mock_list, capsys):
        args = _make_args("drives", plain=True)
        cmd_drives(args)
        assert capsys.readouterr().out.strip() == "d1\tEng"


class TestCmdFindRaw:
    RAW_QUERY = "mimeType='application/vnd.google-apps.document' and 'me' in owners"

    @patch("gdoc.api.drive.list_files", return_value=[
        {"id": "doc1", "name": "Doc", "modifiedTime": "2026-08-07T00:00:00Z"},
    ])
    def test_raw_query_passed_verbatim_across_all_drives(
        self, mock_list, capsys,
    ):
        args = _make_args("find", query=self.RAW_QUERY, raw=True, title=False)
        rc = cmd_find(args)
        assert rc == 0
        mock_list.assert_called_once_with(self.RAW_QUERY, all_drives=True)
        assert "doc1" in capsys.readouterr().out

    def test_raw_with_title_rejected(self):
        args = _make_args("find", query="x", raw=True, title=True)
        with pytest.raises(GdocError, match="mutually exclusive") as exc_info:
            cmd_find(args)
        assert exc_info.value.exit_code == 3

    @patch("gdoc.api.drive.search_files", return_value=[])
    def test_without_raw_uses_search(self, mock_search, capsys):
        args = _make_args("find", query="report", raw=False, title=False)
        cmd_find(args)
        mock_search.assert_called_once_with("report", title_only=False)


class TestCmdShareTargets:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_domain_share(self, mock_perm, _pf, _update, capsys):
        args = _make_args(
            "share", doc="doc1", email=None, domain="example.org",
            anyone=False, role="reader", discoverable=False,
        )
        rc = cmd_share(args)
        assert rc == 0
        mock_perm.assert_called_once_with(
            "doc1", email=None, role="reader",
            domain="example.org", anyone=False, discoverable=False,
        )
        assert (
            "OK shared with example.org as reader"
            in capsys.readouterr().out
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_anyone_share_discoverable(self, mock_perm, _pf, _update, capsys):
        args = _make_args(
            "share", doc="doc1", email=None, domain=None,
            anyone=True, role="reader", discoverable=True,
        )
        rc = cmd_share(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK shared with anyone with the link as reader" in out
        assert "(discoverable)" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_domain_json_output(self, mock_perm, _pf, _update, capsys):
        args = _make_args(
            "share", doc="doc1", email=None, domain="example.org",
            anyone=False, role="writer", discoverable=False, json=True,
        )
        cmd_share(args)
        data = json.loads(capsys.readouterr().out)
        assert data == {
            "ok": True, "type": "domain", "target": "example.org",
            "role": "writer", "status": "shared", "discoverable": False,
        }

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_user_json_schema_unchanged(self, mock_perm, _pf, _update, capsys):
        # Pre-0.16 consumers destructure exactly {ok,email,role,status}.
        args = _make_args(
            "share", doc="doc1", email="a@b.com", domain=None,
            anyone=False, role="reader", discoverable=False, json=True,
        )
        cmd_share(args)
        data = json.loads(capsys.readouterr().out)
        assert data == {
            "ok": True, "email": "a@b.com",
            "role": "reader", "status": "shared",
        }

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.create_permission", return_value={"id": "perm1"})
    def test_domain_plain_reports_discoverable(
        self, mock_perm, _pf, _update, capsys,
    ):
        args = _make_args(
            "share", doc="doc1", email=None, domain="example.org",
            anyone=False, role="reader", discoverable=True, plain=True,
        )
        cmd_share(args)
        lines = capsys.readouterr().out.splitlines()
        assert "target\texample.org" in lines
        assert "type\tdomain" in lines
        assert "role\treader" in lines
        assert "discoverable\ttrue" in lines

    def test_no_target_rejected(self):
        args = _make_args(
            "share", doc="doc1", email=None, domain=None,
            anyone=False, role="reader",
        )
        with pytest.raises(GdocError, match="exactly one") as exc_info:
            cmd_share(args)
        assert exc_info.value.exit_code == 3

    def test_two_targets_rejected(self):
        args = _make_args(
            "share", doc="doc1", email="a@b.com", domain="example.org",
            anyone=False, role="reader",
        )
        with pytest.raises(GdocError, match="exactly one") as exc_info:
            cmd_share(args)
        assert exc_info.value.exit_code == 3

    def test_discoverable_with_email_rejected(self):
        args = _make_args(
            "share", doc="doc1", email="a@b.com", domain=None,
            anyone=False, role="reader", discoverable=True,
        )
        with pytest.raises(GdocError, match="--discoverable") as exc_info:
            cmd_share(args)
        assert exc_info.value.exit_code == 3
