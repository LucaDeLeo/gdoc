"""Lightweight markdown parser for Google Docs API batchUpdate requests."""

from __future__ import annotations

import bisect
import html
import html.entities
import re
from dataclasses import dataclass, field


@dataclass
class StyleRange:
    """A formatting annotation within parsed plain text."""

    start: int
    end: int
    style: dict
    type: str  # "text_style", "paragraph_style", "bullets", or inline "image"
    # Bullet-only parser metadata; never sent as native style properties.
    list_block: int | None = None
    list_depth: int = 0
    literal_tabs: int = 0
    list_group: int | None = None
    # Container path of a markdown_prefix range (see container_path).
    path: tuple = ()


@dataclass
class TableData:
    """A parsed markdown table with cell content and position info."""

    rows: list[list[str]]
    num_rows: int
    num_cols: int
    plain_text_offset: int  # code-point offset of the plain_text placeholder
    # Leading list-indent tabs inserted before this table. createParagraphBullets
    # removes those tabs, shifting the table's real position left by this many.
    removed_tabs_before: int = 0
    alignments: list[str | None] = field(default_factory=list)
    # Container path recorded by a named range on the first cell.
    path: tuple = ()


@dataclass
class ImageData:
    """One space placeholder, indexed before list-indent tabs are consumed."""

    plain_text_offset: int
    uri: str
    alt: str
    removed_tabs_before: int = 0
    object_size: dict | None = None


@dataclass
class CodeBlockData:
    """Complete code paragraphs, in code points before list tabs are removed."""

    start: int
    end: int


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
    # Numbering starts that native createParagraphBullets would reset to 1.
    non_default_list_starts: list[str] = field(default_factory=list)
    # List items nested deeper than a Docs list's nine levels.
    deep_list_items: list[str] = field(default_factory=list)
    # The preset of a level-0 native list directly before the insertion that
    # a first list starting the text continues (an item replaced in place).
    continues_list: str | None = None
    code_blocks: list[CodeBlockData] = field(default_factory=list)
    images: list[ImageData] = field(default_factory=list)
    # Per-paragraph pieces of one fenced replacement share this marker, so
    # the paragraphs they replace become one code block.
    code_group: object = None


# Inline patterns — order matters (bold+italic before bold/italic)
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
# An opener must not be followed by whitespace and a closer must not follow
# it (CommonMark flanking), so `2 ** 10 and 3 ** 2` stays literal.
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*|__(?!\s)(.+?)(?<!\s)__")
_ITALIC_RE = re.compile(
    r"(?<!\*)\*(?![\s*])(.+?)(?<![\s*])\*(?!\*)"
    r"|(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])"
)
# Strikethrough uses two tildes; a run of three or more is literal.
_STRIKE_RE = re.compile(r"(?<!~)~~(?![~\s])(.+?)(?<![~\s])~~(?!~)")
# Code spans follow CommonMark: a backtick string of length N (a run neither
# preceded nor followed by a backtick) opens a span that only a backtick string
# of the same length N closes; an unmatched backtick string is literal text.
# Content is inserted verbatim. This is the whole rule for fences on the inline
# path: ```code``` is a code span, and a fence that is never closed by an equal
# run is literal text.
_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((.*)\)")
# A destination followed by a CommonMark title: "t", 't' or (t).
_LINK_TITLE_RE = re.compile(
    r"(<[^<>\n]*>|[^\s<][^\s]*)[ \t\n]+"
    r"(?:\"[^\"]*\"|'[^']*'|\([^()]*\))[ \t]*")
# A <bracketed> link destination, an optional title and the closing ")".
_BRACKETED_DESTINATION_RE = re.compile(
    r"[ \t\n]*<[^<>\n]*>(?:[ \t\n]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?"
    r"[ \t\n]*\)")

