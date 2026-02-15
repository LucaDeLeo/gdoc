"""Tests for the `gdoc images` subcommand and list_inline_objects API."""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gdoc.api.docs import download_image, list_inline_objects
from gdoc.cli import cmd_images
from gdoc.util import GdocError


def _make_args(**overrides):
    defaults = {
        "command": "images",
        "doc": "doc123",
        "image_id": None,
        "download": None,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_doc(inline_objects=None, positioned_objects=None, body_content=None):
    """Build a minimal Google Docs document dict."""
    doc = {
        "body": {"content": body_content or []},
        "inlineObjects": inline_objects or {},
        "positionedObjects": positioned_objects or {},
    }
    return doc


def _make_inline_image(obj_id, title="", description="", width=200, height=100,
                       content_uri="https://lh3.google.com/img1", source_uri=None):
    return {
        obj_id: {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "title": title,
                    "description": description,
                    "size": {
                        "width": {"magnitude": width, "unit": "PT"},
                        "height": {"magnitude": height, "unit": "PT"},
                    },
                    "imageProperties": {
                        "contentUri": content_uri,
                        "sourceUri": source_uri,
                    },
                }
            }
        }
    }


def _make_inline_drawing(obj_id, width=150, height=150):
    return {
        obj_id: {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "title": "",
                    "description": "",
                    "size": {
                        "width": {"magnitude": width, "unit": "PT"},
                        "height": {"magnitude": height, "unit": "PT"},
                    },
                    "embeddedDrawingProperties": {},
                }
            }
        }
    }


def _make_inline_chart(obj_id, title="", width=400, height=300,
                       content_uri="https://lh3.google.com/chart1",
                       spreadsheet_id="sheet1", chart_id=12345):
    return {
        obj_id: {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "title": title,
                    "description": "",
                    "size": {
                        "width": {"magnitude": width, "unit": "PT"},
                        "height": {"magnitude": height, "unit": "PT"},
                    },
                    "imageProperties": {
                        "contentUri": content_uri,
                    },
                    "linkedContentReference": {
                        "sheetsChartReference": {
                            "spreadsheetId": spreadsheet_id,
                            "chartId": chart_id,
                        }
                    },
                }
            }
        }
    }


def _make_body_with_inline_refs(*obj_ids):
    """Build body content with inlineObjectElement references."""
    elements = []
    for i, obj_id in enumerate(obj_ids):
        elements.append({
            "paragraph": {
                "elements": [
                    {
                        "startIndex": i * 10,
                        "inlineObjectElement": {
                            "inlineObjectId": obj_id,
                        },
                    }
                ]
            },
            "startIndex": i * 10,
        })
    return elements


# --- list_inline_objects tests ---


