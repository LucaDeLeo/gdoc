"""Google Docs API v1 wrapper functions with error translation."""

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gdoc.api import ACCOUNT_CACHE_SIZE, account_cache_key
from gdoc.api.comment_transport import execute_comment_request
from gdoc.util import (
    AuthError,
    GdocError,
    PreviewUnavailableError,
    fold_typography,
)


@lru_cache(maxsize=ACCOUNT_CACHE_SIZE)
def _docs_service(account: str | None, token_stamp):
    from gdoc.auth import get_credentials

    return build("docs", "v1", credentials=get_credentials(account))


def get_docs_service():
    """Build or reuse the Docs API v1 service for the current account."""
    return _docs_service(*account_cache_key())


def _translate_http_error(e: HttpError, doc_id: str) -> None:
    """Translate HttpError for Docs API operations."""
    status = int(e.resp.status)
    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    if status == 403:
        raise GdocError(f"Permission denied: {doc_id}")
    if status == 404:
        raise GdocError(f"Document not found: {doc_id}")
    raise GdocError(f"API error ({status}): {e.reason}")


def replace_all_text(
    doc_id: str,
    old_text: str,
    new_text: str,
    match_case: bool = False,
) -> int:
    """Replace text in a document using replaceAllText.

    Args:
        doc_id: The document ID.
        old_text: Text to find.
        new_text: Replacement text.
        match_case: If True, case-sensitive matching.

    Returns:
        Number of occurrences changed (from API response).
    """
    try:
        service = get_docs_service()
        body = {
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {
                            "text": old_text,
                            "matchCase": match_case,
                        },
                        "replaceText": new_text,
                    }
                }
            ]
        }
        from gdoc.api.comment_transport import execute_mutation_request

        result = execute_mutation_request(
            service.documents().batchUpdate(documentId=doc_id, body=body),
            uncertainty="Replacement outcome is uncertain; inspect the document "
                        "before retrying",
        )

        replies = result.get("replies", [])
        if replies:
            return replies[0].get("replaceAllText", {}).get(
                "occurrencesChanged", 0
            )
        return 0
    except HttpError as e:
        _translate_http_error(e, doc_id)


class CommentRevisionConflictError(GdocError):
    """The pinned comment write was rejected without applying it."""


# Google reports quota, rate-limit and API-disabled failures as 403 too.
# Only these reasons mean the caller lacks permission to batchUpdate.
_PERMISSION_REASONS = frozenset({
    "forbidden", "insufficientPermissions", "insufficientFilePermissions",
    "permissionDenied",
})
_PERMISSION_WORDS = ("permission", "forbidden", "not permitted", "not allowed")


def _is_permission_rejection(e: HttpError) -> bool:
    """True only for a 403 that says this caller may not batchUpdate."""
    details = e.error_details if isinstance(e.error_details, list) else []
    reasons = {d.get("reason", "") for d in details if isinstance(d, dict)}
    domains = {d.get("domain", "") for d in details if isinstance(d, dict)}
    if "usageLimits" in domains or reasons - _PERMISSION_REASONS:
        return False
    if reasons:
        return True
    # HttpError.reason can be the generic HTTP phrase "Forbidden".
    text = repr(e.content).lower()
    return any(word in text for word in _PERMISSION_WORDS)


def insert_comment(
    doc_id: str,
    content: str,
    start_index: int,
    end_index: int,
    tab_id: str | None = None,
    revision_id: str = "",
    segment_id: str | None = None,
) -> str:
    """Insert a comment anchored to a text range (Docs API insertComment).

    Unlike Drive's quotedFileContent (display metadata only), this creates a
    real anchored comment — highlighted in the Docs UI like one created by
    hand. The request is a Workspace Developer Preview feature: projects not
    enrolled get a 400 for the unknown request type, and comment-only access
    can't batchUpdate at all (403) but can still comment via the Drive API.
    Definite preview rejection raises PreviewUnavailableError. A revision
    mismatch raises CommentRevisionConflictError so callers can re-read the
    anchor. Uncertain write outcomes never permit fallback or blind replay.

    Args:
        doc_id: The document ID.
        content: Comment text, plain text (max 2048 UTF-8 code units).
        start_index: Range start (from find_text_in_document).
        end_index: Range end, exclusive.
        tab_id: Tab the range lives in (omitted → first tab).
        revision_id: If non-empty, sent as writeControl.requiredRevisionId
            so the anchor can't land on stale coordinates.
        segment_id: Header, footer or footnote containing the range.

    Returns:
        The new comment thread ID (same ID space as Drive API comments).
    """
    range_: dict = {"startIndex": start_index, "endIndex": end_index}
    if tab_id:
        range_["tabId"] = tab_id
    if segment_id:
        range_["segmentId"] = segment_id
    body: dict = {
        "requests": [
            {"insertComment": {"content": content, "range": range_}}
        ]
    }
    if revision_id:
        body["writeControl"] = {"requiredRevisionId": revision_id}
    try:
        service = get_docs_service()
        result = execute_comment_request(
            service.documents().batchUpdate(documentId=doc_id, body=body),
        )
    except HttpError as e:
        status = int(e.resp.status)
        detail = str(e)
        # Only a definite rejection permits another write. Never treat a
        # revision rejection as a missing preview feature.
        if status in (400, 409, 412) and "revision" in detail.lower():
            raise CommentRevisionConflictError(
                "Document changed before the comment could be anchored",
                exit_code=3,
            ) from e
        if status == 400 and (
            "No request set" in detail
            or (
                "insertComment" in detail
                and ("Unknown name" in detail or "Cannot find field" in detail)
            )
        ):
            raise PreviewUnavailableError(
                "insertComment preview is not enabled"
            ) from e
        if status == 403 and _is_permission_rejection(e):
            raise PreviewUnavailableError(
                "insertComment not permitted for this user"
            ) from e
        if status == 403:
            raise GdocError(
                f"Docs API refused the comment write (403: {e.reason}); "
                "no fallback comment was created"
            ) from e
        _translate_http_error(e, doc_id)

    # Comment saves can fail even when the batchUpdate itself returns 200.
    # The API always reports the state explicitly (ALL_SAVED on success,
    # NO_UPDATES_REQUESTED when no comment request ran), so a missing field
    # is as uncertain as a partial save.
    state = result.get("commentUpdateState") or "missing"
    if state != "ALL_SAVED":
        raise GdocError(
            f"Comment save outcome is uncertain ({state}); inspect comments "
            "before retrying. No fallback comment was created."
        )
    replies = result.get("replies", [])
    thread = (replies[0] if replies else {}).get(
        "insertComment", {},
    ).get("commentThread", {})
    comment_id = thread.get("commentId", "")
    if not comment_id:
        raise GdocError(
            "No comment thread ID in insertComment response; the comment may "
            "have been saved. Inspect comments before retrying. "
            "No fallback comment was created."
        )
    return comment_id


def set_page_mode(doc_id: str, pageless: bool) -> None:
    """Set a document's page mode (pageless vs paged).

    Writes ``documentStyle.documentFormat.documentMode`` via updateDocumentStyle.
    Blank docs inherit the account's page-mode default, while markdown-imported
    docs are always created paged; this makes the mode explicit either way.

    Args:
        doc_id: The document ID.
        pageless: If True, set PAGELESS; otherwise PAGES (paged).
    """
    mode = "PAGELESS" if pageless else "PAGES"
    try:
        service = get_docs_service()
        from gdoc.api.comment_transport import execute_mutation_request

        execute_mutation_request(service.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "updateDocumentStyle": {
                            "documentStyle": {
                                "documentFormat": {"documentMode": mode},
                            },
                            "fields": "documentFormat.documentMode",
                        }
                    }
                ]
            },
        ), uncertainty="Page mode change outcome is uncertain; check the "
                       "document's page setup")
    except HttpError as e:
        _translate_http_error(e, doc_id)


def _extract_paragraphs_text(content: list[dict]) -> str:
    """Extract concatenated text from body content paragraph elements."""
    parts = []
    for element in content:
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run is None:
                continue
            parts.append(text_run.get("content", ""))
    return "".join(parts)


def flatten_tabs(tabs: list[dict], _level: int = 0) -> list[dict]:
    """Recursively flatten a tabs tree into a flat list with nesting level."""
    result = []
    for tab in tabs:
        props = tab.get("tabProperties", {})
        doc_tab = tab.get("documentTab", {})
        result.append({
            "id": props.get("tabId", ""),
            "title": props.get("title", ""),
            "index": props.get("index", 0),
            "nesting_level": _level,
            "body": doc_tab.get("body", {}),
            # listId -> list definition; needed to tell ordered from bullet
            # lists when rendering a tab as markdown.
            "lists": doc_tab.get("lists", {}),
            **{key: doc_tab[key] for key in ("headers", "footers", "footnotes",
                                                "namedRanges", "inlineObjects",
                                                "positionedObjects", "namedStyles",
                                                "documentStyle",
                                                "suggestedDocumentStyleChanges",
                                                "suggestedNamedStylesChanges")
               if key in doc_tab},
        })
        for child in tab.get("childTabs", []):
            result.extend(flatten_tabs([child], _level=_level + 1))
    return result


def native_tab_fingerprint(tab: dict) -> str:
    """Fingerprint a flattened tab's native content, or "" when unprovable.

    Two snapshots of an unchanged tab fingerprint equally even at different
    document revisions, so edits to other tabs do not change it. Any native
    change in the tab does: text, paragraph and text styles, lists, named
    ranges, named and document styles, segments, and pending suggestions
    (including suggested tab style changes) that Markdown does not show.
    A tab with images returns "": each read issues a
    fresh temporary ``contentUri``, and a replaced image's bytes can change
    under the same object ID and size, so no stable field proves the image is
    unchanged. Callers treat "" as unknown and refuse to carry provenance.
    """
    import hashlib
    import json

    if tab.get("inlineObjects") or tab.get("positionedObjects"):
        return ""
    content = {key: value for key, value in tab.items()
               if key not in ("id", "title", "index", "nesting_level")}
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_document_tabs(doc_id: str) -> list[dict]:
    """Fetch document with all tab content and return flattened tab list.

    Uses the bounded Google-client retry policy documented in get_document.
    """
    try:
        service = get_docs_service()
        doc = (
            service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute(num_retries=2)
        )
        return flatten_tabs(doc.get("tabs", []))
    except HttpError as e:
        _translate_http_error(e, doc_id)


def count_document_tabs(doc_id: str, *, document: dict | None = None) -> int:
    """Return the total tab count (including nested child tabs).

    No `fields` mask: the Docs API rejects masks that recursively
    expand `childTabs` (issue #14).
    """
    doc = document if document is not None else get_document_with_tabs(doc_id)
    return len(flatten_tabs(doc.get("tabs", [])))


def _list_is_ordered(lists: dict, list_id: str, level: int) -> bool:
    """Whether a list level is ordered (numbered) rather than a bullet.

    A Docs list level carries a ``glyphType`` (DECIMAL/ALPHA/ROMAN/...) when
    ordered and a ``glyphSymbol`` (a bullet character) when not.
    """
    levels = (
        lists.get(list_id, {})
        .get("listProperties", {})
        .get("nestingLevels", [])
    )
    if 0 <= level < len(levels):
        glyph_type = levels[level].get("glyphType", "")
        return bool(glyph_type) and glyph_type != "GLYPH_TYPE_UNSPECIFIED"
    return False


def _style_run_markdown(content: str, style: dict) -> str:
    """Wrap one text run's content in markdown emphasis / link syntax.

    Surrounding whitespace and the trailing paragraph newline are kept outside
    the markers (``** bold **`` is not valid markdown), so only the visible
    core is wrapped. Emphasis nests as ``***bold italic***`` and
    ``~~struck~~``; a link becomes ``[text](url)``.
    """
    newline = ""
    text = content
    if text.endswith("\n"):
        text, newline = text[:-1], "\n"
    if text and not text.strip() and (style.get("link") or {}).get("url"):
        # A link label may be only whitespace; emphasis on whitespace alone
        # has no Markdown spelling.
        return f"[{text}]({_markdown_destination(style['link']['url'])}){newline}"
    monospace = (style.get("weightedFontFamily") or {}).get(
        "fontFamily") in _MONOSPACE_FONTS
    if not text or (not text.strip() and not monospace):
        # Only a monospace font gives whitespace a spelling (inline code);
        # emphasis or any other font on whitespace alone has none.
        return content
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = "".join(
        "\\" + char if char in "\\`*_[]~<!&" else char
        for char in text.strip()
    )

    if monospace:
        lead = trail = ""
        fence = "`" * (1 + max((len(m[0]) for m in re.finditer(r"`+", text)),
                               default=0))
        padded = text
        if text.startswith("`") or text.endswith("`") or (
            text.startswith(" ") and text.endswith(" ") and text.strip(" ")
        ):
            padded = " " + text + " "
        core = fence + padded + fence

    link = (style.get("link") or {}).get("url")
    if style.get("bold") and style.get("italic"):
        core = f"***{core}***"
    elif style.get("bold"):
        core = f"**{core}**"
    elif style.get("italic"):
        core = f"*{core}*"
    if style.get("strikethrough"):
        core = f"~~{core}~~"
    if link:
        core = f"[{core}]({_markdown_destination(link)})"
    return f"{lead}{core}{trail}{newline}"


_MONOSPACE_FONTS = ("Courier New", "Consolas", "monospace")


def _still_code(paragraph: dict) -> bool:
    """Whether a paragraph in a gdoc code range can still be code-block text.

    Headings, list items, images, links and emphasis cannot be code-block
    content, so a paragraph carrying any of them is exported natively. Fonts
    are not Markdown meaning and leave the block intact.
    """
    style = paragraph.get("paragraphStyle", {})
    if paragraph.get("bullet") or style.get("namedStyleType",
                                            "NORMAL_TEXT") != "NORMAL_TEXT":
        return False
    for element in paragraph.get("elements", []):
        run = element.get("textRun")
        if run is None:
            return False
        text_style = run.get("textStyle", {})
        if run.get("content", "").strip() and any(text_style.get(key) for key in
                                                  ("bold", "italic", "strikethrough",
                                                   "link")):
            return False
    return True


def _markdown_destination(url: str) -> str:
    """A link destination that reads back as exactly *url*.

    A destination with whitespace is bracketed, so text after a space can never
    read as a CommonMark link title.
    """
    if re.search(r"\s", url):
        return "<" + "".join("\\" + char if char in "\\<>()`" else char
                             for char in url) + ">"
    return "".join("\\" + char if char in "\\()`" else char for char in url)


_PREFIX_RANGE_RE = re.compile(
    r"gdoc:prefix:(?:v1:(\d+):(\d+)|v2:(\d+):(\d+):(\d+)|v3:((?:q|\d+)(?:\.(?:q|\d+))*))")


def _prefix_range_name(path: tuple) -> str:
    """Name of a container range for a container path.

    A path lists the containers outside a paragraph, outermost first: ``"q"``
    for a quote marker and an integer for a list item's content indent. v1
    (quotes, then an indent) and v2 (an item indent, quotes, then an indent)
    keep their names; any other nesting uses v3, the path itself.
    """
    from gdoc.mdparse import legacy_prefix

    legacy = legacy_prefix(path)
    if legacy is not None:
        quote, indent, outer = legacy
        if outer:
            return f"gdoc:prefix:v2:{outer}:{quote}:{indent}"
        return f"gdoc:prefix:v1:{quote}:{indent}"
    return "gdoc:prefix:v3:" + ".".join(str(token) for token in path)


def _parse_prefix_range_name(name: str) -> tuple | None:
    """The container path of a gdoc container range name."""
    found = _PREFIX_RANGE_RE.fullmatch(name)
    if not found:
        return None
    if found[1] is not None:
        quote, indent, outer = int(found[1]), int(found[2]), 0
    elif found[3] is not None:
        quote, indent, outer = int(found[4]), int(found[5]), int(found[3])
    else:
        return tuple(token if token == "q" else int(token)
                     for token in found[6].split("."))
    return ((outer,) if outer else ()) + ("q",) * quote + (
        (indent,) if indent else ())


def _without_trailing_indent(path: tuple) -> tuple:
    """A path without its trailing list-item indents (a blank line's markers)."""
    path = tuple(path)
    while path and path[-1] != "q":
        path = path[:-1]
    return path


def _prefix_still_applies(paragraph: dict, path: tuple) -> bool:
    """Whether a paragraph still has the indent its recorded container gave it.

    gdoc indents each container (quote marker or list item content) by 36pt;
    removing that indent in Docs removes the container.
    """
    indent = paragraph.get("paragraphStyle", {}).get("indentStart", {})
    magnitude = (indent.get("magnitude", 0)
                 if indent.get("unit", "PT") == "PT" else 0)
    return magnitude >= 36 * len(path) - 0.5


def _runs_markdown(elements: list[dict]) -> str:
    """Join a paragraph's text runs, styling each as markdown."""
    parts = []
    for pe in elements:
        text_run = pe.get("textRun")
        if "inlineObjectElement" in pe:
            object_id = pe["inlineObjectElement"].get("inlineObjectId", "")
            # Alt text stays on one line, and its brackets, backticks and
            # angle brackets are escaped so it cannot open other syntax. The
            # Docs API cannot set alt text, so folding its line breaks into
            # spaces loses nothing a write could keep.
            alt = " ".join(pe.get("_markdown_image_alt", "").split())
            alt = re.sub(r"([\\\[\]<>`])", r"\\\1", alt)
            image = f"![{alt}](gdoc-image:{object_id})"
            link = (pe["inlineObjectElement"].get("textStyle", {})
                    .get("link") or {}).get("url")
            if link:
                image = f"[{image}]({_markdown_destination(link)})"
            parts.append(image)
            continue
        if text_run is None:
            continue
        content = text_run.get("content", "")
        if content:
            url = (text_run.get("textStyle", {}).get("link") or {}).get("url")
            if url and parts and isinstance(parts[-1], _LinkGroup) \
                    and parts[-1].url == url:
                parts[-1].runs.append(text_run)
                continue
            if url:
                parts.append(_LinkGroup(url, [text_run]))
                continue
            _append_rendered(parts, _style_run_markdown(
                content, text_run.get("textStyle", {})))
    rendered_parts: list[str] = []
    for part in parts:
        _append_rendered(rendered_parts, part.render()
                         if isinstance(part, _LinkGroup) else part)
    return "".join(rendered_parts)


def _append_rendered(parts: list, rendered: str) -> None:
    if (parts and isinstance(parts[-1], str) and parts[-1][-1:] in ("*", "~", "`")
            and rendered[:1] in ("*", "~", "`")):
        # Standard Markdown's empty comment separates delimiters without
        # adding a visible character or merging differently styled runs.
        parts.append("<!-- -->")
    parts.append(rendered)


class _LinkGroup:
    """Consecutive runs sharing one link, written as one Markdown link."""

    def __init__(self, url, runs):
        self.url, self.runs = url, runs

    def render(self) -> str:
        if len(self.runs) == 1:
            return _style_run_markdown(self.runs[0]["content"],
                                       self.runs[0].get("textStyle", {}))
        inner: list[str] = []
        for run in self.runs:
            style = {k: v for k, v in run.get("textStyle", {}).items()
                     if k != "link"}
            _append_rendered(inner, _style_run_markdown(run["content"], style))
        text = "".join(inner)
        newline = "\n" if text.endswith("\n") else ""
        text = text.removesuffix("\n")
        destination = _markdown_destination(self.url)
        if not text.strip():
            # Whitespace-only runs: the whole text is the label, written once.
            return f"[{text}]({destination}){newline}"
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        return f"{lead}[{text.strip()}]({destination}){trail}{newline}"


def _indent_nesting_level(native_level: int, indent: dict, definitions: list) -> int:
    """Return the nesting a list paragraph's physical indent shows.

    gdoc indents a nested list created as its own native list by 36pt per
    level, so the indent recovers nesting its metadata resets. The level is the
    one whose own list-definition indent (36pt per level by default) is closest;
    ties keep the shallower level, so a custom first-level indent is no nesting.
    """
    if indent.get("unit", "PT") != "PT" or "magnitude" not in indent:
        return native_level

    def expected(level):
        defined = (definitions[level].get("indentStart", {})
                   if level < len(definitions) else {})
        if defined.get("unit", "PT") == "PT" and "magnitude" in defined:
            return defined["magnitude"]
        return 36 * (level + 1)

    magnitude = indent["magnitude"]
    closest = min(range(9), key=lambda level: (abs(expected(level) - magnitude), level))
    return max(native_level, closest)


def _paragraph_markdown(
    paragraph: dict, lists: dict, ordered_counters: dict
) -> str:
    """Render one paragraph as markdown: headings, list items, inline styles.

    ``ordered_counters`` (nesting level -> running ordinal) is carried across
    paragraphs by the caller so numbered lists count 1, 2, 3.
    """
    elements = paragraph.get("elements", [])
    if any("horizontalRule" in e for e in elements):
        fragments = []
        chunk = []
        # One native item stays one Markdown item: only its first text keeps
        # the list marker, so the rule cannot add an item or advance numbering.
        marked = [dict(paragraph)]

        def flush():
            if chunk and _runs_markdown(chunk).strip():
                part = dict(marked[0], elements=chunk)
                fragments.append(_paragraph_markdown(
                    part, lists, ordered_counters,
                ).rstrip("\n") + "\n")
                marked[0] = {k: v for k, v in marked[0].items() if k != "bullet"}

        for element in elements:
            if "horizontalRule" in element:
                flush()
                fragments.append("---\n")
                chunk = []
            else:
                chunk.append(element)
        flush()
        return "".join(fragments)
    text = _runs_markdown(paragraph.get("elements", []))
    newline = ""
    if text.endswith("\n"):
        text, newline = text[:-1], "\n"

    bullet = paragraph.get("bullet")
    if bullet is not None:
        native_level = bullet.get("nestingLevel", 0)
        level = native_level
        definitions = lists.get(bullet.get("listId", ""), {}).get(
            "listProperties", {},
        ).get("nestingLevels", [])
        inherited = definitions[native_level] if native_level < len(definitions) else {}
        indent_start = paragraph.get("paragraphStyle", {}).get(
            "indentStart", inherited.get("indentStart", {}),
        )
        level = _indent_nesting_level(native_level, indent_start, definitions)
        list_id = bullet.get("listId", "")
        # List IDs carry continuity even across intervening paragraphs/lists.
        for key in list(ordered_counters):
            if key[0] == list_id and key[1] > native_level:
                del ordered_counters[key]
        indent = "  " * level
        if _list_is_ordered(lists, list_id, native_level):
            definition = lists[list_id]["listProperties"]["nestingLevels"][native_level]
            key = (list_id, native_level)
            ordinal = ordered_counters.get(
                key, definition.get("startNumber", 1) - 1,
            ) + 1
            ordered_counters[key] = ordinal
            marker = f"{ordinal}."
        else:
            marker = "-"
        item = text
        named_style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
        heading = _HEADING_LEVELS.get(named_style)
        if heading:
            item = "#" * heading + " " + item
        elif named_style in ("TITLE", "SUBTITLE"):
            item = f"<!-- gdoc:{named_style} --> {item}"
        else:
            item = re.sub(r"^([#>])", r"\\\1", item)
            from gdoc.mdparse import _HR_RE
            if _HR_RE.match(f"{marker} {item}"):
                # An item of dashes would read back as a thematic break. The
                # escape goes before the first dash: an escaped space is text.
                lead = item[:len(item) - len(item.lstrip())]
                item = lead + "\\" + item[len(lead):]
        return f"{indent}{marker} {item}{newline}"

    # Preserve counters: a later paragraph can resume the same native list.

    paragraph_style = paragraph.get("paragraphStyle", {})
    # Docs returns a zero-width border on some paragraphs; only a visible one
    # is a rule (as in lossy.py).
    if not text and (paragraph_style.get("borderBottom") or {}).get(
            "width", {}).get("magnitude", 0) > 0:
        return "---" + newline
    # A single native indent reads as one quote level around the same
    # heading, title or escaped prose as an unindented paragraph.
    quote = "> " if all(
        paragraph_style.get(key) == {"magnitude": 36, "unit": "PT"}
        for key in ("indentStart", "indentFirstLine")) else ""
    named_style = paragraph_style.get("namedStyleType", "")
    level = _HEADING_LEVELS.get(named_style)
    if named_style in ("TITLE", "SUBTITLE"):
        return f"{quote}<!-- gdoc:{named_style} --> {text}{newline}"
    if level:
        # The parser consumes exactly the one syntactic separator space.
        return quote + "#" * level + " " + text + newline
    # Inline escaping above handles stars, underscores, and code fences. Escape
    # remaining literal block openers only after adding genuine block syntax.
    if re.fullmatch(r"=== Tab: .+ ===", text) and not quote:
        text = "\\" + text
    text = re.sub(r"^([ \t]*)([-+#>|])", r"\1\\\2", text)
    text = re.sub(r"^([ \t]*\d+)\.(?=\s|$)", r"\1\\.", text)
    if text[:1] in (" ", "\t"):
        # Raw indentation marks list item content; a numeric entity keeps
        # literal leading whitespace as text.
        text = f"&#{ord(text[0])};" + text[1:]
    return quote + text + newline