# Inline patterns in precedence order. Each entry: (regex, kind). On a tie at
# the same position, the earlier entry wins, so ***x*** beats **x**/*x*.
_INLINE_PATTERNS = [
    (_BOLD_ITALIC_RE, "bolditalic"),
    # ***bold** then italic*: italic whose text opens with bold, as CommonMark
    # reads it; without this the bold match would swallow the italic opener.
    (re.compile(r"(?<!\*)\*(\*\*(?![\s*])[\s\S]+?(?<![\s*])\*\*"
                r"(?:[\s\S]*?(?<![\s*]))?)\*(?!\*)"), "italic"),
    (_BOLD_RE, "bold"),
    (_ITALIC_RE, "italic"),
    (_STRIKE_RE, "strike"),
    (_CODE_RE, "code"),
    (_LINK_RE, "link"),
    (re.compile(r"&#(?:[0-9]+|x[0-9a-fA-F]+);|&[a-zA-Z][a-zA-Z0-9]+;"), "entity"),
    (re.compile(r"<img\b[^>]*>", re.IGNORECASE), "html_image"),
    # Exported run boundaries: no visible text, unlike a space or zero-width char.
    (re.compile(r"<!-- -->"), "separator"),
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
# A lone marker is an empty heading, so a trimmed "## " keeps its level.
_HEADING_RE = re.compile(r"^(#{1,6})(?:[ \t](.*))?$")
# Docs has two named styles with no ordinary Markdown heading equivalent.
_NAMED_STYLE_RE = re.compile(r"^<!-- gdoc:(TITLE|SUBTITLE) -->(?: (.*))?$")

# List item patterns (capture leading indentation for nesting)
_BULLET_RE = re.compile(r"^([ \t]*)[-*+](?:[ \t](.*)|$)")
_NUMBERED_RE = re.compile(r"^([ \t]*)\d+\.(?:[ \t](.*)|$)")

# Block patterns
_BLOCKQUOTE_RE = re.compile(r"^ {0,3}>\s?(.*)$")
_HR_RE = re.compile(r"^ {0,3}([-*_])[ ]*(?:\1[ ]*){2,}$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def _fence_open(line: str) -> re.Match | None:
    """Match a fence opener; a backtick fence's info string holds no backtick.

    CommonMark 4.5: ```` ```code``` ```` on one line is an inline code span,
    not an opening fence, so it must never swallow the rest of the input.
    """
    match = _FENCE_RE.match(line)
    if match and match.group(1)[0] == "`" and "`" in match.group(2):
        return None
    return match

# Table patterns
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:]*-+[\s:]*(\|[\s:]*-+[\s:]*)*\|$")

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


def _code_spans(text: str) -> list[re.Match]:
    """Code spans of ``text``, opened only by unescaped backticks."""
    spans = []
    code_end = 0
    for opener in re.finditer(r"(?<!`)(`+)(?!`)", _mask_escapes(text)):
        if opener.start() < code_end:
            continue
        if code := _CODE_RE.match(text, opener.start()):
            spans.append(code)
            code_end = code.end()
    return spans


def _table_cells(line: str) -> list[str]:
    """Split unescaped pipes, retaining inline escapes for the cell parser."""
    text = line[1:-1]
    masked = _mask_escapes(text)
    # Backslashes are literal inside code spans, where only "\\|" marks a pipe
    # of the content; elsewhere an escaped backslash leaves the pipe bare.
    in_code = [False] * len(text)
    for code in _code_spans(text):
        in_code[code.start():code.end()] = [True] * (code.end() - code.start())
    separators = [
        index for index, char in enumerate(text) if char == "|" and not (
            text[index - 1:index] == "\\" if in_code[index] else masked[index] != "|"
        )
    ]
    boundaries = [-1, *separators, len(text)]
    cells = []
    for start, end in zip(boundaries, boundaries[1:]):
        # Markdown trims only spaces and tabs; other edge whitespace is text.
        cell = text[start + 1:end].strip(" \t")
        parts = []
        cursor = 0
        for code in _code_spans(cell):
            parts.append(re.sub(r"\\.|<br>",
                                lambda m: "\n" if m[0] == "<br>" else m[0],
                                cell[cursor:code.start()]))
            parts.append(code[0].replace(r"\|", "|"))
            cursor = code.end()
        parts.append(re.sub(r"\\.|<br>",
                            lambda m: "\n" if m[0] == "<br>" else m[0],
                            cell[cursor:]))
        cells.append("".join(parts))
    return cells


def parse_inline(text: str) -> tuple[str, list[StyleRange]]:
    """Public: parse inline formatting from a single string.

    For callers (e.g. table-cell rendering and partial-paragraph edits) that
    need inline parsing without the block-level handling of `parse_markdown`.
    Only inline Markdown applies: bold, italic, strikethrough, CommonMark code
    spans, links and images. Image annotations use type="image", one space
    placeholder, and style={"uri": ..., "alt": ...}; native callers consume
    those separately from text-style requests. Every block-level construct
    (fences, list markers,
    headings, blockquotes, thematic breaks) is literal text, because inline
    content cannot start a block. Returns (plain_text, style_ranges) with
    offsets relative to plain_text.
    """
    return _parse_inline(text.replace("\r\n", "\n").replace("\r", "\n"))


def _parse_inline(
    text: str, references: dict | None = None,
) -> tuple[str, list[StyleRange]]:
    """Parse inline formatting from a text string.

    Returns (plain_text, style_ranges) with offsets relative to plain_text.
    Emphasis spans nest recursively; backslash escapes are resolved per
    segment (and left intact inside code spans).
    """
    text = _expand_link_references(text, references or {})
    return _scan(text, _mask_escapes(text), references)


def _link_pairs(masked: str) -> tuple[dict, dict]:
    """Matching bracket and parenthesis positions, paired within each line.

    Pairs inside any suffix are the same as in the whole text: an unmatched
    opener before the suffix never changes how later delimiters nest.
    """
    closes = {}
    brackets = {}
    stack = []
    bracket_stack = []
    for index, char in enumerate(masked):
        if char == "\n":
            stack.clear()
            bracket_stack.clear()
        elif char == "[":
            bracket_stack.append(index)
        elif char == "]" and bracket_stack:
            brackets[bracket_stack.pop()] = index
        elif char == "(":
            stack.append(index)
        elif char == ")" and stack:
            closes[stack.pop()] = index
    return brackets, closes


def _find_link(masked: str, start: int = 0, pairs=None) -> re.Match | None:
    """Find a link ending at its matching destination parenthesis.

    Escaped parentheses are already masked, so only unescaped delimiters
    contribute to depth. An unfinished destination is left as literal text.
    ``pairs`` from ``_link_pairs`` lets repeated searches share one scan.
    """
    brackets, closes = pairs or _link_pairs(masked)
    opener = masked.find("[", start)
    while opener != -1:
        label_end = brackets.get(opener)
        if label_end is not None and masked[label_end + 1:label_end + 2] == "(":
            # A <bracketed> destination may hold unbalanced parentheses; its
            # closing ")" follows the ">" and an optional title.
            bracketed = _BRACKETED_DESTINATION_RE.match(masked, label_end + 2)
            end = (bracketed.end() - 1 if bracketed
                   else closes.get(label_end + 1))
            if end is not None and end > label_end + 2:
                width = label_end - opener - 1
                return re.compile(r"\[([\s\S]{" + str(width)
                                  + r"})\]\(([\s\S]*)\)").match(
                    masked, opener, end + 1,
                )
        opener = masked.find("[", opener + 1)
    return None


def _find_image(masked: str, references: dict):
    """Find a complete inline or defined reference image in linear time."""
    pairs = {}
    brackets = []
    parens = []
    for index, char in enumerate(masked):
        if char == "[":
            brackets.append(index)
        elif char == "]" and brackets:
            pairs[brackets.pop()] = index
        elif char == "(":
            parens.append(index)
        elif char == ")" and parens:
            pairs[parens.pop()] = index
        elif char == "\n":
            parens.clear()
    for opener in re.finditer(r"!\[", masked):
        close = pairs.get(opener.start() + 1)
        if close is None:
            continue
        after = close + 1
        alt = masked[opener.end():close]
        if masked[after:after + 1] == "(":
            end = pairs.get(after)
            if end is not None and end > after + 1:
                width = close - opener.end()
                match = re.compile(r"!\[([\s\S]{" + str(width)
                                   + r"})\]\(([\s\S]*)\)").match(
                    masked, opener.start(), end + 1,
                )
                return match, "image"
        label = alt
        end = after
        if masked[after:after + 1] == "[" and after in pairs:
            end = pairs[after] + 1
            label = masked[after + 1:end - 1] or alt
        if _ref_label(label) in references:
            width = close - opener.end()
            match = re.compile(r"!\[([\s\S]{" + str(width)
                               + r"})\](?:\[([^\]]*)\])?").match(
                masked, opener.start(), end,
            )
            return match, "image_ref"
    return None


def _protect_code(text: str, masked: str) -> str:
    # Code is literal even inside emphasis/link labels. Hide its punctuation
    # from other recognizers so an internal ** cannot close surrounding bold.
    protected = list(masked)
    code_end = 0
    for opener in re.finditer(r"(?<!`)(`+)(?!`)", masked):
        if opener.start() < code_end:
            continue
        code = _CODE_RE.match(text, opener.start())
        if code:
            protected[code.start():code.end()] = _MASK * (code.end() - code.start())
            code_end = code.end()
    return "".join(protected)


def _expand_link_references(text: str, references: dict) -> str:
    """Resolve full, collapsed and shortcut links outside literal code."""
    if not references:
        return text
    masked = _protect_code(text, _mask_escapes(text))
    # Existing image syntax and inline-link destinations are not reference links.
    protected = list(masked)
    cursor = 0
    while found := _find_image(masked[cursor:], references):
        match, _ = found
        start, end = cursor + match.start(), cursor + match.end()
        protected[start:end] = _MASK * (end - start)
        cursor = end
    cursor = 0
    pairs = _link_pairs(masked)
    while match := _find_link(masked, cursor, pairs):
        start, end = match.start(), match.end()
        protected[start:end] = _MASK * (end - start)
        cursor = end
    masked = "".join(protected)
    pattern = re.compile(r"(?<!!)\[([^\[\]]+)\](?:\[([^\]]*)\])?(?!\()")
    pieces = []
    cursor = 0
    for match in pattern.finditer(masked):
        label = match[2] or match[1]
        uri = references.get(_ref_label(label))
        if uri is None:
            continue
        pieces.append(text[cursor:match.start()])
        title = text[match.start(1):match.end(1)]
        destination = "".join("\\" + c if c in "\\()`" else c for c in uri)
        pieces.append(f"[{title}]({destination})")
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _expand_image_references(text: str, references: dict) -> str:
    """Make table-cell images self-contained for the standalone inline parser."""
    if not references:
        return text
    masked = _protect_code(text, _mask_escapes(text))
    # A URL may itself contain image-looking text. Only labels are Markdown.
    protected = list(masked)
    cursor = 0
    pairs = _link_pairs(masked)
    while link := _find_link(masked, cursor, pairs):
        start, end = link.start(2), link.end(2)
        protected[start:end] = _MASK * (end - start)
        cursor = link.end()
    masked = "".join(protected)
    parts = []
    cursor = 0
    while found := _find_image(masked[cursor:], references):
        match, kind = found
        start, end = cursor + match.start(), cursor + match.end()
        parts.append(text[cursor:start])
        if kind == "image_ref":
            alt = text[cursor + match.start(1):cursor + match.end(1)]
            label = match.group(2) or _strip_escapes(alt)
            uri = references[_ref_label(label)]
            destination = "".join("\\" + c if c in "\\()" else c for c in uri)
            parts.append(f"![{alt}]({destination})")
        else:
            parts.append(text[start:end])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def rename_image_references(text: str, renames: dict[str, str]) -> str:
    """Rename ``gdoc-image:ID`` only where it is an inline image destination.

    Fenced code, code spans, escaped syntax, link destinations and prose keep
    the literal token.
    """
    if not renames or "gdoc-image:" not in text:
        return text

    def rename(chunk: str) -> str:
        masked = _protect_code(chunk, _mask_escapes(chunk))
        protected = list(masked)
        cursor = 0
        pairs = _link_pairs(masked)
        while link := _find_link(masked, cursor, pairs):
            start, end = link.start(2), link.end(2)
            protected[start:end] = _MASK * (end - start)
            cursor = link.end()
        masked = "".join(protected)
        parts = []
        cursor = 0
        while found := _find_image(masked[cursor:], {}):
            match = found[0]
            start, end = cursor + match.start(2), cursor + match.end(2)
            destination = chunk[start:end]
            target = re.fullmatch(r"(\s*<?gdoc-image:)([A-Za-z0-9_.-]+)(>?\s*)",
                                  destination)
            parts.append(chunk[cursor:start])
            if target and target[2] in renames:
                destination = target[1] + renames[target[2]] + target[3]
            parts.append(destination)
            cursor = end
        parts.append(chunk[cursor:])
        return "".join(parts)

    out = []
    prose = []
    fence = None
    for line in re.findall(r"[^\n]*\n|[^\n]+", text):
        bare = re.sub(r"^(?:[ \t]*>)*[ \t]*", "", line.rstrip("\r\n"))
        if fence is not None:
            out.append(line)
            close = _FENCE_CLOSE_RE.match(bare)
            if close and close[1][0] == fence[0] and len(close[1]) >= len(fence):
                fence = None
            continue
        opener = _fence_open(bare)
        if opener:
            out.append(rename("".join(prose)))
            prose.clear()
            out.append(line)
            fence = opener[1]
            continue
        prose.append(line)
    out.append(rename("".join(prose)))
    return "".join(out)


class _ImageMatch:
    """An image finder result that caches like a match (see ``_scan``)."""

    def __init__(self, found):
        self.match, self.kind = found

    @classmethod
    def wrap(cls, found):
        return cls(found) if found is not None else None

    def start(self, group=0):
        return self.match.start(group)


def _scan(
    text: str, masked: str, references: dict | None = None,
) -> tuple[str, list[StyleRange]]:
    """Recursively parse inline formatting.

    ``text`` is the original source; ``masked`` is the same length with escaped
    markers blanked to a sentinel. The regexes run against ``masked``; emitted
    text is sliced from ``text``. Escaping backslashes are stripped from normal
    content but PRESERVED inside code spans (code is literal — CommonMark).
    Spans recurse so emphasis can nest (e.g. ``**bold _and italic_**``).
    Returns (plain_text, [StyleRange]) with offsets relative to plain_text.
    """
    references = references or {}
    protected = _protect_code(text, masked)
    plain_parts: list[str] = []
    styles: list[StyleRange] = []
    offset = 0
    pos = 0
    n = len(masked)

    # Each pattern's last result, with the offset its slice started at. A
    # later slice only differs at its first character (lookbehinds there see
    # nothing), so a result stays valid unless consumed; only a match at the
    # new start needs checking. This avoids repeating every pattern's search
    # after each span (slicing the tail still copies it).
    cache: dict = {}
    link_pairs = None

    def found(key, search, anchored=None):
        entry = cache.get(key)
        if entry is not None:
            m, base = entry
            if m is None or base + m.start() >= pos:
                first = anchored() if anchored is not None else None
                if first is not None and first.start() == 0:
                    cache[key] = (first, pos)
                    return first, pos
                return entry
        cache[key] = (search(), pos)
        return cache[key]

    while pos < n:
        # Search the unconsumed tail (a fresh slice), not masked[pos:] via the
        # pos argument: a lookbehind (`(?<!\*)`) would otherwise read the
        # just-consumed marker before `pos` and wrongly block a span that abuts
        # it (e.g. the `*b*` in `**a***b*`). Match offsets are relative to the
        # slice they came from, so shift them by that slice's start.
        tail = protected[pos:]
        image, image_base = found("image", lambda: _ImageMatch.wrap(
            _find_image(tail, references)))
        best = (image.match, image.kind, image_base) if image is not None else None
        for index, (pat, kind) in enumerate(_INLINE_PATTERNS):
            if kind == "code":
                def search_code(first_only=False):
                    if first_only and not masked.startswith("`", pos):
                        return None
                    raw_tail = text[pos:]
                    for opener in re.finditer(r"(?<!`)(`+)(?!`)", masked[pos:]):
                        if first_only and opener.start():
                            return None
                        m = pat.match(raw_tail, opener.start())
                        if m is not None or first_only:
                            return m
                    return None
                m, base = found(index, search_code, lambda: search_code(True))
            elif kind == "link":
                # Searched in the whole text with shared pairs; offsets are
                # absolute. A link never depends on a lookbehind.
                cached = cache.get("link")
                if "[" not in protected:
                    cache["link"] = (None, 0)
                elif cached is None or (cached[0] is not None
                                        and cached[0].start() < pos):
                    if link_pairs is None:
                        link_pairs = _link_pairs(protected)
                    cache["link"] = (_find_link(protected, pos, link_pairs), 0)
                m, base = cache["link"]
            else:
                m, base = found(index, lambda pat=pat: pat.search(tail),
                                lambda pat=pat: pat.match(tail))
            if m is not None and (best is None or base + m.start()
                                  < best[2] + best[0].start()):
                best = (m, kind, base)
        if best is None:
            plain_parts.append(_strip_escapes(text[pos:]))
            break

        m, kind, base = best
        m_start = base + m.start()
        if m_start > pos:
            lit = _strip_escapes(text[pos:m_start])
            plain_parts.append(lit)
            offset += len(lit)

        def _grp(group: int, m=m, base=base) -> tuple[int, int]:
            return base + m.start(group), base + m.end(group)

        seg_start = offset
        if kind == "html_image":
            from gdoc.util import GdocError

            raise GdocError("Use Markdown image syntax instead of HTML img tags",
                            exit_code=3)
        if kind == "separator":
            pass
        elif kind == "entity":
            # Only a complete HTML5 name is an entity; html.unescape would
            # also decode a legacy prefix (`&notes;` as `¬es;`).
            literal = (html.unescape(m[0]) if m[0].startswith("&#")
                       or m[0][1:] in html.entities.html5 else m[0])
            plain_parts.append(literal)
            offset += len(literal)
        elif kind == "code":
            # Code spans are literal (backslashes kept), normalised per
            # CommonMark 6.1: line endings become spaces, and one leading
            # plus one trailing space is dropped when both are present and
            # the content is not all spaces.
            a, b = _grp(2)
            inner = text[a:b].replace("\n", " ")
            if len(inner) >= 2 and inner[0] == inner[-1] == " " \
                    and inner.strip(" "):
                inner = inner[1:-1]
            plain_parts.append(inner)
            offset += len(inner)
            styles.append(StyleRange(seg_start, offset, _CODE_FONT, "text_style"))
        elif kind in ("image", "image_ref"):
            a, b = _grp(1)
            alt = _strip_escapes(text[a:b])
            if kind == "image":
                ua, ub = _grp(2)
                # As for links, surrounding whitespace and a CommonMark title
                # are not part of the URI.
                ua += len(masked[ua:ub]) - len(masked[ua:ub].lstrip(" \t\n"))
                ub = max(ua, ub - (len(masked[ua:ub])
                                   - len(masked[ua:ub].rstrip(" \t\n"))))
                destination = text[ua:ub]
                titled = _LINK_TITLE_RE.fullmatch(masked[ua:ub])
                if titled:
                    destination = destination[:titled.end(1)]
                uri = _strip_escapes(destination)
                if uri.startswith("<") and uri.endswith(">"):
                    uri = uri[1:-1]
            else:
                label = m.group(2) or alt
                uri = references[_ref_label(label)]
            plain_parts.append(" ")
            offset += 1
            styles.append(StyleRange(
                seg_start, offset, {"uri": uri, "alt": alt}, "image",
            ))
        elif kind == "link":
            a, b = _grp(1)
            sub_plain, sub_styles = _scan(text[a:b], masked[a:b], references)
            plain_parts.append(sub_plain)
            offset += len(sub_plain)
            for s in sub_styles:
                styles.append(StyleRange(
                    s.start + seg_start, s.end + seg_start, s.style, s.type,
                ))
            ua, ub = _grp(2)
            # Whitespace around the destination is not part of it.
            ua += len(masked[ua:ub]) - len(masked[ua:ub].lstrip(" \t\n"))
            ub = max(ua, ub - (len(masked[ua:ub]) - len(masked[ua:ub].rstrip(" \t\n"))))
            destination = text[ua:ub]
            # A CommonMark link title after the destination is not part of the
            # URL; Docs links have no title, so it is dropped.
            titled = _LINK_TITLE_RE.fullmatch(masked[ua:ub])
            if titled:
                destination = destination[:titled.end(1)]
            url = _strip_escapes(destination)
            if url.startswith("<") and url.endswith(">"):
                url = url[1:-1]  # CommonMark's bracketed destination, as for images
            styles.append(StyleRange(
                seg_start, offset, {"link": {"url": url}}, "text_style",
            ))
        else:
            # bold / italic alternations capture group 1 or 2; others, group 1.
            g = 2 if (kind in ("bold", "italic") and m.group(1) is None) else 1
            a, b = _grp(g)
            sub_plain, sub_styles = _scan(text[a:b], masked[a:b], references)
            plain_parts.append(sub_plain)
            offset += len(sub_plain)
            for s in sub_styles:
                styles.append(StyleRange(
                    s.start + seg_start, s.end + seg_start, s.style, s.type,
                ))
            for sd in _STYLES_FOR_KIND[kind]:
                styles.append(StyleRange(seg_start, offset, sd, "text_style"))

        pos = base + m.end()

    return "".join(plain_parts), styles


# A Docs list has at most nine nesting levels (ListProperties.nestingLevels).
MAX_LIST_LEVEL = 8


def _list_level(indent: str) -> int:
    """Nesting level from a list item's leading whitespace.

    Two columns (or one tab) per level; capped at the ninth level (index 8).
    """
    return min(_raw_list_level(indent), MAX_LIST_LEVEL)


def _raw_list_level(indent: str) -> int:
    return len(indent.replace("\t", "  ")) // 2


def _ref_label(label: str) -> str:
    return " ".join(label.split()).casefold()



def _unquote(line: str, limit: int | None = None) -> tuple[str, int]:
    depth = 0
    while limit is None or depth < limit:
        match = _BLOCKQUOTE_RE.match(line)
        if not match:
            break
        line = match[1]
        depth += 1
    return line, depth


def path_lead(path: tuple) -> str:
    """The Markdown line prefix of a container path.

    A path lists a paragraph's containers, outermost first: ``"q"`` is a
    quote marker and an integer is a list item's content indent in spaces.
    """
    return "".join("> " if token == "q" else " " * token for token in path)


def legacy_prefix(path: tuple) -> tuple[int, int, int] | None:
    """``(quotes, indent, outer)`` when a path has a v1/v2 range name."""
    path = tuple(path)
    outer = 0
    if path and path[0] != "q" and len(path) > 1 and path[1] == "q":
        outer, path = path[0], path[1:]
    quotes = 0
    while quotes < len(path) and path[quotes] == "q":
        quotes += 1
    rest = path[quotes:]
    if not rest:
        return quotes, 0, outer
    if len(rest) == 1 and rest[0] != "q" and (quotes or not outer):
        return quotes, rest[0], outer
    return None


def _quoted_in_item(path: tuple) -> bool:
    """Whether some quote in *path* sits inside a list item's content."""
    if "q" not in path:
        return False
    last = len(path) - 1 - path[::-1].index("q")
    return any(token != "q" for token in path[:last])


def _strip_path(line: str, path: tuple, lenient: bool = False) -> str | None:
    """A line with its container markers removed, or None when outside them.

    A blank line stays inside list item content. ``lenient`` (fenced code)
    also accepts a missing quote marker and a shallower indent.
    """
    for token in path:
        if token == "q":
            match = _BLOCKQUOTE_RE.match(line)
            if match:
                line = match[1]
            elif not (lenient or not line.strip()):
                return None
            elif not line.strip():
                line = ""
        else:
            spaces = len(line) - len(line.lstrip(" "))
            if spaces >= token:
                line = line[token:]
            elif lenient or not line.strip():
                line = line[spaces:]
            else:
                return None
    return line


def _list_item_line(text: str) -> bool:
    return bool(_BULLET_RE.match(text) or _NUMBERED_RE.match(text)) and not \
        _HR_RE.match(text)


def parse_markdown(text: str) -> ParsedMarkdown:
    """Parse markdown text into plain text + style annotations.

    Handles: headings (H1-H6), bullet/numbered lists (nested), bold, italic,
    bold+italic, strikethrough, inline code, links, blockquotes, horizontal
    rules, fenced code blocks, and tables. The exporter also uses explicit
    ``<!-- gdoc:TITLE -->`` / ``<!-- gdoc:SUBTITLE -->`` paragraph prefixes.
    """
    if not text:
        return ParsedMarkdown(plain_text="")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # A terminal LF closes the last paragraph; it is not an extra blank one.
    # Preserve additional LFs, each of which represents a real empty paragraph.
    lines = text.removesuffix("\n").split("\n")
    references = {}
    definition_lines = set()
    fence = None
    for line_number, line in enumerate(lines):
        line, _ = _unquote(line)
        line = line.lstrip(" ")
        if fence is not None:
            closer = _FENCE_CLOSE_RE.match(line)
            if closer and closer[1][0] == fence[0] and len(closer[1]) >= len(fence):
                fence = None
            continue
        opener = _fence_open(line)
        if opener:
            fence = opener[1]
            continue
        # Escaped brackets cannot delimit a definition's label.
        # A trailing CommonMark title ("t", 't' or (t)) is allowed and dropped.
        # A destination in angle brackets may contain spaces, as inline.
        masked = re.fullmatch(r" {0,3}\[([^\]]+)\]:[ \t]*(<[^<>\n]*>|\S+)"
                              r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*",
                              _mask_escapes(line))
        definition = masked and (line[masked.start(1):masked.end(1)],
                                 line[masked.start(2):masked.end(2)])
        if definition and (re.match(r"<?[a-zA-Z][a-zA-Z0-9+.-]*:", definition[1])
                           or re.search(r"!\[[^\]]*\]\[" + re.escape(definition[0])
                                        + r"\]", text)):
            uri = _strip_escapes(definition[1])
            references[_ref_label(definition[0])] = (
                uri.removeprefix("<").removesuffix(">")
            )
            definition_lines.add(line_number)
    # Trailing reference definitions, the blank lines among and after them,
    # and one blank line separating them from the text are not paragraphs of
    # the document; further blank lines before them are blank paragraphs.
    tail = len(lines)
    while tail and (tail - 1 in definition_lines or not lines[tail - 1].strip()):
        tail -= 1
    first = next((n for n in range(tail, len(lines)) if n in definition_lines), None)
    if first is not None:
        start = first - 1 if first > tail else first
        definition_lines.update(range(start, len(lines)))
    plain_parts: list[str] = []
    all_styles: list[StyleRange] = []
    all_tables: list[TableData] = []
    images: list[ImageData] = []
    code_blocks: list[CodeBlockData] = []
    offset = 0
    removed_tabs = 0  # running count of leading list-indent tabs (see below)
    list_levels: dict[int, tuple[str, int]] = {}
    list_block = 0
    last_list_end = 0
    groups: dict[tuple[int, str], tuple[int, int]] = {}
    next_group = 0
    # Each container path has its own open lists: a list quoted inside an
    # item is separate from the item's list, which resumes after the quote.
    contexts: dict[tuple, tuple[dict, dict]] = {(): (list_levels, groups)}
    container: tuple = ()  # the current paragraph's container path
    context_blocks: dict[tuple, int] = {}  # each context's current list block
    non_default_list_starts: list[str] = []
    deep_list_items: list[str] = []

    def note_deep(indent: str, content: str) -> None:
        level = _raw_list_level(indent)
        if level > MAX_LIST_LEVEL:
            deep_list_items.append(
                f"list item at line {i + 1} ({content!r}) is nested "
                f"{level + 1} levels deep (written at level {MAX_LIST_LEVEL + 1})"
            )

    def enter(path: tuple) -> None:
        """Make *path* current, ending the lists of containers it left."""
        nonlocal list_levels, groups, container
        for key in list(contexts):
            if key != path[:len(key)]:
                del contexts[key]
        list_levels, groups = contexts.setdefault(path, ({}, {}))
        container = path

    def container_path(line: str) -> tuple[tuple, str]:
        """Split a line into its container path and its content.

        Quote markers open quotes. Inside an open list, an indented line that
        is not itself a list item belongs to the last item's content.
        """
        path: tuple = ()
        while True:
            levels = contexts.get(path, ({},))[0]
            spaces = len(line) - len(line.lstrip(" "))
            body = line[spaces:]
            if levels and spaces and not _list_item_line(body):
                # Content of an item nested at depth d sits inside d + 1 items:
                # one token per enclosing item (two spaces per nesting level,
                # then the item's own content indent), so native indentation
                # counts every level while the Markdown prefix is unchanged.
                depth = min((spaces - 1) // 2, max(levels))
                path += (2,) * depth + (spaces - 2 * depth,)
                line = body
                continue
            quoted = _BLOCKQUOTE_RE.match(line)
            if quoted:
                path += ("q",)
                line = quoted[1]
                continue
            return path, line

    def emit_paragraph(
        content: str,
        content_styles: list[StyleRange],
        para_style: dict,
        bullet_preset: str | None = None,
        leading_tabs: int = 0,
        start_number: int = 1,
    ) -> None:
        """Append one paragraph (content + newline) and its style ranges.

        ``leading_tabs`` prepends tabs for list nesting; createParagraphBullets
        counts and removes them at apply time (tracked via ``removed_tabs``).
        """
        nonlocal offset, removed_tabs, list_block, last_list_end, next_group
        group = None
        if bullet_preset is not None:
            # A parent's next item starts a new child sequence. At the same
            # depth, ordered items may continue across bullets or prose.
            for key in list(groups):
                if key[0] > leading_tabs:
                    del groups[key]
            if bullet_preset.startswith("NUMBERED"):
                for key in list(groups):
                    if key[0] == leading_tabs and not key[1].startswith("NUMBERED"):
                        del groups[key]
            key = (leading_tabs, bullet_preset)
            previous = groups.get(key)
            ordered = bullet_preset.startswith("NUMBERED")
            continuation = previous and (not ordered or start_number == previous[1] + 1)
            if not continuation:
                next_group += 1
                group = next_group
                if ordered and start_number != 1:
                    non_default_list_starts.append(
                        f"numbered list at line {i + 1} ({content!r}) "
                        f"starts at {start_number} (reset to 1)"
                    )
            else:
                group = previous[0]
            groups[key] = (group, start_number)
        if bullet_preset is None:
            # Blank paragraphs may separate items of the same numbered list.
            if content:
                list_levels.clear()
                # A quote opened directly in a container interrupts its lists;
                # content inside a list item does not.
                for depth in range(len(container)):
                    if container[depth] == "q" and container[:depth] in contexts:
                        contexts[container[:depth]][0].clear()
        else:
            previous = list_levels.get(leading_tabs)
            ordered = bullet_preset.startswith("NUMBERED")
            across_blank = last_list_end != offset
            restart = leading_tabs == 0 and previous and ordered and start_number == 1
            continuation = ordered and previous == (bullet_preset, start_number - 1)
            if restart or (leading_tabs == 0 and across_blank and not continuation):
                list_levels.clear()
            if not list_levels:
                list_block += 1
                context_blocks[container] = list_block
            for level in list(list_levels):
                if level > leading_tabs:
                    del list_levels[level]
            previous = list_levels.get(leading_tabs)
            number = previous[1] + 1 if previous and previous[0] == bullet_preset else 1
            list_levels[leading_tabs] = (bullet_preset, number)
        para_start = offset
        if container:
            para_style = dict(para_style)
            indent = {"magnitude": 36 * len(container), "unit": "PT"}
            para_style.update(indentStart=indent, indentFirstLine=indent)
        if leading_tabs:
            plain_parts.append("\t" * leading_tabs)
            offset += leading_tabs
            removed_tabs += leading_tabs
        text_start = offset
        plain_parts.append(content)
        offset += len(content)
        for s in content_styles:
            if s.type == "image":
                images.append(ImageData(
                    s.start + text_start, s.style["uri"], s.style["alt"],
                    removed_tabs,
                ))
                continue
            all_styles.append(StyleRange(
                s.start + text_start, s.end + text_start, s.style, s.type,
            ))
        plain_parts.append("\n")
        offset += 1
        all_styles.append(StyleRange(
            para_start, offset, para_style, "paragraph_style",
        ))
        if container:
            legacy = legacy_prefix(container)
            prefix = ({"quote": legacy[0], "indent": legacy[1]}
                      if legacy else {"path": container})
            if legacy and legacy[2]:
                prefix["outer"] = legacy[2]
            all_styles.append(StyleRange(
                para_start, offset, prefix, "markdown_prefix", path=container,
            ))
        if bullet_preset is not None:
            all_styles.append(StyleRange(
                para_start, offset,
                {"bulletPreset": bullet_preset}, "bullets",
                # A list resumed after a nested container keeps its own block.
                list_block=context_blocks.get(container, list_block),
                list_depth=leading_tabs, list_group=group,
                literal_tabs=len(content) - len(content.lstrip("\t")),
            ))
            last_list_end = offset

    i = 0
    table_separators: set[int] = set()
    while i < len(lines):
        if i in definition_lines or i in table_separators:
            i += 1
            continue
        path, line = container_path(lines[i])
        if not line.strip(" \t") and path and path[-1] != "q":
            # A blank line inside list item content is a blank paragraph; only
            # its quote markers are containers (see get_tab_text).
            while path and path[-1] != "q":
                path = path[:-1]
            line = ""
        enter(path)

        # Fenced code block: ``` (or ~~~) ... ```
        fence_indent = len(line) - len(line.lstrip(" "))
        fence_m = _fence_open(line)
        if fence_m:
            list_levels.clear()
            fence = fence_m.group(1)
            fence_char = fence[0]
            code_start = offset
            i += 1
            while i < len(lines):
                code_line = _strip_path(lines[i], path, lenient=True)
                # Fence indentation belongs to the container, not the code.
                strip = min(fence_indent, len(code_line) - len(code_line.lstrip(" ")))
                code_line = code_line[strip:]
                close = _FENCE_CLOSE_RE.match(code_line)
                if close:
                    close_fence = close.group(1)
                    if close_fence[0] == fence_char and len(
                        close_fence
                    ) >= len(fence):
                        i += 1
                        break
                styles = (
                    [StyleRange(0, len(code_line), _CODE_FONT, "text_style")]
                    if code_line else []
                )
                emit_paragraph(
                    code_line, styles, {"namedStyleType": "NORMAL_TEXT"},
                )
                i += 1
            if offset == code_start:
                emit_paragraph("", [], {"namedStyleType": "NORMAL_TEXT"})
            code_blocks.append(CodeBlockData(code_start, offset))
            continue

        # Table: header row + separator row + data rows. Tables may sit inside
        # quotes and, indented, inside list items; the container is recorded.
        def table_line(j, path=path):
            if j >= len(lines):
                return ""
            text = _strip_path(lines[j], path)
            if text is None or not text.strip():
                return ""
            # As in GFM, a row may be indented up to three spaces and end in
            # whitespace.
            text = text.rstrip(" \t")
            indent = len(text) - len(text.lstrip(" "))
            return text[indent:] if indent <= 3 else text

        header_line = table_line(i)
        if (
            _TABLE_ROW_RE.match(header_line)
            and _TABLE_SEP_RE.match(table_line(i + 1))
        ):
            table_rows: list[list[str]] = []
            list_levels.clear()
            header_cells = _table_cells(header_line)
            table_rows.append(header_cells)
            num_cols = len(header_cells)
            alignments = []
            for separator in _table_cells(table_line(i + 1)):
                if separator.startswith(":") and separator.endswith(":"):
                    alignments.append("CENTER")
                elif separator.startswith(":"):
                    alignments.append("START")
                elif separator.endswith(":"):
                    alignments.append("END")
                else:
                    alignments.append(None)
            alignments = (alignments + [None] * num_cols)[:num_cols]
            i += 2  # skip header + separator
            # Only the line after the header is a separator, so a data row of
            # dashes stays a row, as in GFM.
            while i < len(lines) and _TABLE_ROW_RE.match(table_line(i)):
                cells = _table_cells(table_line(i))
                if len(cells) < num_cols:
                    cells.extend([""] * (num_cols - len(cells)))
                elif len(cells) > num_cols:
                    cells = cells[:num_cols]
                table_rows.append(cells)
                i += 1

            table_rows = [[_expand_link_references(
                _expand_image_references(cell, references), references) for cell in row]
                          for row in table_rows]
            para_start = offset
            all_tables.append(TableData(
                rows=table_rows,
                num_rows=len(table_rows),
                num_cols=num_cols,
                plain_text_offset=offset,
                removed_tabs_before=removed_tabs,
                alignments=alignments,
                path=path,
            ))
            # Canonical adjacent tables: the last blank line before a following
            # table in the same container separates them; earlier blank or
            # whitespace-only lines are paragraphs.
            j = i
            while j < len(lines):
                text = _strip_path(lines[j], path)
                if text is None or text.strip():
                    break
                j += 1
            if (j > i and _TABLE_ROW_RE.match(table_line(j))
                    and _TABLE_SEP_RE.match(table_line(j + 1))):
                table_separators.add(j - 1)
            plain_parts.append("\n")
            offset += 1
            all_styles.append(StyleRange(
                start=para_start, end=offset,
                style={"namedStyleType": "NORMAL_TEXT"},
                type="paragraph_style",
            ))
            continue

        named_m = _NAMED_STYLE_RE.match(line)
        if named_m:
            inline_text, inline_styles = _parse_inline(
                named_m.group(2) or "", references,
            )
            emit_paragraph(
                inline_text, inline_styles, {"namedStyleType": named_m.group(1)},
            )
            i += 1
            continue

        # Heading
        heading_m = _HEADING_RE.match(line)
        if heading_m:
            level = len(heading_m.group(1))
            inline_text, inline_styles = _parse_inline(
                heading_m.group(2) or "", references,
            )
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

        # Bullet list item (indent-aware)
        bullet_m = _BULLET_RE.match(line)
        if bullet_m:
            item = bullet_m.group(2) or ""
            heading = _HEADING_RE.match(item)
            named = _NAMED_STYLE_RE.match(item)
            item_style = "NORMAL_TEXT"
            if heading:
                item_style = f"HEADING_{len(heading.group(1))}"
                item = heading.group(2) or ""
            elif named:
                item_style = named.group(1)
                item = named.group(2) or ""
            inline_text, inline_styles = _parse_inline(item, references)
            emit_paragraph(
                inline_text, inline_styles,
                {"namedStyleType": item_style},
                bullet_preset="BULLET_DISC_CIRCLE_SQUARE",
                leading_tabs=_list_level(bullet_m.group(1)),
            )
            note_deep(bullet_m.group(1), item)
            i += 1
            continue

        # Numbered list item (indent-aware)
        numbered_m = _NUMBERED_RE.match(line)
        if numbered_m:
            item = numbered_m.group(2) or ""
            heading = _HEADING_RE.match(item)
            named = _NAMED_STYLE_RE.match(item)
            item_style = "NORMAL_TEXT"
            if heading:
                item_style = f"HEADING_{len(heading.group(1))}"
                item = heading.group(2) or ""
            elif named:
                item_style = named.group(1)
                item = named.group(2) or ""
            inline_text, inline_styles = _parse_inline(item, references)
            emit_paragraph(
                inline_text, inline_styles,
                {"namedStyleType": item_style},
                bullet_preset="NUMBERED_DECIMAL_ALPHA_ROMAN",
                leading_tabs=_list_level(numbered_m.group(1)),
                start_number=int(line.lstrip().split(".", 1)[0]),
            )
            note_deep(numbered_m.group(1), item)
            i += 1
            continue

        # Normal paragraph line. Explicit NORMAL_TEXT so inserted paragraphs
        # don't inherit the style of the paragraph at the insertion point.
        inline_text, inline_styles = _parse_inline(line, references)
        emit_paragraph(
            inline_text, inline_styles, {"namedStyleType": "NORMAL_TEXT"},
        )
        i += 1

    return ParsedMarkdown(
        plain_text="".join(plain_parts),
        styles=all_styles,
        tables=all_tables,
        removed_tabs=removed_tabs,
        non_default_list_starts=non_default_list_starts,
        deep_list_items=deep_list_items,
        code_blocks=code_blocks,
        images=images,
    )


def to_docs_requests(
    parsed: ParsedMarkdown,
    insert_index: int,
    tab_id: str | None = None,
    *, include_lists: bool = True,
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
            "text": _list_insert_text(parsed),
        }
    })

    # 2. Paragraph styles (named styles, indents, borders). Applied before text
    #    styles because a `namedStyleType` re-resolves a run's direct character
    #    formatting and would clear bold/italic set afterwards.
    for sr in parsed.styles:
        if sr.type == "paragraph_style" and sr.end > sr.start:
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

    if include_lists:
        requests.extend(list_requests(parsed, insert_index, tab_id))
        requests.extend(prefix_indent_requests(parsed, insert_index, tab_id))

    return requests


