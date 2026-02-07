# Stack Research

**Domain:** Python CLI wrapping Google Docs/Drive APIs
**Researched:** 2026-02-07
**Confidence:** MEDIUM (versions from training data, not live PyPI -- external verification tools unavailable during research; versions are best-known as of early 2025 and should be verified with `pip index versions <pkg>` before implementation)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| Python | >=3.10 | Runtime | 3.10+ for `match` statements, modern typing (`X | Y`), and `tomllib` (3.11+). Google's client libraries support 3.8+ but there is no reason to target below 3.10 for a new project in 2025/2026. | HIGH |
| google-api-python-client | >=2.150.0 | Drive API v3 + Docs API v1 | The only official Google API client for Python. Provides `discovery`-based service objects. Auto-generates method stubs from API discovery docs. No alternative exists for full Drive/Docs coverage. | HIGH |
| google-auth-oauthlib | >=1.2.1 | OAuth2 installed-app flow | Bridges `google-auth` with `oauthlib` for the browser-based OAuth2 consent flow needed by CLI tools. `InstalledAppFlow.from_client_secrets_file()` is the standard pattern. | HIGH |
| google-auth-httplib2 | >=0.2.0 | HTTP transport | Bridges `google-auth` credentials with `httplib2` transport used by `google-api-python-client`. Required transitive dependency. | HIGH |
| argparse | stdlib | CLI argument parsing | See detailed rationale below in "CLI Framework Decision" section. Zero dependencies, fits project's minimal-dependency philosophy. | HIGH |

### Supporting Libraries (Dev Only)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | >=8.0 | Test runner | All testing. De facto standard for Python. Fixtures, parametrize, clean assertion introspection. |
| pytest-mock | >=3.14 | Mock wrapper | Thin wrapper around `unittest.mock` with fixture-based API. Cleaner than raw `@patch` decorators. |
| ruff | >=0.8.0 | Linter + formatter | Replaces flake8, isort, black in a single Rust-powered tool. 10-100x faster. The standard for new Python projects. |
| mypy | >=1.13 | Type checking | Static type analysis. Google API stubs available via `google-api-python-client-stubs`. |
| google-api-python-client-stubs | >=1.27 | Type stubs | Provides typed method signatures for Drive and Docs services. Enables IDE autocompletion and mypy checking on API calls. |

### Build & Distribution

| Tool | Purpose | Notes |
|------|---------|-------|
| hatchling | Build backend | Modern, fast, PEP 517 compliant. Used by pip, black, ruff, and most modern Python projects. Simpler config than setuptools. |
| uv | Package installer / tool runner | `uv tool install` is the target distribution method per project spec. uv is the fastest Python package manager (Rust-powered). |
| pyproject.toml | Project config | Single file for metadata, dependencies, build config, tool config (ruff, mypy, pytest). No setup.py, setup.cfg, or MANIFEST.in needed. |

## CLI Framework Decision

This is the most consequential stack choice. Three options evaluated:

### argparse (RECOMMENDED)

**Why this wins for gdoc:**

1. **Zero dependencies** -- The project spec says "No other dependencies. Intentionally minimal." argparse is in the stdlib and adds zero weight.

2. **Flat command structure** -- gdoc has ~19 subcommands, all top-level. No nested command groups. argparse handles flat subcommands cleanly via `add_subparsers()`.

3. **Agent-first design** -- AI agents do not benefit from rich help formatting, color output, or shell completion. They call `gdoc replace DOC_ID "old" "new"` directly. argparse's plain help output is actually more token-efficient.

4. **Predictable behavior** -- argparse has been stable for 15+ years. No surprises, no breaking changes, no dependency conflicts.

5. **Full control over output** -- The project needs custom output formatting (token-efficient tables, `--json`, `--verbose`). argparse stays out of the way. Click/Typer add their own output conventions that would need overriding.

**Implementation pattern:**