# One cell of a pipe-table header separator (``---``, ``:---:``).
_TABLE_SEP_CELL_RE = re.compile(r"[\s:]*-{3,}[\s:]*")


def _table_markdown(table: dict) -> str | None:
    """Export rectangular, unmerged tables; retain the text fallback otherwise.

    Numeric entities preserve boundary whitespace separately from delimiter
    padding; column alignment comes from the first row's paragraphs.
    """
    rows = [row.get("tableCells", []) for row in table.get("tableRows", [])]
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        return None
    for row in rows:
        for cell in row:
            style = cell.get("tableCellStyle", {})
            if (style.get("rowSpan", 1) != 1 or style.get("columnSpan", 1) != 1
                    or any("table" in element for element in cell.get("content", []))):
                return None
    grid = []
    for row in rows:
        cells = []
        for cell in row:
            text = "".join(
                _runs_markdown(element["paragraph"].get("elements", []))
                for element in cell.get("content", []) if "paragraph" in element
            ).removesuffix("\n")
            if _TABLE_SEP_CELL_RE.fullmatch(text):
                # A data row of dashes would read as the separator of a new
                # table; the parser drops the escape again.
                text = text.replace("-", "\\-", 1)
            rendered = text.replace("|", "\\|").replace("\n", "<br>")
            # Numeric entities keep meaningful boundary whitespace distinct
            # from the optional padding around pipe delimiters.
            lead = len(rendered) - len(rendered.lstrip(" \t"))
            tail = len(rendered.rstrip(" \t"))
            rendered = (
                "".join(f"&#{ord(c)};" for c in rendered[:lead])
                + rendered[lead:max(lead, tail)]
                + "".join(f"&#{ord(c)};" for c in rendered[max(lead, tail):])
            )
            cells.append(rendered)
        grid.append(cells)
    lines = ["| " + " | ".join(cells) + " |\n" for cells in grid]
    separators = []
    for cell in rows[0]:
        paragraphs = [e["paragraph"] for e in cell.get("content", [])
                      if "paragraph" in e]
        alignment = (paragraphs[0].get("paragraphStyle", {}).get("alignment")
                     if paragraphs else None)
        separators.append({"START": ":---", "CENTER": ":---:", "END": "---:"}
                          .get(alignment, "---"))
    lines.insert(1, "| " + " | ".join(separators) + " |\n")
    return "".join(lines)


