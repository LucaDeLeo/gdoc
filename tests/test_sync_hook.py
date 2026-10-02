"""Tests for the `gdoc _sync-hook` command handler."""

import io
import json
from types import SimpleNamespace
from unittest.mock import ANY, patch

import pytest

from gdoc.cli import cmd_sync_hook


def _make_args():
    return SimpleNamespace(command="_sync-hook")


def _stdin_json(file_path):
    data = {"tool_input": {"file_path": file_path}}
    return io.StringIO(json.dumps(data))


@pytest.fixture(autouse=True)
def _stub_single_tab(mocker):
    """Use a plain single-tab snapshot unless a test overrides the read."""
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r1", "tabs": [{
            "tabProperties": {"tabId": "main", "title": "Main"},
            "documentTab": {"body": {"content": []}},
        }],
    })
    from gdoc.state import record_content_read
    record_content_read("abc123", ["main"], "r1")
    # The hook pairs its safety snapshot with a version captured first.
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 1})


class TestSyncHookBasic:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    def test_sync_pushes_file(
        self, mock_update_doc, _drv, _update, tmp_path, capsys,
    ):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n"
                     "title: My Doc\n---\n# Hello\n")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0
        mock_update_doc.assert_called_once_with(
            "abc123", "# Hello\n", expected_version=1, document=ANY,
            allow_lossy=False, collapse_tabs=False, result_details=ANY,
            image_aliases={},
        )
        err = capsys.readouterr().err
        assert "SYNC:" in err
        assert "My Doc" in err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    def test_sync_strips_frontmatter(
        self, mock_update_doc, _drv, _update, tmp_path,
    ):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\ntitle: T\n---\nBody text")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            cmd_sync_hook(args)
        mock_update_doc.assert_called_once_with(
            "abc123", "Body text", expected_version=1, document=ANY,
            allow_lossy=False, collapse_tabs=False, result_details=ANY,
            image_aliases={},
        )

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.update_doc_content", return_value=42)
    def test_sync_updates_state(
        self, mock_update_doc, _drv, mock_update, tmp_path,
    ):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\ntitle: T\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            cmd_sync_hook(args)
        mock_update.assert_called_once_with(
            "abc123", None, command="push",
            quiet=True, command_version=42,
        )


class TestSyncHookSkips:
    def test_skip_non_md_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("---\ngdoc: abc\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_missing_file(self):
        args = _make_args()
        with patch("sys.stdin", _stdin_json("/nonexistent/file.md")):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_no_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("# No frontmatter\nJust text.")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_no_gdoc_key(self, tmp_path):
        f = tmp_path / "other.md"
        f.write_text("---\ntitle: Foo\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_empty_stdin(self):
        args = _make_args()
        with patch("sys.stdin", io.StringIO("")):
            rc = cmd_sync_hook(args)
        assert rc == 0

    def test_skip_no_file_path_in_json(self):
        args = _make_args()
        with patch("sys.stdin", io.StringIO('{"tool_input": {}}')):
            rc = cmd_sync_hook(args)
        assert rc == 0


class TestSyncHookErrorHandling:
    def test_never_raises(self):
        """The sync hook must always return 0, even on errors."""
        args = _make_args()
        with patch("sys.stdin", io.StringIO("not json")):
            rc = cmd_sync_hook(args)
        assert rc == 0

    @patch(
        "gdoc.api.drive.update_doc_content",
        side_effect=Exception("API failure"),
    )
    def test_api_error_reported(self, _update_doc, tmp_path, capsys):
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\ntitle: T\n---\nBody")
        args = _make_args()
        with patch("sys.stdin", _stdin_json(str(f))):
            rc = cmd_sync_hook(args)
        assert rc == 0
        assert "SYNC: failed: API failure" in capsys.readouterr().err


class TestSyncHookMultiTabSafety:
    """Sync hook must not silently flatten a multi-tab doc."""

    def test_preserves_sibling_tabs(self, mocker, tmp_path):
        document = {"revisionId": "r1", "tabs": [
            {"tabProperties": {"tabId": tab, "title": tab},
             "documentTab": {"body": {"content": []}}}
            for tab in ("main", "sibling")
        ]}
        mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=document)
        write = mocker.patch("gdoc.api.drive.update_doc_content", return_value=42)
        mocker.patch("gdoc.state.update_state_after_command")
        f = tmp_path / "spec.md"
        f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n"
                     "title: My Doc\n---\n# Hello\n")
        with patch("sys.stdin", _stdin_json(str(f))):
            assert cmd_sync_hook(_make_args()) == 0
        write.assert_called_once_with(
            "abc123", "# Hello\n", expected_version=1, document=document,
            allow_lossy=False, collapse_tabs=False, result_details=ANY,
            image_aliases={},
        )


