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


def list_comments(file_id: str, start_modified_time: str = "") -> list[dict]:
    """List comments on a file, auto-paginating.

    Args:
        file_id: The document ID.
        start_modified_time: ISO timestamp. Only comments modified after this
            time are returned. If empty string, all comments are returned
            (used for first interaction per CONTEXT.md Decision #3).

    Returns:
        List of comment dicts with id, content, author, resolved, modifiedTime, replies.
    """
    try:
        service = get_drive_service()
        all_comments: list[dict] = []
        page_token = None

        while True:
            params: dict = {
                "fileId": file_id,
                "includeDeleted": False,
                "includeResolved": True,
                "fields": (
                    "nextPageToken, "
                    "comments(id, content, author(displayName, emailAddress), "
                    "resolved, modifiedTime, "
                    "replies(author(displayName, emailAddress), modifiedTime, content, action))"
                ),
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

        return all_comments
    except HttpError as e:
        _translate_http_error(e, file_id)
