"""Lightweight markdown parser for Google Docs API batchUpdate requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class StyleRange:
    """A formatting annotation within parsed plain text."""

    start: int
    end: int
    style: dict
    type: str  # "text_style", "paragraph_style", or "bullets"


@dataclass
class ParsedMarkdown:
    """Result of parsing markdown: plain text + style annotations."""

    plain_text: str
    styles: list[StyleRange] = field(default_factory=list)


# Inline patterns — order matters (bold+italic before bold/italic)
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"
    r"|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)"
)
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Heading pattern
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# List item patterns
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.+)$")


def _parse_inline(text: str) -> tuple[str, list[StyleRange]]:
    """Parse inline formatting from a text string.

    Returns (plain_text, style_ranges) where style_ranges have offsets
    relative to the returned plain_text.
    """
    styles: list[StyleRange] = []

    # Process in passes, tracking replacements with placeholders.
    # We process from most specific to least specific to avoid conflicts.

    # We'll collect all matches first, then process them in order of
    # their position in the original string to build the plain text.

    segments: list[tuple[int, int, str, list[dict]]] = []
    # Each segment: (orig_start, orig_end, plain_text, [style_dicts])

    # Find all inline formatting matches
    # Bold+italic: ***text***
    for m in _BOLD_ITALIC_RE.finditer(text):
        segments.append((
            m.start(), m.end(), m.group(1),
            [{"bold": True}, {"italic": True}],
        ))

    # Bold: **text** or __text__
    for m in _BOLD_RE.finditer(text):
        inner = m.group(1) or m.group(2)
        # Skip if overlaps with bold+italic
        if any(s[0] <= m.start() < s[1] or s[0] < m.end() <= s[1]
               for s in segments):
            continue
        segments.append((m.start(), m.end(), inner, [{"bold": True}]))

    # Italic: *text* or _text_
    for m in _ITALIC_RE.finditer(text):
        inner = m.group(1) or m.group(2)
        if any(s[0] <= m.start() < s[1] or s[0] < m.end() <= s[1]
               for s in segments):
            continue
        segments.append((m.start(), m.end(), inner, [{"italic": True}]))

    # Code: `text`
    for m in _CODE_RE.finditer(text):
        if any(s[0] <= m.start() < s[1] or s[0] < m.end() <= s[1]
               for s in segments):
            continue
        segments.append((
            m.start(), m.end(), m.group(1),
            [{"weightedFontFamily": {"fontFamily": "Courier New"}}],
        ))

    # Links: [text](url)
    for m in _LINK_RE.finditer(text):
        if any(s[0] <= m.start() < s[1] or s[0] < m.end() <= s[1]
               for s in segments):
            continue
        segments.append((
            m.start(), m.end(), m.group(1),
            [{"link": {"url": m.group(2)}}],
        ))

    # Sort segments by original start position
    segments.sort(key=lambda s: s[0])

    # Build plain text and style ranges
    plain_parts: list[str] = []
    plain_offset = 0
    prev_end = 0

    for orig_start, orig_end, seg_text, seg_styles in segments:
        # Add any literal text before this segment
        if orig_start > prev_end:
            literal = text[prev_end:orig_start]
            plain_parts.append(literal)
            plain_offset += len(literal)

        # Add the segment's plain text
        seg_start = plain_offset
        plain_parts.append(seg_text)
        plain_offset += len(seg_text)
        seg_end = plain_offset

        # Record styles
        for sd in seg_styles:
            styles.append(StyleRange(
                start=seg_start, end=seg_end,
                style=sd, type="text_style",
            ))

        prev_end = orig_end

    # Add any remaining literal text
    if prev_end < len(text):
        plain_parts.append(text[prev_end:])

    plain_text = "".join(plain_parts)
    return plain_text, styles


def parse_markdown(text: str) -> ParsedMarkdown:
    """Parse markdown text into plain text + style annotations.

    Handles: headings (H1-H6), bullet lists, numbered lists,
    bold, italic, bold+italic, inline code, links.
    """
    if not text:
        return ParsedMarkdown(plain_text="")

    lines = text.split("\n")
    plain_parts: list[str] = []
    all_styles: list[StyleRange] = []
    offset = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Heading
        heading_m = _HEADING_RE.match(line)
        if heading_m:
            level = len(heading_m.group(1))
            content = heading_m.group(2)
            inline_text, inline_styles = _parse_inline(content)

            para_start = offset
            plain_parts.append(inline_text)
            offset += len(inline_text)

            # Shift inline styles
            for s in inline_styles:
                all_styles.append(StyleRange(
                    start=s.start + para_start,
                    end=s.end + para_start,
                    style=s.style, type=s.type,
                ))

            # Add newline
            plain_parts.append("\n")
            offset += 1

            # Record heading style for this paragraph
            all_styles.append(StyleRange(
                start=para_start, end=offset,
                style={"namedStyleType": f"HEADING_{level}"},
                type="paragraph_style",
            ))

            i += 1
            continue

        # Bullet list item
        bullet_m = _BULLET_RE.match(line)
        if bullet_m:
            content = bullet_m.group(1)
            inline_text, inline_styles = _parse_inline(content)

            para_start = offset
            plain_parts.append(inline_text)
            offset += len(inline_text)

            for s in inline_styles:
                all_styles.append(StyleRange(
                    start=s.start + para_start,
                    end=s.end + para_start,
                    style=s.style, type=s.type,
                ))

            plain_parts.append("\n")
            offset += 1

            all_styles.append(StyleRange(
                start=para_start, end=offset,
                style={"bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"},
                type="bullets",
            ))

            i += 1
            continue

        # Numbered list item
        numbered_m = _NUMBERED_RE.match(line)
        if numbered_m:
            content = numbered_m.group(1)
            inline_text, inline_styles = _parse_inline(content)

            para_start = offset
            plain_parts.append(inline_text)
            offset += len(inline_text)

            for s in inline_styles:
                all_styles.append(StyleRange(
                    start=s.start + para_start,
                    end=s.end + para_start,
                    style=s.style, type=s.type,
                ))

            plain_parts.append("\n")
            offset += 1

            all_styles.append(StyleRange(
                start=para_start, end=offset,
                style={"bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN"},
                type="bullets",
            ))

            i += 1
            continue

        # Normal paragraph line
        inline_text, inline_styles = _parse_inline(line)

        para_start = offset
        plain_parts.append(inline_text)
        offset += len(inline_text)

        for s in inline_styles:
            all_styles.append(StyleRange(
                start=s.start + para_start,
                end=s.end + para_start,
                style=s.style, type=s.type,
            ))

        plain_parts.append("\n")
        offset += 1

        i += 1

    plain_text = "".join(plain_parts)
    return ParsedMarkdown(plain_text=plain_text, styles=all_styles)


def to_docs_requests(
    parsed: ParsedMarkdown, insert_index: int,
) -> list[dict]:
    """Convert ParsedMarkdown into Docs API batchUpdate request dicts.

    Args:
        parsed: The parsed markdown result.
        insert_index: The document index at which to insert text.

    Returns:
        List of request dicts for batchUpdate.
    """
    if not parsed.plain_text:
        return []

    requests: list[dict] = []

    # 1. Insert the plain text
    requests.append({
        "insertText": {
            "location": {"index": insert_index},
            "text": parsed.plain_text,
        }
    })

    # 2. Apply text styles (bold, italic, code, link)
    for sr in parsed.styles:
        if sr.type == "text_style":
            # Build the fields mask from the style keys
            fields = _text_style_fields(sr.style)
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": sr.start + insert_index,
                        "endIndex": sr.end + insert_index,
                    },
                    "textStyle": sr.style,
                    "fields": fields,
                }
            })

    # 3. Apply paragraph styles (headings)
    for sr in parsed.styles:
        if sr.type == "paragraph_style":
            requests.append({
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": sr.start + insert_index,
                        "endIndex": sr.end + insert_index,
                    },
                    "paragraphStyle": sr.style,
                    "fields": "namedStyleType",
                }
            })

    # 4. Apply bullet styles
    for sr in parsed.styles:
        if sr.type == "bullets":
            requests.append({
                "createParagraphBullets": {
                    "range": {
                        "startIndex": sr.start + insert_index,
                        "endIndex": sr.end + insert_index,
                    },
                    "bulletPreset": sr.style["bulletPreset"],
                }
            })

    return requests


def _text_style_fields(style: dict) -> str:
    """Build the fields mask string for updateTextStyle."""
    parts = []
    for key in style:
        if key == "bold":
            parts.append("bold")
        elif key == "italic":
            parts.append("italic")
        elif key == "weightedFontFamily":
            parts.append("weightedFontFamily")
        elif key == "link":
            parts.append("link")
    return ",".join(parts)
