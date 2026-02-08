"""Pre-flight change detection and notification banners."""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ChangeInfo:
    """Result of a pre-flight check."""
    is_first_interaction: bool = False
    doc_title: str = ""
    doc_owner: str = ""
    doc_modified: str = ""
    open_comment_count: int = 0
    resolved_comment_count: int = 0

    # Change detection results
    doc_edited: bool = False
    editor: str = ""
    old_version: int | None = None
    new_version: int | None = None

    new_comments: list[dict] = field(default_factory=list)
    new_replies: list[dict] = field(default_factory=list)
    newly_resolved: list[dict] = field(default_factory=list)
    newly_reopened: list[dict] = field(default_factory=list)

    # Pre-flight metadata for state update
    current_version: int | None = None
    preflight_timestamp: str = ""
    all_comment_ids: list[str] = field(default_factory=list)
    all_resolved_ids: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """True if any changes were detected."""
        return (self.doc_edited or bool(self.new_comments) or
                bool(self.new_replies) or bool(self.newly_resolved) or
                bool(self.newly_reopened))

    # Conflict detection: carried from state for has_conflict
    last_read_version: int | None = None

    @property
    def has_conflict(self) -> bool:
        """True if doc was edited since last read (Decision #7).

        Compares current_version against last_read_version, NOT last_version.
        If last_read_version is None (no prior read), treat as conflict.
        """
        if self.current_version is None:
            return False
        if self.last_read_version is None:
            return True  # No prior read = conflict
        return self.current_version != self.last_read_version


def pre_flight(doc_id: str, quiet: bool = False) -> ChangeInfo | None:
    """Run the pre-flight check for a document.

    Returns ChangeInfo with detected changes, or None if --quiet.
    Prints the notification banner to stderr.
    """
    if quiet:
        return None

    from gdoc.state import load_state
    from gdoc.api.drive import get_file_version
    from gdoc.api.comments import list_comments

    state = load_state(doc_id)

    # Capture pre-request timestamp for last_comment_check advancement (Decision #12)
    preflight_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # Carry last_read_version from state for conflict detection (Decision #7)
    last_read_version = state.last_read_version if state else None

    # Pre-flight API call #1: file version
    version_data = get_file_version(doc_id)
    current_version = version_data.get("version")
    modified_time = version_data.get("modifiedTime", "")
    last_modifier = version_data.get("lastModifyingUser", {})
    editor_name = (last_modifier.get("displayName") or
                   last_modifier.get("emailAddress", ""))

    # Pre-flight API call #2: comments
    start_time = state.last_comment_check if state else ""
    comments = list_comments(doc_id, start_modified_time=start_time)

    info = ChangeInfo(
        current_version=current_version,
        preflight_timestamp=preflight_ts,
        last_read_version=last_read_version,
    )

    if state is None:
        # First interaction — build first-interaction banner
        info.is_first_interaction = True
        info.doc_modified = modified_time

        # Need file metadata for title/owner
        from gdoc.api.drive import get_file_info
        metadata = get_file_info(doc_id)
        info.doc_title = metadata.get("name", "")
        owners = metadata.get("owners", [])
        owner = owners[0] if owners else {}
        info.doc_owner = owner.get("emailAddress") or owner.get("displayName", "")

        # Count comments
        open_count = sum(1 for c in comments if not c.get("resolved", False))
        resolved_count = sum(1 for c in comments if c.get("resolved", False))
        info.open_comment_count = open_count
        info.resolved_comment_count = resolved_count

        # Initialize comment ID sets
        info.all_comment_ids = [c["id"] for c in comments if "id" in c]
        info.all_resolved_ids = [c["id"] for c in comments if c.get("resolved", False)]

    else:
        # Subsequent interaction — detect changes

        # Doc edit detection (version changed)
        if current_version is not None and state.last_version is not None:
            if current_version != state.last_version:
                info.doc_edited = True
                info.editor = editor_name
                info.old_version = state.last_version
                info.new_version = current_version

        # Comment change detection
        known_ids = set(state.known_comment_ids)
        known_resolved = set(state.known_resolved_ids)

        for c in comments:
            cid = c.get("id", "")
            resolved = c.get("resolved", False)

            if cid not in known_ids:
                # New comment
                info.new_comments.append(c)
            else:
                # Existing comment — check for new replies and resolve/reopen
                replies = c.get("replies", [])
                has_new_content_reply = any(
                    not r.get("action") and r.get("createdTime", "") > start_time
                    for r in replies
                )
                if has_new_content_reply:
                    info.new_replies.append(c)

                if resolved and cid not in known_resolved:
                    info.newly_resolved.append(c)
                elif not resolved and cid in known_resolved:
                    info.newly_reopened.append(c)

        # Build full comment ID sets for state update
        all_ids = set(state.known_comment_ids)
        all_resolved = set(state.known_resolved_ids)
        for c in comments:
            cid = c.get("id", "")
            if cid:
                all_ids.add(cid)
                if c.get("resolved", False):
                    all_resolved.add(cid)
                elif cid in all_resolved:
                    all_resolved.discard(cid)
        info.all_comment_ids = sorted(all_ids)
        info.all_resolved_ids = sorted(all_resolved)

    # Print the banner to stderr
    _print_banner(info, state)

    return info


