"""Tests for markdown image extraction."""

import os
from unittest.mock import patch

import pytest

from gdoc.mdimport import extract_images


class TestExtractImages:
    def test_no_images(self, tmp_path):
        content = "# Hello\n\nSome text"
        cleaned, images = extract_images(content, str(tmp_path))
        assert cleaned == content
        assert images == []

    def test_remote_image(self, tmp_path):
        content = "![alt](https://example.com/img.png)"
        cleaned, images = extract_images(content, str(tmp_path))
        assert cleaned == "<<IMG_0>>"
        assert len(images) == 1
        assert images[0].is_remote is True
        assert images[0].path == "https://example.com/img.png"
        assert images[0].alt == "alt"

    def test_local_image(self, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")
        content = "![photo](photo.png)"
        cleaned, images = extract_images(content, str(tmp_path))
        assert cleaned == "<<IMG_0>>"
        assert len(images) == 1
        assert images[0].is_remote is False
        assert images[0].resolved_path == str(img)
        assert images[0].mime_type == "image/png"

    def test_path_traversal_blocked(self, tmp_path):
        content = "![bad](../../etc/passwd.png)"
        with pytest.raises(ValueError, match="path traversal"):
            extract_images(content, str(tmp_path))

    def test_unsupported_format(self, tmp_path):
        img = tmp_path / "file.bmp"
        img.write_bytes(b"BM")
        content = "![bmp](file.bmp)"
        with pytest.raises(ValueError, match="unsupported image format"):
            extract_images(content, str(tmp_path))

    def test_missing_file(self, tmp_path):
        content = "![missing](no_such_file.png)"
        with pytest.raises(ValueError, match="image not found"):
            extract_images(content, str(tmp_path))

    def test_multiple_images(self, tmp_path):
        (tmp_path / "a.png").write_bytes(b"\x89PNG")
        (tmp_path / "b.jpg").write_bytes(b"\xff\xd8")
        content = (
            "Start ![a](a.png) middle "
            "![b](b.jpg) end"
        )
        cleaned, images = extract_images(content, str(tmp_path))
        assert "<<IMG_0>>" in cleaned
        assert "<<IMG_1>>" in cleaned
        assert len(images) == 2
        assert images[0].mime_type == "image/png"
        assert images[1].mime_type == "image/jpeg"

    def test_image_in_context(self, tmp_path):
        (tmp_path / "img.png").write_bytes(b"\x89PNG")
        content = "# Title\n\n![desc](img.png)\n\nMore text"
        cleaned, images = extract_images(content, str(tmp_path))
        assert "<<IMG_0>>" in cleaned
        assert "# Title" in cleaned
        assert "More text" in cleaned

    def test_remote_http_image(self, tmp_path):
        content = "![alt](http://example.com/img.jpg)"
        cleaned, images = extract_images(content, str(tmp_path))
        assert images[0].is_remote is True

    def test_webp_supported(self, tmp_path):
        img = tmp_path / "img.webp"
        img.write_bytes(b"RIFF")
        content = "![webp](img.webp)"
        cleaned, images = extract_images(content, str(tmp_path))
        assert images[0].mime_type == "image/webp"

    def test_jpeg_extension(self, tmp_path):
        img = tmp_path / "photo.jpeg"
        img.write_bytes(b"\xff\xd8")
        content = "![photo](photo.jpeg)"
        cleaned, images = extract_images(content, str(tmp_path))
        assert images[0].mime_type == "image/jpeg"
