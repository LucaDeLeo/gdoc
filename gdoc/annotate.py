"""Line-numbered comment annotation engine for cat --comments."""

import html
import re
from array import array


def _format_author(author_dict: dict) -> str:
    """Format author for display: prefer email, fallback to name."""
    if not author_dict:
        return "unknown"
    return (author_dict.get("emailAddress") or
            author_dict.get("displayName") or "unknown")


def _format_annotation_block(
    comment: dict,
    anchor_text: str | None = None,
    fallback_note: str = "",
) -> list[str]:
    """Build annotation lines for a single comment.

    Returns list of un-numbered annotation lines (no line-number prefix).
    """
    prefix = "      \t"
    lines = []

    cid = comment.get("id", "")
    resolved = comment.get("resolved", False)
    status = "resolved" if resolved else "open"
    author = _format_author(comment.get("author", {}))
    content = comment.get("content", "")

    # Header line
    status_part = f"[#{cid} {status}]"
    if fallback_note:
        status_part += f" [{fallback_note}]"

    if anchor_text is not None:
        # Anchored: show truncated anchor text on one line (an anchor can
        # span paragraphs or soft line breaks)
        display_anchor = " ".join(anchor_text.split())
        if len(display_anchor) > 40:
            display_anchor = display_anchor[:37] + "..."
        lines.append(f'{prefix}  {status_part} {author} on "{display_anchor}":')
    else:
        lines.append(f'{prefix}  {status_part} {author}: "{content}"')
        # For unanchored, content is on the header line, no separate content line
        # Add replies
        for r in comment.get("replies", []):
            reply_content = r.get("content", "")
            if not reply_content:
                continue
            r_author = _format_author(r.get("author", {}))
            lines.append(f'{prefix}    > {r_author}: "{reply_content}"')
        return lines

    # Content line (for anchored comments)
    lines.append(f'{prefix}    "{content}"')

    # Reply lines
    for r in comment.get("replies", []):
        reply_content = r.get("content", "")
        if not reply_content:
            continue  # Skip action-only replies
        r_author = _format_author(r.get("author", {}))
        lines.append(f'{prefix}    > {r_author}: "{reply_content}"')

    return lines


def _find_all(text: str, key: str) -> list[int]:
    starts, pos = [], text.find(key)
    while pos != -1:
        starts.append(pos)
        pos = text.find(key, pos + 1)
    return starts


# A link target: backslash escapes (Drive's export writes a ")" in a URL
# as "\)") and one level of balanced parentheses (gdoc's own renderer
# writes URLs raw). Links, images and code spans stay within one line, which
# bounds how far a failed match can scan.
_URL = r"\((?:\\.|\([^()\n]*\)|[^()\\\n])*\)"
# An image, alone or inside a link label (Drive exports a linked image as
# [![][image1]](https://example.com)).
_IMAGE = r"!\[[^\[\]\n]*\](?:" + _URL + r"|\[[^\[\]\n]*\])"

# Markdown that isn't visible text: a code fence (keeps its contents), a
# list marker, an escape (keeps the escaped char), an HTML entity (keeps
# the decoded char), a code span (keeps its contents), an image, a link
# (keeps its label), a footnote definition (see _visible_text), a
# reference definition line, a footnote reference, or an emphasis marker.
# Underscores inside a word are literal, as in CommonMark.
_MARKUP = re.compile("".join([
    r"^(?P<fence>```|~~~)[^\n]*\n(?P<block>(?s:.*?))^(?P=fence)[^\n]*$",
    r"|^[ \t]*(?:\d+[.)]|[-*+])[ \t]+",
    r"|\\(?P<escaped>.)",
    r"|(?P<entity>&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z]+);)",
    # A maximal backtick run opens a code span and the next run of the same
    # length closes it; both runs being maximal keeps a long run of
    # backticks from being retried at every split.
    r"|(?<!`)(?P<ticks>`+)(?!`)(?P<code>.+?)(?<!`)(?P=ticks)(?!`)",
    r"|", _IMAGE,
    # A "[" inside a label must start an image, so a failed image attempt
    # ends the label instead of rescanning the rest of the line.
    r"|\[(?P<label>(?:\\.|", _IMAGE, r"|[^\[\]\\\n])*)\]", _URL,
    r"|^(?P<footnote>\[\^[^\]\n]+\]:)[^\n]*$",
    r"|^\[[^\]\n]+\]:[^\n]*$",
    r"|\[\^[^\]\n]+\]",
    r"|\*+|~~|(?<!\w)_+|_+(?!\w)",
]), re.MULTILINE)


