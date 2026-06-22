"""Tests for the revision paths of `gdoc diff` (--rev / --since)."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_diff
from gdoc.util import GdocError

REVS = [
    {
        "id": "1", "modifiedTime": "2026-06-01T10:00:00.000Z",
        "exportLinks": {"text/markdown": "https://example.test/1.md"},
    },
    {
        "id": "20", "modifiedTime": "2026-06-08T10:00:00.000Z",
        "exportLinks": {"text/markdown": "https://example.test/20.md"},
    },
    {
        "id": "66", "modifiedTime": "2026-06-10T10:00:00.000Z",
        "exportLinks": {"text/markdown": "https://example.test/66.md"},
    },
]

CONTENT = {
    "1": "# Title\n\nThe original opening paragraph with several words.\n",
    "20": "# Title\n\nThe revised opening paragraph with several words.\n",
    "66": "# Title\n\nThe final opening paragraph with several words.\n",
}


def _export(doc_id, revision_id, mime_type="text/markdown",
            export_links=None):
    return CONTENT[revision_id]


def _make_args(**overrides):
    defaults = {
        "command": "diff",
        "doc": "abc123",
        "file": None,
        "rev": None,
        "since": None,
        "format": "auto",
        "out": None,
        "with_comments": False,
        "min_common": 24,
        "context": 2,
        "plain": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patches(func):
    """Stack the standard mocks for revision-diff handler tests.

    The first-applied patch is innermost, so mock args arrive in this
    order: (pre_flight, list_revisions, export_revision, get_file_info,
    update_state).
    """
    func = patch("gdoc.notify.pre_flight", return_value=None)(func)
    func = patch(
        "gdoc.api.revisions.list_revisions", return_value=list(REVS),
    )(func)
    func = patch(
        "gdoc.api.revisions.export_revision", side_effect=_export,
    )(func)
    func = patch(
        "gdoc.api.drive.get_file_info",
        return_value={"name": "My Doc", "version": 42},
    )(func)
    func = patch("gdoc.state.update_state_after_command")(func)
    return func


class TestRevSelection:
    @_patches
    def test_rev_range_json_model(
        self, _pf, _list, _export, _info, _update, capsys,
    ):
        rc = cmd_diff(_make_args(rev="1..20", json=True))
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["identical"] is False
        assert data["doc"] == {"id": "abc123", "name": "My Doc"}
        assert data["old"]["id"] == "1"
        assert data["new"]["id"] == "20"
        kinds = [h["kind"] for h in data["hunks"]]
        assert "replace" in kinds
        replace = next(h for h in data["hunks"] if h["kind"] == "replace")
        assert {r["op"] for r in replace["runs"]} >= {"del", "ins"}
        assert "comments" not in data

    @_patches
    def test_single_rev_diffs_against_latest(
        self, _pf, _list, mock_export, _info, _update, capsys,
    ):
        rc = cmd_diff(_make_args(rev="1", json=True))
        assert rc == 1
        exported = [c.args[1] for c in mock_export.call_args_list]
        assert exported == ["1", "66"]

    @_patches
    def test_since_resolves_old_revision(
        self, _pf, _list, mock_export, _info, _update, capsys,
    ):
        rc = cmd_diff(_make_args(since="2026-06-09T00:00:00Z", json=True))
        assert rc == 1
        exported = [c.args[1] for c in mock_export.call_args_list]
        assert exported == ["20", "66"]

    @_patches
    def test_identical_revisions_return_zero(
        self, _pf, _list, _export, _info, _update, capsys,
    ):
        rc = cmd_diff(_make_args(rev="66..66"))
        assert rc == 0
        assert "OK identical" in capsys.readouterr().out


class TestRevOutput:
    @_patches
    def test_plain_terminal_output(
        self, _pf, _list, _export, _info, _update, capsys,
    ):
        rc = cmd_diff(_make_args(rev="1..20", format="plain"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "[-original-]" in out
        assert "{+revised+}" in out
        assert "\x1b[" not in out

    @_patches
    def test_color_terminal_output(
        self, _pf, _list, _export, _info, _update, capsys,
    ):
        rc = cmd_diff(_make_args(rev="1..20", format="color"))
        assert rc == 1
        assert "\x1b[32m" in capsys.readouterr().out

    @_patches
    def test_html_artifact(
        self, _pf, _list, _export, _info, _update, capsys, tmp_path,
    ):
        out_path = tmp_path / "diff.html"
        rc = cmd_diff(_make_args(rev="1..20", out=str(out_path)))
        assert rc == 1
        html = out_path.read_text()
        assert "<ins>" in html
        assert "My Doc" in html
        assert f"OK wrote {out_path}" in capsys.readouterr().out

    @_patches
    def test_with_comments_in_json(
        self, _pf, _list, _export, _info, _update, capsys,
    ):
        comments = [{
            "id": "c1", "author": {"displayName": "Alice"},
            "createdTime": "2026-06-09T00:00:00Z", "resolved": False,
            "content": "note",
            "quotedFileContent": {"value": "opening paragraph"},
            "replies": [],
        }]
        with patch(
            "gdoc.api.comments.list_comments", return_value=comments,
        ) as mock_comments:
            rc = cmd_diff(_make_args(rev="1..20", json=True,
                                     with_comments=True))
            assert rc == 1
            mock_comments.assert_called_once_with(
                "abc123", include_anchor=True,
            )
        data = json.loads(capsys.readouterr().out)
        assert len(data["comments"]) == 1
        assert data["comments"][0]["author"] == "Alice"
        assert data["comments"][0]["hunk"] is not None

    @_patches
    def test_state_updated_with_version(
        self, _pf, _list, _export, _info, mock_update, capsys,
    ):
        # The version comes from get_file_info's metadata — no extra
        # get_file_version round trip.
        cmd_diff(_make_args(rev="1..20", json=True))
        mock_update.assert_called_once_with(
            "abc123", None, command="diff", quiet=False,
            command_version=42,
        )

    @_patches
    def test_json_confirmation_for_artifact(
        self, _pf, _list, _export, _info, _update, capsys, tmp_path,
    ):
        # --json with an artifact write prints a machine confirmation
        out_path = tmp_path / "diff.html"
        rc = cmd_diff(_make_args(rev="1..20", json=True, out=str(out_path)))
        assert rc == 1
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["path"] == str(out_path)
        assert data["format"] == "html"
        assert data["changed"] >= 1
        assert data["identical"] is False
        assert out_path.exists()

    @_patches
    def test_artifact_write_error_exits_3(
        self, _pf, _list, _export, _info, _update, tmp_path,
    ):
        out_path = tmp_path / "no-such-dir" / "diff.html"
        with pytest.raises(GdocError, match="cannot write file") as exc_info:
            cmd_diff(_make_args(rev="1..20", out=str(out_path)))
        assert exc_info.value.exit_code == 3


class TestRevValidation:
    def test_rev_and_since_conflict(self):
        with pytest.raises(GdocError, match="mutually exclusive") as exc_info:
            cmd_diff(_make_args(rev="1..20", since="2026-06-09"))
        assert exc_info.value.exit_code == 3

    def test_file_and_rev_conflict(self):
        with pytest.raises(GdocError, match="mutually exclusive") as exc_info:
            cmd_diff(_make_args(file="/tmp/x.md", rev="1..20"))
        assert exc_info.value.exit_code == 3

    def test_no_file_no_rev(self):
        with pytest.raises(GdocError, match="nothing to compare") as exc_info:
            cmd_diff(_make_args())
        assert exc_info.value.exit_code == 3

    def test_format_with_file_diff_rejected(self):
        with pytest.raises(GdocError, match="revision diffs") as exc_info:
            cmd_diff(_make_args(file="/tmp/x.md", format="html"))
        assert exc_info.value.exit_code == 3

    def test_out_with_unknown_extension(self):
        with pytest.raises(GdocError, match="cannot infer format") as exc_info:
            cmd_diff(_make_args(rev="1..20", out="diff.pdf"))
        assert exc_info.value.exit_code == 3

    def test_out_with_terminal_format(self):
        with pytest.raises(GdocError, match="--out requires") as exc_info:
            cmd_diff(_make_args(rev="1..20", format="color", out="x.html"))
        assert exc_info.value.exit_code == 3

    def test_json_flag_with_terminal_format(self):
        with pytest.raises(GdocError, match="mutually exclusive") as exc_info:
            cmd_diff(_make_args(rev="1..20", json=True, format="color"))
        assert exc_info.value.exit_code == 3

    def test_plain_flag_with_color_format(self):
        with pytest.raises(GdocError, match="mutually exclusive") as exc_info:
            cmd_diff(_make_args(rev="1..20", plain=True, format="color"))
        assert exc_info.value.exit_code == 3

    def test_with_comments_requires_rich_format(self):
        with pytest.raises(
            GdocError, match="--with-comments requires",
        ) as exc_info:
            cmd_diff(_make_args(rev="1..20", format="plain",
                                with_comments=True))
        assert exc_info.value.exit_code == 3

    def test_invalid_range_fails_before_api_calls(self):
        # No mocks: selector syntax must be validated before pre-flight
        with pytest.raises(GdocError, match="invalid revision range") as exc_info:
            cmd_diff(_make_args(rev="1..", json=True))
        assert exc_info.value.exit_code == 3

    def test_invalid_since_fails_before_api_calls(self):
        with pytest.raises(GdocError, match="invalid timestamp") as exc_info:
            cmd_diff(_make_args(since="not-a-date"))
        assert exc_info.value.exit_code == 3
