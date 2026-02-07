# Plan 05-01 Summary: Comment CRUD Commands

## What Was Built
- Extended `list_comments` with `include_resolved` (client-side filtering) and `include_anchor` (quotedFileContent field) parameters
- Added `create_comment` and `create_reply` API wrappers with error translation
- Removed fake `includeResolved` API param (was silently ignored per research)
- Added `createdTime` to comment fields for date display
- Added `comment_state_patch` parameter to `update_state_after_command` for targeted ID set mutations
- Implemented 5 CLI handlers: `cmd_comments`, `cmd_comment`, `cmd_reply`, `cmd_resolve`, `cmd_reopen`
- Wired all 5 subparsers from `cmd_stub` to real handlers
- Added `--all` flag to `cat` subparser for future Plan 05-02

## Test Results
- 314 tests pass (was 270), 0 failures
- 19 new API tests (filtering, create_comment, create_reply)
- 11 new state patch tests
- 23 new comment command tests

## Deviations
- Updated 4 tests in `test_cli.py` that relied on `comment` being a stub (exit code 4 → assert != 3)
- No architectural deviations from plan

## Commit
`ecb26f3` — feat(phase-05): implement comment CRUD commands with API layer and state patches