def prefix_indent_requests(parsed, insert_index, tab_id):
    """Restore container indentation after native bullet creation changes it."""
    requests = []
    bullets = [s for s in parsed.styles if s.type == "bullets"]
    for prefix in (s for s in parsed.styles if s.type == "markdown_prefix"):
        bullet = next((b for b in bullets if b.start == prefix.start), None)
        level = len(prefix.path) + (bullet.list_depth + 1 if bullet else 0)
        removed_before = sum(b.list_depth for b in bullets if b.start < prefix.start)
        removed_end = sum(b.list_depth for b in bullets if b.start < prefix.end)
        start = (insert_index + utf16_len(parsed.plain_text[:prefix.start])
                 - removed_before)
        end = insert_index + utf16_len(parsed.plain_text[:prefix.end]) - removed_end
        span = {"startIndex": start, "endIndex": max(start + 1, end)}
        if tab_id:
            span["tabId"] = tab_id
        requests.append({"updateParagraphStyle": {
            "range": span,
            "paragraphStyle": {
                "indentStart": {"magnitude": 36 * level, "unit": "PT"},
                "indentFirstLine": {"magnitude": 36 * level - (18 if bullet else 0),
                                    "unit": "PT"},
            },
            "fields": "indentStart,indentFirstLine",
        }})
    return requests


