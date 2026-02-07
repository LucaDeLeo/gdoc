# Phase 5: Comments & Annotations - Research

**Researched:** 2026-02-07
**Domain:** Google Drive API v3 Comments/Replies CRUD + line-numbered annotation rendering
**Confidence:** HIGH

## Summary

Phase 5 implements comment CRUD operations (list, create, reply, resolve, reopen) and the `cat --comments` annotated view. All comment operations use the Google Drive API v3 `comments` and `replies` resources. The existing codebase already has `list_comments` with pagination and error translation, `DocState` with comment tracking fields, and pre-flight comment change detection -- Phase 5 builds on this foundation.

The most complex piece is `annotate.py`, a new pure-logic module that renders line-numbered markdown with inline comment annotations. It uses `quotedFileContent.value` from the comments API to anchor comments to specific lines via substring search, with well-defined fallback rules for ambiguity, deletion, and short anchors (all specified in CONTEXT.md).

A critical finding: the Google Drive API `comments.list` does NOT have an `includeResolved` query parameter. The existing code passes `"includeResolved": True` but this is silently ignored by the API. Resolved filtering must be done client-side. No new dependencies are needed.

**Primary recommendation:** Implement in two waves -- Wave 1: Comment CRUD commands + `comments` list formatting. Wave 2: `cat --comments` annotated view with `annotate.py`. Both waves share the same API layer changes.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

1. **`cat --comments` uses markdown**, not plain text -- the spec says "finds that substring in the markdown" and this aligns with `cat`'s default behavior.

2. **Default `comments` output is multi-line** per the spec example (`#1 [open] author date` + indented content/replies), not tab-separated terse.

3. **State field names** fixed: `last_comment_check` and `known_comment_ids`/`known_resolved_ids` (not `last_comment_time`).

4. **`include_resolved` parameter at API level** -- `list_comments` gains `include_resolved` parameter. Pre-flight always passes `True`. The `comments` command makes its own full API call.

5. **`include_anchor` parameter at API level** -- `list_comments` gains `include_anchor` parameter so only `cat --comments` requests the extra `quotedFileContent` field.

6. **Mutation output format** -- codified per spec: `OK resolved comment #1`, `OK comment #NEW_ID`, etc.

7. **Anchor -> Line Mapping Strategy** -- Use first-occurrence search with ambiguity detection and fallback:
   - Single match -> annotate after that line.
   - Multiple matches -> unanchored with `[anchor ambiguous]`.
   - Zero matches -> unanchored with `[anchor deleted]`.
   - Short anchor (<4 chars) -> unanchored with `[anchor too short]`.
   - Multi-line anchor -> annotate after last line of match span.

8. **No Pre-flight Data Reuse for `comments` Command** -- The `comments` command always makes its own `list_comments` call with `start_modified_time=""` (full fetch). Pre-flight data is used only for the notification banner and state updates.

9. **`cat --comments` Resolved Filtering** -- Default (no `--all`): Only open comments annotated. With `--all`: Resolved comments annotated with `[#ID resolved]`.

10. **State Updates for Comment Mutations** -- Targeted state patches after successful API call, in both quiet and non-quiet modes:
    - `comment` -> Append new comment ID to `known_comment_ids`
    - `resolve` -> Add comment ID to `known_resolved_ids`
    - `reopen` -> Remove comment ID from `known_resolved_ids`
    - `reply` -> No ID set changes
    - `last_comment_check` does NOT advance in quiet mode.
    - Patches applied via `comment_state_patch` parameter in `update_state_after_command()`.

### Claude's Discretion

None specified -- all major decisions were resolved in CONTEXT.md.

### Deferred Ideas (OUT OF SCOPE)

None specified.
</user_constraints>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-api-python-client | >=2.150.0 | Drive API v3 comments/replies CRUD | Already installed; all comment ops use Drive API |

### Supporting

No new libraries needed. All comment operations use the existing `google-api-python-client` dependency. The `annotate.py` module is pure Python string manipulation (no external dependencies).

### Alternatives Considered

None -- the Google Drive API v3 is the only API for comment CRUD on Google Docs files. The Docs API v1 can read comments embedded in document structure but does not support CRUD operations on comments.

## Architecture Patterns

### New File

```
gdoc/
└── annotate.py          # Line-numbered comment annotation for `cat --comments`
```

