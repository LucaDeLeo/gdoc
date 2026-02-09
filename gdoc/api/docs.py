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

    # Sort matches by startIndex descending (last-to-first)
    sorted_matches = sorted(matches, key=lambda m: m["startIndex"], reverse=True)

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

        # Parse and insert formatted replacement
        parsed = parse_markdown(new_markdown)
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
        return len(sorted_matches)
    except HttpError as e:
        _translate_http_error(e, doc_id)