def _list_insert_text(parsed: ParsedMarkdown) -> str:
    """Shield content tabs from the API's destructive nesting-tab scan."""
    text = list(parsed.plain_text)
    for item in parsed.styles:
        if item.type == "bullets" and item.literal_tabs:
            start = item.start + item.list_depth
            text[start:start + item.literal_tabs] = " " * item.literal_tabs
    for start, tabs in _unlisted_leading_tabs(parsed):
        text[start:start + tabs] = " " * tabs
    return "".join(text)


def _unlisted_leading_tabs(parsed: ParsedMarkdown) -> list[tuple[int, int]]:
    """Leading tabs of paragraphs that are not list items, such as code lines.

    A bullet request spanning a list-contained code block or continuation
    paragraph would consume those tabs as nesting, so they are inserted as
    spaces and restored after every bullet request.
    """
    if not any(s.type == "bullets" for s in parsed.styles):
        return []
    items = {s.start for s in parsed.styles if s.type == "bullets"}
    found = []
    for style in parsed.styles:
        if style.type != "paragraph_style" or style.start in items:
            continue
        line = parsed.plain_text[style.start:style.end]
        tabs = len(line) - len(line.lstrip("\t"))
        if tabs:
            found.append((style.start, tabs))
    return found


def _restore_unlisted_tabs(parsed: ParsedMarkdown, insert_index: int,
                           tab_id: str | None) -> list[dict]:
    items = [s for s in parsed.styles if s.type == "bullets"]
    offsets = _utf16_prefix(parsed.plain_text)
    requests = []
    for point, tabs in reversed(_unlisted_leading_tabs(parsed)):
        # Nesting tabs of earlier items were consumed by the bullet requests.
        start = insert_index + offsets[point] - sum(
            item.list_depth for item in items if item.start < point)
        span = {"startIndex": start, "endIndex": start + tabs}
        location = {"index": start + tabs}
        if tab_id:
            span["tabId"] = location["tabId"] = tab_id
        # Inserted after the placeholder spaces, the tabs take their style.
        requests.extend([
            {"insertText": {"location": location, "text": "\t" * tabs}},
            {"deleteContentRange": {"range": span}},
        ])
    return requests


