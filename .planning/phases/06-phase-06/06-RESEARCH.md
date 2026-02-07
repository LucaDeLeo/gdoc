# Phase 6: File Management - Research

**Researched:** 2026-02-07
**Domain:** Google Drive API v3 file creation, duplication, and permissions
**Confidence:** HIGH

## Summary

Phase 6 implements three file management commands: `new` (create blank doc), `cp` (duplicate doc), and `share` (manage permissions). All three operations map directly to well-documented Google Drive API v3 endpoints: `files.create`, `files.copy`, and `permissions.create`. The existing codebase already has stubs registered in `build_parser()` for all three commands (pointing to `cmd_stub`), so the CLI argument definitions are already in place.

The existing auth scope (`https://www.googleapis.com/auth/drive`) already covers all three API operations -- no scope changes are needed. The codebase has a well-established pattern for API wrappers in `gdoc/api/drive.py` (service call + `_translate_http_error`) and CLI handlers in `gdoc/cli.py` (lazy imports, pre-flight checks, output formatting, state updates). Phase 6 follows these patterns exactly.

The CONTEXT.md establishes critical decisions: state seeding for newly created docs (to avoid wasteful "first interaction" banners), pre-flight for `cp`/`share` but not `new`, `webViewLink` in API response fields for verbose/JSON output, and deferred `supportsAllDrives`.

**Primary recommendation:** Add three API wrapper functions to `gdoc/api/drive.py` and three CLI handlers to `gdoc/cli.py`, following the exact patterns from existing commands. Seed state for newly created documents using the version from the Drive API response.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
1. Use Drive v3 `files.create`, `files.copy`, `permissions.create` API methods
2. Add wrappers in `gdoc/api/drive.py` with `_translate_http_error` to match existing patterns
3. Pre-flight for `cp`/`share`, NOT for `new` (aligns with "every command targeting a DOC_ID" pattern)
4. Seed state on newly created docs (`new` and the copy from `cp`) using `last_version` from the Drive API response; include `version` in `fields` parameter; cast version string to int
5. Default `share` role: `reader` (least-privilege, already in stub)
6. Default notification behavior for `share`; defer `--no-notify`
7. `new` output: ID-only default; JSON uses `format_json`; URL from `webViewLink` in verbose/JSON
8. `cp` output: same as `new` -- map Drive `name` to output `title`
9. `share` output: "OK shared..." aligns with existing style; JSON uses `format_json`
10. `--folder` via `_resolve_doc_id` + `parents`; `supportsAllDrives` deferred
11. Single plan (scope is small enough)
12. Remove `cmd_stub` after all callers replaced; `scripts/check-no-stubs.sh` enforces
13. `cp` title is required (per `gdoc.md` command reference)
14. Let generic 403 handling cover "not owned" share attempts
15. `share` requires pre-flight (established pattern: every command targeting a DOC_ID)
16. `webViewLink` in `fields` for `files.create`/`files.copy` to avoid extra API call
17. Error messaging for `new --folder`: pass folder ID or sentinel to `_translate_http_error`

### Claude's Discretion
- None specified; all decisions are locked

### Deferred Ideas (OUT OF SCOPE)
- `supportsAllDrives` -- deferred to a future phase that adds it uniformly to ALL Drive API calls
- `--no-notify` flag for `share` -- deferred
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-api-python-client | >=2.150.0 | Drive API v3 service | Already in use, provides `files().create()`, `files().copy()`, `permissions().create()` |
| google-auth-oauthlib | >=1.2.1 | OAuth2 flow | Already in use for auth |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httplib2 | (transitive) | HTTP error response mocking | Tests only -- `_make_http_error()` helper |
| pytest | >=8.0 | Test framework | All tests |
| pytest-mock | >=3.14 | Mock utilities | Not actually used; tests use `unittest.mock.patch` directly |

No new dependencies required. Everything is already in `pyproject.toml`.

## Architecture Patterns

