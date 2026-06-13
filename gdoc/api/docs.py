"""Google Docs API v1 wrapper functions with error translation."""

import re
from functools import lru_cache

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gdoc.util import AuthError, GdocError, fold_typography


@lru_cache(maxsize=1)
def get_docs_service():
    """Build and cache a Docs API v1 service object."""
    from gdoc.auth import get_credentials

    creds = get_credentials()
    return build("docs", "v1", credentials=creds)


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
        result = (
            service.documents()
            .batchUpdate(documentId=doc_id, body=body)
            .execute()
        )

        replies = result.get("replies", [])
        if replies:
            return replies[0].get("replaceAllText", {}).get(
                "occurrencesChanged", 0
            )
        return 0
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
        })
        for child in tab.get("childTabs", []):
            result.extend(flatten_tabs([child], _level=_level + 1))
    return result


def get_document_tabs(doc_id: str) -> list[dict]:
    """Fetch document with all tab content and return flattened tab list."""
    try:
        service = get_docs_service()
        doc = (
            service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )
        return flatten_tabs(doc.get("tabs", []))
    except HttpError as e:
        _translate_http_error(e, doc_id)


def count_document_tabs(doc_id: str) -> int:
    """Return the total tab count (including nested child tabs).

    No `fields` mask: the Docs API rejects masks that recursively
    expand `childTabs` (issue #14).
    """
    doc = get_document_with_tabs(doc_id)
    return len(flatten_tabs(doc.get("tabs", [])))


def get_tab_text(tab: dict) -> str:
    """Extract plain text from a tab's body content.

    Handles paragraphs and tables (tab-joined cells per row).
    """
    body = tab.get("body", {})
    content = body.get("content", [])
    parts = []
    for element in content:
        if "paragraph" in element:
            parts.append(_extract_paragraphs_text([element]))
        elif "table" in element:
            table = element["table"]
            for row in table.get("tableRows", []):
                cells = []
                for cell in row.get("tableCells", []):
                    cell_content = cell.get("content", [])
                    cell_text = _extract_paragraphs_text(cell_content).strip()
                    cells.append(cell_text)
                parts.append("\t".join(cells) + "\n")
    return "".join(parts)


def resolve_tab(tabs: list[dict], tab_name: str) -> dict:
    """Resolve a tab by title (case-insensitive) or ID.

    Args:
        tabs: Flattened list of tab dicts from flatten_tabs().
        tab_name: Tab title or ID to match.

    Returns:
        The matched tab dict.

    Raises:
        GdocError: If no matching tab is found.
    """
    for t in tabs:
        if t["title"].lower() == tab_name.lower():
            return t
    for t in tabs:
        if str(t["id"]) == tab_name:
            return t
    raise GdocError(f"tab not found: {tab_name}", exit_code=3)


def get_document(doc_id: str) -> dict:
    """Fetch the full document structure via documents().get().

    Returns the document JSON including body.content and revisionId.
    """
    try:
        service = get_docs_service()
        return service.documents().get(documentId=doc_id).execute()
    except HttpError as e:
        _translate_http_error(e, doc_id)


def _collect_segments(content: list[dict]) -> list[list[tuple[int, str]]]:
    """Group (doc_index, char) pairs into independently-searchable segments.

    Paragraph text at one level forms a single segment; each table cell is
    its own segment (recursively, for nested tables). Searching per segment
    means a match can never span a table-cell boundary \u2014 the Docs API can't
    delete a range that crosses cells or removes a cell's final paragraph
    mark, so such a match would produce an invalid edit.
    """
    segments: list[list[tuple[int, str]]] = []
    root: list[tuple[int, str]] = []
    for element in content:
        paragraph = element.get("paragraph")
        if paragraph is not None:
            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if text_run is None:
                    continue
                run = text_run.get("content", "")
                start_idx = pe.get("startIndex", 0)
                for i, ch in enumerate(run):
                    root.append((start_idx + i, ch))
            continue
        table = element.get("table")
        if table is not None:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    segments.extend(_collect_segments(cell.get("content", [])))
    if root:
        segments.append(root)
    return segments


