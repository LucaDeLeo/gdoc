# Phase 02: Read Operations - Research

**Researched:** 2026-02-07
**Domain:** Google Drive API v3 (files.export, files.list, files.get), Python API client patterns, query escaping, error translation
**Confidence:** HIGH

## Summary

Phase 2 implements four CLI commands (`cat`, `ls`, `find`, `info`) that wrap Google Drive API v3 read operations. The technical foundation is well understood: `files.export` for document content export, `files.list` with query syntax for listing and searching, and `files.get` for metadata retrieval. All API endpoints are stable, well-documented, and the Python client library (`google-api-python-client` v2.189.0) provides clean programmatic access.

The key technical decisions are already locked in CONTEXT.md and have been verified against current API documentation. Native markdown export via `text/markdown` MIME type is confirmed as a supported export format for Google Documents (added July 2024, listed in official export MIME types table). The `HttpError` class provides a `status_code` property and `reason` attribute for clean error translation. Query escaping for Drive search requires backslash-first, then single-quote escaping -- order matters.

Three areas require attention during implementation: (1) `files.export()` returns bytes, which must be decoded to UTF-8 string for stdout output; (2) the `size` field in `files.get` may or may not be populated for Google Workspace editor files (documented as "blobs and Google Workspace editor files" but reliability varies -- the `info` command should handle missing `size` gracefully); (3) `extract_doc_id()` currently does not handle `/drive/folders/` URLs, which is needed for the `ls` command's folder_id argument.

**Primary recommendation:** Create `gdoc/api/__init__.py` with `get_drive_service()` using `@lru_cache`, then `gdoc/api/drive.py` with thin wrapper functions that catch `HttpError` and translate to `GdocError`/`AuthError`. Command handlers in `cli.py` remain thin: parse args, call API wrapper, format output. Add `format_json()` helper to `format.py`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**API Module Structure:**
1. Package layout: `gdoc/api/__init__.py` (service factories), `gdoc/api/drive.py` (Drive API wrappers).
2. Service factory: `get_drive_service()` in `gdoc/api/__init__.py` calls `get_credentials()` then `build("drive", "v3", credentials=creds)`. Cached via `@lru_cache`.
3. All Drive API call wrappers (`export_doc`, `list_files`, `search_files`, `get_file_info`) live in `gdoc/api/drive.py`.
4. Not created in Phase 2: `api/docs.py` (Phase 4), `api/comments.py` (Phase 5).
5. Command handlers in `cli.py` stay thin -- parse args, call `api/drive.py` function, format output.

**`cat` -- Document Export:**
6. Default export: `files.export(fileId=doc_id, mimeType="text/markdown")`.
7. `--plain` flag: `files.export(fileId=doc_id, mimeType="text/plain")`.
8. Stdout behavior: Raw content to stdout (no `format_success` wrapper). Matches unix `cat` semantics.
9. `--json` mode: Wrap content: `{"ok": true, "content": "...markdown here..."}`. Uses `format_json` helper.
10. Accept 10MB Drive API export limit for v1. No chunked download.
11. Non-exportable files: Raise `GdocError("Cannot export file as markdown: {reason}")`.
12. `--comments` flag: Not implemented in Phase 2 -- returns "not yet implemented" error if used.

**`ls` -- File Listing:**
13. API call: `files.list(q=query, fields="nextPageToken, files(id, name, mimeType, modifiedTime)", pageSize=100)` with auto-pagination.
14. Default scope: `'root' in parents` when no `folder_id` argument.
15. `--type` filter: `docs` -> `mimeType='application/vnd.google-apps.document'`; `sheets` -> `mimeType='application/vnd.google-apps.spreadsheet'`; `all` (default) -> no mimeType filter.
16. Always include `trashed=false` in query.
17. Terse output: `ID\tTITLE\tMODIFIED` per line (tab-separated, three columns).
18. Verbose output: Add mimeType column: `ID\tTITLE\tMODIFIED\tTYPE`.
19. JSON output: `{"ok": true, "files": [{"id": "...", "name": "...", "mimeType": "...", "modifiedTime": "..."}]}`.
20. Fetch all pages internally. No `--limit` or `--page-token` in v1.
21. Folder ID from URL: `folder_id` passed through `extract_doc_id()`.