### Existing Project Structure (unchanged)
```
gdoc/
  cli.py            # build_parser() + cmd_* handlers
  util.py           # extract_doc_id, GdocError, AuthError
  format.py         # get_output_mode, format_json
  state.py          # DocState, load_state, save_state, update_state_after_command
  notify.py         # pre_flight, ChangeInfo
  auth.py           # OAuth2 flow
  api/
    __init__.py     # get_drive_service (@lru_cache)
    drive.py        # Drive API wrappers (export_doc, list_files, etc.)
    docs.py         # Docs API wrappers (replace_all_text)
    comments.py     # Comments API wrappers
tests/
  test_api_drive.py # API wrapper unit tests
  test_cli.py       # Subprocess integration tests
  test_edit.py      # cmd_edit handler tests
  test_write.py     # cmd_write handler tests
  test_comments_cmd.py # Comment command tests
```

### Pattern 1: API Wrapper in drive.py
**What:** Each Drive API operation gets a thin wrapper function that calls the service, specifies fields, translates errors.
**When to use:** Every new Drive API operation.
**Example (from existing codebase):**
```python
# Source: gdoc/api/drive.py - existing pattern
def get_file_info(doc_id: str) -> dict:
    try:
        service = get_drive_service()
        result = (
            service.files()
            .get(
                fileId=doc_id,
                fields="id, name, mimeType, modifiedTime, ..., version",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)
```

### Pattern 2: CLI Handler with Pre-flight
**What:** Handler resolves doc ID, runs pre-flight, calls API, formats output, updates state.
**When to use:** Commands that target an existing document (`cp`, `share`).
**Example (from existing codebase):**
```python
# Source: gdoc/cli.py - existing pattern (cmd_comment simplified)
def cmd_comment(args) -> int:
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.comments import create_comment
    result = create_comment(doc_id, args.text)
    new_id = result["id"]

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=new_id, status="created"))
    else:
        print(f"OK comment #{new_id}")

    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="comment", quiet=quiet, ...)

    return 0
```

### Pattern 3: CLI Handler WITHOUT Pre-flight (for `new`)
**What:** `new` creates a brand-new document; no existing doc to check against.
**When to use:** Commands that create new resources (not targeting existing docs).
**Key difference:** No `_resolve_doc_id`, no `pre_flight`, but DOES seed state after creation.

### Pattern 4: State Seeding for New Documents
**What:** After `new` or `cp` creates a document, seed state using `update_state_after_command` with the `command_version` from the API response. This prevents wasteful "first interaction" banner + extra API call on next command.
**When to use:** Immediately after `files.create` or `files.copy` returns.
**CRITICAL:** The API `fields` parameter MUST include `version`, and the string must be cast to `int`.

### Anti-Patterns to Avoid
- **Calling `_translate_http_error` with blank file_id for `new --folder`:** Would produce "Document not found: " with no context. Pass the folder ID or descriptive string instead.
- **Forgetting `version` in API response fields:** Would require a separate `get_file_version` call to seed state, defeating the purpose.
- **Adding `supportsAllDrives` only to new commands:** Creates inconsistency. Deferred to a dedicated future phase.
- **Using `title` in `files.copy` body:** Drive API v3 uses `name`, not `title`. The v2 API used `title`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Error translation | Custom exception handling per endpoint | `_translate_http_error(e, file_id)` | Existing pattern handles 401/403/404/5xx consistently |
| Output formatting | Manual JSON construction | `format_json(**data)` | Ensures `ok: True` wrapper consistently |
| Doc ID extraction | URL parsing logic | `_resolve_doc_id(args.doc)` | Already handles URLs, bare IDs, invalid input with proper exit code |
| State management | Custom state file I/O | `update_state_after_command(...)` | Handles all state mutation patterns (version, timestamps, comments) |
| Service caching | Manual singleton | `get_drive_service()` via `@lru_cache` | Already handles credential loading + service build + caching |

**Key insight:** The codebase has strong conventions. Phase 6 should not introduce ANY new patterns -- it should be indistinguishable from existing commands in structure.

## Common Pitfalls

### Pitfall 1: Drive API v3 uses `name`, not `title`
**What goes wrong:** Passing `title` in the request body (a v2 field) results in the field being silently ignored. Document gets created with name "Untitled".
**Why it happens:** Many tutorials and Stack Overflow answers reference the v2 API.
**How to avoid:** Always use `name` in the body dict: `{"name": "My Doc", "mimeType": "application/vnd.google-apps.document"}`.
**Warning signs:** Created documents showing as "Untitled" despite passing a title.

