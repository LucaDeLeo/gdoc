"""Stale-file protection: `pull` stamps `gdoc-version`, uploads honour it.

The scenario these tests pin down:

1. `gdoc pull DOC draft.md` at version 1; edit draft.md.
2. A collaborator edits the doc (version 2).
3. Any `gdoc cat DOC` on this machine records "last read: version 2".
4. Uploading draft.md must refuse — the file is based on version 1.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc import mcp
from gdoc.cli import cmd_pull, cmd_pull_hook, cmd_push, cmd_sync_hook, cmd_write
from gdoc.frontmatter import parse_frontmatter
from gdoc.notify import ChangeInfo
from gdoc.state import DocState
from gdoc.util import GdocError

DOC = "abc123"


def _stamped(version, body="# Draft\n\nMy local edit.\n", doc=DOC):
    return f"---\ngdoc: {doc}\ntitle: My Doc\ngdoc-version: {version}\n---\n{body}"


def _unstamped(body="# Draft\n\nMy local edit.\n"):
    return f"---\ngdoc: {DOC}\ntitle: My Doc\n---\n{body}"


def _stamp_of(path):
    return parse_frontmatter(path.read_text())[0].get("gdoc-version")


def _push_args(path, **overrides):
    args = {
        "command": "push", "file": str(path), "force": False,
        "force_collapse_tabs": False, "json": False, "verbose": False,
        "quiet": False,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def _write_args(path, doc=DOC, **overrides):
    args = {
        "command": "write", "doc": doc, "file": str(path), "force": False,
        "json": False, "verbose": False, "quiet": False, "tab": None,
        "force_collapse_tabs": False,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def _hook_stdin(path):
    return io.StringIO(json.dumps({"tool_input": {"file_path": str(path)}}))


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """No Google calls, no real ~/.config/gdoc state."""
    monkeypatch.setattr("gdoc.state.STATE_DIR", tmp_path / "state")
    with (
        patch("gdoc.api.drive.get_drive_service"),
        patch("gdoc.api.docs.count_document_tabs", return_value=1),
        # The doc now holds the collaborator's edit, never our body.
        patch("gdoc.api.drive.export_doc", return_value="# Collaborator text\n"),
    ):
        yield


def _machine_read(version):
    """Step 3: this machine's pre-flight says it last read `version`."""
    return ChangeInfo(current_version=version, last_read_version=version)


# --- Step 4 refused on every uploading path ---------------------------------


class TestStaleFileRefused:
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_push(self, _pf, mock_upload, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1))
        before = f.read_bytes()
        with pytest.raises(GdocError) as exc:
            cmd_push(_push_args(f))
        assert exc.value.exit_code == 3
        mock_upload.assert_not_called()
        assert f.read_bytes() == before

        # Recovery pulls into a new path: re-pulling draft.md would
        # overwrite the edits this refusal just protected.
        msg = str(exc.value)
        latest = tmp_path / "draft.latest.md"
        assert f"gdoc pull {DOC} {latest}" in msg
        assert f"gdoc pull {DOC} {f}" not in msg
        assert f"gdoc diff {DOC} {f}" in msg
        assert "Nothing was sent" in msg and "--force" in msg

    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    def test_push_quiet(self, _ver, mock_upload, tmp_path):
        from gdoc.state import save_state

        save_state(DOC, DocState(last_read_version=2, last_version=2))
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1))
        with pytest.raises(GdocError) as exc:
            cmd_push(_push_args(f, quiet=True))
        assert exc.value.exit_code == 3
        mock_upload.assert_not_called()

    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_write_doc_file(self, _pf, mock_upload, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1))
        with pytest.raises(GdocError) as exc:
            cmd_write(_write_args(f))
        assert exc.value.exit_code == 3
        mock_upload.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    def test_sync_hook(self, _ver, mock_upload, _state, tmp_path, capsys):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1))
        before = f.read_bytes()
        with patch("sys.stdin", _hook_stdin(f)):
            # Exit 2 makes Claude Code show the hook's stderr to the agent.
            assert cmd_sync_hook(SimpleNamespace(command="_sync-hook")) == 2
        mock_upload.assert_not_called()
        assert f.read_bytes() == before
        err = capsys.readouterr().err
        assert "not pushed" in err and "My Doc" in err
        assert "draft.latest.md" in err

    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_mcp_write(self, _pf, mock_upload, monkeypatch):
        monkeypatch.setattr("gdoc.util.resolve_account", lambda *a, **k: None)
        _out, err, code = mcp.call_command(
            "write", {"doc": DOC, "text": _stamped(1)},
        )
        assert code == 3
        assert "ERR:" in err
        mock_upload.assert_not_called()


