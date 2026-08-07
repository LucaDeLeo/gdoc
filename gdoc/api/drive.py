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
    return export_doc_bytes(doc_id, mime_type).decode("utf-8")


def export_doc_bytes(doc_id: str, mime_type: str) -> bytes:
    """Export a Google Docs document as raw bytes.

    Like export_doc, but without UTF-8 decoding — for binary formats
    (PDF, DOCX, ODT, EPUB) that must be written to a file as-is.
    """
    try:
        service = get_drive_service()
        return (
            service.files()
            .export_media(fileId=doc_id, mimeType=mime_type)
            .execute()
        )
    except HttpError as e:
        _translate_http_error(e, doc_id)


def list_files(query: str, all_drives: bool = False) -> list[dict]:
    """List files matching a Drive API query, auto-paginating.

    Args:
        query: Drive API query string.
        all_drives: Search the allDrives corpus (personal Drive plus
            every shared drive the user is a member of) instead of the
            default user corpus, which only covers files created by,
            opened by, or shared directly with the user. Google may
            answer broad-corpus queries with incompleteSearch=true; a
            WARN is printed to stderr when that happens.
    """
    import sys

    try:
        service = get_drive_service()
        all_files: list[dict] = []
        page_token = None
        incomplete = False

        extra: dict = {"corpora": "allDrives"} if all_drives else {}
        while True:
            response = (
                service.files()
                .list(
                    q=query,
                    fields="nextPageToken, incompleteSearch, "
                    "files(id, name, mimeType, modifiedTime, modifiedByMeTime)",
                    pageSize=100,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    **extra,
                )
                .execute()
            )
            all_files.extend(response.get("files", []))
            incomplete = incomplete or response.get("incompleteSearch", False)
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        if incomplete:
            print(
                "WARN: Drive reported an incomplete search; "
                "results may be partial",
                file=sys.stderr,
            )
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
                supportsAllDrives=True,
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
                supportsAllDrives=True,
            )
            .execute()
        )
        return int(result["version"])
    except HttpError as e:
        _translate_http_error(e, doc_id)


