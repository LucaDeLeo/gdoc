# Phase 06 Summary: File Management

## Status: COMPLETE

## What Was Built

### Plan 06-01: File Management Commands (`new`, `cp`, `share`)

**Drive API Wrappers** (`gdoc/api/drive.py`):
- `create_doc(title, folder_id=None)` — Creates blank Google Doc via `files.create`, returns id/name/version/webViewLink
- `copy_doc(doc_id, title)` — Duplicates via `files.copy`, returns same fields
- `create_permission(doc_id, email, role)` — Shares via `permissions.create` with email notification
- All three use `_translate_http_error` for consistent 401/403/404 handling

**CLI Handlers** (`gdoc/cli.py`):
- `cmd_new` — Creates blank doc, outputs bare ID (terse), JSON with id/title/url, or verbose multi-line. Seeds state for new doc. No pre-flight (new doc has no history).
- `cmd_cp` — Duplicates doc, pre-flights source doc, seeds state for both source and copy. Same output modes.
- `cmd_share` — Shares doc with email+role, pre-flights target doc, outputs "OK shared with EMAIL as ROLE" or JSON.
- `cmd_stub` deleted — All commands now have real implementations.

**State Seeding**: New/copied docs get `last_version` seeded via `update_state_after_command` so subsequent commands don't trigger "first interaction" banners.

**Parser Wiring**: `new`, `cp`, `share` parsers updated from `cmd_stub` to their real handlers.

## Test Coverage
- 50 new tests in `tests/test_file_mgmt.py` (12 new, 12 cp, 10 share, 16 API)
- 3 new stub-removal tests in `tests/test_cli.py`
- **393 total tests passing** (up from 340)

## Verification Checklist
- [x] `gdoc new "Test"` creates a doc and prints the ID
- [x] `gdoc new "Test" --folder FOLDER_ID` creates a doc in the specified folder
- [x] `gdoc cp DOC_ID "Copy Title"` duplicates a doc and prints the new ID
- [x] `gdoc share DOC_ID email@example.com --role writer` shares the doc
- [x] All three commands support `--json` and `--verbose` output modes
- [x] `cmd_stub` is deleted and `scripts/check-no-stubs.sh` passes
- [x] State is seeded for new/copied docs (no "first interaction" on next command)
- [x] Pre-flight runs for `cp` and `share` but NOT for `new`
- [x] All tests pass: `pytest tests/ -v` (393 passed)

## Commits
1. `ebec543` — feat(phase-06): implement file management commands (new, cp, share)

## Deviations
None. Plan followed exactly as written.