### Pitfall 2: Forgetting `mimeType` on `files.create`
**What goes wrong:** Without `mimeType`, Drive creates a generic binary file, not a Google Doc.
**Why it happens:** `files.create` doesn't require `mimeType` parameter.
**How to avoid:** Always set `mimeType` to `application/vnd.google-apps.document` for blank Google Docs.
**Warning signs:** Created file has wrong icon in Drive, cannot be edited as a document.

### Pitfall 3: `version` field is returned as string from Drive API
**What goes wrong:** If you store the version without casting, comparison with integer state values fails silently.
**Why it happens:** Drive API returns version as a string even though it represents a number.
**How to avoid:** Always `int(result["version"])` -- existing pattern in `get_file_info()` and `update_doc_content()`.
**Warning signs:** State comparisons always showing as "changed" because `"42" != 42`.

### Pitfall 4: `permissions.create` role `commenter` vs `reader`
**What goes wrong:** Confusion about role naming -- `commenter` IS a valid Drive API v3 role.
**Why it happens:** Some docs only list `reader`/`writer`/`owner`; `commenter` was added later.
**How to avoid:** The CLI already maps `--role reader|writer|commenter` correctly. Drive API v3 accepts `commenter` directly.
**Warning signs:** None expected -- `commenter` is fully supported.

### Pitfall 5: Error context for `new --folder` with invalid folder ID
**What goes wrong:** `_translate_http_error(e, "")` produces "Document not found: " with no useful context.
**Why it happens:** `new` command doesn't have a traditional `doc_id` to pass.
**How to avoid:** Pass the folder ID to `_translate_http_error` when the error relates to the folder, or handle the 404 specifically: "Folder not found: {folder_id}".
**Warning signs:** User sees blank or confusing error message.

### Pitfall 6: `parents` field must be a list
**What goes wrong:** Passing a string instead of a list for `parents` causes a 400 error.
**Why it happens:** API expects `"parents": ["folder_id"]`, not `"parents": "folder_id"`.
**How to avoid:** Always wrap in a list: `body["parents"] = [folder_id]`.
**Warning signs:** 400 Bad Request from API.

### Pitfall 7: Removing `cmd_stub` too early
**What goes wrong:** If `cmd_stub` is removed before all three commands are implemented, `scripts/check-no-stubs.sh` won't detect the problem but other callers may break.
**Why it happens:** Partial implementation.
**How to avoid:** Implement all three commands, then remove `cmd_stub` and its `set_defaults` usages as the final step. Run `check-no-stubs.sh` to confirm.
**Warning signs:** CI gate `check-no-stubs.sh` failing.

## Code Examples

Verified patterns from official sources and existing codebase:

### Create Blank Google Doc (files.create)
```python
# Source: Context7 /googleapis/google-api-python-client + official Drive API docs
# + existing codebase pattern (gdoc/api/drive.py)
def create_doc(title: str, folder_id: str | None = None) -> dict:
    """Create a blank Google Doc.

    Args:
        title: Document title.
        folder_id: Optional folder ID to place the doc in.

    Returns:
        Dict with id, name, version (int), webViewLink.
    """
    try:
        service = get_drive_service()
        body: dict = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        }
        if folder_id:
            body["parents"] = [folder_id]

        result = (
            service.files()
            .create(
                body=body,
                fields="id, name, version, webViewLink",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, folder_id or "")
```

### Duplicate Doc (files.copy)
```python
# Source: Context7 /googleapis/google-api-python-client + official Drive API docs
# + existing codebase pattern
def copy_doc(doc_id: str, title: str) -> dict:
    """Create a copy of a document.

    Args:
        doc_id: Source document ID.
        title: Title for the copy.

    Returns:
        Dict with id, name, version (int), webViewLink.
    """
    try:
        service = get_drive_service()
        result = (
            service.files()
            .copy(
                fileId=doc_id,
                body={"name": title},
                fields="id, name, version, webViewLink",
            )
            .execute()
        )
        if "version" in result:
            result["version"] = int(result["version"])
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)
```