def pending_suggestion_ids(value) -> set[str]:
    """IDs of pending suggestions anywhere in a tab or content subtree."""
    found: set[str] = set()
    if isinstance(value, list):
        for child in value:
            found |= pending_suggestion_ids(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in ("suggestedInsertionIds", "suggestedDeletionIds"):
                found.update(child or [])
            elif key.startswith("suggested") and isinstance(child, dict):
                found.update(child)
            else:
                found |= pending_suggestion_ids(child)
    return found


def _without_suggestions(content: list[dict], gaps: list | None = None,
                         owned=None) -> list[dict]:
    """Content as it reads before its pending suggestions.

    Reads use the API's inline suggestion view, so suggested insertions
    appear as text beside the wording they would delete. Markdown shows the
    current text: suggested insertions are left out and suggested deletions
    stay. A paragraph break that is itself a suggested insertion joins its
    text to the following paragraph. When the two paragraphs differ in
    style, list membership or gdoc container, the inline view does not say
    reliably what the joined paragraph looks like, so the join is recorded
    in ``gaps`` and the read is not complete.
    """
    result: list[dict] = []
    carried: list[dict] = []
    carried_from: list[dict] = []
    for element in content:
        if element.get("suggestedInsertionIds") or element.get(
                "table", {}).get("suggestedInsertionIds"):
            continue
        if "table" in element:
            # Suggested rows, and suggested cells such as an inserted column,
            # are left out like any other suggested insertion.
            table = dict(element["table"])
            table["tableRows"] = [
                {**row, "tableCells": [
                    {**cell, "content": _without_suggestions(
                        cell.get("content", []), gaps, owned)}
                    for cell in row.get("tableCells", [])
                    if not cell.get("suggestedInsertionIds")]}
                for row in table.get("tableRows", [])
                if not row.get("suggestedInsertionIds")
            ]
            result.append({**element, "table": table})
            continue
        if "paragraph" not in element:
            result.append(element)
            continue
        paragraph = element["paragraph"]
        kept = [e for e in paragraph.get("elements", [])
                if not any(e.get(kind, {}).get("suggestedInsertionIds")
                           for kind in e if isinstance(e.get(kind), dict))]
        mark = next((e for e in reversed(paragraph.get("elements", []))
                     if "textRun" in e), None)
        if mark is not None and mark["textRun"].get("content", "").endswith("\n") \
                and mark["textRun"].get("suggestedInsertionIds"):
            text = [e for e in kept if e is not mark]
            if text:
                carried_from.append(element)
            carried.extend(text)
            continue
        if gaps is not None:
            gaps.extend(
                f"a suggested paragraph break at index "
                f"{joined.get('startIndex', '?')} joins paragraphs whose "
                "style, list or container differ"
                for joined in carried_from
                if _joined_form_differs(joined, element, owned))
        result.append({**element, "paragraph": {
            **paragraph, "elements": carried + kept}})
        carried, carried_from = [], []
    if carried:
        result.append({"paragraph": {"elements": carried + [
            {"textRun": {"content": "\n"}}]}})
    return result


def _joined_form_differs(first: dict, second: dict, owned) -> bool:
    def form(element):
        paragraph = element["paragraph"]
        style = {k: v for k, v in paragraph.get("paragraphStyle", {}).items()
                 if k != "headingId"}
        bullet = paragraph.get("bullet") or {}
        container = owned(element.get("startIndex", -1)) if owned else None
        return (style, bullet.get("listId"), bullet.get("nestingLevel", 0),
                container)
    return form(first) != form(second)


def _owned_containers(tab: dict):
    """Which gdoc code and container ranges hold a given index."""
    spans = [(r.get("startIndex", 0), r.get("endIndex", 0), name)
             for group in tab.get("namedRanges", {}).values()
             for named in group.get("namedRanges", [])
             for name in [named.get("name", group.get("name", ""))]
             if name == "gdoc:code:v1" or _parse_prefix_range_name(name)
             for r in named.get("ranges", [])]
    return lambda index: frozenset(
        (start, name) for start, end, name in spans if start <= index < end)


def suggestion_preview_gaps(tab: dict) -> list[str]:
    """Pending suggestions the Markdown read cannot show faithfully."""
    content = tab.get("body", {}).get("content", [])
    if not pending_suggestion_ids(content):
        return []
    gaps: list[str] = []
    _without_suggestions(content, gaps, _owned_containers(tab))
    return gaps


def get_tab_text(tab: dict, markdown: bool = False) -> str:
    """Extract text from a tab's body content.

    Handles paragraphs and tables (tab-joined cells per row in plain text). When
    *markdown* is True, the per-tab export (which builds markdown by hand,
    unlike the whole-doc Drive export) renders headings (``#``), bullet and
    numbered lists (nested, 2 spaces per level), and inline emphasis
    (``**bold**``, ``*italic*``, ``~~strike~~``) and ``[links](url)``, so a
    tab's supported text and styles can be reconstructed with ``write --tab``.
    Rectangular, unmerged tables use pipe rows, with the first row as a header
    and cell newlines as ``<br>``; irregular and nested tables keep the text
    fallback. Column alignment and boundary whitespace are represented; table
    borders and widths are richer formatting. Named ``gdoc:code:v1`` ranges
    retain fenced code identity. Images use document-scoped object references.

    Markdown export escapes literal syntax: ``1. Hello`` becomes
    ``1\\. Hello``, and ``_``, ``[``, and ``<`` gain backslashes. It uses
    ``<!-- -->`` between touching emphasis runs and ``<!-- gdoc:TITLE -->`` /
    ``<!-- gdoc:SUBTITLE -->`` prefixes for those named paragraph styles.
    This source noise is the accepted trade-off: escapes distinguish prose
    from structure, comments separate styles without adding visible characters,
    and ordinary Markdown has no TITLE/SUBTITLE equivalent. Retain these markers
    when reconstructing a tab; arbitrary Markdown tools and Drive import need
    not preserve them. With *markdown* False (``cat --plain --tab``), text is
    returned verbatim for searching, copying prose, and matching ``gdoc edit``.
    """
    body = tab.get("body", {})
    content = body.get("content", [])
    if markdown and pending_suggestion_ids(content):
        content = _without_suggestions(content)
    if markdown and tab.get("inlineObjects"):
        from copy import deepcopy

        content = deepcopy(content)

        def image_alts(value):
            if isinstance(value, list):
                for child in value:
                    image_alts(child)
            elif isinstance(value, dict):
                if "inlineObjectElement" in value:
                    object_id = value["inlineObjectElement"].get("inlineObjectId")
                    embedded = tab["inlineObjects"].get(object_id, {}).get(
                        "inlineObjectProperties", {},
                    ).get("embeddedObject", {})
                    value["_markdown_image_alt"] = (
                        embedded.get("description") or embedded.get("title", "")
                    )
                for child in list(value.values()):
                    image_alts(child)

        image_alts(content)
    lists = tab.get("lists", {}) if markdown else {}
    parts = []
    ordered_counters: dict = {}
    # Named ranges distinguish fenced code from inline monospace formatting.
    code_ranges = []
    prefix_ranges = []
    for group in tab.get("namedRanges", {}).values():
        for named in group.get("namedRanges", []):
            name = named.get("name", group.get("name", ""))
            if name == "gdoc:code:v1":
                code_ranges.extend(named.get("ranges", []))
            prefix = _parse_prefix_range_name(name)
            if prefix:
                prefix_ranges.extend((r, prefix) for r in named.get("ranges", []))
    code_parts = []
    active_code = None
    active_prefix = ()

    def with_prefix(text, path, code=False):
        from gdoc.mdparse import path_lead

        lead = path_lead(path)
        # A blank line outside code carries only the containers that need a
        # marker: trailing list-item indentation alone would be invisible
        # trailing whitespace, and the parser treats a blank line as inside
        # the item either way.
        blank = path_lead(_without_trailing_indent(path))
        # Only "\n" ends a Markdown line; a soft break (\x0b) stays in its line.
        return "".join((blank if line == "\n" and not code else lead) + line
                       for line in re.findall(r"[^\n]*\n|[^\n]+", text))

    def flush_code():
        if not code_parts:
            return
        literal = "".join(code_parts)
        fence = "`" * max(3, 1 + max(
            (len(m[0]) for m in re.finditer(r"`+", literal)), default=0,
        ))
        parts.append(with_prefix(fence + "\n" + literal + fence + "\n",
                                 active_prefix, code=True))
        code_parts.clear()

    def prefix_at(index):
        return next((path for r, path in prefix_ranges
                     if r.get("startIndex", 0) <= index < r.get("endIndex", 0)),
                    ())

    def table_prefix(table):
        # Only a range starting inside the first cell marks a contained table,
        # so a neighbouring paragraph's container cannot spread onto it.
        cell = next(iter(next(iter(table.get("tableRows", [])), {}).get(
            "tableCells", [])), {})
        low, high = cell.get("startIndex", -1), cell.get("endIndex", -1)
        return next((path for r, path in prefix_ranges
                     if low <= r.get("startIndex", -1) < high), ())

    def blank_paragraph(element):
        # Scaffolding is empty and unstyled (I5): a rule or an empty heading
        # between tables is content, not the paragraph Docs requires there.
        paragraph = element.get("paragraph")
        style = (paragraph or {}).get("paragraphStyle", {})
        return paragraph is not None and not paragraph.get("bullet") and (
            style.get("namedStyleType", "NORMAL_TEXT") == "NORMAL_TEXT"
            and not style.get("borderBottom", {}).get("width", {}).get("magnitude")
        ) and all(
            not run.get("textRun", {}).get("content", "\n").strip("\n")
            and "textRun" in run for run in paragraph.get("elements", []))

    def next_table_prefix(position):
        """The container of a table that follows only blank paragraphs."""
        for later in content[position + 1:]:
            if "table" in later:
                return table_prefix(later["table"])
            if not blank_paragraph(later):
                return None
        return None

    table_run = None  # Container of the last pipe table, until non-blank content.
    for position, element in enumerate(content):
        if not markdown:
            prefix = ()
        elif "table" in element:
            prefix = table_prefix(element["table"])
        else:
            prefix = prefix_at(element.get("startIndex", -1))
        marker = next((index for index, r in enumerate(code_ranges)
                       if r.get("startIndex", 0) <= element.get("startIndex", -1)
                       < r.get("endIndex", 0)), None) if markdown else None
        # gdoc's ranges record what it wrote. A paragraph someone has since
        # restyled in Docs is read from its native content instead.
        if "paragraph" in element:
            if prefix and not _prefix_still_applies(element["paragraph"], prefix):
                prefix = ()
            if marker is not None and not _still_code(element["paragraph"]):
                marker = None
            # Docs keeps a paragraph between adjacent tables. Blank paragraphs
            # there belong to the tables' container, so the separator logic
            # below counts them in that container. A blank recorded in a
            # container of its own, not one enclosing the tables, is content.
            if (markdown and marker is None and table_run is not None
                    and prefix != table_run
                    and tuple(table_run[:len(prefix)]) == tuple(prefix)
                    and blank_paragraph(element)
                    and next_table_prefix(position) == table_run):
                prefix = table_run
        if (marker != active_code or prefix != active_prefix
                or "paragraph" not in element):
            flush_code()
        active_code = marker
        active_prefix = prefix
        if marker is not None and "paragraph" in element:
            code_parts.append(_extract_paragraphs_text([element]))
            table_run = None
            continue
        if "paragraph" in element:
            if not markdown:
                parts.append(_extract_paragraphs_text([element]))
                continue
            paragraph = element["paragraph"]
            if prefix:
                # Prefix ranges distinguish quote/list containers from incidental
                # native indentation and avoid inferring a second quote marker.
                paragraph_style = dict(paragraph.get("paragraphStyle", {}))
                for key in ("indentStart", "indentFirstLine"):
                    if "bullet" in paragraph and key in paragraph_style:
                        paragraph_style[key] = {
                            **paragraph_style[key],
                            "magnitude": paragraph_style[key].get("magnitude", 0)
                            - 36 * len(prefix),
                        }
                    else:
                        paragraph_style.pop(key, None)
                paragraph = dict(paragraph, paragraphStyle=paragraph_style)
            rendered = _paragraph_markdown(paragraph, lists, ordered_counters)
            # A blank paragraph outside the tables' container already separates
            # them; another separator line would read back as a new paragraph.
            if rendered.strip() or prefix != table_run:
                table_run = None
            parts.append(with_prefix(rendered, prefix))
        elif "table" in element:
            table = element["table"]
            rendered = _table_markdown(table) if markdown else None
            if rendered is not None:
                if table_run == prefix:
                    # A table following a table (across any blank paragraphs)
                    # gets one separating blank line, which the parser drops.
                    parts.append(with_prefix("\n", prefix))
                parts.append(with_prefix(rendered, prefix))
                table_run = prefix
                continue
            table_run = None
            for row in table.get("tableRows", []):
                cells = []
                for cell in row.get("tableCells", []):
                    cell_content = cell.get("content", [])
                    cell_text = _extract_paragraphs_text(cell_content).strip()
                    cells.append(cell_text)
                parts.append("\t".join(cells) + "\n")
    flush_code()
    return "".join(parts)


def _match_tab(tabs: list[dict], tab_name: str) -> dict | None:
    """Prefer immutable IDs, then unique exact or case-insensitive titles."""
    for tab in tabs:
        if str(tab["id"]) == tab_name:
            return tab
    matches = [tab for tab in tabs if tab["title"] == tab_name]
    if not matches:
        matches = [tab for tab in tabs if tab["title"].lower() == tab_name.lower()]
    if len(matches) > 1:
        candidates = ", ".join(f'{tab["title"]!r} (id: {tab["id"]})' for tab in matches)
        raise GdocError(
            f"ambiguous tab {tab_name!r}: {candidates}; use a tab ID",
            exit_code=3,
        )
    return matches[0] if matches else None


def resolve_tab(tabs: list[dict], tab_name: str) -> dict:
    """Resolve a flattened tab by ID, exact title, or unique folded title.

    Raise GdocError with exit code 3 for missing or ambiguous titles.
    """
    match = _match_tab(tabs, tab_name)
    if match is None:
        raise GdocError(f"tab not found: {tab_name}", exit_code=3)
    return match


def get_document(doc_id: str) -> dict:
    """Fetch the full document structure via documents().get().

    Returns the document JSON including body.content and revisionId.
    Allows two additional Google-client retries for retryable transport errors,
    HTTP 5xx/429, and rate-limit 403 responses. This bounds client retries, not
    wire sends: httplib2 may retry internally. Mutations add no client retries.
    """
    try:
        service = get_docs_service()
        return service.documents().get(documentId=doc_id).execute(num_retries=2)
    except HttpError as e:
        _translate_http_error(e, doc_id)


def _utf16_len(ch: str) -> int:
    """Width of one code point in UTF-16 code units (Docs API indices)."""
    return 2 if ord(ch) > 0xFFFF else 1


def _collect_segments(
    content: list[dict], *, allow_native_gaps: bool = False,
) -> list[list[tuple[int, str]]]:
    """Group (doc_index, char) pairs into independently-searchable segments.

    Paragraph text at one level forms a segment, split at non-text inline
    elements unless allow_native_gaps is set for non-destructive anchors.
    Structural blocks and discontinuous native indices also split segments.
    Each table cell remains a separate segment, recursively, because Docs
    rejects edits that cross cell boundaries.

    Doc indices are UTF-16 code units, so a non-BMP character (emoji)
    advances the index by 2 even though it's one Python char.
    """
    segments: list[list[tuple[int, str]]] = []
    root: list[tuple[int, str]] = []
    for element in content:
        paragraph = element.get("paragraph")
        if paragraph is not None:
            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if text_run is None:
                    # An edit spanning this gap would delete the native element.
                    if root and not allow_native_gaps:
                        segments.append(root)
                        root = []
                    continue
                run = text_run.get("content", "")
                start_idx = pe.get("startIndex", 0)
                if (root and not allow_native_gaps
                        and start_idx != root[-1][0] + _utf16_len(root[-1][1])):
                    segments.append(root)
                    root = []
                offset = 0
                for ch in run:
                    root.append((start_idx + offset, ch))
                    offset += _utf16_len(ch)
            continue
        # Structural blocks interrupt surrounding paragraphs, even for anchors.
        if root:
            segments.append(root)
            root = []
        table = element.get("table")
        if table is not None:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    segments.extend(_collect_segments(
                        cell.get("content", []), allow_native_gaps=allow_native_gaps,
                    ))
    if root:
        segments.append(root)
    return segments


def _search_containers(document: dict):
    """Yield every supplied tab's independent index spaces, body first.

    Accept a legacy document, a raw tabs document, or one flattened tab.
    Segment IDs are sorted so API map iteration cannot change write order.
    """
    if "tabs" in document:
        for tab in flatten_tabs(document["tabs"]):
            yield from _search_containers(tab)
        return
    tab_id = document.get("id")
    coordinates = {"tabId": tab_id} if tab_id else {}
    yield document.get("body", {}), {"container": "body", **coordinates}
    for collection, kind in (("headers", "header"), ("footers", "footer"),
                             ("footnotes", "footnote")):
        for segment_id, content in sorted(document.get(collection, {}).items()):
            yield content, {"container": kind, "segmentId": segment_id,
                            **coordinates}


def find_text_in_document(
    document: dict | None,
    text: str,
    match_case: bool = False,
    body: dict | None = None,
    normalize: bool = False,
    *,
    allow_native_gaps: bool = False,
) -> list[dict]:
    """Find text in the supplied tabs' bodies and non-body containers.

    Passing ``body`` explicitly limits the search to that content and keeps
    the legacy two-key range shape. A full document or flattened tab also
    searches its headers, footers, and footnotes, retaining their addresses.

    Searches body.content per segment (top-level paragraphs, and each table
    cell on its own) so a match never crosses a table-cell boundary.

    Args:
        document: The full document dict (used if body is None).
        text: Text to search for.
        match_case: If True, case-sensitive matching.
        body: Optional body dict to search in (e.g. from a specific tab).
        normalize: If True, fold smart quotes/dashes to ASCII on both sides
            before matching. The fold is length-preserving, so returned
            indices stay correct.
        allow_native_gaps: For non-destructive anchors only, allow matches
            across inline native elements omitted from plain-text extraction.
            Such ranges include the native elements: never use this option
            for replacement or deletion. Table-cell boundaries still apply.

    Returns ranges with startIndex/endIndex, plus container, segmentId,
    and tabId where available. Containers are ordered body, headers,
    footers, footnotes; matches ascend by startIndex within each container.
    """
    if body is None:
        if document is None:
            return []
        matches = []
        for content, coordinates in _search_containers(document):
            for match in find_text_in_document(
                None, text, match_case=match_case, body=content,
                normalize=normalize, allow_native_gaps=allow_native_gaps,
            ):
                # Preserve the legacy body shape when no tab was supplied.
                if coordinates != {"container": "body"}:
                    match.update(coordinates)
                matches.append(match)
        return matches

    matches = []
    for chars in _collect_segments(
        body.get("content", []), allow_native_gaps=allow_native_gaps,
    ):
        concat = "".join(ch for _, ch in chars)

        search_text = text
        search_in = concat
        if normalize:
            search_text = fold_typography(search_text)
            search_in = fold_typography(search_in)
        # Map each transformed code point back to its original character.
        # Lowercasing can expand a character (İ -> i + combining dot).
        source_indices = [
            i for i, ch in enumerate(search_in)
            for _ in (ch if match_case else ch.lower())
        ]
        if not match_case:
            search_text = search_text.lower()
            # Lower the whole string to preserve contextual forms such as sigma.
            search_in = search_in.lower()
        if not search_text:
            continue

        start = 0
        while True:
            pos = search_in.find(search_text, start)
            if pos == -1:
                break
            end_pos = pos + len(search_text)
            start = pos + 1
            first, last = source_indices[pos], source_indices[end_pos - 1]
            # A match inside an expansion cannot select part of a native character.
            if ((pos > 0 and source_indices[pos - 1] == first)
                    or (end_pos < len(source_indices)
                        and source_indices[end_pos] == last)):
                continue
            matches.append({
                "startIndex": chars[first][0],
                "endIndex": chars[last][0] + _utf16_len(chars[last][1]),
            })

    matches.sort(key=lambda m: m["startIndex"])
    return matches


def diagnose_no_match(
    document: dict | None,
    text: str,
    match_case: bool = False,
    body: dict | None = None,
    already_normalized: bool = False,
) -> str | None:
    """Explain why an exact search for `text` found nothing.

    Returns a human-readable reason (to append to "no match found") or None
    if no near-match can explain the miss. Runs entirely on the already-
    fetched document \u2014 no extra API calls.
    """
    # Near-match that differs only in quote/dash style.
    if not already_normalized and find_text_in_document(
        document, text, match_case=match_case, body=body, normalize=True,
    ):
        return (
            "but a near-match exists with different quote/dash style "
            "(e.g. \u2019 vs '). Re-run with --normalize to match it"
        )

    # Near-match that differs only in whitespace (line breaks, runs of
    # spaces, non-breaking spaces). Folded so quote style doesn't mask it.
    if body is None and document is not None:
        body = document.get("body", {})
    segments = _collect_segments((body or {}).get("content", []))
    concat = fold_typography(
        "\n".join("".join(c for _, c in seg) for seg in segments)
    )
    needle = fold_typography(text)

    def collapse(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    hay_c, needle_c = collapse(concat), collapse(needle)
    if not match_case:
        hay_c, needle_c = hay_c.lower(), needle_c.lower()
    if needle_c and needle_c in hay_c:
        return (
            "but the text appears with different whitespace (line breaks "
            "or non-breaking spaces). Adjust the anchor to match exactly"
        )

    return None


_COORD_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")


def _parse_coord(spec: str) -> tuple[int, int] | None:
    """Parse 'R,C' into (row, col) ints, or None if not coordinate form."""
    m = _COORD_RE.match(spec)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _cell_text_range(cell: dict) -> dict | None:
    """Editable text range of a table cell as {startIndex, endIndex}.

    Spans the cell's text but excludes the final structural paragraph mark
    (the Docs API forbids deleting a cell's last newline). An empty cell
    yields a zero-width range → pure insert. Returns None if no paragraph
    element with an index can be located. Refuse native objects or structural
    gaps: a whole-cell text replacement must not silently delete them.
    """
    first_start: int | None = None
    last_start: int | None = None
    last_content = ""
    for element in cell.get("content", []):
        para = element.get("paragraph")
        if (para is None or para.get("positionedObjectIds")
                or any("textRun" not in pe for pe in para.get("elements", []))):
            raise GdocError(
                "cell contains non-text content; replace specific text instead",
                exit_code=3,
            )
        for pe in para.get("elements", []):
            start = pe.get("startIndex")
            if start is None:
                continue
            previous_width = sum(_utf16_len(ch) for ch in last_content)
            if last_start is not None and start != last_start + previous_width:
                raise GdocError(
                    "cell contains a structural gap; replace specific text instead",
                    exit_code=3,
                )
            if first_start is None:
                first_start = start
            tr = pe.get("textRun")
            last_start = start
            last_content = tr.get("content", "") if tr else ""
    if first_start is None:
        content = cell.get("content", [])
        fs = content[0].get("startIndex") if content else None
        return {"startIndex": fs, "endIndex": fs} if fs is not None else None
    # Doc indexes are UTF-16 code units: an emoji in the cell counts as 2.
    end = last_start + sum(_utf16_len(ch) for ch in last_content)
    if last_content.endswith("\n"):
        end -= 1  # keep the cell's final paragraph mark
    if end < first_start:
        end = first_start
    return {"startIndex": first_start, "endIndex": end}


def resolve_cell_range(
    body: dict,
    cell: str,
    col: int | None = None,
    table_index: int | None = None,
    normalize: bool = False,
) -> dict | None:
    """Resolve a cell address to an editable {startIndex, endIndex} range.

    Two forms, auto-detected:
    - coordinate ('R,C'): row R, column C of a table (0-based). Uses table
      `table_index`, or the first table when `table_index` is None.
    - label (anything else): match the first column of a unique row whose
      text equals `cell`; the target is column `col` if given, else column 1.
      Searches every table by default, or only table `table_index` when given.
      Duplicate labels or labels repeated in value columns raise GdocError
      with exit code 3 before resolving a range.

    `normalize` folds smart quotes/dashes when comparing labels. Returns
    None if nothing resolves.
    """
    tables = [el["table"] for el in body.get("content", []) if "table" in el]

    coord = _parse_coord(cell)
    if coord is not None:
        r, c = coord
        ti = 0 if table_index is None else table_index
        if not 0 <= ti < len(tables):
            return None
        rows = tables[ti].get("tableRows", [])
        if not 0 <= r < len(rows):
            return None
        cells = rows[r].get("tableCells", [])
        if not 0 <= c < len(cells):
            return None
        return _cell_text_range(cells[c])

    # Only the first column identifies a row; values must not select neighbours.
    if table_index is None:
        search_tables = enumerate(tables)
    elif 0 <= table_index < len(tables):
        search_tables = [(table_index, tables[table_index])]
    else:
        return None

    target = fold_typography(cell) if normalize else cell
    target = target.strip()
    matches = []
    value_matches = []
    for ti, table in search_tables:
        for ri, row in enumerate(table.get("tableRows", [])):
            cells = row.get("tableCells", [])
            for ci, candidate in enumerate(cells):
                label = _extract_paragraphs_text(candidate.get("content", []))
                label = (fold_typography(label) if normalize else label).strip()
                if label == target:
                    if ci == 0:
                        matches.append((ti, ri, cells))
                    else:
                        value_matches.append(f"table {ti} row {ri} column {ci}")
    if len(matches) > 1 or (matches and value_matches):
        candidates = ", ".join(
            [f"table {ti} row {ri} column 0" for ti, ri, _ in matches]
            + value_matches
        )
        raise GdocError(
            f"ambiguous cell label {cell!r}: {candidates}; "
            "use --table and --cell ROW,COL",
            exit_code=3,
        )
    if not matches:
        return None
    cells = matches[0][2]
    target_col = 1 if col is None else col
    if not 0 <= target_col < len(cells):
        return None
    return _cell_text_range(cells[target_col])


def _find_table_cell_indices(
    document: dict | None,
    table_start_index: int,
    body: dict | None = None,
) -> list[list[int]]:
    """Find the startIndex of each cell's first paragraph in a table.

    Walks body.content to find the table element at or near the given
    index, then extracts cell paragraph start indices. Searches for the
    nearest table at or after the index (insertTable may place the table
    one position after the specified location).

    Returns a 2D list: cell_indices[row][col] = startIndex.
    """
    if body is None:
        if document is None:
            return []
        body = document.get("body", {})
    for element in body.get("content", []):
        if "table" not in element:
            continue
        el_start = element.get("startIndex", 0)
        # Table may be at index or up to 2 positions after
        if el_start < table_start_index or el_start > table_start_index + 2:
            continue

        table = element["table"]
        cell_indices: list[list[int]] = []
        for row in table.get("tableRows", []):
            row_indices: list[int] = []
            for cell in row.get("tableCells", []):
                cell_content = cell.get("content", [])
                if cell_content:
                    first_para = cell_content[0]
                    para = first_para.get("paragraph", {})
                    elements = para.get("elements", [])
                    if elements:
                        row_indices.append(
                            elements[0].get("startIndex", 0)
                        )
                    else:
                        row_indices.append(
                            first_para.get("startIndex", 0)
                        )
                else:
                    row_indices.append(cell.get("startIndex", 0))
            cell_indices.append(row_indices)
        return cell_indices

    return []


def _revision_conflict(error: Exception) -> bool:
    if not isinstance(error, HttpError) or int(error.resp.status) != 400:
        return False
    detail = str(error).lower()
    return "revision" in detail and any(phrase in detail for phrase in (
        "does not match", "not match", "mismatch", "not the latest",
        "not latest", "stale", "out of date", "too old",
    ))


@dataclass
class _StagedWrite:
    """Track acknowledged stages separately from an unanswered mutation."""

    doc_id: str
    applied: list[str] = field(default_factory=list)
    stage: str = "preparing content"
    sent: bool = False
    rebased: bool = False
    # (content URI, object size) -> object IDs in request order.
    inserted_images: dict[tuple, list[str]] = field(default_factory=dict)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, error, traceback):
        if error is None:
            return False
        if isinstance(error, AuthError) and not self.applied:
            return False  # Raised before any send; keep the exit-2 guidance.
        conflict = _revision_conflict(error) or (
            isinstance(error, GdocError) and error.exit_code == 3
        )
        rejected = (isinstance(error, HttpError)
                    and 400 <= int(error.resp.status) < 500)
        uncertain = self.sent and not rejected
        status = "completion uncertain" if uncertain else "not applied"
        completed = "; ".join(self.applied) or "none confirmed"
        prefix = "Partial completion" if self.applied else "Write failed"
        detail = str(error)
        if isinstance(error, HttpError) and not conflict:
            try:
                _translate_http_error(error, self.doc_id)
            except AuthError:
                if not self.applied:
                    raise
            except GdocError as translated:
                detail = str(translated)
        if conflict:
            detail = f"conflict: {detail}"
        raise GdocError(
            f"{prefix}: applied: {completed}; {self.stage}: {status}; "
            f"remaining stages not attempted. {detail}. "
            "Inspect the document before retrying; "
            "completed batches were not replayed.",
            exit_code=3 if conflict and not self.applied and not uncertain else 1,
        ) from error

    def mark_sent(self):
        self.sent = True

    def read(self, stage: str, tab_id: str | None):
        self.stage, self.sent = stage, False
        kwargs = {"documentId": self.doc_id}
        if tab_id:
            kwargs["includeTabsContent"] = True
        return get_docs_service().documents().get(**kwargs).execute(num_retries=2)

    def batch(self, stage, requests, revision_id, recompute=None):
        self.stage, self.sent = stage, False
        for attempt in range(2):
            if not isinstance(revision_id, str) or not revision_id:
                raise GdocError(
                    "missing revision; refusing an unpinned write", exit_code=3,
                )
            request = get_docs_service().documents().batchUpdate(
                documentId=self.doc_id,
                body={
                    "requests": requests,
                    "writeControl": {"requiredRevisionId": revision_id},
                },
            )
            try:
                from gdoc.api.comment_transport import execute_mutation_request
                response = execute_mutation_request(request, on_send=self.mark_sent)
            except HttpError as error:
                if not _revision_conflict(error) or attempt or recompute is None:
                    raise
                self.sent = False  # A revision rejection is known not to apply.
                try:
                    requests, revision_id = recompute()
                except Exception as failure:
                    raise GdocError(
                        f"conflict: cannot safely recompute {stage}: {failure}",
                        exit_code=3,
                    ) from failure
                self.stage = stage
                self.rebased = True
                continue
            for operation, reply in zip(requests, response.get("replies", [])):
                insertion = operation.get("insertInlineImage", {})
                object_id = reply.get("insertInlineImage", {}).get("objectId")
                if insertion.get("uri") and isinstance(object_id, str) and object_id:
                    self.inserted_images.setdefault(_image_key(
                        insertion["uri"], insertion.get("objectSize"),
                    ), []).append(object_id)
            self.applied.append(stage)
            self.sent = False
            return response.get("writeControl", {}).get("requiredRevisionId", "")


def _stage_tab(doc, tab_id):
    if not tab_id:
        return {"body": doc.get("body", {}),
                "namedRanges": doc.get("namedRanges", {})}
    matches = [t for t in flatten_tabs(doc.get("tabs", [])) if t["id"] == tab_id]
    if len(matches) != 1:
        raise GdocError("conflict: target tab disappeared", exit_code=3)
    return matches[0]


def _stage_body(doc, tab_id):
    if tab_id:
        # Only an exact ID is safe once the tab was selected by the caller.
        matches = [t for t in flatten_tabs(doc.get("tabs", [])) if t["id"] == tab_id]
        if len(matches) != 1:
            raise GdocError("conflict: target tab disappeared", exit_code=3)
        return matches[0]["body"]
    return doc.get("body", {})


def _table_position_resolver(parsed, table, tab_id):
    """Use unique unchanged text before the placeholder, or refuse recovery.

    Search only a contiguous native range. Adjacent tables and table-only
    replacements have no text anchor and deliberately cannot be rebased.
    """
    from gdoc.mdparse import utf16_len

    previous = max((t.plain_text_offset + 1 for t in parsed.tables
                    if t.plain_text_offset < table.plain_text_offset), default=0)
    anchor = parsed.plain_text[previous:table.plain_text_offset]
    # createParagraphBullets consumes leading nesting tabs.
    anchor = re.sub(r"(?m)^\t+", "", anchor)

    def resolve(doc):
        candidates = find_text_in_document(
            None, anchor, match_case=True, body=_stage_body(doc, tab_id),
        ) if anchor.strip() else []
        chars = dict(pair for segment in _collect_segments(
            _stage_body(doc, tab_id).get("content", []),
        ) for pair in segment)
        candidates = [m for m in candidates
                      if m["endIndex"] - m["startIndex"] == utf16_len(anchor)
                      and chars.get(m["endIndex"]) == "\n"]
        if len(candidates) != 1:
            raise GdocError(
                "conflict: cannot uniquely relocate the table insertion point",
                exit_code=3,
            )
        return candidates[0]["endIndex"]

    return resolve


def _without_indices(value):
    if isinstance(value, dict):
        return {k: _without_indices(v) for k, v in value.items()
                if k not in ("startIndex", "endIndex")}
    if isinstance(value, list):
        return [_without_indices(v) for v in value]
    return value


def _table_at(body, index):
    matches = [e for e in body.get("content", [])
               if "table" in e and index <= e.get("startIndex", 0) <= index + 2]
    if len(matches) != 1:
        raise GdocError("conflict: inserted table cannot be identified", exit_code=3)
    return matches[0]


def _table_prefix_requests(cell_indices, table, tab_id):
    """Record a quote or list container on the whole first-cell paragraph.

    Cell text is inserted at or after this index, so the paragraph start is
    stable within the fill batch. The range covers the inserted text and the
    retained terminator: whole code points, and it survives replacement of the
    cell's text. The exporter reads the container from here.
    """
    from gdoc.mdparse import parse_inline, utf16_len

    path = getattr(table, "path", ())
    if not path or not cell_indices or not cell_indices[0]:
        return []
    start = cell_indices[0][0]
    raw = table.rows[0][0] if table.rows and table.rows[0] else ""
    plain = parse_inline(raw)[0] if raw else ""
    span = {"startIndex": start, "endIndex": start + utf16_len(plain) + 1}
    if tab_id:
        span["tabId"] = tab_id
    return [{"createNamedRange": {
        "name": _prefix_range_name(path), "range": span,
    }}]


def _table_cell_requests(cell_indices, table, tab_id):
    # Parse each cell's markdown to plain text + inline styles, once.
    from gdoc.mdparse import (
        _utf16_prefix,
        parse_inline,
        text_style_fields,
        utf16_len,
    )

    parsed_cells: dict[tuple[int, int], tuple[str, list]] = {}
    for r_idx, row in enumerate(cell_indices):
        for c_idx in range(len(row)):
            raw = ""
            if r_idx < len(table.rows) and c_idx < len(table.rows[r_idx]):
                raw = table.rows[r_idx][c_idx]
            parsed_cells[(r_idx, c_idx)] = (
                parse_inline(raw) if raw else ("", [])
            )

    # Step 3: Insert cell plain text (reverse order, so the original cell
    # indices stay valid — inserting at a higher index never shifts a
    # lower one).
    text_requests: list[dict] = []
    for r_idx in range(len(cell_indices) - 1, -1, -1):
        row = cell_indices[r_idx]
        for c_idx in range(len(row) - 1, -1, -1):
            plain, _ = parsed_cells[(r_idx, c_idx)]
            if plain:
                cell_location = {"index": row[c_idx]}
                if tab_id:
                    cell_location["tabId"] = tab_id
                text_requests.append({
                    "insertText": {
                        "location": cell_location,
                        "text": plain,
                    }
                })

    # Apply only requested inline styles in forward
    # index order. Each cell's final position is its original index plus the
    # total length of all earlier (lower-index) cells already inserted.
    shift = 0
    for r_idx in range(len(cell_indices)):
        row = cell_indices[r_idx]
        for c_idx in range(len(row)):
            plain, cell_styles = parsed_cells[(r_idx, c_idx)]
            cell_styles = list(cell_styles)
            base = row[c_idx] + shift
            alignment = (table.alignments[c_idx]
                         if c_idx < len(table.alignments) else None)
            if alignment:
                span = {"startIndex": base, "endIndex": base + utf16_len(plain) + 1}
                if tab_id:
                    span["tabId"] = tab_id
                text_requests.append({"updateParagraphStyle": {
                    "range": span, "paragraphStyle": {"alignment": alignment},
                    "fields": "alignment",
                }})
            # Style offsets are code points; Docs indexes are UTF-16.
            utf16 = _utf16_prefix(plain)
            for s in cell_styles:
                if s.type == "image":
                    continue
                style_range = {
                    "startIndex": base + utf16[s.start],
                    "endIndex": base + utf16[s.end],
                }
                if tab_id:
                    style_range["tabId"] = tab_id
                text_requests.append({
                    "updateTextStyle": {
                        "range": style_range,
                        "textStyle": s.style,
                        "fields": text_style_fields(s.style),
                    }
                })
            from gdoc.mdparse import ImageData
            images = [ImageData(style.start,
                                getattr(table, "image_sources", {}).get(
                                    style.style["uri"], style.style["uri"]),
                                style.style["alt"])
                      for style in cell_styles if style.type == "image"]
            image_styles = [style for style in cell_styles if style.type == "image"]
            for image, style in zip(images, image_styles):
                image.object_size = getattr(table, "image_sizes", {}).get(
                    style.style["uri"],
                )
            text_requests.extend(_image_requests(images, plain, base, tab_id,
                                                 cell_styles))
            shift += utf16_len(plain)

    return text_requests


def _table_scaffolding(parsed, table):
    """Describe where a table goes relative to the parser's placeholder.

    Returns ``(before, placeholder)``. ``before`` is 1 when a paragraph of
    the inserted Markdown ends directly before the placeholder;
    ``placeholder`` is 1 when the placeholder paragraph exists.

    The table is inserted at the preceding paragraph's mark, which splits
    that paragraph: its text keeps a copy of its own style and list
    membership, and its emptied original mark and the placeholder land after
    the table, where only they are removed.
    """
    offset = table.plain_text_offset
    previous_placeholder = any(t.plain_text_offset == offset - 1
                               for t in parsed.tables)
    before = (offset > 0 and parsed.plain_text[offset - 1] == "\n"
              and not previous_placeholder)
    placeholder = offset < len(parsed.plain_text)
    return int(before), int(placeholder)


def _table_cleanup_requests(tab, table_element, scaffolding, tab_id, expected=None):
    """Remove the table's scaffolding paragraphs and clip grown gdoc ranges.

    Inserting at the preceding paragraph's mark grows a gdoc range that ended
    after that mark over the new table. Such a range is recreated to end at
    the paragraph's new mark. Only whole empty paragraphs directly after the
    table are deleted, and never the tab's final paragraph, so no paragraph
    that keeps text loses its own mark. Each is removed by its own
    single-paragraph deletion, the merge shape observed live to leave the
    following paragraph's style and list membership intact; that paragraph's
    style is also restored explicitly, and an inherited bullet removed.

    Returns ``(requests, neighbors)``. ``neighbors`` fingerprints the
    paragraphs after the table; with ``expected`` (the fingerprint from the
    pinned read), a relocated fill refuses if a collaborator changed them.
    """
    before, placeholder = scaffolding
    count = before + placeholder
    content = tab.get("body", {}).get("content", [])
    start = table_element["startIndex"]
    end = table_element["endIndex"]
    position = next(i for i, e in enumerate(content)
                    if "table" in e and e.get("startIndex") == start)
    following = content[position + 1:position + 2 + count]
    neighbors = _without_indices(following)
    if expected is not None and neighbors != expected:
        raise GdocError(
            "conflict: the paragraphs after the inserted table changed; "
            "scaffolding was not removed", exit_code=3,
        )
    requests = []
    # A split grew a range over the table (it ended after the split mark);
    # it is recreated to end at the paragraph's new mark, directly before
    # the table.
    grown_end = end + 1 if before else None
    for named_id, name, spans in _owned_named_ranges(tab, tab_id):
        if len(spans) != 1:
            continue
        if spans[0][0] < start - 1 and spans[0][1] == grown_end:
            span = {"startIndex": spans[0][0], "endIndex": start}
        elif before and spans[0][0] == end and spans[0][1] <= end + count:
            # A range of the empty paragraph before the table moved with its
            # original mark past the table; it belongs on the new mark.
            span = {"startIndex": start - 1, "endIndex": start}
        else:
            continue
        if tab_id:
            span["tabId"] = tab_id
        requests.extend([_delete_owned_range(named_id, tab_id), {
            "createNamedRange": {"name": name, "range": span},
        }])
    if not count:
        return _final_paragraph_reset(content, position, placeholder,
                                      tab_id) + requests, neighbors
    removed = [e for e in following[:count] if _is_empty_paragraph(e)]
    if (len(removed) != count or len(following) != count + 1
            or "paragraph" not in following[-1]):
        raise GdocError(
            "conflict: the paragraphs around the inserted table changed; "
            "scaffolding was not removed", exit_code=3,
        )

    def ranged(body):
        span = {"startIndex": end, "endIndex": end + 1}
        if tab_id:
            span["tabId"] = tab_id
        return {**body, "range": span}

    # One empty paragraph per request: deleting a single empty paragraph at
    # a paragraph start is the merge shape observed live to leave the
    # following paragraph's style and list membership intact.
    cleanup = [{"deleteContentRange": ranged({})} for _ in range(count)]
    survivor = following[-1]["paragraph"]
    first = removed[0]["paragraph"]
    kept = survivor.get("paragraphStyle", {})
    merged = first.get("paragraphStyle", {})
    fields = [f for f in _RESTORED_PARAGRAPH_FIELDS
              if (f in kept or f in merged) and kept.get(f) != merged.get(f)]
    if fields:
        cleanup.append({"updateParagraphStyle": ranged({
            "paragraphStyle": {f: kept[f] for f in fields if f in kept},
            "fields": ",".join(fields),
        })})
    if first.get("bullet") and not survivor.get("bullet"):
        cleanup.append({"deleteParagraphBullets": ranged({})})
    return cleanup + requests, neighbors


def _final_paragraph_reset(content, position, placeholder, tab_id):
    """Plain final paragraph after a table that ends the inserted Markdown.

    Appending a leading table splits the tab's last paragraph at its mark,
    so the mandatory final paragraph after the table keeps that paragraph's
    heading or bullet. The Markdown ends with the table, so the final
    paragraph is reset like the retained mark of a replaced tab.
    """
    if placeholder or position + 2 != len(content):
        return []
    final = content[-1]
    paragraph = final.get("paragraph", {})
    style = paragraph.get("paragraphStyle", {})
    if not _is_empty_paragraph(final) or not (
            paragraph.get("bullet")
            or style.get("namedStyleType", "NORMAL_TEXT") != "NORMAL_TEXT"
            or any(style.get(f) for f in _INSERT_INHERITED_FIELDS)):
        return []
    span = {"startIndex": final["startIndex"], "endIndex": final["endIndex"]}
    if tab_id:
        span["tabId"] = tab_id
    return [
        {"deleteParagraphBullets": {"range": dict(span)}},
        {"updateParagraphStyle": {
            "range": dict(span),
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "fields": "*",
        }},
    ]


# Writable paragraph style fields restored on a paragraph that a merge could
# restyle. Output-only fields such as headingId are never sent.
_RESTORED_PARAGRAPH_FIELDS = (
    "namedStyleType", "alignment", "lineSpacing", "direction", "spacingMode",
    "spaceAbove", "spaceBelow", "borderBetween", "borderTop", "borderBottom",
    "borderLeft", "borderRight", "indentFirstLine", "indentStart", "indentEnd",
    "keepLinesTogether", "keepWithNext", "avoidWidowAndOrphan", "shading",
    "pageBreakBefore",
)


def _is_empty_paragraph(element):
    paragraph = element.get("paragraph")
    if not paragraph or paragraph.get("positionedObjectIds"):
        return False
    elements = paragraph.get("elements", [])
    return (len(elements) == 1
            and elements[0].get("textRun", {}).get("content") == "\n")


def _insert_table(
    doc_id: str,
    index: int,
    table,
    tab_id: str | None = None,
    *, revision_id: str = "", progress=None, resolve_index=None,
    ordinal: int = 1, scaffolding: tuple = (0, 0),
) -> str:
    """Insert and fill a table with revision-pinned, single-shot stages."""
    if progress is None:
        with _StagedWrite(doc_id) as progress:
            if not revision_id:
                doc = progress.read("reading table insertion point", tab_id)
                revision_id = doc.get("revisionId", "")
            return _insert_table(
                doc_id, index, table, tab_id, revision_id=revision_id,
                progress=progress, resolve_index=resolve_index, ordinal=ordinal,
                scaffolding=scaffolding,
            )

    label = f"table {ordinal} in tab {tab_id or 'default'}"

    def insertion():
        # insertTable supplies its own leading newline. At a paragraph's mark
        # that newline splits the paragraph, so no mark of a paragraph that
        # keeps text is ever deleted; the emptied scaffolding after the table
        # is removed with the cell fill, where its content can be verified.
        before, _ = scaffolding
        location = {"index": index - before}
        if tab_id:
            location["tabId"] = tab_id
        return [{"insertTable": {
            "rows": table.num_rows, "columns": table.num_cols,
            "location": location,
        }}]

    def relocate():
        nonlocal index
        doc = progress.read("re-reading table insertion point", tab_id)
        if resolve_index is None:
            raise GdocError("conflict: cannot relocate table insertion", exit_code=3)
        index = resolve_index(doc)
        return insertion(), doc.get("revisionId", "")

    # A previous table may have recovered from a collaborator's index shift.
    if progress.rebased:
        requests, revision_id = relocate()
    else:
        requests = insertion()
    revision_id = progress.batch(
        f"{label}: table structure inserted", requests, revision_id, relocate,
    )
    doc = progress.read("reading inserted table cells", tab_id)
    if not revision_id or doc.get("revisionId") != revision_id:
        # The collaborator moved the table before its first read-back. We
        # have no trusted table fingerprint yet; do not guess another table.
        raise GdocError(
            "conflict: document changed before the inserted table could be located",
            exit_code=3,
        )
    body = _stage_body(doc, tab_id)
    element = _table_at(body, index - scaffolding[0])
    fingerprint = _without_indices(element["table"])
    _, neighbors = _table_cleanup_requests(_stage_tab(doc, tab_id), element,
                                           scaffolding, tab_id)
    unique_before = sum(
        "table" in e and _without_indices(e["table"]) == fingerprint
        for e in body.get("content", [])
    ) == 1

    def fill(snapshot, target):
        indices = _find_table_cell_indices(None, target["startIndex"],
                                           body=_stage_body(snapshot, tab_id))
        if len(indices) != table.num_rows or any(
            len(row) != table.num_cols for row in indices
        ):
            raise GdocError("conflict: table dimensions changed", exit_code=3)
        # Cleanup lies after the table, so the cell indices stay valid.
        cleanup, _ = _table_cleanup_requests(
            _stage_tab(snapshot, tab_id), target, scaffolding, tab_id,
            expected=neighbors,
        )
        return (cleanup + _table_cell_requests(indices, table, tab_id)
                + _table_prefix_requests(indices, table, tab_id))

    def relocate_cells():
        snapshot = progress.read("re-reading table cells", tab_id)
        candidates = [e for e in _stage_body(snapshot, tab_id).get("content", [])
                      if "table" in e and _without_indices(e["table"]) == fingerprint]
        if not unique_before or len(candidates) != 1:
            raise GdocError(
                "conflict: inserted table changed or is ambiguous; cells not filled",
                exit_code=3,
            )
        return fill(snapshot, candidates[0]), snapshot.get("revisionId", "")

    requests = fill(doc, element)
    if requests:
        revision_id = progress.batch(
            f"{label}: table cells filled", requests, doc.get("revisionId", ""),
            relocate_cells,
        )
    return revision_id


def _collect_object_refs(body: dict) -> list[tuple[str, int, str]]:
    """Walk body.content for object references.

    Returns (object_id, start_index, source) tuples, where source is
    "inline" or "positioned".
    """
    refs: list[tuple[str, int, str]] = []
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        for pe in paragraph.get("elements", []):
            ioe = pe.get("inlineObjectElement")
            if ioe:
                obj_id = ioe.get("inlineObjectId", "")
                start = pe.get("startIndex", 0)
                refs.append((obj_id, start, "inline"))
        # Check for positioned object references
        positioned_ids = paragraph.get("positionedObjectIds", [])
        para_start = element.get("startIndex", 0)
        for pid in positioned_ids:
            refs.append((pid, para_start, "positioned"))
    return refs


def list_inline_objects(doc_id: str) -> list[dict]:
    """List all inline and positioned objects in a document, every tab.

    Walks each tab's body.content for inlineObjectElement and
    positionedObjectId references, joins with the tab's inlineObjects
    and positionedObjects maps, and classifies each object.

    Returns list of dicts with id, tab, type, title, description,
    dimensions, content_uri, source_uri, start_index, and chart
    metadata. `tab` is "" for documents fetched without tab content.
    """
    doc = get_document_with_tabs(doc_id)

    # (tab_id, body, inline_map, positioned_map) per content segment
    segments: list[tuple[str, dict, dict, dict]] = []
    tabs = doc.get("tabs")
    if tabs:
        def walk(ts: list[dict]):
            for t in ts:
                doc_tab = t.get("documentTab", {})
                yield (
                    t.get("tabProperties", {}).get("tabId", ""),
                    doc_tab.get("body", {}),
                    doc_tab.get("inlineObjects", {}),
                    doc_tab.get("positionedObjects", {}),
                )
                yield from walk(t.get("childTabs", []))

        segments = list(walk(tabs))
    else:
        segments = [(
            "",
            doc.get("body", {}),
            doc.get("inlineObjects", {}),
            doc.get("positionedObjects", {}),
        )]

    results = []
    seen = set()

    for tab_id, body, inline_map, positioned_map in segments:
        for obj_id, start_index, source in _collect_object_refs(body):
            if (tab_id, obj_id) in seen:
                continue
            seen.add((tab_id, obj_id))

            if source == "inline":
                obj_data = inline_map.get(obj_id, {})
            else:
                obj_data = positioned_map.get(obj_id, {})
            results.append(_classify_object(obj_id, obj_data, start_index, tab_id))

    return results


def _classify_object(
    obj_id: str, obj_data: dict, start_index: int, tab_id: str,
) -> dict:
    """Build the metadata entry for one inline/positioned object."""
    props = obj_data.get("inlineObjectProperties", {}) or obj_data.get(
        "positionedObjectProperties", {}
    )
    embedded = props.get("embeddedObject", {})

    # Classify type
    obj_type = "image"
    spreadsheet_id = None
    chart_id = None
    if "embeddedDrawingProperties" in embedded:
        obj_type = "drawing"
    elif "linkedContentReference" in embedded:
        lcr = embedded["linkedContentReference"]
        if "sheetsChartReference" in lcr:
            obj_type = "chart"
            scr = lcr["sheetsChartReference"]
            spreadsheet_id = scr.get("spreadsheetId")
            chart_id = scr.get("chartId")

    # Extract dimensions
    size = embedded.get("size", {})
    width = size.get("width", {}).get("magnitude", 0)
    height = size.get("height", {}).get("magnitude", 0)

    # Content URI (None for drawings)
    content_uri = None
    if obj_type != "drawing":
        image_props = embedded.get("imageProperties", {})
        content_uri = image_props.get("contentUri")

    entry = {
        "id": obj_id,
        "tab": tab_id,
        "type": obj_type,
        "title": embedded.get("title", ""),
        "description": embedded.get("description", ""),
        "width_pt": width,
        "height_pt": height,
        "content_uri": content_uri,
        "source_uri": embedded.get("imageProperties", {}).get("sourceUri"),
        "start_index": start_index,
    }
    if obj_type == "chart":
        entry["spreadsheet_id"] = spreadsheet_id
        entry["chart_id"] = chart_id
    return entry


def download_image(content_uri: str, dest_path: str) -> None:
    """Download an image from a pre-signed content URI to a local file."""
    import urllib.request

    with urllib.request.urlopen(content_uri) as resp:
        data = resp.read()
    with open(dest_path, "wb") as f:
        f.write(data)


def insert_inline_image(
    doc_id: str,
    uri: str,
    index: int,
    tab_id: str | None = None,
    revision_id: str = "",
    width_pt: float | None = None,
    height_pt: float | None = None,
    result_details: dict | None = None,
) -> str:
    """Insert an inline image at a document index via insertInlineImage.

    Args:
        doc_id: The document ID.
        uri: Publicly fetchable image URL (Google's servers download it).
        index: UTF-16 insertion index (from find_text_in_document).
        tab_id: Tab the index lives in (omitted → first tab).
        revision_id: If non-empty, sent as writeControl.requiredRevisionId
            so the insert can't land on stale coordinates.
        width_pt: Optional display width in points.
        height_pt: Optional display height in points.

    Returns:
        The new inline object ID (usable with `gdoc images`/replace_image).
    """
    location: dict = {"index": index}
    if tab_id:
        location["tabId"] = tab_id
    request: dict = {"location": location, "uri": uri}
    size: dict = {}
    if width_pt:
        size["width"] = {"magnitude": width_pt, "unit": "PT"}
    if height_pt:
        size["height"] = {"magnitude": height_pt, "unit": "PT"}
    if size:
        request["objectSize"] = size
    body: dict = {"requests": [{"insertInlineImage": request}]}
    if revision_id:
        body["writeControl"] = {"requiredRevisionId": revision_id}
    try:
        service = get_docs_service()
        from gdoc.api.comment_transport import execute_mutation_request

        result = execute_mutation_request(
            service.documents().batchUpdate(documentId=doc_id, body=body),
            uncertainty="Image write outcome is uncertain; inspect before retrying",
        )
    except HttpError as e:
        _raise_if_stale_revision(e)
        _translate_http_error(e, doc_id)
    if result_details is not None:
        result_details["acknowledged_revision_id"] = result.get("writeControl", {}).get(
            "requiredRevisionId", "",
        )
    replies = result.get("replies", [])
    return (replies[0] if replies else {}).get(
        "insertInlineImage", {},
    ).get("objectId", "")


def replace_image(
    doc_id: str,
    object_id: str,
    uri: str,
    tab_id: str | None = None,
    revision_id: str = "",
    result_details: dict | None = None,
) -> None:
    """Replace an existing image's content via replaceImage.

    The existing image keeps its size; the new content is scaled and
    center-cropped to fit (CENTER_CROP is the only supported method).

    Args:
        doc_id: The document ID.
        object_id: Inline object ID of the image (see `gdoc images`).
        uri: Publicly fetchable replacement image URL.
        tab_id: Tab the image lives in (omitted → first tab).
        revision_id: If non-empty, sent as writeControl.requiredRevisionId.
    """
    request: dict = {
        "imageObjectId": object_id,
        "uri": uri,
        "imageReplaceMethod": "CENTER_CROP",
    }
    if tab_id:
        request["tabId"] = tab_id
    body: dict = {"requests": [{"replaceImage": request}]}
    if revision_id:
        body["writeControl"] = {"requiredRevisionId": revision_id}
    try:
        service = get_docs_service()
        from gdoc.api.comment_transport import execute_mutation_request

        result = execute_mutation_request(
            service.documents().batchUpdate(documentId=doc_id, body=body),
            uncertainty="Image write outcome is uncertain; inspect before retrying",
        )
    except HttpError as e:
        _raise_if_stale_revision(e)
        _translate_http_error(e, doc_id)

    if result_details is not None:
        result_details["acknowledged_revision_id"] = result.get("writeControl", {}).get(
            "requiredRevisionId", "",
        )


def _raise_if_stale_revision(e: HttpError) -> None:
    """Turn a writeControl revision-mismatch 400 into a clear retry hint."""
    if int(e.resp.status) == 400 and "revision" in str(e).lower():
        # The server refused the pinned revision, so nothing was applied: a
        # clean refusal (exit 3), like other revision conflicts.
        raise GdocError(
            "document changed while the command was running; re-run it",
            exit_code=3,
        )


def find_object_tab(doc: dict, object_id: str) -> str | None:
    """Find which tab holds an inline/positioned object ID.

    Walks the tab tree of a documents.get(includeTabsContent=True) response.
    Returns the tab ID, or None if no tab declares the object (including
    docs fetched without tab content).
    """
    def walk(tabs: list[dict]) -> str | None:
        for tab in tabs:
            doc_tab = tab.get("documentTab", {})
            if (
                object_id in doc_tab.get("inlineObjects", {})
                or object_id in doc_tab.get("positionedObjects", {})
            ):
                return tab.get("tabProperties", {}).get("tabId")
            found = walk(tab.get("childTabs", []))
            if found is not None:
                return found
        return None

    return walk(doc.get("tabs", []))


_HEADING_LEVELS = {
    "HEADING_1": 1, "HEADING_2": 2, "HEADING_3": 3,
    "HEADING_4": 4, "HEADING_5": 5, "HEADING_6": 6,
}


def get_document_headings(doc_id: str, body: dict | None = None) -> list[dict]:
    """Extract headings with deep-link IDs from a document body.

    If *body* is provided (e.g. from a specific tab), it is used
    directly; otherwise the document is fetched via documents().get().

    Returns a list of dicts:
        {"level": int, "heading_id": str, "text": str}
    """
    if body is None:
        doc = get_document(doc_id)
        body = doc.get("body", {})

    headings: list[dict] = []
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        style = paragraph.get("paragraphStyle", {})
        named_style = style.get("namedStyleType", "")
        heading_id = style.get("headingId")
        if named_style not in _HEADING_LEVELS or not heading_id:
            continue
        text = "".join(
            run.get("textRun", {}).get("content", "")
            for run in paragraph.get("elements", [])
        ).strip()
        if not text:
            continue
        headings.append({
            "level": _HEADING_LEVELS[named_style],
            "heading_id": heading_id,
            "text": text,
        })
    return headings


def get_document_with_tabs(doc_id: str) -> dict:
    """Fetch document with includeTabsContent=True.

    Returns the full document dict (including revisionId and tabs).
    Uses the bounded Google-client retry policy documented in get_document.
    HttpError is translated via _translate_http_error.
    """
    try:
        service = get_docs_service()
        return (
            service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute(num_retries=2)
        )
    except HttpError as e:
        _translate_http_error(e, doc_id)


def get_document_structure(
    doc_id: str,
    fields: str | None = None,
    suggestions_view_mode: str | None = None,
) -> dict:
    """Fetch the raw documents.get JSON for native structure inspection.

    Always requests includeTabsContent=True so every tab's body is
    present. A fields mask is passed verbatim when given — note Google
    rejects masks that recursively expand childTabs (repo issue #14).
    Uses the bounded Google-client retry policy documented in get_document.

    Args:
        doc_id: The document ID.
        fields: Optional Docs API field mask.
        suggestions_view_mode: Optional SuggestionsViewMode enum value
            (e.g. PREVIEW_SUGGESTIONS_ACCEPTED). Changes returned content
            and indexes.
    """
    kwargs: dict = {"documentId": doc_id, "includeTabsContent": True}
    if fields:
        kwargs["fields"] = fields
    if suggestions_view_mode:
        kwargs["suggestionsViewMode"] = suggestions_view_mode
    try:
        service = get_docs_service()
        return service.documents().get(**kwargs).execute(num_retries=2)
    except HttpError as e:
        _translate_http_error(e, doc_id)


def resolve_raw_tab(tabs: list[dict], tab_name: str) -> dict | None:
    """Resolve a raw tab across the tree with the same identity rules as resolve_tab.

    Return None when missing; refuse ambiguous titles with exit code 3.
    """
    def walk(ts: list[dict]):
        for tab in ts:
            props = tab.get("tabProperties", {})
            yield {"id": props.get("tabId", ""), "title": props.get("title", ""),
                   "raw": tab}
            yield from walk(tab.get("childTabs", []))

    match = _match_tab(list(walk(tabs)), tab_name)
    return match["raw"] if match is not None else None


def add_tab(doc_id: str, title: str) -> dict:
    """Add a new tab to a document.

    Returns dict with 'tabId', 'title', 'index'.
    """
    service = get_docs_service()
    from gdoc.api.comment_transport import execute_mutation_request

    uncertainty = ("Tab creation outcome is uncertain; list the document's "
                   "tabs before retrying")
    try:
        resp = execute_mutation_request(service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"addDocumentTab": {
                "tabProperties": {"title": title},
            }}]},
        ), uncertainty=uncertainty)
    except HttpError as e:
        _translate_http_error(e, doc_id)
    # The request was sent and acknowledged, so the tab may exist even when
    # the reply cannot be read: report that, never a plain failure to retry.
    try:
        props = resp["replies"][0]["addDocumentTab"]["tabProperties"]
        tab_id = props["tabId"]
        if not isinstance(tab_id, str) or not tab_id:
            raise TypeError("tabId is not a non-empty string")
        return {
            "tabId": tab_id,
            "title": props.get("title", title),
            "index": props.get("index", 0),
        }
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GdocError(
            f"{uncertainty}: the response did not identify the new tab ({exc})",
        ) from exc


