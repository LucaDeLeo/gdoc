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


def update_state_after_command(
    doc_id: str,
    change_info,  # ChangeInfo | None (from pre_flight)
    command: str,
    quiet: bool = False,
    command_version: int | None = None,
) -> None:
    """Update per-doc state after a successful command.

    Args:
        doc_id: The document ID.
        change_info: ChangeInfo from pre_flight, or None if --quiet.
        command: The command name (e.g., "cat", "info", "edit").
        quiet: Whether --quiet was passed.
        command_version: Version from command's own API response (for info command).
    """
    from datetime import datetime, timezone

    state = load_state(doc_id) or DocState()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    state.last_seen = now

    is_read = command in ("cat", "info")

    if quiet:
        # Decision #14: --quiet state update rules
        if command == "info" and command_version is not None:
            state.last_version = command_version
            state.last_read_version = command_version
    elif change_info is not None:
        # Normal (non-quiet) run: update from pre-flight data
        if change_info.current_version is not None:
            state.last_version = change_info.current_version
            if is_read:
                state.last_read_version = change_info.current_version

        # Advance last_comment_check to pre-request timestamp (Decision #12)
        if change_info.preflight_timestamp:
            state.last_comment_check = change_info.preflight_timestamp

        # Update comment ID sets
        if change_info.all_comment_ids:
            state.known_comment_ids = change_info.all_comment_ids
        if change_info.all_resolved_ids is not None:
            state.known_resolved_ids = change_info.all_resolved_ids

    # Override last_version with post-mutation version for edit/write
    # (the pre-flight version is from BEFORE the mutation; this is from AFTER)
    if command_version is not None and command not in ("cat", "info"):
        state.last_version = command_version

    save_state(doc_id, state)
