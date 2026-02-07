# Architecture Research

**Domain:** Python CLI wrapping Google Docs/Drive APIs
**Researched:** 2026-02-07
**Confidence:** HIGH (google-api-python-client, argparse, OAuth2 patterns are mature and well-documented; architecture draws from established conventions)

## Standard Architecture

### System Overview

```
                              CLI Layer
┌──────────────────────────────────────────────────────────┐
│  __main__.py ──► cli.py (argparse dispatch)              │
│                    │                                     │
│        ┌───────────┼────────────┬──────────┐             │
│        ▼           ▼            ▼          ▽             │
│   [cat/push]  [edit/replace] [comments]  [ls/find]       │
│   (read ops)  (write ops)   (comment ops)(browse ops)    │
└────────┬───────────┬────────────┬──────────┬─────────────┘
         │           │            │          │
         │    Middleware Layer    │          │
┌────────┴───────────┴────────────┴──────────┴─────────────┐
│  notify.py (pre-flight awareness check)                  │
│  state.py  (per-doc state read/write)                    │
│  format.py (output mode selection)                       │
└────────┬───────────┬────────────┬──────────┬─────────────┘
         │           │            │          │
         │      API Client Layer │          │
┌────────┴───────────┴────────────┴──────────┴─────────────┐
│  api/drive.py   api/docs.py   api/comments.py            │
│  (Drive v3)     (Docs v1)     (Drive v3 comments)        │
└────────┬───────────┬────────────┬────────────────────────┘
         │           │            │
         │     Auth Layer         │
┌────────┴───────────┴────────────┴────────────────────────┐
│  auth.py (OAuth2 flow, credential caching, auto-refresh) │
│  ~/.gdoc/credentials.json   ~/.gdoc/token.json           │
└──────────────────────────────────────────────────────────┘
         │
         ▽
   Google APIs (Drive v3 + Docs v1)
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| `__main__.py` | Entry point for `python -m gdoc` and `gdoc` console_script | 3-line file: imports `cli.main`, calls `main()` |
| `cli.py` | Argument parsing, subcommand dispatch, global flags (`--json`, `--quiet`, `--verbose`) | argparse with `add_subparsers`, one function per command |
| `auth.py` | OAuth2 installed-app flow, credential storage, transparent token refresh | `google-auth-oauthlib` InstalledAppFlow + serialized Credentials |
| `api/drive.py` | Drive API v3 operations: list, search, export, upload, share, copy | Thin wrappers returning dicts/lists, no formatting |
| `api/docs.py` | Docs API v1 operations: batchUpdate (insert, delete, replace), documents.get | Builds request bodies, returns raw responses |
| `api/comments.py` | Drive API v3 comments + replies: list, create, reply, resolve, reopen | Wraps comments/replies resources |
| `state.py` | Per-doc state CRUD in `~/.gdoc/state/{DOC_ID}.json` | JSON read/write with atomic file operations |
| `notify.py` | Pre-flight change detection, diff computation, banner string construction | Consumes state.py + api calls, produces banner strings |
| `annotate.py` | Inline comment injection into markdown for `cat --comments` | String processing, no API calls |
| `format.py` | Output formatting: terse tables, JSON, verbose human-readable | Formatter functions that accept data dicts, return strings |
| `util.py` | URL-to-ID extraction, common error handling, shared constants | Pure functions, no state or API calls |

## Recommended Project Structure

```
gdoc/
├── __init__.py              # Package marker, version string
├── __main__.py              # Entry: sys.exit(main())
├── cli.py                   # argparse subcommand dispatch + global flags
├── auth.py                  # OAuth2 flow + credential caching
├── api/
│   ├── __init__.py          # Shared: get_service() factory
│   ├── drive.py             # Drive API v3 wrappers
│   ├── docs.py              # Docs API v1 wrappers
│   └── comments.py          # Drive comments + replies wrappers
├── state.py                 # Per-doc state persistence
├── notify.py                # Pre-flight awareness check + banner
├── annotate.py              # Comment annotation injection
├── format.py                # Output formatters (terse/json/verbose)
└── util.py                  # URL-to-ID, error helpers, constants
tests/
├── conftest.py              # Shared fixtures: mock credentials, mock services
├── test_cli.py              # End-to-end CLI invocation tests
├── test_auth.py             # Auth flow tests
├── test_api_drive.py        # Drive API wrapper tests
├── test_api_docs.py         # Docs API wrapper tests
├── test_api_comments.py     # Comments API wrapper tests
├── test_state.py            # State persistence tests
├── test_notify.py           # Pre-flight + banner tests
├── test_annotate.py         # Comment injection tests
├── test_format.py           # Output formatter tests
└── test_util.py             # Utility function tests
pyproject.toml               # Project metadata, dependencies, console_script entry
```

### Structure Rationale

- **`api/` sub-package:** Isolates all Google API interactions behind a clean boundary. Every module in `api/` takes a `service` object and returns raw Python data (dicts, lists). No formatting, no state, no CLI concerns. This makes them independently testable with mocked service objects.
- **`cli.py` as single file (not package):** For a tool with ~15 subcommands, a single `cli.py` with argparse is sufficient and simpler than a multi-file command package. Each subcommand handler is a function that orchestrates: pre-flight check, API call, formatting, output. If it grows past 500 lines, split into `cli/` package with one file per command group.
- **`state.py` and `notify.py` separate:** State is pure persistence (read/write JSON). Notify is logic that consumes state + makes API calls to detect changes. Keeping them separate means state operations are trivially testable without mocking APIs.
- **`format.py` separate from `cli.py`:** Formatters are pure functions (data in, string out). They should not import anything from CLI or API layers. This makes them testable with simple dict inputs.
- **Tests mirror source:** One test file per source module. `conftest.py` provides shared fixtures for mock Google API services.

## Architectural Patterns

### Pattern 1: Lazy Service Singleton

**What:** Build Google API service objects once per CLI invocation, lazily on first use. Store on a module-level or passed-through context object.
**When to use:** Every command that touches Google APIs (which is almost all of them).
**Trade-offs:** Simple, avoids repeated credential loading. CLI is short-lived (one invocation = one process), so no concern about long-lived connections.

**Example:**
```python
# api/__init__.py
from functools import lru_cache
from gdoc.auth import get_credentials