### Modified Files

```
gdoc/
├── api/
│   └── comments.py      # Add: create_comment, create_reply; modify: list_comments params
├── cli.py               # Add: cmd_comments, cmd_comment, cmd_reply, cmd_resolve, cmd_reopen
│                        # Modify: cmd_cat (--comments path), build_parser (--all on cat)
├── state.py             # Modify: update_state_after_command (comment_state_patch param)
└── format.py            # Add: format_comment_list (multi-line comment output helper)
```

### New Test Files

```
tests/
├── test_comments_cmd.py   # CLI handler tests for comments/comment/reply/resolve/reopen
├── test_annotate.py       # Pure-logic annotation tests (no API mocking needed)
└── (existing test_comments_api.py extended with create/reply tests)
```

### Pattern 1: Comment CRUD via Drive API

**What:** All comment mutations go through `comments.create` and `replies.create` in the Drive API v3.
**When to use:** Every comment operation in Phase 5.

```python
# Source: https://developers.google.com/workspace/drive/api/reference/rest/v3/comments/create

# Create an unanchored comment
def create_comment(file_id: str, content: str) -> dict:
    service = get_drive_service()
    body = {"content": content}
    result = service.comments().create(
        fileId=file_id,
        body=body,
        fields="id, content, author(displayName, emailAddress), createdTime",
    ).execute()
    return result

# Create a text reply
def create_reply(file_id: str, comment_id: str, content: str = "", action: str = "") -> dict:
    service = get_drive_service()
    body = {}
    if content:
        body["content"] = content
    if action:  # "resolve" or "reopen"
        body["action"] = action
    result = service.replies().create(
        fileId=file_id,
        commentId=comment_id,
        body=body,
        fields="id, content, action, author(displayName, emailAddress), createdTime",
    ).execute()
    return result
```

### Pattern 2: Client-Side Resolved Filtering

**What:** The `comments.list` API does NOT support an `includeResolved` parameter. Filtering must be done after fetching.
**When to use:** `list_comments(include_resolved=False)` for `comments` command (default) and `cat --comments` (default).

```python
# After fetching all comments from API:
if not include_resolved:
    all_comments = [c for c in all_comments if not c.get("resolved", False)]
```

### Pattern 3: Resolve/Reopen via Reply Action

**What:** Comments cannot be directly set to resolved. You must create a reply with `action: "resolve"` or `action: "reopen"`.
**When to use:** `gdoc resolve` and `gdoc reopen` commands.

```python
# Source: https://developers.google.com/workspace/drive/api/reference/rest/v3/replies

# Resolve a comment
service.replies().create(
    fileId=file_id,
    commentId=comment_id,
    body={"action": "resolve"},
    fields="id, action",
).execute()

# Reopen a comment
service.replies().create(
    fileId=file_id,
    commentId=comment_id,
    body={"action": "reopen"},
    fields="id, action",
).execute()
```

### Pattern 4: Annotation Module (annotate.py)

**What:** Pure-logic module that takes markdown content + comments list and produces line-numbered annotated output.
**When to use:** `cat --comments` handler.

```python
def annotate_markdown(markdown: str, comments: list[dict], show_resolved: bool = False) -> str:
    """Render line-numbered markdown with inline comment annotations.

    Args:
        markdown: Raw markdown content from export_doc.
        comments: Comments list from list_comments(include_anchor=True).
        show_resolved: If True, include resolved comments with [#ID resolved] markers.

    Returns:
        Formatted string with numbered content lines and un-numbered annotation lines.
    """
    lines = markdown.split("\n")
    # 1. Classify comments as anchored or unanchored
    # 2. Map anchored comments to line numbers via str.find()
    # 3. Build output: numbered lines interleaved with annotations
    # 4. Append unanchored section at bottom
    ...
```

### Pattern 5: State Patch for Comment Mutations

**What:** After a successful comment mutation, apply a targeted patch to state.
**When to use:** cmd_comment, cmd_resolve, cmd_reopen handlers.