class TestListInlineObjects:
    @patch("gdoc.api.docs.get_document")
    def test_image_metadata(self, mock_get_doc):
        inline = _make_inline_image("kix.abc", title="Logo", width=200, height=100)
        body = _make_body_with_inline_refs("kix.abc")
        mock_get_doc.return_value = _make_doc(inline_objects=inline, body_content=body)

        result = list_inline_objects("doc123")
        assert len(result) == 1
        img = result[0]
        assert img["id"] == "kix.abc"
        assert img["type"] == "image"
        assert img["title"] == "Logo"
        assert img["width_pt"] == 200
        assert img["height_pt"] == 100
        assert img["content_uri"] == "https://lh3.google.com/img1"

    @patch("gdoc.api.docs.get_document")
    def test_drawing_classification(self, mock_get_doc):
        drawing = _make_inline_drawing("kix.draw")
        body = _make_body_with_inline_refs("kix.draw")
        mock_get_doc.return_value = _make_doc(inline_objects=drawing, body_content=body)

        result = list_inline_objects("doc123")
        assert len(result) == 1
        assert result[0]["type"] == "drawing"
        assert result[0]["content_uri"] is None

    @patch("gdoc.api.docs.get_document")
    def test_chart_classification(self, mock_get_doc):
        chart = _make_inline_chart("kix.chart", title="Q1 Revenue",
                                   spreadsheet_id="sID", chart_id=99)
        body = _make_body_with_inline_refs("kix.chart")
        mock_get_doc.return_value = _make_doc(inline_objects=chart, body_content=body)

        result = list_inline_objects("doc123")
        assert len(result) == 1
        c = result[0]
        assert c["type"] == "chart"
        assert c["title"] == "Q1 Revenue"
        assert c["spreadsheet_id"] == "sID"
        assert c["chart_id"] == 99
        assert c["content_uri"] == "https://lh3.google.com/chart1"

    @patch("gdoc.api.docs.get_document")
    def test_mixed_objects(self, mock_get_doc):
        inline = {}
        inline.update(_make_inline_image("kix.img", title="Photo"))
        inline.update(_make_inline_drawing("kix.draw"))
        inline.update(_make_inline_chart("kix.chart", title="Sales"))
        body = _make_body_with_inline_refs("kix.img", "kix.draw", "kix.chart")
        mock_get_doc.return_value = _make_doc(inline_objects=inline, body_content=body)

        result = list_inline_objects("doc123")
        assert len(result) == 3
        types = [r["type"] for r in result]
        assert "image" in types
        assert "drawing" in types
        assert "chart" in types

    @patch("gdoc.api.docs.get_document")
    def test_empty_doc(self, mock_get_doc):
        mock_get_doc.return_value = _make_doc()
        result = list_inline_objects("doc123")
        assert result == []

    @patch("gdoc.api.docs.get_document")
    def test_positioned_objects(self, mock_get_doc):
        positioned = {
            "kix.pos": {
                "positionedObjectProperties": {
                    "embeddedObject": {
                        "title": "Floating",
                        "description": "",
                        "size": {
                            "width": {"magnitude": 300, "unit": "PT"},
                            "height": {"magnitude": 200, "unit": "PT"},
                        },
                        "imageProperties": {
                            "contentUri": "https://lh3.google.com/pos1",
                        },
                    }
                }
            }
        }
        body = [{
            "paragraph": {
                "elements": [
                    {"startIndex": 0, "textRun": {"content": "text"}},
                ],
            },
            "startIndex": 0,
            "positionedObjectIds": ["kix.pos"],  # wrong location, fix below
        }]
        # positionedObjectIds is on the paragraph, not the element
        body[0]["paragraph"]["positionedObjectIds"] = ["kix.pos"]
        mock_get_doc.return_value = _make_doc(
            positioned_objects=positioned, body_content=body,
        )

        result = list_inline_objects("doc123")
        assert len(result) == 1
        assert result[0]["id"] == "kix.pos"
        assert result[0]["type"] == "image"
        assert result[0]["title"] == "Floating"

    @patch("gdoc.api.docs.get_document")
    def test_dedup_same_id(self, mock_get_doc):
        """Same object ID referenced twice should only appear once."""
        inline = _make_inline_image("kix.dup", title="Dup")
        body = _make_body_with_inline_refs("kix.dup", "kix.dup")
        mock_get_doc.return_value = _make_doc(inline_objects=inline, body_content=body)

        result = list_inline_objects("doc123")
        assert len(result) == 1


# --- download_image tests ---


class TestDownloadImage:
    @patch("urllib.request.urlopen")
    def test_download_writes_file(self, mock_urlopen, tmp_path):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"\x89PNG\r\n\x1a\nfakedata"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        dest = str(tmp_path / "test.png")
        download_image("https://example.com/img.png", dest)

        assert os.path.isfile(dest)
        with open(dest, "rb") as f:
            assert f.read() == b"\x89PNG\r\n\x1a\nfakedata"


# --- cmd_images tests ---


_SAMPLE_INLINE = {}
_SAMPLE_INLINE.update(
    _make_inline_image("kix.abc", title="Company Logo", width=200, height=100),
)
_SAMPLE_INLINE.update(
    _make_inline_chart("kix.def", title="Q1 Revenue", width=400, height=300),
)
_SAMPLE_INLINE.update(_make_inline_drawing("kix.ghi"))

_SAMPLE_BODY = _make_body_with_inline_refs("kix.abc", "kix.def", "kix.ghi")
_SAMPLE_DOC = _make_doc(inline_objects=_SAMPLE_INLINE, body_content=_SAMPLE_BODY)

_SAMPLE_OBJECTS = [
    {
        "id": "kix.abc", "type": "image", "title": "Company Logo",
        "description": "", "width_pt": 200, "height_pt": 100,
        "content_uri": "https://lh3.google.com/img1", "source_uri": None,
        "start_index": 0,
    },
    {
        "id": "kix.def", "type": "chart", "title": "Q1 Revenue",
        "description": "", "width_pt": 400, "height_pt": 300,
        "content_uri": "https://lh3.google.com/chart1", "source_uri": None,
        "start_index": 10, "spreadsheet_id": "sheet1", "chart_id": 12345,
    },
    {
        "id": "kix.ghi", "type": "drawing", "title": "",
        "description": "", "width_pt": 150, "height_pt": 150,
        "content_uri": None, "source_uri": None,
        "start_index": 20,
    },
]


