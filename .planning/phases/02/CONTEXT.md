# Phase 02 - Context (Auto-Generated)

**Generated:** 2026-02-07T19:30:48Z
**Method:** Claude <> Codex dialogue
**Status:** Ready for planning

## Milestone Anchor

**Milestone Goal:** Users can authenticate and the CLI framework handles input parsing, output formatting, and error reporting consistently across all future commands

**Requirements:** READ-01, READ-03, READ-04, READ-05, READ-06

## Implementation Decisions

### API Module Structure

| Decision | Detail | Source |
|----------|--------|--------|
| Package layout | `gdoc/api/__init__.py` (service factories), `gdoc/api/drive.py` (Drive API wrappers) | Codex questioned single-file `api.py`; resolved to match `gdoc.md` architecture (`api/drive.py`, `api/docs.py`, etc.) |
| Service factory | `get_drive_service()` in `gdoc/api/__init__.py` calls `get_credentials()` then `build("drive", "v3", credentials=creds)`. Cached via `@lru_cache` (already decided in Phase 01 CONTEXT). | Both agreed |
| API function location | All Drive API call wrappers (`export_doc`, `list_files`, `search_files`, `get_file_info`) live in `gdoc/api/drive.py` | Codex suggested package; Claude aligned to gdoc.md architecture |
| Not created in Phase 2 | `api/docs.py` (Phase 4), `api/comments.py` (Phase 5) — created when needed with real content | Both agreed (same policy as Phase 1) |
| Handler pattern | Command handlers in `cli.py` stay thin — parse args, call an `api/drive.py` function, format output | Both agreed |

### `cat` — Document Export

| Decision | Detail | Source |
|----------|--------|--------|
| Default export | `files.export(fileId=doc_id, mimeType="text/markdown")` — native markdown export (Drive API, July 2024) | Both agreed |
| `--plain` flag | `files.export(fileId=doc_id, mimeType="text/plain")` | Both agreed |
| Stdout behavior | Raw content to stdout (no `format_success` wrapper). Matches unix `cat` semantics — agents/pipes expect raw content. | Both agreed |
| `--json` mode | Wrap content: `{"ok": true, "content": "...markdown here..."}`. Uses new `format_json` helper (see Output Formatting below). | Claude proposed; Codex flagged inconsistency with `format_success` — resolved via `format_json` |
| Export size limit | Accept 10MB Drive API export limit for v1. No chunked download needed. | Both agreed |
| Non-exportable files | If export fails (e.g., trying to `cat` a PDF or binary), raise `GdocError` with clear message: "Cannot export file as markdown: {reason}" | Codex identified gap; Claude resolved |
| `--comments` flag | **Not implemented in Phase 2** — registered in argparse (Phase 1) but handler deferred to Phase 5 (Comments & Annotations). Returns clear "not yet implemented" error if used. | Both agreed (per roadmap) |

### `ls` — File Listing

| Decision | Detail | Source |
|----------|--------|--------|
| API call | `files.list(q=query, fields="nextPageToken, files(id, name, mimeType, modifiedTime)", pageSize=100)` with auto-pagination | Both agreed |
| Default scope | `'root' in parents` when no `folder_id` argument provided — matches `gdoc.md` comment "default: root" | Codex identified; Claude aligned to gdoc.md spec |
| Folder argument | When `folder_id` provided, use `'{folder_id}' in parents` | Both agreed |
| Type filter | `--type docs` adds `mimeType='application/vnd.google-apps.document'`; `--type sheets` adds `mimeType='application/vnd.google-apps.spreadsheet'`; `--type all` (default) adds no mimeType filter | Both agreed |
| Trash filter | Always include `trashed=false` in query | Both agreed |
| Terse output | `ID\tTITLE\tMODIFIED` per line (tab-separated, three columns) — matches `gdoc.md` output example showing ID, TITLE, MODIFIED | Codex flagged conflict with original `ID\tNAME` proposal; resolved to match gdoc.md |
| Verbose output | Add mimeType column: `ID\tTITLE\tMODIFIED\tTYPE` | Both agreed |
| JSON output | `{"ok": true, "files": [{"id": "...", "name": "...", "mimeType": "...", "modifiedTime": "..."}]}` | Both agreed |
| Pagination | Fetch all pages internally. No `--limit` or `--page-token` in v1. | Both agreed |
| Folder ID from URL | `folder_id` argument passed through `extract_doc_id()` to support both bare IDs and URLs | Codex identified gap (folder IDs can be URLs); Claude resolved |

### `find` — File Search