@lru_cache(maxsize=1)
def get_drive_service():
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build('drive', 'v3', credentials=creds)

@lru_cache(maxsize=1)
def get_docs_service():
    from googleapiclient.discovery import build
    creds = get_credentials()
    return build('docs', 'v1', credentials=creds)
```

**Why not dependency injection here:** CLI tools are short-lived processes. A simple cached factory is more pragmatic than DI. Tests override via `unittest.mock.patch`.

### Pattern 2: Command Handler Convention

**What:** Each subcommand maps to a handler function with a consistent signature: receives parsed args, returns an exit code. The handler orchestrates pre-flight, API call, formatting, and output.
**When to use:** Every subcommand in cli.py.
**Trade-offs:** Straightforward, easy to test by calling the handler directly with a mock args namespace. Slightly repetitive (each handler does pre-flight + format), but explicit is better than magic.

**Example:**
```python
# cli.py
def cmd_cat(args):
    """Handler for `gdoc cat DOC_ID`."""
    doc_id = util.extract_id(args.doc)

    # Pre-flight awareness (unless --quiet)
    if not args.quiet:
        banner = notify.pre_flight_check(doc_id)
        if banner:
            print(banner, file=sys.stderr)

    # API call
    if args.comments:
        content = api.drive.export_markdown(doc_id)
        comments = api.comments.list_comments(doc_id)
        content = annotate.inject_comments(content, comments)
    elif args.plain:
        content = api.drive.export_plain(doc_id)
    else:
        content = api.drive.export_markdown(doc_id)

    # Output (stdout, never stderr)
    print(content)

    # Update state
    state.touch(doc_id)
    return 0
