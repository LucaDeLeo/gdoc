"""Stale-file protection for files an older gdoc pulled (0.21.1 to 0.22.0),
stamped only with `gdoc-version`, and the merged rules for files `pull` now
writes with `gdoc-revision` and a tab fingerprint.

The scenario the older tests pin down:

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
    @pytest.fixture(autouse=True)
    def _no_native_tabs(self):
        # The in-sync check also reads the doc's first tab natively.
        with patch("gdoc.api.docs.get_document_with_tabs",
                   return_value={"tabs": []}):
            yield

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


class TestPullStamp:
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


# The merged rules: a file pulled by this gdoc carries `gdoc-revision` and a
# tab fingerprint, which the native write checks and refreshes; a file an
# older gdoc stamped with only `gdoc-version` is refused while stale, and
# otherwise needs a fresh pull or --force like any file without a revision.

def _native_pull(monkeypatch, tmp_path):
    from gdoc import cli
    from tests.acceptance.test_round5_workflows import NativeRoute
    from tests.native_model import NativeDoc

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    # The Drive export an older gdoc pulled from, for old-style files.
    monkeypatch.setattr("gdoc.api.drive.export_doc",
                        lambda *a, **k: "Alpha.\n")
    pulled = tmp_path / "d.md"
    assert cli.run_argv(["pull", "synthetic", str(pulled)], check_updates=False) == 0
    return route, pulled


def _run(*argv):
    from gdoc import cli

    try:
        return cli.run_argv(list(argv), check_updates=False)
    except SystemExit as exc:
        return exc.code


def _old_style(pulled, version, body):
    pulled.write_text(f"---\ngdoc: synthetic\ntitle: T\ngdoc-version: {version}\n"
                      f"---\n{body}")


class TestMergedRules:
    def test_pull_records_revision_provenance_not_a_version(self, monkeypatch,
                                                           tmp_path):
        _, pulled = _native_pull(monkeypatch, tmp_path)
        text = pulled.read_text()
        assert "gdoc-revision:" in text and "gdoc-tab-sha256:" in text
        assert "gdoc-version:" not in text

    def test_new_style_file_ignores_a_version_key(self, monkeypatch, tmp_path):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        text = pulled.read_text().replace("---\n", "---\ngdoc-version: 1\n", 1)
        pulled.write_text(text.replace("Alpha.", "Beta."))
        route.service.revision += 5  # a Drive version the stamp never saw
        assert _run("write", "synthetic", str(pulled)) == 0
        assert _run("push", str(pulled)) == 0

    @pytest.mark.parametrize("command", ["write", "push"])
    def test_stale_old_style_file_is_refused(self, monkeypatch, tmp_path,
                                             capsys, command):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision - 1, "Beta.\n")
        batches = len(route.service.batches)
        argv = (["write", "synthetic", str(pulled)] if command == "write"
                else ["push", str(pulled)])
        assert _run(*argv) == 3
        assert "is from doc version" in capsys.readouterr().err
        assert len(route.service.batches) == batches

    @pytest.mark.parametrize("command", ["write", "push"])
    def test_current_old_style_file_needs_a_fresh_pull(self, monkeypatch,
                                                       tmp_path, capsys, command):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision, "Beta.\n")
        batches = len(route.service.batches)
        argv = (["write", "synthetic", str(pulled)] if command == "write"
                else ["push", str(pulled)])
        assert _run(*argv) == 3
        assert "is from doc version" not in capsys.readouterr().err
        assert len(route.service.batches) == batches

    def test_force_writes_an_old_style_file(self, monkeypatch, tmp_path):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision - 1, "Beta.\n")
        assert _run("write", "synthetic", str(pulled), "--force") == 0
        assert "Beta." in route.ok("cat")

    @pytest.mark.parametrize("command", ["write", "push"])
    def test_stale_old_style_file_matching_the_doc_is_in_sync(
            self, monkeypatch, tmp_path, capsys, command):
        """As 0.21.1 did: nothing to write, exit 0."""
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision - 1, "Alpha.\n")
        batches = len(route.service.batches)
        argv = (["write", "synthetic", str(pulled)] if command == "write"
                else ["push", str(pulled)])
        assert _run(*argv) == 0
        assert "already in sync" in capsys.readouterr().out
        assert len(route.service.batches) == batches

    def _hook(self, monkeypatch, pulled):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"tool_input": {"file_path": str(pulled)}})))
        return cmd_pull_hook(SimpleNamespace())

    def test_pull_hook_blocks_a_stale_old_style_file(self, monkeypatch, tmp_path,
                                                     capsys):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision - 1, "Local edit.\n")
        before = pulled.read_text()
        assert self._hook(monkeypatch, pulled) == 2
        assert pulled.read_text() == before
        assert "is from doc version" in capsys.readouterr().err

    def test_pull_hook_restamps_a_matching_old_style_file(self, monkeypatch,
                                                          tmp_path):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision - 1, "Alpha.\n")
        assert self._hook(monkeypatch, pulled) == 0
        text = pulled.read_text()
        assert "gdoc-revision:" in text and "Alpha." in text

    def test_pull_hook_skips_a_current_old_style_file_with_edits(
            self, monkeypatch, tmp_path, capsys):
        route, pulled = _native_pull(monkeypatch, tmp_path)
        _old_style(pulled, route.service.revision, "Local edit.\n")
        before = pulled.read_text()
        assert self._hook(monkeypatch, pulled) == 0
        assert pulled.read_text() == before
        assert "pull skipped" in capsys.readouterr().err
