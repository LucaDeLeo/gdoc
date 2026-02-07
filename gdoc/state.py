"""Per-document state tracking for the awareness system."""

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from gdoc.util import STATE_DIR


@dataclass
class DocState:
    """Tracks last-known state of a document for change detection."""
    last_seen: str = ""                          # ISO timestamp
    last_version: int | None = None              # doc version number
    last_read_version: int | None = None         # version at last cat/info
    last_comment_check: str = ""                 # ISO timestamp for comments.list
    known_comment_ids: list[str] = field(default_factory=list)
    known_resolved_ids: list[str] = field(default_factory=list)


def _state_path(doc_id: str) -> Path:
    """Return the path to a document's state file."""
    return STATE_DIR / f"{doc_id}.json"


def load_state(doc_id: str) -> DocState | None:
    """Load state for a document. Returns None if no state exists (first interaction)."""
    path = _state_path(doc_id)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return DocState(**{k: v for k, v in data.items() if k in DocState.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def save_state(doc_id: str, state: DocState) -> None:
    """Save state atomically using temp file + rename."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(doc_id)
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(state), f)
        os.rename(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
