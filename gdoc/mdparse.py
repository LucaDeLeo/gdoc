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
class TableData:
    """A parsed markdown table with cell content and position info."""

    rows: list[list[str]]
    num_rows: int
    num_cols: int
    plain_text_offset: int  # byte offset in plain_text where placeholder sits
    # Leading list-indent tabs inserted before this table. createParagraphBullets
    # removes those tabs, shifting the table's real position left by this many.
    removed_tabs_before: int = 0


@dataclass
class ParsedMarkdown:
    """Result of parsing markdown: plain text + style annotations."""

    plain_text: str
    styles: list[StyleRange] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    # Total leading list-indent tabs in plain_text. createParagraphBullets
    # removes them at apply time, so the document grows by len(plain_text)
    # minus this when the requests are applied.
    removed_tabs: int = 0


# Inline patterns — order matters (bold+italic before bold/italic)
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"
    r"|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)"
)
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Inline patterns in precedence order. Each entry: (regex, kind). On a tie at
# the same position, the earlier entry wins, so ***x*** beats **x**/*x*.
_INLINE_PATTERNS = [
    (_BOLD_ITALIC_RE, "bolditalic"),
    (_BOLD_RE, "bold"),
    (_ITALIC_RE, "italic"),
    (_STRIKE_RE, "strike"),
    (_CODE_RE, "code"),
    (_LINK_RE, "link"),
]

# Text-style dicts applied per emphasis kind (these recurse into their inner
# content so emphasis can nest, e.g. **bold _and italic_**).
_STYLES_FOR_KIND = {
    "bolditalic": [{"bold": True}, {"italic": True}],
    "bold": [{"bold": True}],
    "italic": [{"italic": True}],
    "strike": [{"strikethrough": True}],
}

_CODE_FONT = {"weightedFontFamily": {"fontFamily": "Courier New"}}

# Heading pattern
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# List item patterns (capture leading indentation for nesting)
_BULLET_RE = re.compile(r"^([ \t]*)[-*]\s+(.+)$")
_NUMBERED_RE = re.compile(r"^([ \t]*)(\d+)\.\s+(.+)$")

# Block patterns
_BLOCKQUOTE_RE = re.compile(r"^ {0,3}>\s?(.*)$")
_HR_RE = re.compile(r"^ {0,3}([-*_])[ ]*(?:\1[ ]*){2,}$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)\s*$")

# Table patterns
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:]*-{3,}[\s:]*(\|[\s:]*-{3,}[\s:]*)*\|$")

# Characters a backslash may escape (CommonMark ASCII-punctuation set).
_ESCAPABLE = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

# Sentinel used to mask escaped characters so they cannot match (or break)
# the inline regexes. NUL never appears in real document text.
_MASK = "\x00"

# Indentation magnitude (PT) used for one level of blockquote indent.
_QUOTE_INDENT_PT = 36


def _mask_escapes(text: str) -> str:
    """Return a same-length copy of ``text`` with each backslash-escaped
    character blanked to a sentinel, so the inline regexes never match (or are
    broken by) an escaped marker. The backslash itself is left in place (it
    isn't a marker). Emitted text is sliced from the original, so the lengths
    must stay aligned — hence blanking in place rather than removing.
    """
    if "\\" not in text:
        return text
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n and text[i + 1] in _ESCAPABLE:
            out[i + 1] = _MASK
            i += 2
        else:
            i += 1
    return "".join(out)


def _strip_escapes(s: str) -> str:
    """Drop escaping backslashes (``\\X`` -> ``X`` for escapable X), matching
    CommonMark. NOT applied inside code spans, whose content is literal.
    A backslash before a non-escapable character (or at end) is kept.
    """
    if "\\" not in s:
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n and s[i + 1] in _ESCAPABLE:
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def parse_inline(text: str) -> tuple[str, list[StyleRange]]:
    """Public: parse inline formatting from a single string.

    For callers (e.g. table-cell rendering) that need inline parsing without
    the block-level handling of `parse_markdown`. Returns (plain_text,
    style_ranges) with offsets relative to plain_text.
    """
    return _parse_inline(text)