def _format_time_ago(last_seen: str) -> str:
    """Format a human-readable 'time ago' string from an ISO timestamp."""
    if not last_seen:
        return ""
    try:
        then = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - then
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds} sec ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hr ago"
        days = hours // 24
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    except (ValueError, TypeError):
        return ""


def _print_banner(info: ChangeInfo, state) -> None:
    """Print the notification banner to stderr (Decision #4)."""
    if info.is_first_interaction:
        _print_first_interaction_banner(info)
        return

    if not info.has_changes:
        print("--- no changes ---", file=sys.stderr)
        return

    # Build change lines
    time_ago = _format_time_ago(state.last_seen if state else "")
    header = f"--- since last interaction ({time_ago}) ---" if time_ago else "--- since last interaction ---"
    print(header, file=sys.stderr)

    if info.doc_edited:
        version_str = ""
        if info.old_version is not None and info.new_version is not None:
            version_str = f" (v{info.old_version} \u2192 v{info.new_version})"
        print(f" \u270e doc edited by {info.editor}{version_str}", file=sys.stderr)

    for c in info.new_comments:
        author = c.get("author", {})
        name = author.get("emailAddress") or author.get("displayName", "")
        content = c.get("content", "")
        cid = c.get("id", "")
        if len(content) > 60:
            content = content[:57] + "..."
        print(f' \U0001f4ac new comment #{cid} by {name}: "{content}"', file=sys.stderr)

    for c in info.new_replies:
        cid = c.get("id", "")
        replies = c.get("replies", [])
        if replies:
            last_reply = replies[-1]
            author = last_reply.get("author", {})
            name = author.get("emailAddress") or author.get("displayName", "")
            content = last_reply.get("content", "")
            if len(content) > 60:
                content = content[:57] + "..."
            print(f' \u21a9 new reply on #{cid} by {name}: "{content}"', file=sys.stderr)

    for c in info.newly_resolved:
        cid = c.get("id", "")
        replies = c.get("replies", [])
        resolver = ""
        for r in reversed(replies):
            if r.get("action") == "resolve":
                author = r.get("author", {})
                resolver = author.get("emailAddress") or author.get("displayName", "")
                break
        if not resolver:
            resolver = "unknown"
        print(f" \u2713 comment #{cid} resolved by {resolver}", file=sys.stderr)

    for c in info.newly_reopened:
        cid = c.get("id", "")
        replies = c.get("replies", [])
        reopener = ""
        for r in reversed(replies):
            if r.get("action") == "reopen":
                author = r.get("author", {})
                reopener = author.get("emailAddress") or author.get("displayName", "")
                break
        if not reopener:
            reopener = "unknown"
        print(f" \u21ba comment #{cid} reopened by {reopener}", file=sys.stderr)

    print("---", file=sys.stderr)


def _print_first_interaction_banner(info: ChangeInfo) -> None:
    """Print the first-interaction banner (Decision #8)."""
    print("--- first interaction with this doc ---", file=sys.stderr)

    modified_date = info.doc_modified[:10] if info.doc_modified else ""
    print(
        f' \U0001f4c4 "{info.doc_title}" by {info.doc_owner}, last edited {modified_date}',
        file=sys.stderr,
    )

    parts = []
    if info.open_comment_count > 0:
        parts.append(f"{info.open_comment_count} open comment{'s' if info.open_comment_count != 1 else ''}")
    if info.resolved_comment_count > 0:
        parts.append(f"{info.resolved_comment_count} resolved")
    if parts:
        print(f" \U0001f4ac {', '.join(parts)}", file=sys.stderr)

    print("---", file=sys.stderr)
