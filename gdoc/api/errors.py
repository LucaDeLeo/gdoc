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

# Google's message: "<API title> has not been used in project N before or it
# is disabled. Enable it by visiting <url> then retry. ..."
_TITLE_RE = re.compile(r"^\s*(.+?) has not been used in project\b")
_URL_RE = re.compile(r"https://console\.(?:developers|cloud)\.google\.com/\S+")


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


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


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
    details = _dicts(error.get("details"))
    infos = [d for d in details if d.get("reason") in _DISABLED_REASONS]
    legacy = [d for d in _dicts(error.get("errors"))
              if d.get("reason") in _DISABLED_REASONS]
    if not infos and not legacy:
        return None

    metadata = infos[0].get("metadata") if infos else None
    metadata = metadata if isinstance(metadata, dict) else {}
    message = error.get("message")
    message = message if isinstance(message, str) else ""

    title = metadata.get("serviceTitle")
    if not isinstance(title, str) or not title.strip():
        match = _TITLE_RE.match(message)
        title = match.group(1) if match else ""
    url = metadata.get("activationUrl")
    if not isinstance(url, str) or not url.strip():
        match = _URL_RE.search(message)
        url = match.group(0).rstrip(".,;)") if match else ""

    what = (
        f"{title.strip()} is" if title.strip()
        else "A Google API this command needs is"
    )
    enable = (
        f"Enable it: {url.strip()}" if url.strip()
        else "Enable it in the Google Cloud Console under APIs & Services"
    )
    return (
        f"{what} disabled for this OAuth client's Cloud project. {enable}"
    )


def raise_if_service_disabled(content) -> None:
    """Raise GdocError (exit 1) when ``content`` reports a disabled API.

    Not AuthError: re-running `gdoc auth` doesn't enable an API.
    """
    message = service_disabled_message(content)
    if message:
        raise GdocError(message)