def _parse_inline(text: str) -> tuple[str, list[StyleRange]]:
    """Parse inline formatting from a text string.

    Returns (plain_text, style_ranges) with offsets relative to plain_text.
    Emphasis spans nest recursively; backslash escapes are resolved per
    segment (and left intact inside code spans).
    """
    return _scan(text, _mask_escapes(text))


def _scan(text: str, masked: str) -> tuple[str, list[StyleRange]]:
    """Recursively parse inline formatting.

    ``text`` is the original source; ``masked`` is the same length with escaped
    markers blanked to a sentinel. The regexes run against ``masked``; emitted
    text is sliced from ``text``. Escaping backslashes are stripped from normal
    content but PRESERVED inside code spans (code is literal — CommonMark).
    Spans recurse so emphasis can nest (e.g. ``**bold _and italic_**``).
    Returns (plain_text, [StyleRange]) with offsets relative to plain_text.
    """
    plain_parts: list[str] = []
    styles: list[StyleRange] = []
    offset = 0
    pos = 0
    n = len(masked)

    while pos < n:
        # Search the unconsumed tail (a fresh slice), not masked[pos:] via the
        # pos argument: a lookbehind (`(?<!\*)`) would otherwise read the
        # just-consumed marker before `pos` and wrongly block a span that abuts
        # it (e.g. the `*b*` in `**a***b*`). Match offsets are relative to the
        # slice, so shift them by `pos`.
        tail = masked[pos:]
        best: tuple[re.Match, str] | None = None
        for pat, kind in _INLINE_PATTERNS:
            m = pat.search(tail)
            if m is not None and (best is None or m.start() < best[0].start()):
                best = (m, kind)
        if best is None:
            plain_parts.append(_strip_escapes(text[pos:]))
            break

        m, kind = best
        m_start = pos + m.start()
        if m_start > pos:
            lit = _strip_escapes(text[pos:m_start])
            plain_parts.append(lit)
            offset += len(lit)

        def _grp(group: int) -> tuple[int, int]:
            return pos + m.start(group), pos + m.end(group)

        seg_start = offset
        if kind == "code":
            # Code spans are literal — content kept verbatim (backslashes too).
            a, b = _grp(1)
            inner = text[a:b]
            plain_parts.append(inner)
            offset += len(inner)
            styles.append(StyleRange(seg_start, offset, _CODE_FONT, "text_style"))
        elif kind == "link":
            a, b = _grp(1)
            sub_plain, sub_styles = _scan(text[a:b], masked[a:b])
            plain_parts.append(sub_plain)
            offset += len(sub_plain)
            for s in sub_styles:
                styles.append(StyleRange(
                    s.start + seg_start, s.end + seg_start, s.style, s.type,
                ))
            ua, ub = _grp(2)
            styles.append(StyleRange(
                seg_start, offset,
                {"link": {"url": _strip_escapes(text[ua:ub])}}, "text_style",
            ))
        else:
            # bold / italic alternations capture group 1 or 2; others, group 1.
            g = 2 if (kind in ("bold", "italic") and m.group(1) is None) else 1
            a, b = _grp(g)
            sub_plain, sub_styles = _scan(text[a:b], masked[a:b])
            plain_parts.append(sub_plain)
            offset += len(sub_plain)
            for s in sub_styles:
                styles.append(StyleRange(
                    s.start + seg_start, s.end + seg_start, s.style, s.type,
                ))
            for sd in _STYLES_FOR_KIND[kind]:
                styles.append(StyleRange(seg_start, offset, sd, "text_style"))

        pos = pos + m.end()

    return "".join(plain_parts), styles