```python
# In update_state_after_command:
def update_state_after_command(
    doc_id, change_info, command, quiet=False,
    command_version=None, comment_state_patch=None,
):
    # ... existing logic ...

    # Apply comment mutation patch (both quiet and non-quiet)
    if comment_state_patch:
        if "add_comment_id" in comment_state_patch:
            if comment_state_patch["add_comment_id"] not in state.known_comment_ids:
                state.known_comment_ids.append(comment_state_patch["add_comment_id"])
        if "add_resolved_id" in comment_state_patch:
            if comment_state_patch["add_resolved_id"] not in state.known_resolved_ids:
                state.known_resolved_ids.append(comment_state_patch["add_resolved_id"])
        if "remove_resolved_id" in comment_state_patch:
            rid = comment_state_patch["remove_resolved_id"]
            state.known_resolved_ids = [x for x in state.known_resolved_ids if x != rid]

    save_state(doc_id, state)
```

### Anti-Patterns to Avoid

- **Do NOT pass `includeResolved` to the Google API** -- it is not a valid API parameter and is silently ignored. The existing code's `"includeResolved": True` in `list_comments` should be removed (or kept but understood as a no-op). Filter client-side instead.
- **Do NOT try to update `comment.resolved` directly** -- resolve/reopen can only be done via `replies.create` with the `action` field.
- **Do NOT reuse pre-flight comment data for the `comments` command display** -- pre-flight fetches incrementally (`start_modified_time`), which is incomplete for display. The `comments` command must make its own full fetch.
- **Do NOT advance `last_comment_check` in quiet mode** -- no full comment list was fetched, so advancing could cause missed changes.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Comment CRUD | Custom HTTP calls | `google-api-python-client` service methods | Error handling, auth, pagination already handled |
| Line numbering | Custom formatting | Python string formatting (`f"{n:>6}\t{line}"`) | Simple, matches Read tool format |
| Substring search | Regex for anchor matching | `str.find()` | Simpler, exact match is what's needed, handles multi-line naturally |

**Key insight:** The annotate module is pure string manipulation. It does not need any external libraries. The complexity is in the edge case handling (ambiguity, deletion, short anchors), not in the technology.

## Common Pitfalls

### Pitfall 1: Missing `fields` Parameter on Comments API

**What goes wrong:** All comments API methods (except delete) require a `fields` parameter. Omitting it causes an API error, not a partial response.
**Why it happens:** Most Drive API methods have sensible defaults; the comments resource is an exception.
**How to avoid:** Always include `fields` in every `comments.create`, `comments.list`, `comments.get`, `comments.update`, `replies.create` call.
**Warning signs:** 400 errors from comments API calls.

### Pitfall 2: `includeResolved` is Not an API Parameter

**What goes wrong:** Passing `includeResolved` to `comments.list` does nothing -- the API silently ignores unknown parameters and returns all comments regardless.
**Why it happens:** The parameter does not exist in the Drive API v3. This has been verified against the official REST reference (4 query params only: `includeDeleted`, `pageSize`, `pageToken`, `startModifiedTime`).
**How to avoid:** Filter resolved comments client-side after fetching. The `include_resolved` parameter on our `list_comments` function is a convenience wrapper.
**Warning signs:** Tests pass but resolved comments appear when they shouldn't.

### Pitfall 3: `content` Required for Reply When No Action

**What goes wrong:** `replies.create` requires `content` when no `action` is specified. Sending an empty body or body with only `action` for a text reply fails.
**Why it happens:** The API distinguishes between "text replies" (content required) and "action replies" (resolve/reopen, content optional).
**How to avoid:** For `gdoc reply`: always include `content`. For `gdoc resolve`/`gdoc reopen`: include `action` only, `content` is optional.

### Pitfall 4: Anchor Text Matching False Positives

**What goes wrong:** Short `quotedFileContent.value` strings (e.g., "a", "is") match many locations in the document, causing silent mis-placement of comment annotations.
**Why it happens:** The anchor text is whatever the user selected when creating the comment in Google Docs. Very short selections produce ambiguous anchors.
**How to avoid:** CONTEXT.md Decision: treat anchor text < 4 chars as unanchored with `[anchor too short]` marker. Also handle multiple matches with `[anchor ambiguous]` marker.
**Warning signs:** Comment annotations appearing next to wrong lines.

### Pitfall 5: Pre-flight vs Display Data Confusion

