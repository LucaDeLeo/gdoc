# Phase 01: Foundation & Auth - Research

**Researched:** 2026-02-07
**Domain:** Python CLI scaffolding, OAuth2 authentication, argparse customization, output formatting
**Confidence:** HIGH

## Summary

Phase 01 covers project scaffolding (pyproject.toml, package structure, entry points), the OAuth2 authentication flow, CLI infrastructure (argparse with custom exit codes, global flags, mutually exclusive groups), output formatting (terse/json/verbose), and utility functions (URL-to-ID extraction, error handling). All core technologies are mature and well-documented. The key technical risks are: (1) argparse's default exit code 2 colliding with the spec's auth error code 2, requiring a custom ArgumentParser subclass; (2) headless OAuth requiring `run_local_server(open_browser=False)` since `run_console()` was removed in google-auth-oauthlib v1.0.0; and (3) token.json corruption requiring defensive loading with fallback to re-auth.

All locked decisions from CONTEXT.md have been verified against current library versions and official documentation. The google-auth-oauthlib `open_browser` parameter is confirmed to exist and work as expected. The argparse subclass pattern for exit code override is straightforward. The `id=` query parameter URL format (`drive.google.com/open?id=...`) is a real Google Drive URL pattern that must be supported alongside the standard `/d/ID` path pattern.

**Primary recommendation:** Use the custom ArgumentParser subclass for exit code control (not `exit_on_error=False` which is buggy across Python versions). Implement defensive token.json loading that catches `json.JSONDecodeError`, `ValueError`, and `KeyError`, falling back to re-auth on any failure.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

1. **Custom ArgumentParser for exit code 3** -- argparse defaults to exit 2 on usage errors, which collides with auth error code 2. Subclass `ArgumentParser.error()` to emit exit code 3 instead.
2. **Stub subcommands use exit code 4** -- Dev-only artifact not defined in `gdoc.md`. Exit code 4 avoids collision with all spec-defined codes. Enforcement via CI release gate (`scripts/check-no-stubs.sh` or equivalent).
3. **`--json`/`--verbose` mutual exclusivity** -- enforced via argparse `add_mutually_exclusive_group`.
4. **Error format: `ERR: <message>` prefix on stderr** -- for all errors; errors always plain text on stderr even in `--json` mode; top-level exception handler in `main()`.
5. **Runtime dependencies locked** -- `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2` with minimum versions.
6. **`gdoc auth` validates `credentials.json` exists** before starting flow.
7. **Guard for corrupt `token.json` and missing `refresh_token`**.
8. **Support `id=` query parameter URLs**.
9. **`--no-browser` as explicit headless toggle**.
10. **Build backend: hatchling.** Python `>=3.10`.
11. **Headless OAuth: `run_local_server(open_browser=False)`** with documented limitations.

### Claude's Discretion

No explicit discretion areas were defined in CONTEXT.md. All decisions are locked or resolved.

### Deferred Ideas (OUT OF SCOPE)

- Stubbing all architecture files (api/drive.py, state.py, notify.py, etc.) in Phase 1 -- rejected, no value in empty files.
</user_constraints>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | >=3.10 | Runtime | `match` statements, modern typing (`X \| Y`), project targets modern Python |
| google-api-python-client | >=2.150.0 | Drive API v3 + Docs API v1 | Only official Google API client for Python. Latest: 2.189.0 (verified PyPI 2026-02-07) |
| google-auth-oauthlib | >=1.2.1 | OAuth2 installed-app flow | Bridges google-auth with oauthlib for browser-based consent. Latest: 1.2.4 (verified PyPI 2026-01-15) |
| google-auth-httplib2 | >=0.2.0 | HTTP transport bridge | Bridges google-auth credentials with httplib2 transport. Latest: 0.3.0 (verified PyPI 2025-12-15) |
| argparse | stdlib | CLI argument parsing | Zero dependencies, fits minimal-dependency philosophy |
| hatchling | >=1.21 | Build backend (build-time only) | PEP 517, lighter than setuptools. Latest: 1.28.0 (requires Python >=3.10) |

### Supporting (Dev Only)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | >=8.0 | Test runner | All testing |
| pytest-mock | >=3.14 | Mock wrapper | Cleaner than raw `@patch` decorators |
| ruff | >=0.8.0 | Linter + formatter | Replaces flake8, isort, black |

