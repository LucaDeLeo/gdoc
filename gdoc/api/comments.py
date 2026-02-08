"""Comments API wrapper functions (Drive API v3)."""

from googleapiclient.errors import HttpError

from gdoc.api import get_drive_service
from gdoc.util import AuthError, GdocError


def _translate_http_error(e: HttpError, file_id: str) -> None:
    """Translate HttpError for comments operations."""
    status = int(e.resp.status)
    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    if status == 403:
        raise GdocError(f"Permission denied: {file_id}")
    if status == 404:
        raise GdocError(f"Document not found: {file_id}")
    raise GdocError(f"API error ({status}): {e.reason}")


def list_comments(
    file_id: str,
    start_modified_time: str = "",
    include_resolved: bool = True,
    include_anchor: bool = False,
) -> list[dict]:
    """List comments on a file, auto-paginating.

    Args:
        file_id: The document ID.
        start_modified_time: ISO timestamp. Only comments modified after this
            time are returned. If empty string, all comments are returned
            (used for first interaction per CONTEXT.md Decision #3).
        include_resolved: If False, resolved comments are filtered out
            client-side after fetching.
        include_anchor: If True, includes quotedFileContent(value) in the
            response fields (needed for cat --comments anchor mapping).

    Returns:
        List of comment dicts with id, content, author, resolved, modifiedTime, replies.
    """
    try:
        service = get_drive_service()
        all_comments: list[dict] = []
        page_token = None

        while True:
            # Build fields string
            comment_fields = (
                "id, content, author(displayName, emailAddress), "
                "resolved, createdTime, modifiedTime, "
                "replies(author(displayName, emailAddress), modifiedTime, content, action)"
            )
            if include_anchor:
                comment_fields = (
                    "id, content, author(displayName, emailAddress), "
                    "resolved, createdTime, modifiedTime, "
                    "quotedFileContent(value), "
                    "replies(author(displayName, emailAddress), modifiedTime, content, action)"
                )
            fields = f"nextPageToken, comments({comment_fields})"

            params: dict = {
                "fileId": file_id,
                "includeDeleted": False,
                "fields": fields,
                "pageSize": 100,
            }
            if start_modified_time:
                params["startModifiedTime"] = start_modified_time
            if page_token:
                params["pageToken"] = page_token

            response = service.comments().list(**params).execute()
            all_comments.extend(response.get("comments", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        # Client-side resolved filtering
        if not include_resolved:
            all_comments = [c for c in all_comments if not c.get("resolved", False)]

        return all_comments
    except HttpError as e:
        _translate_http_error(e, file_id)


def delete_comment(file_id: str, comment_id: str) -> None:
    """Delete a comment from a file."""
    try:
        service = get_drive_service()
        service.comments().delete(
            fileId=file_id, commentId=comment_id,
        ).execute()
    except HttpError as e:
        _translate_http_error(e, file_id)


def create_comment(file_id: str, content: str) -> dict:
    """Create an unanchored comment on a file.

    Args:
        file_id: The document ID.
        content: The comment text.

    Returns:
        Comment dict with id, content, author, createdTime, resolved.
    """
    try:
        service = get_drive_service()
        result = service.comments().create(
            fileId=file_id,
            body={"content": content},
            fields="id, content, author(displayName, emailAddress), createdTime, resolved",
        ).execute()
        return result
    except HttpError as e:
        _translate_http_error(e, file_id)


def create_reply(
    file_id: str, comment_id: str, content: str = "", action: str = "",
) -> dict:
    """Create a reply on a comment.

    Args:
        file_id: The document ID.
        comment_id: The comment ID to reply to.
        content: Reply text (required when no action).
        action: Action to perform ("resolve" or "reopen").

    Returns:
        Reply dict with id, content, action, author, createdTime.
    """
    try:
        service = get_drive_service()
        body: dict = {}
        if content:
            body["content"] = content
        if action:
            body["action"] = action
        result = service.replies().create(
            fileId=file_id,
            commentId=comment_id,
            body=body,
            fields="id, content, action, author(displayName, emailAddress), createdTime",
        ).execute()
        return result
    except HttpError as e:
        _translate_http_error(e, file_id)