**What goes wrong:** Using pre-flight's incremental comment fetch for the `comments` command output produces incomplete results on subsequent runs.
**Why it happens:** Pre-flight uses `start_modified_time=state.last_comment_check` which returns only recently-modified comments. The display needs ALL comments.
**How to avoid:** CONTEXT.md Decision: `comments` command always makes its own full `list_comments` call with `start_modified_time=""`.

### Pitfall 6: State Desync After Mutations in Quiet Mode

**What goes wrong:** After `gdoc comment DOC "text" --quiet`, the newly created comment ID is not in `known_comment_ids`, causing a spurious "new comment" notification on the next non-quiet run.
**Why it happens:** In quiet mode, pre-flight is skipped entirely and no state update happens for comment sets.
**How to avoid:** CONTEXT.md Decision: Always apply the targeted `comment_state_patch` regardless of quiet/non-quiet mode. Do NOT advance `last_comment_check` in quiet mode.

## Code Examples

### Example 1: comments.create API Call (Unanchored Comment)

```python
# Source: https://developers.google.com/workspace/drive/api/reference/rest/v3/comments/create
# Note: `fields` is MANDATORY

service = get_drive_service()
result = service.comments().create(
    fileId="DOC_ID",
    body={"content": "This is a comment"},
    fields="id, content, author(displayName, emailAddress), createdTime, resolved",
).execute()
# result: {"id": "AAAABBBBcccc", "content": "This is a comment", "author": {...}, ...}
```

### Example 2: replies.create for Text Reply

```python
# Source: https://developers.google.com/workspace/drive/api/reference/rest/v3/replies/create

service = get_drive_service()
result = service.replies().create(
    fileId="DOC_ID",
    commentId="COMMENT_ID",
    body={"content": "I agree with this"},
    fields="id, content, author(displayName, emailAddress), createdTime",
).execute()
# result: {"id": "reply_id", "content": "I agree with this", ...}
```

### Example 3: replies.create for Resolve

```python
# Source: https://developers.google.com/workspace/drive/api/reference/rest/v3/replies
# The `action` field accepts "resolve" or "reopen"

service = get_drive_service()
result = service.replies().create(
    fileId="DOC_ID",
    commentId="COMMENT_ID",
    body={"action": "resolve"},
    fields="id, action",
).execute()
# result: {"id": "reply_id", "action": "resolve"}
```

### Example 4: list_comments with quotedFileContent

```python
# For `cat --comments`, include quotedFileContent in fields to get anchor text

params = {
    "fileId": file_id,
    "includeDeleted": False,
    "fields": (
        "nextPageToken, "
        "comments(id, content, author(displayName, emailAddress), "
        "resolved, modifiedTime, createdTime, "
        "quotedFileContent(value), "
        "replies(author(displayName, emailAddress), modifiedTime, content, action))"
    ),
    "pageSize": 100,
}
```

### Example 5: Annotation Output Format

```
     1\t# Q3 Planning Doc
     2\t
     3\tWe need to ship the roadmap by end of month.
      \t  [#1 open] alice@co.com on "ship the roadmap":
      \t    "This paragraph needs a citation"
      \t    > bob@co.com: "Added, see line 42"
     4\t
     5\tThe budget is $2M for infrastructure.
      \t[UNANCHORED]
      \t  [#5 open] dave@co.com: "General feedback: great doc"
```

Line number format: `%6d\t` for numbered lines, `      \t` (6 spaces + tab) for annotations.

### Example 6: Comments List Output Format (from spec)

```
#1 [open] alice@co.com 2025-01-15
  "This paragraph needs a citation"
  -> bob@co.com: "Added, see line 42"
#2 [open] carol@co.com 2025-01-18
  "Should we include the Q2 comparison?"
```

Note: The spec uses the right arrow character. In the actual output, replies use `->` prefix.

### Example 7: Mutation Output Formats (from spec/CONTEXT.md)

```
# comment
OK comment #NEW_ID

# reply
OK reply on #COMMENT_ID

# resolve
OK resolved comment #ID

# reopen
OK reopened comment #ID
```