### Alternatives Considered

None -- all stack choices are locked by CONTEXT.md and gdoc.md spec.

**Installation:**
```bash
# Runtime (pyproject.toml [project].dependencies)
google-api-python-client>=2.150.0
google-auth-oauthlib>=1.2.1
google-auth-httplib2>=0.2.0

# Dev (pyproject.toml [project.optional-dependencies].dev)
pytest>=8.0
pytest-mock>=3.14
ruff>=0.8.0
```

## Architecture Patterns

### Recommended Project Structure (Phase 01 Scope)

```
gdoc/
├── __init__.py              # Package marker, __version__ string
├── __main__.py              # Entry: from gdoc.cli import main; sys.exit(main())
├── cli.py                   # GdocArgumentParser subclass + argparse dispatch + global flags
├── auth.py                  # OAuth2 flow + credential storage + token refresh
├── format.py                # Output formatters (terse/json/verbose) -- Phase 1 scaffolding
└── util.py                  # URL-to-ID extraction, error classes, constants
tests/
├── conftest.py              # Shared fixtures
├── test_cli.py              # Argument parsing, command routing, exit codes
├── test_auth.py             # Auth flow tests (mocked)
├── test_format.py           # Output formatter tests
└── test_util.py             # URL-to-ID extraction tests
scripts/
└── check-no-stubs.sh        # CI gate: verify no exit-code-4 stub paths remain
pyproject.toml               # Project metadata, dependencies, console_script, tool config
```

### Pattern 1: Custom ArgumentParser Subclass for Exit Code 3

