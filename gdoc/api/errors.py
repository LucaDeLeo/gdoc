"""Recognize a Google API that is disabled in the OAuth client's Cloud project.

Google reports a disabled API as HTTP 403, the same status as a sharing
problem. Reporting it as "Permission denied" sends users and agents chasing
file access when the fix is one click in the Cloud Console, so every 403
handler checks for it first.
"""

import json

from gdoc.util import GdocError

_NOTE = "(if you just enabled it, wait a few minutes and retry)"


def _error_object(content) -> dict | None:
    """The `error` object of a Google JSON error body, or None."""
    try:
        if isinstance(content, (bytes, bytearray)):
            content = content.decode("utf-8")
        if isinstance(content, str):
            content = json.loads(content)
    except ValueError:  # includes UnicodeDecodeError
        return None
    error = content.get("error") if isinstance(content, dict) else None
    return error if isinstance(error, dict) else None


def _with_reason(entries, reason: str) -> dict | None:
    if not isinstance(entries, list):
        return None
    return next(
        (e for e in entries if isinstance(e, dict) and e.get("reason") == reason),
        None,
    )


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def service_disabled_message(content) -> str | None:
    """Name the disabled API and its enable link, or None if not disabled.

    ``content`` is the raw error body. Detection uses only Google's
    structured reason: ``SERVICE_DISABLED`` in ``details[]`` or the legacy
    ``accessNotConfigured`` in ``errors[]``. An unparseable body is not a
    disabled API.
    """
    error = _error_object(content)
    if error is None:
        return None
    info = _with_reason(error.get("details"), "SERVICE_DISABLED")
    if info is None and not _with_reason(error.get("errors"), "accessNotConfigured"):
        return None

    metadata = info.get("metadata") if info else None
    metadata = metadata if isinstance(metadata, dict) else {}
    title = _text(metadata.get("serviceTitle"))
    url = _text(metadata.get("activationUrl"))
    message = _text(error.get("message"))
    if title and url:
        text = (f"{title} is disabled for this OAuth client's Cloud project. "
                f"Enable it: {url}")
    elif message:
        # Google's own text already names the API and has the link.
        text = ("A Google API is disabled for this OAuth client's Cloud "
                f"project. Google said: {message}")
    else:
        text = ("A Google API this command needs is disabled for this OAuth "
                "client's Cloud project. Enable it in the Google Cloud Console "
                "under APIs & Services")
    # The note comes last so a caller that wraps the message in parentheses
    # or ends it with a period doesn't glue punctuation onto the link.
    return f"{text} {_NOTE}"


def raise_if_service_disabled(content) -> None:
    """Raise GdocError (exit 1) when ``content`` reports a disabled API.

    Not AuthError: re-running `gdoc auth` doesn't enable an API.
    """
    message = service_disabled_message(content)
    if message:
        raise GdocError(message)