def get_file_version(doc_id: str) -> dict:
    """Get lightweight version metadata for pre-flight checks.

    Returns dict with keys: modifiedTime, version (int), lastModifyingUser,
    mimeType.
    """
    try:
        service = get_drive_service()
        result = (
            service.files()
            .get(
                fileId=doc_id,
                fields="modifiedTime, version, mimeType, "
                "lastModifyingUser(displayName, emailAddress)",
                supportsAllDrives=True,
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)


def create_doc_from_markdown(
    title: str,
    content: str,
    folder_id: str | None = None,
) -> dict:
    """Create a Google Doc by uploading markdown content.

    Drive auto-converts the markdown to Google Docs format.

    Args:
        title: Document title.
        content: Markdown content string.
        folder_id: Optional folder ID to place the doc in.

    Returns:
        Dict with keys: id, name, version (int), webViewLink.
    """
    import io

    from googleapiclient.http import MediaIoBaseUpload

    body: dict = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if folder_id:
        body["parents"] = [folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/markdown",
        resumable=False,
    )

    try:
        service = get_drive_service()
        result = (
            service.files()
            .create(
                body=body,
                media_body=media,
                fields="id, name, version, webViewLink",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, folder_id or "")


def upload_temp_image(file_path: str, mime_type: str) -> dict:
    """Upload a local image to Drive as a temporary file.

    Sets public read permission so the image URL can be used in
    insertInlineImage requests.

    Args:
        file_path: Path to the local image file.
        mime_type: MIME type of the image.

    Returns:
        Dict with keys: id, webContentLink.
    """
    from googleapiclient.http import MediaFileUpload

    try:
        service = get_drive_service()
        media = MediaFileUpload(file_path, mimetype=mime_type)
        result = (
            service.files()
            .create(
                body={"name": f"gdoc-temp-{id(file_path)}"},
                media_body=media,
                fields="id, webContentLink",
            )
            .execute()
        )
        # Make publicly readable for inline image insertion. If that is
        # blocked (e.g. a Workspace policy forbids anyone-sharing), the
        # caller never learns the file ID, so delete the orphan here
        # rather than leaving a gdoc-temp-* file behind on every attempt.
        try:
            service.permissions().create(
                fileId=result["id"],
                body={"type": "anyone", "role": "reader"},
            ).execute()
        except HttpError:
            try:
                service.files().delete(fileId=result["id"]).execute()
            except HttpError:
                pass
            raise
        return result
    except HttpError as e:
        _translate_http_error(e, file_path)


def delete_file(file_id: str) -> None:
    """Delete a file from Drive."""
    try:
        service = get_drive_service()
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    except HttpError as e:
        _translate_http_error(e, file_id)


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


def create_folder(title: str, parent_id: str | None = None) -> dict:
    """Create a Drive folder.

    Args:
        title: Folder name.
        parent_id: Optional parent folder ID (omitted → My Drive root).

    Returns:
        Dict with keys: id, name, webViewLink.
    """
    body: dict = {
        "name": title,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]
    try:
        service = get_drive_service()
        return (
            service.files()
            .create(
                body=body,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    except HttpError as e:
        _translate_http_error(e, parent_id or "")


def move_file(file_id: str, folder_id: str) -> dict:
    """Move a file into a folder, removing it from its current parents.

    Drive files can technically have multiple parents (legacy); a move
    replaces all of them so the file ends up in exactly one place.

    Returns:
        Dict with keys: id, name, parents, version (int).
    """
    try:
        service = get_drive_service()
        current = (
            service.files()
            .get(
                fileId=file_id,
                fields="id, name, parents, version",
                supportsAllDrives=True,
            )
            .execute()
        )
        parents = current.get("parents", [])
        # Never add and remove the destination in the same request: a
        # re-run mv, or a legacy multi-parent file that already includes
        # the destination, must keep it.
        to_remove = [p for p in parents if p != folder_id]
        kwargs: dict = {}
        if folder_id not in parents:
            kwargs["addParents"] = folder_id
        if to_remove:
            kwargs["removeParents"] = ",".join(to_remove)
        if not kwargs:
            # Already exactly in the destination — nothing to write.
            if "version" in current:
                current["version"] = int(current["version"])
            return current
        result = (
            service.files()
            .update(
                fileId=file_id,
                fields="id, name, parents, version",
                supportsAllDrives=True,
                **kwargs,
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, file_id)


def rename_file(file_id: str, title: str) -> dict:
    """Rename a Drive file.

    Returns:
        Dict with keys: id, name, version (int).
    """
    try:
        service = get_drive_service()
        result = (
            service.files()
            .update(
                fileId=file_id,
                body={"name": title},
                fields="id, name, version",
                supportsAllDrives=True,
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, file_id)


def list_shared_drives() -> list[dict]:
    """List shared drives the user can access, auto-paginating.

    Returns list of dicts with keys: id, name.
    """
    try:
        service = get_drive_service()
        all_drives: list[dict] = []
        page_token = None
        while True:
            response = (
                service.drives()
                .list(pageSize=100, pageToken=page_token)
                .execute()
            )
            all_drives.extend(response.get("drives", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break
        return all_drives
    except HttpError as e:
        _translate_http_error(e, "")


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
                supportsAllDrives=True,
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)


def create_permission(
    doc_id: str,
    email: str | None = None,
    role: str = "reader",
    domain: str | None = None,
    anyone: bool = False,
    discoverable: bool = False,
) -> dict:
    """Share a document with a user, a whole domain, or anyone with the link.

    Exactly one of email / domain / anyone selects the grantee type; the
    caller validates that. User shares send a notification email; domain
    and anyone shares are link-based, with `discoverable` controlling
    whether the file also appears in search results
    (`allowFileDiscovery` — never inferred, off by default).

    Args:
        doc_id: Document ID.
        email: Email address to share with (user grant).
        role: Permission role ('reader', 'writer', 'commenter').
        domain: Workspace domain to share with (domain grant).
        anyone: Share with anyone who has the link.
        discoverable: Let the file surface in search (domain/anyone only).

    Returns:
        Permission resource dict from the API.
    """
    kwargs: dict = {}
    if email:
        body: dict = {"type": "user", "role": role, "emailAddress": email}
        kwargs["sendNotificationEmail"] = True
    elif domain:
        body = {
            "type": "domain",
            "role": role,
            "domain": domain,
            "allowFileDiscovery": discoverable,
        }
    elif anyone:
        body = {
            "type": "anyone",
            "role": role,
            "allowFileDiscovery": discoverable,
        }
    else:
        raise GdocError("share target required (email, domain, or anyone)")
    try:
        service = get_drive_service()
        result = (
            service.permissions()
            .create(
                fileId=doc_id,
                body=body,
                supportsAllDrives=True,
                **kwargs,
            )
            .execute()
        )
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)