```

### Pattern 3: Stderr for Banners, Stdout for Data

**What:** Notification banners, warnings, and progress messages go to stderr. Command output (doc content, listings, JSON) goes to stdout. This makes the CLI pipe-safe.
**When to use:** Always. This is fundamental for a pipe-friendly CLI.
**Trade-offs:** None -- this is standard Unix convention.

**Example:**
```python
# Banners/awareness → stderr (won't corrupt piped data)
print("--- since last interaction (3 min ago) ---", file=sys.stderr)
print(" * doc edited by alice@co.com", file=sys.stderr)

# Data → stdout (pipeable)
print(markdown_content)
```

### Pattern 4: Output Mode Dispatch

**What:** A single `--json` / `--verbose` flag selects the output formatter. Default is terse. Formatters are pure functions.
**When to use:** Every command that produces structured output (ls, comments, info).
**Trade-offs:** Simple branching. Avoids over-engineering with formatter registries.

**Example:**
```python
# format.py
def format_file_list(files, mode='terse'):
    if mode == 'json':
        return json.dumps(files, indent=2)
    elif mode == 'verbose':
        return _verbose_file_list(files)
    else:
        return _terse_file_table(files)

def _terse_file_table(files):
    """Fixed-width columns, no decoration."""
    lines = []
    for f in files:
        lines.append(f"{f['id']:<33} {f['name']:<25} {f['modifiedTime'][:10]}")
    return '\n'.join(lines)
```

### Pattern 5: Pre-flight as Middleware

**What:** The awareness check (2 API calls) runs before every command targeting a DOC_ID, unless `--quiet` is passed. It is not embedded in API wrappers -- it sits between CLI dispatch and API calls.
**When to use:** Every command that takes a DOC_ID argument.
**Trade-offs:** Adds ~200ms latency per invocation (2 lightweight API calls). Worth it for situational awareness. `--quiet` skips it entirely for batch operations.

**Example:**
```python
# notify.py
def pre_flight_check(doc_id):
    """Returns banner string or None if no changes."""
    prev_state = state.load(doc_id)

    if prev_state is None:
        return _first_interaction_banner(doc_id)

    # 2 API calls
    file_meta = api.drive.get_file_meta(doc_id)
    recent_comments = api.comments.list_since(doc_id, prev_state['last_comment_check'])

    changes = _detect_changes(prev_state, file_meta, recent_comments)

    if not changes:
        return "--- no changes ---"

    return _format_banner(changes, prev_state)
```

## Data Flow

### Command Execution Flow

```
User/Agent types: gdoc replace DOC_ID "old" "new"
    │
    ▽
__main__.py → cli.main()
    │
    ▽
cli.py: argparse parses args → dispatches to cmd_replace(args)
    │
    ▽
util.extract_id(args.doc)  →  resolves URL or raw ID
    │
    ▽
notify.pre_flight_check(doc_id)         [MIDDLEWARE]
    ├─► state.load(doc_id)              read ~/.gdoc/state/{id}.json
    ├─► api.drive.get_file_meta(doc_id) 1 API call
    ├─► api.comments.list_since(...)    1 API call
    ├─► _detect_changes(...)            pure comparison logic
    └─► print(banner, file=stderr)      awareness output
    │
    ▽
api.docs.replace_all_text(doc_id, "old", "new")  [CORE OPERATION]
    ├─► auth.get_credentials()          lazy, cached
    ├─► build('docs', 'v1', ...)        lazy, cached
    └─► service.documents().batchUpdate(...)  1 API call
    │
    ▽
format.format_result(result, mode)     [OUTPUT]
    │
    ▽
print(formatted, file=stdout)
    │
    ▽
state.touch(doc_id, new_version=...)   [STATE UPDATE]
    │
    ▽
sys.exit(0)
```

### Credential Flow

```
First run:
  gdoc auth
    ├─► User places credentials.json in ~/.gdoc/
    ├─► InstalledAppFlow.from_client_secrets_file(...)
    ├─► flow.run_local_server(port=0)   opens browser
    ├─► Credentials object returned
    └─► Serialize to ~/.gdoc/token.json

Subsequent runs:
  Any command
    ├─► auth.get_credentials()
    ├─► Load ~/.gdoc/token.json → Credentials.from_authorized_user_file()
    ├─► Check creds.valid / creds.expired
    ├─► If expired: creds.refresh(Request())  automatic
    ├─► If refresh fails: prompt re-auth
    └─► Return valid Credentials object
