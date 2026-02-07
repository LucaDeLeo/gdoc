# Phase 3: Awareness System - Research

**Researched:** 2026-02-07
**Domain:** Google Drive API v3 change detection, local state management, CLI pre-flight checks
**Confidence:** HIGH

## Summary

Phase 3 adds situational awareness to the gdoc CLI: before executing any command targeting a document, the CLI checks what changed since the last interaction and displays a notification banner on stderr. This requires three new capabilities: (1) local state persistence per document, (2) pre-flight API calls to detect changes, and (3) conflict detection logic for edit/write commands.

The implementation requires no new dependencies. All functionality uses the existing `google-api-python-client` (already installed) for `files.get` and `comments.list` API calls, and Python's stdlib (`json`, `os`, `tempfile`, `datetime`) for atomic state file management. The Google Drive API v3 provides all required fields: `version` (monotonically increasing integer), `modifiedTime`, `lastModifyingUser`, and full comment/reply data with `resolved` status and `startModifiedTime` filtering.

**Primary recommendation:** Implement in 3 plans -- (1) state module with atomic read/write, (2) API layer additions for pre-flight data fetching and comment listing, (3) notify module with banner formatting, conflict detection, and CLI handler integration.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

1. **State Schema:** Per-doc JSON at `~/.gdoc/state/{DOC_ID}.json` with fields: `last_seen`, `last_version`, `last_read_version`, `last_comment_check`, `known_comment_ids`, `known_resolved_ids`. Atomic writes via temp file + `os.rename()`.

2. **Pre-flight on all DOC_ID commands:** cat, info, edit, write, comments, comment, reply, resolve, reopen, share, cp. Not on: auth, ls, find, new.

3. **Pre-flight API calls:** Two calls: `files.get(fields="modifiedTime,version,lastModifyingUser")` and `comments.list(startModifiedTime=last_comment_check, includeResolved=true)`. First interaction omits `startModifiedTime` entirely.

4. **Banner on stderr:** Banners go to stderr, not stdout. Pipe-safe per OUT-06.

5. **--json mode:** No banner suppression needed; banners on stderr, JSON on stdout.

6. **--quiet short-circuits:** Skips all pre-flight network I/O. State freeze rule for `last_comment_check`. Exception: post-mutation comment IDs always recorded.

7. **Conflict detection uses `last_read_version`:** Set only by cat and info. `edit` warns, `write` blocks (requires `--force`). No `last_read_version` = treat as conflict.

8. **First interaction banner:** Shows doc metadata and comment counts from the same 2 pre-flight API calls.

9. **Post-mutation version fetch:** Extra `files.get(fields="version")` after mutating commands. Also updates `known_comment_ids`/`known_resolved_ids` for comment-mutation commands.

10. **Module structure:** `gdoc/state.py` (load/save state) and `gdoc/notify.py` (pre-flight + banner + conflict detection).

11. **Per-handler integration:** No middleware or decorator. Each handler calls `notify.pre_flight()` and `state.update()` explicitly.

12. **`last_comment_check` advancement:** Pre-request timestamp, not post-success. `known_comment_ids` handles deduplication.

13. **`comments.list` pagination:** Must paginate to completion. Follows existing `list_files()` pattern.

14. **`--quiet cat` version staleness:** Accept staleness, no extra API call. `info` is not affected (gets version from its own `files.get`).

### Claude's Discretion

None specified -- all decisions are locked.

### Deferred Ideas (OUT OF SCOPE)

- Comment CRUD (Phase 5)
- Write operations (Phase 4)
- File management (Phase 6)
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-api-python-client | >=2.150.0 | Drive API v3 `files.get`, `comments.list` | Already installed; provides all needed API access |
| json (stdlib) | N/A | State file serialization | No external dependency needed for simple JSON state |
| os / tempfile (stdlib) | N/A | Atomic file writes (temp + rename) | POSIX-atomic rename, no external dependency |
| datetime (stdlib) | N/A | ISO 8601 timestamps for `last_comment_check` | RFC 3339 compatible with Drive API |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pathlib (stdlib) | N/A | State directory path manipulation | Already used in `util.py` for `CONFIG_DIR` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw JSON + os.rename | python-atomicwrites | External dependency for a 10-line function; not justified |
| Per-doc JSON files | SQLite database | Over-engineered for simple per-doc key-value state |

