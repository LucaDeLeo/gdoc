"""Tests for the `gdoc revisions` command handler."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from gdoc.cli import cmd_revisions

REVS = [
    {
        "id": "1", "modifiedTime": "2026-06-01T10:00:00.000Z",
        "lastModifyingUser": {"displayName": "Alice"},
        "keepForever": False,
        "exportLinks": {"text/markdown": "https://example.test/1"},
    },
    {
        "id": "7", "modifiedTime": "2026-06-05T10:00:00.000Z",
        "lastModifyingUser": {"displayName": "Bob"},
        "keepForever": True,
        "exportLinks": {"text/markdown": "https://example.test/7"},
    },
    {
        "id": "66", "modifiedTime": "2026-06-10T10:00:00.000Z",
        "lastModifyingUser": {"displayName": "Alice"},
        "keepForever": False,
        "exportLinks": {"text/markdown": "https://example.test/66"},
    },
]


def _make_args(**overrides):
    defaults = {
        "command": "revisions",
        "doc": "abc123",
        "limit": 0,
        "plain": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.revisions.list_revisions", return_value=list(REVS))
@patch("gdoc.notify.pre_flight", return_value=None)
class TestRevisionsOutput:
    def test_terse_table(self, _pf, _list, _update, capsys):
        rc = cmd_revisions(_make_args())
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 3
        assert "Alice" in lines[0]
        assert "[keep]" in lines[1]
        assert "[keep]" not in lines[0]

    def test_json(self, _pf, _list, _update, capsys):
        rc = cmd_revisions(_make_args(json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert [r["id"] for r in data["revisions"]] == ["1", "7", "66"]
        assert data["revisions"][1]["keepForever"] is True
        assert data["revisions"][0]["lastModifyingUser"]["displayName"] == "Alice"
        assert "exportLinks" in data["revisions"][0]

    def test_plain_tsv(self, _pf, _list, _update, capsys):
        rc = cmd_revisions(_make_args(plain=True))
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0] == "1\t2026-06-01T10:00:00.000Z\tAlice\tfalse"
        assert lines[1] == "7\t2026-06-05T10:00:00.000Z\tBob\ttrue"

    def test_limit_keeps_most_recent(self, _pf, _list, _update, capsys):
        rc = cmd_revisions(_make_args(json=True, limit=2))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in data["revisions"]] == ["7", "66"]

    def test_verbose_shows_full_timestamps(self, _pf, _list, _update, capsys):
        rc = cmd_revisions(_make_args(verbose=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "2026-06-05T10:00:00.000Z" in out
        assert "(3 revisions" in out


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.revisions.list_revisions", return_value=[])
@patch("gdoc.notify.pre_flight", return_value=None)
class TestRevisionsEmpty:
    def test_terse_empty(self, _pf, _list, _update, capsys):
        rc = cmd_revisions(_make_args())
        assert rc == 0
        assert "No revisions retained." in capsys.readouterr().out


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.revisions.list_revisions", return_value=list(REVS))
@patch("gdoc.notify.pre_flight", return_value=None)
class TestRevisionsAwareness:
    def test_preflight_and_state(self, mock_pf, _list, mock_update, capsys):
        cmd_revisions(_make_args(quiet=True))
        mock_pf.assert_called_once_with("abc123", quiet=True)
        mock_update.assert_called_once_with(
            "abc123", None, command="revisions", quiet=True,
        )