```

### State Management Flow

```
~/.gdoc/
├── credentials.json       # User-provided OAuth client secrets
├── token.json             # Cached access + refresh token
└── state/
    ├── {DOC_ID_1}.json    # Per-doc state
    └── {DOC_ID_2}.json

Per-doc state file:
{
    "last_seen": "2025-01-20T14:30:00Z",
    "last_version": 847,
    "last_comment_check": "2025-01-20T14:30:00Z",
    "known_comment_ids": ["AAA", "BBB"],
    "last_read_version": 847           // version at last cat/read
}

Read path:  state.load(doc_id) → dict or None
Write path: state.touch(doc_id, **updates) → atomic JSON write
```

### Key Data Flows

1. **Read flow (cat):** `cli → notify(pre-flight) → api.drive.export() → annotate(if --comments) → format → stdout`
2. **Write flow (replace):** `cli → notify(pre-flight, includes conflict warning) → api.docs.batchUpdate() → format result → stdout → state.touch()`
3. **Destructive write flow (push):** Same as write but `notify` checks `last_read_version` and blocks if doc changed since last `cat` (unless `--force`)
4. **Browse flow (ls/find):** `cli → api.drive.list_files() → format → stdout` (no pre-flight, no state -- these target folders not docs)

## Build Order (Dependency Graph)

Components must be built in dependency order. Later components depend on earlier ones.

### Phase 1: Foundation (no Google API calls)

```
util.py          # Pure functions: URL-to-ID, constants, error types
state.py         # JSON read/write to ~/.gdoc/state/
format.py        # Output formatters (terse table, json, verbose)
```

**Rationale:** These are leaf modules with zero external dependencies. They can be built and fully tested without any Google API interaction. `util.py` and `format.py` are pure functions. `state.py` only touches the filesystem.

**Testable immediately:** Yes, with simple unit tests (no mocks needed).

### Phase 2: Auth

```
auth.py          # OAuth2 flow + credential caching
```

**Rationale:** Depends on `util.py` (for config directory paths). Required by all API modules. Must be built before any API wrapper.

**Testable:** Yes, by mocking `google_auth_oauthlib.flow.InstalledAppFlow` and filesystem operations. Integration test requires real credentials.

### Phase 3: API Client Layer

```
api/__init__.py  # get_drive_service(), get_docs_service() factories
api/drive.py     # Drive v3: list, export, upload, share, file meta
api/docs.py      # Docs v1: batchUpdate, documents.get
api/comments.py  # Drive v3: comments + replies CRUD
```

**Rationale:** Depends on `auth.py` for credentials. These are independent of each other (drive.py does not import docs.py). Build in any order, but `drive.py` first is recommended since it serves both browse commands (ls/find) and read commands (cat/export).

**Testable:** Yes, by mocking `googleapiclient.discovery.build` or patching the service factory. Each API module is independently testable.

### Phase 4: Middleware

```
notify.py        # Pre-flight awareness (depends on state.py + api/)
annotate.py      # Comment injection (depends on nothing external)
```

**Rationale:** `notify.py` depends on `state.py` + `api/drive.py` + `api/comments.py`. It is the most cross-cutting component. `annotate.py` is pure string processing and could be built in Phase 1, but logically belongs here since it is only used by the `cat --comments` command.

**Testable:** `annotate.py` is pure functions (unit tests, no mocks). `notify.py` needs mocked API calls and state fixtures.

### Phase 5: CLI Shell

```
cli.py           # argparse, subcommand dispatch, handler functions
__main__.py      # Entry point
__init__.py      # Package, version
```

**Rationale:** Depends on everything. Build last. Each command handler wires together the layers below.

**Testable:** End-to-end tests using `subprocess` or by calling `main()` with patched sys.argv. Also testable by calling individual `cmd_*` handlers with mock args.

### Build Order Summary

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
(foundation)  (auth)    (API)      (middleware)  (CLI)

util.py ─────────────────────────────────────► cli.py
state.py ──────────────► notify.py ──────────► cli.py
format.py ───────────────────────────────────► cli.py
              auth.py ──► api/* ──► notify.py ► cli.py
                                   annotate.py ► cli.py
```

