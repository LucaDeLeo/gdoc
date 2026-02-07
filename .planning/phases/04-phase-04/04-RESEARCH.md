# Phase 4: Write Operations - Research

**Researched:** 2026-02-07
**Domain:** Google Docs API v1 (batchUpdate/replaceAllText), Google Drive API v3 (files.update with media upload), conflict-safe write operations
**Confidence:** HIGH

## Summary

Phase 4 adds two write commands to the gdoc CLI: `edit` (find-and-replace via the Docs API v1 `replaceAllText`) and `write` (full-document overwrite via Drive API v3 `files.update` with media upload). Both commands integrate with the existing awareness system for conflict detection, with `edit` issuing a warning on conflict and `write` blocking unless `--force` is passed.

The implementation requires one new module (`gdoc/api/docs.py` for the Docs API v1 service), one extension to the existing Drive API wrapper (`update_doc_content` in `gdoc/api/drive.py`), and two new CLI handler functions (`cmd_edit` and `cmd_write` in `gdoc/cli.py`). The parser already has both subcommands wired to `cmd_stub` -- they just need rewiring to the real handlers.

**Primary recommendation:** Follow the exact patterns established by `cmd_cat`/`cmd_info` for CLI handler structure, and mirror `gdoc/api/drive.py` patterns when creating `gdoc/api/docs.py`. All 6 locked decisions from CONTEXT.md are thoroughly validated and implementable.

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Decision 1 -- Case-Sensitivity Default: case-insensitive.** Default is case-insensitive. `--case-sensitive` is an opt-in flag. The Docs API `replaceAllText` has `matchCase` which defaults to `false`, aligning perfectly.

**Decision 2 -- `--quiet` and Write Conflict Block: `--quiet` does NOT bypass safety.** For `write`, `--quiet` skips the full 2-call pre-flight but adds back one lightweight `files.get(fields="version")` call for conflict detection. Only `--force` overrides the block. For non-write commands (`cat`, `edit`, `info`), `--quiet` skips pre-flight entirely.

**Decision 3 -- Uniqueness Pre-Check Strategy: best-effort gate + post-call reconciliation.** Uses text/plain export to count occurrences before API call (without `--all`). Reconciles with `occurrencesChanged` from API response. Documents divergence limitation (tables, smart quotes, Unicode case folding). Exit code 3 for pre-check "no match" or "multiple matches".

**Decision 4 -- State Update for edit/write: `last_version` only, never `last_read_version`.** `edit`/`write` update `state.last_version` from their post-command version, but never touch `last_read_version`. Only `cat`/`info` update `last_read_version`.

**Decision 5 -- Write Conflict When `last_read_version` Is Absent: block unless `--force`.** If `last_read_version` is `None` (no prior `cat`/`info`), `write` blocks with `ERR: no read baseline. Run 'gdoc cat DOC_ID' first, or use --force to overwrite.` (exit 3).

**Decision 6 -- `write --quiet --force`: skip version-check call entirely.** When `--force` is set, skip the version-check call entirely. `--quiet --force` achieves full 2-call savings.

### Codex Review Items (all resolved)