# --- Stamped files that are current upload and advance ---------------------


class TestCurrentStampUploads:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=6)
    @patch(
        "gdoc.notify.pre_flight",
        # No read baseline on this machine: the stamp alone is enough.
        return_value=ChangeInfo(current_version=5, last_read_version=None),
    )
    def test_push_uploads_and_advances_stamp(
        self, _pf, mock_upload, _state, tmp_path,
    ):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(5))
        assert cmd_push(_push_args(f)) == 0
        mock_upload.assert_called_once_with(DOC, "# Draft\n\nMy local edit.\n")
        assert f.read_text() == _stamped(6)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", side_effect=[6, 7])
    @patch("gdoc.notify.pre_flight")
    def test_push_edit_push(self, mock_pf, mock_upload, _state, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(5, body="one\n"))
        mock_pf.return_value = ChangeInfo(current_version=5)
        assert cmd_push(_push_args(f)) == 0

        f.write_text(f.read_text().replace("one\n", "two\n"))
        mock_pf.return_value = ChangeInfo(current_version=6)
        assert cmd_push(_push_args(f)) == 0

        assert mock_upload.call_count == 2
        assert f.read_text() == _stamped(7, body="two\n")

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=6)
    @patch("gdoc.notify.pre_flight", return_value=ChangeInfo(current_version=5))
    def test_write_uploads_and_advances_stamp(
        self, _pf, mock_upload, _state, tmp_path,
    ):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(5))
        assert cmd_write(_write_args(f)) == 0
        mock_upload.assert_called_once()
        assert _stamp_of(f) == "6"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=6)
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 5})
    def test_sync_hook_uploads_and_advances_stamp(
        self, _ver, mock_upload, _state, tmp_path,
    ):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(5))
        with patch("sys.stdin", _hook_stdin(f)):
            cmd_sync_hook(SimpleNamespace(command="_sync-hook"))
        mock_upload.assert_called_once()
        assert _stamp_of(f) == "6"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=ChangeInfo(current_version=5))
    def test_file_changed_during_upload_keeps_old_stamp(
        self, _pf, _state, tmp_path, capsys,
    ):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(5))

        def upload(doc_id, body):
            f.write_text(_stamped(5, body="edited meanwhile\n"))
            return 6

        with patch("gdoc.api.drive.update_doc_content", side_effect=upload):
            assert cmd_push(_push_args(f)) == 0
        assert f.read_text() == _stamped(5, body="edited meanwhile\n")
        assert "gdoc-version" in capsys.readouterr().err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 9})
    @patch("gdoc.api.docs.insert_markdown_into_tab", return_value={})
    @patch("gdoc.notify.pre_flight", return_value=ChangeInfo(current_version=5))
    def test_tab_write_leaves_stamp(self, _pf, _tab, _ver, _state, tmp_path, capsys):
        """A tab write's post-upload version comes from a separate read,
        which could bless someone else's edit, so the stamp stays put."""
        f = tmp_path / "draft.md"
        f.write_text(_stamped(5))
        with patch("gdoc.cli._print_tab_write_result"):
            assert cmd_write(_write_args(f, tab="Notes")) == 0
        assert _stamp_of(f) == "5"
        assert "gdoc-version" in capsys.readouterr().err


# --- Everything else keeps the old rule -------------------------------------


