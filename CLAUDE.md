# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_cat.py -v

# Run a single test by name
uv run pytest tests/test_cat.py -k "test_name" -v

# Lint
uv run ruff check gdoc/ tests/

# CI stub gate (ensure no stub exit codes remain)
bash scripts/check-no-stubs.sh

# Install in dev mode
uv sync --extra dev
```

## Architecture

**gdoc** is a token-efficient CLI for AI agents to interact with Google Docs and Drive. It uses three Google APIs: Drive v3 (file ops, export, comments), Docs v1 (text replacement), and OAuth2 for auth.

### Layered Design

1. **CLI layer** (`gdoc/cli.py`): Argument parsing, subcommand dispatch, exception handling. Uses a custom `GdocArgumentParser` that exits with code 3 (not argparse's default 2). All subcommand handlers (`cmd_cat`, `cmd_edit`, etc.) live here. Imports of auth/API modules are **lazy** (inside functions) so `gdoc --help` works without Google libraries installed.

2. **API layer** (`gdoc/api/`): Thin wrappers around Google APIs. Each module translates `HttpError` into `GdocError`/`AuthError` at the API boundary — the CLI layer does not catch `HttpError`. Service objects are cached via `@lru_cache(maxsize=1)`.
   - `api/drive.py` — Drive v3: export, list, search, file info, update content, create, copy, share
   - `api/docs.py` — Docs v1: `replace_all_text` (for the `edit` command)
   - `api/comments.py` — Drive v3 comments: list (paginated), create comment, create reply

3. **Awareness system** (`gdoc/state.py` + `gdoc/notify.py`): Tracks per-document state in `~/.gdoc/state/{doc_id}.json`. Before most commands, `pre_flight()` detects changes (edits, new comments, resolved/reopened) since last interaction and prints a banner to stderr. The `write` command uses version tracking to prevent conflicts (blocks unless `--force`).

4. **Formatting** (`gdoc/format.py`): Three output modes — `terse` (default), `verbose` (`--verbose`), `json` (`--json`). These flags are mutually exclusive. Errors always go to stderr as `ERR: <message>`, even in `--json` mode.

5. **Annotation engine** (`gdoc/annotate.py`): Powers `cat --comments`. Produces line-numbered content with inline comment annotations, matching anchor text to source lines.

### Exit Codes

- **0**: Success
- **1**: API/unexpected error
- **2**: Auth error (`AuthError`)
- **3**: Usage/validation error (bad args, no match, missing baseline)

### Error Handling Pattern

`GdocError` carries an `exit_code` field (default 1). `AuthError` is a subclass with exit_code=2. The `main()` function catches both and prints `ERR: <message>` to stderr. Validation errors in handlers raise `GdocError(msg, exit_code=3)`.

### Key Conventions

- Doc arguments accept both full Google URLs and bare document IDs — `_resolve_doc_id()` handles extraction
- `--quiet` skips pre-flight checks (no API calls for change detection)
- `--force` on `write` bypasses conflict detection
- All state files live under `~/.gdoc/` (token, credentials, per-doc state)
- Tests mock Google API calls at the `gdoc.api` layer using `pytest-mock`
