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
    # Resolved tab id -> doc version at the last full read/write of that tab.
    # The write-conflict baseline for a tab-scoped write is per-tab (see
    # update_state_after_command). Old state files load with an empty dict.
    tab_read_versions: dict[str, int] = field(default_factory=dict)
    # Provenance for last_read_version: True when it was written by gdoc
    # >= 0.22, whose tab-scoped reads no longer touch the global baseline.
    # Pre-0.22 `cat --tab A` stored its version in last_read_version, so a
    # legacy global baseline is ambiguous and must not authorize a
    # tab-scoped write — legacy files load as False and the tab guard
    # fails closed (fresh `cat` required). A downgrade round-trip strips
    # the field (old binaries drop unknown keys on save), re-entering the
    # same fail-closed state.
    global_read_covers_doc: bool = False


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
    comment_state_patch: dict | None = None,
    full_doc_write: bool = False,
    metadata_only_write: bool = False,
    read_tab_id: str | None = None,
    written_tab_id: str | None = None,
) -> None:
    """Update per-doc state after a successful command.

    Args:
        doc_id: The document ID.
        change_info: ChangeInfo from pre_flight, or None if --quiet.
        command: The command name (e.g., "cat", "info", "edit").
        quiet: Whether --quiet was passed.
        command_version: Version from command's own API response (for info command).
        comment_state_patch: Optional dict with targeted comment state mutations.
            Keys: "add_comment_id", "add_resolved_id", "remove_resolved_id".
        full_doc_write: True when the command replaced the entire document
            content, so the write doubles as a read of the whole doc.
        metadata_only_write: True when the command bumped the version
            without touching content (mv/rename). If the read baseline was
            current at pre-flight, it is carried forward past our own
            version bump so the next content write doesn't see a phantom
            conflict.
        read_tab_id: Set for a tab-scoped read (`cat --tab X`). Stamps that
            tab's read baseline in tab_read_versions instead of advancing the
            whole-doc last_read_version — a single-tab read is not a full read.
        written_tab_id: Set for a tab-scoped write (`write --tab X`). Stamps
            that tab's baseline to the post-write version so the writer's own
            output doesn't false-conflict a later write to the same tab.
    """
    from datetime import datetime, timezone

    state = load_state(doc_id) or DocState()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    state.last_seen = now

    # A tab-scoped read is NOT a whole-doc read: it stamps only that tab's
    # baseline, never the doc-global last_read_version.
    is_read = (
        command in ("cat", "info", "pull", "export", "structure")
        and read_tab_id is None
    )

    if quiet:
        # Decision #14: --quiet state update rules
        if command == "info" and command_version is not None:
            state.last_version = command_version
            # See the non-quiet info branch below: a version bump seen
            # only through `info` voids content provenance.
            if command_version != state.last_read_version:
                state.global_read_covers_doc = False
            state.last_read_version = command_version
    elif change_info is not None:
        # Normal (non-quiet) run: update from pre-flight data
        if change_info.current_version is not None:
            state.last_version = change_info.current_version
            if is_read:
                # `info` shows metadata only: it keeps the whole-doc
                # guard's status quo (advancing last_read_version) but
                # never *grants* whole-doc content provenance — and when
                # it moves the baseline past the last genuine read, it
                # must also VOID any existing provenance: the versions
                # in between were never seen, so leaving the marker True
                # would launder an unseen edit into the tab guard.
                if command == "info":
                    if change_info.current_version != state.last_read_version:
                        state.global_read_covers_doc = False
                else:
                    state.global_read_covers_doc = True
                state.last_read_version = change_info.current_version
            if read_tab_id is not None:
                state.tab_read_versions[read_tab_id] = change_info.current_version

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
        # The post-op version is exactly one past pre-flight, so the bump is
        # attributable solely to our own metadata change. Shared by the global
        # and per-tab healing rules below so the two cannot drift apart.
        attributable_bump = (
            metadata_only_write
            and change_info is not None
            and change_info.current_version is not None
            and command_version == change_info.current_version + 1
        )
        # A successful full-content write doubles as a read: the doc now
        # contains exactly what we sent, so advance the conflict baseline.
        # Without this, a later push false-conflicts against our own write.
        # Partial writes (tab-scoped, find/replace) must NOT advance it —
        # the rest of the doc may hold changes the writer never saw.
        if full_doc_write:
            state.last_read_version = command_version
            state.global_read_covers_doc = True
        elif attributable_bump and not change_info.has_conflict:
            # Content is untouched, the baseline was current going in,
            # and the post-op version is exactly one past the pre-flight
            # read — the bump is attributable solely to our own metadata
            # change. A bigger jump means a concurrent edit slipped in
            # between pre-flight and the mutation; with --quiet
            # (change_info None) or a stale baseline there's no proof
            # either. In all those cases leave the baseline alone — a
            # spurious conflict later is recoverable, marking unseen
            # content as read is not.
            state.last_read_version = command_version
        # Metadata-only bumps also heal per-tab baselines, under the same
        # attributability condition (post-op version exactly one past
        # pre-flight). Deliberately independent of the global branch's
        # has_conflict guard: after a tab-only read the global baseline is
        # unset, which reads as a conflict there, yet each per-tab entry
        # proves its own currency by matching the pre-flight version.
        if attributable_bump:
            for tid, ver in state.tab_read_versions.items():
                if ver == change_info.current_version:
                    state.tab_read_versions[tid] = command_version
        # A tab-scoped write is a full read+replace of that one tab: stamp its
        # per-tab baseline so a second write to the same tab doesn't conflict
        # against the version our own write just produced.
        if written_tab_id is not None:
            state.tab_read_versions[written_tab_id] = command_version

    # Apply comment mutation patch (both quiet and non-quiet)
    # Per CONTEXT.md Decision #10
    if comment_state_patch:
        if "add_comment_id" in comment_state_patch:
            cid = comment_state_patch["add_comment_id"]
            if cid not in state.known_comment_ids:
                state.known_comment_ids.append(cid)
        if "add_resolved_id" in comment_state_patch:
            rid = comment_state_patch["add_resolved_id"]
            if rid not in state.known_resolved_ids:
                state.known_resolved_ids.append(rid)
        if "remove_resolved_id" in comment_state_patch:
            rid = comment_state_patch["remove_resolved_id"]
            state.known_resolved_ids = [x for x in state.known_resolved_ids if x != rid]
        if "remove_comment_id" in comment_state_patch:
            cid = comment_state_patch["remove_comment_id"]
            state.known_comment_ids = [x for x in state.known_comment_ids if x != cid]
            state.known_resolved_ids = [x for x in state.known_resolved_ids if x != cid]

    save_state(doc_id, state)