def _build_cleanup_requests(
    body: dict, position: int, tab_id: str | None = None,
) -> list[dict]:
    """Remove an explicitly identified empty paragraph, never its neighbor.

    Call only for a separator created by the current operation. The segment's
    final newline is mandatory, and non-text elements are never scaffolding.
    """
    content = body.get("content", [])
    for element in content:
        if element.get("startIndex") != position:
            continue
        paragraph = element.get("paragraph", {})
        elements = paragraph.get("elements", [])
        if (paragraph.get("positionedObjectIds") or len(elements) != 1
                or elements[0].get("textRun", {}).get("content") != "\n"
                or elements[0].get("startIndex") != position
                or elements[0].get("endIndex") != position + 1
                or element.get("endIndex") != position + 1
                or element is content[-1]):
            return []
        target = {"startIndex": position, "endIndex": position + 1}
        if tab_id:
            target["tabId"] = tab_id
        return [{"deleteContentRange": {"range": target}}]
    return []


def _structured_empty_paragraph(body: dict, tab: dict | None = None,
                                tab_id: str | None = None) -> bool:
    """True for a lone empty paragraph that Markdown reads as structure,
    including an empty code line or quote held by one of gdoc's ranges."""
    paragraphs = [e["paragraph"] for e in body.get("content", []) if "paragraph" in e]
    if len(paragraphs) != 1 or len(body.get("content", [])) > 2:
        return False
    style = paragraphs[0].get("paragraphStyle", {})
    return bool(paragraphs[0].get("bullet")
                or style.get("namedStyleType", "NORMAL_TEXT") != "NORMAL_TEXT"
                or any(style.get(f) for f in _INSERT_INHERITED_FIELDS)
                or any(True for _ in _owned_named_ranges(tab, tab_id)))


def _tab_body_range(body: dict) -> tuple[int, int]:
    """Return (startIndex, endIndex_exclusive_final_newline) for a tab body.

    A tab body always begins at index 1. The "end" is one less than the
    final element's endIndex because Docs stores a trailing newline that
    cannot be deleted. Returns (1, 1) for an empty body.
    """
    last_end = 1
    for elem in body.get("content", []):
        end = elem.get("endIndex")
        if end is not None and end > last_end:
            last_end = end
    if last_end <= 1:
        return (1, 1)
    return (1, last_end - 1)


def _strip_trailing_newline_unless_hr(parsed) -> None:
    """Drop the trailing \\n parse_markdown appends — the existing paragraph at
    the insertion point already owns one, so without this every write leaves an
    extra blank line. Skipped when the last paragraph is a horizontal rule (an
    intentionally-empty paragraph whose border is lost if its only character is
    removed). Mutates ``parsed`` in place.
    """
    old_len = len(parsed.plain_text)
    last_is_hr = any(
        s.type == "paragraph_style" and s.end == old_len
        and "borderBottom" in s.style
        for s in parsed.styles
    )
    if parsed.plain_text.endswith("\n") and not last_is_hr:
        parsed.plain_text = parsed.plain_text[:-1]
        for s in parsed.styles:
            if s.end == old_len:
                s.end = old_len - 1


def _reset_list_indents(parsed) -> None:
    """Clear inherited list indents without overriding explicit Markdown."""
    for style in parsed.styles:
        if style.type == "paragraph_style":
            for field in ("indentStart", "indentEnd", "indentFirstLine"):
                style.style.setdefault(field, {"magnitude": 0, "unit": "PT"})


def _mixed_list_requests(parsed, insert_index, tab_id):
    """Compile list boundaries, continuation, nesting and literal content tabs."""
    from gdoc.mdparse import list_requests

    return list_requests(parsed, insert_index, tab_id)


def _parsed_images(parsed):
    """Include contextual inline images and their native size metadata."""
    from gdoc.mdparse import ImageData

    images = list(parsed.images)
    for style in parsed.styles:
        if style.type == "image":
            image = ImageData(style.start, style.style["uri"], style.style["alt"])
            image.object_size = style.style.get("objectSize")
            images.append(image)
    return images


def _image_key(uri, size):
    """Identify inserted images that are interchangeable in later writes."""
    return uri, json.dumps(size, sort_keys=True) if size else ""


def _image_reference_properties(uri, snapshot):
    if not uri.startswith("gdoc-image:"):
        return {}
    object_id = uri.removeprefix("gdoc-image:")
    scopes = flatten_tabs(snapshot["tabs"]) if "tabs" in snapshot else [snapshot]
    objects = [scope.get("inlineObjects", {}).get(object_id) for scope in scopes]
    objects = [obj for obj in objects if obj is not None]
    if len(objects) != 1:
        raise GdocError(
            "image reference is missing or ambiguous in the current snapshot: "
            + object_id, exit_code=3,
        )
    return objects[0].get("inlineObjectProperties", {}).get("embeddedObject", {})


def _resolve_image_uri(uri, snapshot):
    from urllib.parse import urlsplit

    if uri.startswith("gdoc-image:"):
        embedded = _image_reference_properties(uri, snapshot)
        uri = embedded.get("imageProperties", {}).get("contentUri", "")
    parsed = urlsplit(uri)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise GdocError("image source must be an accessible HTTP(S) URL or a current "
                        "gdoc-image reference", exit_code=3)
    return uri


def _prepare_image_sources(parsed, snapshot, aliases=None):
    """Resolve all sources before destructive requests, including table cells.

    ``aliases`` maps an image reference that an acknowledged gdoc write
    replaced to its copy; it applies only when the original object is gone.
    """
    import sys

    from gdoc.mdparse import parse_inline

    scopes = flatten_tabs(snapshot["tabs"]) if "tabs" in snapshot else [snapshot]

    def current(uri):
        object_id = uri.removeprefix("gdoc-image:")
        if (uri == object_id or object_id not in (aliases or {})
                or any(object_id in s.get("inlineObjects", {}) for s in scopes)):
            return uri
        return "gdoc-image:" + aliases[object_id]

    parsed.image_reference_sources = {}
    has_alt = any(image.alt for image in _parsed_images(parsed))
    for table in parsed.tables:
        sources = {}
        sizes = {}
        for row in table.rows:
            for cell in row:
                _, styles = parse_inline(cell)
                for style in styles:
                    if style.type == "image":
                        source = style.style["uri"]
                        sources[source] = _resolve_image_uri(current(source), snapshot)
                        sizes[source] = _image_reference_properties(
                            current(source), snapshot,
                        ).get("size")
                        if source.startswith("gdoc-image:"):
                            parsed.image_reference_sources[source[11:]] = (
                                _image_key(sources[source], sizes[source])
                            )
                        if style.style.get("alt"):
                            has_alt = True
        table.image_sources = sources
        table.image_sizes = sizes
    for image in _parsed_images(parsed):
        image.object_size = _image_reference_properties(
            current(image.uri), snapshot,
        ).get("size")
        resolved = _resolve_image_uri(current(image.uri), snapshot)
        if image.uri.startswith("gdoc-image:"):
            parsed.image_reference_sources[image.uri[11:]] = _image_key(
                resolved, image.object_size,
            )
        image.uri = resolved
        for style in parsed.styles:
            if style.type == "image" and style.start == image.plain_text_offset:
                style.style = {**style.style, "uri": resolved,
                               "objectSize": image.object_size}
    if has_alt:
        print("WARN: Google Docs API cannot set image alt text; inserted images "
              "will not retain the Markdown alt description.", file=sys.stderr)


