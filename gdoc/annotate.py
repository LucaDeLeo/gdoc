"""Line-numbered comment annotation engine for cat --comments."""

import html
import re
from array import array
from bisect import bisect_left, bisect_right

from gdoc.util import find_overlapping


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
    return list(find_overlapping(text, key))


# Chars that never start markup; a run of them is kept in one step.
_PLAIN = re.compile(r"[^\\&`!\[*~_\n]+")
_LIST_MARKER = re.compile(r"[ \t]*(?:\d+[.)]|[-*+])[ \t]+")
_ENTITY = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z]+);")
_FOOTNOTE_DEF = re.compile(r"\[\^[^\]\n]+\]:")
_REFERENCE_DEF = re.compile(r"\[[^\]\n]+\]:")


def _inside(ranges: list[tuple[int, int]], k: int) -> bool:
    """Whether *k* falls in one of the sorted, disjoint (start, end) ranges."""
    idx = bisect_right(ranges, (k, float("inf"))) - 1
    return idx >= 0 and ranges[idx][0] <= k < ranges[idx][1]


def _is_word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _visible_text(
    markdown: str, footnotes: bool = False,
) -> tuple[str, array]:
    """The markdown's visible text, and each char's index in *markdown*.

    Dropped, as not visible: code fence lines (their contents are kept),
    list markers, escape backslashes, code span backticks, images, link
    targets (link labels are kept), reference definition lines, footnote
    references, and emphasis markers (``*``, ``~~``, and ``_`` at a word
    edge — inside a word it is literal, as in CommonMark). HTML entities
    are decoded.

    Footnote definitions are dropped unless *footnotes* is set: live
    anchors are counted against the document body, which doesn't hold
    footnote text, while a quote search must see every copy of the quote.

    One pass over precomputed tables — matching brackets and parentheses,
    backtick runs by length, fence lines, line ends — so no construct is
    rescanned, whatever the input.
    """
    md = markdown
    n = len(md)
    chars: list[str] = []
    where = array("q")  # one compact int per char

    newlines = [k for k, ch in enumerate(md) if ch == "\n"]
    blank_lines = [k for k in newlines if k + 1 < n and md[k + 1] == "\n"]

    def line_end(k: int) -> int:
        """Index of the newline ending the line holding *k* (or n)."""
        idx = bisect_right(newlines, k)
        return newlines[idx] if idx < len(newlines) else n

    def one_line(a: int, b: int) -> bool:
        return b < line_end(a)

    def no_blank_line(a: int, b: int) -> bool:
        idx = bisect_right(blank_lines, a)
        return idx == len(blank_lines) or blank_lines[idx] >= b

    # Tables, in order of precedence: fenced blocks, then code spans, then
    # brackets and parentheses outside both. Text inside a fence or a code
    # span is literal, so its brackets never open or close a link.
    literal: list[tuple[int, int]] = []  # (start, end) of fences and spans
    fences: dict[int, int] = {}  # opening fence line -> closing fence line
    fence_lines = sorted(
        (k + 1, md[k + 1:k + 4]) for k in [-1, *newlines]
        if md.startswith(("```", "~~~"), k + 1)
    )
    next_same: list[int | None] = [None] * len(fence_lines)
    last: dict[str, int] = {}
    for idx in range(len(fence_lines) - 1, -1, -1):
        mark = fence_lines[idx][1]
        next_same[idx] = last.get(mark)
        last[mark] = fence_lines[idx][0]
    after = -1
    for idx, (line, _mark) in enumerate(fence_lines):
        closer = next_same[idx]
        if line <= after or closer is None:
            continue
        fences[line] = closer
        after = line_end(closer)
        literal.append((line, after))

    # A code span opens on a backtick run and closes on the next maximal
    # run of the same length on its line; a backslash before the opening
    # run escapes one backtick, but a backslash inside the span is literal.
    tick_runs = [
        (m.start(), m.end()) for m in re.finditer(r"`+", md)
        if not _inside(literal, m.start())
    ]
    runs: dict[int, list[int]] = {}  # backtick run length -> run starts
    for a, b in tick_runs:
        runs.setdefault(b - a, []).append(a)
    spans: dict[int, int] = {}  # code span opening backtick -> closing run
    after = -1
    for a, b in tick_runs:
        if a < after:
            continue
        slashes = a
        while slashes > 0 and md[slashes - 1] == "\\":
            slashes -= 1
        opener = a + (a - slashes) % 2
        same = runs.get(b - opener, [])
        k = bisect_right(same, a)
        if b > opener and k < len(same) and one_line(opener, same[k]):
            spans[opener] = same[k]
            after = same[k] + (b - opener)
            literal.append((opener, after))
    literal.sort()

    close_bracket: dict[int, int] = {}
    close_paren: dict[int, int] = {}
    brackets: list[int] = []
    parens: list[int] = []
    escaped = -1  # position of the char after the latest escaping backslash
    for m in re.finditer(r"[\\\[\]()]", md):
        i = m.start()
        if i == escaped or _inside(literal, i):
            continue
        ch = md[i]
        if ch == "\\":
            escaped = i + 1
        elif ch == "[":
            brackets.append(i)
        elif ch == "]" and brackets:
            close_bracket[brackets.pop()] = i
        elif ch == "(":
            parens.append(i)
        elif ch == ")" and parens:
            close_paren[parens.pop()] = i

    def target_end(k: int) -> int | None:
        """End of a (url) or [ref] target starting at *k*, or None."""
        if k < n and md[k] == "(" and k in close_paren:
            end = close_paren[k]
        elif k < n and md[k] == "[" and k in close_bracket:
            end = close_bracket[k]
        else:
            return None
        return end + 1 if one_line(k, end) else None

    def keep(a: int, b: int) -> None:
        chars.extend(md[a:b])
        where.extend(range(a, b))

    frames: list[tuple[int, int]] = []  # (label end, resume after target)
    i = 0
    while i < n:
        if frames and i >= frames[-1][0]:
            i = frames.pop()[1]
            continue
        limit = frames[-1][0] if frames else n
        ch = md[i]
        # Line-level markup counts only outside a link label.
        if not frames and (i == 0 or md[i - 1] == "\n"):
            if i in fences:
                keep(line_end(i) + 1, fences[i])
                i = line_end(fences[i])
                continue
            m = _LIST_MARKER.match(md, i)
            if m:
                i = m.end()
                continue
            m = _FOOTNOTE_DEF.match(md, i)
            if m:
                i = m.end() if footnotes else line_end(i)
                continue
            if _REFERENCE_DEF.match(md, i):
                i = line_end(i)
                continue
        m = _PLAIN.match(md, i, limit)
        if m:
            keep(i, m.end())
            i = m.end()
        elif ch == "\\" and i + 1 < limit and md[i + 1] != "\n":
            keep(i + 1, i + 2)
            i += 2
        elif ch == "&" and (m := _ENTITY.match(md, i, limit)):
            decoded = html.unescape(m.group())
            chars.extend(decoded)
            where.extend([i] * len(decoded))
            i = m.end()
        elif ch == "`":
            j = i
            while j < n and md[j] == "`":
                j += 1
            closer = spans.get(i)
            if closer is not None and closer < limit:
                keep(j, closer)
                i = closer + (j - i)
            else:
                keep(i, j)
                i = j
        elif (
            ch == "!" and i + 1 < limit and md[i + 1] == "["
            and i + 1 in close_bracket
            and (end := target_end(close_bracket[i + 1] + 1)) is not None
            and end <= limit
        ):
            i = end  # an image: nothing visible
        elif ch == "[" and i in close_bracket:
            close = close_bracket[i]
            end = target_end(close + 1)
            if (
                md[i + 1:i + 2] != "^" and end is not None
                and md[close + 1] == "(" and end <= limit
                and no_blank_line(i, close)
            ):
                frames.append((close, end))  # a link: keep its label
                i += 1
            elif md[i + 1:i + 2] == "^" and one_line(i, close):
                i = close + 1  # a footnote reference
            else:
                keep(i, i + 1)
                i += 1
        elif ch == "*":
            while i < n and md[i] == "*":
                i += 1
        elif ch == "~" and md[i:i + 2] == "~~":
            i += 2
        elif ch == "_":
            j = i
            while j < n and md[j] == "_":
                j += 1
            before = md[i - 1] if i else ""
            after = md[j] if j < n else ""
            if before and _is_word(before) and after and _is_word(after):
                keep(i, j)
            i = j
        else:
            keep(i, i + 1)
            i += 1
    return "".join(chars), where


def _newlines(markdown: str) -> list[int]:
    """Positions of the newlines in *markdown*; line index of position p is
    ``bisect_left(newlines, p)``."""
    return [i for i, ch in enumerate(markdown) if ch == "\n"]


def _place_live(
    newlines: list[int], visible: tuple[str, array], anchor: dict,
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
    return bisect_left(newlines, last)


def _place_quote(
    newlines: list[int], visible: tuple[str, array], qfc: dict,
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
        for start in find_overlapping(text, reading):
            last = where[start + len(reading) - 1]
            found.setdefault(bisect_left(newlines, last), reading)
            if len(found) > 1:
                break
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
    newlines = None  # newline positions, for line lookups
    for c in comments:
        if c.get("id") in anchors:
            live = anchors[c["id"]]
            if live is None:
                unanchored.append((c, "detached"))
                continue
            if visible is None:
                visible = _visible_text(markdown)
            if newlines is None:
                newlines = _newlines(markdown)
            line_idx = _place_live(newlines, visible, live)
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
        if newlines is None:
            newlines = _newlines(markdown)
        note, line_idx, anchor_text = _place_quote(newlines, visible_all, qfc)
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