def list_requests(parsed: ParsedMarkdown, insert_index: int,
                  tab_id: str | None = None) -> list[dict]:
    """Bullet requests, then the shielded tabs of non-item paragraphs."""
    return (_list_bullet_requests(parsed, insert_index, tab_id)
            + _restore_unlisted_tabs(parsed, insert_index, tab_id))


def _list_bullet_requests(parsed: ParsedMarkdown, insert_index: int,
                          tab_id: str | None = None) -> list[dict]:
    """Create each native list with the levels and identity its Markdown gives."""
    items = sorted((s for s in parsed.styles if s.type == "bullets"),
                   key=lambda s: s.start)
    if not items:
        return []
    return _native_list_requests(parsed, insert_index, tab_id, items,
                                 _native_lists(parsed, items))


def _native_lists(parsed: ParsedMarkdown, items: list) -> list[list]:
    """Partition list items into native lists, each with one preset.

    A block of items with one preset, whose nested sublists are separated by
    their parents, is one native list, as Docs' own Tab key makes it. Any
    other block (mixed bullet types, nested restarts, or numbering that
    continues across another list or prose) gets one native list per
    Markdown list, so numbering continues exactly where the Markdown does.
    """
    blocks: list[list] = []
    for item in items:
        if blocks and (
            item.list_block is not None
            and blocks[-1][-1].list_block == item.list_block
            or item.list_block is None and blocks[-1][-1].end == item.start
        ):
            blocks[-1].append(item)
        else:
            blocks.append([item])
    if not _needs_list_per_group(parsed, items, blocks):
        return blocks
    groups: dict = {}
    for item in items:
        key = item.list_group
        if key is None:
            key = (item.list_block, item.list_depth, item.style["bulletPreset"])
        groups.setdefault(key, []).append(item)
    return list(groups.values())