def _image_requests(images, text, insert_index, tab_id, styles=()):
    """Replace each image placeholder with its image, keeping any link on it."""
    from gdoc.mdparse import utf16_len

    requests = []
    for image in images:
        link = next((s.style["link"] for s in styles
                     if s.type == "text_style" and "link" in s.style
                     and s.start <= image.plain_text_offset < s.end), None)
        index = (insert_index + utf16_len(text[:image.plain_text_offset])
                 - image.removed_tabs_before)
        target = {"startIndex": index, "endIndex": index + 1}
        location = {"index": index}
        if tab_id:
            target["tabId"] = location["tabId"] = tab_id
        insertion = {"location": location, "uri": image.uri}
        if getattr(image, "object_size", None):
            insertion["objectSize"] = image.object_size
        requests.extend([
            {"deleteContentRange": {"range": target}},
            {"insertInlineImage": insertion},
        ])
        if link:
            requests.append({"updateTextStyle": {
                "range": dict(target), "textStyle": {"link": link}, "fields": "link",
            }})
    return requests


def _native_docs_requests(parsed, insert_index, tab_id=None):
    from gdoc.mdparse import prefix_indent_requests, to_docs_requests

    requests = to_docs_requests(parsed, insert_index, tab_id=tab_id,
                                include_lists=False)
    requests.extend(_mixed_list_requests(parsed, insert_index, tab_id))
    requests.extend(prefix_indent_requests(parsed, insert_index, tab_id))
    requests.extend(_image_requests(_parsed_images(parsed), parsed.plain_text,
                                    insert_index, tab_id, parsed.styles))
    return requests


def _code_range_requests(parsed, insert_index: int, tab_id: str | None) -> list[dict]:
    """Mark complete code paragraphs before tables shift their native positions."""
    from gdoc.mdparse import utf16_len

    def coordinate(offset):
        consumed = sum(
            style.list_depth for style in parsed.styles
            if style.type == "bullets" and style.start < offset
        )
        # A final code range includes the mandatory retained paragraph newline.
        return insert_index + utf16_len(parsed.plain_text[:offset]) + max(
            0, offset - len(parsed.plain_text),
        ) - consumed

    requests = []
    for block in parsed.code_blocks:
        span = {"startIndex": coordinate(block.start),
                "endIndex": coordinate(block.end)}
        if tab_id:
            span["tabId"] = tab_id
        requests.append({"createNamedRange": {"name": "gdoc:code:v1", "range": span}})
    for style in parsed.styles:
        if style.type != "markdown_prefix":
            continue
        start = coordinate(style.start)
        span = {"startIndex": start,
                "endIndex": max(start + 1, coordinate(style.end))}
        if tab_id:
            span["tabId"] = tab_id
        name = _prefix_range_name(style.path)
        requests.append({"createNamedRange": {"name": name, "range": span}})
    return requests


_OWNED_RANGE_NAME_RE = re.compile(
    r"gdoc:code:v1|gdoc:prefix:v1:\d+:\d+|gdoc:prefix:v2:\d+:\d+:\d+"
    r"|gdoc:prefix:v3:(?:q|\d+)(?:\.(?:q|\d+))*")


def _owned_named_ranges(tab: dict | None, tab_id: str | None):
    """Yield ``(namedRangeId, name, spans)`` for gdoc's own ranges in a tab body.

    Only code and container markers that gdoc creates qualify; other named
    ranges, and ranges in headers, footers or footnotes, are never touched.
    """
    for group in (tab or {}).get("namedRanges", {}).values():
        for named in group.get("namedRanges", []):
            name = named.get("name", group.get("name", ""))
            ranges = named.get("ranges", [])
            if (not _OWNED_RANGE_NAME_RE.fullmatch(name)
                    or not named.get("namedRangeId") or not ranges
                    or any(r.get("segmentId") for r in ranges)
                    or any(tab_id and r.get("tabId", tab_id) != tab_id
                           for r in ranges)):
                continue
            yield named["namedRangeId"], name, [
                (r.get("startIndex", 0), r.get("endIndex", 0)) for r in ranges
            ]


def _delete_owned_range(named_id: str, tab_id: str | None) -> dict:
    """Delete one gdoc range by ID, limited to its tab.

    Without tabsCriteria, DeleteNamedRangeRequest applies to every tab.
    """
    request = {"namedRangeId": named_id}
    if tab_id:
        request["tabsCriteria"] = {"tabIds": [tab_id]}
    return {"deleteNamedRange": request}


def _owned_range_requests(tab, tab_id, parts, *, keep_after=True,
                          split_mark=False):
    """Rebuild gdoc-owned ranges that replaced body parts intersect.

    Each part is ``(start, end, inserted_length, stays_inside)`` in original
    body indexes. A range a part touches is deleted and recreated over its
    unreplaced portions in final indexes; text that ``stays_inside`` (inline
    wording within a code line or quote) remains part of the range. With
    *keep_after* False, a range's portion after an insertion point is dropped
    (an appended tab keeps the retained final mark for the new content).
    With *split_mark*, the first inserted character is the newline that
    split the old last paragraph and now ends it, so that paragraph's
    ranges keep it, even when the paragraph has no text of its own.
    Returns ``(deletions, creations)`` for one revision-pinned batch.
    """
    # A part may carry its physical deletion span as a fifth item when it
    # differs from the logical one (a borrowed paragraph mark): Docs clips
    # ranges by the physical span, pieces are rebuilt from the logical one.
    physical = [p[4] if len(p) > 4 else (p[0], p[1]) for p in parts]
    parts = sorted(p[:4] for p in parts)
    deletions, creations = [], []
    for named_id, name, spans in _owned_named_ranges(tab, tab_id):
        def overlaps(part, a, b):
            s, e = part[0], part[1]
            return (s < b and e > a) or (s == e and a <= s < b)

        if not any(overlaps(p, a, b) for p in parts + physical for a, b in spans):
            continue
        deletions.append(_delete_owned_range(named_id, tab_id))
        for a, b in spans:
            shift = sum(length - (e - s) for s, e, length, _ in parts
                        if not overlaps((s, e), a, b) and e <= a)
            cur = a + shift
            pieces = []
            for s, e, length, inside in (p for p in parts if overlaps(p, a, b)):
                start = s + shift
                shift += length - (e - s)
                if inside and a <= s and e <= b:
                    continue
                mark = int(split_mark and not keep_after and a < s + 1 <= b)
                if start + mark > cur:
                    pieces.append((cur, start + mark))
                cur = start + length
            if keep_after and b + shift > cur:
                pieces.append((cur, b + shift))
            for lo, hi in pieces:
                span = {"startIndex": lo, "endIndex": hi}
                if tab_id:
                    span["tabId"] = tab_id
                creations.append({"createNamedRange": {"name": name, "range": span}})
    return deletions, creations


def insert_markdown_into_tab(
    doc_id: str,
    tab_name: str,
    markdown: str,
    position: str = "start",
    replace: bool = False,
    allow_lossy: bool = False,
    *, document: dict | None = None, image_aliases: dict | None = None,
) -> dict:
    """Insert (or replace) markdown content in a tab via Docs API.

    Bypasses Drive's markdown importer so multi-tab docs are never
    collapsed. Reused by both `gdoc insert` and `gdoc write --tab`.

    Args:
        doc_id: Document ID.
        tab_name: Tab title (case-insensitive) or tab ID.
        markdown: Markdown source to insert (frontmatter should be
            stripped by the caller).
        position: "start" or "end". Ignored when replace=True.
        replace: If True, delete the tab body first then insert at the
            body start.
        allow_lossy: Explicitly permit named native-content losses.
        document: Optional guard-read snapshot; its revision pins the first
            batch so a later read cannot silently adopt a collaborator edit.

    Returns:
        Dict with "tab_id", "tab_title", "insert_index".
    """
    from gdoc.mdparse import (
        _paragraph_style_fields,
        parse_markdown,
        utf16_len,
    )

    doc = document if document is not None else get_document_with_tabs(doc_id)
    revision_id = doc.get("revisionId", "")
    input_revision_id = revision_id
    tabs = flatten_tabs(doc.get("tabs", []))
    tab_match = resolve_tab(tabs, tab_name)
    tab_id = tab_match["id"]
    body = tab_match["body"]

    # Section breaks inside the body need loss consent (checked below). The
    # body deletion then covers each break with the newline before it, which
    # Docs deletes together, so the tab becomes one section.
    body_start, body_end = _tab_body_range(body)

    if replace:
        insert_index = body_start
    elif position == "end":
        insert_index = body_end
    else:
        insert_index = body_start
    original_insert_index = insert_index

    parsed = parse_markdown(markdown)
    if parsed.non_default_list_starts:
        import sys

        print("WARN: Google Docs cannot set arbitrary native list starts; "
              "the following lists will start at 1: "
              + "; ".join(parsed.non_default_list_starts), file=sys.stderr)
    if parsed.deep_list_items:
        import sys

        print("WARN: a Google Docs list has at most nine nesting levels; deeper "
              "items are written at the ninth: "
              + "; ".join(parsed.deep_list_items), file=sys.stderr)
    requests: list[dict] = []

    # A tab whose only paragraph is a rule, an empty heading or an empty
    # list item still holds content: insertion splits it like any paragraph.
    occupied = body_end > body_start or (
        not replace and _structured_empty_paragraph(body, tab_match, tab_id))
    at_end = replace or not occupied or position == "end"
    if at_end:
        _strip_trailing_newline_unless_hr(parsed)
    # Trimming an empty final paragraph leaves its annotation at zero width.
    # Its style belongs on the retained native mark, not on extra inserted text.
    final_style = next((s.style for s in parsed.styles
                        if s.type == "paragraph_style"
                        and s.start == s.end == len(parsed.plain_text)), None)
    # A leading table needs no separator: InsertTableRequest adds its own
    # newline before the table, so the placeholder newline becomes the
    # existing paragraph's mark. Anything else that starts with a newline (a
    # thematic break, a deliberate blank line) is a new paragraph of its own
    # and keeps the separator so its mark never lands on existing text.
    appending = not replace and occupied and position == "end"
    leading_table = (
        appending and bool(parsed.tables) and parsed.tables[0].plain_text_offset == 0
    )
    # An appended leading table splits the tab's last paragraph at its mark.
    # When that paragraph is empty and one of gdoc's ranges holds it (an
    # empty code line, a rule in a quote), the range has no text to keep
    # during the text batch, so it is restored over its original spans once
    # the table's newline has become that paragraph's mark.
    restored_ranges = []
    if leading_table:
        last_elements = next((e["paragraph"].get("elements", [])
                              for e in reversed(body.get("content", []))
                              if "paragraph" in e), [])
        last_start = last_elements[0].get("startIndex", 0) if last_elements else 0
        if last_start == body_end:
            restored_ranges = [
                (name, spans)
                for _, name, spans in _owned_named_ranges(tab_match, tab_id)
                if any(a <= last_start < b for a, b in spans)]
    if leading_table:
        # The placeholder newline ends the existing paragraph, whose style
        # and list membership must stay untouched.
        parsed.styles = [s for s in parsed.styles
                         if not (s.type == "paragraph_style"
                                 and s.start == 0 and s.end <= 1)]
        if not parsed.plain_text:
            final_style = None
    if (not replace and occupied and not leading_table
            and (parsed.plain_text or parsed.tables or final_style is not None)):
        if position == "end":
            # The mandatory final newline belongs to the existing paragraph.
            # Split first, then insert and style only the new paragraph.
            requests.append({"insertText": {
                "location": {"index": insert_index, "tabId": tab_id},
                "text": "\n",
            }})
            insert_index += 1
        # At start, the parser's final newline separates the inserted text
        # from the existing first paragraph; do not strip it.

    owned_deletions = []
    if replace:
        # The replaced body's code and container markers would otherwise
        # survive, shrunk onto the retained final mark.
        owned_deletions = [_delete_owned_range(named_id, tab_id)
                           for named_id, _, _ in _owned_named_ranges(tab_match, tab_id)]
    if replace and body_end > body_start:
        delete_range = {
            "startIndex": body_start,
            "endIndex": body_end,
            "tabId": tab_id,
        }
        requests.append({"deleteContentRange": {"range": delete_range}})

    if replace:
        check_tab_body_replacement(tab_match, allow_lossy=allow_lossy)
        # Deletion retains the native final paragraph. Reset that paragraph
        # before insertion so its bullets, heading, alignment and spacing do
        # not become the initial state of every new paragraph.
        retained = {"startIndex": body_start, "endIndex": body_start + 1,
                    "tabId": tab_id}
        requests.extend([
            {"deleteParagraphBullets": {"range": retained}},
            {"updateParagraphStyle": {
                "range": retained,
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "*",
            }},
            {"updateTextStyle": {
                "range": retained, "textStyle": {}, "fields": "*",
            }},
        ])

    # Only inherited bullets require indent resets. Ordinary headings and
    # paragraphs must not materialize new zero-valued indent overrides.
    # A replacement keeps the final paragraph mark, so the inserted text
    # inherits that paragraph's bullet, not the first paragraph's.
    boundary = body_end if replace or position == "end" else body_start
    inherited_bullet = any(
        paragraph.get("bullet") for paragraph, _, _ in _replacement_paragraphs(
            body.get("content", []),
            {"startIndex": boundary, "endIndex": boundary + 1},
        )
    )
    if not replace and inherited_bullet:
        _reset_list_indents(parsed)
    _prepare_image_sources(parsed, doc, image_aliases)
    insertion = _native_docs_requests(parsed, insert_index, tab_id=tab_id)
    # A table placed after the rule is the tab's last block instead.
    table_last = any(t.plain_text_offset == len(parsed.plain_text)
                     for t in parsed.tables)
    # An empty final paragraph after the rule (such as an empty code line)
    # owns the mandatory final newline itself, so the rule keeps its mark.
    if at_end and not table_last and final_style is None \
            and parsed.plain_text.endswith("\n") and any(
        s.type == "paragraph_style" and s.end == len(parsed.plain_text)
        and "borderBottom" in s.style for s in parsed.styles
    ):
        # A trailing thematic break kept the parser's newline so its border
        # has a range. The mandatory final newline already follows the
        # insertion point and serves as that paragraph's mark, so insert one
        # character less; the style range already lands on it.
        # Trim the sent text, which shields literal list tabs from bullet
        # creation, rather than resending the raw parsed text.
        text = insertion[0]["insertText"]["text"]
        if text[:-1]:
            insertion[0]["insertText"]["text"] = text[:-1]
        else:
            del insertion[0]
    boundary_style = next((paragraph.get("paragraphStyle", {})
                           for paragraph, _, _ in _replacement_paragraphs(
                               body.get("content", []),
                               {"startIndex": boundary, "endIndex": boundary + 1})),
                          {})
    inherited_fields = [f for f in _INSERT_INHERITED_FIELDS if boundary_style.get(f)]
    boundary_runs = [run.get("textRun", {}).get("textStyle")
                     for paragraph, _, _ in _replacement_paragraphs(
                         body.get("content", []),
                         {"startIndex": boundary, "endIndex": boundary + 1})
                     for run in paragraph.get("elements", [])]
    if (not replace and any(boundary_runs) and parsed.plain_text and insertion
            and "insertText" in insertion[0]):
        # Inserted text copies the text style beside it (bold, a link, code
        # font). Markdown text is plain unless it says otherwise.
        first = insert_index + (1 if leading_table else 0)
        last = insert_index + utf16_len(insertion[0]["insertText"]["text"])
        if last > first:
            insertion.insert(1, {"updateTextStyle": {
                "range": {"startIndex": first, "endIndex": last, "tabId": tab_id},
                "textStyle": {}, "fields": "*",
            }})
    if (not replace and inherited_fields and parsed.plain_text and insertion
            and "insertText" in insertion[0]):
        # New paragraphs split from a quote or rule copy its indent or
        # border, which would read back as a quote or a second rule. Clear
        # them before the parsed styles apply what the Markdown asks for.
        first = insert_index + (1 if leading_table else 0)
        # Appended content ends at the tab's retained final mark, which the
        # split copied from the old last paragraph: it is new content too.
        last = (insert_index + utf16_len(insertion[0]["insertText"]["text"])
                + (position == "end"))
        if last > first:
            insertion.insert(1, {"updateParagraphStyle": {
                "range": {"startIndex": first, "endIndex": last, "tabId": tab_id},
                "paragraphStyle": {}, "fields": ",".join(inherited_fields),
            }})
    if not replace and inherited_bullet and parsed.plain_text:
        # After a leading table placeholder the reset starts past the
        # newline that now ends the existing list item.
        # A trailing empty paragraph's style lands on the retained final
        # mark, which is then one of the new paragraphs and loses the bullet.
        insertion.insert(1, {"deleteParagraphBullets": {"range": {
            "startIndex": insert_index + (1 if leading_table else 0),
            "endIndex": insert_index + utf16_len(parsed.plain_text)
            + (final_style is not None and position == "end"),
            "tabId": tab_id,
        }}})
    if (appending and not leading_table
            and not (insertion and "insertText" in insertion[0])
            and insert_index > original_insert_index):
        # Appended Markdown that is one empty paragraph (an empty heading,
        # quote or code line, or a lone rule whose text was trimmed onto the
        # mark) is only the retained final mark, which the split copied from
        # the old last paragraph: clear what it copied.
        mark = {"startIndex": insert_index, "endIndex": insert_index + 1,
                "tabId": tab_id}
        cleared = [{"deleteParagraphBullets": {"range": dict(mark)}}]
        if inherited_fields:
            cleared.append({"updateParagraphStyle": {
                "range": dict(mark), "paragraphStyle": {},
                "fields": ",".join(inherited_fields),
            }})
        insertion[:0] = cleared
    if final_style is not None:
        final_index = (insert_index + utf16_len(parsed.plain_text)
                       - parsed.removed_tabs)
        insertion.append({"updateParagraphStyle": {
            "range": {"startIndex": final_index, "endIndex": final_index + 1,
                      "tabId": tab_id},
            "paragraphStyle": final_style,
            "fields": _paragraph_style_fields(final_style),
        }})
    insertion.extend(_code_range_requests(parsed, insert_index, tab_id))
    requests.extend(insertion)
    if not replace and (requests or parsed.tables):
        # Existing markers at the insertion point must not absorb new text.
        # List compilation also sends temporary tabs and separators that later
        # requests consume, so measure the text that remains: the parsed text
        # as inserted, less its nesting tabs, plus any end-of-body split.
        main = (insertion[0]["insertText"]["text"]
                if insertion and "insertText" in insertion[0] else "")
        inserted = (utf16_len(main) - (parsed.removed_tabs if main else 0)
                    + (insert_index - original_insert_index))
        owned_deletions, rebuilt = _owned_range_requests(
            tab_match, tab_id,
            [(original_insert_index, original_insert_index, inserted, False)],
            keep_after=position != "end",
            split_mark=insert_index > original_insert_index,
        )
        # A restored range replaces the pieces rebuilt from its own spans.
        requests.extend(
            request for request in rebuilt
            if not any(a <= request["createNamedRange"]["range"]["startIndex"] < b
                       for _, spans in restored_ranges for a, b in spans))
    requests[:0] = owned_deletions

    with _StagedWrite(doc_id) as progress:
        if requests:
            revision_id = progress.batch(
                "tab text and formatting applied", requests, revision_id,
            )
        if parsed.tables:
            for ordinal, table in reversed(list(enumerate(parsed.tables, 1))):
                # Earlier list-indent tabs have already been consumed.
                revision_id = _insert_table(
                    doc_id,
                    insert_index
                    + utf16_len(parsed.plain_text[:table.plain_text_offset])
                    - table.removed_tabs_before,
                    table, tab_id=tab_id, revision_id=revision_id, progress=progress,
                    resolve_index=_table_position_resolver(parsed, table, tab_id),
                    ordinal=ordinal, scaffolding=_table_scaffolding(parsed, table),
                )
        if restored_ranges:
            revision_id = progress.batch(
                "code and container ranges restored",
                [{"createNamedRange": {"name": name, "range": {
                    "startIndex": a, "endIndex": b, "tabId": tab_id}}}
                 for name, spans in restored_ranges for a, b in spans],
                revision_id,
            )

    return {
        "tab_id": tab_id,
        "tab_title": tab_match["title"],
        "insert_index": insert_index,
        "input_revision_id": input_revision_id,
        "acknowledged_revision_id": revision_id,
        "rebased": progress.rebased,
        "image_reference_ids": {
            # Copies of one image share its content and size; any copy
            # stands in for the original reference in later writes.
            original: progress.inserted_images[key][0]
            for original, key in parsed.image_reference_sources.items()
            if progress.inserted_images.get(key)
        },
    }


# Paragraph properties the exporter reads as Markdown structure (a quote's
# indent, a rule's border) that newly inserted paragraphs must not inherit.
_INSERT_INHERITED_FIELDS = ("indentStart", "indentFirstLine", "borderBottom")


def check_tab_body_replacement(tab: dict, *, allow_lossy: bool = False) -> None:
    """Refuse native losses within one tab body before it is replaced.

    ``tab`` is a flattened tab (``body`` and ``lists`` keys). Lists live
    beside the body; only definitions used by its paragraphs (including
    cells) are inspected, not header/footer-only lists.
    """
    from gdoc.lossy import check_markdown_replacement

    check_markdown_replacement(_tab_replacement_scope(tab), tab_body=True,
                               allow_lossy=allow_lossy)


# Not content a Markdown read omits: suggestions have their own read notes.
# Custom named ranges are named: Markdown does not show them and a rewrite
# needs consent to remove them. Images read as references; the "embedded
# objects" hazard covers only objects without image properties (drawings
# and the like), which a reference cannot recreate.
_NOT_READ_OMISSIONS = {"pending suggestions"}


def markdown_read_omissions(tab: dict) -> list[str]:
    """Native content in a tab body, including table cells, that its Markdown
    read leaves out or flattens; the same content a rewrite needs consent to
    discard."""
    from gdoc.lossy import markdown_hazards

    hazards, _, _ = markdown_hazards(_tab_replacement_scope(tab), tab_body=True)
    return sorted(hazards - _NOT_READ_OMISSIONS)


def _tab_replacement_scope(tab: dict) -> dict:
    body = tab.get("body", {})
    body_start, body_end = _tab_body_range(body)
    list_ids = {
        paragraph.get("bullet", {}).get("listId")
        for paragraph, _, _ in _replacement_paragraphs(
            body.get("content", []),
            {"startIndex": body_start, "endIndex": body_end + 1},
        )
    }
    return {**{key: tab[key] for key in ("inlineObjects", "namedRanges")
               if key in tab},
            "body": body, "lists": {
        key: value for key, value in tab.get("lists", {}).items()
        if key in list_ids
    }}