```python
# cli.py
import argparse

def build_parser():
    parser = argparse.ArgumentParser(prog="gdoc", description="CLI for Google Docs & Drive")
    sub = parser.add_subparsers(dest="command", required=True)

    # Each command gets its own subparser
    cat_p = sub.add_parser("cat", help="Export doc as markdown")
    cat_p.add_argument("doc_id")
    cat_p.add_argument("--comments", action="store_true")
    cat_p.add_argument("--plain", action="store_true")
    cat_p.add_argument("--quiet", action="store_true")
    cat_p.add_argument("--json", action="store_true")

    # ... repeat for each command
    return parser
```

### click (NOT recommended)

| Pro | Con |
|-----|-----|
| Better help formatting | Adds dependency (~80KB) |
| Built-in parameter types | Decorator-based API adds indirection |
| Context object passing | Output helpers conflict with custom formatters |
| Shell completion | Overkill for flat subcommands |

**When you'd choose click instead:** If gdoc had nested command groups (`gdoc drive ls`, `gdoc docs edit`, `gdoc comments list`) or needed rich interactive prompts. It does not.

### typer (NOT recommended)

| Pro | Con |
|-----|-----|
| Type-hint-based, modern DX | Adds typer + click as dependencies |
| Auto-generates CLI from function signatures | Rich output formatting conflicts with token-efficient design |
| Shell completion out of the box | Colored/formatted output is anti-agent |
| | Converts function signatures to CLI args -- less control over arg naming |

**When you'd choose typer instead:** If the CLI were human-first and you wanted rapid prototyping with pretty output. gdoc is agent-first with custom output formatting.

## OAuth2 Implementation Pattern

The Google OAuth2 stack for CLI tools is well-established. There are no alternatives to consider -- the google-auth ecosystem is the only supported path.

### Key Components

```
google-auth          -- core credentials, transport (auto-installed as dependency)
google-auth-oauthlib -- InstalledAppFlow for browser-based consent
google-auth-httplib2 -- transport adapter for google-api-python-client
```

### Standard Flow (Installed App)

```python
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
]

def get_credentials(config_dir: Path) -> Credentials:
    creds = None
    token_path = config_dir / "token.json"
    creds_path = config_dir / "credentials.json"

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return creds
```

### Best Practices for CLI OAuth2

1. **Use `run_local_server(port=0)`** -- Picks a random available port. Avoids port conflicts. The flow opens the user's browser, redirects to `localhost:<port>`, and captures the auth code automatically.

2. **Store tokens in `~/.gdoc/token.json`** -- Standard location. `Credentials.to_json()` serializes refresh token. On next run, `from_authorized_user_file()` loads it and auto-refreshes.

3. **Scope minimization** -- `drive` scope gives full file access (needed for export, upload, comments, sharing). `documents` scope gives Docs API access (needed for batchUpdate). These two are the minimum viable scopes.

4. **Headless/SSH environments** -- `InstalledAppFlow.run_console()` is the fallback for environments without a browser. Consider detecting `$DISPLAY` / `$BROWSER` and falling back automatically. This is important for AI agent use cases where the tool may run on a remote server.

5. **Token file permissions** -- Set `0600` on `token.json` to prevent other users from reading the refresh token. The `credentials.json` (client secret) should also be `0600`.

## Markdown Handling

**No markdown library needed.** This is a key finding.

### Export (cat)

Google Drive API v3 natively exports Google Docs as markdown:

```python
service.files().export(fileId=doc_id, mimeType='text/markdown').execute()
```

This has been available since mid-2024. The output is clean markdown that preserves headings, lists, bold/italic, links, and tables. No post-processing library required.

### Import (push)

Google Drive API v3 natively converts markdown to Google Doc format on upload:

```python
from googleapiclient.http import MediaFileUpload

media = MediaFileUpload('local.md', mimetype='text/markdown')
service.files().update(fileId=doc_id, media_body=media).execute()
```

Drive handles the markdown-to-doc conversion server-side. No client-side markdown parsing needed.

### Comment Annotation (cat --comments)

The `annotate.py` module injects comments as HTML comments into markdown output. This is pure string manipulation:

1. Get markdown export from Drive API
2. Get comments from comments.list()
3. For each comment with `quotedFileContent.value`, find that substring in the markdown
4. Inject `<!-- [#N status] author on "quoted": "comment text" -->` after the paragraph

