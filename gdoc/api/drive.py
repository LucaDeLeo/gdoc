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


def search_files(query: str, title_only: bool = False) -> list[dict]:
    """Search for files by name or full-text content.

    Escapes special characters in the query before embedding in the
    Drive API query string.

    Args:
        query: Search term.
        title_only: When True, only match against the file name.
    """
    escaped = _escape_query_value(query)
    if title_only:
        drive_query = f"name contains '{escaped}' and trashed=false"
    else:
        drive_query = (
            f"(name contains '{escaped}' or fullText contains '{escaped}') "
            f"and trashed=false"
        )
    return list_files(drive_query)


def get_file_info(doc_id: str) -> dict:
    """Get metadata for a single file."""
    try:
        service = get_drive_service()
        result = (
            service.files()
            .get(
                fileId=doc_id,
                fields="id, name, mimeType, modifiedTime, createdTime, "
                "owners(emailAddress, displayName), "
                "lastModifyingUser(emailAddress, displayName), size, version",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)


def update_doc_content(doc_id: str, content: str) -> int:
    """Overwrite a Google Doc's content with markdown.

    Uploads markdown content via files.update with media, triggering
    automatic conversion to Google Docs format.

    Args:
        doc_id: The document ID.
        content: Markdown content string to upload.

    Returns:
        The new document version (int) from the API response.
    """
    import io

    from googleapiclient.http import MediaIoBaseUpload

    try:
        service = get_drive_service()
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/markdown",
            resumable=False,
        )
        result = (
            service.files()
            .update(
                fileId=doc_id,
                body={
                    "mimeType": (
                        "application/vnd.google-apps.document"
                    ),
                },
                media_body=media,
                fields="version",
            )
            .execute()
        )
        return int(result["version"])
    except HttpError as e:
        _translate_http_error(e, doc_id)


def get_file_version(doc_id: str) -> dict:
    """Get lightweight version metadata for pre-flight checks.

    Returns dict with keys: modifiedTime, version (int), lastModifyingUser.
    """
    try:
        service = get_drive_service()
        result = (
            service.files()
            .get(
                fileId=doc_id,
                fields="modifiedTime, version, lastModifyingUser(displayName, emailAddress)",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)


def create_doc(title: str, folder_id: str | None = None) -> dict:
    """Create a new blank Google Doc.

    Args:
        title: Document title.
        folder_id: Optional folder ID to place the doc in.

    Returns:
        Dict with keys: id, name, version (int), webViewLink.
    """
    body = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if folder_id:
        body["parents"] = [folder_id]
    try:
        service = get_drive_service()
        result = (
            service.files()
            .create(
                body=body,
                fields="id, name, version, webViewLink",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, folder_id or "")


def copy_doc(doc_id: str, title: str) -> dict:
    """Duplicate a Google Doc.

    Args:
        doc_id: Source document ID.
        title: Title for the copy.

    Returns:
        Dict with keys: id, name, version (int), webViewLink.
    """
    try:
        service = get_drive_service()
        result = (
            service.files()
            .copy(
                fileId=doc_id,
                body={"name": title},
                fields="id, name, version, webViewLink",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)


def create_permission(doc_id: str, email: str, role: str) -> dict:
    """Share a document with a user.

    Args:
        doc_id: Document ID.
        email: Email address to share with.
        role: Permission role ('reader', 'writer', 'commenter').

    Returns:
        Permission resource dict from the API.
    """
    try:
        service = get_drive_service()
        result = (
            service.permissions()
            .create(
                fileId=doc_id,
                body={
                    "type": "user",
                    "role": role,
                    "emailAddress": email,
                },
                sendNotificationEmail=True,
            )
            .execute()
        )
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)
