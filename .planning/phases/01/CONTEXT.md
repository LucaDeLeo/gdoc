# Phase 01 - Context (Auto-Generated)

**Generated:** 2026-02-07T18:16:42Z
**Method:** Claude ↔ Codex dialogue
**Status:** Ready for planning

## Milestone Anchor

**Milestone Goal:** Users can authenticate and the CLI framework handles input parsing, output formatting, and error reporting consistently across all future commands

**Requirements:** AUTH-01, AUTH-02, AUTH-03, OUT-01, OUT-02, OUT-03, OUT-04, OUT-05, OUT-06

## Implementation Decisions

### Project Scaffolding & Build System

| Decision | Detail | Source |
|----------|--------|--------|
| Build backend | `hatchling` with `pyproject.toml` | Resolved: hatchling. gdoc.md mandates minimal deps — hatchling is build-time only, PEP 517, lighter than setuptools, and the modern default for pure-Python packages. Easily swappable later if needed. |
| Python version | `>=3.10` | Resolved: `>=3.10`. Needed for `match` statements and modern typing (`X | Y`). google-auth supports 3.8+ but no reason to target below 3.10 for a new project. |
| Entry point | `[project.scripts] gdoc = "gdoc.cli:main"` | Both agreed |
| Distribution | `uv tool install` | Both agreed (per `.planning/PROJECT.md`) |
| Runtime dependencies | `google-api-python-client>=2.150.0`, `google-auth-oauthlib>=1.2.1`, `google-auth-httplib2>=0.2.0` | Both agreed (per gdoc.md) |
| Dev dependencies | `pytest>=8.0`, `pytest-mock>=3.14`, `ruff>=0.8.0` in `[project.optional-dependencies].dev` | Both agreed |

### CLI Framework

| Decision | Detail | Source |
|----------|--------|--------|
| Framework | stdlib `argparse` with `add_subparsers(dest="command", required=True)` | Both agreed |
| Global flags | `--json`, `--verbose`, `--quiet` on parent parser | Both agreed |
| Mutual exclusivity | `--json` and `--verbose` are mutually exclusive (argparse `add_mutually_exclusive_group`) | Codex suggested, incorporated |
| Custom exit codes | Subclass `ArgumentParser`, override `error()` to exit with code 3 instead of default 2 — avoids collision with auth error exit code 2 | Codex identified (High finding), Claude resolved |
| Handler pattern | One `cmd_*` function per subcommand, receives parsed args, returns exit code | Both agreed |

### Package Structure (Phase 1 only)

| File | Purpose | Source |
|------|---------|--------|
| `gdoc/__init__.py` | Version string (`__version__`) | Both agreed |
| `gdoc/__main__.py` | Entry point: `from gdoc.cli import main; sys.exit(main())` | Both agreed |
| `gdoc/cli.py` | Parser, `auth` handler, stub subcommands, top-level exception handler | Both agreed |
| `gdoc/auth.py` | OAuth2 flow + credential caching | Both agreed |
| `gdoc/util.py` | URL-to-ID extraction, `GdocError`/`AuthError` exceptions, exit codes | Both agreed |
| `gdoc/format.py` | Output mode dispatch (terse/json/verbose) with initial formatters | Both agreed |
| `gdoc/api/__init__.py` | Lazy `get_drive_service()`/`get_docs_service()` factories via `@lru_cache` | Both agreed |

**Not created in Phase 1:** `api/drive.py`, `api/docs.py`, `api/comments.py`, `state.py`, `notify.py`, `annotate.py` — these have no Phase 1 work and are created when needed in later phases. Codex suggested stubbing them to reduce churn; rejected because empty stubs add no value and later phases will create them with real content.

### OAuth2 Authentication

| Decision | Detail | Source |
|----------|--------|--------|
| Primary flow | `InstalledAppFlow.run_local_server(port=0)` | Both agreed |
| Headless support | Explicit `--no-browser` flag as primary toggle. Uses `run_local_server(open_browser=False)` + prints authorization URL for manual copy-paste. `run_console()` is **removed** (google-auth-oauthlib v1.0.0 dropped OOB flow, Feb 2023) — do not use or attempt fallback. | Codex suggested explicit flag; `run_console()` deprecation verified via google-auth-oauthlib changelog |
| Token storage | `~/.gdoc/token.json` with `0600` permissions | Both agreed |
| Scope tracking | Store scopes in token file; compare on load; trigger re-auth on mismatch | Both agreed |
| Scopes | `https://www.googleapis.com/auth/drive` + `https://www.googleapis.com/auth/documents` | Both agreed |
| credentials.json validation | `gdoc auth` checks that `~/.gdoc/credentials.json` exists before starting flow; clear error if missing | Codex suggested, incorporated |

