"""Tests for per-doc state persistence."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gdoc.state import DocState, load_state, save_state, update_state_after_command, _state_path


class TestDocState:
    def test_default_values(self):
        s = DocState()
        assert s.last_seen == ""
        assert s.last_version is None
        assert s.last_read_version is None
        assert s.last_comment_check == ""
        assert s.known_comment_ids == []
        assert s.known_resolved_ids == []

    def test_custom_values(self):
        s = DocState(
            last_seen="2025-01-20T14:30:00Z",
            last_version=847,
            last_read_version=845,
            last_comment_check="2025-01-20T14:30:00Z",
            known_comment_ids=["AAA", "BBB"],
            known_resolved_ids=["CCC"],
        )
        assert s.last_version == 847
        assert s.last_read_version == 845
        assert s.known_comment_ids == ["AAA", "BBB"]


class TestSaveLoadState:
    def test_round_trip(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            state = DocState(
                last_seen="2025-01-20T14:30:00Z",
                last_version=847,
                last_read_version=845,
                last_comment_check="2025-01-20T14:30:00Z",
                known_comment_ids=["AAA", "BBB"],
                known_resolved_ids=["CCC"],
            )
            save_state("doc123", state)
            loaded = load_state("doc123")
            assert loaded is not None
            assert loaded.last_version == 847
            assert loaded.last_read_version == 845
            assert loaded.known_comment_ids == ["AAA", "BBB"]
            assert loaded.known_resolved_ids == ["CCC"]

    def test_load_nonexistent_returns_none(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            assert load_state("nonexistent") is None

    def test_load_corrupt_json_returns_none(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            path = tmp_path / "corrupt.json"
            path.write_text("not json{{{")
            with patch("gdoc.state._state_path", return_value=path):
                assert load_state("corrupt") is None

    def test_save_creates_directory(self, tmp_path):
        state_dir = tmp_path / "nested" / "state"
        with patch("gdoc.state.STATE_DIR", state_dir):
            save_state("doc1", DocState(last_seen="2025-01-20T00:00:00Z"))
            assert (state_dir / "doc1.json").exists()

    def test_save_atomic_write(self, tmp_path):
        """Verify no .tmp files are left behind after successful save."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_seen="2025-01-20T00:00:00Z"))
            tmp_files = list(tmp_path.glob("*.tmp"))
            assert len(tmp_files) == 0

    def test_load_ignores_unknown_fields(self, tmp_path):
        """Forward compatibility: unknown JSON keys are silently ignored."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            path = tmp_path / "doc1.json"
            data = {"last_seen": "2025-01-20T00:00:00Z", "future_field": "value"}
            path.write_text(json.dumps(data))
            loaded = load_state("doc1")
            assert loaded is not None
            assert loaded.last_seen == "2025-01-20T00:00:00Z"

    def test_state_path(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            path = _state_path("abc123")
            assert path == tmp_path / "abc123.json"


class TestUpdateStateAfterCommand:
    def _make_change_info(self, **overrides):
        """Create a mock ChangeInfo-like object."""
        from types import SimpleNamespace
        defaults = {
            "current_version": 10,
            "preflight_timestamp": "2025-01-20T14:30:00.000000Z",
            "all_comment_ids": ["c1", "c2"],
            "all_resolved_ids": ["c2"],
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_normal_cat_updates_all_fields(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            info = self._make_change_info(current_version=42)
            update_state_after_command("doc1", info, command="cat", quiet=False)
            state = load_state("doc1")
            assert state.last_version == 42
            assert state.last_read_version == 42  # cat is a read
            assert state.last_comment_check == "2025-01-20T14:30:00.000000Z"
            assert state.known_comment_ids == ["c1", "c2"]
            assert state.known_resolved_ids == ["c2"]

    def test_normal_info_updates_all_fields(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            info = self._make_change_info(current_version=50)
            update_state_after_command("doc1", info, command="info", quiet=False)
            state = load_state("doc1")
            assert state.last_version == 50
            assert state.last_read_version == 50  # info is a read

    def test_quiet_cat_version_stays_stale(self, tmp_path):
        """Decision #14: --quiet cat does not update version fields."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_version=10, last_read_version=10))
            update_state_after_command("doc1", None, command="cat", quiet=True)
            state = load_state("doc1")
            assert state.last_version == 10  # unchanged
            assert state.last_read_version == 10  # unchanged
            assert state.last_seen != ""  # last_seen IS updated

    def test_quiet_info_version_from_command(self, tmp_path):
        """Decision #14: --quiet info updates version from command response."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_version=10, last_read_version=10))
            update_state_after_command(
                "doc1", None, command="info",
                quiet=True, command_version=20,
            )
            state = load_state("doc1")
            assert state.last_version == 20
            assert state.last_read_version == 20

    def test_quiet_does_not_advance_comment_check(self, tmp_path):
        """Decision #6: --quiet does not advance last_comment_check."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_comment_check="2025-01-20T00:00:00Z"))
            update_state_after_command("doc1", None, command="cat", quiet=True)
            state = load_state("doc1")
            assert state.last_comment_check == "2025-01-20T00:00:00Z"

    def test_first_interaction_creates_state(self, tmp_path):
        """First interaction (no existing state) initializes from change_info."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            info = self._make_change_info(current_version=5)
            update_state_after_command("doc1", info, command="cat", quiet=False)
            state = load_state("doc1")
            assert state is not None
            assert state.last_version == 5
            assert state.last_read_version == 5
            assert state.known_comment_ids == ["c1", "c2"]

    def test_non_read_command_does_not_set_read_version(self, tmp_path):
        """edit/write commands do not update last_read_version."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_read_version=5))
            info = self._make_change_info(current_version=10)
            update_state_after_command("doc1", info, command="edit", quiet=False)
            state = load_state("doc1")
            assert state.last_version == 10
            assert state.last_read_version == 5  # unchanged

    def test_last_seen_always_updated(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command("doc1", None, command="cat", quiet=True)
            state = load_state("doc1")
            assert state.last_seen != ""
            assert "T" in state.last_seen

    def test_edit_command_version_updates_last_version(self, tmp_path):
        """edit with command_version updates last_version."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            info = self._make_change_info(current_version=10)
            update_state_after_command(
                "doc1", info, command="edit",
                quiet=False, command_version=15,
            )
            state = load_state("doc1")
            assert state.last_version == 15  # from command_version, not pre-flight

    def test_edit_command_version_does_not_update_read_version(self, tmp_path):
        """edit with command_version does not update last_read_version."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_read_version=5))
            info = self._make_change_info(current_version=10)
            update_state_after_command(
                "doc1", info, command="edit",
                quiet=False, command_version=15,
            )
            state = load_state("doc1")
            assert state.last_read_version == 5  # unchanged

    def test_write_command_version_updates_last_version(self, tmp_path):
        """write with command_version updates last_version."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            info = self._make_change_info(current_version=10)
            update_state_after_command(
                "doc1", info, command="write",
                quiet=False, command_version=20,
            )
            state = load_state("doc1")
            assert state.last_version == 20

    def test_edit_with_preflight_then_command_version(self, tmp_path):
        """Pre-flight sets version, command_version overrides last_version."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_read_version=5, last_version=5))
            info = self._make_change_info(current_version=10)
            update_state_after_command(
                "doc1", info, command="edit",
                quiet=False, command_version=15,
            )
            state = load_state("doc1")
            # Pre-flight set last_version=10, then command_version overrides to 15
            assert state.last_version == 15
            assert state.last_read_version == 5  # unchanged by edit


class TestCommentStatePatch:
    def _make_change_info(self, **overrides):
        from types import SimpleNamespace
        defaults = {
            "current_version": 10,
            "preflight_timestamp": "2025-01-20T14:30:00.000000Z",
            "all_comment_ids": ["c1", "c2"],
            "all_resolved_ids": ["c2"],
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_add_comment_id(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_comment_ids=["c1"]))
            update_state_after_command(
                "doc1", None, command="comment", quiet=True,
                comment_state_patch={"add_comment_id": "c2"},
            )
            state = load_state("doc1")
            assert "c2" in state.known_comment_ids

    def test_add_resolved_id(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_resolved_ids=[]))
            update_state_after_command(
                "doc1", None, command="resolve", quiet=True,
                comment_state_patch={"add_resolved_id": "c1"},
            )
            state = load_state("doc1")
            assert "c1" in state.known_resolved_ids

    def test_remove_resolved_id(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_resolved_ids=["c1", "c2"]))
            update_state_after_command(
                "doc1", None, command="reopen", quiet=True,
                comment_state_patch={"remove_resolved_id": "c1"},
            )
            state = load_state("doc1")
            assert "c1" not in state.known_resolved_ids
            assert "c2" in state.known_resolved_ids

    def test_no_duplicate_add_comment_id(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_comment_ids=["c1"]))
            update_state_after_command(
                "doc1", None, command="comment", quiet=True,
                comment_state_patch={"add_comment_id": "c1"},
            )
            state = load_state("doc1")
            assert state.known_comment_ids.count("c1") == 1

    def test_no_duplicate_add_resolved_id(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_resolved_ids=["c1"]))
            update_state_after_command(
                "doc1", None, command="resolve", quiet=True,
                comment_state_patch={"add_resolved_id": "c1"},
            )
            state = load_state("doc1")
            assert state.known_resolved_ids.count("c1") == 1

    def test_patch_none_is_noop(self, tmp_path):
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(
                known_comment_ids=["c1"],
                known_resolved_ids=["c2"],
            ))
            update_state_after_command(
                "doc1", None, command="reply", quiet=True,
                comment_state_patch=None,
            )
            state = load_state("doc1")
            assert state.known_comment_ids == ["c1"]
            assert state.known_resolved_ids == ["c2"]

    def test_nonquiet_preflight_then_patch(self, tmp_path):
        """Non-quiet: pre-flight merged IDs written, THEN patch adds new ID."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_comment_ids=["c1"]))
            info = self._make_change_info(
                all_comment_ids=["c1", "c2"],
                all_resolved_ids=[],
            )
            update_state_after_command(
                "doc1", info, command="comment", quiet=False,
                comment_state_patch={"add_comment_id": "c3"},
            )
            state = load_state("doc1")
            # Pre-flight set ["c1","c2"], then patch adds "c3"
            assert "c1" in state.known_comment_ids
            assert "c2" in state.known_comment_ids
            assert "c3" in state.known_comment_ids

    def test_nonquiet_resolve_preflight_then_patch(self, tmp_path):
        """Non-quiet: pre-flight merged IDs, then patch adds resolved ID."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_resolved_ids=[]))
            info = self._make_change_info(
                all_comment_ids=["c1", "c2"],
                all_resolved_ids=[],
            )
            update_state_after_command(
                "doc1", info, command="resolve", quiet=False,
                comment_state_patch={"add_resolved_id": "c1"},
            )
            state = load_state("doc1")
            assert "c1" in state.known_resolved_ids

    def test_nonquiet_reopen_preflight_then_patch(self, tmp_path):
        """Non-quiet: pre-flight merged IDs, then patch removes resolved ID."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(known_resolved_ids=["c1"]))
            info = self._make_change_info(
                all_comment_ids=["c1", "c2"],
                all_resolved_ids=["c1"],
            )
            update_state_after_command(
                "doc1", info, command="reopen", quiet=False,
                comment_state_patch={"remove_resolved_id": "c1"},
            )
            state = load_state("doc1")
            assert "c1" not in state.known_resolved_ids

    def test_remove_comment_id_prunes_both_lists(self, tmp_path):
        """remove_comment_id removes from known_comment_ids and known_resolved_ids."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(
                known_comment_ids=["c1", "c2", "c3"],
                known_resolved_ids=["c1", "c3"],
            ))
            update_state_after_command(
                "doc1", None, command="delete-comment", quiet=True,
                comment_state_patch={"remove_comment_id": "c1"},
            )
            state = load_state("doc1")
            assert "c1" not in state.known_comment_ids
            assert "c1" not in state.known_resolved_ids
            assert "c2" in state.known_comment_ids
            assert "c3" in state.known_comment_ids
            assert "c3" in state.known_resolved_ids

    def test_remove_comment_id_nonexistent_is_noop(self, tmp_path):
        """Removing a comment ID that doesn't exist is a no-op."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(
                known_comment_ids=["c1"],
                known_resolved_ids=["c1"],
            ))
            update_state_after_command(
                "doc1", None, command="delete-comment", quiet=True,
                comment_state_patch={"remove_comment_id": "c999"},
            )
            state = load_state("doc1")
            assert state.known_comment_ids == ["c1"]
            assert state.known_resolved_ids == ["c1"]

    def test_quiet_does_not_advance_comment_check(self, tmp_path):
        """Quiet mode: last_comment_check unchanged with patch."""
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("doc1", DocState(last_comment_check="2025-01-20T00:00:00Z"))
            update_state_after_command(
                "doc1", None, command="comment", quiet=True,
                comment_state_patch={"add_comment_id": "c_new"},
            )
            state = load_state("doc1")
            assert state.last_comment_check == "2025-01-20T00:00:00Z"