def find_text_in_document(
    document: dict | None,
    text: str,
    match_case: bool = False,
    body: dict | None = None,
    normalize: bool = False,
) -> list[dict]:
    """Find all occurrences of text within the document body.

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

    Returns list of {"startIndex": int, "endIndex": int} in document
    coordinates, ordered by startIndex.
    """
    if body is None:
        if document is None:
            return []
        body = document.get("body", {})

    matches = []
    for chars in _collect_segments(body.get("content", [])):
        concat = "".join(ch for _, ch in chars)
        doc_indices = [idx for idx, _ in chars]

        search_text = text
        search_in = concat
        if normalize:
            search_text = fold_typography(search_text)
            search_in = fold_typography(search_in)
        if not match_case:
            search_text = search_text.lower()
            search_in = search_in.lower()
        if not search_text:
            continue

        start = 0
        while True:
            pos = search_in.find(search_text, start)
            if pos == -1:
                break
            end_pos = pos + len(search_text)
            matches.append({
                "startIndex": doc_indices[pos],
                "endIndex": doc_indices[end_pos - 1] + 1,
            })
            start = pos + 1

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
    element with an index can be located.
    """
    first_start: int | None = None
    last_start: int | None = None
    last_content = ""
    for element in cell.get("content", []):
        para = element.get("paragraph")
        if para is None:
            continue
        for pe in para.get("elements", []):
            start = pe.get("startIndex")
            if start is None:
                continue
            if first_start is None:
                first_start = start
            tr = pe.get("textRun")
            last_start = start
            last_content = tr.get("content", "") if tr else ""
    if first_start is None:
        content = cell.get("content", [])
        fs = content[0].get("startIndex") if content else None
        return {"startIndex": fs, "endIndex": fs} if fs is not None else None
    end = last_start + len(last_content)
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
    - label (anything else): find the first row cell whose text equals
      `cell`; the target is column `col` if given, else the cell to its
      right. Searches every table by default, or only table `table_index`
      when one is given.

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

    # Label mode: honor an explicit --table; otherwise scan all tables.
    if table_index is None:
        search_tables = tables
    elif 0 <= table_index < len(tables):
        search_tables = [tables[table_index]]
    else:
        return None

    target = fold_typography(cell) if normalize else cell
    target = target.strip()
    for table in search_tables:
        for row in table.get("tableRows", []):
            cells = row.get("tableCells", [])
            for ci, c_ in enumerate(cells):
                label = _extract_paragraphs_text(c_.get("content", []))
                label = (fold_typography(label) if normalize else label).strip()
                if label == target:
                    target_col = col if col is not None else ci + 1
                    if not 0 <= target_col < len(cells):
                        return None
                    return _cell_text_range(cells[target_col])
    return None


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


def _insert_table(
    doc_id: str,
    index: int,
    table,
    tab_id: str | None = None,
) -> None:
    """Insert a native Google Docs table and populate cells.

    Three-step process:
    1. insertTable batchUpdate
    2. documents().get() read-back to find cell indices
    3. insertText into cells (reverse order to avoid shifts)
    """
    try:
        service = get_docs_service()

        # Step 1: Insert the table structure
        location = {"index": index}
        if tab_id:
            location["tabId"] = tab_id
        insert_req = {
            "insertTable": {
                "rows": table.num_rows,
                "columns": table.num_cols,
                "location": location,
            }
        }
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [insert_req]},
        ).execute()

        # Step 2: Read back document to find cell positions
        if tab_id:
            doc = service.documents().get(
                documentId=doc_id, includeTabsContent=True,
            ).execute()
            tabs = flatten_tabs(doc.get("tabs", []))
            tab_match = resolve_tab(tabs, tab_id)
            cell_indices = _find_table_cell_indices(
                None, index, body=tab_match["body"],
            )
        else:
            document = service.documents().get(
                documentId=doc_id
            ).execute()
            cell_indices = _find_table_cell_indices(document, index)

        if not cell_indices:
            return

        # Parse each cell's markdown to plain text + inline styles, once.
        from gdoc.mdparse import StyleRange, parse_inline, text_style_fields

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

        # Apply inline styles (plus bold for the whole header row) in forward
        # index order. Each cell's final position is its original index plus the
        # total length of all earlier (lower-index) cells already inserted.
        shift = 0
        for r_idx in range(len(cell_indices)):
            row = cell_indices[r_idx]
            for c_idx in range(len(row)):
                plain, cell_styles = parsed_cells[(r_idx, c_idx)]
                cell_styles = list(cell_styles)
                if r_idx == 0 and plain:
                    cell_styles.append(StyleRange(
                        0, len(plain), {"bold": True}, "text_style",
                    ))
                base = row[c_idx] + shift
                for s in cell_styles:
                    style_range = {
                        "startIndex": base + s.start,
                        "endIndex": base + s.end,
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
                shift += len(plain)

        if text_requests:
            service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": text_requests},
            ).execute()

    except HttpError as e:
        _translate_http_error(e, doc_id)


def list_inline_objects(doc_id: str) -> list[dict]:
    """List all inline and positioned objects in a document.

    Walks body.content for inlineObjectElement and positionedObjectId
    references, joins with document.inlineObjects and positionedObjects
    maps, and classifies each object.

    Returns list of dicts with id, type, title, description, dimensions,
    content_uri, source_uri, start_index, and chart metadata.
    """
    try:
        doc = get_document(doc_id)
    except GdocError:
        raise

    inline_map = doc.get("inlineObjects", {})
    positioned_map = doc.get("positionedObjects", {})

    # Walk body.content to find references and their startIndex
    refs: list[tuple[str, int, str]] = []  # (object_id, start_index, source)
    body = doc.get("body", {})
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

    results = []
    seen = set()

    for obj_id, start_index, source in refs:
        if obj_id in seen:
            continue
        seen.add(obj_id)

        if source == "inline":
            obj_data = inline_map.get(obj_id, {})
        else:
            obj_data = positioned_map.get(obj_id, {})

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

        results.append(entry)

    return results


def download_image(content_uri: str, dest_path: str) -> None:
    """Download an image from a pre-signed content URI to a local file."""
    import urllib.request

    with urllib.request.urlopen(content_uri) as resp:
        data = resp.read()
    with open(dest_path, "wb") as f:
        f.write(data)


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
    HttpError is translated via _translate_http_error.
    """
    try:
        service = get_docs_service()
        return (
            service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )
    except HttpError as e:
        _translate_http_error(e, doc_id)