def _visible_text(
    markdown: str, footnotes: bool = False,
) -> tuple[str, array]:
    """The markdown's visible text, and each char's index in *markdown*.

    Footnote definitions are dropped unless *footnotes* is set: live
    anchors are counted against the document body, which doesn't hold
    footnote text, while a quote search must see every copy of the quote.
    """
    chars: list[str] = []
    where = array("q")  # one compact int per char

    def keep(start: int, end: int) -> None:
        chars.extend(markdown[start:end])
        where.extend(range(start, end))

    def scan(start: int, end: int) -> None:
        pos = start
        for m in _MARKUP.finditer(markdown, start, end):
            keep(pos, m.start())
            if m["block"] is not None:
                keep(m.start("block"), m.end("block"))
            elif m["escaped"] is not None:
                keep(m.start("escaped"), m.end("escaped"))
            elif m["entity"] is not None:
                decoded = html.unescape(m["entity"])
                chars.extend(decoded)
                where.extend([m.start("entity")] * len(decoded))
            elif m["code"] is not None:
                keep(m.start("code"), m.end("code"))
            elif m["label"] is not None:
                scan(m.start("label"), m.end("label"))
            elif m["footnote"] is not None and footnotes:
                scan(m.end("footnote"), m.end())
            pos = m.end()
        keep(pos, end)

    scan(0, len(markdown))
    return "".join(chars), where


def _place_live(
    markdown: str, visible: tuple[str, array], anchor: dict,
) -> int | None:
    """Line index for a live anchor, or None when it can't be pinned down.

    The anchor's key is found in the markdown's *visible* text, so emphasis
    markers, escapes and link targets neither hide nor fake a match. When
    the key occurs more than once, the anchor's own occurrence (counted in
    the document text) picks one, provided the markdown has the same
    number of occurrences.
    """
    key = anchor.get("key", "")
    if not key:
        return None
    text, where = visible
    starts = _find_all(text, key)
    if not starts or len(starts) != anchor.get("occurrences"):
        return None
    last = where[starts[anchor["occurrence"]] + len(key) - 1]
    return markdown.count("\n", 0, last)


def _place_quote(
    markdown: str, visible: tuple[str, array], qfc: dict,
) -> tuple[str, int | None, str]:
    """Place a comment by its quoted text: (note, line index or None, quote).

    Searched in the visible text, like live anchors, so a copy inside a
    link target can't stand in for text split by formatting. Drive returns
    a quote made in the Docs UI as HTML (an apostrophe is &#39;) and marks
    it text/html; a quote set through the API is plain text. When the type
    is missing and decoding changes the quote, both readings are tried,
    and they must agree on one place.
    """
    raw = qfc["value"]
    mime = qfc.get("mimeType")
    if mime == "text/html":
        readings = [html.unescape(raw)]
    elif mime:
        readings = [raw]
    else:
        readings = list(dict.fromkeys([raw, html.unescape(raw)]))
    readings = [r for r in readings if len(r.strip()) >= 4]
    if not readings:
        return "quoted text too short", None, raw

    text, where = visible
    found: dict[int, str] = {}  # line index -> the reading found there
    for reading in readings:
        for start in _find_all(text, reading):
            last = where[start + len(reading) - 1]
            found.setdefault(markdown.count("\n", 0, last), reading)
    if not found:
        return "quoted text not found (edited or detached)", None, raw
    if len(found) > 1:
        return "quoted text ambiguous", None, raw
    [(line_idx, reading)] = found.items()
    return "quoted text found", line_idx, reading