- `files.update` must set `body={'mimeType': 'application/vnd.google-apps.document'}` for conversion (Codex #2)
- Output: `OK replaced N occurrence(s)` for terse mode (Codex #9)
- Output: `OK written` for terse mode (Codex #10)
- Post-write version: `write` gets version from `files.update` response; `edit` needs one `get_file_version()` call (Codex #12)
- Two sub-plans (edit + write) is the agreed split (Codex #13)

### Other Locked Decisions

- Pre-check "no match" or "multiple matches" are exit code 3 (usage errors)
- `--all` with zero API matches still errors (exit 3)
- Pre-check counting respects `--case-sensitive`
- Overlapping-count semantics: non-overlapping (both Python `str.count()` and API)

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-api-python-client | >=2.150.0 | Google Docs API v1 + Drive API v3 | Already in project dependencies. Provides `build('docs', 'v1')` for Docs service |
| google-auth-oauthlib | >=1.2.1 | OAuth2 credentials | Already in project. Same creds work for both Drive and Docs scopes |
| google-auth-httplib2 | >=0.2.0 | HTTP transport | Already in project |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| io (stdlib) | - | `io.BytesIO` for in-memory media upload | Used in `write` command for `MediaIoBaseUpload` |
| googleapiclient.http.MediaIoBaseUpload | (bundled) | Upload media from memory buffer | Used in `write` to upload markdown content to Drive |
| googleapiclient.http.MediaFileUpload | (bundled) | Upload media from file path | Alternative to MediaIoBaseUpload; not recommended here since we read file content anyway |

### No New Dependencies

No new PyPI packages needed. The `google-api-python-client` package already includes the Docs API v1 discovery. The auth scopes already include `https://www.googleapis.com/auth/documents` (see `gdoc/auth.py` line 15-17).

## Architecture Patterns

### New/Modified Files

```
gdoc/
├── api/
│   ├── __init__.py       # ADD: get_docs_service() with @lru_cache
│   ├── drive.py          # ADD: update_doc_content() function
│   └── docs.py           # NEW: Docs API v1 wrapper (replace_all_text)
└── cli.py                # MODIFY: cmd_edit(), cmd_write() handlers; rewire parser
```

### Pattern 1: API Service Factory (mirrors existing `get_drive_service`)

**What:** Cached Docs API v1 service constructor, identical pattern to Drive service.
**When to use:** Any call to the Docs API.
**Example:**

```python
# Source: Mirrors existing gdoc/api/__init__.py pattern
# In gdoc/api/__init__.py

@lru_cache(maxsize=1)
def get_docs_service():
    """Build and cache a Docs API v1 service object."""
    from gdoc.auth import get_credentials
    creds = get_credentials()
    return build("docs", "v1", credentials=creds)
```

### Pattern 2: API Wrapper with Error Translation (mirrors `gdoc/api/drive.py`)

**What:** Thin wrapper functions that call the API and translate `HttpError` into `GdocError`/`AuthError`.
**When to use:** Every API call in `gdoc/api/docs.py`.
**Example:**

```python
# Source: Mirrors gdoc/api/drive.py pattern
# In gdoc/api/docs.py

from googleapiclient.errors import HttpError
from gdoc.api import get_docs_service
from gdoc.util import AuthError, GdocError

def _translate_http_error(e: HttpError, doc_id: str) -> None:
    """Translate HttpError for Docs API operations."""
    status = int(e.resp.status)
    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    if status == 403:
        raise GdocError(f"Permission denied: {doc_id}")
    if status == 404:
        raise GdocError(f"Document not found: {doc_id}")
    raise GdocError(f"API error ({status}): {e.reason}")

def replace_all_text(doc_id: str, old_text: str, new_text: str, match_case: bool = False) -> int:
    """Replace all occurrences of old_text with new_text in a Google Doc.

    Returns the number of occurrences changed.
    """
    try:
        service = get_docs_service()
        body = {
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {
                            "text": old_text,
                            "matchCase": match_case,
                        },
                        "replaceText": new_text,
                    }
                }
            ]
        }
        result = service.documents().batchUpdate(
            documentId=doc_id, body=body
        ).execute()
        replies = result.get("replies", [])
        if replies:
            return replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
        return 0
    except HttpError as e:
        _translate_http_error(e, doc_id)
```

### Pattern 3: Drive Media Upload for Document Overwrite

**What:** Upload markdown content to replace a Google Doc's body via `files.update`.
**When to use:** The `write` command.
**Example:**

```python
# Source: Google Drive API v3 official docs + Context7
# In gdoc/api/drive.py

import io
from googleapiclient.http import MediaIoBaseUpload

def update_doc_content(doc_id: str, content: str) -> int:
    """Overwrite a Google Doc's content with markdown text.

    Uploads the content as text/markdown and sets target mimeType to
    application/vnd.google-apps.document to trigger conversion.

    Returns the new version number (int).
    """
    try:
        service = get_drive_service()
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")),
            mimetype="text/markdown",
            resumable=False,
        )
        result = service.files().update(
            fileId=doc_id,
            body={"mimeType": "application/vnd.google-apps.document"},
            media_body=media,
            fields="version",
        ).execute()
        return int(result["version"])
    except HttpError as e:
        _translate_http_error(e, doc_id)
```

### Pattern 4: CLI Handler with Awareness Integration (mirrors `cmd_cat`/`cmd_info`)

**What:** Standard handler flow: resolve ID, pre-flight, execute, update state, output.
**When to use:** Both `cmd_edit` and `cmd_write`.
**Example (edit):**

```python
# Source: Mirrors gdoc/cli.py cmd_cat pattern
def cmd_edit(args) -> int:
    """Handler for `gdoc edit`."""
    doc_id = _resolve_doc_id(args.doc)

    # Pre-flight awareness check
    quiet = getattr(args, "quiet", False)
    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    # Conflict warning (edit warns, does not block)
    if change_info is not None and change_info.has_conflict:
        import sys
        print(
            " \u26a0 WARNING: doc changed since your last read. "
            "Run `gdoc cat` to refresh.",
            file=sys.stderr,
        )

    # ... uniqueness pre-check, API call, output, state update ...
```

### Anti-Patterns to Avoid

- **Updating `last_read_version` from edit/write:** The `is_read = command in ("cat", "info")` guard in `update_state_after_command` already prevents this. Do NOT add "edit" or "write" to that set (Decision #4).
- **Using `files.create` instead of `files.update` for write:** The existing doc ID must be reused. `files.update` replaces the content in-place.
- **Skipping post-call reconciliation for edit:** Always check `occurrencesChanged` from the API response, even after a pre-check (Decision #3).
- **Letting `--quiet` bypass write safety:** `--quiet` only skips the banner/pre-flight. Conflict detection for `write` remains active unless `--force` (Decision #2).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Text replacement in Google Docs | Custom Docs API request builder | `documents.batchUpdate` with `replaceAllText` | API handles formatting, tabs, embedded objects correctly; single atomic operation |
| Document content upload | Raw HTTP multipart upload | `MediaIoBaseUpload` from `googleapiclient.http` | Handles content-type headers, chunking, error retry correctly |
| Markdown-to-Docs conversion | Custom markdown parser | Drive API `files.update` with `mimeType='application/vnd.google-apps.document'` | Google's server-side conversion handles all markdown features |
| Case-insensitive matching | Custom Unicode case folding | API's `matchCase: false` + Python's `str.lower()` for pre-check | API uses proper ICU case folding; pre-check is best-effort only |

**Key insight:** The Google APIs handle all the hard work (formatting preservation, atomic replacement, markdown conversion). The CLI is a thin orchestration layer that adds conflict safety and token-efficient output.

## Common Pitfalls

### Pitfall 1: `containsText` Field Structure

**What goes wrong:** Passing a plain string to `containsText` instead of a `SubstringMatchCriteria` object.
**Why it happens:** Some simplified examples show `containsText: "text"` but the real API requires `containsText: { text: "...", matchCase: true/false }`.
**How to avoid:** Always use the full object structure: `{"text": old_text, "matchCase": match_case}`.
**Warning signs:** API returns 400 Bad Request.

### Pitfall 2: Missing `mimeType` in `files.update` Body

**What goes wrong:** Uploading markdown via `files.update` without setting `body={'mimeType': 'application/vnd.google-apps.document'}` results in the file being stored as raw markdown without conversion.
**Why it happens:** Confusing the upload media mimetype (`text/markdown`) with the target format. Both are needed.
**How to avoid:** Set `body={"mimeType": "application/vnd.google-apps.document"}` AND `media mimetype="text/markdown"`.
**Warning signs:** Document displays raw markdown syntax instead of formatted text.

### Pitfall 3: Pre-Check / API Count Divergence

**What goes wrong:** Text/plain export shows different occurrence count than what `replaceAllText` matches.
**Why it happens:** Tables, smart quotes, special formatting in the doc render differently in plain text vs. the API's internal text representation. Unicode case folding differences in case-insensitive mode.
**How to avoid:** Always reconcile with `occurrencesChanged` from the API response. Treat pre-check as best-effort. Issue warning if counts differ (per Decision #3).
**Warning signs:** Pre-check says 1 match but API replaces 0 or >1.

### Pitfall 4: `--quiet` Write Safety Gap

**What goes wrong:** If `--quiet` skipped ALL checks for `write`, the conflict safety mechanism would be silently bypassed.
**Why it happens:** General `--quiet` semantics skip pre-flight entirely. But `write` is destructive and needs conflict detection.
**How to avoid:** Implement the layered check per Decision #2: `--quiet` on `write` skips full pre-flight but adds back a lightweight `files.get(fields="version")` call for conflict detection. Only `--force` truly bypasses all safety.
**Warning signs:** `write --quiet` overwrites a doc that was modified by someone else.

### Pitfall 5: State Update After Write Advancing Read Version

**What goes wrong:** If `edit`/`write` updated `last_read_version`, the conflict detection baseline would advance, masking third-party changes between the agent's last `cat` and a future `write`.
**Why it happens:** Temptation to update all version fields on any successful command.
**How to avoid:** Only update `last_version` (for banner tracking), never `last_read_version` (Decision #4). The existing guard `is_read = command in ("cat", "info")` already handles this.
**Warning signs:** `write` succeeds silently after someone else edited the doc since agent's last `cat`.

### Pitfall 6: File Read Errors

**What goes wrong:** `write` command fails to handle missing/unreadable local files gracefully.
**Why it happens:** Not checking file existence before attempting to read.
**How to avoid:** Check file exists and is readable. Raise `GdocError("File not found: ...", exit_code=3)` for missing file (usage error, not API error).
**Warning signs:** Python traceback instead of clean `ERR:` message.

## Code Examples

### Example 1: Complete `replaceAllText` Request and Response

```python
# Source: Google Docs API v1 official docs (Context7, verified)
# Full request structure with SubstringMatchCriteria

body = {
    "requests": [
        {
            "replaceAllText": {
                "containsText": {
                    "text": "old text to find",
                    "matchCase": False,  # case-insensitive (default)
                },
                "replaceText": "new replacement text",
            }
        }
    ]
}

result = service.documents().batchUpdate(
    documentId=doc_id, body=body
).execute()

# Response structure:
# {
#     "documentId": "...",
#     "replies": [
#         {
#             "replaceAllText": {
#                 "occurrencesChanged": 3
#             }
#         }
#     ]
# }

occurrences = result["replies"][0]["replaceAllText"]["occurrencesChanged"]
```

### Example 2: Drive `files.update` with Media Upload and Version Response

```python
# Source: Google Drive API v3 docs (Context7 + official docs, verified)
import io
from googleapiclient.http import MediaIoBaseUpload

content = open("local.md").read()
media = MediaIoBaseUpload(
    io.BytesIO(content.encode("utf-8")),
    mimetype="text/markdown",
    resumable=False,
)

result = service.files().update(
    fileId=doc_id,
    body={"mimeType": "application/vnd.google-apps.document"},
    media_body=media,
    fields="version",  # request version in response -- avoids extra API call
).execute()

new_version = int(result["version"])
```

### Example 3: Uniqueness Pre-Check (Without `--all`)

```python
# Source: Project CONTEXT.md Decision #3 flow
from gdoc.api.drive import export_doc

text = export_doc(doc_id, mime_type="text/plain")

if case_sensitive:
    count = text.count(old_text)
else:
    count = text.lower().count(old_text.lower())

if count == 0:
    raise GdocError(f'no match found for "{old_text}"', exit_code=3)
if count > 1:
    raise GdocError(
        f"multiple matches ({count} found). Use --all to replace all occurrences.",
        exit_code=3,
    )
# count == 1: proceed to API call
```

### Example 4: Write Conflict Check Flow

```python
# Source: Project CONTEXT.md Decisions #2, #5, #6
from gdoc.state import load_state
from gdoc.api.drive import get_file_version

quiet = getattr(args, "quiet", False)
force = getattr(args, "force", False)

if not force:
    # Load state for conflict detection
    state = load_state(doc_id)

    # Decision #5: block if no read baseline
    if state is None or state.last_read_version is None:
        raise GdocError(
            "no read baseline. Run 'gdoc cat DOC_ID' first, "
            "or use --force to overwrite.",
            exit_code=3,
        )

    if quiet:
        # Decision #2: lightweight version check (1 call)
        version_data = get_file_version(doc_id)
        current_version = version_data.get("version")
        if current_version != state.last_read_version:
            raise GdocError(
                "doc modified since last read. Use --force to overwrite, "
                "or `gdoc cat` first.",
                exit_code=3,
            )
    # If not quiet: pre_flight() already ran and populated change_info
    # Check change_info.has_conflict below
```

### Example 5: Edit Output Formatting

```python
# Source: Project CONTEXT.md (Codex #9, #10), gdoc.md spec
from gdoc.format import get_output_mode, format_json

mode = get_output_mode(args)

# For edit:
if mode == "json":
    print(format_json(replaced=occurrences_changed))
else:
    print(f"OK replaced {occurrences_changed} occurrence(s)")

# For write:
if mode == "json":
    print(format_json(written=True, version=new_version))
else:
    print("OK written")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Drive API `convert=True` (v2) | Drive API v3 `mimeType` in body metadata | Drive API v3 | No `convert` parameter in v3; set target mimeType in body instead |
| Manual markdown parsing + Docs API insertText | Drive API `files.update` with `text/markdown` | ~2024 | Google handles markdown-to-Docs conversion server-side |
| `files.export` only supported basic formats | `text/markdown` export MIME type | July 2024 | Native markdown round-trip (export + import) without libraries |

**Current and verified:**
- `google-api-python-client` supports `build('docs', 'v1')` for Docs API (verified via Context7, HIGH confidence)
- `replaceAllText` uses `SubstringMatchCriteria` with `text` and `matchCase` fields (verified via Context7 official docs, HIGH confidence)
- `replaceAllText` response includes `occurrencesChanged` integer (verified via Context7, HIGH confidence)
- Drive API v3 `files.update` supports media upload with format conversion (verified via official docs, HIGH confidence)
- `MediaIoBaseUpload` is the standard way to upload from memory in Python (verified via Context7, HIGH confidence)

## Implementation Notes

### cmd_edit Flow (Detailed)

1. `doc_id = _resolve_doc_id(args.doc)`
2. `change_info = pre_flight(doc_id, quiet=quiet)`
3. If `change_info` and `change_info.has_conflict`: print conflict WARNING to stderr (non-blocking)
4. Extract flags: `replace_all = getattr(args, "all", False)`, `case_sensitive = getattr(args, "case_sensitive", False)`
5. **If not `replace_all`:** run pre-check:
   - `text = export_doc(doc_id, mime_type="text/plain")`
   - Count occurrences (respecting case sensitivity)
   - If 0: `raise GdocError('no match found for "..."', exit_code=3)`
   - If >1: `raise GdocError('multiple matches (N found). Use --all to replace all occurrences.', exit_code=3)`
6. Call `replace_all_text(doc_id, old_text, new_text, match_case=case_sensitive)`
7. Read `occurrences_changed` from return value
8. **Post-call reconciliation:**
   - If 0: `raise GdocError('no match found for "..."', exit_code=3)`
   - If >0 and not `replace_all` and count != occurrences_changed: print WARNING to stderr
   - Output: `OK replaced N occurrence(s)`
9. Get post-edit version: `version_data = get_file_version(doc_id)`, extract version
10. `update_state_after_command(doc_id, change_info, command="edit", quiet=quiet, command_version=version)`

### cmd_write Flow (Detailed)

1. `doc_id = _resolve_doc_id(args.doc)`
2. Read local file: `Path(args.file).read_text()` -- raise `GdocError` (exit 3) if missing
3. Extract flags: `quiet`, `force`
4. **Conflict check** (complex, per Decisions #2, #5, #6):
   - **If force:** skip all conflict checks; run pre_flight only if not quiet (for banner)
   - **If not force:**
     - Load state, check `last_read_version` exists (Decision #5)
     - If quiet: lightweight `get_file_version()` check (Decision #2)
     - If not quiet: `pre_flight()` already ran; check `change_info.has_conflict`
     - Block with `ERR:` if conflict detected
5. Upload: `new_version = update_doc_content(doc_id, content)`
6. Output: `OK written` (terse), `format_json(written=True, version=new_version)` (JSON)
7. `update_state_after_command(doc_id, change_info, command="write", quiet=quiet, command_version=new_version)`

### State Update Extension

The existing `update_state_after_command` already supports `command_version` for the `info` command, but only inside the `quiet` branch for `info` specifically. For edit/write, `command_version` should update `last_version` (but not `last_read_version`) in both quiet and non-quiet paths. The function needs a small extension:

```python
# In the non-quiet branch of update_state_after_command:
# After existing version/comment update logic:
if command_version is not None and not is_read:
    state.last_version = command_version
```

This ensures `edit`/`write` post-command versions prevent false "doc edited" banners on the next interaction.

## Testing Strategy

### Test File Structure

```
tests/
├── test_edit.py          # cmd_edit handler tests
├── test_write.py         # cmd_write handler tests
├── test_api_docs.py      # Docs API wrapper (replace_all_text) tests
├── test_api_drive.py     # Extended: update_doc_content tests (add to existing)
└── test_state.py         # Extended: edit/write state update tests (add to existing)
```

### Mock Patterns (from existing codebase)

```python
# Args helper (mirrors test_cat.py pattern)
def _make_args(**overrides):
    defaults = {
        "command": "edit",
        "doc": "abc123",
        "old_text": "old",
        "new_text": "new",
        "all": False,
        "case_sensitive": False,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)

# API mocking (mirrors test_cat.py pattern)
@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_drive_service")
@patch("gdoc.api.drive.export_doc", return_value="some old text here")
@patch("gdoc.api.docs.replace_all_text", return_value=1)
def test_edit_single_match(self, mock_replace, mock_export, ...):
    ...

# For write:
@patch("gdoc.state.update_state_after_command")
@patch("gdoc.notify.pre_flight", return_value=None)
@patch("gdoc.api.drive.get_drive_service")
@patch("gdoc.api.drive.update_doc_content", return_value=42)
@patch("gdoc.state.load_state")
def test_write_success(self, mock_load, mock_update_doc, ...):
    ...
```

### Key Test Cases

**cmd_edit:**
- Single match success (default mode)
- Single match with URL input
- `--all` replaces multiple occurrences
- `--all` with zero matches (exit 3)
- Pre-check: no match found (exit 3, no API call)
- Pre-check: multiple matches without `--all` (exit 3, no API call)
- `--case-sensitive` flag passed through
- Post-call reconciliation: warning when pre-check and API counts differ
- Post-call: API returns 0 even though pre-check found 1 (false positive)
- JSON output mode
- Conflict warning printed to stderr (non-blocking)
- State updated with command_version after success
- No state update on error
- `--quiet` skips pre-flight

**cmd_write:**
- Success with local markdown file
- Missing file (exit 3)
- Conflict: doc modified since last read (exit 3)
- Conflict: no read baseline / `last_read_version` is None (exit 3)
- `--force` bypasses conflict block
- `--quiet` still performs lightweight conflict check
- `--quiet --force` skips all checks (full 2-call savings)
- JSON output mode
- State updated with version from `files.update` response
- No state update on error

**API layer:**
- `replace_all_text`: success, 401, 403, 404 errors
- `update_doc_content`: success with version response, error cases
- `get_docs_service`: caching behavior

## Open Questions

1. **Verbose output for edit/write**
   - What we know: Terse is `OK replaced N occurrence(s)` and `OK written`. JSON wraps with `{"ok": true, ...}`.
   - What's unclear: Exact verbose format is not specified in gdoc.md or CONTEXT.md. Codex #9 says "No old/new text echo in verbose to keep tokens low."
   - Recommendation: Verbose for `edit` could show `Replaced N occurrence(s) in "Doc Title"`. Verbose for `write` could show `Written DOC_ID (version N)`. Keep it minimal per Codex guidance.

2. **Write command with non-markdown files**
   - What we know: The spec says `write DOC_ID FILE.md` -- always a markdown file.
   - What's unclear: Should we validate the file extension is `.md`?
   - Recommendation: Do not validate extension. Read any text file and upload as `text/markdown`. The user/agent knows what they're doing.

3. **Edit conflict warning exact format**
   - What we know: gdoc.md shows `WARNING: doc changed since your last read. Run 'gdoc cat' to refresh.` with a warning symbol.
   - What's unclear: Whether the warning should be part of the banner or printed separately after it.
   - Recommendation: Print the warning as part of the banner output (between the change lines and the closing `---`), matching the exact format in gdoc.md line 156-157.

## Sources

### Primary (HIGH confidence)
- Context7 `/websites/developers_google_workspace_api` -- `replaceAllText` request structure, `SubstringMatchCriteria` with `text`/`matchCase`, `occurrencesChanged` response, `batchUpdate` endpoint
- Context7 `/googleapis/google-api-python-client` -- `files.update` with `MediaIoBaseUpload`, Drive API v3 service patterns
- Official Google docs: `https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update` -- files.update supports media upload with conversion
- Official Google docs: `https://developers.google.com/workspace/drive/api/guides/manage-uploads` -- confirmed: "When you upload and convert media during an update request to a Docs file, the full contents of the document are replaced"
- Official Google docs: `https://developers.google.com/workspace/drive/api/guides/ref-export-formats` -- `text/markdown` is a supported export MIME type for Google Docs

### Secondary (MEDIUM confidence)
- Dev.to article by Wesley Chun (Google Workspace team): confirms markdown-to-Docs conversion via API (published 2025-02-10)

### Project Sources (HIGH confidence)
- `/Users/luca/dev/gdoc/.planning/phases/04-phase-04/CONTEXT.md` -- all 6 locked decisions, Codex review
- `/Users/luca/dev/gdoc/gdoc.md` -- spec file, API mapping, output examples, error messages
- `/Users/luca/dev/gdoc/gdoc/cli.py` -- existing handler patterns, parser with edit/write stubs
- `/Users/luca/dev/gdoc/gdoc/api/drive.py` -- API wrapper patterns, error translation
- `/Users/luca/dev/gdoc/gdoc/api/__init__.py` -- service factory pattern
- `/Users/luca/dev/gdoc/gdoc/state.py` -- state management, `update_state_after_command`
- `/Users/luca/dev/gdoc/gdoc/notify.py` -- `pre_flight`, `ChangeInfo.has_conflict`
- `/Users/luca/dev/gdoc/gdoc/auth.py` -- scopes already include `documents`
- `/Users/luca/dev/gdoc/tests/test_cat.py` -- testing patterns reference
- `/Users/luca/dev/gdoc/tests/test_info.py` -- testing patterns reference
- `/Users/luca/dev/gdoc/tests/test_state.py` -- state testing patterns reference

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies; all APIs verified via Context7 and official docs
- Architecture: HIGH -- follows existing codebase patterns exactly; all decisions locked in CONTEXT.md
- API details: HIGH -- `replaceAllText` and `files.update` thoroughly documented with verified examples
- Pitfalls: HIGH -- all pitfalls derived from locked decisions and verified API behavior
- Testing strategy: HIGH -- mirrors established project patterns exactly

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (stable APIs, locked decisions)
