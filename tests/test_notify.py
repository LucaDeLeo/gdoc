"""Tests for the pre-flight notification system."""

from unittest.mock import patch

import pytest

from gdoc.notify import pre_flight, ChangeInfo, _format_time_ago, _print_banner
from gdoc.state import DocState


class TestChangeInfo:
    def test_no_changes(self):
        info = ChangeInfo()
        assert not info.has_changes
        assert not info.has_conflict

    def test_doc_edited(self):
        info = ChangeInfo(doc_edited=True)
        assert info.has_changes

    def test_has_conflict_version_changed_since_read(self):
        """Conflict: current_version differs from last_read_version (Decision #7)."""
        info = ChangeInfo(current_version=10, last_read_version=8)
        assert info.has_conflict

    def test_no_conflict_version_matches_read(self):
        """No conflict: current_version matches last_read_version."""
        info = ChangeInfo(current_version=10, last_read_version=10)
        assert not info.has_conflict

    def test_conflict_no_prior_read(self):
        """Conflict: last_read_version is None (never read) (Decision #7)."""
        info = ChangeInfo(current_version=10, last_read_version=None)
        assert info.has_conflict

    def test_no_conflict_no_current_version(self):
        """No conflict: current_version is None (no version data)."""
        info = ChangeInfo(current_version=None, last_read_version=5)
        assert not info.has_conflict

    def test_new_comments_only(self):
        info = ChangeInfo(new_comments=[{"id": "c1"}])
        assert info.has_changes
        assert not info.has_conflict

    def test_new_replies(self):
        info = ChangeInfo(new_replies=[{"id": "c1"}])
        assert info.has_changes

    def test_resolved(self):
        info = ChangeInfo(newly_resolved=[{"id": "c1"}])
        assert info.has_changes

    def test_reopened(self):
        info = ChangeInfo(newly_reopened=[{"id": "c1"}])
        assert info.has_changes


class TestPreFlightQuiet:
    def test_quiet_returns_none(self):
        """--quiet short-circuits before any API calls (Decision #6)."""
        result = pre_flight("doc1", quiet=True)
        assert result is None

    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 1, "modifiedTime": "2025-01-20T00:00:00Z"})
    @patch("gdoc.api.drive.get_file_info", return_value={"name": "Test", "owners": [], "modifiedTime": "2025-01-20T00:00:00Z"})
    @patch("gdoc.state.load_state", return_value=None)
    def test_quiet_makes_no_api_calls(self, mock_load, mock_info, mock_ver, mock_comments):
        """Verify --quiet doesn't call any API functions."""
        pre_flight("doc1", quiet=True)
        mock_ver.assert_not_called()
        mock_comments.assert_not_called()
        mock_info.assert_not_called()


class TestPreFlightFirstInteraction:
    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.drive.get_file_info")
    @patch("gdoc.state.load_state", return_value=None)
    def test_first_interaction_banner(self, mock_load, mock_info, mock_ver, mock_comments, capsys):
        mock_ver.return_value = {"version": 10, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}
        mock_info.return_value = {
            "name": "Q3 Planning Doc",
            "owners": [{"emailAddress": "alice@co.com"}],
            "modifiedTime": "2025-01-20T14:30:00Z",
        }
        mock_comments.return_value = [
            {"id": "c1", "resolved": False},
            {"id": "c2", "resolved": False},
            {"id": "c3", "resolved": True},
        ]

        result = pre_flight("doc1")
        assert result is not None
        assert result.is_first_interaction
        assert result.open_comment_count == 2
        assert result.resolved_comment_count == 1
        assert result.current_version == 10

        err = capsys.readouterr().err
        assert "first interaction with this doc" in err
        assert "Q3 Planning Doc" in err
        assert "alice@co.com" in err
        assert "2 open comments" in err
        assert "1 resolved" in err

    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.drive.get_file_info")
    @patch("gdoc.state.load_state", return_value=None)
    def test_first_interaction_no_comments(self, mock_load, mock_info, mock_ver, mock_comments, capsys):
        mock_ver.return_value = {"version": 5, "modifiedTime": "2025-01-20T00:00:00Z", "lastModifyingUser": {}}
        mock_info.return_value = {"name": "Empty Doc", "owners": [{"emailAddress": "bob@co.com"}], "modifiedTime": "2025-01-20T00:00:00Z"}
        mock_comments.return_value = []

        result = pre_flight("doc1")
        err = capsys.readouterr().err
        assert "first interaction" in err
        assert "Empty Doc" in err
        assert "open comment" not in err

    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.drive.get_file_info")
    @patch("gdoc.state.load_state", return_value=None)
    def test_first_interaction_initializes_comment_ids(self, mock_load, mock_info, mock_ver, mock_comments):
        mock_ver.return_value = {"version": 5, "modifiedTime": "2025-01-20T00:00:00Z", "lastModifyingUser": {}}
        mock_info.return_value = {"name": "Doc", "owners": [], "modifiedTime": "2025-01-20T00:00:00Z"}
        mock_comments.return_value = [
            {"id": "c1", "resolved": False},
            {"id": "c2", "resolved": True},
        ]
        result = pre_flight("doc1")
        assert "c1" in result.all_comment_ids
        assert "c2" in result.all_comment_ids
        assert "c2" in result.all_resolved_ids
        assert "c1" not in result.all_resolved_ids