def annotate_markdown(
    markdown: str,
    comments: list[dict],
    show_resolved: bool = False,
    anchors: dict[str, dict | None] | None = None,
) -> str:
    """Produce line-numbered annotated output with inline comment annotations.

    Args:
        markdown: Raw markdown content from export_doc.
        comments: Comment dicts from list_comments(include_anchor=True).
        show_resolved: If True, include resolved comments. If False,
            filter them out (defensive — caller should pre-filter).
        anchors: Live anchors from get_comment_anchors, or None when they
            are unavailable. A comment with a live anchor is placed on the
            line holding its anchored text, or marked detached. Any other
            comment is placed where its quoted text occurs, which is only
            a location guess: Drive keeps the quote after the anchor is
            edited or detached.

    Returns:
        Annotated string with numbered content lines and un-numbered
        annotation lines.
    """
    # Defensive resolved filtering
    if not show_resolved:
        comments = [c for c in comments if not c.get("resolved", False)]
    if anchors is None:
        anchors = {}

    lines = markdown.split("\n")
    # Remove trailing empty line from split if markdown ends with \n
    if lines and lines[-1] == "" and markdown.endswith("\n"):
        lines = lines[:-1]

    # Classify comments: placed on a line vs listed at the end
    # line_annotations: line_index (0-based) -> list of (comment, anchor_text, fallback_note)
    line_annotations: dict[int, list[tuple[dict, str, str]]] = {}
    unanchored: list[tuple[dict, str]] = []  # (comment, fallback_note)

    def place(line_idx: int, c: dict, anchor_text: str, note: str) -> None:
        # Clamp to valid range
        if line_idx >= len(lines):
            line_idx = len(lines) - 1 if lines else 0
        line_annotations.setdefault(line_idx, []).append(
            (c, anchor_text, note),
        )

    visible = None  # built on first use, for live anchors
    visible_all = None  # with footnote text, for quote searches
    for c in comments:
        if c.get("id") in anchors:
            live = anchors[c["id"]]
            if live is None:
                unanchored.append((c, "detached"))
                continue
            if visible is None:
                visible = _visible_text(markdown)
            line_idx = _place_live(markdown, visible, live)
            if line_idx is None:
                unanchored.append((c, "attached, location not found"))
            else:
                place(line_idx, c, live["text"], "")
            continue

        qfc = c.get("quotedFileContent")
        if not qfc or not qfc.get("value"):
            # Unanchored comment
            unanchored.append((c, ""))
            continue

        if visible_all is None:
            visible_all = _visible_text(markdown, footnotes=True)
        note, line_idx, anchor_text = _place_quote(markdown, visible_all, qfc)
        if line_idx is None:
            unanchored.append((c, note))
        else:
            place(line_idx, c, anchor_text, note)

    # Build output
    output_lines: list[str] = []

    for i, line in enumerate(lines):
        line_num = i + 1
        output_lines.append(f"{line_num:>6}\t{line}")

        if i in line_annotations:
            for c, anchor_text, fallback_note in line_annotations[i]:
                annotation_lines = _format_annotation_block(
                    c, anchor_text=anchor_text, fallback_note=fallback_note,
                )
                output_lines.extend(annotation_lines)

    # Unanchored section
    if unanchored:
        output_lines.append("      \t[UNANCHORED]")
        for c, fallback_note in unanchored:
            annotation_lines = _format_annotation_block(
                c, anchor_text=None, fallback_note=fallback_note,
            )
            output_lines.extend(annotation_lines)

    # Final newline
    output_lines.append("")
    return "\n".join(output_lines)