def _needs_list_per_group(parsed: ParsedMarkdown, items: list, blocks: list) -> bool:
    if any(len({item.style["bulletPreset"] for item in block}) > 1
           for block in blocks):
        return True
    active = {}
    previous = {}
    latest = {}
    objects = {t.plain_text_offset for t in parsed.tables} | {
        image.plain_text_offset for image in parsed.images} | {
        s.start for s in parsed.styles if s.type == "image"}
    for item in items:
        for depth in list(active):
            if depth > item.list_depth:
                del active[depth]
        # A list resumed after another list at its depth or shallower (such as
        # one quoted inside its item) needs its own identity across the gap.
        resumed = latest.get(item.list_group)
        if resumed is not None and item.list_group is not None and any(
                other.list_group != item.list_group
                and other.list_depth <= item.list_depth
                and resumed.start < other.start < item.start for other in items):
            return True
        latest[item.list_group] = item
        if not item.style["bulletPreset"].startswith("NUMBERED"):
            continue
        prior = active.get(item.list_depth)
        if item.list_depth and prior and prior != item.list_group:
            return True
        active[item.list_depth] = item.list_group
        earlier = previous.get(item.list_group)
        if earlier is not None:
            between = [s for s in parsed.styles
                       if earlier.end <= s.start < item.start]
            interleaved = any(s.type == "bullets" and
                              s.list_depth <= item.list_depth for s in between)
            # A table or image between items interrupts the list like prose.
            prose = any(
                s.type == "paragraph_style"
                and (parsed.plain_text[s.start:s.end].strip()
                     or any(s.start <= o < s.end for o in objects))
                and not any(b.start == s.start for b in items)
                for s in between
            )
            if interleaved or prose:
                return True
        previous[item.list_group] = item
    return False