def _replacement_paragraphs(content: list[dict], match: dict):
    """Yield intersected native paragraphs, including paragraphs in cells."""
    for element in content:
        paragraph = element.get("paragraph")
        if paragraph:
            elements = paragraph.get("elements", [])
            if elements:
                start = elements[0].get("startIndex", element.get("startIndex", 0))
                end = elements[-1].get("endIndex", element.get("endIndex", start))
                if (start < match["endIndex"] or start == match["startIndex"]) \
                        and match["startIndex"] < end:
                    yield paragraph, start, end - 1
        for row in element.get("table", {}).get("tableRows", []):
            for cell in row.get("tableCells", []):
                yield from _replacement_paragraphs(cell.get("content", []), match)


def _covers_whole_paragraphs(content: list[dict], match: dict) -> bool:
    """True when the match spans complete native paragraphs, marks excluded."""
    native = list(_replacement_paragraphs(content, match))
    return bool(native) and (
        match["startIndex"] == native[0][1]
        and match["endIndex"] == native[-1][2]
    )


def _replacement_paragraph(content: list[dict], match: dict):
    """Find the paragraph containing the match without its paragraph mark."""
    for paragraph, start, end in _replacement_paragraphs(content, match):
        if start <= match["startIndex"] and match["endIndex"] <= end:
            return paragraph, start, end
    return None


def _paragraph_wording_matches(body: dict, match: dict, markdown: str):
    """Split a wording edit at native paragraph marks, which stay untouched.

    Retaining each mark preserves named styles, list IDs, nesting and custom
    paragraph properties without trying to reconstruct them from Markdown.
    """
    paragraphs = list(_replacement_paragraphs(body.get("content", []), match))
    if not paragraphs:
        return [(match, markdown)]
    # The matched terminal LF and its replacement denote the retained native
    # mark, not a new empty paragraph. Interior blank paragraphs still count.
    if match["endIndex"] == paragraphs[-1][2] + 1:
        markdown = markdown.removesuffix("\n")
    lines = markdown.split("\n") if markdown else [""] * len(paragraphs)
    if len(lines) != len(paragraphs):
        raise GdocError(
            f"paragraph count mismatch: matched {len(paragraphs)}, "
            f"replacement has {len(lines)}; edit each paragraph separately "
            "or use write --tab for structural body changes "
            "(--cell replaces an entire table cell)", exit_code=3,
        )
    result = []
    for (_, start, end), line in zip(paragraphs, lines):
        result.append(({**match, "startIndex": max(start, match["startIndex"]),
                        "endIndex": min(end, match["endIndex"])}, line))
    return result


def _wording_contexts(body: dict, match: dict, markdown: str):
    """Share contextual parsing between edits and suggestions, including fences."""
    from gdoc.mdparse import (
        _FENCE_RE,
        ParsedMarkdown,
        StyleRange,
        parse_markdown,
    )

    def _opens_block_fence(line: str) -> bool:
        # A backtick fence's info string cannot contain a backtick
        # (CommonMark 4.5), so a closed span such as ```code``` is inline.
        fence = _FENCE_RE.match(line)
        return bool(fence) and not (
            fence.group(1).startswith("`") and "`" in fence.group(2)
        )

    # Only complete paragraphs can become code lines (a match may include the
    # last paragraph's newline, clipped below); inside a paragraph the fence
    # lines are literal text like every other block marker.
    native = list(_replacement_paragraphs(body.get("content", []), match))
    whole = bool(native) and match["startIndex"] == native[0][1] and (
        match["endIndex"] in (native[-1][2], native[-1][2] + 1)
    )
    if "\n" in markdown and whole and any(
        _opens_block_fence(line) for line in markdown.split("\n")
    ):
        parsed = parse_markdown(markdown)
        check_inline_only_markdown(parsed)
        # Split rendered code, never its source lines: fence delimiters are
        # syntax, and asterisks/links inside the fence are literal code.
        text = parsed.plain_text.removesuffix("\n")
        rendered_count = sum(s.type == "paragraph_style" for s in parsed.styles)
        if native and rendered_count != len(native):
            raise GdocError(
                f"paragraph count mismatch: matched {len(native)}, "
                f"fenced replacement renders {rendered_count}; "
                "edit each paragraph separately", exit_code=3,
            )
        # The parser already removed its generated terminal mark. Clip the
        # old range as well so a real final empty code line is not stripped.
        clipped = ({**match, "endIndex": min(match["endIndex"], native[-1][2])}
                   if native else match)
        parts = _paragraph_wording_matches(body, clipped, text)
        offset = 0
        result = []
        group = object()
        for part, line in parts:
            end = offset + len(line)
            selected = ParsedMarkdown(line, [
                StyleRange(max(s.start, offset) - offset,
                           min(s.end, end) - offset, s.style, s.type)
                for s in parsed.styles if s.type == "text_style"
                and s.start < end and s.end > offset
            ], code_group=group if parsed.code_blocks else None)
            found = _replacement_paragraph(body.get("content", []), part)
            baseline = (_inline_baseline(found[0], part, line, selected.styles)
                        if found else [])
            result.append((part, (selected, baseline)))
            offset = end + 1
        return result
    result = []
    for part, line in _paragraph_wording_matches(body, match, markdown):
        selected = parse_markdown(line)
        _strip_trailing_newline_unless_hr(selected)
        result.append((part, _contextual_replacement(selected, line, part, body)))
    return result


def _table_between_blanks(content, match, parsed):
    """Plan a table-only replacement so the table reuses blank separators (I5).

    ``write`` of ``A / blank / table / blank / B`` keeps one empty paragraph
    on each side of the table: Docs' mandatory paragraph before a table and
    the separator after it. insertTable always adds one paragraph mark, so
    replacing whole paragraphs that sit between two blank paragraphs removes
    the paragraphs and the blank after them with their own marks, and
    inserts the table at the blank before them (tableBack). That blank splits
    into the mandatory paragraph and the separator, both keeping its style.
    Any other shape returns None and keeps the general plan.
    """
    if (len(parsed.tables) != 1 or parsed.plain_text.strip("\n")
            or any(s.type != "paragraph_style" for s in parsed.styles)):
        return None
    paragraphs = [e for e in content if "paragraph" in e or "table" in e]
    end = max(match["endIndex"], match["startIndex"] + 1)
    covered = [i for i, e in enumerate(paragraphs) if "paragraph" in e
               and e.get("startIndex", 0) < end
               and e["endIndex"] > match["startIndex"]]
    if not covered:
        return None
    first, last = covered[0], covered[-1]
    if first < 1 or last + 2 >= len(paragraphs):
        return None
    before, after = paragraphs[first - 1], paragraphs[last + 1]
    following = paragraphs[last + 2]
    if (paragraphs[first].get("startIndex", 0) != match["startIndex"]
            or not all(_is_empty_paragraph(e) for e in (before, after))
            or any(e["paragraph"].get("bullet") for e in (before, after))
            or "table" in following):
        return None
    return {**match, "endIndex": after["endIndex"], "tableBack": 1}


def _contained_parse(parsed, tab, tab_id, match):
    """Write whole-paragraph Markdown inside the container it replaces (I3).

    Paragraphs replacing the content of one quote or list item stay in that
    container, as ``write`` of the indented Markdown would place them: each
    non-blank top-level paragraph and each table gets the container's path.
    Blank separators start clean (I4): they would otherwise keep the
    container's indent from the split mark and read back as quotes.
    """
    import copy

    from gdoc.mdparse import StyleRange, legacy_prefix

    if (any(s.type in ("markdown_prefix", "bullets") for s in parsed.styles)
            or parsed.code_blocks or any(t.path for t in parsed.tables)):
        return parsed
    paths = {path for _, name, spans in _owned_named_ranges(tab, tab_id)
             if (path := _parse_prefix_range_name(name))
             and any(a <= match["startIndex"] and match["endIndex"] < b
                     for a, b in spans)}
    if len(paths) != 1:
        return parsed
    (path,), contained = paths, copy.deepcopy(parsed)
    legacy = legacy_prefix(path)
    marker = ({"quote": legacy[0], "indent": legacy[1]} if legacy else {})
    zero = {"magnitude": 0, "unit": "PT"}
    for style in list(contained.styles):
        if style.type != "paragraph_style" or style.path:
            continue
        if contained.plain_text[style.start:style.end].strip("\n"):
            contained.styles.append(StyleRange(
                style.start, style.end, dict(marker), "markdown_prefix",
                path=path))
        else:
            style.style = {**style.style, "indentStart": zero,
                           "indentFirstLine": zero}
    for table in contained.tables:
        table.path = path
    return contained


def _removes_whole_paragraphs(content: list[dict], match: dict) -> bool:
    """True when an empty replacement of the match removes its paragraphs.

    The match starts at its first paragraph's first index and reaches the
    last paragraph's text end. A paragraph anchoring a positioned object
    keeps its mark, so only its wording can go.
    """
    paragraphs = list(_replacement_paragraphs(content, match))
    return bool(paragraphs) and (
        match["startIndex"] == paragraphs[0][1]
        and match["endIndex"] >= paragraphs[-1][2]
        and not any(p.get("positionedObjectIds") for p, _, _ in paragraphs)
    )


def _paragraph_siblings(content: list[dict], start: int):
    """The element list holding the paragraph that starts at ``start``, and
    its position there (a tab body, or a table cell's content)."""
    for i, element in enumerate(content):
        if "paragraph" in element and element.get("startIndex", 0) == start:
            return content, i
        for row in element.get("table", {}).get("tableRows", []):
            for cell in row.get("tableCells", []):
                found = _paragraph_siblings(cell.get("content", []), start)
                if found:
                    return found
    return None


def _plan_paragraph_removals(source, removals: list[dict]) -> list[dict]:
    """Plan removing whole paragraphs, once per run of consecutive ones.

    ``removals`` are matches that each remove whole paragraphs. They may be
    adjacent or overlap (``--all``, or several paragraphs of one match), so
    their paragraphs are merged into maximal runs of consecutive paragraphs
    in one element list, and each run is planned once from its paragraphs.
    Every character a plan deletes, beyond the matched text too, must carry
    no suggestion: a direct deletion silently settles one (observed live).
    """
    runs: dict = {}
    for match in removals:
        body = _replacement_body(source, match)
        for _, start, _ in _replacement_paragraphs(body.get("content", []), match):
            siblings, index = _paragraph_siblings(body["content"], start)
            key = (_match_space(match), id(siblings))
            runs.setdefault(key, (match, body, siblings, set()))[3].add(index)
    plans = []
    for match, body, siblings, indexes in runs.values():
        indexes = sorted(indexes)
        first = indexes[0]
        for previous, index in zip(indexes, indexes[1:] + [None]):
            if index is not None and index == previous + 1:
                continue
            plan = _plan_paragraph_run(siblings, first, previous, match)
            overlapping = find_suggestions_in_range(
                body, plan["startIndex"], plan["endIndex"])
            if overlapping:
                raise GdocError(
                    "removing these paragraphs would also delete text or a "
                    "paragraph break that carries suggestion(s) "
                    + ", ".join(sorted(overlapping)) + "; accept or reject "
                    "them in Docs first. Nothing was changed.", exit_code=3,
                )
            plans.append(plan)
            first = index
    return plans


def _plan_paragraph_run(siblings: list[dict], first: int, last: int,
                        match: dict) -> dict:
    """The deletion that removes paragraphs ``first..last`` of ``siblings``.

    Docs merges what a deletion leaves into the paragraph where it starts,
    keeping that paragraph's style and list, unless it starts at that
    paragraph's first index; then the paragraph where it ends keeps its own
    (observed live). Removing a run with its own marks therefore leaves the
    next paragraph untouched. A segment's last mark and the mark before a
    table cannot be deleted, so a run ending there borrows the previous
    paragraph K's mark instead: K's text then ends on the run's last mark and
    keeps its own style, except when K is empty, which the deletion removes
    whole; its style is then restored on the retained mark.
    """
    run = siblings[first:last + 1]
    start = run[0].get("startIndex", 0)
    text_end = run[-1]["endIndex"] - 1
    plan = {k: v for k, v in match.items()
            if k not in ("startIndex", "endIndex")}
    before_table = last + 1 < len(siblings) and "table" in siblings[last + 1]
    if last + 1 < len(siblings) and not before_table:
        # Empty the paragraphs first, then remove the one empty paragraph
        # left; either deletion starts at a paragraph's first index.
        return {**plan, "startIndex": start, "endIndex": text_end + 1,
                "emptiedMark": text_end}
    previous = siblings[first - 1] if first else None
    if previous is None or "table" in previous:
        if before_table:
            raise GdocError(
                "cannot remove the paragraph directly before a table "
                "when no paragraph precedes it; replace its wording, or "
                "rewrite the tab with write --tab", exit_code=3,
            )
        if previous is not None:
            raise GdocError(
                "cannot remove the mandatory final paragraph after a table; "
                "replace its wording instead", exit_code=3,
            )
        # The segment's only paragraphs: their wording goes, one mark stays.
        return {**plan, "startIndex": start, "endIndex": text_end}
    if previous["paragraph"].get("positionedObjectIds"):
        # Its mark must survive; the run's last paragraph stays, empty.
        return {**plan, "startIndex": start, "endIndex": text_end}
    borrowed = {**plan, "startIndex": previous["endIndex"] - 1,
                "endIndex": text_end, "removedSpan": (start, text_end + 1)}
    if previous["endIndex"] - previous.get("startIndex", 0) == 1:
        restore = _retained_mark_restore(previous["paragraph"], run[-1]["paragraph"])
        if restore:
            borrowed["retainedMarkRestore"] = restore
    return borrowed


def _joined_deletion(content: list[dict], match: dict) -> dict | None:
    """Delete wording across a paragraph break, joining the paragraphs.

    Matches are native text: ``lo\nwor`` matches across the paragraphs
    ``Hello`` and the heading ``world``, and deleting it leaves one
    paragraph, ``Helld``. The joined paragraph always keeps the first
    paragraph's style. Docs does that itself unless the deletion starts at the
    first paragraph's first index; only then is the style restored. A
    deletion of whole paragraphs is not a join and returns None.
    """
    paragraphs = list(_replacement_paragraphs(content, match))
    if len(paragraphs) < 2 or _removes_whole_paragraphs(content, match):
        return None
    first, last = paragraphs[0], paragraphs[-1]
    if any(p.get("positionedObjectIds") for p, _, _ in paragraphs[1:]):
        raise GdocError(
            "cannot join these paragraphs: a later one anchors a positioned "
            "object; delete each paragraph's wording separately", exit_code=3,
        )
    # Docs keeps the first paragraph's style and list when the deletion
    # starts inside it; one starting at its first index removes it whole,
    # and the last paragraph's style must be replaced.
    restore = None
    if match["startIndex"] == first[1]:
        restore = _retained_mark_restore(first[0], last[0])
    joined = {**match}
    if restore:
        joined["retainedMarkRestore"] = restore
    return joined


def _retained_mark_restore(kept: dict, removed: dict) -> dict | None:
    """Keep a paragraph whose text moves onto a removed paragraph's mark.

    Called only when the deletion starts at the kept paragraph's first
    index (an empty preceding paragraph, or a join that deletes all of the
    first paragraph's text). Docs then leaves the retained mark with the
    removed paragraph's style and list, observed live. The kept paragraph's
    style is restored and a bullet it lacks is removed. A list item's
    membership cannot be re-created reliably through the API, so a removal
    that would move it onto another list state is refused before any write.
    """
    kept_bullet, removed_bullet = kept.get("bullet"), removed.get("bullet")

    def identity(bullet):
        return bullet.get("listId"), bullet.get("nestingLevel", 0)

    if kept_bullet and (not removed_bullet
                        or identity(kept_bullet) != identity(removed_bullet)):
        raise GdocError(
            "cannot remove this paragraph without moving the list item before "
            "it off its list; replace its wording, or rewrite the tab with "
            "write --tab", exit_code=3,
        )
    style = kept.get("paragraphStyle", {})
    merged = removed.get("paragraphStyle", {})
    fields = [f for f in _RESTORED_PARAGRAPH_FIELDS
              if (f in style or f in merged) and style.get(f) != merged.get(f)]
    restore = {}
    if fields:
        restore["paragraphStyle"] = {f: style[f] for f in fields if f in style}
        restore["fields"] = ",".join(fields)
    if removed_bullet and not kept_bullet:
        restore["deleteBullets"] = True
    return restore or None


def _retained_mark_requests(match: dict, tab_id: str | None) -> list[dict]:
    """Requests restoring the paragraph that now ends at a retained mark."""
    restore = match.get("retainedMarkRestore")
    if not restore:
        return []
    span = {"startIndex": match["startIndex"], "endIndex": match["startIndex"] + 1}
    if tab_id:
        span["tabId"] = tab_id
    if match.get("segmentId"):
        span["segmentId"] = match["segmentId"]
    requests = []
    if "fields" in restore:
        requests.append({"updateParagraphStyle": {
            "range": span, "paragraphStyle": restore["paragraphStyle"],
            "fields": restore["fields"],
        }})
    if restore.get("deleteBullets"):
        requests.append({"deleteParagraphBullets": {"range": dict(span)}})
    return requests


def _replacement_text_style(runs: list[dict], match: dict, text: str,
                            explicit_links: bool = False):
    """Keep unique surviving runs, including plain runs, in source order.

    Unmatched gaps keep only styles common to their original runs. Ambiguous
    mixed rewrites keep common fields, never a guessed dominant style. An edit
    inside one link's label keeps the whole replacement in that link;
    otherwise the linked wording the match covers must reappear uniquely, at
    word boundaries, for its link to follow it.
    Offsets returned here are Python offsets into the replacement text.
    """
    start, end = match["startIndex"], match["endIndex"]
    targets = []
    links = []
    for run in runs:
        offset = run.get("startIndex", 0)
        source = run["textRun"]
        style = source.get("textStyle", {})
        link = style.get("link")
        if link:
            if links and links[-1][1] == offset and links[-1][3] == link:
                lo, _, label, _ = links[-1]
                links[-1] = (lo, run["endIndex"], label + source["content"], link)
            else:
                links.append((offset, run["endIndex"], source["content"], link))
        lo, hi = max(start, offset), min(end, run["endIndex"])
        if lo >= hi:
            continue
        raw = source["content"].encode("utf-16-le")
        label = raw[(lo - offset) * 2:(hi - offset) * 2].decode("utf-16-le")
        # Linked wording carries the link's colour and underline; they go
        # with the link if it does not follow the replacement.
        style = {k: v for k, v in style.items() if k != "link"}
        if link:
            style["_linked"] = True
        if targets and targets[-1][1] == style:
            targets[-1] = (targets[-1][0] + label, style)
        else:
            targets.append((label, style))
    if not targets or not text:
        return []

    def common(selected):
        return {k: v for k, v in selected[0][1].items()
                if all(style.get(k) == v for _, style in selected)} if selected else {}

    mapped = []
    for index, (label, style) in enumerate(targets):
        pos = text.find(label)
        if label and pos >= 0 and text.find(label, pos + 1) < 0:
            mapped.append((index, pos, pos + len(label), style))
    if any(left[2] > right[1] for left, right in zip(mapped, mapped[1:])):
        mapped = []
    result = []
    offset, source_index = 0, 0
    for index, lo, hi, style in mapped:
        if offset < lo:
            result.append((offset, lo, common(targets[source_index:index] or targets)))
        result.append((lo, hi, style))
        offset, source_index = hi, index + 1
    if offset < len(text):
        result.append((offset, len(text), common(targets[source_index:] or targets)))

    for lo, hi, label, link in links:
        # A link that also covers the paragraph mark ends at its visible text.
        while label.endswith("\n"):
            label, hi = label[:-1], hi - 1
        if not label.strip() or hi <= start or lo >= end:
            continue
        if (lo <= start and end <= hi and (lo, hi) != (start, end)
                and not explicit_links):
            # An edit of part of one link's label is a label edit: all of
            # the replacement stays in that link.
            pos, stop = 0, len(text)
        else:
            # Otherwise the linked wording the match covers must survive
            # uniquely, as whole words, for its link to follow it.
            raw = label.encode("utf-16-le")
            part = raw[(max(lo, start) - lo) * 2:(min(hi, end) - lo) * 2].decode(
                "utf-16-le")
            pos = text.find(part)
            stop = pos + len(part)
            if (not part.strip() or pos < 0 or text.find(part, pos + 1) >= 0
                    or (pos and part[0].isalnum() and text[pos - 1].isalnum())
                    or (stop < len(text) and part[-1].isalnum()
                        and text[stop].isalnum())):
                continue
        # Link spans may cross non-link formatting boundaries.
        split = []
        for a, b, style in result:
            boundaries = sorted({a, b, max(a, min(b, pos)), max(a, min(b, stop))})
            for left, right in zip(boundaries, boundaries[1:]):
                split.append((left, right, {**style, **(
                    {"link": link} if pos <= left < stop else {})}))
        result = split
    unlinked = []
    for lo, hi, style in result:
        style = dict(style)
        if style.pop("_linked", False) and "link" not in style:
            style = _without_link_appearance(style)
        unlinked.append((lo, hi, style))
    return unlinked


# Docs gives a link it sets this colour and an underline (observed in a live
# readback); custom colours on linked text are the author's own styling.
_LINK_BLUE = (0.06666667, 0.33333334, 0.8)


def _without_link_appearance(style: dict) -> dict:
    """Drop Docs' default link colour and underline from unlinked wording."""
    rgb = style.get("foregroundColor", {}).get("color", {}).get("rgbColor", {})
    blue = all(abs(rgb.get(key, 0) - value) < 0.002
               for key, value in zip(("red", "green", "blue"), _LINK_BLUE))
    if not (blue and style.get("underline")):
        return style
    return {k: v for k, v in style.items()
            if k not in ("foregroundColor", "underline")}


def _inline_baseline(paragraph: dict, match: dict, text: str,
                     styles=()) -> list[dict]:
    """Restore target styles over inserted text, using masks for absent fields.

    Keep the complete desired style for restoring link decorations after
    explicit Markdown; only differing fields need an initial style request.
    ``styles`` are the replacement's parsed styles: a replacement that sets
    its own links never inherits the old one as a label edit.
    """
    from gdoc.mdparse import utf16_len

    start, end = match["startIndex"], match["endIndex"]
    runs = [el for el in paragraph.get("elements", []) if "textRun" in el]
    if start == end:
        return []
    following = next((el["textRun"].get("textStyle", {}) for el in runs
                      if el.get("endIndex", 0) > end), {})
    neighbour = next(
        (el["textRun"].get("textStyle", {}) for el in runs
         if el.get("startIndex", 0) < start <= el.get("endIndex", 0)), following,
    )
    old_link = any("link" in el["textRun"].get("textStyle", {}) for el in runs
                   if el.get("startIndex", 0) < end and el["endIndex"] > start)
    result = []
    explicit_links = any(s.type == "text_style" and "link" in s.style
                         for s in styles)
    for lo, hi, target in _replacement_text_style(runs, match, text,
                                                  explicit_links):
        fields = {key for key in target.keys() | neighbour.keys()
                  if target.get(key) != neighbour.get(key)}
        if old_link or "link" in target:
            fields.add("link")
        if "link" in target:
            # Setting links resets colour/underline unless included together.
            fields.update(key for key in ("foregroundColor", "underline")
                          if key in target)
        result.append({
            "range": {"startIndex": start + utf16_len(text[:lo]),
                      "endIndex": start + utf16_len(text[:hi])},
            "textStyle": dict(target), "fields": ",".join(sorted(fields)),
        })
    # A full-paragraph style request can implicitly include its retained LF.
    # Keep its direct style separately so the builder restores it last.
    if (runs and start == runs[0].get("startIndex", 0)
            and end == runs[-1]["endIndex"] - 1):
        mark = start + utf16_len(text)
        result.append({"range": {"startIndex": mark, "endIndex": mark + 1},
                       "textStyle": dict(runs[-1]["textRun"].get("textStyle", {})),
                       "fields": "", "retainedMark": True})
    return result