This does NOT require a markdown AST parser. Simple `str.find()` / `str.index()` with paragraph boundary detection (splitting on `\n\n`) is sufficient and more predictable than AST manipulation.

**What NOT to use:** Do not add `markdown`, `mistune`, `markdown-it-py`, or `commonmark` as dependencies. They add complexity without benefit for this use case. The Drive API handles conversion; annotation is string manipulation.

## Testing Strategy

### Test Pyramid

```
Unit tests (80%)     -- Mock Google API service objects, test business logic
Integration tests    -- Test against real API (optional, manual, not CI)
No E2E tests needed  -- CLI is thin; unit + integration covers everything
```

### Mocking Google API Calls

The `google-api-python-client` library provides `googleapiclient.http.HttpMockSequence` for testing, but the simpler approach is mocking at the service object level:

```python
# test_drive.py
from unittest.mock import MagicMock

def test_list_files():
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        'files': [
            {'id': '123', 'name': 'Test Doc', 'modifiedTime': '2025-01-01T00:00:00Z'}
        ]
    }

    result = list_files(mock_service, folder_id=None)
    assert len(result) == 1
    assert result[0]['name'] == 'Test Doc'
```

### CLI Output Testing

Since gdoc uses stdout for all output, test CLI commands by capturing output:

```python
import io
from contextlib import redirect_stdout

def test_cat_command(mock_service):
    f = io.StringIO()
    with redirect_stdout(f):
        run_command(['cat', 'doc123'], service=mock_service)
    output = f.getvalue()
    assert '# Test Document' in output
```

Or with `subprocess.run()` for true end-to-end CLI testing:

```python
import subprocess

def test_cat_cli():
    result = subprocess.run(['gdoc', 'cat', 'doc123'], capture_output=True, text=True)
    assert result.returncode == 0
```

### Recommended pytest Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-v --tb=short"
markers = [
    "integration: marks tests requiring real Google API credentials",
]
```

### Test Directory Structure

```
tests/
├── conftest.py          # Shared fixtures (mock services, credentials)
├── test_auth.py         # OAuth2 flow tests
├── test_cli.py          # Argument parsing, command routing
├── test_drive.py        # Drive API operations (ls, find, cat, push)
├── test_docs.py         # Docs API operations (replace, insert, delete)
├── test_comments.py     # Comment CRUD operations
├── test_state.py        # State tracking (awareness system)
├── test_notify.py       # Change detection + banner formatting
├── test_annotate.py     # Comment injection into markdown
├── test_format.py       # Output formatters (table, json, plain)
└── test_util.py         # URL-to-ID extraction, error handling
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| argparse | click 8.x | If command structure grows to nested groups (e.g., `gdoc drive ls`, `gdoc docs replace`) |
| argparse | typer 0.x | If building a human-first CLI with rich output and interactive prompts |
| hatchling | setuptools | If you need complex build customization (C extensions, custom commands) |
| hatchling | flit | Simpler than hatchling but less flexible. Fine for pure-Python, but hatchling is equally simple and more capable. |
| ruff | black + flake8 + isort | Never. ruff replaces all three, is faster, and is the clear standard for new projects. |
| pytest | unittest | Never for new projects. pytest is strictly superior. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `markdown` / `mistune` / `markdown-it-py` | Drive API handles markdown conversion natively. Adding a markdown parser adds complexity, dependency weight, and edge-case bugs for zero benefit. | Drive API `files.export(mimeType='text/markdown')` and `files.update()` with markdown media |
| `click` / `typer` | Adds dependencies to a project that explicitly targets minimal deps. Overkill for flat subcommands. Rich output formatting conflicts with agent-first token-efficient design. | `argparse` (stdlib) |
| `black` + `flake8` + `isort` | Three separate tools, three configs, slower. Superseded by ruff in 2024. | `ruff` (single tool, Rust-powered) |
| `requests` / `httpx` | `google-api-python-client` manages its own HTTP transport via `httplib2`. Adding another HTTP library creates confusion about which transport layer to use. | `googleapiclient.http` and `google.auth.transport.requests.Request` (for token refresh only) |
| `oauth2client` | Deprecated since 2019. Replaced by `google-auth` + `google-auth-oauthlib`. Still appears in many tutorials/StackOverflow answers. | `google-auth-oauthlib` |
| `gspread` / `pydrive2` | Higher-level wrappers around Google APIs. Add abstraction layers that hide the API surface gdoc needs to expose directly. Also add unnecessary dependencies. | Direct `google-api-python-client` usage |
| `setuptools` + `setup.py` | Legacy build system. `pyproject.toml` + `hatchling` is the modern standard. | `hatchling` with `pyproject.toml` |
| `tox` | Test environment manager. Overkill for a single-Python-version project. uv handles virtualenvs. | `pytest` directly, or `uv run pytest` |

