"""Markdown image extraction for doc import."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass
class ImageRef:
    """A reference to an image found in markdown content."""

    index: int
    alt: str
    path: str
    is_remote: bool
    placeholder: str
    resolved_path: str | None = None
    mime_type: str | None = None


def extract_images(
    content: str, base_dir: str,
) -> tuple[str, list[ImageRef]]:
    """Extract image references from markdown content.

    Replaces each ``![alt](path)`` with a ``<<IMG_N>>`` placeholder.
    Validates local paths for traversal and supported formats.

    Args:
        content: Markdown text.
        base_dir: Base directory for resolving relative paths.

    Returns:
        Tuple of (cleaned content, list of ImageRef).

    Raises:
        ValueError: On path traversal or unsupported format.
    """
    images: list[ImageRef] = []
    counter = 0

    def _replace(m: re.Match) -> str:
        nonlocal counter
        alt = m.group(1)
        path = m.group(2)
        placeholder = f"<<IMG_{counter}>>"
        is_remote = path.startswith(("http://", "https://"))

        ref = ImageRef(
            index=counter,
            alt=alt,
            path=path,
            is_remote=is_remote,
            placeholder=placeholder,
        )

        if not is_remote:
            # Resolve and validate local path
            resolved = os.path.normpath(
                os.path.join(base_dir, path)
            )
            # Path traversal check
            real_base = os.path.realpath(base_dir)
            real_resolved = os.path.realpath(resolved)
            if not real_resolved.startswith(real_base + os.sep):
                if real_resolved != real_base:
                    raise ValueError(
                        f"path traversal blocked: {path}"
                    )

            # Extension check
            ext = os.path.splitext(path)[1].lower()
            if ext not in _IMAGE_EXTENSIONS:
                raise ValueError(
                    f"unsupported image format: {ext}"
                )

            if not os.path.isfile(resolved):
                raise ValueError(
                    f"image not found: {path}"
                )

            ref.resolved_path = resolved
            ref.mime_type = _MIME_TYPES.get(ext)

        counter += 1
        images.append(ref)
        return placeholder

    cleaned = _IMAGE_RE.sub(_replace, content)
    return cleaned, images
