"""Google Docs API v1 wrapper functions with error translation."""

from functools import lru_cache

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gdoc.util import AuthError, GdocError


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
        if t["id"] == tab_name:
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


def find_text_in_document(
    document: dict | None,
    text: str,
    match_case: bool = False,
    body: dict | None = None,
) -> list[dict]:
    """Find all occurrences of text within the document body.

    Walks body.content → paragraph.elements → textRun.content to
    build a concatenated string with position mapping, then searches.

    Args:
        document: The full document dict (used if body is None).
        text: Text to search for.
        match_case: If True, case-sensitive matching.
        body: Optional body dict to search in (e.g. from a specific tab).

    Returns list of {"startIndex": int, "endIndex": int} in document
    coordinates.
    """
    # Build a mapping: (doc_index, char) for each character in the document
    chars: list[tuple[int, str]] = []

    if body is None:
        if document is None:
            return []
        body = document.get("body", {})
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run is None:
                continue
            content = text_run.get("content", "")
            start_idx = pe.get("startIndex", 0)
            for i, ch in enumerate(content):
                chars.append((start_idx + i, ch))

    if not chars:
        return []

    # Build the concatenated text and index map
    concat = "".join(ch for _, ch in chars)
    doc_indices = [idx for idx, _ in chars]

    search_text = text
    search_in = concat
    if not match_case:
        search_text = text.lower()
        search_in = concat.lower()

    matches = []
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

    return matches


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

        # Step 3: Insert text into cells (reverse order)
        text_requests: list[dict] = []
        for r_idx in range(len(cell_indices) - 1, -1, -1):
            row = cell_indices[r_idx]
            for c_idx in range(len(row) - 1, -1, -1):
                cell_text = ""
                if r_idx < len(table.rows):
                    row_data = table.rows[r_idx]
                    if c_idx < len(row_data):
                        cell_text = row_data[c_idx]
                if cell_text:
                    cell_location = {"index": row[c_idx]}
                    if tab_id:
                        cell_location["tabId"] = tab_id
                    text_requests.append({
                        "insertText": {
                            "location": cell_location,
                            "text": cell_text,
                        }
                    })

        # Bold the header row (row 0). Indices must account for
        # shifts from earlier columns' insertions (processed right-to-
        # left, so col 0's insert shifts col 1+).
        if cell_indices and table.rows:
            row = cell_indices[0]
            shift = 0
            for c_idx in range(len(row)):
                cell_text = ""
                if c_idx < len(table.rows[0]):
                    cell_text = table.rows[0][c_idx]
                if cell_text:
                    bold_range = {
                        "startIndex": row[c_idx] + shift,
                        "endIndex": row[c_idx] + shift + len(cell_text),
                    }
                    if tab_id:
                        bold_range["tabId"] = tab_id
                    text_requests.append({
                        "updateTextStyle": {
                            "range": bold_range,
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    })
                shift += len(cell_text)

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

    Pure function — inspects body content and returns request dicts
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

    # Sort matches by startIndex descending (last-to-first)
    sorted_matches = sorted(
        matches, key=lambda m: m["startIndex"], reverse=True,
    )

    all_requests: list[dict] = []

    for match in sorted_matches:
        # Delete the matched range
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
        delta = len(parsed.plain_text) - match_len
        # Matches are sorted descending by startIndex; iterate in
        # that same order so higher positions are cleaned first.
        # Within one batchUpdate, deletions at higher indices
        # don't affect lower indices, so no cross-cleanup shift.
        for j, match in enumerate(sorted_matches):
            # (n-1-j) matches below this one each shifted content
            # by `delta` chars during the main replacement.
            adjusted_pos = (
                match["startIndex"]
                + len(parsed.plain_text)
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
                    idx = match["startIndex"] + table.plain_text_offset + shift
                    _insert_table(doc_id, idx, table, tab_id=tab_id)

        return len(sorted_matches)
    except HttpError as e:
        _translate_http_error(e, doc_id)