def _is_code_style(style: dict) -> bool:
    return style.get("weightedFontFamily", {}).get("fontFamily") in _MONOSPACE_FONTS


def _code_context(paragraph: dict, match: dict) -> str | None:
    """"line" when a match lies in an all-code paragraph, "span" in inline code.

    Every text run the match touches must be monospace; a match that also
    covers prose is ordinary Markdown.
    """
    start, end = match["startIndex"], match["endIndex"]
    runs = [el for el in paragraph.get("elements", []) if "textRun" in el
            and el["textRun"].get("content", "") not in ("", "\n")]
    touched = [el for el in runs
               if el.get("startIndex", 0) < end and el.get("endIndex", 0) > start]
    if not touched or not all(
            _is_code_style(el["textRun"].get("textStyle", {})) for el in touched):
        return None
    return "line" if all(_is_code_style(el["textRun"].get("textStyle", {}))
                         for el in runs) else "span"


def _contextual_replacement(parsed, markdown: str, match: dict, body: dict):
    """Plan a paragraph edit without replacing its native paragraph mark.

    A partial-paragraph replacement is inline Markdown only (see
    ``parse_inline``): code spans follow CommonMark backtick-string matching
    and every block-level construct is literal text, because a partial
    replacement cannot start a block.
    """
    from gdoc.mdparse import ParsedMarkdown, parse_inline

    found = _replacement_paragraph(body.get("content", []), match)
    if not found:
        return parsed, None
    paragraph, start, end = found
    code = _code_context(paragraph, match)
    if code:
        # Code is literal: asterisks, links and fences typed into a code line
        # or span stay characters. Only inside prose's inline code may the
        # replacement be written as one code span, whose content is used.
        text, styles = markdown, []
        if code == "span":
            spanned, spans = parse_inline(markdown)
            if (len(spans) == 1 and spans[0].type == "text_style"
                    and _is_code_style(spans[0].style)
                    and (spans[0].start, spans[0].end) == (0, len(spanned))):
                text = spanned
        return (ParsedMarkdown(plain_text=text, styles=styles),
                _inline_baseline(paragraph, match, text, styles))
    text, styles = parse_inline(markdown)
    explicit_paragraph = any(
        s.type == "bullets" or (s.type == "paragraph_style"
                               and s.style != {"namedStyleType": "NORMAL_TEXT"})
        for s in parsed.styles
    )
    whole = match["startIndex"] == start and match["endIndex"] == end
    # A complete paragraph can change its own style explicitly without
    # deleting its native mark or entering the block/cleanup path.
    if (whole and explicit_paragraph and "\n" not in markdown
            and not parsed.tables and not match.get("segmentId")):
        return parsed, []
    return (ParsedMarkdown(plain_text=text, styles=styles),
            _inline_baseline(paragraph, match, text, styles))


def _match_space(match: dict) -> tuple[str, str]:
    """Identity of an independent Docs index space (empty segment = body)."""
    return match.get("tabId", ""), match.get("segmentId", "")


def _match_key(match: dict) -> tuple:
    return (*_match_space(match), match["startIndex"])


def _replacement_body(scope: dict | None, match: dict) -> dict | None:
    """Resolve paragraph/style context in the match's own index space."""
    if scope is None or "content" in scope:
        return scope
    for content, coordinates in _search_containers(scope):
        if _match_space(coordinates) == _match_space(match):
            return content
    raise GdocError("replacement container not found in source snapshot", exit_code=3)


def _replacement_order(match: dict) -> tuple:
    tab, segment = _match_space(match)
    kind_order = {"body": 0, "header": 1, "footer": 2, "footnote": 3}
    kind = kind_order.get(match.get("container", "body"), 4)
    return tab, bool(segment), kind, segment, -match["startIndex"]


def check_segment_replacement(parsed, markdown: str, matches: list[dict]) -> None:
    """Segments interpret one source line as inline Markdown, including #/- text.

    A header/footer/footnote replacement cannot introduce body blocks. Block
    punctuation in ordinary wording is literal, so validate paragraph breaks
    and actual inline objects rather than the body parser's paragraph style.
    """
    if not any(m.get("segmentId") for m in matches):
        return
    from gdoc.mdparse import parse_inline

    source = markdown.removesuffix("\n")
    if markdown == "\n" or "\n" in source or "\r" in source:
        raise GdocError(
            "headers, footers, and footnotes support only plain or inline "
            "Markdown replacements", exit_code=3,
        )
    text, styles = parse_inline(source)
    if any(style.type == "image" for style in styles):
        raise GdocError(
            "inline images in header/footer/footnote edits are not supported",
            exit_code=3,
        )
    if all(m.get("segmentId") for m in matches):
        parsed.plain_text, parsed.styles = text, styles
        parsed.code_blocks, parsed.tables, parsed.images = [], [], []



def _build_replacement_requests(
    parsed, matches: list[dict], tab_id: str | None = None,
    *, contexts: dict | None = None, reset_bullets: set[tuple] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build the delete+insert requests for a find/replace, last-to-first.

    Pure function shared by ``replace_formatted`` (EDIT) and
    ``suggest_replacement`` (SUGGEST). Matches are processed in descending
    startIndex order within each independent segment, with deterministic
    body/header/footer/footnote ordering between containers.

    Returns (sorted_matches, requests).
    """
    sorted_matches = sorted(matches, key=_replacement_order)
    all_requests: list[dict] = []
    for match in sorted_matches:
        match_tab = match.get("tabId", tab_id)
        segment_id = match.get("segmentId")
        # Delete the matched range (skip empty ranges — Docs API rejects
        # them with "The range should not be empty", and a zero-width
        # match is a pure insert).
        if match["endIndex"] > match["startIndex"]:
            delete_range = {
                "startIndex": match["startIndex"],
                "endIndex": match["endIndex"],
            }
            if match_tab:
                delete_range["tabId"] = match_tab
            if segment_id:
                delete_range["segmentId"] = segment_id
            emptied = match.get("emptiedMark")
            if emptied is not None and emptied > match["startIndex"]:
                all_requests.append({"deleteContentRange": {
                    "range": {**delete_range, "endIndex": emptied}}})
                delete_range = {**delete_range,
                                "endIndex": match["startIndex"] + 1}
            all_requests.append({
                "deleteContentRange": {"range": delete_range}
            })
            all_requests.extend(_retained_mark_requests(match, match_tab))
        selected, baseline = (contexts[_match_key(match)] if contexts is not None
                              else (parsed, None))
        requests = _native_docs_requests(
            _inline_only(selected) if segment_id else selected,
            match["startIndex"], tab_id=match_tab,
        )
        if baseline is not None and selected.plain_text == "\n" and any(
            s.type == "paragraph_style" and "borderBottom" in s.style
            for s in selected.styles
        ):
            # The HR styles the retained native LF; inserting its renderer's
            # placeholder would create a second paragraph.
            requests = [r for r in requests if "insertText" not in r]
        if reset_bullets and _match_key(match) in reset_bullets:
            from gdoc.mdparse import utf16_len
            target = {"startIndex": match["startIndex"],
                      "endIndex": match["startIndex"]
                      + max(1, utf16_len(selected.plain_text))}
            if match_tab:
                target["tabId"] = match_tab
            requests.insert(1 if requests else 0,
                            {"deleteParagraphBullets": {"range": target}})
            if not selected.plain_text:
                requests.append({"updateParagraphStyle": {
                    "range": target,
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "fields": "namedStyleType,indentStart,indentEnd,indentFirstLine",
                }})
        if requests and baseline:
            updates = []
            for style in baseline:
                if style.get("retainedMark"):
                    continue
                target = dict(style["range"])
                if match_tab:
                    target["tabId"] = match_tab
                fields = style["fields"]
                if any("updateParagraphStyle" in request for request in requests):
                    fields = ",".join(sorted(
                        set(filter(None, fields.split(","))) | style["textStyle"].keys()
                    ))
                if fields:
                    updates.append({"updateTextStyle": {
                        "range": target,
                        "textStyle": {k: v for k, v in style["textStyle"].items()
                                      if k in fields.split(",")},
                        "fields": fields,
                    }})
                # A Markdown link replaces only the URL, retaining the target's
                # own decorations on its intersection with this style span.
                decor = {key: style["textStyle"][key]
                         for key in ("foregroundColor", "underline")
                         if key in style["textStyle"]}
                if decor:
                    for request in list(requests):
                        link = request.get("updateTextStyle", {})
                        if "link" not in link.get("textStyle", {}):
                            continue
                        lo = max(target["startIndex"], link["range"]["startIndex"])
                        hi = min(target["endIndex"], link["range"]["endIndex"])
                        if lo < hi:
                            requests.append({"updateTextStyle": {
                                "range": {**target, "startIndex": lo, "endIndex": hi},
                                "textStyle": decor, "fields": ",".join(sorted(decor)),
                            }})
            # Paragraph changes re-resolve direct text styles. Restore the
            # baseline after them, but before explicit inline styles/bullets.
            before_inline = next(
                (i for i, req in enumerate(requests)
                 if "updateTextStyle" in req or "createParagraphBullets" in req),
                len(requests),
            )
            requests[before_inline:before_inline] = updates
            for style in baseline:
                if not style.get("retainedMark"):
                    continue
                fields = set().union(*(set(r["updateTextStyle"]["fields"].split(","))
                                      for r in requests if "updateTextStyle" in r))
                if fields:
                    target = dict(style["range"])
                    if match_tab:
                        target["tabId"] = match_tab
                    requests.append({"updateTextStyle": {
                        "range": target,
                        "textStyle": {k: v for k, v in style["textStyle"].items()
                                      if k in fields},
                        "fields": ",".join(sorted(fields)),
                    }})
        if segment_id:
            for request in requests:
                operation = next(iter(request.values()))
                address = operation.get("range", operation.get("location"))
                address["segmentId"] = segment_id
        all_requests.extend(requests)
    return sorted_matches, all_requests


def _snapshot_tab(source: dict | None, tab_id: str | None) -> dict | None:
    """Return the flattened tab for *tab_id* from a replacement source."""
    if not source:
        return None
    if "tabs" in source:
        return next((tab for tab in flatten_tabs(source["tabs"])
                     if tab["id"] == tab_id), None)
    if "body" in source and (not tab_id or source.get("id") == tab_id):
        return source
    return None


def _replacement_range_requests(source, matches, contexts, tab_id):
    """Create ranges for replaced code/containers and keep untouched portions.

    Body matches are grouped by tab. Inline wording inside a code line or quote
    stays in that marker; structural replacements split it and get their own.
    """
    from gdoc.mdparse import utf16_len

    by_tab: dict = {}
    for match in matches:
        if match.get("segmentId"):
            continue
        selected, baseline = contexts[_match_key(match)]
        length = utf16_len(selected.plain_text) - selected.removed_tabs
        if baseline is not None and selected.plain_text == "\n" and any(
            s.type == "paragraph_style" and "borderBottom" in s.style
            for s in selected.styles
        ):
            length = 0  # The rule styles the retained LF; nothing is inserted.
        structural = (bool(selected.code_blocks) or selected.code_group is not None
                      or any(s.type == "markdown_prefix" for s in selected.styles))
        # A borrowed mark ends the previous paragraph and keeps its ranges:
        # Docs clips ranges by the physical deletion, and pieces are rebuilt
        # from the removed paragraphs with their own marks.
        physical = (match["startIndex"], match["endIndex"])
        match_span = match.get("removedSpan", physical)
        by_tab.setdefault(match.get("tabId", tab_id), []).append((
            match_span[0], match_span[1], length,
            baseline is not None and not structural, selected, physical,
        ))
    deletions, creations = [], []
    for match_tab, parts in by_tab.items():
        parts.sort(key=lambda part: part[0])
        removed, rebuilt = _owned_range_requests(
            _snapshot_tab(source, match_tab), match_tab,
            [part[:4] + (part[5],) for part in parts],
        )
        deletions += removed
        creations += rebuilt
        shift = 0
        groups: dict = {}
        for start, end, length, _, selected, _physical in parts:
            creations += _code_range_requests(selected, start + shift, match_tab)
            if selected.code_group is not None:
                # Span the group's paragraphs, including the last one's mark.
                low, _ = groups.get(id(selected.code_group), (start + shift, 0))
                groups[id(selected.code_group)] = (low, start + shift + length + 1)
            shift += length - (end - start)
        for low, high in groups.values():
            span = {"startIndex": low, "endIndex": high}
            if match_tab:
                span["tabId"] = match_tab
            creations.append({"createNamedRange": {"name": "gdoc:code:v1",
                                                   "range": span}})
    return deletions, creations


def replace_formatted(
    doc_id: str,
    matches: list[dict],
    new_markdown: str,
    revision_id: str,
    tab_id: str | None = None,
    *, body: dict | None = None, replace_paragraphs: bool = False,
    result_details: dict | None = None,
) -> int:
    """Replace matched text ranges with formatted content.

    Builds and executes a single batchUpdate with
    writeControl.requiredRevisionId. Processes matches last-to-first
    so index shifts don't affect earlier replacements.

    Args:
        doc_id: The document ID.
        matches: List of {"startIndex": int, "endIndex": int}.
        new_markdown: Replacement text (may contain markdown).
        revision_id: The document revision ID for concurrency control.
        tab_id: Optional tab ID for targeting a specific tab.
        body: Original body with paragraph and direct run styles for inline edits.
        replace_paragraphs: Whole-cell replacement may change paragraph count.
            Plain prose replaces list items with NORMAL_TEXT; non-list prose
            preserves native paragraph styles.

    Returns:
        Number of replacements made. When provided, result_details receives
        input_revision_id, acknowledged_revision_id and rebased on success.
    """
    from gdoc.mdparse import parse_markdown, utf16_len

    input_revision_id = revision_id
    parsed = parse_markdown(new_markdown)
    check_segment_replacement(parsed, new_markdown, matches)
    # A table replacing whole paragraphs inside a cell would nest a table,
    # which later stages cannot fill; refuse before sending anything.
    if parsed.tables and body is not None and any(
        element.get("startIndex", 0) <= match["startIndex"] < element.get("endIndex", 0)
        and (replace_paragraphs or _covers_whole_paragraphs(content, match))
        for match in matches if not match.get("segmentId")
        for content in [_replacement_body(body, match).get("content", [])]
        for element in content if "table" in element
    ):
        raise GdocError(
            "a Markdown table cannot replace text inside a table cell: nested "
            "tables are not supported. Replace the cell with text, or change "
            "the table's rows and columns by rewriting the tab (cat, edit the "
            "Markdown table, write --tab).", exit_code=3,
        )

    # Same guard as suggest_replacement: overlapping matches ("aa" in
    # "aaa" with --all) would make the last-to-first delete/insert plan
    # land on already-shifted text and corrupt the document.
    _reject_overlapping_matches(matches)

    _strip_trailing_newline_unless_hr(parsed)

    occurrence_count = len(matches)
    planned = []
    removals = []  # whole paragraphs an empty replacement removes
    reset_bullets = set()
    source = body
    for match in matches:
        body = _replacement_body(source, match)
        # Whole-cell selection permits structural changes. Equal-count edits
        # retain native marks, with list removal handled explicitly below.
        native = (list(_replacement_paragraphs(body.get("content", []), match))
                  if body is not None else [])
        # A table replacing complete paragraphs is structural and must reach
        # _insert_table; inside a paragraph its source stays literal text.
        whole = _covers_whole_paragraphs(body.get("content", []), match) \
            if body is not None else False
        if replace_paragraphs:
            contextual = body is not None and not parsed.tables and (
                len(native) == len(new_markdown.split("\n"))
            )
        else:
            contextual = body is not None and not (parsed.tables and whole)
        if (body is not None and not new_markdown and not replace_paragraphs
                and _removes_whole_paragraphs(body.get("content", []), match)):
            removals.append(match)
            continue
        if match.get("segmentId"):
            from gdoc.mdparse import ParsedMarkdown, parse_inline
            text, styles = parse_inline(new_markdown.removesuffix("\n"))
            selected = ParsedMarkdown(text, styles)
            found = (_replacement_paragraph(body.get("content", []), match)
                     if body is not None else None)
            baseline = (_inline_baseline(found[0], match, text, styles)
                        if found else None)
            parts = [(match, (selected, baseline))]
        elif body is not None and not new_markdown and not replace_paragraphs:
            from gdoc.mdparse import ParsedMarkdown
            content = body.get("content", [])
            joined = _joined_deletion(content, match)
            pieces = [joined] if joined else [
                part for part, _ in _paragraph_wording_matches(body, match, "")]
            whole_pieces = [p for p in pieces if _removes_whole_paragraphs(content, p)]
            removals += whole_pieces
            parts = [(p, (ParsedMarkdown(""), [])) for p in pieces
                     if p not in whole_pieces]
        elif contextual:
            parts = _wording_contexts(body, match, new_markdown)
        elif native and replace_paragraphs and not parsed.tables and not any(
            s.type == "bullets" or (s.type == "paragraph_style"
                                   and s.style != {"namedStyleType": "NORMAL_TEXT"})
            for s in parsed.styles
        ):
            # Collapsing/expanding cell wording still inherits the native
            # paragraph's custom properties; NORMAL_TEXT would reset them.
            paragraph = {"elements": [run for p, _, _ in native
                                      for run in p.get("elements", [])]}
            parts = [(match, (_inline_only(parsed), _inline_baseline(
                paragraph, match, parsed.plain_text, parsed.styles,
            )))]
        else:
            placed = match
            if whole and body is not None:
                placed = _table_between_blanks(body.get("content", []), match,
                                               parsed) or match
            parts = [(placed, (_contained_parse(
                parsed, _snapshot_tab(source, match.get("tabId", tab_id)),
                match.get("tabId", tab_id), match) if whole else parsed, None))]
        for part, context in parts:
            found = (_replacement_paragraph(body.get("content", []), part)
                     if body is not None else None)
            explicit = any(s.type in ("paragraph_style", "bullets")
                           for s in context[0].styles)
            # A structural collapse keeps only the last paragraph's mark, so
            # its bullet is the one the inserted prose would inherit.
            if replace_paragraphs and not explicit and native and (
                not new_markdown or (found and found[0].get("bullet"))
                or (not contextual and native[-1][0].get("bullet"))
            ):
                # Plain whole-cell prose is the explicit list-removal route.
                # Keep non-list paragraph properties and ordinary edits intact.
                from gdoc.mdparse import StyleRange
                context[0].styles.append(StyleRange(
                    0, len(context[0].plain_text),
                    {"namedStyleType": "NORMAL_TEXT"}, "paragraph_style",
                ))
                reset_bullets.add(_match_key(part))
                _reset_list_indents(context[0])
            elif explicit and (
                (replace_paragraphs and not contextual)
                or (found and found[0].get("bullet"))
            ):
                reset_bullets.add(_match_key(part))
                _reset_list_indents(context[0])
            planned.append((part, context))
    if removals:
        from gdoc.mdparse import ParsedMarkdown
        planned += [(plan, (ParsedMarkdown(""), []))
                    for plan in _plan_paragraph_removals(source, removals)]
    matches = [part for part, _ in planned]
    contexts = {_match_key(part): context for part, context in planned}
    # Table insertion after the main batch tracks index shifts for a single
    # block-path match only. Inline matches insert the table source literally
    # and never reach _insert_table, so they do not count.
    block_paths = sum(1 for _, baseline in contexts.values() if baseline is None)
    if parsed.tables and block_paths > 1:
        raise GdocError(
            "replacement with tables not supported with --all", exit_code=3,
        )
    image_snapshot = source or {}
    selected_parses = [selected for selected, _ in contexts.values()]
    if any(_parsed_images(selected) or selected.tables for selected in selected_parses):
        needs_snapshot = any(image.uri.startswith("gdoc-image:")
                             for selected in selected_parses
                             for image in _parsed_images(selected))
        # Table references need the same current object map as body images.
        needs_snapshot = needs_snapshot or any(
            "gdoc-image:" in cell for selected in selected_parses
            for table in selected.tables for row in table.rows for cell in row
        )
        if needs_snapshot and not (
            "tabs" in image_snapshot or "inlineObjects" in image_snapshot
        ):
            image_snapshot = get_document_with_tabs(doc_id)
            if image_snapshot.get("revisionId") != input_revision_id:
                raise GdocError("conflict: image snapshot revision changed",
                                exit_code=3)
        for selected in selected_parses:
            _prepare_image_sources(selected, image_snapshot)
    sorted_matches, all_requests = _build_replacement_requests(
        parsed, matches, tab_id=tab_id, contexts=contexts,
        reset_bullets=reset_bullets,
    )
    if all_requests:
        deletions, creations = _replacement_range_requests(
            source, sorted_matches, contexts, tab_id,
        )
        all_requests = deletions + all_requests + creations

    if not all_requests:
        if result_details is not None:
            result_details.update(input_revision_id=input_revision_id,
                                  acknowledged_revision_id=input_revision_id,
                                  rebased=False)
        return 0

    # Each lower replacement may have a different rendered length when
    # --all includes both partial and complete paragraph matches.
    shifts = {}
    totals = {}
    for match in reversed(sorted_matches):
        pos = match["startIndex"]
        selected, _ = contexts[_match_key(match)]
        space = _match_space(match)
        shifts[_match_key(match)] = totals.get(space, 0)
        totals[space] = (totals.get(space, 0) + utf16_len(selected.plain_text)
                         - selected.removed_tabs - (match["endIndex"] - pos))

    with _StagedWrite(doc_id) as progress:
        revision_id = progress.batch(
            "matched text and formatting replaced", all_requests, revision_id,
        )

        # Insert tables only for explicit structural replacements.
        if parsed.tables:
            for ordinal, table in reversed(list(enumerate(parsed.tables, 1))):
                # UTF-16 offset of the table placeholder; invariant per
                # table, so hoisted out of the per-match loop.
                offset16 = utf16_len(
                    parsed.plain_text[:table.plain_text_offset],
                )
                for match in sorted_matches:
                    if contexts[_match_key(match)][1] is not None:
                        continue
                    shift = shifts[_match_key(match)]
                    idx = (
                        match["startIndex"] + offset16
                        - table.removed_tabs_before + shift
                        - match.get("tableBack", 0)
                    )
                    # The match's own parse may place the table in a container.
                    placed = contexts[_match_key(match)][0].tables[ordinal - 1]
                    revision_id = _insert_table(
                        doc_id, idx, placed, tab_id=match.get("tabId", tab_id),
                        revision_id=revision_id,
                        progress=progress,
                        resolve_index=_table_position_resolver(
                            parsed, table, match.get("tabId", tab_id),
                        ),
                        ordinal=ordinal, scaffolding=_table_scaffolding(parsed, table),
                    )

        if result_details is not None:
            result_details.update(input_revision_id=input_revision_id,
                                  acknowledged_revision_id=revision_id,
                                  rebased=progress.rebased)
        return occurrence_count


# ---------------------------------------------------------------------------
# Suggested edits (Docs API Developer Preview: writeControl.writeMode=SUGGEST)
# ---------------------------------------------------------------------------

SUGGESTIONS_INLINE = "SUGGESTIONS_INLINE"

_SUGGEST_UNSUPPORTED_HINT = (
    "suggest supports plain text and inline formatting (bold, italic, "
    "strikethrough, code, links); headings, lists, blockquotes, horizontal "
    "rules, and tables are not supported yet. Use `gdoc edit` to apply "
    "structural Markdown directly."
)


@dataclass
class SuggestionResult:
    """Outcome of a suggest-mode batchUpdate.

    ``created_suggestion_ids`` come from ``suggestionResponses[].
    createdSuggestionIds``; ``updated_suggestion_ids`` from
    ``updatedSummarySuggestionIds`` minus anything created in the same batch
    (Google merges an edit adjacent to the same author's open suggestion
    into it instead of creating a new one, and also echoes new IDs under
    "updated"). Both are flattened across responses and de-duplicated in
    order.
    """

    occurrences: int
    created_suggestion_ids: list[str] = field(default_factory=list)
    updated_suggestion_ids: list[str] = field(default_factory=list)
    comment_update_state: str = ""
    acknowledged_revision_id: str = ""

    @property
    def suggestion_ids(self) -> list[str]:
        """Every affected suggestion ID, created first, no duplicates."""
        return _dedupe(self.created_suggestion_ids + self.updated_suggestion_ids)


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def check_inline_only_markdown(parsed) -> None:
    """Reject replacement Markdown that suggest mode can't represent yet.

    Suggest mode runs one batch only — no read-back cleanup or table
    insertion phases (those would each have to stay in SUGGEST mode and
    Google's suggested-structure shapes are unverified). So anything that
    is not plain text or an inline text style fails here, before any API
    call, with a usage error (exit 3).

    Newlines are allowed: they become suggested paragraph breaks, and the
    new paragraphs inherit the anchor paragraph's style (no paragraph-style
    requests are sent in suggest mode). Fenced code blocks pass too — they
    parse to plain NORMAL_TEXT paragraphs in the code font.
    """
    if parsed.tables or _parsed_images(parsed):
        raise GdocError(_SUGGEST_UNSUPPORTED_HINT, exit_code=3)
    for sr in parsed.styles:
        if sr.type == "text_style":
            continue
        if sr.type == "paragraph_style" and sr.style == {
            "namedStyleType": "NORMAL_TEXT",
        }:
            # Plain paragraphs carry an explicit NORMAL_TEXT; harmless.
            continue
        raise GdocError(_SUGGEST_UNSUPPORTED_HINT, exit_code=3)


def _inline_only(parsed):
    """Copy of *parsed* keeping only text styles.

    The NORMAL_TEXT paragraph-style requests ``edit`` sends would become
    suggested paragraph-style changes on the surrounding paragraph — noise
    in the review thread. Suggested text inherits the paragraph's style.
    """
    from gdoc.mdparse import ParsedMarkdown

    return ParsedMarkdown(
        plain_text=parsed.plain_text,
        styles=[s for s in parsed.styles if s.type == "text_style"],
        tables=[],
        removed_tabs=0,
    )


def _walk_suggestion_ids(node, out: set[str]) -> None:
    """Collect every suggestion ID referenced anywhere under *node*.

    Schema-tolerant: any ``suggested*`` list contributes its string values
    (``suggestedInsertionIds``) and any ``suggested*`` map contributes its
    keys (``suggestedTextStyleChanges``, and also
    ``suggestedPositionedObjectIds``, which is a map keyed by suggestion ID
    despite its name), so new suggestion kinds are picked up without a
    code change.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("suggested"):
                if isinstance(value, list):
                    out.update(v for v in value if isinstance(v, str))
                    continue
                if isinstance(value, dict):
                    out.update(value.keys())
                    continue
            _walk_suggestion_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_suggestion_ids(item, out)


def collect_suggestion_ids(doc: dict) -> set[str]:
    """All suggestion IDs present in a SUGGESTIONS_INLINE document read."""
    out: set[str] = set()
    _walk_suggestion_ids(doc, out)
    return out


def _overlaps(elem: dict, start: int, end: int) -> bool:
    e_start = elem.get("startIndex", 0)
    e_end = elem.get("endIndex", e_start)
    if start == end:
        # zero-width match: an insertion point inside (or at the end of)
        # the element
        return e_start <= start < e_end or (e_start < start <= e_end)
    return e_start < end and start < e_end


def find_suggestions_in_range(body: dict, start: int, end: int) -> set[str]:
    """Suggestion IDs whose inline content or style map touches [start, end).

    Walks paragraphs (recursing into table cells) read with
    SUGGESTIONS_INLINE. For an overlapping paragraph, its paragraph-level
    ``suggested*`` fields count, plus every overlapping element's
    (``suggestedInsertionIds``, ``suggestedDeletionIds``,
    ``suggestedTextStyleChanges``, ...). For an overlapping table, the
    table's, each overlapping row's, and each overlapping cell's own
    ``suggested*`` fields count too (suggested table/row/cell insertions,
    deletions, and style changes), before recursing into the cell content.
    Any other overlapping structural element (a section break, a table of
    contents) has no index-aware model here, so every suggestion anywhere
    under it counts — conservative, like ``_container_overlaps``.
    """
    found: set[str] = set()
    for elem in body.get("content", []):
        if not _overlaps(elem, start, end):
            continue
        paragraph = elem.get("paragraph")
        if paragraph is not None:
            _walk_container_suggestions(paragraph, found)
            for pe in paragraph.get("elements", []):
                if _overlaps(pe, start, end):
                    _walk_suggestion_ids(pe, found)
            continue
        table = elem.get("table")
        if table is not None:
            # Container-level suggestions (a suggested table, row, or cell
            # insertion/deletion/style) are recorded on the container, not
            # on the text runs inside it.
            _walk_container_suggestions(table, found)
            for row in table.get("tableRows", []):
                if not _container_overlaps(row, start, end):
                    continue
                _walk_container_suggestions(row, found)
                for cell in row.get("tableCells", []):
                    if not _container_overlaps(cell, start, end):
                        continue
                    _walk_container_suggestions(cell, found)
                    found |= find_suggestions_in_range(cell, start, end)
            continue
        # sectionBreak, tableOfContents, or a future element type: a match
        # can span one (segments concatenate across them), and a suggested
        # section break / TOC entry is someone's review thread too.
        _walk_suggestion_ids(elem, found)
    return found


def _container_overlaps(node: dict, start: int, end: int) -> bool:
    """Rows/cells without index fields are not skipped (be conservative)."""
    if "startIndex" not in node or "endIndex" not in node:
        return True
    return _overlaps(node, start, end)


def _walk_container_suggestions(node: dict, out: set[str]) -> None:
    """Collect this node's own ``suggested*`` fields (no recursion)."""
    for key, value in node.items():
        if key.startswith("suggested"):
            _walk_suggestion_ids({key: value}, out)


_SUGGEST_UNCERTAIN = (
    "The outcome is unknown — the suggestion may or may not have been saved. "
    "Inspect the document before retrying."
)


def _classify_suggest_error(e: HttpError, doc_id: str) -> None:
    """Translate a suggest-mode batchUpdate HttpError, preserving the reason.

    Never falls back: a suggestion that cannot be made must not become a
    direct edit.
    """
    status = int(e.resp.status)
    detail = str(e)
    lowered = detail.lower()
    if (
        status == 400
        and "revision" in lowered
        and "invalid value" not in lowered
    ):
        # A stale requiredRevisionId. A malformed one ("Invalid value at
        # 'write_control.required_revision_id'") is a bug, not a race, and
        # must not be reported as "re-run it".
        raise GdocError(
            "document changed while the command was running; re-run it "
            f"(server: {e.reason})", exit_code=3,
        )
    if status == 400 and (
        "unknown name" in lowered
        or "cannot find field" in lowered
        or "no request set" in lowered
        or "write_mode" in lowered
        or "writemode" in lowered
    ):
        raise GdocError(
            "suggest mode not available: the OAuth client's Cloud project "
            "is not enrolled in the Google Workspace Developer Preview "
            f"(server rejected the request: {e.reason}). No change was made."
        )
    if status == 403:
        raise GdocError(
            f"Permission denied: {doc_id} (suggesting through the API needs "
            "comment or edit access on the document, or the project lacks "
            "Developer Preview access)"
        )
    if status >= 500:
        # A 5xx can arrive after Google applied the mutation: like a
        # transport failure, the outcome is unknown and a blind retry may
        # create a duplicate suggestion.
        raise GdocError(
            f"the suggest write returned a server error ({status}: "
            f"{e.reason}). The outcome is unknown — the suggestion may or "
            "may not have been saved. Inspect the document before retrying."
        )
    _translate_http_error(e, doc_id)


def _token_identity(account: str | None) -> tuple[str | None, str | None]:
    """The (client_id, refresh_token) pair in the account's token file.

    The identity that must hold still between the enrollment gate and the
    write: Developer Preview enrollment belongs to the OAuth client
    project (client_id), and the suggestion's author is the granted user
    (refresh_token — any new grant mints a new one, whether the client or
    the Google user changed). A routine access-token refresh rewrites the
    file but keeps both. Missing or unreadable token reads as (None, None).
    """
    import json

    from gdoc.util import token_path_for

    try:
        with token_path_for(account).open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)
    return (data.get("client_id"), data.get("refresh_token"))


