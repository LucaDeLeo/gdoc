"""Drive API wrapper functions with error translation."""

from googleapiclient.errors import HttpError

from gdoc.api import get_drive_service
from gdoc.util import AuthError, GdocError


def _translate_http_error(e: HttpError, file_id: str) -> None:
    """Translate a googleapiclient HttpError into GdocError or AuthError."""
    status = int(e.resp.status)

    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")

    if status == 403:
        reason = e.reason if hasattr(e, "reason") and e.reason else ""
        if "Export only supports Docs Editors files" in reason:
            raise GdocError(
                "Cannot export file as markdown: file is not a Google Docs editor document"
            )
        raise GdocError(f"Permission denied: {file_id}")

    if status == 404:
        raise GdocError(f"Document not found: {file_id}")

    raise GdocError(f"API error ({status}): {e.reason}")


def _escape_query_value(value: str) -> str:
    """Escape a value for embedding in a Drive API query string.

    Backslashes are escaped first, then single quotes, to avoid
    double-escaping.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace("'", "\\'")
    return value


def export_doc(doc_id: str, mime_type: str = "text/markdown") -> str:
    """Export a Google Docs document as the given MIME type.

    Returns the decoded UTF-8 content string.
    """
    try:
        service = get_drive_service()
        content = (
            service.files()
            .export_media(fileId=doc_id, mimeType=mime_type)
            .execute()
        )
        return content.decode("utf-8")
    except HttpError as e:
        _translate_http_error(e, doc_id)


def list_files(query: str) -> list[dict]:
    """List files matching a Drive API query, auto-paginating."""
    try:
        service = get_drive_service()
        all_files: list[dict] = []
        page_token = None

        while True:
            response = (
                service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageSize=100,
                    pageToken=page_token,
                )
                .execute()
            )
            all_files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        return all_files
    except HttpError as e:
        _translate_http_error(e, "")


def search_files(query: str) -> list[dict]:
    """Search for files by name or full-text content.

    Escapes special characters in the query before embedding in the
    Drive API query string.
    """
    escaped = _escape_query_value(query)
    drive_query = (
        f"(name contains '{escaped}' or fullText contains '{escaped}') "
        f"and trashed=false"
    )
    return list_files(drive_query)


def get_file_info(doc_id: str) -> dict:
    """Get metadata for a single file."""
    try:
        service = get_drive_service()
        return (
            service.files()
            .get(
                fileId=doc_id,
                fields="id, name, mimeType, modifiedTime, createdTime, "
                "owners(emailAddress, displayName), "
                "lastModifyingUser(emailAddress, displayName), size",
            )
            .execute()
        )
    except HttpError as e:
        _translate_http_error(e, doc_id)