class TestCmdImages:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    def test_terse_output(self, _doc, _pf, _update, capsys):
        args = _make_args()
        rc = cmd_images(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "kix.abc" in out
        assert "image" in out
        assert '"Company Logo"' in out
        assert "kix.ghi" in out
        assert "(not exportable)" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    def test_json_output(self, _doc, _pf, _update, capsys):
        args = _make_args(json=True)
        rc = cmd_images(args)
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert len(data["images"]) == 3
        types = [i["type"] for i in data["images"]]
        assert "image" in types
        assert "chart" in types
        assert "drawing" in types

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    def test_plain_output(self, _doc, _pf, _update, capsys):
        args = _make_args(plain=True)
        rc = cmd_images(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 3
        # Tab-separated fields
        assert "\t" in lines[0]
        assert "kix.abc" in lines[0]
        assert "image" in lines[0]

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    def test_verbose_output(self, _doc, _pf, _update, capsys):
        args = _make_args(verbose=True)
        rc = cmd_images(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "kix.abc" in out
        assert "kix.def" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document")
    def test_empty_no_images(self, mock_doc, _pf, _update, capsys):
        mock_doc.return_value = _make_doc()
        args = _make_args()
        rc = cmd_images(args)
        assert rc == 0
        assert "No images." in capsys.readouterr().out


class TestCmdImagesFilter:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    def test_filter_by_id(self, _doc, _pf, _update, capsys):
        args = _make_args(image_id="kix.abc")
        rc = cmd_images(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "kix.abc" in out
        assert "kix.def" not in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    def test_filter_not_found(self, _doc, _pf, _update):
        args = _make_args(image_id="kix.nonexistent")
        with pytest.raises(GdocError, match="image not found"):
            cmd_images(args)


class TestCmdImagesDownload:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    @patch("gdoc.api.docs.download_image")
    def test_download_images(self, mock_dl, _doc, _pf, _update, capsys, tmp_path):
        download_dir = str(tmp_path / "imgs")
        args = _make_args(download=download_dir)
        rc = cmd_images(args)
        assert rc == 0

        # Should have called download for image and chart, not drawing
        assert mock_dl.call_count == 2
        out = capsys.readouterr()
        assert "kix.abc.png" in out.out
        assert "kix.def.png" in out.out
        # Drawing warning on stderr
        assert "kix.ghi" in out.err
        assert "drawing" in out.err

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    @patch("gdoc.api.docs.download_image")
    def test_download_creates_dir(self, mock_dl, _doc, _pf, _update, tmp_path):
        download_dir = str(tmp_path / "new" / "nested")
        args = _make_args(download=download_dir)
        cmd_images(args)
        assert os.path.isdir(download_dir)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    @patch("gdoc.api.docs.download_image")
    def test_download_specific_image(
        self, mock_dl, _doc, _pf, _update, capsys, tmp_path,
    ):
        download_dir = str(tmp_path / "imgs")
        args = _make_args(download=download_dir, image_id="kix.abc")
        rc = cmd_images(args)
        assert rc == 0
        assert mock_dl.call_count == 1
        out = capsys.readouterr().out
        assert "kix.abc.png" in out

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.notify.pre_flight", return_value=None)
    @patch("gdoc.api.docs.get_document", return_value=_SAMPLE_DOC)
    @patch("gdoc.api.docs.download_image")
    def test_download_skips_no_uri(self, mock_dl, _doc, _pf, _update, capsys, tmp_path):
        """Drawing has no content_uri; should be skipped with warning."""
        download_dir = str(tmp_path / "imgs")
        args = _make_args(download=download_dir, image_id="kix.ghi")
        rc = cmd_images(args)
        assert rc == 0
        assert mock_dl.call_count == 0
        err = capsys.readouterr().err
        assert "drawing" in err


class TestCmdImagesParser:
    """Test that the images subparser is wired correctly."""

    def test_parser_has_images(self):
        from gdoc.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["images", "doc123"])
        assert args.command == "images"
        assert args.doc == "doc123"
        assert args.image_id is None
        assert args.download is None

    def test_parser_with_image_id(self):
        from gdoc.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["images", "doc123", "kix.abc"])
        assert args.image_id == "kix.abc"

    def test_parser_with_download(self):
        from gdoc.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["images", "--download", "/tmp/imgs", "doc123"])
        assert args.download == "/tmp/imgs"

    def test_parser_with_json(self):
        from gdoc.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["images", "--json", "doc123"])
        assert args.json is True
