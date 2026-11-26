"""Shared classification of Google API error bodies.

Google reports a disabled API (one not enabled in the OAuth client's Cloud
project) as HTTP 403, the same status as a sharing problem. Translating that
into "Permission denied" sends users and agents chasing file access when the
fix is one click in the Cloud Console, so every 403 handler checks for it
first.
"""

import json
import re
import unicodedata

from gdoc.util import GdocError

# ErrorInfo reason (google.rpc) and the legacy v1 `errors[].reason`.
_DISABLED_REASONS = frozenset({"SERVICE_DISABLED", "accessNotConfigured"})

# Google's message: "[Access Not Configured. ]<API title> has not been used
# in project N before or it is disabled. Enable it by visiting <url> then
# retry. ..."
_TITLE_RE = re.compile(
    r"^(?:Access Not Configured\.\s+)?(.+?) has not been used in project\b"
)
# URL-safe characters only, so a printed link can't carry shell syntax.
_URL_RE = re.compile(
    r"https://console\.(?:developers|cloud)\.google\.com/[A-Za-z0-9\-._~/?#&=%:+@]+"
)

_PROPAGATION_NOTE = "(if you just enabled it, wait a few minutes and retry)"


def _error_object(content) -> dict | None:
    """The `error` object of a Google JSON error body, or None."""
    if isinstance(content, (bytes, bytearray)):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    if isinstance(content, str):
        try:
            content = json.loads(content.lstrip("\ufeff"))
        except (ValueError, RecursionError):
            return None
    if isinstance(content, list) and content:
        content = content[0]
    if not isinstance(content, dict):
        return None
    error = content.get("error")
    return error if isinstance(error, dict) else None


def _disabled_entries(value) -> list[dict]:
    """Entries of a details/errors list whose reason marks a disabled API."""
    if not isinstance(value, list):
        return []
    return [
        v for v in value
        if isinstance(v, dict)
        and isinstance(v.get("reason"), str)
        and v["reason"] in _DISABLED_REASONS
    ]


def _other_reasons(value) -> bool:
    """True when a details/errors list names a non-disabled reason."""
    return isinstance(value, list) and any(
        isinstance(v, dict)
        and isinstance(v.get("reason"), str)
        and v["reason"].strip()
        and v["reason"] not in _DISABLED_REASONS
        for v in value
    )


def _text(value) -> str:
    """A printable string field with whitespace collapsed."""
    if not isinstance(value, str):
        return ""
    # Drop controls, format characters (bidi overrides, zero-width) and
    # lone surrogates, which could reorder or hide text in a terminal.
    printable = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Cs") else c
        for c in value
    )
    return " ".join(printable.split())


def _console_url(value: str) -> str:
    """``value`` if it is a Cloud Console link, else ""."""
    match = _URL_RE.fullmatch(value)
    return match.group(0).rstrip(".,;)") if match else ""


def service_disabled_message(content) -> str | None:
    """A message naming the disabled API and its enable link, or None.

    ``content`` is the raw error body (bytes, str or parsed JSON). Detection
    uses only the structured reason (``SERVICE_DISABLED`` in ``details[]`` or
    ``accessNotConfigured`` in ``errors[]``), so no other 403 changes; the
    message text only fills in the API name and link when metadata lacks
    them. Never raises: an unreadable body is simply not a disabled API.
    """
    error = _error_object(content)
    if error is None:
        return None
    infos = _disabled_entries(error.get("details"))
    if not infos:
        # The legacy reason counts only when nothing names another cause.
        if not _disabled_entries(error.get("errors")):
            return None
        if _other_reasons(error.get("details")) or _other_reasons(
            error.get("errors")
        ):
            return None

    metadatas = [i["metadata"] for i in infos if isinstance(i.get("metadata"), dict)]
    message = _text(error.get("message"))

    def first(key: str) -> str:
        return next((t for m in metadatas if (t := _text(m.get(key)))), "")

    title = first("serviceTitle")
    if not title:
        match = _TITLE_RE.search(message)
        title = match.group(1).strip() if match else first("service")
    url = _console_url(first("activationUrl"))
    if not url:
        match = _URL_RE.search(message)
        url = _console_url(match.group(0)) if match else ""

    what = f"{title} is" if title else "A Google API this command needs is"
    enable = (
        f"Enable it: {url}" if url
        else "Enable it in the Google Cloud Console under APIs & Services"
    )
    # Keep Google's own words when they couldn't be parsed.
    said = (
        f" (Google said: {message.rstrip('.')})"
        if message and not (title and url) else ""
    )
    # The note comes last so callers that wrap the message in parentheses
    # or end it with a period don't glue punctuation onto a link.
    return (
        f"{what} disabled for this OAuth client's Cloud project{said}. "
        f"{enable} {_PROPAGATION_NOTE}"
    )


def raise_if_service_disabled(content) -> None:
    """Raise GdocError (exit 1) when ``content`` reports a disabled API.

    Not AuthError: re-running `gdoc auth` doesn't enable an API.
    """
    message = service_disabled_message(content)
    if message:
        raise GdocError(message)
