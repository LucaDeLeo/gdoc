# Plan 03-02 Summary: CLI Awareness Integration

## What Was Built

### New Functions
- **gdoc/state.py: `update_state_after_command`** — Centralizes post-command state update rules for all Decision #6/#14 scenarios

### Modified Files
- **gdoc/cli.py** — `cmd_cat` and `cmd_info` now call `pre_flight` before execution and `update_state_after_command` after success; `--quiet` flag added to `share` and `cp` parsers

### Updated Tests
- **tests/test_cat.py** — All existing tests updated with awareness mocks; 6 new awareness tests (preflight called, quiet bypass, state update, comments stub skips, error prevents update)
- **tests/test_info.py** — All existing tests updated with awareness mocks; 5 new awareness tests (preflight called, quiet passthrough, version from get_file_info, quiet version, error prevents update)
- **tests/test_state.py** — 8 new `update_state_after_command` integration tests covering: normal cat/info, quiet cat (stale version), quiet info (command version), comment check freeze, first interaction, non-read command, last_seen always updated

## Key Decisions Implemented
- `cmd_cat` calls `pre_flight` → `export_doc` → `update_state_after_command` (no version param)
- `cmd_info` calls `pre_flight` → `get_file_info` → `export_doc` → `update_state_after_command` (with `command_version` from metadata)
- `--quiet cat` leaves version fields stale (Decision #14)
- `--quiet info` updates version from `get_file_info` response (Decision #14)
- `--comments` stub returns before pre_flight (no wasted API calls)
- Errors in export_doc/get_file_info prevent state update (state only saved on success)
- `share` and `cp` parsers now accept `--quiet` (Decision #2)

## Test Count
- New tests: 19 (6 cat awareness + 5 info awareness + 8 state integration)
- Total after plan: 190 (168 + 22, minus 3 existing that were just updated)

## Commit
- `7877a58` — feat(phase-03): integrate awareness into CLI handlers