### Share Doc (permissions.create)
```python
# Source: Context7 /googleapis/google-api-python-client + official Drive API docs
def create_permission(doc_id: str, email: str, role: str) -> dict:
    """Share a document with an email address.

    Args:
        doc_id: Document ID.
        email: Email address to share with.
        role: Permission role (reader, writer, commenter).

    Returns:
        Dict with id, role, type, emailAddress.
    """
    try:
        service = get_drive_service()
        result = (
            service.permissions()
            .create(
                fileId=doc_id,
                body={
                    "type": "user",
                    "role": role,
                    "emailAddress": email,
                },
                fields="id, role, type, emailAddress",
                sendNotificationEmail=True,
            )
            .execute()
        )
        return result
    except HttpError as e:
        _translate_http_error(e, doc_id)
```

### CLI Handler for `new` (no pre-flight, with state seeding)
```python
# Pattern derived from existing codebase
def cmd_new(args) -> int:
    title = args.title
    folder_id = None
    if getattr(args, "folder", None):
        folder_id = _resolve_doc_id(args.folder)

    from gdoc.api.drive import create_doc
    result = create_doc(title, folder_id=folder_id)
    new_id = result["id"]
    version = result.get("version")
    url = result.get("webViewLink", "")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=new_id, title=result["name"], url=url))
    elif mode == "verbose":
        print(f"{new_id}\t{result['name']}\t{url}")
    else:
        print(new_id)

    # Seed state for the new doc
    from gdoc.state import update_state_after_command
    update_state_after_command(
        new_id, None, command="new",
        quiet=True, command_version=version,
    )

    return 0
```

### CLI Handler for `cp` (pre-flight on source, state seeding on copy)
```python
# Pattern derived from existing codebase
def cmd_cp(args) -> int:
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import copy_doc
    result = copy_doc(doc_id, args.title)
    new_id = result["id"]
    version = result.get("version")
    url = result.get("webViewLink", "")

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(id=new_id, title=result["name"], url=url))
    elif mode == "verbose":
        print(f"{new_id}\t{result['name']}\t{url}")
    else:
        print(new_id)

    # Update state for SOURCE doc (standard pre-flight pattern)
    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="cp", quiet=quiet)

    # Seed state for the NEW COPY
    update_state_after_command(
        new_id, None, command="cp",
        quiet=True, command_version=version,
    )

    return 0
```

### CLI Handler for `share` (pre-flight, no state seeding)
```python
# Pattern derived from existing codebase
def cmd_share(args) -> int:
    doc_id = _resolve_doc_id(args.doc)
    quiet = getattr(args, "quiet", False)
    role = args.role

    from gdoc.notify import pre_flight
    change_info = pre_flight(doc_id, quiet=quiet)

    from gdoc.api.drive import create_permission
    result = create_permission(doc_id, args.email, role)

    from gdoc.format import get_output_mode, format_json
    mode = get_output_mode(args)
    if mode == "json":
        print(format_json(email=args.email, role=role, status="shared"))
    else:
        print(f"OK shared with {args.email} as {role}")

    from gdoc.state import update_state_after_command
    update_state_after_command(doc_id, change_info, command="share", quiet=quiet)

    return 0
```