**Installation:**
```bash
# No new dependencies required
```

## Architecture Patterns

### New Module Structure
```
gdoc/
├── state.py             # NEW: Per-doc state load/save with atomic writes
├── notify.py            # NEW: Pre-flight check, banner formatting, conflict detection
├── api/
│   ├── __init__.py      # EXISTING: get_drive_service() (also needs get_comments_service or reuse)
│   └── drive.py         # MODIFIED: Add get_file_version(), list_comments()
├── cli.py               # MODIFIED: Integration in cmd_cat, cmd_info handlers
├── util.py              # MODIFIED: Add STATE_DIR constant
└── format.py            # UNCHANGED
```

### Pattern 1: State Module (`gdoc/state.py`)

**What:** Dataclass-based state with atomic JSON persistence.
**When to use:** Every command that targets a DOC_ID reads state at start and writes state after success.

```python
# Source: CONTEXT.md Decision #1, verified against codebase patterns
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import os
import tempfile

STATE_DIR = Path.home() / ".gdoc" / "state"

@dataclass
class DocState:
    last_seen: str | None = None
    last_version: int | None = None
    last_read_version: int | None = None
    last_comment_check: str | None = None
    known_comment_ids: list[str] = field(default_factory=list)
    known_resolved_ids: list[str] = field(default_factory=list)

def load_state(doc_id: str, state_dir: Path = STATE_DIR) -> DocState:
    """Load per-doc state. Returns empty DocState if no state file exists."""
    path = state_dir / f"{doc_id}.json"
    if not path.exists():
        return DocState()
    try:
        data = json.loads(path.read_text())
        return DocState(**{k: v for k, v in data.items() if k in DocState.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError, KeyError):
        return DocState()

def save_state(doc_id: str, state: DocState, state_dir: Path = STATE_DIR) -> None:
    """Save state atomically: write temp file, then rename."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{doc_id}.json"
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
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
```

**Key design decisions:**
- `state_dir` parameter allows test injection (use `tmp_path` fixture)
- `load_state` returns empty `DocState` for corrupt/missing files (never crashes)
- `save_state` uses `mkstemp` in same directory as target (same filesystem = atomic rename)
- Dataclass with `asdict()` for clean serialization

### Pattern 2: Notify Module (`gdoc/notify.py`)

**What:** Pre-flight change detection and banner formatting.
**When to use:** Called by every DOC_ID command handler before executing the command.

```python
# Source: CONTEXT.md Decisions #3, #4, #6, #7, #8, #11, #12
from dataclasses import dataclass

@dataclass
class ChangeInfo:
    """Result of pre-flight check. Passed to handler for conflict decisions."""
    is_first_interaction: bool = False
    doc_edited: bool = False
    version_from: int | None = None
    version_to: int | None = None
    editor: str = ""
    new_comments: list = field(default_factory=list)      # [{id, author, content}]
    new_replies: list = field(default_factory=list)        # [{comment_id, author, content}]
    resolved_comments: list = field(default_factory=list)  # [{id, author}]
    reopened_comments: list = field(default_factory=list)   # [{id, author}]
    has_conflict: bool = False
    # Carry forward for state update
    current_version: int | None = None
    preflight_ts: str | None = None
    all_comment_ids: list = field(default_factory=list)
    all_resolved_ids: list = field(default_factory=list)

def pre_flight(doc_id: str, state: DocState, quiet: bool = False) -> ChangeInfo | None:
    """Run pre-flight check. Returns None if --quiet."""
    if quiet:
        return None
    # 1. files.get for version/modifiedTime/lastModifyingUser
    # 2. comments.list with startModifiedTime
    # 3. Compare against state
    # 4. Print banner to stderr
    # 5. Return ChangeInfo for conflict detection
    ...

def format_banner(changes: ChangeInfo, state: DocState) -> str:
    """Format the notification banner for stderr."""
    ...
```