**What:** Subclass `argparse.ArgumentParser` and override `error()` to emit exit code 3 instead of the default 2.
**When to use:** Always -- this is the project's global parser.
**Why not `exit_on_error=False`:** This parameter is buggy across Python versions (cpython issue #103498). In Python 3.10-3.12, `ArgumentParser.error()` still calls `sys.exit(2)` even when `exit_on_error=False` for certain error types (e.g., missing required arguments). The subclass approach is reliable across all Python versions.

**Verified pattern:**
```python
# cli.py
import argparse
import sys

class GdocArgumentParser(argparse.ArgumentParser):
    """Custom parser that exits with code 3 on usage errors (not 2)."""

    def error(self, message: str) -> None:
        """Override to use exit code 3 instead of argparse default 2.

        argparse.ArgumentParser.error() prints usage + error to stderr
        then calls sys.exit(2). We need exit code 2 reserved for auth errors
        per gdoc.md spec: 0=success, 1=API error, 2=auth error, 3=usage error.
        """
        self.print_usage(sys.stderr)
        print(f"ERR: {message}", file=sys.stderr)
        sys.exit(3)
```
**Source:** [Python docs: argparse.ArgumentParser.error()](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser.error) -- confirmed `error()` method signature and default behavior (exits with status 2).

**Confidence:** HIGH -- the subclass pattern is explicitly documented in Python's argparse docs and is the standard approach for customizing error behavior.

### Pattern 2: Mutually Exclusive Output Flags on Parent Parser

**What:** Use `add_mutually_exclusive_group()` at the top-level parser for `--json` and `--verbose`. These are global flags inherited by all subcommands.
**When to use:** Top-level parser definition.

**Verified pattern:**
```python
# cli.py
def build_parser() -> GdocArgumentParser:
    parser = GdocArgumentParser(
        prog="gdoc",
        description="CLI for Google Docs & Drive",
    )

    # Global output mode flags (mutually exclusive)
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="JSON output")
    output_group.add_argument("--verbose", action="store_true", help="Detailed output")

    sub = parser.add_subparsers(dest="command")

    # auth subcommand
    auth_p = sub.add_parser("auth", help="Authenticate with Google")
    auth_p.add_argument("--no-browser", action="store_true",
                        help="Don't open browser, print URL for manual auth")

    # ... stub subcommands for other phases
    return parser
```
**Source:** [Python docs: add_mutually_exclusive_group()](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser.add_mutually_exclusive_group)

**Confidence:** HIGH -- standard argparse feature, well-tested and stable.

**Important note:** When adding `--json`/`--verbose` as global flags on the parent parser, they will appear in the namespace for all subcommands. Adding them to the parent parser (not each subparser) avoids duplication and ensures consistent behavior. If a subparser needs to override help text, use `parents=[]` pattern instead.

### Pattern 3: Top-Level Exception Handler with Exit Codes

**What:** `main()` wraps all execution in a try/except that maps exception types to exit codes and formats errors consistently.
**When to use:** Always -- this is the top-level entry point.

**Verified pattern:**
```python
# cli.py
import sys
from gdoc.util import GdocError, AuthError

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help(sys.stderr)
        return 3

    try:
        return args.func(args)
    except AuthError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 2
    except GdocError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return e.exit_code
    except Exception as e:
        print(f"ERR: unexpected error: {e}", file=sys.stderr)
        return 1
```

**Confidence:** HIGH -- standard Python CLI pattern.

### Pattern 4: Stub Subcommands with Exit Code 4

**What:** Register all future subcommands as stubs that print an error message and exit with code 4.
**When to use:** Phase 1 only -- stubs are replaced with real implementations in later phases.

```python
# cli.py
def cmd_stub(args) -> int:
    """Placeholder for unimplemented commands."""
    print(f"ERR: {args.command} is not yet implemented", file=sys.stderr)
    return 4
```

**CI enforcement:**
```bash
#!/usr/bin/env bash
# scripts/check-no-stubs.sh
# Fails if any exit-code-4 stub paths remain in the codebase.
if grep -rn 'return 4' gdoc/ --include='*.py'; then
    echo "FAIL: stub exit code 4 found -- all stubs must be replaced before release"
    exit 1
fi
echo "OK: no stubs found"
```

**Confidence:** HIGH -- simple convention, no library dependency.

### Anti-Patterns to Avoid

- **Using `exit_on_error=False` for custom exit codes:** Buggy across Python 3.10-3.12. Use the subclass approach instead. ([cpython#103498](https://github.com/python/cpython/issues/103498))
- **Adding `--json`/`--verbose` to each subparser individually:** Duplicates code and risks inconsistency. Add to the parent parser's mutually exclusive group.
- **Calling `sys.exit()` from within command handlers:** Return exit codes from handlers, let `main()` call `sys.exit()`. This makes handlers testable.
- **Building Google API service objects at import time:** This would break `gdoc --help` and `gdoc auth` when credentials are missing. Use lazy factories.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OAuth2 flow | Custom HTTP redirect handler | `InstalledAppFlow.run_local_server()` | Handles PKCE, CSRF, redirect, token exchange |
| Token serialization | Custom JSON format for tokens | `Credentials.to_json()` / `from_authorized_user_file()` | Standard format, handles all fields correctly |
| Token refresh | Manual HTTP token refresh | `creds.refresh(Request())` | Handles error cases, token rotation |
| Mutually exclusive args | Custom validation in handler | `parser.add_mutually_exclusive_group()` | Built into argparse, generates correct help text |
| Exit code on usage errors | Post-parse validation | `GdocArgumentParser.error()` override | Catches all argparse error paths |
| URL-to-ID extraction | Simple string splitting | Regex with multiple patterns | Must handle `/d/ID`, `open?id=ID`, `uc?id=ID`, bare IDs |

**Key insight:** The google-auth ecosystem handles all OAuth2 complexity. The CLI layer should be thin wrappers around these primitives, not reimplementations.

## Common Pitfalls

### Pitfall 1: argparse Default Exit Code 2 Collides with Auth Error Code

**What goes wrong:** argparse calls `sys.exit(2)` on usage errors (missing args, invalid args, unknown flags). The gdoc.md spec defines exit code 2 as "auth error." Without override, a usage error looks like an auth error to automation.
**Why it happens:** argparse hardcodes `self.exit(2, ...)` inside `ArgumentParser.error()`. There is no configuration option to change this (the `exit_on_error` parameter is buggy -- see above).
**How to avoid:** Subclass `ArgumentParser` and override `error()` to call `sys.exit(3)`. This is the only reliable approach.
**Warning signs:** Tests that check `returncode == 2` for auth errors also pass for typos in command names.

### Pitfall 2: `run_console()` Does Not Exist Anymore

**What goes wrong:** Developer tries to implement headless OAuth using `InstalledAppFlow.run_console()`, which was the documented approach before 2023.
**Why it happens:** `run_console()` relied on Google's OOB (Out-of-Band) flow using the redirect URI `urn:ietf:wg:oauth:2.0:oob`. Google deprecated OOB in 2022, and `run_console()` was removed in google-auth-oauthlib v1.0.0 (February 2023, [PR #264](https://github.com/googleapis/google-auth-library-python-oauthlib/pull/264)). Many tutorials and Stack Overflow answers still reference it.
**How to avoid:** Use `run_local_server(open_browser=False)`. This starts the local HTTP server for the redirect but does not attempt to open a browser. The auth URL is printed to the terminal for the user to copy-paste. Map the `--no-browser` flag to `open_browser=False`.
**Warning signs:** `AttributeError: 'InstalledAppFlow' has no attribute 'run_console'` at runtime.

### Pitfall 3: Corrupt or Incomplete token.json Breaks All Commands

**What goes wrong:** `Credentials.from_authorized_user_file()` raises `ValueError` ("authorized user info was not in the expected format, missing fields...") or `json.JSONDecodeError` on corrupt files. If uncaught, every command fails with an unhelpful traceback.
**Why it happens:** token.json can be corrupted by: (1) process crash during write (partial JSON), (2) manual editing, (3) disk space exhaustion, (4) concurrent writes from multiple CLI instances. The `from_authorized_user_file` method requires three fields: `refresh_token`, `client_id`, `client_secret` -- any missing field triggers ValueError.
**How to avoid:** Wrap token loading in a try/except that catches `json.JSONDecodeError`, `ValueError`, and `KeyError`. On any failure: delete the corrupt file, print a clear message ("ERR: stored credentials are corrupt. Run `gdoc auth` to re-authenticate."), and exit with code 2.
**Warning signs:** Truncated JSON in `~/.gdoc/token.json`. Missing `refresh_token` field after a failed auth flow.

### Pitfall 4: Headless OAuth Requires Localhost Redirect

**What goes wrong:** User runs `gdoc auth --no-browser` on a remote server without port forwarding. The local HTTP server starts on `localhost:PORT`, but the OAuth redirect cannot reach it from the user's browser on a different machine.
**Why it happens:** `run_local_server()` always starts a local HTTP server listening on `localhost`. The OAuth redirect goes to `http://localhost:PORT/...`. If the browser is on a different machine than the CLI, the redirect has nowhere to go.
**How to avoid:** Document the limitation clearly. Supported headless scenarios: (1) local desktop with no default browser configured, (2) WSL, (3) containers with host networking, (4) SSH with port forwarding (`ssh -L 8080:localhost:8080`). For truly remote environments without port forwarding: run `gdoc auth` locally, then copy `~/.gdoc/token.json` to the remote host.
**Warning signs:** OAuth flow hangs after user authorizes in browser. "Connection refused" or timeout on redirect.

### Pitfall 5: Token File Permissions Expose Credentials

**What goes wrong:** `token.json` is created with default permissions (typically 0644), meaning any user on the system can read the OAuth refresh token and impersonate the user.
**Why it happens:** Python's `open()` and `Path.write_text()` use the process umask for permissions. On most systems this is 0022, resulting in world-readable files.
**How to avoid:** After writing `token.json`, explicitly set permissions to 0600 (`owner read/write only`). Same for `credentials.json`. Use `os.chmod()` or set umask before writing.
**Warning signs:** `ls -la ~/.gdoc/token.json` shows `-rw-r--r--` instead of `-rw-------`.

### Pitfall 6: Scope Mismatch on Token Reuse

**What goes wrong:** Developer adds a new scope in a later phase, but existing `token.json` files from Phase 1 lack that scope. API calls requiring the new scope fail with 403.
**Why it happens:** `from_authorized_user_file()` loads whatever scopes are in the token file. It does not compare against the scopes the application now requires.
**How to avoid:** Store the requested scopes in the token file (or alongside it). On load, compare stored scopes against `SCOPES` constant. If stored scopes are a subset of required scopes, trigger re-auth. Alternatively, always pass the required scopes to `from_authorized_user_file()` and check `creds.scopes` after loading.
**Warning signs:** 403 errors after adding a new API feature. "Insufficient permissions" on operations that worked before.

## Code Examples

### Example 1: Complete OAuth2 Auth Flow (auth.py)

```python
# Source: google-auth-oauthlib official docs + google-auth docs
# Verified against: google-auth-oauthlib 1.2.4, google-auth 2.47.0

import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

CONFIG_DIR = Path.home() / ".gdoc"
TOKEN_PATH = CONFIG_DIR / "token.json"
CREDS_PATH = CONFIG_DIR / "credentials.json"


def get_credentials() -> Credentials:
    """Load or refresh credentials. Returns valid Credentials or raises AuthError."""
    creds = _load_token()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception:
            # refresh_token revoked or invalid_grant
            # Fall through to re-auth
            pass

    raise AuthError("Not authenticated. Run `gdoc auth` to authenticate.")


def authenticate(no_browser: bool = False) -> Credentials:
    """Run the full OAuth2 flow. Called by `gdoc auth`."""
    if not CREDS_PATH.exists():
        raise AuthError(
            f"credentials.json not found at {CREDS_PATH}. "
            "Download it from Google Cloud Console and place it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDS_PATH), SCOPES
    )

    if no_browser:
        # Headless: start server but don't open browser
        # User must copy-paste the URL manually
        creds = flow.run_local_server(
            port=0,
            open_browser=False,
            authorization_prompt_message=(
                "Visit this URL to authorize gdoc:\n\n{url}\n"
            ),
        )
    else:
        creds = flow.run_local_server(port=0)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _save_token(creds)
    return creds


def _load_token() -> Credentials | None:
    """Load token.json with defensive error handling."""
    if not TOKEN_PATH.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(
            str(TOKEN_PATH), SCOPES
        )
        return creds
    except (json.JSONDecodeError, ValueError, KeyError):
        # Corrupt or incomplete token file -- delete and return None
        print(
            "ERR: stored credentials are corrupt. "
            "Run `gdoc auth` to re-authenticate.",
            file=sys.stderr,
        )
        TOKEN_PATH.unlink(missing_ok=True)
        return None


def _save_token(creds: Credentials) -> None:
    """Save credentials to token.json with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)
```

**Key details verified:**
- `from_authorized_user_file()` requires `refresh_token`, `client_id`, `client_secret` fields (verified from [google-auth source](https://github.com/googleapis/google-auth-library-python/blob/main/google/oauth2/credentials.py))
- `to_json()` outputs: token, refresh_token, token_uri, client_id, client_secret, scopes, expiry (verified from source)
- `run_local_server(open_browser=False)` is confirmed to exist with default `open_browser=True` (verified from [google-auth-oauthlib source](https://github.com/googleapis/google-auth-library-python-oauthlib/blob/main/google_auth_oauthlib/flow.py))
- `run_local_server` also accepts: `host`, `bind_addr`, `port`, `authorization_prompt_message`, `success_message`, `redirect_uri_trailing_slash`, `timeout_seconds`, `token_audience`, `browser`
- `run_console()` does NOT exist in the codebase (verified from source -- removed in v1.0.0)

**Confidence:** HIGH -- verified against current source code.

### Example 2: URL-to-ID Extraction (util.py)

```python
# Supports all Google Drive/Docs URL formats plus bare IDs

import re

# Google Docs/Drive URL patterns (order matters -- most specific first):
# 1. /d/ID path pattern (most common):
#    https://docs.google.com/document/d/ID/edit
#    https://docs.google.com/spreadsheets/d/ID/edit
#    https://drive.google.com/file/d/ID/view
# 2. id= query parameter pattern:
#    https://drive.google.com/open?id=ID
#    https://drive.google.com/uc?export=download&id=ID
# 3. Bare document ID (no URL)

_PATTERNS = [
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),         # /d/ID path
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),     # id=ID query param
]

# Valid bare doc ID: alphanumeric, hyphens, underscores, typically 20-44 chars
_BARE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def extract_doc_id(input_str: str) -> str:
    """Extract document ID from a URL or bare ID string.

    Accepts:
    - Full Google Docs URL: https://docs.google.com/document/d/ID/edit
    - Full Drive URL with query: https://drive.google.com/open?id=ID
    - Bare document ID: 1aBcDeFgHiJkLmNoPqRsTuVwXyZ

    Raises ValueError if no valid ID can be extracted.
    """
    input_str = input_str.strip()

    # Try URL patterns first
    for pattern in _PATTERNS:
        match = pattern.search(input_str)
        if match:
            return match.group(1)

    # Fall back to bare ID
    if _BARE_ID.match(input_str):
        return input_str

    raise ValueError(f"Cannot extract document ID from: {input_str}")
```

**URL formats verified:**
- `/d/ID` path: `docs.google.com/document/d/ID/edit`, `/preview`, `/copy`, `/export?format=pdf` ([Source](https://youneedawiki.com/blog/posts/google-doc-url-parameters.html))
- `open?id=ID`: `drive.google.com/open?id=ID` ([Source](https://www.labnol.org/internet/direct-links-for-google-drive/28356))
- `uc?id=ID`: `drive.google.com/uc?export=download&id=ID` (download links)
- `file/d/ID`: `drive.google.com/file/d/ID/view` (sharing links)
- URL suffixes after ID: `/edit`, `/edit#heading=...`, `/preview`, `/copy`, `/comment`, `/view`, `/view?usp=sharing`

**Confidence:** HIGH -- patterns verified against documented URL formats.

### Example 3: pyproject.toml Configuration

```toml
# Verified against: hatchling 1.28.0 docs, uv tool install docs, PEP 517/621

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
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"

[tool.ruff]
target-version = "py310"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]
```

**Key requirements for `uv tool install` compatibility:**
1. `[build-system]` with `build-backend` is required -- uv uses PEP 517 to build wheels
2. `[project.scripts]` defines the console entry point -- uv discovers executables from console_scripts
3. `[project]` must have `name` and `version` at minimum
4. The entry point format is `"package.module:function"` (not `"package.module"`)

**Source:** [uv tools docs](https://docs.astral.sh/uv/guides/tools/), [hatch pyproject.toml docs](https://hatch.pypa.io/latest/config/metadata/)

**Hatchling version source:** By default, hatchling reads `version` from `[project]` table. To read from `__init__.py` instead, add:
```toml
[tool.hatch.version]
path = "gdoc/__init__.py"
```
and set `dynamic = ["version"]` in `[project]` while removing the static `version` field.

**Confidence:** HIGH -- verified against current hatchling and uv docs.

### Example 4: `__main__.py` Entry Point

```python
# gdoc/__main__.py
# Enables: python -m gdoc
"""Entry point for `python -m gdoc`."""
import sys
from gdoc.cli import main

sys.exit(main())
```

```python
# gdoc/__init__.py
"""gdoc -- Token-efficient CLI for Google Docs & Drive."""
__version__ = "0.1.0"
```

**Confidence:** HIGH -- standard Python packaging pattern.

### Example 5: Output Format Scaffolding (format.py)

```python
# format.py -- pure functions, no imports from other gdoc modules
import json
from typing import Any


def get_output_mode(args) -> str:
    """Determine output mode from parsed args."""
    if getattr(args, "json", False):
        return "json"
    if getattr(args, "verbose", False):
        return "verbose"
    return "terse"


def format_success(message: str, mode: str = "terse") -> str:
    """Format a success message."""
    if mode == "json":
        return json.dumps({"ok": True, "message": message})
    return message


def format_error(message: str) -> str:
    """Format an error message. Always plain text, always stderr."""
    return f"ERR: {message}"
```

**Confidence:** HIGH -- simple pure functions, no external dependency.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `InstalledAppFlow.run_console()` (OOB flow) | `run_local_server(open_browser=False)` | v1.0.0 (Feb 2023) | Must use local server for headless; cannot use console-based copy-paste redirect URI |
| `oauth2client` library | `google-auth` + `google-auth-oauthlib` | 2019 (deprecated) | Many old tutorials reference oauth2client; must use google-auth stack |
| `setup.py` + `setuptools` | `pyproject.toml` + `hatchling` | ~2022 (PEP 517/621 mainstream) | Single config file, no setup.py needed |
| `argparse exit_on_error=False` | Custom `ArgumentParser.error()` subclass | Still buggy as of 3.12 | Cannot rely on `exit_on_error` for custom exit codes |

**Deprecated/outdated:**
- `run_console()`: Removed in google-auth-oauthlib v1.0.0. Do not use. ([Release notes](https://github.com/googleapis/google-auth-library-python-oauthlib/releases))
- `oauth2client`: Deprecated since 2019. Still appears in many tutorials. Use `google-auth-oauthlib` instead.
- `setup.py` / `setup.cfg`: Legacy build system. `pyproject.toml` is the modern standard.

## Open Questions

1. **`authorization_prompt_message` format string in `run_local_server`**
   - What we know: The parameter accepts a string. The default includes `{url}` placeholder.
   - What's unclear: Whether `{url}` is expanded via `str.format()` or is a literal placeholder. The source code shows it uses `.format(url=...)` formatting.
   - Recommendation: Use `"{url}"` placeholder in the message string. Verify during implementation by testing the actual output.
   - **Confidence:** MEDIUM -- needs implementation-time verification.

2. **Exact `check-no-stubs.sh` implementation**
   - What we know: Must detect `return 4` in Python files under `gdoc/`.
   - What's unclear: Whether `return 4` might appear in non-stub contexts (e.g., legitimate exit code 4 usage).
   - Recommendation: Use a more specific pattern like `grep -rn 'return 4.*# stub'` or a comment marker `# STUB` alongside the return statement, making the grep more precise.
   - **Confidence:** MEDIUM -- implementation detail, low risk.

3. **Token scope comparison strategy**
   - What we know: `Credentials.from_authorized_user_file(path, scopes)` accepts scopes but does not enforce them. The loaded credentials may have different scopes than requested.
   - What's unclear: Whether `creds.scopes` is reliably populated after loading from file. In some cases it may be `None`.
   - Recommendation: Store required scopes in a separate key in the token file (or a companion file). Compare on load. If scopes differ, trigger re-auth. Implement this defensively -- treat `creds.scopes is None` as "unknown, re-auth to be safe."
   - **Confidence:** MEDIUM -- needs implementation-time testing.

## Sources

### Primary (HIGH confidence)
- [google-auth-oauthlib source code (flow.py)](https://github.com/googleapis/google-auth-library-python-oauthlib/blob/main/google_auth_oauthlib/flow.py) -- `run_local_server` signature, `open_browser` parameter, absence of `run_console`
- [google-auth-oauthlib releases](https://github.com/googleapis/google-auth-library-python-oauthlib/releases) -- v1.0.0 removed OOB code, latest is 1.2.4
- [google-auth credentials.py source](https://github.com/googleapis/google-auth-library-python/blob/main/google/oauth2/credentials.py) -- `from_authorized_user_file` required fields, `to_json` output fields
- [Python argparse docs](https://docs.python.org/3/library/argparse.html) -- `error()` default exit code 2, `add_mutually_exclusive_group()`, `exit_on_error` parameter
- [Hatch/hatchling docs](https://hatch.pypa.io/latest/) -- pyproject.toml configuration, console scripts entry points
- [uv tools docs](https://docs.astral.sh/uv/guides/tools/) -- `uv tool install` requirements
- [PyPI: google-api-python-client](https://pypi.org/project/google-api-python-client/) -- latest version 2.189.0
- [PyPI: google-auth-oauthlib](https://pypi.org/project/google-auth-oauthlib/) -- latest version 1.2.4
- [PyPI: google-auth-httplib2](https://pypi.org/project/google-auth-httplib2/) -- latest version 0.3.0
- [PyPI: hatchling](https://pypi.org/project/hatchling/) -- latest version 1.28.0, requires Python >=3.10

### Secondary (MEDIUM confidence)
- [Google Drive URL formats](https://www.labnol.org/internet/direct-links-for-google-drive/28356) -- `open?id=`, `uc?id=`, `/d/ID`, `/file/d/ID` patterns verified
- [Google Docs URL parameters](https://youneedawiki.com/blog/posts/google-doc-url-parameters.html) -- `/d/ID` pattern with various suffixes
- [cpython#103498](https://github.com/python/cpython/issues/103498) -- `exit_on_error=False` bug documentation

### Tertiary (LOW confidence)
- None -- all findings verified with primary or secondary sources.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all versions verified against PyPI, all APIs verified against source code
- Architecture: HIGH -- patterns from mature, stable libraries with well-documented APIs
- Pitfalls: HIGH -- argparse exit code collision verified against Python docs, `run_console` removal verified against release notes and source code, token corruption handling verified against google-auth source

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (30 days -- all libraries are stable and mature)