## Stack Patterns

**If targeting Python 3.11+:**
- Use `tomllib` (stdlib) for reading config files if needed
- Use `ExceptionGroup` for batch API error aggregation
- Because: reduces external dependencies further

**If headless/SSH support is critical:**
- Implement `InstalledAppFlow.run_console()` fallback
- Detect with: `os.environ.get('DISPLAY')` on Linux, or check if `webbrowser.open()` would work
- Because: AI agents often run in headless environments

**If you add shell completion later:**
- Use `argcomplete` (lightweight, argparse-native) rather than switching to click/typer
- Because: adds one small dependency vs rewriting the entire CLI layer

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| google-api-python-client >=2.x | google-auth >=2.x | Both are maintained by Google and versioned together |
| google-auth-oauthlib >=1.2 | google-auth >=2.14 | oauthlib pins to google-auth >=2.14 |
| google-auth-httplib2 >=0.2 | google-auth >=2.x, httplib2 >=0.19 | Thin bridge, rarely causes issues |
| pytest >=8.0 | Python >=3.8 | No conflict with google packages |
| ruff >=0.8 | Standalone binary | No Python version constraint (Rust binary) |
| hatchling >=1.21 | Python >=3.8 | Build-time only, not a runtime dependency |

**Key compatibility note:** All three `google-*` packages share `google-auth` as a common dependency. pip/uv resolves this automatically. Pin loosely (`>=`) not tightly (`==`) to avoid resolution conflicts.

## Installation

```bash
# Runtime dependencies (add to pyproject.toml [project].dependencies)
google-api-python-client>=2.150.0
google-auth-oauthlib>=1.2.1
google-auth-httplib2>=0.2.0

# Dev dependencies (add to pyproject.toml [project.optional-dependencies].dev)
pytest>=8.0
pytest-mock>=3.14
ruff>=0.8.0
mypy>=1.13
google-api-python-client-stubs>=1.27.0
```

```toml
# pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "gdoc"
version = "0.1.0"
description = "Token-efficient CLI for Google Docs & Drive"
requires-python = ">=3.10"
dependencies = [
    "google-api-python-client>=2.150.0",
    "google-auth-oauthlib>=1.2.1",
    "google-auth-httplib2>=0.2.0",
]

[project.scripts]
gdoc = "gdoc.cli:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.14",
    "ruff>=0.8.0",
    "mypy>=1.13",
    "google-api-python-client-stubs>=1.27.0",
]
```

## Sources

- Training data (knowledge cutoff ~May 2025) -- MEDIUM confidence for version numbers
- Google API Python Client official docs (googleapis.github.io/google-api-python-client/) -- HIGH confidence for API patterns
- Google Auth Library docs (google-auth.readthedocs.io) -- HIGH confidence for OAuth2 flow
- Google Drive API v3 documentation (developers.google.com/drive/api/v3) -- HIGH confidence for export/import MIME types
- Google Docs API v1 documentation (developers.google.com/docs/api) -- HIGH confidence for batchUpdate patterns

**Note:** External verification tools (Context7, Exa, WebSearch, PyPI API) were unavailable during this research session. All version numbers are from training data and should be verified with `pip index versions <package>` or `uv pip compile` before pinning in pyproject.toml. The _architectural recommendations_ (which library to use, which to avoid) are HIGH confidence regardless of exact version numbers.

---
*Stack research for: Python CLI wrapping Google Docs/Drive APIs*
*Researched: 2026-02-07*