def _native_list_requests(parsed: ParsedMarkdown, insert_index: int,
                          tab_id: str | None, items: list,
                          lists: list[list]) -> list[dict]:
    """Bullet requests that give every item its list and absolute level.

    createParagraphBullets sets levels relative to its range: a paragraph's
    level is its leading tabs plus its indent (in 36pt steps) minus the least
    in the range, plus the level of the paragraph before the range when the
    range joins that paragraph's list of the same preset. It also converts or
    joins a list already on a paragraph in its range (both observed live).

    So each native list is created in one pinned sequence, top to bottom,
    behind two temporary paragraphs: an unbulleted separator, so the range
    never joins a list above it (unless ``parsed.continues_list`` asks the
    first list to continue a level-0 list there), and an empty anchor with
    no tabs or indent as the range's first paragraph, so each item's tabs
    are its absolute level. Paragraphs the range spans that belong to no
    list, or to a list created later, lose their bullets right after. A list spans only
    paragraphs that have no bullet yet: one that spanned an earlier list's
    item would also span that list's first item (their Markdown lists would
    interleave both ways, which the parser never produces).
    """
    offsets = _utf16_prefix(parsed.plain_text)
    requests = []

    def span(start, end):
        target = {"startIndex": start, "endIndex": max(start + 1, end)}
        if tab_id:
            target["tabId"] = tab_id
        return target

    def location(index):
        target = {"index": index}
        if tab_id:
            target["tabId"] = tab_id
        return target

    # Remove parser nesting tabs up front: each list inserts its own. Literal
    # content tabs stay shielded as spaces.
    for item in reversed(items):
        if item.list_depth:
            start = insert_index + offsets[item.start]
            requests.append({"deleteContentRange": {
                "range": span(start, start + item.list_depth),
            }})

    starts = [item.start for item in items]
    consumed = [0]
    for item in items:
        consumed.append(consumed[-1] + item.list_depth)

    def coordinate(point):
        # Nesting tabs removed before the point; only the item the point
        # starts in can have part of its tabs after it.
        before = bisect.bisect_left(starts, point)
        removed = consumed[before]
        if before:
            last = items[before - 1]
            removed -= last.list_depth - min(last.list_depth, point - last.start)
        return insert_index + offsets[point] - removed

    def end_of(item):
        # A stripped final empty item still owns the retained paragraph mark.
        return coordinate(item.end) + (item.start == item.end)

    zero = {"magnitude": 0, "unit": "PT"}
    for members in sorted(lists, key=lambda members: members[0].start):
        start, end = coordinate(members[0].start), end_of(members[-1])
        preset = members[0].style["bulletPreset"]
        # Continuing the list above joins it at level 0, so tabs stay
        # absolute; only the anchor is needed.
        temporary = 1 if (members[0].start == 0
                          and parsed.continues_list == preset) else 2
        requests += [
            {"insertText": {"location": location(start),
                            "text": "\n" * temporary}},
            {"deleteParagraphBullets": {"range": span(start, end + temporary)}},
            # Leftover indent from an earlier range would add levels.
            {"updateParagraphStyle": {
                "range": span(start, end + temporary),
                "paragraphStyle": {"indentStart": zero, "indentFirstLine": zero},
                "fields": "indentStart,indentFirstLine",
            }},
        ]
        tabs = 0
        for item in reversed(members):
            if item.list_depth:
                requests.append({"insertText": {
                    "location": location(coordinate(item.start) + temporary),
                    "text": "\t" * item.list_depth,
                }})
                tabs += item.list_depth
        requests += [
            {"createParagraphBullets": {
                "range": span(start + temporary - 1, end + temporary + tabs),
                "bulletPreset": preset,
            }},
            # Starting at the first temporary paragraph's first index, the
            # deletion leaves the first item its own style and bullet.
            {"deleteContentRange": {"range": span(start, start + temporary)}},
        ]
        for before, after in zip(members, members[1:]):
            if end_of(before) < coordinate(after.start):
                requests.append({"deleteParagraphBullets": {
                    "range": span(end_of(before), coordinate(after.start)),
                }})

    # Restore the requested style of non-list paragraphs a list spanned.
    for style in parsed.styles:
        if (style.type == "paragraph_style" and not any(
            item.start == style.start for item in items
        ) and any(members[0].start <= style.start < members[-1].end
                  for members in lists)):
            paragraph_style = {"indentStart": zero, "indentFirstLine": zero,
                               **style.style}
            requests.append({"updateParagraphStyle": {
                "range": span(coordinate(style.start), coordinate(style.end)),
                "paragraphStyle": paragraph_style,
                "fields": _paragraph_style_fields(paragraph_style),
            }})
    for item in items:
        start, end = coordinate(item.start), coordinate(item.end)
        depth = item.list_depth
        requests.append({"updateParagraphStyle": {
            "range": span(start, end),
            "paragraphStyle": {
                "indentStart": {"magnitude": 36 * (depth + 1), "unit": "PT"},
                "indentFirstLine": {"magnitude": 36 * (depth + 1) - 18, "unit": "PT"},
            },
            "fields": "indentStart,indentFirstLine",
        }})
        if item.literal_tabs:
            requests.extend([
                # Inserted after its placeholder spaces, the tab takes their
                # parsed style rather than the following text's.
                {"insertText": {"location": location(start + item.literal_tabs),
                                "text": "\t" * item.literal_tabs}},
                {"deleteContentRange": {
                    "range": span(start, start + item.literal_tabs),
                }},
            ])
    return requests


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
    "namedStyleType", "indentStart", "indentEnd", "indentFirstLine",
    "borderBottom",
})


def _paragraph_style_fields(style: dict) -> str:
    """Build the fields mask string for updateParagraphStyle.

    Whitelisted (rather than ``",".join(style.keys())``) so an unexpected key
    can't produce a malformed field mask — mirrors ``_text_style_fields``.
    """
    return ",".join(k for k in style if k in _PARAGRAPH_STYLE_FIELDS)