### Token Refresh & Error Recovery

| Decision | Detail | Source |
|----------|--------|--------|
| Refresh flow | Load `token.json` → check `creds.valid` → if expired, `creds.refresh(Request())` → save updated token | Both agreed |
| `invalid_grant` recovery | Delete `token.json`, print "Authorization expired. Run `gdoc auth`.", exit code 2 | Both agreed |
| Corrupt token.json | If JSON parse fails, delete file, treat as first run, warn on stderr | Codex suggested, incorporated |
| Missing refresh_token | Treat as invalid credentials, trigger re-auth prompt | Codex suggested, incorporated |
| Scope mismatch | Compare stored scopes vs required scopes on load; trigger re-auth if different | Both agreed |

### URL-to-ID Resolution

| Decision | Detail | Source |
|----------|--------|--------|
| Regex extraction | Match `/d/([a-zA-Z0-9_-]+)` from `docs.google.com/document/d/{ID}` patterns | Both agreed |
| URL suffixes | Support `/edit`, `/edit#heading=...`, `/preview`, `/copy`, `/comment` | Both agreed |
| Query parameter URLs | Support `id=` query parameter format (common in Drive sharing links) | Codex suggested, incorporated |
| Bare IDs | Accept alphanumeric + `-_` strings directly | Both agreed |
| Validation | Verify extracted ID matches `[a-zA-Z0-9_-]+` before API calls | Both agreed |

### Exit Codes & Error Handling

| Code | Meaning | Source |
|------|---------|--------|
| 0 | Success | Both agreed (per gdoc.md) |
| 1 | API error | Both agreed (per gdoc.md) |
| 2 | Auth error | Both agreed (per gdoc.md) |
| 3 | Usage error | Both agreed (per gdoc.md) |
| 4 | Not yet implemented (Phase 1 stubs only) | Unassigned by spec; avoids collision with 1=API, 2=auth, 3=usage. Temporary — removed when stubs are replaced. **Enforcement:** a CI release gate (e.g., `scripts/check-no-stubs.sh`) must verify no stub exit-code-4 paths remain before any tagged release. This script is created as part of Phase 1 scaffolding. |

| Decision | Detail | Source |
|----------|--------|--------|
| Custom ArgumentParser | Override `error()` method to use `sys.exit(3)` instead of argparse default `sys.exit(2)` | Codex identified conflict, Claude resolved |
| Exception hierarchy | `GdocError(exit_code=1)` base, `AuthError(exit_code=2)` subclass | Both agreed |
| Top-level handler | `main()` wraps handler in try/except: catch `AuthError` → exit 2, `GdocError` → exit 1, `HttpError` → friendly message + exit 1 | Codex identified gap, Claude resolved |
| Error message format | `ERR: <message>` prefix on all errors to stderr | Codex identified gap, Claude resolved (per gdoc.md examples) |
| Errors in --json mode | Errors always go to stderr as plain text `ERR: ...`. `--json` only affects stdout data output. | Codex identified gap, Claude resolved |
| Stub subcommand exit code | "Not yet implemented" stubs use exit code 4 (not 1 or 3). Exit 3 misleads as usage error; exit 1 collides with spec's "API error" definition. Exit 4 is unassigned and unambiguous. | Codex identified issue, resolved with spec-safe code |

### Output Formatting

| Decision | Detail | Source |
|----------|--------|--------|
| Architecture | `format.py` with pure functions accepting data dicts, returning strings | Both agreed |
| Modes | `terse` (default), `json` (`json.dumps`), `verbose` (human-friendly) | Both agreed |
| Default | Terse — minimal text for token efficiency | Both agreed |
| Mutual exclusivity | `--json` and `--verbose` enforced via argparse `add_mutually_exclusive_group` | Codex suggested, incorporated |
| Stderr/stdout | All data to stdout, all banners/warnings/errors to stderr | Both agreed |
| JSON stdout purity | When `--json` is active, stdout contains only valid JSON — no mixing | Codex suggested, incorporated |

### Stub Subcommands

| Decision | Detail | Source |
|----------|--------|--------|
| Scope | Register all future subcommands (`cat`, `ls`, `find`, `edit`, `write`, `comments`, `comment`, `reply`, `resolve`, `reopen`, `info`, `share`, `new`, `cp`) with their arguments in argparse | Both agreed |
| Behavior | Print "Not yet implemented" to stderr, exit code 4 | Exit code 4 avoids spec collision (1=API, 3=usage) |
| Rationale | Complete help text visible from Phase 1; argument parsing validated early | Both agreed |
| Release guard | CI script (`scripts/check-no-stubs.sh`) greps for exit-code-4 stub pattern and fails the build if any remain. Prevents stubs from shipping in a tagged release. Created in Phase 1 scaffolding. | Required by dev-only override policy |

