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
    document: dict,
    text: str,
    match_case: bool = False,
) -> list[dict]:
    """Find all occurrences of text within the document body.

    Walks body.content → paragraph.elements → textRun.content to
    build a concatenated string with position mapping, then searches.

    Returns list of {"startIndex": int, "endIndex": int} in document
    coordinates.
    """
    # Build a mapping: (doc_index, char) for each character in the document
    chars: list[tuple[int, str]] = []

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
    document: dict, table_start_index: int,
) -> list[list[int]]:
    """Find the startIndex of each cell's first paragraph in a table.

    Walks body.content to find the table element at or near the given
    index, then extracts cell paragraph start indices. Searches for the
    nearest table at or after the index (insertTable may place the table
    one position after the specified location).

    Returns a 2D list: cell_indices[row][col] = startIndex.
    """
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
        insert_req = {
            "insertTable": {
                "rows": table.num_rows,
                "columns": table.num_cols,
                "location": {"index": index},
            }
        }
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [insert_req]},
        ).execute()

        # Step 2: Read back document to find cell positions
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
                    text_requests.append({
                        "insertText": {
                            "location": {"index": row[c_idx]},
                            "text": cell_text,
                        }
                    })

        if text_requests:
            service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": text_requests},
            ).execute()

    except HttpError as e:
        _translate_http_error(e, doc_id)


def replace_formatted(
    doc_id: str,
    matches: list[dict],
    new_markdown: str,
    revision_id: str,
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
        all_requests.append({
            "deleteContentRange": {
                "range": {
                    "startIndex": match["startIndex"],
                    "endIndex": match["endIndex"],
                }
            }
        })

        # Insert formatted replacement
        insert_requests = to_docs_requests(parsed, match["startIndex"])
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

        # Insert tables if any (after main batchUpdate)
        if parsed.tables:
            for table in reversed(parsed.tables):
                # Adjust index: placeholder is at match start +
                # table's offset within the plain text
                for match in sorted_matches:
                    idx = match["startIndex"] + table.plain_text_offset
                    _insert_table(doc_id, idx, table)

        return len(sorted_matches)
    except HttpError as e:
        _translate_http_error(e, doc_id)