def check_suggest_preview_access(doc_id: str) -> None:
    """Non-mutating gate: is this OAuth client's project preview-enrolled?

    An unenrolled backend has been observed *ignoring* ``writeMode: SUGGEST``
    and applying the batch as a direct edit, so enrollment must be proven
    before the write, not inferred from its response. The decisive probe
    is a ``documents.get`` with the preview-only ``commentsViewMode`` field:
    a registered project echoes it (HTTP 200), an unregistered one rejects
    it (400 ``Unknown name "comments_view_mode"``). Same account, document,
    and scopes — only the client project changes the answer.

    Sent as a raw authorized GET because the bundled discovery document
    predates the field and the generated client refuses unknown parameters.
    """
    from google.auth.exceptions import GoogleAuthError, TransportError
    from google.auth.transport.requests import AuthorizedSession
    from requests.exceptions import RequestException

    from gdoc.auth import get_credentials

    account, _stamp = account_cache_key()
    try:
        session = AuthorizedSession(get_credentials(account))
        resp = session.get(
            f"https://docs.googleapis.com/v1/documents/{doc_id}",
            params={
                # Google requires the two companions ("Comments view mode
                # may only be specified if tabs content is also requested"
                # / "... if inline suggestions are also explicitly
                # requested").
                "includeTabsContent": "true",
                "suggestionsViewMode": "SUGGESTIONS_INLINE",
                "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
                "fields": "documentId,commentsViewMode",
            },
            timeout=60,
        )
    except TransportError as e:
        # google-auth wraps a network failure during token refresh in
        # TransportError (a GoogleAuthError subclass) — it is a transport
        # problem, not bad credentials, so it must not become "run
        # `gdoc auth`". Fail closed — nothing has been written.
        raise GdocError(
            "suggest mode check failed before any write "
            f"(network error: {e}). No change was made."
        )
    except GoogleAuthError as e:
        # Credentials that could not be refreshed (revoked, expired).
        raise AuthError(f"Authentication expired ({e}). Run `gdoc auth`.")
    except RequestException as e:
        # Transport failure: fail closed — nothing has been written.
        raise GdocError(
            "suggest mode check failed before any write "
            f"(network error: {e}). No change was made."
        )
    status = resp.status_code
    if status == 200:
        try:
            mode = resp.json().get("commentsViewMode", "")
        except ValueError:
            mode = ""
        if mode != "COMMENTS_VIEW_MODE_INCLUDED":
            raise GdocError(
                "suggest mode not available: the server accepted but did "
                "not apply the Developer Preview read field, so suggest "
                "mode cannot be trusted. No change was made."
            )
        return
    detail = resp.text.lower()
    if status == 400 and (
        "unknown name" in detail or "cannot find field" in detail
    ):
        raise GdocError(
            "suggest mode not available: the OAuth client's Cloud project "
            "is not enrolled in the Google Workspace Developer Preview "
            "(preview read field rejected). No change was made."
        )
    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    if status == 403:
        raise GdocError(
            f"Permission denied: {doc_id} (reading suggestions needs comment "
            "or edit access on the document)"
        )
    if status == 404:
        raise GdocError(f"Document not found: {doc_id}")
    raise GdocError(f"API error ({status}): {resp.reason}")


def _reject_overlapping_matches(matches: list[dict]) -> None:
    """Two matches sharing text (``aa`` in ``aaa``) can't both be replaced:
    the last-to-first delete/insert plan would land on shifted text."""
    ordered = sorted(matches, key=lambda m: (_match_space(m), m["startIndex"]))
    for prev, cur in zip(ordered, ordered[1:]):
        if (_match_space(prev) == _match_space(cur)
                and cur["startIndex"] < prev["endIndex"]):
            raise GdocError(
                "matches overlap each other (the anchor repeats within "
                f"itself around index {cur['startIndex']}); use a longer "
                "anchor or drop --all",
                exit_code=3,
            )


def suggest_replacement(
    doc_id: str,
    matches: list[dict],
    new_markdown: str,
    revision_id: str,
    tab_id: str | None = None,
    expected_token_identity: tuple[str | None, str | None] | None = None,
    *, body: dict | None = None,
) -> SuggestionResult:
    """Replace matched ranges as *suggested* edits (writeMode=SUGGEST).

    One batchUpdate carries the delete + insert + inline-style requests for
    every match, with ``writeControl.requiredRevisionId`` pinned to the
    revision the matches were computed against. Before it, a non-mutating
    preview read (``check_suggest_preview_access``) proves the project is
    enrolled, because an unenrolled backend may silently apply the batch as
    a direct edit. Success requires all of:
    HTTP 200, ``commentUpdateState == ALL_SAVED``, at least one created or
    updated suggestion ID in ``suggestionResponses``, and a SUGGESTIONS_INLINE
    read-back containing every one of those IDs. Anything less raises —
    there is deliberately no fallback to a direct edit.

    Args:
        doc_id: The document ID.
        matches: ``{"startIndex", "endIndex"}`` ranges in the
            SUGGESTIONS_INLINE index space of *tab_id*.
        new_markdown: Replacement text; plain or inline Markdown only
            (see ``check_inline_only_markdown``).
        revision_id: Non-empty revision the ranges were read at.
        tab_id: Tab holding the ranges (omitted → first tab).
        expected_token_identity: ``_token_identity()`` captured before the
            document read; when given, the pre-write identity check uses it
            as the baseline, extending the re-auth guard across the read
            (the CLI passes it). Omitted → the baseline is captured here,
            guarding the gate→write pair only.
        body: Source snapshot for native paragraph marks and target run styles.
    """
    from google.auth.exceptions import GoogleAuthError, TransportError

    from gdoc.mdparse import parse_markdown

    if not revision_id:
        raise GdocError(
            "cannot suggest: the document read did not include a revision "
            "ID, so the write cannot be pinned to the text that was "
            "matched. Google withholds revisionId from users without "
            "comment or edit access; suggesting through the API needs at "
            "least commenter permission on the document."
        )

    parsed = parse_markdown(new_markdown)
    check_segment_replacement(parsed, new_markdown, matches)
    if body is None:
        check_inline_only_markdown(parsed)
    _reject_overlapping_matches(matches)
    _strip_trailing_newline_unless_hr(parsed)
    occurrence_count = len(matches)
    if body is None:
        _, requests = _build_replacement_requests(_inline_only(parsed), matches,
                                                 tab_id=tab_id)
    else:
        if parsed.tables and any(
            _covers_whole_paragraphs(
                _replacement_body(body, match).get("content", []), match)
            for match in matches
        ):
            # edit would insert a native table here; suggest cannot, and
            # literal rows would misrepresent the request. Inside a
            # paragraph the rows are literal text, as in edit.
            check_inline_only_markdown(parsed)
        if not new_markdown and any(
            len(list(_replacement_paragraphs(
                _replacement_body(body, match).get("content", []), match))) > 1
            for match in matches
        ):
            # Suggestions keep every native paragraph mark, so accepting this
            # would leave the paragraphs apart instead of joining them.
            raise GdocError(
                "cannot suggest deleting text across a paragraph break: the "
                "suggestion would keep the break, so accepting it would not "
                "join the paragraphs. Suggest each paragraph's deletion "
                "separately, or use edit", exit_code=3,
            )
        planned = [part for match in matches
                   for part in _wording_contexts(
                       _replacement_body(body, match), match, new_markdown)]
        # Validate each resolved context before planning suggestion requests.
        for _, (selected, _) in planned:
            check_inline_only_markdown(selected)
        requests = []
        for match, (selected, baseline) in sorted(
                planned, key=lambda part: _replacement_order(part[0])):
            # A SUGGEST style update changes the accepted preview only. Never
            # mistake that proposal for the pending insertion's native style.
            baseline = [s for s in baseline or [] if not s.get("retainedMark")]
            scope = _replacement_body(body, match)
            found = _replacement_paragraph(scope.get("content", []), match)
            runs = [run for run in found[0].get("elements", [])
                    if "textRun" in run] if found else []
            # Unlike EDIT, SUGGEST retains the original at paragraph start.
            # Its first run supplies insertion style, not the following run
            # used by the edit baseline; an empty field mask is not proof of
            # safety. Check the desired native style at every target position.
            pending_style = next(
                (run["textRun"].get("textStyle", {}) for run in runs
                 if run.get("startIndex", 0) < match["startIndex"] <= run["endIndex"]),
                runs[0]["textRun"].get("textStyle", {}) if runs else {},
            )
            at_target_end = bool(selected.plain_text and any(
                s["fields"] or s["textStyle"] != pending_style for s in baseline))
            if at_target_end:
                source_style = next((
                    run["textRun"].get("textStyle", {})
                    for run in found[0].get("elements", [])
                    if "textRun" in run and run.get("startIndex", 0)
                    < match["endIndex"] <= run["endIndex"]
                ), {}) if found else {}
                if (not found or "link" in source_style
                        or any(s["textStyle"] != source_style for s in baseline)):
                    raise GdocError(
                        "cannot suggest this replacement while preserving its pending "
                        "text style: mixed or linked targets require "
                        "formatting that the API can only propose for acceptance; "
                        "use narrower uniformly styled, unlinked matches "
                        "or edit instead",
                        exit_code=3,
                    )
                # Insert while the matched run still exists so its own style
                # is inherited. Suggest deletion afterwards; the native pending
                # order is old+new, and rejecting retains the untouched original.
                insertion = {**match, "startIndex": match["endIndex"]}
                context = (_inline_only(selected), [])
                _, built = _build_replacement_requests(
                    parsed, [insertion], tab_id=tab_id,
                    contexts={_match_key(insertion): context},
                )
                delete_range = {key: match[key] for key in
                                ("startIndex", "endIndex", "tabId", "segmentId")
                                if key in match}
                if tab_id and "tabId" not in delete_range:
                    delete_range["tabId"] = tab_id
                built.append({"deleteContentRange": {"range": delete_range}})
            else:
                _, built = _build_replacement_requests(
                    parsed, [match], tab_id=tab_id,
                    contexts={_match_key(match): (_inline_only(selected), baseline)},
                )
            requests.extend(built)
    if not requests:
        return SuggestionResult(occurrences=0)

    # Prove enrollment with a read before the write (see the gate's doc),
    # pinning one OAuth identity across both: a re-auth landing between
    # them could otherwise swap the client project (proving enrollment for
    # one project while the batch goes through another — which an
    # unenrolled backend may apply as a direct edit) or the granted user
    # (authoring the suggestion as someone else). The pin is the token's
    # (client_id, refresh_token) pair, not the file's identity: a routine
    # access-token refresh (the gate's own get_credentials persists one
    # when a long-lived process refreshed only in memory) rewrites the
    # file but keeps both, and must not abort; any new grant mints a new
    # refresh_token.
    account, _stamp = account_cache_key()
    gate_identity = (
        expected_token_identity
        if expected_token_identity is not None
        else _token_identity(account)
    )
    # The gate, the service lookup, and the verification read each resolve
    # the active account themselves; pin the one captured above so an
    # unpinned direct call cannot have a concurrent default-account change
    # split them across two accounts (the CLI and MCP already pin — they
    # re-enter the same value).
    from gdoc.util import account_context

    with account_context(account):
        check_suggest_preview_access(doc_id)

        body = {
            "requests": requests,
            "writeControl": {
                "requiredRevisionId": revision_id,
                "writeMode": "SUGGEST",
            },
        }
        service = get_docs_service()
        if _token_identity(account) != gate_identity:
            raise GdocError(
                "credentials changed while preparing the suggest write "
                "(the account was re-authenticated between the enrollment "
                "check and the write, so the check may have proven a "
                "different OAuth client project or user). No change was "
                "made — rerun the command."
            )
        from gdoc.api.comment_transport import execute_mutation_request

        try:
            # One wire send: a resent suggestion after a lost response would
            # be refused as stale and misreported as not saved.
            result = execute_mutation_request(
                service.documents().batchUpdate(documentId=doc_id, body=body),
                uncertainty=_SUGGEST_UNCERTAIN,
            )
        except HttpError as e:
            _classify_suggest_error(e, doc_id)
        except (GdocError, AuthError):
            raise
        except TransportError as e:
            # Raised only while refreshing the access token, which happens
            # before the request is sent — nothing reached Google.
            raise GdocError(
                "the suggest write failed before it was sent (network "
                f"error during token refresh: {e}). No change was made."
            )
        except GoogleAuthError as e:
            # Credential failure (revoked/expired beyond refresh) — also
            # pre-send, and an auth problem, not an indeterminate write.
            raise AuthError(f"Authentication expired ({e}). Run `gdoc auth`.")
        except Exception as e:  # noqa: BLE001 — .execute() is the network
            # call; a timeout or reset here can land after Google has
            # accepted the write, so the outcome is genuinely
            # indeterminate. A generic unexpected error would let the
            # caller retry blindly.
            raise GdocError(
                f"the suggest write failed in transit ({str(e) or type(e).__name__})"
                ". " + _SUGGEST_UNCERTAIN
            )

        state = result.get("commentUpdateState", "")
        created: list[str] = []
        updated: list[str] = []
        for resp in result.get("suggestionResponses", []) or []:
            created.extend(resp.get("createdSuggestionIds", []) or [])
            updated.extend(resp.get("updatedSummarySuggestionIds", []) or [])
        created = _dedupe(created)
        # Google echoes a just-created ID under updatedSummarySuggestionIds
        # as well; report as "updated" only threads that existed before this
        # batch (i.e. the edit was merged into the author's open suggestion).
        updated = [sid for sid in _dedupe(updated) if sid not in created]
        outcome = SuggestionResult(
            occurrences=occurrence_count,
            created_suggestion_ids=created,
            updated_suggestion_ids=updated,
            comment_update_state=state,
            acknowledged_revision_id=result.get("writeControl", {}).get(
                "requiredRevisionId", "",
            ),
        )

        if state != "ALL_SAVED" or not outcome.suggestion_ids:
            # The server answered 200 but produced no durable review object:
            # either it ignored writeMode (an unenrolled backend has been
            # seen applying the change directly) or the suggestion thread
            # failed to save. Say so loudly; the caller must inspect the
            # document.
            raise GdocError(
                "suggest mode was not applied: the server returned "
                f"commentUpdateState={state or 'none'} and "
                f"{len(outcome.suggestion_ids)} suggestion ID(s)"
                + (
                    # Name any IDs that did come back: some review objects
                    # may exist, and a caller deciding whether to retry
                    # must be able to find them.
                    " (" + ", ".join(outcome.suggestion_ids) + ")"
                    if outcome.suggestion_ids
                    else ""
                )
                + ". The Cloud project may lack Developer Preview access. "
                "Check the document — the text may have been edited "
                "directly."
            )

        try:
            readback = get_document_structure(
                doc_id, suggestions_view_mode=SUGGESTIONS_INLINE,
            )
        except Exception as e:  # noqa: BLE001 — the write succeeded; only
            # the verification read failed, either as a GdocError from the
            # API layer or as an untranslated transport error (timeout,
            # reset). A bare "API error (503)" — or a naked ConnectionError
            # — would hide that a suggestion very likely exists now.
            raise GdocError(
                "suggestion(s) " + ", ".join(outcome.suggestion_ids)
                + " were reported saved but could not be verified "
                f"({str(e) or type(e).__name__}); inspect the document",
                exit_code=getattr(e, "exit_code", 1),
            )
        present = collect_suggestion_ids(readback)
        missing = [
            sid for sid in outcome.suggestion_ids if sid not in present
        ]
        if missing:
            raise GdocError(
                "suggestion not found on read-back: "
                + ", ".join(missing)
                + ". The server reported it but the document does not show "
                "it as a pending suggestion; inspect the document."
            )
        return outcome