### Config Directory

| Decision | Detail | Source |
|----------|--------|--------|
| Location | `~/.gdoc/` | Both agreed (per gdoc.md) |
| Subdirectory | `state/` for per-doc state (created in later phases) | Both agreed |
| Creation | `os.makedirs(exist_ok=True)` on first use | Both agreed |
| Files | `credentials.json` (user-provided), `token.json` (generated) | Both agreed |

### Testing

| Decision | Detail | Source |
|----------|--------|--------|
| Framework | `pytest` with `pytest-mock` | Both agreed |
| Linting | `ruff` | Both agreed |
| Directory | `tests/` mirroring source structure | Both agreed |
| Phase 1 scope | Layer 1 (pure unit tests) for `util.py` and `format.py`; Layer 2 (mocked) for `auth.py` and `cli.py` | Both agreed |
| Location | Dev dependencies only — `[project.optional-dependencies].dev` | Both agreed |

## Source-of-Truth Hierarchy

Codex review (Round 2) noted that decisions reference `STACK.md`, `PROJECT.md`, `ARCHITECTURE.md`, and `PITFALLS.md` and stated "only `gdoc.md` exists in this repo." This was factually incorrect — those files exist as research outputs under `.planning/research/` and `.planning/PROJECT.md`. The authoritative source hierarchy is:

1. **`gdoc.md`** (repo root) — product spec; overrides everything on conflict
2. **`.planning/research/*.md`** — research outputs (STACK, ARCHITECTURE, PITFALLS, FEATURES, SUMMARY)
3. **`.planning/PROJECT.md`** — project definition derived from gdoc.md

Where research files informed a decision not directly covered by `gdoc.md` (e.g., build backend, Python version), the Source column notes the origin. `gdoc.md` always wins on conflicts.

**Dev-only override policy:** Development scaffolding may introduce behaviors not described in `gdoc.md` (e.g., exit code 4 for stubs) provided: (1) the behavior does not contradict any spec-defined behavior — only extends into undefined territory, (2) it is documented in this CONTEXT.md with explicit rationale, and (3) a concrete enforcement mechanism (CI gate, test assertion) prevents the dev-only behavior from shipping. This policy does not permit deviating from spec-defined behaviors.

## Resolved (Previously Flagged)

> **1. Build backend: hatchling** *(resolved)*
> - Codex noted gdoc.md doesn't specify a build backend or Python version.
> - `.planning/research/STACK.md` recommends `hatchling` with Python `>=3.10`.
> - **Decision: hatchling.** Pure-Python package with no special build needs. Hatchling is PEP 517, build-time only, lighter than setuptools, and the modern default (used by pip, ruff, black). Easily swappable later — low-impact choice.
> - Python `>=3.10` for `match` statements and `X | Y` typing syntax.

> **2. Headless OAuth: `run_local_server(open_browser=False)`** *(resolved)*
> - **`run_console()` is removed.** Google deprecated the OOB flow in Feb 2022 and google-auth-oauthlib v1.0.0 (Feb 2023) removed `run_console()` entirely. Our minimum version (`>=1.2.1`) does not have it.
> - **Decision: `run_local_server(open_browser=False)` + printed authorization URL.** When `--no-browser` is passed (or headless detected), the local server starts without opening a browser and prints the authorization URL for manual copy-paste. The user visits the URL, authorizes, and the local redirect captures the code.
> - Device authorization grant flow is **not implemented** — it requires additional Google Cloud Console configuration and is unnecessary when `run_local_server` works headlessly.
> - Source: google-auth-oauthlib [v1.0.0 changelog](https://github.com/googleapis/google-auth-library-python-oauthlib/releases) — "Remove deprecated OOB code (#264)"

## Claude's Discretion

- Exact argparse help text wording for each subcommand
- Internal organization of `cli.py` (order of subparser definitions, helper grouping)
- Specific regex pattern for URL-to-ID extraction (implementation detail, well-tested)
- Whether `format.py` uses classes or plain functions (recommend plain functions per `.planning/research/ARCHITECTURE.md`)
- Token JSON serialization details (use `Credentials.to_json()` + scope field)
- Test fixture naming and organization within `conftest.py`
- Whether to include `py.typed` marker in Phase 1 or defer mypy setup
- Exact `fields` parameters on Google API calls
- How to structure the `friendly_http_error()` status-to-message mapping

---
*Auto-generated via milestone sprint*
