"""Tests for per-doc state persistence."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gdoc.state import DocState, load_state, save_state, _state_path


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