### Test Pattern (from existing test files)
```python
# Source: tests/test_comments_cmd.py -- established test pattern
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

def _make_args(command, **overrides):
    defaults = {
        "command": command,
        "json": False,
        "verbose": False,
        "quiet": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)

class TestCmdNew:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_drive_service")
    @patch("gdoc.api.drive.create_doc", return_value={
        "id": "new123", "name": "My Doc", "version": 1, "webViewLink": "https://..."
    })
    def test_new_default_output(self, mock_create, _svc, _update, capsys):
        args = _make_args("new", title="My Doc")
        rc = cmd_new(args)
        assert rc == 0
        assert "new123" in capsys.readouterr().out
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Drive API v2 `title` field | Drive API v3 `name` field | 2015 (v3 launch) | Must use `name` in all request bodies |
| Drive API v2 `copy()` with `title` | Drive API v3 `copy()` with `name` in body | 2015 (v3 launch) | `body={"name": title}` not `body={"title": title}` |
| Permission roles without `commenter` | `commenter` as a first-class role | ~2017 | Can be used directly in `role` field |

**Deprecated/outdated:**
- `enforceSingleParent` parameter: deprecated in Drive API v3; do not use
- `supportsTeamDrives` parameter: deprecated; use `supportsAllDrives` (which we are also deferring)

## OAuth Scopes

The existing auth configuration in `gdoc/auth.py` already includes:
```python
SCOPES = [
    "https://www.googleapis.com/auth/drive",        # Full Drive access
    "https://www.googleapis.com/auth/documents",     # Docs API access
]
```

The `drive` scope covers `files.create`, `files.copy`, and `permissions.create`. **No scope changes needed.**

**Note:** Users who authenticated before this phase do NOT need to re-authenticate, since the Drive scope was already granted.

## Stub Removal Strategy

The existing stubs in `build_parser()` (lines 752-779 of `cli.py`) already define the argument structure:
- `new`: `title` (positional), `--folder` (optional)
- `cp`: `doc` (positional), `title` (positional), `--quiet`
- `share`: `doc` (positional), `email` (positional), `--role` (choices: reader/writer/commenter, default: reader), `--quiet`

All three currently use `set_defaults(func=cmd_stub)`. Implementation involves:
1. Creating `cmd_new`, `cmd_cp`, `cmd_share` handler functions
2. Changing `set_defaults(func=cmd_stub)` to `set_defaults(func=cmd_new)`, etc.
3. Removing the `cmd_stub` function itself (and its entry in `test_cli.py` `TestExitCode4OnStubs`)
4. Running `scripts/check-no-stubs.sh` to confirm no stub markers remain

## Open Questions

1. **`new --folder` error message detail**
   - What we know: `_translate_http_error` uses the `file_id` parameter for error messages. When `new --folder` fails because the folder doesn't exist, passing `folder_id` would produce "Document not found: {folder_id}" which is misleading.
   - What's unclear: Whether to add a folder-specific branch to `_translate_http_error` or handle it in the `create_doc` wrapper.
   - Recommendation: Handle in the `create_doc` wrapper by catching 404 specifically and raising `GdocError("Folder not found: {folder_id}")`. This keeps `_translate_http_error` unchanged.

2. **`update_state_after_command` for `new` command**
   - What we know: The `is_read` check in `update_state_after_command` uses `command in ("cat", "info")`. The `new` command won't be in this list, so `last_read_version` won't be set.
   - What's unclear: Whether the `new` command should also set `last_read_version` (since the user knows the full content -- it's empty).
   - Recommendation: Do NOT set `last_read_version` for `new`. The doc is empty at creation, but the user hasn't "read" it. Standard flow: user creates doc -> edits in browser -> runs `gdoc cat` to read -> then uses `gdoc write`. Setting `last_read_version` would give a false sense of safety.

## Sources

### Primary (HIGH confidence)
- Context7 `/googleapis/google-api-python-client` -- files.create, files.copy, permissions.create API signatures and Python examples
- Google Drive API v3 official reference: https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create -- file creation spec
- Google Drive API v3 official reference: https://developers.google.com/workspace/drive/api/reference/rest/v3/files/copy -- file copy spec
- Google Drive API v3 official reference: https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/create -- permission creation spec
- Google Drive API v3 Permissions resource: https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions -- confirmed `commenter` as valid role
- Existing codebase: `gdoc/api/drive.py`, `gdoc/cli.py`, `gdoc/state.py`, `gdoc/notify.py` -- established patterns

### Secondary (MEDIUM confidence)
- CONTEXT.md decisions from auto-discuss phase -- locked implementation decisions

### Tertiary (LOW confidence)
- None -- all claims verified against official sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, existing patterns
- Architecture: HIGH -- follows existing codebase conventions exactly
- API operations: HIGH -- verified against official Google Drive API v3 docs and Context7
- Pitfalls: HIGH -- based on known Drive API v3 gotchas and codebase patterns
- State seeding: MEDIUM -- the pattern is clear, but edge cases around `last_read_version` need care

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (stable -- Google Drive API v3 is mature and rarely changes)