### Incremental Delivery Strategy

Each phase enables a working subset of commands:

| After Phase | Working Commands |
|-------------|-----------------|
| 1-3 | `gdoc auth`, `gdoc ls`, `gdoc cat` (no awareness banners) |
| 1-4 | Above + awareness banners, `cat --comments` |
| 1-5 | All commands fully wired |

## Anti-Patterns

### Anti-Pattern 1: API Calls in CLI Handlers

**What people do:** Put Google API construction and calls directly in cli.py command handlers.
**Why it's wrong:** Makes CLI handlers untestable without mocking Google internals. Mixes presentation concerns with data fetching. Cannot reuse API logic.
**Do this instead:** API modules return raw data (dicts/lists). CLI handlers call API modules, then pass results to formatters. Three clean layers.

### Anti-Pattern 2: Global Mutable Service Object

**What people do:** Create a global `service = build(...)` at module import time.
**Why it's wrong:** Fails at import time if credentials are missing or expired. Makes testing painful (module-level side effects). Breaks `gdoc --help` when not authenticated.
**Do this instead:** Lazy factory with `@lru_cache`. Service object is built on first API call, not at import. `gdoc --help` and `gdoc auth` never trigger service construction.

### Anti-Pattern 3: Formatting Inside API Wrappers

**What people do:** API wrapper functions return formatted strings or print directly.
**Why it's wrong:** Cannot switch between terse/json/verbose without duplicating API logic. Cannot test API logic independently of output format.
**Do this instead:** API wrappers return Python data structures (dicts, lists). Formatting is a separate concern handled by `format.py`.

### Anti-Pattern 4: Stateful Auth Module

**What people do:** Auth module stores credentials in module-level variables and mutates them during refresh.
**Why it's wrong:** Implicit state makes testing unpredictable. Hard to reason about credential lifecycle.
**Do this instead:** `get_credentials()` is a pure-ish function that reads from disk, refreshes if needed, writes back, and returns. Cached with `@lru_cache` for the process lifetime. Tests patch `get_credentials` to return a mock.

### Anti-Pattern 5: Monolithic Pre-flight Check