def _list_level(indent: str, open_items: list[int]) -> int:
    """Nesting level from a list item's leading whitespace.

    ``open_items`` holds the indent columns of the enclosing items, outermost
    first, and is updated in place. An item indented at least two columns
    past the item above it is that item's child, so ``- a`` then
    ``    - b`` is one level deep, as in CommonMark, and so are sub-items under
    ``1.`` (three columns) and ``10.`` (four). A tab is four columns. Capped
    at 8 (Docs' max).
    """
    columns = len(indent.expandtabs(4))
    while open_items and columns < open_items[-1] + 2:
        open_items.pop()
    open_items.append(columns)
    return min(len(open_items) - 1, 8)


def parse_markdown(text: str) -> ParsedMarkdown:
    """Parse markdown text into plain text + style annotations.

    Handles: headings (H1-H6), bullet/numbered lists (nested), bold, italic,
    bold+italic, strikethrough, inline code, links, blockquotes, horizontal
    rules, fenced code blocks, and tables.
    """
    if not text:
        return ParsedMarkdown(plain_text="")

    lines = text.split("\n")
    plain_parts: list[str] = []
    all_styles: list[StyleRange] = []
    all_tables: list[TableData] = []
    offset = 0
    removed_tabs = 0  # running count of leading list-indent tabs (see below)
    open_items: list[int] = []  # indents of the enclosing list items

    def emit_paragraph(
        content: str,
        content_styles: list[StyleRange],
        para_style: dict,
        bullet_preset: str | None = None,
        leading_tabs: int = 0,
        list_start: int | None = None,
    ) -> None:
        """Append one paragraph (content + newline) and its style ranges.

        ``leading_tabs`` prepends tabs for list nesting; createParagraphBullets
        counts and removes them at apply time (tracked via ``removed_tabs``).
        """
        nonlocal offset, removed_tabs
        para_start = offset
        if leading_tabs:
            plain_parts.append("\t" * leading_tabs)
            offset += leading_tabs
            removed_tabs += leading_tabs
        text_start = offset
        plain_parts.append(content)
        offset += len(content)
        for s in content_styles:
            all_styles.append(StyleRange(
                s.start + text_start, s.end + text_start, s.style, s.type,
            ))
        plain_parts.append("\n")
        offset += 1
        all_styles.append(StyleRange(
            para_start, offset, para_style, "paragraph_style",
        ))
        if bullet_preset is not None:
            bullet = {"bulletPreset": bullet_preset}
            if list_start is not None:
                bullet["start"] = list_start
            all_styles.append(StyleRange(para_start, offset, bullet, "bullets"))

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() and not (_BULLET_RE.match(line) or _NUMBERED_RE.match(line)):
            open_items.clear()

        # Fenced code block: ``` (or ~~~) ... ```
        fence_m = _FENCE_RE.match(line)
        if fence_m:
            fence = fence_m.group(1)
            fence_char = fence[0]
            i += 1
            while i < len(lines):
                close = _FENCE_RE.match(lines[i])
                if close:
                    close_fence = close.group(1)
                    if close_fence[0] == fence_char and len(
                        close_fence
                    ) >= len(fence):
                        i += 1
                        break
                code_line = lines[i]
                styles = (
                    [StyleRange(0, len(code_line), _CODE_FONT, "text_style")]
                    if code_line else []
                )
                emit_paragraph(
                    code_line, styles, {"namedStyleType": "NORMAL_TEXT"},
                )
                i += 1
            continue

        # Table: header row + separator row + data rows
        if (
            _TABLE_ROW_RE.match(line)
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1])
        ):
            table_rows: list[list[str]] = []
            header_cells = [c.strip() for c in line.strip("|").split("|")]
            table_rows.append(header_cells)
            num_cols = len(header_cells)
            i += 2  # skip header + separator
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                if len(cells) < num_cols:
                    cells.extend([""] * (num_cols - len(cells)))
                elif len(cells) > num_cols:
                    cells = cells[:num_cols]
                table_rows.append(cells)
                i += 1

            para_start = offset
            all_tables.append(TableData(
                rows=table_rows,
                num_rows=len(table_rows),
                num_cols=num_cols,
                plain_text_offset=offset,
                removed_tabs_before=removed_tabs,
            ))
            plain_parts.append("\n")
            offset += 1
            all_styles.append(StyleRange(
                start=para_start, end=offset,
                style={"namedStyleType": "NORMAL_TEXT"},
                type="paragraph_style",
            ))
            continue

        # Heading
        heading_m = _HEADING_RE.match(line)
        if heading_m:
            level = len(heading_m.group(1))
            inline_text, inline_styles = _parse_inline(heading_m.group(2))
            emit_paragraph(
                inline_text, inline_styles,
                {"namedStyleType": f"HEADING_{level}"},
            )
            i += 1
            continue

        # Horizontal rule (--- / *** / ___) — an empty paragraph with a
        # bottom border (the Docs API has no direct horizontal-rule insert).
        if _HR_RE.match(line):
            emit_paragraph("", [], {
                "namedStyleType": "NORMAL_TEXT",
                "borderBottom": {
                    "color": {"color": {"rgbColor": {
                        "red": 0.5, "green": 0.5, "blue": 0.5,
                    }}},
                    "width": {"magnitude": 1, "unit": "PT"},
                    "padding": {"magnitude": 1, "unit": "PT"},
                    "dashStyle": "SOLID",
                },
            })
            i += 1
            continue

        # Blockquote — render as an indented normal paragraph.
        quote_m = _BLOCKQUOTE_RE.match(line)
        if quote_m:
            inline_text, inline_styles = _parse_inline(quote_m.group(1))
            indent = {"magnitude": _QUOTE_INDENT_PT, "unit": "PT"}
            emit_paragraph(inline_text, inline_styles, {
                "namedStyleType": "NORMAL_TEXT",
                "indentStart": indent,
                "indentFirstLine": indent,
            })
            i += 1
            continue

        # Bullet list item (indent-aware)
        bullet_m = _BULLET_RE.match(line)
        if bullet_m:
            inline_text, inline_styles = _parse_inline(bullet_m.group(2))
            emit_paragraph(
                inline_text, inline_styles,
                {"namedStyleType": "NORMAL_TEXT"},
                bullet_preset="BULLET_DISC_CIRCLE_SQUARE",
                leading_tabs=_list_level(bullet_m.group(1), open_items),
            )
            i += 1
            continue

        # Numbered list item (indent-aware)
        numbered_m = _NUMBERED_RE.match(line)
        if numbered_m:
            inline_text, inline_styles = _parse_inline(numbered_m.group(3))
            emit_paragraph(
                inline_text, inline_styles,
                {"namedStyleType": "NORMAL_TEXT"},
                bullet_preset="NUMBERED_DECIMAL_ALPHA_ROMAN",
                leading_tabs=_list_level(numbered_m.group(1), open_items),
                list_start=int(numbered_m.group(2)),
            )
            i += 1
            continue

        # Normal paragraph line. Explicit NORMAL_TEXT so inserted paragraphs
        # don't inherit the style of the paragraph at the insertion point.
        inline_text, inline_styles = _parse_inline(line)
        emit_paragraph(
            inline_text, inline_styles, {"namedStyleType": "NORMAL_TEXT"},
        )
        i += 1

    return ParsedMarkdown(
        plain_text="".join(plain_parts),
        styles=all_styles,
        tables=all_tables,
        removed_tabs=removed_tabs,
    )