class TestPreFlightChanges:
    def _make_state(self, **overrides):
        defaults = {
            "last_seen": "2025-01-20T14:30:00Z",
            "last_version": 847,
            "last_read_version": 845,
            "last_comment_check": "2025-01-20T14:30:00Z",
            "known_comment_ids": ["c1", "c2"],
            "known_resolved_ids": [],
        }
        defaults.update(overrides)
        return DocState(**defaults)

    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_no_changes_banner(self, mock_load, mock_ver, mock_comments, capsys):
        mock_load.return_value = self._make_state()
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}

        result = pre_flight("doc1")
        assert not result.has_changes
        err = capsys.readouterr().err
        assert "no changes" in err

    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_doc_edited_detection(self, mock_load, mock_ver, mock_comments, capsys):
        mock_load.return_value = self._make_state(last_version=847)
        mock_ver.return_value = {
            "version": 851,
            "modifiedTime": "2025-01-20T15:00:00Z",
            "lastModifyingUser": {"emailAddress": "alice@co.com"},
        }

        result = pre_flight("doc1")
        assert result.doc_edited
        assert result.has_conflict
        assert result.editor == "alice@co.com"
        assert result.old_version == 847
        assert result.new_version == 851

        err = capsys.readouterr().err
        assert "doc edited" in err
        assert "alice@co.com" in err
        assert "v847" in err
        assert "v851" in err

    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_new_comment_detection(self, mock_load, mock_ver, mock_comments, capsys):
        mock_load.return_value = self._make_state(known_comment_ids=["c1"])
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}
        mock_comments.return_value = [
            {"id": "c2", "content": "New comment here", "resolved": False,
             "author": {"emailAddress": "carol@co.com"}},
        ]

        result = pre_flight("doc1")
        assert len(result.new_comments) == 1
        err = capsys.readouterr().err
        assert "carol@co.com" in err
        assert "New comment here" in err

    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_resolved_comment_detection(self, mock_load, mock_ver, mock_comments, capsys):
        mock_load.return_value = self._make_state(known_comment_ids=["c1"], known_resolved_ids=[])
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}
        mock_comments.return_value = [
            {"id": "c1", "resolved": True, "replies": [
                {"action": "resolve", "author": {"emailAddress": "alice@co.com"}}
            ]},
        ]

        result = pre_flight("doc1")
        assert len(result.newly_resolved) == 1
        err = capsys.readouterr().err
        assert "resolved" in err
        assert "alice@co.com" in err

    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_reopened_comment_detection(self, mock_load, mock_ver, mock_comments, capsys):
        mock_load.return_value = self._make_state(known_comment_ids=["c1"], known_resolved_ids=["c1"])
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}
        mock_comments.return_value = [
            {"id": "c1", "resolved": False, "replies": [
                {"action": "reopen", "author": {"emailAddress": "bob@co.com"}}
            ]},
        ]

        result = pre_flight("doc1")
        assert len(result.newly_reopened) == 1
        err = capsys.readouterr().err
        assert "reopened" in err

    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_new_reply_detection(self, mock_load, mock_ver, mock_comments, capsys):
        mock_load.return_value = self._make_state(known_comment_ids=["c1"])
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}
        mock_comments.return_value = [
            {"id": "c1", "resolved": False, "replies": [
                {"author": {"emailAddress": "bob@co.com"}, "content": "Done"},
            ]},
        ]

        result = pre_flight("doc1")
        assert len(result.new_replies) == 1
        err = capsys.readouterr().err
        assert "bob@co.com" in err

    @patch("gdoc.api.comments.list_comments", return_value=[])
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_preflight_timestamp_captured(self, mock_load, mock_ver, mock_comments):
        mock_load.return_value = self._make_state()
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}

        result = pre_flight("doc1")
        assert result.preflight_timestamp != ""
        assert "T" in result.preflight_timestamp

    @patch("gdoc.api.comments.list_comments")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.state.load_state")
    def test_comment_ids_accumulated(self, mock_load, mock_ver, mock_comments):
        """Comment IDs from both state and new API results are merged."""
        mock_load.return_value = self._make_state(known_comment_ids=["c1", "c2"])
        mock_ver.return_value = {"version": 847, "modifiedTime": "2025-01-20T14:30:00Z", "lastModifyingUser": {}}
        mock_comments.return_value = [
            {"id": "c3", "resolved": False},
        ]

        result = pre_flight("doc1")
        assert "c1" in result.all_comment_ids
        assert "c2" in result.all_comment_ids
        assert "c3" in result.all_comment_ids


class TestFormatTimeAgo:
    def test_empty_string(self):
        assert _format_time_ago("") == ""

    def test_invalid_timestamp(self):
        assert _format_time_ago("not-a-date") == ""

    @patch("gdoc.notify.datetime")
    def test_seconds_ago(self, mock_dt):
        from datetime import datetime, timezone
        now = datetime(2025, 1, 20, 14, 30, 30, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        assert _format_time_ago("2025-01-20T14:30:00Z") == "30 sec ago"

    @patch("gdoc.notify.datetime")
    def test_minutes_ago(self, mock_dt):
        from datetime import datetime, timezone
        now = datetime(2025, 1, 20, 14, 35, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        assert _format_time_ago("2025-01-20T14:30:00Z") == "5 min ago"

    @patch("gdoc.notify.datetime")
    def test_hours_ago(self, mock_dt):
        from datetime import datetime, timezone
        now = datetime(2025, 1, 20, 17, 30, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        assert _format_time_ago("2025-01-20T14:30:00Z") == "3 hr ago"

    @patch("gdoc.notify.datetime")
    def test_days_ago(self, mock_dt):
        from datetime import datetime, timezone
        now = datetime(2025, 1, 23, 14, 30, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        assert _format_time_ago("2025-01-20T14:30:00Z") == "3 days ago"

    @patch("gdoc.notify.datetime")
    def test_one_day_ago(self, mock_dt):
        from datetime import datetime, timezone
        now = datetime(2025, 1, 21, 14, 30, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        assert _format_time_ago("2025-01-20T14:30:00Z") == "1 day ago"