class TestFallbacks:
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch(
        "gdoc.notify.pre_flight",
        return_value=ChangeInfo(current_version=2, last_read_version=None),
    )
    def test_unstamped_needs_read_baseline(self, _pf, mock_upload, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_unstamped())
        with pytest.raises(GdocError, match="no read baseline") as exc:
            cmd_push(_push_args(f))
        assert exc.value.exit_code == 3
        mock_upload.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_unstamped_uses_machine_read(self, _pf, mock_upload, _state, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_unstamped())
        assert cmd_push(_push_args(f)) == 0
        mock_upload.assert_called_once()
        assert f.read_text() == _unstamped()  # no stamp invented

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    def test_unstamped_sync_hook_unchanged(self, _ver, mock_upload, _state, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_unstamped())
        with patch("sys.stdin", _hook_stdin(f)):
            cmd_sync_hook(SimpleNamespace(command="_sync-hook"))
        mock_upload.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_force_overrides_stale_stamp(self, _pf, mock_upload, _state, tmp_path):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1))
        assert cmd_push(_push_args(f, force=True)) == 0
        mock_upload.assert_called_once()
        assert _stamp_of(f) == "3"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_stamp_for_other_doc_is_ignored(
        self, _pf, mock_upload, _state, tmp_path,
    ):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1, doc="otherdoc999"))
        # Writing into DOC: the file's stamp describes otherdoc999, so the
        # machine read baseline (version 2 == current) decides.
        assert cmd_write(_write_args(f, doc=DOC)) == 0
        mock_upload.assert_called_once()
        assert _stamp_of(f) == "1"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.update_doc_content", return_value=3)
    @patch("gdoc.notify.pre_flight", return_value=_machine_read(2))
    def test_stale_stamp_but_doc_already_matches(
        self, _pf, mock_upload, _state, tmp_path, capsys,
    ):
        f = tmp_path / "draft.md"
        f.write_text(_stamped(1, body="# Collaborator text\n"))
        with patch("gdoc.api.drive.get_file_version", return_value={"version": 2}):
            assert cmd_push(_push_args(f)) == 0
        mock_upload.assert_not_called()
        assert "in sync" in capsys.readouterr().out


# --- pull writes the stamp --------------------------------------------------


class TestPullStamp:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=ChangeInfo(current_version=42))
    def test_pull_stamps_version_read_before_export(self, _pf, _state, tmp_path):
        calls = []

        def info(doc_id):
            calls.append("info")
            return {"name": "My Doc", "version": 42}

        def export(doc_id, mime_type):
            calls.append("export")
            return "# Hello\n"

        f = tmp_path / "out.md"
        with (
            patch("gdoc.api.drive.get_file_info", side_effect=info),
            patch("gdoc.api.drive.export_doc", side_effect=export),
        ):
            cmd_pull(SimpleNamespace(
                command="pull", doc=DOC, file=str(f), json=False,
                verbose=False, quiet=False, revision=None,
            ))
        # A version read after the export could name an edit the file lacks.
        assert calls == ["info", "export"]
        meta, body = parse_frontmatter(f.read_text())
        assert meta["gdoc-version"] == "42"
        assert meta["gdoc"] == DOC
        assert body == "# Hello\n"

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.revisions.export_revision", return_value="# Old\n")
    @patch("gdoc.cli._resolve_revision", return_value={"id": "7"})
    @patch("gdoc.api.drive.get_file_info", return_value={"name": "D", "version": 42})
    @patch("gdoc.notify.pre_flight", return_value=ChangeInfo(current_version=42))
    def test_revision_pull_has_no_stamp(
        self, _pf, _info, _rev, _export, _state, tmp_path,
    ):
        f = tmp_path / "old.md"
        cmd_pull(SimpleNamespace(
            command="pull", doc=DOC, file=str(f), json=False,
            verbose=False, quiet=False, revision="7",
        ))
        meta, _ = parse_frontmatter(f.read_text())
        assert "gdoc-version" not in meta

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_info", return_value={"name": "D", "version": 55})
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 55})
    def test_pull_hook_stamps_and_trusts_stamp_over_state(
        self, _ver, _info, _state, tmp_path,
    ):
        from gdoc.state import save_state

        # The machine already read 55, but this file is based on 50.
        save_state(DOC, DocState(last_version=55, last_read_version=55))
        f = tmp_path / "draft.md"
        f.write_text(_stamped(50))
        with patch("sys.stdin", _hook_stdin(f)):
            cmd_pull_hook(SimpleNamespace(command="_pull-hook"))
        meta, body = parse_frontmatter(f.read_text())
        assert meta["gdoc-version"] == "55"
        assert body == "# Collaborator text\n"