def test_sync_refuses_lossy_scope(mocker, tmp_path, capsys):
    f = tmp_path / "spec.md"
    f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n---\nBody", encoding="utf-8")
    mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value={
        "revisionId": "r1", "tabs": [{
            "tabProperties": {"tabId": "main", "title": "Main"},
            "documentTab": {"body": {"content": [
                {"startIndex": 1, "endIndex": 3, "paragraph": {"elements": [
                    {"startIndex": 1, "endIndex": 2, "person": {}},
                    {"startIndex": 2, "endIndex": 3,
                     "textRun": {"content": "\n"}},
                ]}},
            ]}},
        }],
    })
    service = mocker.patch("gdoc.api.docs.get_docs_service")
    state = mocker.patch("gdoc.state.update_state_after_command")
    with patch("sys.stdin", _stdin_json(str(f))):
        assert cmd_sync_hook(_make_args()) == 0
    service.return_value.documents.return_value.batchUpdate.assert_not_called()
    state.assert_not_called()
    assert "Markdown replacement refused" in capsys.readouterr().err


@pytest.mark.parametrize("error", [RuntimeError("offline"), OSError("read failed")])
def test_sync_safety_read_failure_is_visible(mocker, tmp_path, capsys, error):
    f = tmp_path / "spec.md"
    f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n---\nBody", encoding="utf-8")
    mocker.patch("gdoc.api.docs.get_document_with_tabs", side_effect=error)
    upload = mocker.patch("gdoc.api.drive.update_doc_content")
    with patch("sys.stdin", _stdin_json(str(f))):
        assert cmd_sync_hook(_make_args()) == 0
    upload.assert_not_called()
    assert "SYNC: failed" in capsys.readouterr().err


def test_sync_uses_one_safety_snapshot(mocker, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n---\nBody", encoding="utf-8")
    fetch = mocker.patch("gdoc.api.docs.get_document_with_tabs", side_effect=[
        {"revisionId": "r1", "tabs": [{
            "tabProperties": {"tabId": "main", "title": "Main"},
            "documentTab": {"body": {"content": []}},
        }]}, {"tabs": [{}, {}]},
    ])
    upload = mocker.patch("gdoc.api.drive.update_doc_content", return_value=42)
    mocker.patch("gdoc.state.update_state_after_command")
    with patch("sys.stdin", _stdin_json(str(f))):
        assert cmd_sync_hook(_make_args()) == 0
    fetch.assert_called_once_with("abc123")
    upload.assert_called_once()


@pytest.mark.parametrize("exit_code", [1, 2, 3])
def test_sync_reports_refusals_separately_from_failures(
    mocker, tmp_path, capsys, exit_code,
):
    from gdoc.util import GdocError

    f = tmp_path / "spec.md"
    original = "---\ngdoc: abc123\ngdoc-revision: r1\n---\nBody"
    f.write_text(original)
    mocker.patch(
        "gdoc.cli._write_native_markdown",
        side_effect=GdocError("mutation outcome", exit_code),
    )
    with patch("sys.stdin", _stdin_json(str(f))):
        assert cmd_sync_hook(_make_args()) == 0
    error = capsys.readouterr().err
    assert ("SYNC: skipped" if exit_code == 3 else "ERR: SYNC: failed") in error
    assert f.read_text() == original


@pytest.mark.parametrize("acknowledged,rebased", [("r2", False), ("", False),
                                                  ("r2", True)])
def test_sync_file_revision_requires_known_acknowledged_content(
    mocker, tmp_path, acknowledged, rebased,
):
    from gdoc.frontmatter import parse_frontmatter

    f = tmp_path / "spec.md"
    f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n---\nNew body")

    def upload(*args, result_details, **kwargs):
        result_details.update(
            input_revision_id="r1", acknowledged_revision_id=acknowledged,
            rebased=rebased,
        )
        return 42

    mocker.patch("gdoc.api.drive.update_doc_content", side_effect=upload)
    mocker.patch("gdoc.state.update_state_after_command")
    with patch("sys.stdin", _stdin_json(str(f))):
        assert cmd_sync_hook(_make_args()) == 0
    metadata, body = parse_frontmatter(f.read_text())
    assert metadata["gdoc-revision"] == (
        "r2" if acknowledged and not rebased else "r1"
    )
    assert body == "New body"


def test_sync_does_not_replace_a_concurrent_local_edit(mocker, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("---\ngdoc: abc123\ngdoc-revision: r1\n---\nNew body")
    concurrent = "---\ngdoc: abc123\ngdoc-revision: r1\n---\nLater local edit"

    def upload(*args, result_details, **kwargs):
        f.write_text(concurrent)
        result_details.update(acknowledged_revision_id="r2", rebased=False)
        return 42

    mocker.patch("gdoc.api.drive.update_doc_content", side_effect=upload)
    mocker.patch("gdoc.state.update_state_after_command")
    with patch("sys.stdin", _stdin_json(str(f))):
        assert cmd_sync_hook(_make_args()) == 0
    assert f.read_text() == concurrent


@patch("gdoc.api.drive.update_doc_content")
def test_refused_sync_reaches_the_agent_as_hook_context(mock_update, tmp_path, capsys):
    f = tmp_path / "old.md"
    f.write_text("---\ngdoc: abc123\ntitle: Old\n---\nAgent edit\n")
    data = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": str(f)}}
    with patch("sys.stdin", io.StringIO(json.dumps(data))):
        assert cmd_sync_hook(_make_args()) == 0
    mock_update.assert_not_called()
    out, err = capsys.readouterr()
    context = json.loads(out)["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse"
    assert "SYNC: skipped" in context["additionalContext"] and str(f) in err
    assert f.read_text().endswith("Agent edit\n")