### Pattern 3: Per-Handler Integration

**What:** Each command handler calls pre-flight and state update explicitly.
**When to use:** In every `cmd_*` function that receives a DOC_ID.

```python
# Source: CONTEXT.md Decision #11
# In cli.py - example for cmd_cat:
def cmd_cat(args) -> int:
    doc_id = _resolve_doc_id(args.doc)

    # Pre-flight check (skipped if --quiet)
    from gdoc.state import load_state, save_state
    from gdoc.notify import pre_flight

    state = load_state(doc_id)
    changes = pre_flight(doc_id, state, quiet=getattr(args, "quiet", False))

    # No conflict check for cat (it IS a read)

    # ... existing command logic ...

    # Post-success state update
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    state.last_seen = now
    if changes:  # Non-quiet run: version available from pre-flight
        state.last_version = changes.current_version
        state.last_read_version = changes.current_version  # cat is a read
        state.last_comment_check = changes.preflight_ts
        state.known_comment_ids = changes.all_comment_ids
        state.known_resolved_ids = changes.all_resolved_ids
    # Quiet cat: version fields stay stale (Decision #14)
    save_state(doc_id, state)
    return 0
```

### Anti-Patterns to Avoid

- **Middleware/decorator for pre-flight:** Different commands have different conflict behaviors (warn vs block vs none). A generic middleware would need so many parameters that per-handler calls are simpler and more explicit.
- **Storing state in a single database file:** Per-doc JSON files allow independent reads/writes and are immune to corruption of one doc affecting another.
- **Using `modifiedTime` for conflict detection:** Use `version` instead. `modifiedTime` can be set by API clients, `version` is server-controlled and monotonically increasing.
- **Fetching version from `export_media`:** The export endpoint returns only content bytes, no metadata. Version must come from `files.get`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic file writes | Custom locking/journaling | `tempfile.mkstemp()` + `os.rename()` | POSIX atomic rename is the standard pattern; no lock contention, no corruption window |
| RFC 3339 timestamps | String formatting/parsing | `datetime.now(timezone.utc).isoformat()` / `datetime.fromisoformat()` | Stdlib handles timezone, parsing, comparison correctly |
| Comment change detection | Diff-based approach comparing full comment text | ID-set comparison (`known_comment_ids`, `known_resolved_ids`) | Set membership is O(1), handles edge cases like edited comments without false positives |
| Pagination | Manual page tracking | While loop with `nextPageToken` (existing pattern in `list_files`) | Already battle-tested in the codebase |

**Key insight:** The state tracking design deliberately separates "what changed" detection (set-based, using IDs) from "when did we last check" (timestamp-based, using `last_comment_check`). The timestamp is a performance optimization (limits API response size), while the ID sets are the correctness mechanism. This means occasional duplicate API results are harmless -- `known_comment_ids` deduplicates.

## Common Pitfalls

### Pitfall 1: Version Field is a String
**What goes wrong:** Drive API v3 returns `version` as a string (JSON int64 format), not a native integer. Comparing strings gives wrong results ("9" > "10" lexicographically).
**Why it happens:** JSON int64 values are serialized as strings to avoid JavaScript precision loss.
**How to avoid:** Always convert to `int()` immediately when reading from API response: `int(metadata["version"])`.
**Warning signs:** Tests pass with small version numbers but fail with larger ones.

