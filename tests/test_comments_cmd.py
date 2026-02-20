"""Tests for comment CRUD CLI commands."""

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from gdoc.cli import (
    cmd_comments, cmd_comment, cmd_reply, cmd_resolve, cmd_reopen,
    cmd_delete_comment, cmd_comment_info,
)
from gdoc.notify import ChangeInfo
from gdoc.util import AuthError, GdocError


def _make_args(command, **overrides):
    """Build a SimpleNamespace mimicking parsed args."""
    defaults = {
        "command": command,
        "doc": "abc123",
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_comment(cid="c1", content="test comment", email="alice@co.com",
                  resolved=False, created="2025-06-15T10:00:00Z", replies=None):
    """Build a comment dict matching API shape."""
    return {
        "id": cid,
        "content": content,
        "author": {"displayName": "Alice", "emailAddress": email},
        "resolved": resolved,
        "createdTime": created,
        "modifiedTime": created,
        "replies": replies or [],
    }


# --- cmd_comment tests ---

_MOCK_VERSION = {"version": 50}


class TestCmdComment:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_ok_output(self, mock_create, _svc, _ver, _pf, _update, capsys):
        args = _make_args("comment", text="hello", quiet=True)
        rc = cmd_comment(args)
        assert rc == 0
        assert "OK comment #c_new" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_json_output(self, mock_create, _svc, _ver, _pf, _update, capsys):
        args = _make_args("comment", text="hello", json=True, quiet=True)
        rc = cmd_comment(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c_new", "status": "created"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", side_effect=GdocError("Document not found: abc123"))
    def test_comment_api_error(self, mock_create, _svc, _ver, _pf, _update):
        args = _make_args("comment", text="hello", quiet=True)
        with pytest.raises(GdocError, match="Document not found"):
            cmd_comment(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", side_effect=AuthError("Authentication expired"))
    def test_comment_auth_error(self, mock_create, _svc, _ver, _pf, _update):
        args = _make_args("comment", text="hello", quiet=True)
        with pytest.raises(AuthError):
            cmd_comment(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_state_patch(self, mock_create, _svc, _ver, _pf, mock_update):
        args = _make_args("comment", text="hello", quiet=True)
        cmd_comment(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"add_comment_id": "c_new"}
        assert call_kwargs[1].get("command_version") == 50


# --- cmd_reply tests ---

class TestCmdReply:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_ok_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("reply", comment_id="c1", text="thanks", quiet=True)
        rc = cmd_reply(args)
        assert rc == 0
        assert "OK reply on #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_json_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("reply", comment_id="c1", text="thanks", json=True, quiet=True)
        rc = cmd_reply(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "commentId": "c1", "replyId": "r1", "status": "created"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_state_patch_adds_comment_id(self, mock_reply, _svc, _ver, _pf, mock_update):
        args = _make_args("reply", comment_id="c1", text="thanks", quiet=True)
        cmd_reply(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"add_comment_id": "c1"}
        assert call_kwargs[1].get("command_version") == 50


# --- cmd_resolve tests ---

class TestCmdResolve:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_ok_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("resolve", comment_id="c1", quiet=True)
        rc = cmd_resolve(args)
        assert rc == 0
        assert "OK resolved comment #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_json_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("resolve", comment_id="c1", json=True, quiet=True)
        rc = cmd_resolve(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c1", "status": "resolved"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_state_patch(self, mock_reply, _svc, _ver, _pf, mock_update):
        args = _make_args("resolve", comment_id="c1", quiet=True)
        cmd_resolve(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"add_comment_id": "c1", "add_resolved_id": "c1"}
        assert call_kwargs[1].get("command_version") == 50


# --- cmd_reopen tests ---

class TestCmdReopen:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_ok_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("reopen", comment_id="c1", quiet=True)
        rc = cmd_reopen(args)
        assert rc == 0
        assert "OK reopened comment #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_json_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("reopen", comment_id="c1", json=True, quiet=True)
        rc = cmd_reopen(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c1", "status": "reopened"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_state_patch(self, mock_reply, _svc, _ver, _pf, mock_update):
        args = _make_args("reopen", comment_id="c1", quiet=True)
        cmd_reopen(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"add_comment_id": "c1", "remove_resolved_id": "c1"}
        assert call_kwargs[1].get("command_version") == 50


# --- cmd_comments (list) tests ---

class TestCmdComments:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_default_calls_include_resolved_false(
        self, mock_list, _svc, _pf, _update
    ):
        mock_list.return_value = []
        args = _make_args("comments", quiet=True)
        cmd_comments(args)
        mock_list.assert_called_once_with("abc123", include_resolved=False, include_anchor=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_all_calls_include_resolved_true(
        self, mock_list, _svc, _pf, _update
    ):
        mock_list.return_value = []
        args = _make_args("comments", quiet=True, **{"all": True})
        cmd_comments(args)
        mock_list.assert_called_once_with("abc123", include_resolved=True, include_anchor=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_quiet_no_preflight(self, mock_list, _svc, mock_pf, _update):
        mock_list.return_value = []
        args = _make_args("comments", quiet=True)
        cmd_comments(args)
        mock_pf.assert_called_once_with("abc123", quiet=True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight")
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_nonquiet_separate_api_call(
        self, mock_list, _svc, mock_pf, _update
    ):
        """Both pre_flight AND list_comments are called (separate paths)."""
        mock_pf.return_value = ChangeInfo()
        mock_list.return_value = []
        args = _make_args("comments")
        cmd_comments(args)
        mock_pf.assert_called_once_with("abc123", quiet=False)
        mock_list.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_terse_format(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = [
            _make_comment(
                cid="c1", content="Fix typo", email="alice@co.com",
                replies=[
                    {"author": {"emailAddress": "bob@co.com"}, "content": "Done"},
                ],
            ),
        ]
        args = _make_args("comments", quiet=True)
        cmd_comments(args)
        out = capsys.readouterr().out
        assert "#c1 [open] alice@co.com 2025-06-15" in out
        assert '"Fix typo"' in out
        assert '-> bob@co.com: "Done"' in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_action_only_replies_hidden(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = [
            _make_comment(
                cid="c1", content="Fix typo",
                replies=[
                    {"author": {"emailAddress": "bob@co.com"}, "content": "", "action": "resolve"},
                ],
            ),
        ]
        args = _make_args("comments", quiet=True)
        cmd_comments(args)
        out = capsys.readouterr().out
        assert "->" not in out  # action-only reply not shown

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_json_format(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = [_make_comment()]
        args = _make_args("comments", json=True, quiet=True)
        cmd_comments(args)
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert len(data["comments"]) == 1

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_empty(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = []
        args = _make_args("comments", quiet=True)
        rc = cmd_comments(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "No comments."

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_empty_json(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = []
        args = _make_args("comments", json=True, quiet=True)
        rc = cmd_comments(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "comments": []}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_resolved_shown_with_flag(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = [
            _make_comment(cid="c1", content="Done", resolved=True),
        ]
        args = _make_args("comments", quiet=True, **{"all": True})
        cmd_comments(args)
        out = capsys.readouterr().out
        assert "[resolved]" in out


# --- cmd_delete_comment tests ---

class TestCmdDeleteComment:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.delete_comment", return_value=None)
    def test_delete_comment_ok_output(self, mock_delete, _svc, _ver, _pf, _update, capsys):
        args = _make_args("delete-comment", comment_id="c1", quiet=True, force=True)
        rc = cmd_delete_comment(args)
        assert rc == 0
        assert "OK deleted comment #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.delete_comment", return_value=None)
    def test_delete_comment_json_output(self, mock_delete, _svc, _ver, _pf, _update, capsys):
        args = _make_args("delete-comment", comment_id="c1", json=True, quiet=True, force=True)
        rc = cmd_delete_comment(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c1", "status": "deleted"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.delete_comment", return_value=None)
    def test_delete_comment_state_patch(self, mock_delete, _svc, _ver, _pf, mock_update):
        args = _make_args("delete-comment", comment_id="c1", quiet=True, force=True)
        cmd_delete_comment(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"remove_comment_id": "c1"}
        assert call_kwargs[1].get("command_version") == 50

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.delete_comment", side_effect=GdocError("Document not found: abc123"))
    def test_delete_comment_api_error(self, mock_delete, _svc, _ver, _pf, _update):
        args = _make_args("delete-comment", comment_id="c1", quiet=True, force=True)
        with pytest.raises(GdocError, match="Document not found"):
            cmd_delete_comment(args)

    def test_delete_comment_force_skips_confirm(self):
        """--force bypasses confirmation even in non-interactive mode."""
        with patch("gdoc.util.confirm_destructive") as mock_confirm, \
             patch("gdoc.api.comments.delete_comment", return_value=None), \
             patch("gdoc.api.comments.get_drive_service"), \
             patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION), \
             patch("gdoc.notify.pre_flight", return_value=None), \
             patch("gdoc.state.update_state_after_command"):
            args = _make_args("delete-comment", comment_id="c1", quiet=True, force=True)
            cmd_delete_comment(args)
            mock_confirm.assert_called_once_with("delete comment #c1", force=True)

    def test_delete_comment_non_tty_without_force(self):
        """Non-interactive without --force raises GdocError."""
        with patch("sys.stdin") as mock_stdin, \
             patch("gdoc.notify.pre_flight", return_value=None), \
             patch("gdoc.state.update_state_after_command"):
            mock_stdin.isatty.return_value = False
            args = _make_args("delete-comment", comment_id="c1", quiet=True, force=False)
            with pytest.raises(GdocError, match="non-interactive"):
                cmd_delete_comment(args)

    def test_delete_comment_user_declines(self):
        """User declining confirmation raises GdocError."""
        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="n"), \
             patch("gdoc.notify.pre_flight", return_value=None), \
             patch("gdoc.state.update_state_after_command"):
            mock_stdin.isatty.return_value = True
            args = _make_args("delete-comment", comment_id="c1", quiet=True, force=False)
            with pytest.raises(GdocError, match="Cancelled"):
                cmd_delete_comment(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.delete_comment", return_value=None)
    def test_delete_comment_plain_output(self, mock_delete, _svc, _ver, _pf, _update, capsys):
        args = _make_args("delete-comment", comment_id="c1", quiet=True, force=True, plain=True)
        rc = cmd_delete_comment(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tc1" in out
        assert "status\tdeleted" in out


# --- cmd_resolve --message tests ---

class TestCmdResolveMessage:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_with_message(self, mock_reply, _svc, _ver, _pf, _update):
        args = _make_args("resolve", comment_id="c1", quiet=True, message="Done")
        cmd_resolve(args)
        mock_reply.assert_called_once_with("abc123", "c1", content="Done", action="resolve")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_without_message(self, mock_reply, _svc, _ver, _pf, _update):
        args = _make_args("resolve", comment_id="c1", quiet=True)
        cmd_resolve(args)
        mock_reply.assert_called_once_with("abc123", "c1", content="", action="resolve")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_plain_output(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("resolve", comment_id="c1", quiet=True, plain=True)
        rc = cmd_resolve(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tc1" in out
        assert "status\tresolved" in out


# --- cmd_comment_info tests ---

_MOCK_COMMENT_DETAIL = {
    "id": "c1",
    "content": "Fix this typo",
    "author": {"displayName": "Alice", "emailAddress": "alice@co.com"},
    "resolved": False,
    "createdTime": "2025-06-15T10:00:00Z",
    "modifiedTime": "2025-06-15T12:00:00Z",
    "quotedFileContent": {"value": "teh"},
    "replies": [
        {
            "id": "r1",
            "author": {"displayName": "Bob", "emailAddress": "bob@co.com"},
            "content": "Done",
            "action": "",
            "createdTime": "2025-06-15T11:00:00Z",
        },
    ],
}


class TestCmdCommentInfo:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.get_comment", return_value=_MOCK_COMMENT_DETAIL)
    def test_comment_info_terse(self, mock_get, _svc, _pf, _update, capsys):
        args = _make_args("comment-info", comment_id="c1", quiet=True)
        rc = cmd_comment_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "#c1 [open] alice@co.com 2025-06-15" in out
        assert '"Fix this typo"' in out
        assert "1 reply" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.get_comment", return_value=_MOCK_COMMENT_DETAIL)
    def test_comment_info_verbose(self, mock_get, _svc, _pf, _update, capsys):
        args = _make_args("comment-info", comment_id="c1", quiet=True, verbose=True)
        rc = cmd_comment_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "#c1 [open] alice@co.com 2025-06-15T10:00:00Z" in out
        assert '"Fix this typo"' in out
        assert 'on "teh"' in out
        assert "Modified: 2025-06-15T12:00:00Z" in out
        assert 'bob@co.com 2025-06-15T11:00:00Z: "Done"' in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.get_comment", return_value=_MOCK_COMMENT_DETAIL)
    def test_comment_info_json(self, mock_get, _svc, _pf, _update, capsys):
        args = _make_args("comment-info", comment_id="c1", quiet=True, json=True)
        rc = cmd_comment_info(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["comment"]["id"] == "c1"
        assert data["comment"]["content"] == "Fix this typo"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.get_comment", return_value=_MOCK_COMMENT_DETAIL)
    def test_comment_info_plain(self, mock_get, _svc, _pf, _update, capsys):
        args = _make_args("comment-info", comment_id="c1", quiet=True, plain=True)
        rc = cmd_comment_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tc1" in out
        assert "status\topen" in out
        assert "author\talice@co.com" in out
        assert "content\tFix this typo" in out
        assert "quote\tteh" in out
        assert "replies\t1" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.get_comment", side_effect=GdocError("Document not found: abc123"))
    def test_comment_info_not_found(self, mock_get, _svc, _pf, _update):
        args = _make_args("comment-info", comment_id="c1", quiet=True)
        with pytest.raises(GdocError, match="Document not found"):
            cmd_comment_info(args)


# --- plain output tests for other comment commands ---

class TestCommentCommandsPlainOutput:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_plain(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = [
            _make_comment(cid="c1", content="Fix typo", email="alice@co.com"),
        ]
        args = _make_args("comments", quiet=True, plain=True)
        cmd_comments(args)
        out = capsys.readouterr().out
        assert "c1\topen\talice@co.com\tFix typo\t" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.list_comments")
    def test_comments_plain_empty(self, mock_list, _svc, _pf, _update, capsys):
        mock_list.return_value = []
        args = _make_args("comments", quiet=True, plain=True)
        rc = cmd_comments(args)
        assert rc == 0
        assert capsys.readouterr().out == ""

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_plain(self, mock_create, _svc, _ver, _pf, _update, capsys):
        args = _make_args("comment", text="hello", quiet=True, plain=True)
        rc = cmd_comment(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "id\tc_new"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_plain(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("reply", comment_id="c1", text="thanks", quiet=True, plain=True)
        rc = cmd_reply(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "commentId\tc1" in out
        assert "replyId\tr1" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.drive.get_file_version", return_value=_MOCK_VERSION)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_plain(self, mock_reply, _svc, _ver, _pf, _update, capsys):
        args = _make_args("reopen", comment_id="c1", quiet=True, plain=True)
        rc = cmd_reopen(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "id\tc1" in out
        assert "status\treopened" in out