| Decision | Detail | Source |
|----------|--------|--------|
| Query construction | `(name contains '{query}' or fullText contains '{query}') and trashed=false` — combined name + content search | Codex suggested combined query over `fullText` alone; Claude incorporated |
| Input escaping | Escape backslashes (`\` → `\\`) then single quotes (`'` → `\'`) in user query before embedding in Drive `q` string. Order matters: escape `\` first to avoid double-escaping. | Codex identified gap; Claude incorporated; backslash escaping added per follow-up review |
| Output format | Same as `ls` — `ID\tTITLE\tMODIFIED` terse, structured JSON in `--json` mode | Both agreed |
| Pagination | Same auto-pagination as `ls` | Both agreed |

### `info` — Document Metadata

| Decision | Detail | Source |
|----------|--------|--------|
| Metadata call | `files.get(fileId=doc_id, fields="id, name, mimeType, modifiedTime, createdTime, owners(emailAddress, displayName), lastModifyingUser(emailAddress, displayName), size")` | Codex suggested nested fields for owners; Claude incorporated |
| Word count | Always computed via `files.export(mimeType="text/plain")` + `len(text.split())`. Two API calls per `info` invocation. | Both agreed — meets success criteria explicitly listing "word count" |
| Terse output | Key-value pairs, one per line: `Title: ...`, `Owner: ...`, `Modified: ...`, `Words: ...` | Both agreed |
| Verbose output | Add createdTime, lastModifyingUser, mimeType, size | Both agreed |
| JSON output | Flat object per gdoc.md example: `{"id": "...", "title": "...", "owner": "...", "modified": "...", "words": ...}` | Both agreed (per gdoc.md `info --json` example) |

### Doc ID Resolution

| Decision | Detail | Source |
|----------|--------|--------|
| Pattern | Each command handler calls `extract_doc_id(args.doc)` at top, wraps `ValueError` in `GdocError(message, exit_code=3)` | Both agreed |
| Location | In command handler, not in argparse type= | Both agreed — keeps argparse clean, better error messages |

### Error Handling for API Calls

| Decision | Detail | Source |
|----------|--------|--------|
| Error translation | Catch `googleapiclient.errors.HttpError` in `api/drive.py` and translate to typed exceptions | Both agreed |
| 401 Unauthorized | Raise `AuthError("Authentication expired. Run `gdoc auth`.")` — exit code 2 | Codex suggested mapping 401 to AuthError; Claude incorporated |
| 403 Forbidden | Raise `GdocError("Permission denied: {id}")` — exit code 1 | Both agreed |
| 404 Not Found | Raise `GdocError("Document not found: {id}")` — exit code 1 | Both agreed |
| Other HTTP errors | Raise `GdocError("API error ({status}): {reason}")` — exit code 1 | Both agreed |
| ERR: prefix | **Not** added in API layer. `cli.py`'s top-level exception handler adds `ERR:` prefix (established Phase 1 pattern). API layer raises clean exceptions. | Codex identified; Claude confirmed existing pattern |

### Output Formatting Evolution

| Decision | Detail | Source |
|----------|--------|--------|
| New helper: `format_json` | `format_json(**data)` returns `json.dumps({"ok": True, **data})`. Used by all commands for structured JSON. | Codex flagged inconsistency between per-command JSON and `format_success`; resolved with new helper |
| `format_success` retained | Still used for simple string confirmations (e.g., "OK replaced 1 occurrence" in future phases) | Both agreed |
| Per-command JSON | Each command defines its own JSON structure using `format_json`. `cat`: `format_json(content=text)`. `ls`/`find`: `format_json(files=list)`. `info`: `format_json(**metadata_dict)`. | Codex flagged need for consistent schema; Claude resolved |

## Resolved Decisions (Previously Flagged)

> **1. Shared Drive support** — **Deferred.** Phase 2 targets personal Drive only. When shared drive support is added in a future phase, pass `supportsAllDrives=True`, `includeItemsFromAllDrives=True`, and `corpora="allDrives"` to `files.list`. No shared-drive code in Phase 2.

> **2. `ls` with no argument: root folder vs. all files** — **Locked: `'root' in parents`** per `gdoc.md` "default: root" comment. Users use `find` for broader search. Can revisit based on user feedback.

> **3. Query escaping** — Escape both backslashes (`\` → `\\`) and single quotes (`'` → `\'`) in user input before embedding in Drive `q` strings. Backslashes escaped first to avoid double-escaping. Applies to `find` and any command building `q` parameters.

> **4. Non-Google-Docs IDs in `cat`/`info`** — **Explicit error for `cat`.** If `files.export` fails because the file is not a Google Docs editor file (e.g., PDF, image, binary), raise `GdocError("Cannot export file as markdown: {reason}")` with exit code 1. The `info` command uses `files.get` which works on any Drive file type, so no special handling needed there.

## Claude's Discretion

- Exact `fields` parameter lists for `files.list` and `files.get` (may adjust based on what the API actually returns for nested owner/user fields)
- Whether to use `MediaIoBaseDownload` or simple `.execute()` for export (simple `.execute()` should work for sub-10MB docs)
- Verbose output formatting details (date format — ISO 8601 truncated to date vs. full timestamp, column alignment)
- Test structure — likely one test file per command (`test_cat.py`, `test_ls.py`, `test_find.py`, `test_info.py`) plus `test_api_drive.py` for the API wrapper layer, following Phase 1 patterns
- Whether `api/drive.py` functions return raw data or typed dicts (likely raw dicts, with formatting in handlers)
- `lru_cache` on `get_drive_service()` vs. passing service as parameter (Phase 01 decided `@lru_cache`)
- How the `--comments` flag on `cat` reports "not yet implemented" in Phase 2 (likely same stub pattern as Phase 1 with exit code 4 and clear message)
- Tab-separated date formatting in terse `ls`/`find` output (e.g., `2025-01-15` vs `2025-01-15T10:30:00Z`)

---
*Auto-generated via milestone sprint*
