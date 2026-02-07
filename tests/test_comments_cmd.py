"""Tests for comment CRUD CLI commands."""

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from gdoc.cli import cmd_comments, cmd_comment, cmd_reply, cmd_resolve, cmd_reopen
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

class TestCmdComment:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_ok_output(self, mock_create, _svc, _pf, _update, capsys):
        args = _make_args("comment", text="hello", quiet=True)
        rc = cmd_comment(args)
        assert rc == 0
        assert "OK comment #c_new" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_json_output(self, mock_create, _svc, _pf, _update, capsys):
        args = _make_args("comment", text="hello", json=True, quiet=True)
        rc = cmd_comment(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c_new", "status": "created"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", side_effect=GdocError("Document not found: abc123"))
    def test_comment_api_error(self, mock_create, _svc, _pf, _update):
        args = _make_args("comment", text="hello", quiet=True)
        with pytest.raises(GdocError, match="Document not found"):
            cmd_comment(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", side_effect=AuthError("Authentication expired"))
    def test_comment_auth_error(self, mock_create, _svc, _pf, _update):
        args = _make_args("comment", text="hello", quiet=True)
        with pytest.raises(AuthError):
            cmd_comment(args)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_comment", return_value={"id": "c_new"})
    def test_comment_state_patch(self, mock_create, _svc, _pf, mock_update):
        args = _make_args("comment", text="hello", quiet=True)
        cmd_comment(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"add_comment_id": "c_new"}


# --- cmd_reply tests ---

class TestCmdReply:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_ok_output(self, mock_reply, _svc, _pf, _update, capsys):
        args = _make_args("reply", comment_id="c1", text="thanks", quiet=True)
        rc = cmd_reply(args)
        assert rc == 0
        assert "OK reply on #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_json_output(self, mock_reply, _svc, _pf, _update, capsys):
        args = _make_args("reply", comment_id="c1", text="thanks", json=True, quiet=True)
        rc = cmd_reply(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "commentId": "c1", "replyId": "r1", "status": "created"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r1"})
    def test_reply_no_state_patch(self, mock_reply, _svc, _pf, mock_update):
        args = _make_args("reply", comment_id="c1", text="thanks", quiet=True)
        cmd_reply(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        # reply should NOT have comment_state_patch
        assert "comment_state_patch" not in call_kwargs[1]


# --- cmd_resolve tests ---

class TestCmdResolve:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_ok_output(self, mock_reply, _svc, _pf, _update, capsys):
        args = _make_args("resolve", comment_id="c1", quiet=True)
        rc = cmd_resolve(args)
        assert rc == 0
        assert "OK resolved comment #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_json_output(self, mock_reply, _svc, _pf, _update, capsys):
        args = _make_args("resolve", comment_id="c1", json=True, quiet=True)
        rc = cmd_resolve(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c1", "status": "resolved"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r2", "action": "resolve"})
    def test_resolve_state_patch(self, mock_reply, _svc, _pf, mock_update):
        args = _make_args("resolve", comment_id="c1", quiet=True)
        cmd_resolve(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"add_resolved_id": "c1"}


# --- cmd_reopen tests ---

class TestCmdReopen:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_ok_output(self, mock_reply, _svc, _pf, _update, capsys):
        args = _make_args("reopen", comment_id="c1", quiet=True)
        rc = cmd_reopen(args)
        assert rc == 0
        assert "OK reopened comment #c1" in capsys.readouterr().out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_json_output(self, mock_reply, _svc, _pf, _update, capsys):
        args = _make_args("reopen", comment_id="c1", json=True, quiet=True)
        rc = cmd_reopen(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"ok": True, "id": "c1", "status": "reopened"}

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.comments.get_drive_service")
    @patch("gdoc.api.comments.create_reply", return_value={"id": "r3", "action": "reopen"})
    def test_reopen_state_patch(self, mock_reply, _svc, _pf, mock_update):
        args = _make_args("reopen", comment_id="c1", quiet=True)
        cmd_reopen(args)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1].get("comment_state_patch") == {"remove_resolved_id": "c1"}


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
        mock_list.assert_called_once_with("abc123", include_resolved=False)

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
        mock_list.assert_called_once_with("abc123", include_resolved=True)

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
        assert capsys.readouterr().out == ""

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
