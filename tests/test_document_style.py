"""Whole-doc writes keep the document's style across Drive's markdown import.

Drive's import resets documentStyle (pageless becomes paged, margins go to
their defaults), so these run against a fake doc whose import does the same,
with only the Google API services replaced.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.drive import update_doc_content
from gdoc.cli import cmd_push, cmd_sync_hook, cmd_write
from gdoc.frontmatter import parse_frontmatter
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError

DOC = "abc123"
PAGELESS = {"documentFormat": {"documentMode": "PAGELESS"}}
IMPORT_VERSION = 11
RESTORED_VERSION = 12


def _http_error(status):
    return HttpError(httplib2.Response({"status": str(status)}), b"")


class _FakeGoogleDoc:
    """A Doc at version 10 whose markdown import resets documentStyle."""

    IMPORT_DEFAULTS = {
        "documentFormat": {"documentMode": "PAGES"},
        "background": {"color": {}},
        "marginTop": {"magnitude": 72, "unit": "PT"},
    }

    def __init__(self, style):
        self.style = style
        self.version = 10
        self.style_writes = []
        self.fail_style_write = False

        self.docs = MagicMock()
        documents = self.docs.documents.return_value
        documents.get.return_value.execute.side_effect = (
            lambda **_: {"documentStyle": dict(self.style)}
        )
        documents.batchUpdate.side_effect = lambda **kwargs: MagicMock(
            execute=lambda **_: self._apply(kwargs["body"]),
        )

        self.drive = MagicMock()
        files = self.drive.files.return_value
        files.update.return_value.execute.side_effect = self._import
        files.get.return_value.execute.side_effect = (
            lambda: {"version": str(self.version)}
        )

    def _import(self):
        self.style = dict(self.IMPORT_DEFAULTS)
        self.version += 1
        return {"version": str(self.version)}

    def _apply(self, body):
        if self.fail_style_write:
            raise _http_error(500)
        request = body["requests"][0]["updateDocumentStyle"]
        self.style_writes.append(request)
        for name in request["fields"].split(","):
            if name in request["documentStyle"]:
                self.style[name] = request["documentStyle"][name]
            else:
                self.style.pop(name, None)
        self.version += 1
        return {}


@pytest.fixture
def fake_doc():
    """Build a _FakeGoogleDoc wired in as both API services."""
    started = []

    def build(style):
        doc = _FakeGoogleDoc(style)
        for target, service in (
            ("gdoc.api.docs.get_docs_service", doc.docs),
            ("gdoc.api.drive.get_drive_service", doc.drive),
        ):
            patcher = patch(target, return_value=service)
            patcher.start()
            started.append(patcher)
        return doc

    yield build
    for patcher in started:
        patcher.stop()


class TestUpdateDocContent:
    def test_pageless_stays_pageless(self, fake_doc):
        doc = fake_doc(dict(PAGELESS))

        update_doc_content(DOC, "# Hello")

        assert doc.style["documentFormat"] == {"documentMode": "PAGELESS"}

    def test_returns_version_after_the_restore(self, fake_doc):
        fake_doc(dict(PAGELESS))

        # Returning the import's version would make the next command report
        # our own restore as somebody else's edit.
        assert update_doc_content(DOC, "# Hello") == RESTORED_VERSION

    def test_restores_other_style_fields(self, fake_doc):
        background = {"color": {"color": {"rgbColor": {"red": 1}}}}
        doc = fake_doc({
            "documentFormat": {"documentMode": "PAGES"},
            "background": background,
            "marginTop": {"magnitude": 20, "unit": "PT"},
            "flipPageOrientation": True,
        })

        update_doc_content(DOC, "# Hello")

        assert doc.style["background"] == background
        assert doc.style["marginTop"] == {"magnitude": 20, "unit": "PT"}
        assert doc.style["flipPageOrientation"] is True
        assert doc.style["documentFormat"] == {"documentMode": "PAGES"}

    def test_field_mask_names_only_what_changed(self, fake_doc):
        doc = fake_doc({
            **_FakeGoogleDoc.IMPORT_DEFAULTS,
            "documentFormat": {"documentMode": "PAGELESS"},
        })

        update_doc_content(DOC, "# Hello")

        assert [w["fields"] for w in doc.style_writes] == ["documentFormat"]

    def test_field_the_import_set_is_cleared_when_it_was_unset(self, fake_doc):
        doc = fake_doc(dict(PAGELESS))

        update_doc_content(DOC, "# Hello")

        assert "marginTop" not in doc.style

    def test_read_only_fields_are_not_written(self, fake_doc):
        doc = fake_doc({
            **_FakeGoogleDoc.IMPORT_DEFAULTS,
            "useCustomHeaderFooterMargins": True,
            "defaultHeaderId": "kix.header",
        })

        update_doc_content(DOC, "# Hello")

        assert doc.style_writes == []

    @pytest.mark.parametrize("background", [
        {"color": {}},
        {"color": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}},
    ])
    def test_unchanged_style_makes_no_extra_write(self, fake_doc, background):
        doc = fake_doc({
            **_FakeGoogleDoc.IMPORT_DEFAULTS,
            "background": background,
        })

        # A default doc keeps the version files.update returned atomically.
        assert update_doc_content(DOC, "# Hello") == IMPORT_VERSION
        assert doc.style_writes == []

    def test_black_background_is_not_mistaken_for_blank(self, fake_doc):
        black = {"color": {"color": {"rgbColor": {}}}}
        doc = fake_doc({**_FakeGoogleDoc.IMPORT_DEFAULTS, "background": black})

        update_doc_content(DOC, "# Hello")

        assert doc.style["background"] == black

    def test_failed_style_read_writes_nothing(self, fake_doc):
        doc = fake_doc(dict(PAGELESS))
        get = doc.docs.documents.return_value.get.return_value
        get.execute.side_effect = _http_error(403)

        with pytest.raises(GdocError, match="Permission denied"):
            update_doc_content(DOC, "# Hello")

        doc.drive.files.return_value.update.assert_not_called()

    def test_failed_restore_warns_with_the_old_style(self, fake_doc, capsys):
        doc = fake_doc(dict(PAGELESS))
        doc.fail_style_write = True

        # The content landed, so the caller must still record that write.
        assert update_doc_content(DOC, "# Hello") == IMPORT_VERSION
        err = capsys.readouterr().err
        assert "could not be restored" in err
        assert "PAGELESS" in err

    def test_failed_version_read_warns_and_uses_the_import(
        self, fake_doc, capsys,
    ):
        doc = fake_doc(dict(PAGELESS))
        get = doc.drive.files.return_value.get.return_value
        get.execute.side_effect = _http_error(500)

        assert update_doc_content(DOC, "# Hello") == IMPORT_VERSION
        assert doc.style["documentFormat"] == {"documentMode": "PAGELESS"}
        assert "may report this write as an edit" in capsys.readouterr().err


def _stamped_file(tmp_path, version=10):
    path = tmp_path / "draft.md"
    path.write_text(
        f"---\ngdoc: {DOC}\ntitle: My Doc\ngdoc-version: {version}\n---\n# Hi\n"
    )
    return path


def _stamp_of(path):
    return parse_frontmatter(path.read_text())[0].get("gdoc-version")


@pytest.fixture
def pageless_doc(fake_doc):
    """A pageless doc this machine last read at version 10."""
    doc = fake_doc(dict(PAGELESS))
    change_info = ChangeInfo(current_version=10, last_read_version=10)
    with (
        patch("gdoc.api.docs.count_document_tabs", return_value=1),
        patch("gdoc.notify.pre_flight", return_value=change_info),
        patch("gdoc.state.update_state_after_command") as record,
    ):
        doc.record_state = record
        yield doc


class TestCommandsKeepPageless:
    """Each whole-doc command stays pageless and records the final version."""

    def _assert_kept(self, doc, path):
        assert doc.style["documentFormat"] == {"documentMode": "PAGELESS"}
        recorded = doc.record_state.call_args.kwargs["command_version"]
        assert recorded == RESTORED_VERSION
        assert _stamp_of(path) == str(RESTORED_VERSION)

    def test_write(self, pageless_doc, tmp_path):
        path = _stamped_file(tmp_path)
        args = SimpleNamespace(
            command="write", doc=DOC, file=str(path), force=False,
            json=False, verbose=False, quiet=False, tab=None,
            force_collapse_tabs=False,
        )

        assert cmd_write(args) == 0

        self._assert_kept(pageless_doc, path)

    def test_push(self, pageless_doc, tmp_path):
        path = _stamped_file(tmp_path)
        args = SimpleNamespace(
            command="push", file=str(path), force=False,
            force_collapse_tabs=False, json=False, verbose=False, quiet=False,
        )

        assert cmd_push(args) == 0

        self._assert_kept(pageless_doc, path)

    def test_sync_hook(self, pageless_doc, tmp_path):
        path = _stamped_file(tmp_path)
        stdin = io.StringIO(json.dumps({"tool_input": {"file_path": str(path)}}))

        with patch("sys.stdin", stdin):
            assert cmd_sync_hook(SimpleNamespace()) == 0

        self._assert_kept(pageless_doc, path)
