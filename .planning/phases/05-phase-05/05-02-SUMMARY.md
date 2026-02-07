# Plan 05-02 Summary: Annotated View (cat --comments)

## What Was Built
- Created `gdoc/annotate.py` with `annotate_markdown(markdown, comments, show_resolved)` function
- Anchor mapping: single match (inline), multiple matches (ambiguous → unanchored), zero matches (deleted → unanchored), short anchor (<4 chars → unanchored), multi-line anchor (annotate after last line)
- Line number format: `%6d\t` for content, `      \t` for annotation lines
- Unanchored section at bottom with `[UNANCHORED]` header
- Fallback notes: `[anchor ambiguous]`, `[anchor deleted]`, `[anchor too short]`
- Wired `cat --comments` in `cmd_cat` replacing the stub
- `--all` flag controls resolved comment inclusion with `[resolved]` markers
- JSON mode wraps annotated content in standard format

## Test Results
- 340 tests pass (was 314), 0 failures
- 21 new annotation engine tests (pure logic, no mocking)
- 6 new cat --comments integration tests
- Updated 1 awareness test from stub expectation to real behavior

## Deviations
- None from plan

## Commit
`e22e880` — feat(phase-05): implement cat --comments annotated view with anchor mapping