def to_docs_requests(
    parsed: ParsedMarkdown,
    insert_index: int,
    tab_id: str | None = None,
) -> list[dict]:
    """Convert ParsedMarkdown into Docs API batchUpdate request dicts.

    Args:
        parsed: The parsed markdown result.
        insert_index: The document index at which to insert text.
        tab_id: Optional tab ID for targeting a specific tab.

    Returns:
        List of request dicts for batchUpdate.
    """
    if not parsed.plain_text:
        return []

    requests: list[dict] = []

    # Style ranges are Python code-point offsets into plain_text; Docs API
    # indexes are UTF-16 code units, so an emoji (non-BMP) earlier in the
    # replacement shifts every later range by one extra unit.
    utf16 = _utf16_prefix(parsed.plain_text)

    def _location(index: int) -> dict:
        loc = {"index": index}
        if tab_id:
            loc["tabId"] = tab_id
        return loc

    def _range(start: int, end: int) -> dict:
        r = {"startIndex": start, "endIndex": end}
        if tab_id:
            r["tabId"] = tab_id
        return r

    # 1. Insert the plain text.
    requests.append({
        "insertText": {
            "location": _location(insert_index),
            "text": parsed.plain_text,
        }
    })

    # 2. Paragraph styles (named styles, indents, borders). Applied before text
    #    styles because a `namedStyleType` re-resolves a run's direct character
    #    formatting and would clear bold/italic set afterwards.
    for sr in parsed.styles:
        if sr.type == "paragraph_style":
            requests.append({
                "updateParagraphStyle": {
                    "range": _range(
                        utf16[sr.start] + insert_index,
                        utf16[sr.end] + insert_index,
                    ),
                    "paragraphStyle": sr.style,
                    "fields": _paragraph_style_fields(sr.style),
                }
            })

    # 3. Text styles (bold, italic, strikethrough, code, link). After paragraph
    #    styles so they are not clobbered; before bullets so they are already
    #    attached to their runs when bullet creation removes leading tabs.
    for sr in parsed.styles:
        if sr.type == "text_style":
            requests.append({
                "updateTextStyle": {
                    "range": _range(
                        utf16[sr.start] + insert_index,
                        utf16[sr.end] + insert_index,
                    ),
                    "textStyle": sr.style,
                    "fields": _text_style_fields(sr.style),
                }
            })

    # 4. Bullets last, in FORWARD document order. Two forces:
    #    - createParagraphBullets counts and REMOVES the leading tabs that
    #      encode nesting level, shifting all later indices left.
    #    - ordered lists only number continuously (1, 2, 3) when each item is
    #      created after the one above it — reverse order makes each item start
    #      its own list at 1 (and can drop bullets entirely).
    #    - the nesting level is the item's leading tabs minus the fewest
    #      leading tabs in the request's range (see listnest.py), so an item
    #      bulleted on its own always lands at its neighbour's level. Each run
    #      of adjacent list items is therefore bulleted in one request.
    #    - a numbered list that resumes after other paragraphs ("10." below a
    #      heading) must be the same Docs list as the one it continues: the
    #      API cannot set a start number. Such a chain of runs is bulleted in
    #      one request, then the paragraphs between its runs are taken out of
    #      the list again, with their indent restored.
    #    So process runs top-to-bottom and subtract the tabs that earlier runs
    #    in this same batch have already removed.
    text = parsed.plain_text
    removed = 0
    bullet_ranges = sorted(
        (sr for sr in parsed.styles if sr.type == "bullets"),
        key=lambda s: s.start,
    )
    para_styles = [sr for sr in parsed.styles if sr.type == "paragraph_style"]
    runs = _list_runs(bullet_ranges, text)
    table_offsets = [t.plain_text_offset for t in parsed.tables]
    for chain in _numbering_chains(runs, table_offsets):
        start, end = chain[0][0].start, chain[-1][-1].end
        requests.append({
            "createParagraphBullets": {
                "range": _range(
                    utf16[start] + insert_index - removed,
                    utf16[end] + insert_index - removed,
                ),
                "bulletPreset": chain[0][0].style["bulletPreset"],
            }
        })
        for prev, run in zip(chain, chain[1:]):
            removed += sum(_leading_tabs(text, sr.start) for sr in prev)
            gap_start, gap_end = prev[-1].end, run[0].start
            shift = insert_index - removed
            requests.append({
                "deleteParagraphBullets": {
                    "range": _range(utf16[gap_start] + shift, utf16[gap_end] + shift),
                }
            })
            for sr in para_styles:
                if gap_start <= sr.start and sr.end <= gap_end:
                    zero = {"magnitude": 0, "unit": "PT"}
                    indent = {
                        "indentStart": sr.style.get("indentStart", zero),
                        "indentFirstLine": sr.style.get("indentFirstLine", zero),
                    }
                    requests.append({
                        "updateParagraphStyle": {
                            "range": _range(
                                utf16[sr.start] + shift, utf16[sr.end] + shift,
                            ),
                            "paragraphStyle": indent,
                            "fields": "indentStart,indentFirstLine",
                        }
                    })
        removed += sum(_leading_tabs(text, sr.start) for sr in chain[-1])

    return requests