def add_tab(doc_id: str, title: str) -> dict:
    """Add a new tab to a document.

    Returns dict with 'tabId', 'title', 'index'.
    """
    service = get_docs_service()
    try:
        resp = service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"addDocumentTab": {
                "tabProperties": {"title": title},
            }}]},
        ).execute()
        try:
            props = resp["replies"][0]["addDocumentTab"]["tabProperties"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GdocError(
                f"Unexpected API response for addDocumentTab: {exc}",
            )
        return {
            "tabId": props["tabId"],
            "title": props.get("title", title),
            "index": props.get("index", 0),
        }
    except HttpError as e:
        _translate_http_error(e, doc_id)


def _build_cleanup_requests(
    body: dict, position: int, tab_id: str | None = None,
) -> list[dict]:
    """Build batchUpdate requests to clean up an empty heading paragraph.

    Pure function \u2014 inspects body content and returns request dicts
    without making API calls. When the deleted text was the entire
    content of a heading paragraph, an empty "\\n" with the heading
    style remains. This returns requests that transfer that style to
    the preceding paragraph (if NORMAL_TEXT) and delete the empty one.
    """
    target_elem = None
    prev_elem = None
    for elem in body.get("content", []):
        si = elem.get("startIndex", 0)
        if si == position and "paragraph" in elem:
            target_elem = elem
            break
        if "paragraph" in elem:
            prev_elem = elem

    if target_elem is None:
        return []

    p = target_elem["paragraph"]
    style = p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")

    # Only act on empty paragraphs with a non-NORMAL_TEXT style
    content = ""
    for e in p.get("elements", []):
        if "textRun" in e:
            content += e["textRun"]["content"]
    if content != "\n" or style == "NORMAL_TEXT":
        return []

    requests: list[dict] = []

    # Transfer the heading style to the preceding paragraph if it's
    # NORMAL_TEXT (i.e. the last paragraph of the inserted text).
    if prev_elem is not None:
        prev_style = prev_elem["paragraph"].get(
            "paragraphStyle", {},
        ).get("namedStyleType", "NORMAL_TEXT")
        if prev_style == "NORMAL_TEXT":
            prev_range: dict = {
                "startIndex": prev_elem.get("startIndex", 0),
                "endIndex": prev_elem.get("endIndex", 0),
            }
            if tab_id:
                prev_range["tabId"] = tab_id
            requests.append({
                "updateParagraphStyle": {
                    "range": prev_range,
                    "paragraphStyle": {"namedStyleType": style},
                    "fields": "namedStyleType",
                }
            })

    # Delete the empty heading paragraph
    delete_range: dict = {
        "startIndex": position,
        "endIndex": position + 1,
    }
    if tab_id:
        delete_range["tabId"] = tab_id
    requests.append({
        "deleteContentRange": {"range": delete_range}
    })

    return requests


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


def insert_markdown_into_tab(
    doc_id: str,
    tab_name: str,
    markdown: str,
    position: str = "start",
    replace: bool = False,
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

    Returns:
        Dict with "tab_id", "tab_title", "insert_index".
    """
    from gdoc.mdparse import parse_markdown, to_docs_requests

    doc = get_document_with_tabs(doc_id)
    revision_id = doc.get("revisionId", "")
    tabs = flatten_tabs(doc.get("tabs", []))
    tab_match = resolve_tab(tabs, tab_name)
    tab_id = tab_match["id"]
    body = tab_match["body"]

    body_start, body_end = _tab_body_range(body)

    if replace:
        insert_index = body_start
    elif position == "end":
        insert_index = body_end
    else:
        insert_index = body_start

    parsed = parse_markdown(markdown)

    _strip_trailing_newline_unless_hr(parsed)

    requests: list[dict] = []

    if replace and body_end > body_start:
        delete_range = {
            "startIndex": body_start,
            "endIndex": body_end,
            "tabId": tab_id,
        }
        requests.append({"deleteContentRange": {"range": delete_range}})

    requests.extend(
        to_docs_requests(parsed, insert_index, tab_id=tab_id)
    )

    if requests:
        try:
            service = get_docs_service()
            service.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": requests,
                    "writeControl": {"requiredRevisionId": revision_id},
                },
            ).execute()
        except HttpError as e:
            _translate_http_error(e, doc_id)

    if parsed.tables:
        for table in reversed(parsed.tables):
            # Subtract leading list-indent tabs that createParagraphBullets
            # removed before this table, shifting its real position left.
            _insert_table(
                doc_id,
                insert_index + table.plain_text_offset
                - table.removed_tabs_before,
                table,
                tab_id=tab_id,
            )

    return {
        "tab_id": tab_id,
        "tab_title": tab_match["title"],
        "insert_index": insert_index,
    }


def replace_formatted(
    doc_id: str,
    matches: list[dict],
    new_markdown: str,
    revision_id: str,
    tab_id: str | None = None,
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

    Returns:
        Number of replacements made.
    """
    from gdoc.mdparse import parse_markdown, to_docs_requests

    parsed = parse_markdown(new_markdown)

    _strip_trailing_newline_unless_hr(parsed)

    # Sort matches by startIndex descending (last-to-first)
    sorted_matches = sorted(
        matches, key=lambda m: m["startIndex"], reverse=True,
    )

    all_requests: list[dict] = []

    for match in sorted_matches:
        # Delete the matched range (skip empty ranges \u2014 Docs API rejects
        # them with "The range should not be empty", and a zero-width
        # match is a pure insert).
        if match["endIndex"] > match["startIndex"]:
            delete_range = {
                "startIndex": match["startIndex"],
                "endIndex": match["endIndex"],
            }
            if tab_id:
                delete_range["tabId"] = tab_id
            all_requests.append({
                "deleteContentRange": {
                    "range": delete_range,
                }
            })

        # Insert formatted replacement
        insert_requests = to_docs_requests(
            parsed, match["startIndex"], tab_id=tab_id,
        )
        all_requests.extend(insert_requests)

    if not all_requests:
        return 0

    try:
        service = get_docs_service()
        body = {
            "requests": all_requests,
            "writeControl": {"requiredRevisionId": revision_id},
        }
        service.documents().batchUpdate(
            documentId=doc_id, body=body,
        ).execute()

        # Clean up leftover heading paragraphs (before table insertion
        # so indices haven't shifted from table expansion).
        # Fetch document once, compute all cleanup requests, then
        # execute in a single batchUpdate.
        if tab_id:
            doc = get_document_with_tabs(doc_id)
            tabs = flatten_tabs(doc.get("tabs", []))
            tab_match = resolve_tab(tabs, tab_id)
            fetch_body = tab_match["body"]
        else:
            doc = service.documents().get(documentId=doc_id).execute()
            fetch_body = doc.get("body", {})

        all_cleanup: list[dict] = []
        n = len(sorted_matches)
        match_len = (
            sorted_matches[0]["endIndex"] - sorted_matches[0]["startIndex"]
            if sorted_matches else 0
        )
        # createParagraphBullets removes the nested-list indent tabs during
        # the main batch, so each match grows the doc by the post-removal
        # length, not len(plain_text).
        effective_len = len(parsed.plain_text) - parsed.removed_tabs
        delta = effective_len - match_len
        # Matches are sorted descending by startIndex; iterate in
        # that same order so higher positions are cleaned first.
        # Within one batchUpdate, deletions at higher indices
        # don't affect lower indices, so no cross-cleanup shift.
        for j, match in enumerate(sorted_matches):
            # (n-1-j) matches below this one each shifted content
            # by `delta` chars during the main replacement.
            adjusted_pos = (
                match["startIndex"]
                + effective_len
                + (n - 1 - j) * delta
            )
            reqs = _build_cleanup_requests(fetch_body, adjusted_pos, tab_id)
            all_cleanup.extend(reqs)

        if all_cleanup:
            service.documents().batchUpdate(
                documentId=doc_id, body={"requests": all_cleanup},
            ).execute()

        # Insert tables if any (after main batchUpdate + cleanup)
        if parsed.tables:
            for table in reversed(parsed.tables):
                for j, match in enumerate(sorted_matches):
                    shift = (n - 1 - j) * delta
                    idx = (
                        match["startIndex"] + table.plain_text_offset
                        - table.removed_tabs_before + shift
                    )
                    _insert_table(doc_id, idx, table, tab_id=tab_id)

        return len(sorted_matches)
    except HttpError as e:
        _translate_http_error(e, doc_id)