**What people do:** Embed pre-flight awareness logic (API calls + state comparison + banner formatting) directly in each command handler.
**Why it's wrong:** Duplicated across 10+ handlers. Changes to awareness logic require editing every handler.
**Do this instead:** Single `notify.pre_flight_check(doc_id)` function called from each handler. Returns a banner string (or None). Handler just prints it to stderr.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Google Drive API v3 | `googleapiclient.discovery.build('drive', 'v3')` | Used for file ops (list, export, upload, share) AND comments/replies |
| Google Docs API v1 | `googleapiclient.discovery.build('docs', 'v1')` | Used only for batchUpdate (insert/delete/replace) and documents.get |
| Google OAuth2 | `google_auth_oauthlib.flow.InstalledAppFlow` | Desktop/installed app flow with local redirect |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| cli.py <-> api/* | Direct function calls, data dicts returned | CLI calls API functions, receives raw dicts |
| cli.py <-> format.py | Direct function calls, strings returned | CLI passes data dicts to formatters, receives strings |
| cli.py <-> notify.py | Direct function call, string returned | CLI calls pre_flight_check, receives banner string |
| notify.py <-> state.py | Direct function calls | Notify reads state, CLI updates state after command |
| notify.py <-> api/* | Direct function calls | Notify makes 2 API calls for change detection |
| api/* <-> auth.py | Via api/__init__.py factory | API modules call get_drive_service()/get_docs_service() |
| annotate.py <-> nothing | Pure input/output | Takes markdown string + comment list, returns annotated markdown |

### Boundary Rule

**Direction of dependencies flows downward only:**

```
cli.py (top)
  ├── imports notify.py, format.py, annotate.py, api/*, state.py, util.py
  │
  notify.py (middleware)
  ├── imports state.py, api/drive.py, api/comments.py
  │
  api/* (client layer)
  ├── imports auth.py
  │
  auth.py (foundation)
  ├── imports util.py (for paths)
  │
  util.py, state.py, format.py (leaf modules)
  └── import nothing from gdoc package
```

**No upward imports.** `api/drive.py` must never import from `cli.py`. `state.py` must never import from `notify.py`. Circular imports indicate a boundary violation.

## Testing Strategy

### Layer 1: Pure Unit Tests (no mocks needed)

| Module | What to Test | How |
|--------|-------------|-----|
| `util.py` | URL-to-ID extraction, error helpers | Direct function calls with various URL formats |
| `format.py` | All three output modes | Pass sample data dicts, assert string output |
| `annotate.py` | Comment injection into markdown | Pass markdown + comment list, assert annotated output |
| `state.py` | JSON read/write, atomic saves, missing file handling | Use `tmp_path` fixture, real filesystem |

### Layer 2: Mocked API Tests

| Module | What to Test | How |
|--------|-------------|-----|
| `api/drive.py` | File listing, export, upload, share | Mock `service.files()` chain with `MagicMock` |
| `api/docs.py` | batchUpdate request construction | Mock `service.documents()` chain |
| `api/comments.py` | Comment CRUD, reply creation | Mock `service.comments()` / `service.replies()` chain |
| `notify.py` | Change detection logic, banner formatting | Mock api calls + provide state fixtures |

**Mock pattern for google-api-python-client:**

```python
# conftest.py
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_drive_service():
    service = MagicMock()
    # Mock the chained call pattern: service.files().list().execute()
    service.files.return_value.list.return_value.execute.return_value = {
        'files': [
            {'id': '123', 'name': 'Test Doc', 'modifiedTime': '2025-01-15T00:00:00Z'}
        ]
    }
    service.files.return_value.get.return_value.execute.return_value = {
        'id': '123', 'name': 'Test Doc', 'version': '42',
        'modifiedTime': '2025-01-15T00:00:00Z'
    }
    service.files.return_value.export.return_value.execute.return_value = \
        b'# Test Doc\n\nContent here.'
    return service

@pytest.fixture
def mock_docs_service():
    service = MagicMock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        'replies': [{'replaceAllText': {'occurrencesChanged': 1}}]
    }
    return service

@pytest.fixture(autouse=True)
def patch_services(mock_drive_service, mock_docs_service):
    with patch('gdoc.api.get_drive_service', return_value=mock_drive_service), \
         patch('gdoc.api.get_docs_service', return_value=mock_docs_service):
        yield
```

**Key mock insight:** The google-api-python-client uses a chained builder pattern (`service.files().list(q=...).execute()`). Each `()` call returns a new object. `MagicMock` handles this naturally since attribute access and calls on a MagicMock return new MagicMocks. Set `.execute.return_value` at the end of the chain to control what the API "returns."

### Layer 3: CLI Integration Tests

```python
# test_cli.py
import subprocess

def test_cat_outputs_markdown(mock_services, tmp_path):
    """End-to-end: gdoc cat DOC_ID prints markdown to stdout."""
    result = subprocess.run(
        ['python', '-m', 'gdoc', 'cat', 'test-doc-id', '--quiet'],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert '# Test Doc' in result.stdout

def test_replace_reports_occurrences(mock_services):
    result = subprocess.run(
        ['python', '-m', 'gdoc', 'replace', 'test-doc-id', 'old', 'new', '--quiet'],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert 'replaced' in result.stdout
```

**Alternative (no subprocess):** Call `cli.main()` directly with patched `sys.argv`:

```python
from unittest.mock import patch
from gdoc.cli import main

def test_cat_via_main(mock_services):
    with patch('sys.argv', ['gdoc', 'cat', 'test-doc-id', '--quiet']):
        exit_code = main()
    assert exit_code == 0
```

### Layer 4: Auth Tests

```python
# test_auth.py
def test_get_credentials_from_cached_token(tmp_path):
    """Valid token.json → returns Credentials without browser flow."""
    token_file = tmp_path / 'token.json'
    token_file.write_text('{"token": "...", "refresh_token": "..."}')

    with patch('gdoc.auth.TOKEN_PATH', token_file):
        creds = get_credentials()
        assert creds is not None

def test_get_credentials_expired_refreshes(tmp_path):
    """Expired token → auto-refresh via refresh_token."""
    # ... mock expired credentials, verify refresh() is called

def test_auth_command_runs_flow(tmp_path):
    """gdoc auth → triggers InstalledAppFlow."""
    # ... mock InstalledAppFlow, verify run_local_server called
```

### What NOT to Test

- **Google API behavior itself.** Do not test that `files().list()` returns files. That is Google's responsibility. Test that YOUR code constructs the right request and handles the response correctly.
- **Exact banner formatting in integration tests.** Test banner content in unit tests for `notify.py`. Integration tests should just verify banners appear on stderr, not their exact format.

## Error Handling and Exit Codes

### Exit Code Convention

| Code | Meaning | When |
|------|---------|------|
| 0 | Success | Command completed successfully |
| 1 | API error | Google API returned an error (404, 403, 500, etc.) |
| 2 | Auth error | No credentials, expired with no refresh, revoked access |
| 3 | Usage error | Bad arguments, missing required args (argparse handles this) |
| 4 | Conflict error | `push` blocked due to doc modification since last read |

### Error Handling Pattern

```python
# util.py
from googleapiclient.errors import HttpError

class GdocError(Exception):
    """Base error with exit code."""
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code

class AuthError(GdocError):
    def __init__(self, message):
        super().__init__(message, exit_code=2)

class ConflictError(GdocError):
    def __init__(self, message):
        super().__init__(message, exit_code=4)

# cli.py
def main():
    args = parse_args()
    try:
        return args.handler(args)
    except AuthError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return e.exit_code
    except GdocError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return e.exit_code
    except HttpError as e:
        print(f"ERR: {_friendly_http_error(e)}", file=sys.stderr)
        return 1
```

### Google API Error Translation

```python
# util.py
def friendly_http_error(error):
    """Convert HttpError to user-friendly message."""
    status = error.resp.status
    messages = {
        401: "authentication required. Run `gdoc auth`",
        403: "permission denied. Check sharing settings",
        404: "file not found (404)",
        429: "rate limited. Wait and retry",
        500: "Google API internal error. Retry",
    }
    return messages.get(status, f"API error ({status})")
```

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Single user, <100 docs | Current architecture is perfect. No changes needed. |
| Single user, 1000+ docs | Add pagination to `ls` and `find`. Already supported by Drive API. |
| Team tool, many users | Each user has their own `~/.gdoc/` directory. No shared state needed. |
| High-frequency automation | Use `--quiet` to skip pre-flight. Consider batch API calls for bulk operations. |

### Scaling Priorities

1. **First bottleneck:** API quota limits (Drive API: 12,000 queries/day default). Not an architecture concern -- handle with rate limit detection and backoff in `util.py`.
2. **Second bottleneck:** Pre-flight check overhead (2 calls per command). Already mitigated by `--quiet` flag. Could add client-side caching with TTL (skip pre-flight if last check was <30 seconds ago).

## Sources

- google-api-python-client library conventions (method chaining, discovery-based service construction) -- well-established patterns from library documentation
- google-auth-oauthlib InstalledAppFlow -- standard OAuth2 desktop flow documented by Google
- argparse subcommand dispatch -- Python stdlib, documented at docs.python.org
- pytest + unittest.mock for Google API testing -- established community pattern
- Unix CLI conventions (stdout for data, stderr for diagnostics, exit codes) -- POSIX standard

**Confidence note:** External research tools were unavailable during this session. All patterns documented here are based on mature, stable libraries (google-api-python-client has been stable since v2.0) and well-established Python CLI conventions. Architecture recommendations are HIGH confidence for this domain. Version-specific details (exact current versions of dependencies) should be verified during implementation.

---
*Architecture research for: Python CLI wrapping Google Docs/Drive APIs*
*Researched: 2026-02-07*
