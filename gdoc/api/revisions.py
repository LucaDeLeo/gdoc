"""Drive API revisions wrappers (milestone revision history).

Google Docs' rich "Version history" UI has no public API, but the Drive
``revisions`` collection returns milestone revisions for native Docs,
and each milestone is exportable via authenticated ``exportLinks``.
Revision ids are sparse and non-``keepForever`` revisions are pruned by
Google over time (~weeks).
"""

import sys
from functools import lru_cache

from googleapiclient.errors import HttpError

from gdoc.api import get_drive_service
from gdoc.revdiff import pruned_error
from gdoc.util import AuthError, GdocError

_REVISION_FIELDS = (
    "id, modifiedTime, keepForever, "
    "lastModifyingUser(displayName, emailAddress), exportLinks"
)

_EXPORT_TIMEOUT = 30  # seconds


def _translate_http_error(e: HttpError, file_id: str) -> None:
    """Translate HttpError for revisions operations."""
    status = int(e.resp.status)
    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    if status == 403:
        raise GdocError(f"Permission denied: {file_id}")
    if status == 404:
        raise GdocError(f"Document not found: {file_id}")
    raise GdocError(f"API error ({status}): {e.reason}")


@lru_cache(maxsize=1)
def _get_session():
    """One authorized HTTP session per process (exportLinks downloads)."""
    from google.auth.transport.requests import AuthorizedSession

    from gdoc.auth import get_credentials

    return AuthorizedSession(get_credentials())


def list_revisions(file_id: str) -> list[dict]:
    """List retained revisions for a file, oldest first, auto-paginating.

    Returns revision dicts with id, modifiedTime, keepForever,
    lastModifyingUser, and exportLinks.
    """
    try:
        service = get_drive_service()
        revisions: list[dict] = []
        page_token = None

        while True:
            response = (
                service.revisions()
                .list(
                    fileId=file_id,
                    pageSize=1000,
                    fields=f"nextPageToken, revisions({_REVISION_FIELDS})",
                    pageToken=page_token,
                )
                .execute()
            )
            revisions.extend(response.get("revisions", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        # Drive doesn't document an ordering guarantee; the selector
        # grammar (latest/prev/head~N/@ISO) depends on oldest-first.
        revisions.sort(key=lambda r: r.get("modifiedTime", ""))
        return revisions
    except HttpError as e:
        _translate_http_error(e, file_id)


def export_revision(
    file_id: str,
    revision_id: str,
    mime_type: str = "text/markdown",
    export_links: dict | None = None,
) -> str:
    """Export one revision's content via its exportLinks.

    The Drive API has no export endpoint for revisions of native Docs;
    each revision instead carries authenticated exportLinks URLs. Falls
    back to text/plain when the requested mime type has no link.

    Args:
        file_id: The document ID.
        revision_id: The revision ID to export.
        mime_type: Preferred export format.
        export_links: exportLinks dict from a prior revisions.list call;
            when omitted, fetched via revisions.get.
    """
    if export_links is None:
        try:
            service = get_drive_service()
            result = (
                service.revisions()
                .get(
                    fileId=file_id,
                    revisionId=revision_id,
                    fields="exportLinks",
                )
                .execute()
            )
            export_links = result.get("exportLinks", {})
        except HttpError as e:
            if int(e.resp.status) == 404:
                raise pruned_error(revision_id) from e
            _translate_http_error(e, file_id)

    url = export_links.get(mime_type)
    if not url:
        url = export_links.get("text/plain")
        if url and mime_type != "text/plain":
            print(
                f"WARN: revision {revision_id} has no {mime_type} "
                "export; falling back to text/plain",
                file=sys.stderr,
            )
    if not url:
        raise GdocError(
            f"revision {revision_id} has no {mime_type} export link"
        )

    response = _get_session().get(url, timeout=_EXPORT_TIMEOUT)
    if response.status_code == 404:
        raise pruned_error(revision_id)
    if response.status_code == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    if response.status_code == 403:
        # Not an expired token: the session auto-refreshes, so a 403
        # here means export/download is denied for this file.
        raise GdocError(f"Permission denied: {file_id}")
    if response.status_code != 200:
        raise GdocError(
            f"export failed for revision {revision_id} "
            f"(HTTP {response.status_code})"
        )
    response.encoding = "utf-8"
    return response.text
