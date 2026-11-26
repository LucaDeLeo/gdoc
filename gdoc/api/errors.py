"""Shared classification of Google API error bodies.

Google reports a disabled API (one not enabled in the OAuth client's Cloud
project) as HTTP 403, the same status as a sharing problem. Translating that
into "Permission denied" sends users and agents chasing file access when the
fix is one click in the Cloud Console, so every 403 handler checks for it
first.
"""

import json
import re

from gdoc.util import GdocError

# ErrorInfo reason (google.rpc) and the legacy v1 `errors[].reason`.
_DISABLED_REASONS = frozenset({"SERVICE_DISABLED", "accessNotConfigured"})

# Google's message: "[Access Not Configured. ]<API title> has not been used
# in project N before or it is disabled. Enable it by visiting <url> then
# retry. ..." The title is the sentence ending at "has not been used".
_TITLE_RE = re.compile(r"(?:^|\.\s+)([^.]+?) has not been used in project\b")
_URL_RE = re.compile(r"https://console\.(?:developers|cloud)\.google\.com/\S+")

_PROPAGATION_NOTE = "(if you just enabled it, wait a few minutes and retry)"


def _error_object(content) -> dict | None:
    """The `error` object of a Google JSON error body, or None."""
    if isinstance(content, (bytes, bytearray)):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
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


def _text(value) -> str:
    """A string field with whitespace (including newlines) collapsed."""
    return " ".join(value.split()) if isinstance(value, str) else ""


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
    if not infos and not _disabled_entries(error.get("errors")):
        return None

    metadatas = [i["metadata"] for i in infos if isinstance(i.get("metadata"), dict)]
    message = _text(error.get("message"))

    def first(key: str) -> str:
        return next((t for m in metadatas if (t := _text(m.get(key)))), "")

    title = first("serviceTitle")
    if not title:
        match = _TITLE_RE.search(message)
        title = match.group(1).strip() if match else first("service")
    url = first("activationUrl")
    if not url.startswith("https://"):
        match = _URL_RE.search(message)
        url = match.group(0).rstrip(".,;)") if match else ""

    what = f"{title} is" if title else "A Google API this command needs is"
    enable = (
        f"Enable it: {url}" if url
        else "Enable it in the Google Cloud Console under APIs & Services"
    )
    # The URL is never last, so callers that wrap the message in
    # parentheses or end it with a period don't glue punctuation onto it.
    return (
        f"{what} disabled for this OAuth client's Cloud project. {enable} "
        f"{_PROPAGATION_NOTE}"
    )


def raise_if_service_disabled(content) -> None:
    """Raise GdocError (exit 1) when ``content`` reports a disabled API.

    Not AuthError: re-running `gdoc auth` doesn't enable an API.
    """
    message = service_disabled_message(content)
    if message:
        raise GdocError(message)