### Pitfall 2: `comments.list` Requires Explicit `fields` Parameter
**What goes wrong:** Calling `comments.list()` without the `fields` parameter returns an error or empty response.
**Why it happens:** Unlike most Drive API resources, the `comments` and `replies` resources do not have default fields. The `fields` parameter is mandatory.
**How to avoid:** Always specify `fields="comments(id,content,author(displayName,emailAddress),resolved,modifiedTime,replies(id,author(displayName,emailAddress),modifiedTime,content,action))"` or equivalent.
**Warning signs:** HTTP 400 errors from comments.list; empty comment data in responses.

### Pitfall 3: `startModifiedTime` on First Interaction
**What goes wrong:** Passing `None` or empty string as `startModifiedTime` causes API errors. Passing epoch timestamp returns all comments but adds unnecessary parameter.
**Why it happens:** The parameter expects RFC 3339 format or should be omitted entirely.
**How to avoid:** When `last_comment_check` is `None` (first interaction), do not include `startModifiedTime` in the API call parameters at all. Build the params dict conditionally.
**Warning signs:** API errors on first `gdoc cat` of a new document.

### Pitfall 4: Banner Output Must Use `sys.stderr`
**What goes wrong:** Banner text mixed into stdout corrupts pipe output (`gdoc cat DOC > file.md` captures banner in file) and breaks `--json` parsing.
**Why it happens:** Easy to use `print()` without `file=sys.stderr`.
**How to avoid:** All banner output uses `print(..., file=sys.stderr)`. Tests verify with `capsys.readouterr().err`.
**Warning signs:** JSON parse errors when piping `--json` output; extra text in redirected file output.

### Pitfall 5: State Update on Failure
**What goes wrong:** State is updated even when the command fails, advancing `last_comment_check` past unseen comments.
**Why it happens:** State update code runs before the command or in a finally block.
**How to avoid:** State update MUST happen only after the command succeeds (returns 0). Place `save_state()` after the command logic, before the return statement.
**Warning signs:** Running a command that fails, then running it again and seeing no banner for changes that occurred before the failure.

### Pitfall 6: `--quiet` on share/cp Missing from Parser
**What goes wrong:** `share` and `cp` commands lack `--quiet` flag in the argument parser, but CONTEXT Decision #2 says they need pre-flight.
**Why it happens:** These commands were added as stubs and `--quiet` was only added to commands implemented in Phase 2-3.
**How to avoid:** Add `--quiet` argument to `share_p` and `cp_p` in `build_parser()` as part of this phase.
**Warning signs:** `gdoc share DOC EMAIL --quiet` fails with "unrecognized arguments: --quiet".

### Pitfall 7: Comment `modifiedTime` Includes Reply Changes
**What goes wrong:** A comment appears as "changed" when only a reply was added, leading to double-notification (both "comment changed" and "new reply").
**Why it happens:** The comment's `modifiedTime` is updated when any of its replies is modified.
**How to avoid:** When a comment ID is already in `known_comment_ids`, the detection should check for new replies specifically (by examining the `replies[]` array), not treat the modified comment as "new."
**Warning signs:** Duplicate notifications -- "new comment" and "new reply" for the same activity.

## Code Examples

### API Call: Pre-flight `files.get`
```python
# Source: Google Drive API v3 official docs - files.get
# Verified via Context7 /googleapis/google-api-python-client
def get_file_version(doc_id: str) -> dict:
    """Fetch lightweight metadata for pre-flight check."""
    try:
        service = get_drive_service()
        return (
            service.files()
            .get(
                fileId=doc_id,
                fields="version,modifiedTime,lastModifyingUser(displayName,emailAddress)",
            )
            .execute()
        )
    except HttpError as e:
        _translate_http_error(e, doc_id)
```

**Response shape:**
```json
{
  "version": "851",
  "modifiedTime": "2025-01-20T14:35:00.000Z",
  "lastModifyingUser": {
    "displayName": "Alice Smith",
    "emailAddress": "alice@co.com"
  }
}
```

Note: `version` is a string. Convert with `int(response["version"])`.