def _leading_tabs(text: str, start: int) -> int:
    n = 0
    while start + n < len(text) and text[start + n] == "\t":
        n += 1
    return n


def _list_runs(bullet_ranges: list[StyleRange], text: str) -> list[list[StyleRange]]:
    """Group list paragraphs into runs that become one Docs list each.

    A run is a sequence of adjacent list paragraphs; a top-level item of the
    other kind (numbered vs bullet) starts a new run. Nested items take the
    run's preset, as the Docs editor does when Tab indents a bullet under a
    numbered item (it shows as "a.").
    """
    runs: list[list[StyleRange]] = []
    for sr in bullet_ranges:
        if runs:
            prev = runs[-1][-1]
            same_kind = sr.style["bulletPreset"] == runs[-1][0].style["bulletPreset"]
            if sr.start == prev.end and (same_kind or _leading_tabs(text, sr.start)):
                runs[-1].append(sr)
                continue
        runs.append([sr])
    return runs


def _numbering_chains(
    runs: list[list[StyleRange]], table_offsets: list[int],
) -> list[list[list[StyleRange]]]:
    """Join each numbered run that starts above 1 to the numbered run before it.

    Docs numbers every item of one list in sequence, even with other
    paragraphs between them, so "1. a", a heading, then "2. b" stays 1, 2
    only if both runs are one list. Runs separated by a table or by a bullet
    list are not joined.
    """
    chains: list[list[list[StyleRange]]] = []
    for run in runs:
        if chains and _numbered(run) and run[0].style.get("start", 1) > 1 \
                and _numbered(chains[-1][-1]):
            gap_start, gap_end = chains[-1][-1][-1].end, run[0].start
            if not any(gap_start <= off < gap_end for off in table_offsets):
                chains[-1].append(run)
                continue
        chains.append([run])
    return chains