JSON variants:
```json
{"ok": true, "id": "NEW_ID", "status": "created"}
{"ok": true, "commentId": "COMMENT_ID", "replyId": "REPLY_ID", "status": "created"}
{"ok": true, "id": "COMMENT_ID", "status": "resolved"}
{"ok": true, "id": "COMMENT_ID", "status": "reopened"}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Drive API v2 `comments.update` with `status` field | Drive API v3 `replies.create` with `action` field | v3 (2015+) | Cannot directly set resolved; must use reply action |
| No `fields` requirement | `fields` mandatory on comments API | v3 | All comments API calls must specify fields or get errors |

**Deprecated/outdated:**
- Drive API v2 comment operations: v2 allowed direct `status` updates on comments. v3 removed this and requires resolve/reopen via reply actions.

## Open Questions

1. **`--json` output for `cat --comments`**
   - What we know: CONTEXT.md says `--json` should be supported and "your plan to wrap annotated content + structured comments is reasonable."
   - What's unclear: The exact JSON schema for `cat --comments --json`. Likely `{"ok": true, "content": "annotated_string", "comments": [...]}` or `{"ok": true, "annotated": "...", "raw": "...", "comments": [...]}`.
   - Recommendation: Use `{"ok": true, "content": "<annotated text>"}` for simplicity, matching existing `cat --json` output shape. The annotated text in JSON mode is the same line-numbered format. If the agent needs structured comments, it uses `gdoc comments DOC --json`.

2. **Existing `includeResolved: True` in comments.py**
   - What we know: The current `list_comments` passes `"includeResolved": True` to the API, but this parameter does not exist in the Drive API v3.
   - What's unclear: Whether to remove it (cleaner) or leave it (harmless).
   - Recommendation: Remove it when adding the new `include_resolved` client-side filter parameter to avoid confusion between the non-existent API param and the real client-side filter.

3. **`--all` flag on `cat` subparser**
   - What we know: Currently `cat` does not have `--all`. It needs to be added so `cat --comments --all` includes resolved comments.
   - Recommendation: Add `--all` to `cat` subparser (not in the mutually-exclusive group). It only has effect when `--comments` is also set.

## Sources

### Primary (HIGH confidence)
- Google Drive API v3 REST Reference: comments resource -- https://developers.google.com/workspace/drive/api/reference/rest/v3/comments
- Google Drive API v3 REST Reference: replies resource -- https://developers.google.com/workspace/drive/api/reference/rest/v3/replies
- Google Drive API v3: comments.list -- https://developers.google.com/workspace/drive/api/reference/rest/v3/comments/list (confirmed: NO `includeResolved` parameter)
- Google Drive API v3: comments.create -- https://developers.google.com/workspace/drive/api/reference/rest/v3/comments/create
- Google Drive API v3: replies.create -- https://developers.google.com/workspace/drive/api/reference/rest/v3/replies/create
- Manage comments and replies guide -- https://developers.google.com/workspace/drive/api/guides/manage-comments
- Python client library reference: comments -- https://developers.google.com/resources/api-libraries/documentation/drive/v3/python/latest/drive_v3.comments.html
- Python client library reference: replies -- https://developers.google.com/resources/api-libraries/documentation/drive/v3/python/latest/drive_v3.replies.html

### Secondary (MEDIUM confidence)
- Stack Overflow: resolving comments via reply action -- https://stackoverflow.com/questions/74069563/how-do-i-mark-a-comment-resolved-with-the-google-drive-comment-api (confirms v3 requires reply action, not direct status update)

### Codebase (HIGH confidence)
- `gdoc/api/comments.py` -- existing `list_comments` with pagination, error translation
- `gdoc/cli.py` -- existing parser with stub handlers for comments/comment/reply/resolve/reopen
- `gdoc/state.py` -- existing `DocState` with `last_comment_check`, `known_comment_ids`, `known_resolved_ids`
- `gdoc/notify.py` -- existing pre-flight with comment change detection
- `gdoc.md` -- spec with output format examples for all comment commands

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all verified against existing codebase and official docs
- Architecture: HIGH -- file layout specified in spec, patterns follow existing codebase conventions
- API surface: HIGH -- all endpoints verified against official REST reference and Python client docs
- Pitfalls: HIGH -- `includeResolved` finding verified across 4 official doc pages; resolve-via-reply verified in docs + Stack Overflow
- Annotate module: HIGH -- algorithm fully specified in CONTEXT.md decisions; pure logic, no API uncertainty

**Research date:** 2026-02-07
**Valid until:** 2026-03-07 (stable -- Google Drive API v3 is mature, unlikely to change)