**`find` -- File Search:**
22. Query: `(name contains '{query}' or fullText contains '{query}') and trashed=false`.
23. Escape backslashes first (`\` -> `\\`), then single quotes (`'` -> `\'`).
24. Output format: Same as `ls` -- `ID\tTITLE\tMODIFIED` terse, structured JSON in `--json` mode.
25. Same auto-pagination as `ls`.

**`info` -- Document Metadata:**
26. Metadata call: `files.get(fileId=doc_id, fields="id, name, mimeType, modifiedTime, createdTime, owners(emailAddress, displayName), lastModifyingUser(emailAddress, displayName), size")`.
27. Word count: Always computed via `files.export(mimeType="text/plain")` + `len(text.split())`. Two API calls per `info` invocation.
28. Terse output: Key-value pairs, one per line: `Title: ...`, `Owner: ...`, `Modified: ...`, `Words: ...`.
29. Verbose output: Add createdTime, lastModifyingUser, mimeType, size.
30. JSON output: Flat object: `{"id": "...", "title": "...", "owner": "...", "modified": "...", "words": ...}`.

**Doc ID Resolution:**
31. Each command handler calls `extract_doc_id(args.doc)` at top, wraps `ValueError` in `GdocError(message, exit_code=3)`.

**Error Handling for API Calls:**
32. Catch `googleapiclient.errors.HttpError` in `api/drive.py` and translate to typed exceptions.
33. 401 -> `AuthError("Authentication expired. Run \`gdoc auth\`.")` (exit 2).
34. 403 -> `GdocError("Permission denied: {id}")` (exit 1).
35. 404 -> `GdocError("Document not found: {id}")` (exit 1).
36. Other -> `GdocError("API error ({status}): {reason}")` (exit 1).
37. API layer raises clean exceptions; `cli.py` top-level handler adds `ERR:` prefix.

**Output Formatting:**
38. New helper: `format_json(**data)` returns `json.dumps({"ok": True, **data})`.
39. `format_success` retained for simple string confirmations.
40. Per-command JSON: Each command defines its own structure using `format_json`.

**Resolved:**
41. Shared Drive support deferred -- no `supportsAllDrives` in Phase 2.
42. `ls` with no argument uses `'root' in parents`.
43. Non-Google-Docs IDs in `cat` get explicit error via HttpError catch.

### Claude's Discretion

- Exact `fields` parameter lists (may adjust for nested owner/user fields)
- Whether to use `MediaIoBaseDownload` or simple `.execute()` for export
- Verbose output date formatting (ISO 8601 truncated vs full timestamp)
- Test structure (likely per-command test files plus `test_api_drive.py`)
- Whether `api/drive.py` functions return raw data or typed dicts
- `lru_cache` on `get_drive_service()` vs passing service as parameter
- How `--comments` flag reports "not yet implemented" in Phase 2
- Tab-separated date formatting in terse `ls`/`find` output

### Deferred Ideas (OUT OF SCOPE)

- Shared Drive support (future phase)
- `--limit` / `--page-token` pagination controls
- `cat --comments` implementation (Phase 5)
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-api-python-client | 2.189.0 (installed) | Drive API v3 client: `files.export`, `files.list`, `files.get` | Already in deps; provides `build()`, `HttpError`, dynamic API methods |
| google-auth | 2.48.0 (installed) | Credential management, token refresh | Transitive dep; `get_credentials()` already returns `google.oauth2.credentials.Credentials` |
| google-auth-oauthlib | 1.2.4 (installed) | OAuth2 flow (Phase 1, already used) | No new usage in Phase 2, but auth module depends on it |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| functools.lru_cache | stdlib | Cache `get_drive_service()` result | Single service instance per CLI invocation |
| json (stdlib) | stdlib | `format_json()` helper, JSON output mode | All `--json` formatted output |
| io.BytesIO | stdlib | Buffer for `MediaIoBaseDownload` if used | Only if chunked download approach chosen |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Simple `.execute()` for export | `export_media()` + `MediaIoBaseDownload` | Official docs recommend `export_media()` with downloader. However, `.execute()` on `files().export()` also returns bytes for sub-10MB content. `export_media()` is the safer, officially documented path. **Recommendation: Use `export_media()` with `MediaIoBaseDownload` for correctness.** |
| Raw dicts from API | TypedDict or dataclass wrappers | Adds complexity with no benefit in Phase 2; raw dicts are fine for thin handlers |

**No new dependencies needed.** Everything is in the stdlib or already installed.

## Architecture Patterns

### Recommended Project Structure (Phase 2 additions)
```
gdoc/
├── api/
│   ├── __init__.py      # get_drive_service() with @lru_cache
│   └── drive.py         # export_doc, list_files, search_files, get_file_info
├── cli.py               # + cmd_cat, cmd_ls, cmd_find, cmd_info (replace cmd_stub)
├── format.py            # + format_json(**data)
├── util.py              # + /folders/ pattern in extract_doc_id
└── (existing files unchanged)

tests/
├── test_api_drive.py    # API wrapper unit tests (mock HttpError, mock service)
├── test_cat.py          # cat command integration tests
├── test_ls.py           # ls command integration tests
├── test_find.py         # find command integration tests
├── test_info.py         # info command integration tests
└── (existing test files unchanged)
```

### Pattern 1: Service Factory with lru_cache
**What:** Single cached Drive API service instance per CLI invocation.
**When to use:** Every API call in `api/drive.py` needs a service object.
**Example:**
```python
# gdoc/api/__init__.py
# Source: Verified against google-api-python-client docs/oauth-installed.md
from functools import lru_cache
from googleapiclient.discovery import build

@lru_cache(maxsize=1)
def get_drive_service():
    """Build and cache the Drive API v3 service object."""
    from gdoc.auth import get_credentials
    creds = get_credentials()
    return build("drive", "v3", credentials=creds)
```

### Pattern 2: API Error Translation
**What:** Catch `HttpError` in `api/drive.py`, translate to `GdocError`/`AuthError`.
**When to use:** Every Drive API call wrapper function.
**Example:**
```python
# gdoc/api/drive.py
# Source: Verified against googleapiclient.errors source code
from googleapiclient.errors import HttpError
from gdoc.util import GdocError, AuthError

def export_doc(doc_id: str, mime_type: str = "text/markdown") -> str:
    """Export a Google Doc as text content."""
    service = get_drive_service()
    try:
        # Use export_media for content download
        request = service.files().export_media(
            fileId=doc_id, mimeType=mime_type
        )
        content = request.execute()
        return content.decode("utf-8")
    except HttpError as e:
        _translate_http_error(e, doc_id)

def _translate_http_error(e: HttpError, file_id: str) -> None:
    """Translate HttpError to GdocError/AuthError. Always raises."""
    status = e.status_code
    if status == 401:
        raise AuthError("Authentication expired. Run `gdoc auth`.")
    elif status == 403:
        # Could be permission denied or non-exportable file
        raise GdocError(f"Permission denied: {file_id}")
    elif status == 404:
        raise GdocError(f"Document not found: {file_id}")
    else:
        raise GdocError(f"API error ({status}): {e.reason}")
```

### Pattern 3: Thin Command Handler
**What:** CLI handler parses args, calls API, formats output.
**When to use:** All four new command handlers.
**Example:**
```python
# gdoc/cli.py
def cmd_cat(args) -> int:
    """Handler for `gdoc cat`."""
    from gdoc.api.drive import export_doc
    from gdoc.format import get_output_mode, format_json

    doc_id = _resolve_doc_id(args.doc)

    if getattr(args, "comments", False):
        print("ERR: cat --comments is not yet implemented", file=sys.stderr)
        return 4  # STUB

    mime = "text/plain" if getattr(args, "plain", False) else "text/markdown"
    content = export_doc(doc_id, mime_type=mime)

    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(content=content))
    else:
        print(content, end="")
    return 0

def _resolve_doc_id(raw: str) -> str:
    """Extract doc ID, wrapping ValueError as GdocError(exit_code=3)."""
    from gdoc.util import extract_doc_id
    try:
        return extract_doc_id(raw)
    except ValueError as e:
        raise GdocError(str(e), exit_code=3)
```

### Pattern 4: Auto-Pagination for files.list
**What:** Fetch all pages of `files.list` results internally.
**When to use:** `ls` and `find` commands.
**Example:**
```python
# gdoc/api/drive.py
def list_files(query: str, fields: str = "nextPageToken, files(id, name, mimeType, modifiedTime)") -> list[dict]:
    """List files matching a Drive query, auto-paginating."""
    service = get_drive_service()
    all_files = []
    page_token = None
    try:
        while True:
            response = service.files().list(
                q=query,
                fields=fields,
                pageSize=100,
                pageToken=page_token,
            ).execute()
            all_files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        _translate_http_error(e, "")
    return all_files
```

### Pattern 5: Query Escaping
**What:** Escape user input before embedding in Drive `q` strings.
**When to use:** `find` command and any dynamic query construction.
**Example:**
```python
# gdoc/api/drive.py
def _escape_query_value(value: str) -> str:
    """Escape a value for embedding in a Drive API q parameter.

    Escapes backslashes first, then single quotes.
    Order matters to avoid double-escaping.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace("'", "\\'")
    return value
```

### Anti-Patterns to Avoid
- **Don't put formatting logic in `api/drive.py`:** API layer returns raw data (dicts, strings). Formatting belongs in command handlers or `format.py`.
- **Don't catch `HttpError` in command handlers:** Error translation happens in `api/drive.py`. Command handlers only deal with `GdocError`/`AuthError`.
- **Don't import `googleapiclient` at module level in `cli.py`:** Use lazy imports (already established pattern) to avoid import errors when running `gdoc --help` without Google libraries.
- **Don't use `argparse type=` for doc_id validation:** Keeps argparse clean, allows better error messages from handler code.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP request handling | Custom HTTP calls to Drive API | `googleapiclient.discovery.build()` service | Handles auth headers, retries, pagination tokens, discovery docs |
| Error status extraction | Parse HTTP response manually | `HttpError.status_code` property, `.reason` attribute | Already parses JSON error body, extracts message and details |
| Query parameter encoding | URL-encode `q` parameter manually | Pass `q` as string to `.list()`, client handles encoding | Python client handles URL encoding automatically |
| File content download | `urllib`/`requests` for export | `service.files().export_media()` + `.execute()` | Handles auth, content-type negotiation |

**Key insight:** The Google API Python client does all HTTP-level work. Our code only needs to construct parameters, call methods, and handle the typed `HttpError` exceptions.

## Common Pitfalls

### Pitfall 1: `files.export()` vs `files.export_media()` Confusion
**What goes wrong:** Using `files().export().execute()` expecting bytes but getting unexpected behavior, or using `export_media()` without proper byte handling.
**Why it happens:** The Python client dynamically generates two variants. The official download guide exclusively shows `export_media()` with `MediaIoBaseDownload`.
**How to avoid:** Use `export_media().execute()` for simple sub-10MB exports. This returns raw bytes directly. Decode with `.decode("utf-8")` for text content. If content exceeds 10MB, Drive API returns an error -- this is acceptable per CONTEXT.md decision.
**Warning signs:** Getting a dict instead of bytes, or getting `b''` empty content.

### Pitfall 2: Query Escaping Order
**What goes wrong:** Escaping single quotes before backslashes causes double-escaping. E.g., `it's a \path` becomes `it\'s a \\path` (wrong: the `\` in `\'` gets double-escaped).
**Why it happens:** If you escape `'` first to `\'`, then escape `\` to `\\`, the backslash in `\'` becomes `\\'` which is wrong.
**How to avoid:** Always escape backslashes FIRST, then single quotes: `\` -> `\\`, then `'` -> `\'`.
**Warning signs:** Search queries with backslashes or apostrophes returning no results.

### Pitfall 3: `name contains` is Prefix-Only
**What goes wrong:** Expecting `name contains 'World'` to match `HelloWorld`, but it only matches names starting with `World`.
**Why it happens:** Drive API `contains` operator on `name` does prefix matching only (per official docs: "The `contains` operator only performs prefix matching for a `name` term").
**How to avoid:** The `find` command combines `name contains` with `fullText contains` (OR), so at least one will match. Document this limitation clearly.
**Warning signs:** Name-only searches miss files where the query appears mid-word.

### Pitfall 4: `fullText contains` Tokenization
**What goes wrong:** Searching for `'8393'` (pure digits) in `fullText` returns no results.
**Why it happens:** Drive API `fullText` search only matches complete tokens, not substrings. Pure numeric strings may not be tokenized as expected (known issue, see SO thread).
**How to avoid:** No workaround in v1. The combined `name contains OR fullText contains` query gives best coverage. Worth noting in help text or docs.
**Warning signs:** Short numeric queries returning empty results.

### Pitfall 5: `size` Field May Be Empty for Google Docs
**What goes wrong:** Requesting `size` in `files.get` fields for a Google Doc returns `None`/missing.
**Why it happens:** The `size` field documentation says "Output only. Size in bytes of blobs and Google Workspace editor files" but actual behavior may not populate it for all Workspace files (Google Support thread from 2025 confirms inconsistency).
**How to avoid:** The `info` command's verbose mode should handle missing `size` gracefully (display "N/A" or omit). Don't rely on `size` for any logic.
**Warning signs:** KeyError or None when accessing `size` from metadata dict.

### Pitfall 6: `extract_doc_id()` Doesn't Handle Folder URLs
**What goes wrong:** Passing a folder URL like `https://drive.google.com/drive/folders/1aBcDeFg` to `extract_doc_id()` raises `ValueError` -- tested and confirmed.
**Why it happens:** Current regex patterns are `/d/([...])` and `?id=([...])`. Folder URLs use `/folders/([...])` pattern.
**How to avoid:** Add `/folders/` pattern to `_PATTERNS` in `util.py`. Pattern: `re.compile(r'/folders/([a-zA-Z0-9_-]+)')`.
**Warning signs:** `ls` with folder URL argument failing with "Cannot extract document ID" error.

### Pitfall 7: Non-Exportable File in `cat`
**What goes wrong:** Running `cat` on a PDF or image uploaded to Drive gives a confusing 403 error.
**Why it happens:** `files.export` only works on Google Workspace editor files (Docs, Sheets, Slides). Non-editor files return HTTP 403 with message "Export only supports Docs Editors files."
**How to avoid:** In `_translate_http_error`, check for the specific 403 message about export, and raise a more helpful `GdocError("Cannot export file as markdown: file is not a Google Docs editor document")`.
**Warning signs:** 403 errors on files that the user definitely has access to.

## Code Examples

Verified patterns from official sources and installed library inspection:

### Building the Drive Service
```python
# Source: google-api-python-client docs/oauth-installed.md (verified via Context7)
from googleapiclient.discovery import build
drive_service = build('drive', 'v3', credentials=credentials)
```

### Exporting a Document as Markdown
```python
# Source: Official Google Drive API reference (files.export)
# Confirmed: text/markdown is a valid export MIME type for Google Documents
request = service.files().export_media(
    fileId=doc_id, mimeType="text/markdown"
)
content_bytes = request.execute()  # Returns bytes for sub-10MB docs
content_str = content_bytes.decode("utf-8")
```

### Listing Files with Query
```python
# Source: Official Google Drive API reference (files.list)
response = service.files().list(
    q="'root' in parents and trashed=false",
    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
    pageSize=100,
).execute()
files = response.get("files", [])
next_token = response.get("nextPageToken")
```

### Searching Files
```python
# Source: Official Google Drive API search guide
# Note: Single quotes in query values must be escaped with backslash
escaped = query.replace("\\", "\\\\").replace("'", "\\'")
q = f"(name contains '{escaped}' or fullText contains '{escaped}') and trashed=false"
```

### Getting File Metadata
```python
# Source: Official Google Drive API reference (files.get)
# Note: Nested field syntax uses parentheses
metadata = service.files().get(
    fileId=doc_id,
    fields="id, name, mimeType, modifiedTime, createdTime, "
           "owners(emailAddress, displayName), "
           "lastModifyingUser(emailAddress, displayName), size"
).execute()
# owners is a list, lastModifyingUser is a single object
owner = metadata.get("owners", [{}])[0]
```

### HttpError Handling
```python
# Source: Verified from installed googleapiclient.errors source (v2.189.0)
from googleapiclient.errors import HttpError

try:
    result = service.files().get(fileId=doc_id, fields="id,name").execute()
except HttpError as e:
    status = e.status_code     # int, e.g. 404 (property accessing self.resp.status)
    reason = e.reason          # str, parsed from JSON error body
    details = e.error_details  # str or list, additional error details
```

### format_json Helper
```python
# New helper for format.py
import json

def format_json(**data) -> str:
    """Format structured data as JSON with ok=True wrapper."""
    return json.dumps({"ok": True, **data})
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `text/html` export + markdown converter | Native `text/markdown` export MIME type | July 2024 | No third-party markdown conversion library needed |
| `files().export().execute()` | `files().export_media().execute()` | Gradual shift in official docs | `export_media()` is the recommended path; returns raw bytes |
| `e.resp.status` for HTTP status | `e.status_code` property | Added to `HttpError` class | Cleaner API; property wraps `self.resp.status` |
| `run_console()` for headless OAuth | `run_local_server(open_browser=False)` | google-auth-oauthlib 1.0.0 | `run_console()` removed (Phase 1 already handles this) |

**Deprecated/outdated:**
- `files().export().execute()` for content: While it may work, official samples exclusively use `export_media()`. Prefer `export_media()`.
- `supportsTeamDrives` parameter: Deprecated in favor of `supportsAllDrives` (not used in Phase 2 anyway).

## Discretion Recommendations

Based on research, here are recommendations for areas left to Claude's discretion:

### 1. Export Method: Use `export_media().execute()`
**Recommendation:** Use `service.files().export_media(fileId=id, mimeType=mime).execute()` for simple export. This returns raw bytes directly. No `MediaIoBaseDownload` needed for sub-10MB text content. The `export_media()` method is the officially documented path. Decode result with `.decode("utf-8")`.

**Confidence:** HIGH -- verified against official Google Drive API download guide and installed library.

### 2. Date Formatting in Terse Output: Truncated ISO 8601
**Recommendation:** Use `YYYY-MM-DD` format (e.g., `2025-01-15`) for terse `ls`/`find` output. Parse the RFC 3339 timestamp from the API (`2025-01-15T10:30:00.000Z`) and truncate to date. For verbose output, use full ISO 8601 (`2025-01-15T10:30:00Z`). This matches the `gdoc.md` example which shows `2025-01-15` in `ls` output.

**Confidence:** HIGH -- matches spec examples.

### 3. Test Structure: Per-Command Files + API Layer
**Recommendation:** Create `test_cat.py`, `test_ls.py`, `test_find.py`, `test_info.py` for command-level tests (subprocess-based, matching Phase 1 pattern), plus `test_api_drive.py` for unit testing `api/drive.py` functions with mocked `HttpError` and mocked service objects.

**Confidence:** HIGH -- follows Phase 1 established patterns.

### 4. API Functions Return Raw Data
**Recommendation:** `api/drive.py` functions return raw types: `export_doc()` returns `str`, `list_files()` returns `list[dict]`, `get_file_info()` returns `dict`. No typed wrappers. Command handlers select the fields they need.

**Confidence:** HIGH -- simplest approach, no premature abstraction.

### 5. `--comments` Stub Behavior
**Recommendation:** Use the same stub pattern as Phase 1: print `ERR: cat --comments is not yet implemented` to stderr, return exit code 4 with `# STUB` comment. This keeps the CI gate (`check-no-stubs.sh`) active as a reminder.

**Confidence:** HIGH -- consistent with Phase 1 pattern.

### 6. `lru_cache` on `get_drive_service()`
**Recommendation:** Use `@lru_cache(maxsize=1)` as decided in Phase 1 CONTEXT. A no-argument function with `lru_cache` is trivial and works correctly. The service object is thread-safe for sequential CLI use.

**Confidence:** HIGH -- verified `lru_cache` compatibility with `build()`.

## Open Questions

1. **`export_media().execute()` return type verification**
   - What we know: Official docs say `files.export` returns "file content as bytes." The `export_media()` method should return raw bytes when `.execute()` is called.
   - What's unclear: Whether `.execute()` on `export_media()` directly returns bytes, or requires `MediaIoBaseDownload`. Cannot test without valid credentials.
   - Recommendation: Implement with `export_media().execute()`, add a fallback comment noting that `MediaIoBaseDownload` pattern can be used if this doesn't work. This should be validated in the first manual test.

2. **`size` field reliability for Google Workspace documents**
   - What we know: Documentation says size applies to "blobs and Google Workspace editor files." A 2025 Google Support thread reports inconsistency.
   - What's unclear: Whether `size` is reliably populated for Google Docs in current API version.
   - Recommendation: Request `size` in fields but handle `None`/missing gracefully. Display "N/A" in verbose output if not present.

## Sources

### Primary (HIGH confidence)
- `/googleapis/google-api-python-client` (Context7) -- Drive API v3 files.list, files.get, files.export endpoint documentation, HttpError class source
- Google Drive API reference: https://developers.google.com/workspace/drive/api/reference/rest/v3/files -- files.get, files.list, files.export methods
- Google Drive API export MIME types: https://developers.google.com/workspace/drive/api/guides/ref-export-formats -- Confirmed `text/markdown` for Google Documents
- Google Drive API search terms: https://developers.google.com/workspace/drive/api/guides/ref-search-terms -- Query syntax, operators, escaping rules
- Google Drive API search guide: https://developers.google.com/workspace/drive/api/guides/search-files -- Search examples, query patterns
- Google Drive API download guide: https://developers.google.com/workspace/drive/api/guides/manage-downloads -- Export patterns, `export_media()` usage
- Installed library inspection: `googleapiclient.errors.HttpError` source code (v2.189.0) -- `status_code` property, `reason` attribute, `error_details`

### Secondary (MEDIUM confidence)
- StackOverflow: Google Drive API files.export 403 error for non-Docs files -- Confirms "Export only supports Docs Editors files" error message
- StackOverflow: `name contains` with digit-only strings -- Confirms tokenization limitations
- Google Drive API fields parameter guide: https://developers.google.com/workspace/drive/api/guides/fields-parameter -- FieldMask syntax for nested fields

### Tertiary (LOW confidence)
- Google Support thread (2025): `size` field not populated for large Google Docs -- Needs real-world validation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- All libraries already installed, versions confirmed, no new deps
- Architecture: HIGH -- Patterns verified against official docs and Phase 1 codebase
- Pitfalls: HIGH -- Most pitfalls verified via code testing (extract_doc_id folder URLs), official docs (query escaping, export MIME types), or library source inspection (HttpError attributes)

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (stable APIs, no expected breaking changes)