def _numbered(run: list[StyleRange]) -> bool:
    return run[0].style["bulletPreset"].startswith("NUMBERED")


def utf16_len(text: str) -> int:
    """Length of *text* in UTF-16 code units (the Docs API index space)."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _utf16_prefix(text: str) -> list[int]:
    """offsets[i] = UTF-16 length of text[:i] (len(text) + 1 entries)."""
    offsets = [0]
    total = 0
    for ch in text:
        total += 2 if ord(ch) > 0xFFFF else 1
        offsets.append(total)
    return offsets


def text_style_fields(style: dict) -> str:
    """Public: build the updateTextStyle `fields` mask for a style dict."""
    return _text_style_fields(style)


def _text_style_fields(style: dict) -> str:
    """Build the fields mask string for updateTextStyle."""
    parts = []
    for key in style:
        if key == "bold":
            parts.append("bold")
        elif key == "italic":
            parts.append("italic")
        elif key == "strikethrough":
            parts.append("strikethrough")
        elif key == "weightedFontFamily":
            parts.append("weightedFontFamily")
        elif key == "link":
            parts.append("link")
    return ",".join(parts)


# ParagraphStyle keys this module emits, each a valid Docs API field name.
_PARAGRAPH_STYLE_FIELDS = frozenset({
    "namedStyleType", "indentStart", "indentFirstLine", "borderBottom",
})


def _paragraph_style_fields(style: dict) -> str:
    """Build the fields mask string for updateParagraphStyle.

    Whitelisted (rather than ``",".join(style.keys())``) so an unexpected key
    can't produce a malformed field mask — mirrors ``_text_style_fields``.
    """
    return ",".join(k for k in style if k in _PARAGRAPH_STYLE_FIELDS)