### API Call: `comments.list` with Pagination
```python
# Source: Google Drive API v3 official docs - comments.list
# Follows existing list_files() pagination pattern in api/drive.py
def list_comments(file_id: str, start_modified_time: str | None = None) -> list[dict]:
    """List comments on a file, paginating to completion.

    Args:
        file_id: The document ID.
        start_modified_time: RFC 3339 timestamp. If None, returns all comments.
    """
    try:
        service = get_drive_service()
        all_comments: list[dict] = []
        page_token = None

        fields = (
            "nextPageToken,"
            "comments(id,content,author(displayName,emailAddress),"
            "resolved,modifiedTime,"
            "replies(id,author(displayName,emailAddress),modifiedTime,content,action))"
        )

        while True:
            params = {
                "fileId": file_id,
                "includeResolved": True,
                "fields": fields,
                "pageToken": page_token,
            }
            if start_modified_time is not None:
                params["startModifiedTime"] = start_modified_time

            response = service.comments().list(**params).execute()
            all_comments.extend(response.get("comments", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        return all_comments
    except HttpError as e:
        _translate_http_error(e, file_id)
```

**Key points:**
- `startModifiedTime` is only included when not `None` (Decision #3: first interaction omits it)
- `includeResolved=True` required to detect resolve/reopen transitions
- `fields` is mandatory for comments resource
- Replies are returned inline within each comment object
- Pagination follows existing `list_files()` pattern

### Banner Formatting
```python
# Source: gdoc.md L105-134, CONTEXT.md Decision #4, #8
import sys
from datetime import datetime, timezone

def _relative_time(iso_timestamp: str) -> str:
    """Convert ISO timestamp to relative time string like '3 min ago'."""
    then = datetime.fromisoformat(iso_timestamp)
    now = datetime.now(timezone.utc)
    delta = now - then
    minutes = int(delta.total_seconds() / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"

def print_banner(changes: ChangeInfo, state: DocState) -> None:
    """Print notification banner to stderr."""
    if changes.is_first_interaction:
        # First interaction banner (Decision #8)
        print("--- first interaction with this doc ---", file=sys.stderr)
        # ... metadata and comment counts
        print("---", file=sys.stderr)
        return

    lines = []
    if changes.doc_edited:
        editor = changes.editor or "someone"
        lines.append(
            f" \u270e doc edited by {editor} "
            f"(v{changes.version_from} \u2192 v{changes.version_to})"
        )
    for c in changes.new_comments:
        lines.append(f" \U0001f4ac new comment #{c['id']} by {c['author']}: \"{c['content'][:50]}\"")
    for r in changes.new_replies:
        lines.append(f" \u21a9 new reply on #{r['comment_id']} by {r['author']}: \"{r['content'][:50]}\"")
    for c in changes.resolved_comments:
        lines.append(f" \u2713 comment #{c['id']} resolved by {c['author']}")
    for c in changes.reopened_comments:
        lines.append(f" \u21ba comment #{c['id']} reopened by {c['author']}")

    if not lines:
        print("--- no changes ---", file=sys.stderr)
        return

    relative = _relative_time(state.last_seen) if state.last_seen else "unknown"
    print(f"--- since last interaction ({relative}) ---", file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    print("---", file=sys.stderr)
```

### Conflict Detection
```python
# Source: CONTEXT.md Decision #7
def check_conflict(changes: ChangeInfo, command: str, force: bool = False) -> int | None:
    """Check for version conflict. Returns exit code if blocked, None to proceed."""
    if changes is None:  # --quiet
        return None
    if not changes.has_conflict:
        return None

    if command == "write" and not force:
        print(
            "ERR: doc modified since last read. "
            "Use --force to overwrite, or `gdoc cat` first.",
            file=sys.stderr,
        )
        return 1
    elif command == "edit":
        print(
            " \u26a0 WARNING: doc changed since your last read. "
            "Run `gdoc cat` to refresh.",
            file=sys.stderr,
        )
        # Warning only -- proceed
        return None

    return None
```

### Atomic State Write Pattern
```python
# Source: Standard Python atomic write pattern
# Verified: os.rename is atomic on POSIX when src/dest on same filesystem
import tempfile
import os
import json

def save_state(doc_id: str, state: DocState, state_dir: Path = STATE_DIR) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / f"{doc_id}.json"
    # mkstemp in same dir guarantees same filesystem
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(state), f)
        os.rename(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

### Testing Pattern: State Module
```python
# Source: Codebase test patterns (test_cat.py, test_info.py)
import json
from pathlib import Path
from gdoc.state import load_state, save_state, DocState

class TestLoadState:
    def test_no_state_file(self, tmp_path):
        state = load_state("abc123", state_dir=tmp_path)
        assert state.last_version is None
        assert state.known_comment_ids == []

    def test_valid_state(self, tmp_path):
        (tmp_path / "abc123.json").write_text(json.dumps({
            "last_seen": "2025-01-20T14:30:00Z",
            "last_version": 847,
            "last_read_version": 847,
            "last_comment_check": "2025-01-20T14:30:00Z",
            "known_comment_ids": ["AAA"],
            "known_resolved_ids": [],
        }))
        state = load_state("abc123", state_dir=tmp_path)
        assert state.last_version == 847
        assert state.known_comment_ids == ["AAA"]

    def test_corrupt_json(self, tmp_path):
        (tmp_path / "abc123.json").write_text("not json{{{")
        state = load_state("abc123", state_dir=tmp_path)
        assert state.last_version is None  # Graceful fallback

class TestSaveState:
    def test_creates_directory(self, tmp_path):
        state_dir = tmp_path / "nested" / "state"
        state = DocState(last_seen="2025-01-20T14:30:00Z", last_version=847)
        save_state("abc123", state, state_dir=state_dir)
        assert (state_dir / "abc123.json").exists()

    def test_atomic_no_partial_writes(self, tmp_path):
        # Write initial state
        state = DocState(last_version=1)
        save_state("abc123", state, state_dir=tmp_path)
        # Write again -- should overwrite atomically
        state.last_version = 2
        save_state("abc123", state, state_dir=tmp_path)
        loaded = load_state("abc123", state_dir=tmp_path)
        assert loaded.last_version == 2
```

### Testing Pattern: Pre-flight with Mocked API
```python
# Source: Codebase patterns (test_cat.py, test_api_drive.py)
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

class TestPreFlightBanner:
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.get_file_version")
    @patch("gdoc.api.drive.list_comments")
    def test_no_changes_banner(self, mock_comments, mock_version, _svc, capsys, tmp_path):
        mock_version.return_value = {
            "version": "847",
            "modifiedTime": "2025-01-20T14:35:00Z",
            "lastModifyingUser": {"displayName": "Alice"},
        }
        mock_comments.return_value = []
        state = DocState(
            last_seen="2025-01-20T14:30:00Z",
            last_version=847,
            last_comment_check="2025-01-20T14:30:00Z",
            known_comment_ids=[],
            known_resolved_ids=[],
        )
        changes = pre_flight("abc123", state)
        err = capsys.readouterr().err
        assert "no changes" in err

    def test_quiet_skips_preflight(self, tmp_path):
        result = pre_flight("abc123", DocState(), quiet=True)
        assert result is None  # No API calls made
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `headRevisionId` for change detection | `version` field (monotonically increasing int64) | Drive API v3 | `version` is more reliable; `headRevisionId` may not exist for all file types |
| Polling `changes.list` for file changes | Direct `files.get` per-doc check | N/A (different use case) | Per-doc check is simpler for CLI; `changes.list` is for watch/sync scenarios |
| `status` field on comments (v2) | `resolved` boolean on comments (v3) | Drive API v2 -> v3 | Cleaner boolean; resolve/reopen via `replies.create` with `action` field |
| `verb` field on replies (v2) | `action` field on replies (v3) | Drive API v2 -> v3 | Same semantics, renamed field |

**Deprecated/outdated:**
- Drive API v2: Entire v2 is legacy. This project uses v3 exclusively.
- `revisions.list` for change detection: Unreliable for Google Docs (see REQUIREMENTS.md Out of Scope). Use `files.get(fields="version")` instead.

## Open Questions

1. **Comment `modifiedTime` vs reply detection granularity**
   - What we know: Comment `modifiedTime` is updated when any reply changes. The `replies[]` array contains all replies.
   - What's unclear: For very active documents, could there be race conditions where a reply is added between the `comments.list` call and state save?
   - Recommendation: Accept the theoretical race. The ID-set approach means the worst case is a duplicate notification on next run, which is harmless. No additional mitigation needed.

2. **`version` field upper bound**
   - What we know: `version` is int64 format, monotonically increasing. Typical Google Docs versions are in the hundreds/thousands.
   - What's unclear: Can `version` overflow Python int? Is there a practical upper bound?
   - Recommendation: Python ints are arbitrary precision, so no overflow. Store as `int` in state, not as string.

3. **State directory permissions**
   - What we know: `~/.gdoc/` exists (created by auth). Token file uses `0o600` permissions.
   - What's unclear: Should state files also use restricted permissions?
   - Recommendation: State files contain only document IDs and version numbers, not credentials. Standard file permissions (umask-default) are fine. The state directory inherits permissions from `~/.gdoc/`.

## Sources

### Primary (HIGH confidence)
- Context7 `/googleapis/google-api-python-client` - files.get fields, version field documentation, lastModifyingUser schema
- Google Drive API v3 official docs: `comments.list` parameters (startModifiedTime, includeResolved, fields), Comment resource schema (replies[], resolved, modifiedTime), Reply resource schema (action field: resolve/reopen)
- Google Drive API v3 official docs: `files` resource (version: "A monotonically increasing version number for the file. This reflects every change made to the file on the server, even those not visible to the user." -- string, int64 format)
- Google Drive API v3 official docs: `replies.create` -- action field for resolve/reopen

### Secondary (MEDIUM confidence)
- StackOverflow answers on comment resolve workflow -- confirms `replies.create` with `action: "resolve"` is the only way to resolve a comment (cannot set `resolved` directly)
- Python atomic write patterns -- `os.rename()` is POSIX-atomic on same filesystem; `tempfile.mkstemp()` guarantees same-directory temp file

### Tertiary (LOW confidence)
- None. All findings verified with primary or secondary sources.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new dependencies, all capabilities verified against official API docs
- Architecture: HIGH - CONTEXT.md provides exhaustive locked decisions; codebase patterns well understood from reading all source files
- API capabilities: HIGH - `files.get` version field, `comments.list` with startModifiedTime/includeResolved, Reply action field all verified through Context7 and official docs
- Pitfalls: HIGH - identified through API docs research, codebase analysis, and Codex review findings preserved in CONTEXT.md

**Codebase observations for planner:**
- `--quiet` flag already exists on: cat, info, edit, write, comments, comment, reply, resolve, reopen
- `--quiet` flag is MISSING on: share, cp (must be added in this phase)
- `--force` flag already exists on: write
- `CONFIG_DIR = Path.home() / ".gdoc"` already in `util.py` -- add `STATE_DIR = CONFIG_DIR / "state"` there
- `get_drive_service()` is cached with `@lru_cache(maxsize=1)` -- reuse for comments.list calls (comments API is part of Drive API v3, same service object)
- Only `cmd_cat` and `cmd_info` are implemented (not stubs) -- these are the only handlers that need integration NOW
- All other DOC_ID commands (`edit`, `write`, `comments`, etc.) are currently `cmd_stub` -- they will integrate awareness when implemented in their respective phases
- Existing error pattern: `print(f"ERR: {message}", file=sys.stderr)` -- conflict block message must follow this

**Research date:** 2026-02-07
**Valid until:** 2026-03-09 (Google Drive API v3 is stable; 30-day validity)
