"""Drive API service factory."""

from functools import lru_cache

from googleapiclient.discovery import build


@lru_cache(maxsize=1)
def get_drive_service():
    """Build and cache a Drive API v3 service object.

    Uses lru_cache to ensure a single service instance per CLI invocation.
    Lazy-imports get_credentials to avoid import errors when Google libraries
    are not available (e.g., during ``gdoc --help``).
    """
    from gdoc.auth import get_credentials

    creds = get_credentials()
    return build("drive", "v3", credentials=creds)
