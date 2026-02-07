# Plan 01-01 Summary: Project Scaffolding, Core Utilities, Output Formatting

**Status:** COMPLETE
**Commit:** 6c417ba

## What Was Built

1. **pyproject.toml** — Hatchling build backend, Python >=3.10, 3 runtime dependencies (google-api-python-client, google-auth-oauthlib, google-auth-httplib2), dev extras (pytest, pytest-mock, ruff), console_script entry point `gdoc = "gdoc.cli:main"`
2. **gdoc/__init__.py** — Package marker with `__version__ = "0.1.0"`
3. **gdoc/__main__.py** — `python -m gdoc` entry point
4. **gdoc/util.py** — `extract_doc_id()` (handles /d/ID, ?id=ID, bare ID), `GdocError` (exit 1), `AuthError` (exit 2), config path constants
5. **gdoc/format.py** — `get_output_mode()`, `format_success()`, `format_error()` (ERR: prefix)
6. **tests/test_util.py** — 16 tests: URL extraction (7 formats + 4 error cases), error classes (5 tests)
7. **tests/test_format.py** — 9 tests: output mode selection (4), success formatting (3), error formatting (2)

## Test Results

25/25 tests pass.

## Deviations

None — implemented exactly as planned.
