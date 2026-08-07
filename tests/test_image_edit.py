"""Tests for insert-image/replace-image commands and their API wrappers."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.api.docs import find_object_tab, insert_inline_image, replace_image
from gdoc.cli import cmd_insert_image, cmd_replace_image
from gdoc.util import GdocError

IMG_URL = "https://example.org/pic.png"


def _http_error(status, content=b""):
    resp = httplib2.Response({"status": str(status)})
    resp.reason = "Error"
    return HttpError(resp, content, uri="")


def _mock_docs_service(batch_response=None, batch_error=None):
    """Docs service mock whose batchUpdate().execute() returns or raises."""
    service = MagicMock()
    execute = service.documents.return_value.batchUpdate.return_value.execute
    if batch_error is not None:
        execute.side_effect = batch_error
    else:
        execute.return_value = batch_response or {}
    return service


_INSERT_OK = {
    "replies": [{"insertInlineImage": {"objectId": "kix.newimg"}}],
}


class TestInsertInlineImage:
    @patch("gdoc.api.docs.get_docs_service")
    def test_happy_path_returns_object_id(self, mock_svc):
        service = _mock_docs_service(batch_response=_INSERT_OK)
        mock_svc.return_value = service

        result = insert_inline_image("doc1", IMG_URL, 19)

        assert result == "kix.newimg"
        call = service.documents.return_value.batchUpdate.call_args
        assert call.kwargs["documentId"] == "doc1"
        assert call.kwargs["body"] == {
            "requests": [
                {
                    "insertInlineImage": {
                        "location": {"index": 19},
                        "uri": IMG_URL,
                    }
                }
            ]
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_tab_revision_and_size_in_request(self, mock_svc):
        service = _mock_docs_service(batch_response=_INSERT_OK)
        mock_svc.return_value = service

        insert_inline_image(
            "doc1", IMG_URL, 19,
            tab_id="t2", revision_id="rev9",
            width_pt=200.0, height_pt=100.0,
        )

        body = service.documents.return_value.batchUpdate.call_args.kwargs[
            "body"
        ]
        assert body["writeControl"] == {"requiredRevisionId": "rev9"}
        request = body["requests"][0]["insertInlineImage"]
        assert request["location"] == {"index": 19, "tabId": "t2"}
        assert request["objectSize"] == {
            "width": {"magnitude": 200.0, "unit": "PT"},
            "height": {"magnitude": 100.0, "unit": "PT"},
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_revision_mismatch_400_is_clear_error(self, mock_svc):
        content = (
            b'{"error": {"code": 400, "message": "The provided revision ID '
            b'does not match the latest revision of the document."}}'
        )
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(400, content),
        )
        with pytest.raises(GdocError, match="document changed"):
            insert_inline_image("doc1", IMG_URL, 19, revision_id="rev9")

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_raises_gdoc_error(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(
            batch_error=_http_error(404),
        )
        with pytest.raises(GdocError, match="not found"):
            insert_inline_image("doc1", IMG_URL, 19)

    @patch("gdoc.api.docs.get_docs_service")
    def test_empty_replies_returns_empty_id(self, mock_svc):
        mock_svc.return_value = _mock_docs_service(batch_response={})
        assert insert_inline_image("doc1", IMG_URL, 19) == ""


class TestReplaceImage:
    @patch("gdoc.api.docs.get_docs_service")
    def test_request_shape(self, mock_svc):
        service = _mock_docs_service(batch_response={})
        mock_svc.return_value = service

        replace_image(
            "doc1", "kix.img1", IMG_URL, tab_id="t2", revision_id="rev9",
        )

        call = service.documents.return_value.batchUpdate.call_args
        assert call.kwargs["body"] == {
            "requests": [
                {
                    "replaceImage": {
                        "imageObjectId": "kix.img1",
                        "uri": IMG_URL,
                        "imageReplaceMethod": "CENTER_CROP",
                        "tabId": "t2",
                    }
                }
            ],
            "writeControl": {"requiredRevisionId": "rev9"},
        }

    @patch("gdoc.api.docs.get_docs_service")
    def test_no_tab_no_revision_omits_fields(self, mock_svc):
        service = _mock_docs_service(batch_response={})
        mock_svc.return_value = service

        replace_image("doc1", "kix.img1", IMG_URL)

        body = service.documents.return_value.batchUpdate.call_args.kwargs[
            "body"
        ]
        assert "writeControl" not in body
        assert "tabId" not in body["requests"][0]["replaceImage"]


def _tab(tab_id, text, start=1, title=None, inline_objects=None,
         positioned_objects=None, children=None):
    """Build a Docs API tab dict with one paragraph of text."""
    tab = {
        "tabProperties": {
            "tabId": tab_id, "title": title or tab_id, "index": 0,
        },
        "documentTab": {
            "body": {
                "content": [
                    {
                        "endIndex": start + len(text),
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": start,
                                    "textRun": {"content": text},
                                }
                            ]
                        },
                    }
                ]
            },
        },
    }
    if inline_objects:
        tab["documentTab"]["inlineObjects"] = inline_objects
    if positioned_objects:
        tab["documentTab"]["positionedObjects"] = positioned_objects
    if children:
        tab["childTabs"] = children
    return tab


class TestFindObjectTab:
    def test_found_in_second_tab(self):
        doc = {
            "tabs": [
                _tab("t1", "one\n"),
                _tab("t2", "two\n", inline_objects={"kix.img1": {}}),
            ]
        }
        assert find_object_tab(doc, "kix.img1") == "t2"

    def test_found_in_child_tab(self):
        doc = {
            "tabs": [
                _tab("t1", "one\n", children=[
                    _tab("t1c", "child\n", inline_objects={"kix.img1": {}}),
                ]),
            ]
        }
        assert find_object_tab(doc, "kix.img1") == "t1c"

    def test_positioned_object_found(self):
        doc = {
            "tabs": [
                _tab("t1", "one\n", positioned_objects={"kix.pos1": {}}),
            ]
        }
        assert find_object_tab(doc, "kix.pos1") == "t1"

    def test_not_found_returns_none(self):
        doc = {"tabs": [_tab("t1", "one\n")]}
        assert find_object_tab(doc, "kix.other") is None


# "Intro Architecture Detail\n": "Architecture" starts at doc index 7
# (start=1 + offset 6) and ends at 19; body endIndex is 27.
_ONE_TAB_DOC = {
    "revisionId": "rev1",
    "tabs": [_tab("t1", "Intro Architecture Detail\n")],
}

_TWO_TAB_DOC = {
    "revisionId": "rev1",
    "tabs": [
        _tab("t1", "Intro only\n"),
        _tab("t2", "Architecture here\n", title="Notes"),
    ],
}


def _make_args(command, **overrides):
    defaults = {
        "command": command,
        "doc": "doc123",
        "image": IMG_URL,
        "tab": None,
        "after": None,
        "index": None,
        "end": False,
        "width": None,
        "height": None,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
@patch("gdoc.api.docs.insert_inline_image", return_value="kix.newimg")
@patch("gdoc.api.docs.get_document_with_tabs", return_value=_ONE_TAB_DOC)
class TestCmdInsertImage:
    def test_after_anchor_inserts_at_end_index(
        self, mock_get, mock_insert, _ver, mock_update, capsys,
    ):
        args = _make_args("insert-image", after="Architecture")
        rc = cmd_insert_image(args)
        assert rc == 0
        mock_insert.assert_called_once_with(
            "doc123", IMG_URL, 19,
            tab_id="t1", revision_id="rev1",
            width_pt=None, height_pt=None,
        )
        assert "OK inserted image kix.newimg" in capsys.readouterr().out
        assert mock_update.call_args.kwargs["command_version"] == 7

    def test_normalized_anchor_fallback(
        self, mock_get, mock_insert, _ver, _update,
    ):
        mock_get.return_value = {
            "revisionId": "rev1",
            "tabs": [_tab("t1", "It’s here\n")],
        }
        args = _make_args("insert-image", after="It's")
        rc = cmd_insert_image(args)
        assert rc == 0
        assert mock_insert.call_args.args[2] == 5  # after "It's"

    def test_anchor_not_found(self, mock_get, mock_insert, _ver, _update):
        args = _make_args("insert-image", after="Nonexistent")
        with pytest.raises(GdocError, match="anchor not found") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3
        mock_insert.assert_not_called()

    def test_ambiguous_anchor(self, mock_get, mock_insert, _ver, _update):
        mock_get.return_value = {
            "revisionId": "rev1",
            "tabs": [_tab("t1", "foo bar foo\n")],
        }
        args = _make_args("insert-image", after="foo")
        with pytest.raises(GdocError, match="ambiguous") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3

    def test_multi_tab_requires_tab_flag(
        self, mock_get, mock_insert, _ver, _update,
    ):
        mock_get.return_value = _TWO_TAB_DOC
        args = _make_args("insert-image", after="Architecture")
        with pytest.raises(GdocError, match="specify --tab") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3

    def test_tab_flag_selects_tab(
        self, mock_get, mock_insert, _ver, _update,
    ):
        mock_get.return_value = _TWO_TAB_DOC
        args = _make_args("insert-image", after="Architecture", tab="Notes")
        rc = cmd_insert_image(args)
        assert rc == 0
        assert mock_insert.call_args.kwargs["tab_id"] == "t2"

    def test_index_used_directly(self, mock_get, mock_insert, _ver, _update):
        args = _make_args("insert-image", index=5)
        cmd_insert_image(args)
        assert mock_insert.call_args.args[2] == 5

    def test_index_zero_rejected(self, mock_get, mock_insert, _ver, _update):
        args = _make_args("insert-image", index=0)
        with pytest.raises(GdocError, match="--index") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3

    def test_end_appends_before_final_newline(
        self, mock_get, mock_insert, _ver, _update,
    ):
        args = _make_args("insert-image", end=True)
        cmd_insert_image(args)
        # body endIndex 27 → insert at 26, the segment's closing newline
        assert mock_insert.call_args.args[2] == 26

    def test_width_height_passed(self, mock_get, mock_insert, _ver, _update):
        args = _make_args(
            "insert-image", index=5, width=200.0, height=100.0,
        )
        cmd_insert_image(args)
        assert mock_insert.call_args.kwargs["width_pt"] == 200.0
        assert mock_insert.call_args.kwargs["height_pt"] == 100.0

    def test_json_output(self, mock_get, mock_insert, _ver, _update, capsys):
        args = _make_args("insert-image", after="Architecture", json=True)
        cmd_insert_image(args)
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["object_id"] == "kix.newimg"
        assert data["tab"] == "t1"
        assert data["index"] == 19

    @patch("gdoc.api.drive.delete_file")
    @patch("gdoc.api.drive.upload_temp_image",
           return_value={"id": "tmp1", "webContentLink": "https://dl/tmp1"})
    def test_local_file_uploaded_and_cleaned_up(
        self, mock_upload, mock_delete, mock_get, mock_insert, _ver, _update,
        tmp_path,
    ):
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG")
        args = _make_args("insert-image", image=str(img), index=5)
        rc = cmd_insert_image(args)
        assert rc == 0
        mock_upload.assert_called_once_with(str(img), "image/png")
        assert mock_insert.call_args.args[1] == "https://dl/tmp1"
        mock_delete.assert_called_once_with("tmp1")

    @patch("gdoc.api.drive.delete_file")
    @patch("gdoc.api.drive.upload_temp_image",
           return_value={"id": "tmp1", "webContentLink": "https://dl/tmp1"})
    def test_temp_cleaned_up_when_insert_fails(
        self, mock_upload, mock_delete, mock_get, mock_insert, _ver, _update,
        tmp_path,
    ):
        mock_insert.side_effect = GdocError("API error (400): bad image")
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG")
        args = _make_args("insert-image", image=str(img), index=5)
        with pytest.raises(GdocError):
            cmd_insert_image(args)
        mock_delete.assert_called_once_with("tmp1")

    @patch("gdoc.api.drive.delete_file", side_effect=GdocError("denied"))
    @patch("gdoc.api.drive.upload_temp_image",
           return_value={"id": "tmp1", "webContentLink": "https://dl/tmp1"})
    def test_cleanup_failure_warns_but_succeeds(
        self, mock_upload, mock_delete, mock_get, mock_insert, _ver, _update,
        tmp_path, capsys,
    ):
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG")
        args = _make_args("insert-image", image=str(img), index=5)
        rc = cmd_insert_image(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARN" in err
        assert "tmp1" in err

    @patch("gdoc.api.drive.upload_temp_image")
    def test_invalid_dimensions_fail_fast(
        self, mock_upload, mock_get, mock_insert, _ver, _update,
    ):
        for bad in (0.0, -5.0, float("nan"), float("inf")):
            args = _make_args("insert-image", index=5, width=bad)
            with pytest.raises(GdocError, match="--width") as exc_info:
                cmd_insert_image(args)
            assert exc_info.value.exit_code == 3
        args = _make_args("insert-image", index=5, height=0.0)
        with pytest.raises(GdocError, match="--height"):
            cmd_insert_image(args)
        mock_upload.assert_not_called()
        mock_insert.assert_not_called()

    def test_missing_local_file_fails_fast(
        self, mock_get, mock_insert, _ver, _update, tmp_path,
    ):
        args = _make_args(
            "insert-image", image=str(tmp_path / "nope.png"), index=5,
        )
        with pytest.raises(GdocError, match="not found") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3
        mock_get.assert_not_called()

    def test_unsupported_extension_fails_fast(
        self, mock_get, mock_insert, _ver, _update, tmp_path,
    ):
        img = tmp_path / "pic.bmp"
        img.write_bytes(b"BM")
        args = _make_args("insert-image", image=str(img), index=5)
        with pytest.raises(GdocError, match="unsupported") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3
        mock_get.assert_not_called()

    @patch("gdoc.api.drive.upload_temp_image")
    def test_webp_rejected_before_upload(
        self, mock_upload, mock_get, mock_insert, _ver, _update, tmp_path,
    ):
        # The Docs API rejects WebP (live-verified 400) — refuse it before
        # a public-read temp file exists.
        img = tmp_path / "pic.webp"
        img.write_bytes(b"RIFF....WEBP")
        args = _make_args("insert-image", image=str(img), index=5)
        with pytest.raises(GdocError, match="unsupported") as exc_info:
            cmd_insert_image(args)
        assert exc_info.value.exit_code == 3
        mock_upload.assert_not_called()

    @patch("gdoc.api.drive.delete_file")
    @patch("gdoc.api.drive.upload_temp_image", return_value={"id": "tmp1"})
    def test_missing_web_content_link_cleans_up(
        self, mock_upload, mock_delete, mock_get, mock_insert, _ver, _update,
        tmp_path,
    ):
        # Drive can omit webContentLink; the already-public temp file must
        # still be deleted and the failure reported.
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG")
        args = _make_args("insert-image", image=str(img), index=5)
        with pytest.raises(GdocError, match="no download link"):
            cmd_insert_image(args)
        mock_delete.assert_called_once_with("tmp1")
        mock_insert.assert_not_called()


class TestUploadTempImageCleanup:
    @patch("gdoc.api.drive.get_drive_service")
    def test_blocked_public_share_deletes_upload(self, mock_svc, tmp_path):
        """A Workspace policy can forbid anyone-sharing; the just-created
        temp file must not be orphaned when permissions.create fails."""
        from gdoc.api.drive import upload_temp_image

        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG")

        service = MagicMock()
        files = service.files.return_value
        files.create.return_value.execute.return_value = {
            "id": "tmp1", "webContentLink": "https://dl/tmp1",
        }
        service.permissions.return_value.create.return_value.execute.side_effect = (
            _http_error(403)
        )
        mock_svc.return_value = service

        with pytest.raises(GdocError):
            upload_temp_image(str(img), "image/png")

        files.delete.assert_called_once_with(fileId="tmp1")


_REPLACE_DOC = {
    "revisionId": "rev1",
    "tabs": [
        _tab("t1", "one\n"),
        _tab("t2", "two\n", inline_objects={"kix.img1": {}}),
    ],
}


@patch("gdoc.state.update_state_after_command")
@patch("gdoc.api.drive.get_file_version", return_value={"version": 8})
@patch("gdoc.api.docs.replace_image")
@patch("gdoc.api.docs.get_document_with_tabs", return_value=_REPLACE_DOC)
class TestCmdReplaceImage:
    def test_replaces_in_owning_tab(
        self, mock_get, mock_replace, _ver, mock_update, capsys,
    ):
        args = _make_args(
            "replace-image", object_id="kix.img1",
        )
        rc = cmd_replace_image(args)
        assert rc == 0
        mock_replace.assert_called_once_with(
            "doc123", "kix.img1", IMG_URL,
            tab_id="t2", revision_id="rev1",
        )
        assert "OK replaced image kix.img1" in capsys.readouterr().out
        assert mock_update.call_args.kwargs["command_version"] == 8

    def test_object_not_found(self, mock_get, mock_replace, _ver, _update):
        args = _make_args("replace-image", object_id="kix.missing")
        with pytest.raises(GdocError, match="not found") as exc_info:
            cmd_replace_image(args)
        assert exc_info.value.exit_code == 3
        mock_replace.assert_not_called()

    def test_legacy_doc_without_tabs(
        self, mock_get, mock_replace, _ver, _update,
    ):
        mock_get.return_value = {
            "revisionId": "rev1",
            "inlineObjects": {"kix.img1": {}},
        }
        args = _make_args("replace-image", object_id="kix.img1")
        rc = cmd_replace_image(args)
        assert rc == 0
        assert mock_replace.call_args.kwargs["tab_id"] is None

    @patch("gdoc.api.drive.delete_file")
    @patch("gdoc.api.drive.upload_temp_image",
           return_value={"id": "tmp2", "webContentLink": "https://dl/tmp2"})
    def test_local_file_uploaded_and_cleaned_up(
        self, mock_upload, mock_delete, mock_get, mock_replace, _ver,
        _update, tmp_path,
    ):
        img = tmp_path / "new.jpg"
        img.write_bytes(b"\xff\xd8")
        args = _make_args(
            "replace-image", object_id="kix.img1", image=str(img),
        )
        rc = cmd_replace_image(args)
        assert rc == 0
        mock_upload.assert_called_once_with(str(img), "image/jpeg")
        assert mock_replace.call_args.args[2] == "https://dl/tmp2"
        mock_delete.assert_called_once_with("tmp2")

    @patch("gdoc.api.drive.delete_file")
    @patch("gdoc.api.drive.upload_temp_image",
           return_value={"id": "tmp2", "webContentLink": "https://dl/tmp2"})
    def test_temp_cleaned_up_when_replace_fails(
        self, mock_upload, mock_delete, mock_get, mock_replace, _ver,
        _update, tmp_path,
    ):
        mock_replace.side_effect = GdocError("API error (400): bad image")
        img = tmp_path / "new.jpg"
        img.write_bytes(b"\xff\xd8")
        args = _make_args(
            "replace-image", object_id="kix.img1", image=str(img),
        )
        with pytest.raises(GdocError):
            cmd_replace_image(args)
        mock_delete.assert_called_once_with("tmp2")

    def test_json_output(
        self, mock_get, mock_replace, _ver, _update, capsys,
    ):
        args = _make_args(
            "replace-image", object_id="kix.img1", json=True,
        )
        cmd_replace_image(args)
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["object_id"] == "kix.img1"
        assert data["status"] == "replaced"
