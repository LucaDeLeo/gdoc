Phase 03 context decisions for the Awareness System.

> **Canonical decisions live here.** If this file conflicts with earlier drafts or summaries, this file wins.

## Scope Confirmation

Phase 3 scope is **exclusively the Awareness System**: requirements AWARE-01 through AWARE-04. The Codex review flagged a potential mismatch with "auth/CLI framework consistency," but that refers to the v1.0 milestone as a whole — auth and CLI framework were completed in Phase 1. Phase 3 adds no auth or CLI framework changes beyond what's needed for awareness integration.

**In scope:** State tracking, pre-flight change detection, notification banners, conflict detection (warn/block), `--quiet` flag.
**Out of scope:** Comment CRUD (Phase 5), write operations (Phase 4), file management (Phase 6).

## Decision Table

### 1. State Schema

**Decision:** Per-doc JSON state at `~/.gdoc/state/{DOC_ID}.json` with the following schema:

```json
{
  "last_seen": "2025-01-20T14:30:00Z",
  "last_version": 847,
  "last_read_version": 847,
  "last_comment_check": "2025-01-20T14:30:00Z",
  "known_comment_ids": ["AAA", "BBB"],
  "known_resolved_ids": ["CCC"]
}
```

| Field | Purpose | Set by |
|-------|---------|--------|
| `last_seen` | Timestamp of last interaction (any command) | All DOC_ID commands |
| `last_version` | Doc version at last interaction | All DOC_ID commands |
| `last_read_version` | Doc version at last **read** | `cat` and `info` only |
| `last_comment_check` | Timestamp for `comments.list(startModifiedTime=...)` | All DOC_ID commands |
| `known_comment_ids` | Comment IDs seen, for detecting new comments | All DOC_ID commands |
| `known_resolved_ids` | Comment IDs that were resolved, for detecting resolve/reopen | All DOC_ID commands |

**Rationale:**
- `last_version` vs `last_read_version`: The spec says conflict detection triggers "since last `cat`" (`gdoc.md` L150: "the doc was edited since last `cat`"). General awareness banners use `last_version` (any interaction), but conflict detection for `edit`/`write` uses `last_read_version` (set only by read commands). This is a critical distinction — without it, running `gdoc info DOC` would reset the conflict baseline and mask edits made after the last `cat`.
- `known_resolved_ids`: Spec extension. Required to detect `✓` (resolved) vs `↺` (reopened) transitions. The spec notification table (`gdoc.md` L145-146) requires both symbols, which is impossible without tracking prior resolved state.
- Atomic writes: temp file + `os.rename()` + `os.makedirs(exist_ok=True)`.

**Source:** `gdoc.md` L84-91 (state schema), L150 (conflict trigger).

### 2. Pre-flight Check: Which Commands

**Decision:** Pre-flight runs on **every command that takes a DOC_ID argument**. Specifically:

| Command | Pre-flight? | Conflict check? | Rationale |
|---------|-------------|-----------------|-----------|
| `cat` | Yes | No (it IS a read) | Spec: "any command targeting a DOC_ID" |
| `info` | Yes | No (it IS a read) | Same as cat |
| `edit` | Yes | Yes → **warn** | Spec L153-158: warning, doesn't block |
| `write` | Yes | Yes → **block** | Spec L163-169: blocks unless `--force` |
| `comments` | Yes | No | Read-only command |
| `comment` | Yes | No | Mutating but not doc body |
| `reply` | Yes | No | Mutating but not doc body |
| `resolve` | Yes | No | Mutating but not doc body |
| `reopen` | Yes | No | Mutating but not doc body |
| `share` | Yes | No | Targets a DOC_ID |
| `cp` | Yes | No | Targets a DOC_ID |
| `auth` | No | No | No DOC_ID |
| `ls` | No | No | No DOC_ID |
| `find` | No | No | No DOC_ID |
| `new` | No | No | Creates new doc, no existing DOC_ID |

**Rationale:** `gdoc.md` L96: "Before executing any command targeting a DOC_ID." The spec is unambiguous — it says "any command." Commands that don't take a DOC_ID (auth, ls, find, new) are naturally excluded.

**Source:** `gdoc.md` L96.

### 3. Pre-flight API Calls

**Decision:** Two API calls before command execution:

1. `files.get(fileId, fields="modifiedTime,version,lastModifyingUser")` — detect doc body edits
2. `comments.list(fileId, startModifiedTime=last_comment_check, includeResolved=true, fields="comments(id,content,author,resolved,modifiedTime,replies(author,modifiedTime,content,action))")` — detect comment activity

**First-interaction rule:** When `last_comment_check` is absent (no state file exists — first interaction with this doc), the `startModifiedTime` parameter MUST be omitted entirely from the `comments.list` call (not passed as `None` or empty string). Omitting the parameter causes the API to return all comments on the doc, which is the correct behavior for first interaction: we need the full comment set to initialize `known_comment_ids` and `known_resolved_ids`. This aligns with Decision #8 (first interaction banner shows comment counts).

**Rationale:** Matches `gdoc.md` L98-99. `includeResolved=true` is required to detect resolve/reopen transitions. Reply fields needed to produce `↩` (new reply) notifications. Omitting `startModifiedTime` on first interaction avoids passing an invalid value and ensures complete state initialization.

**Source:** `gdoc.md` L98-99, L140-146, L183-193.

### 4. Banner Output: stderr (CORRECTED)

**Decision:** Notification banners go to **stderr**, not stdout.

This **reverses** the previous decision ("Banner on stdout"). The correction is based on:

1. **OUT-06 requirement** (REQUIREMENTS.md): "Data to stdout, banners/warnings to stderr (pipe-safe)" — this is an explicit, formal requirement.
2. **Design principle #4** (`gdoc.md` L4): "Pipe-friendly — reads stdin, writes stdout, exits with proper codes."
3. **Pipe safety:** `gdoc cat DOC > file.md` must capture only document content. If the banner were on stdout, it would be captured in the file — this breaks the pipe-friendly contract.
4. **Terminal equivalence:** In a terminal, stderr and stdout are interleaved visually, so the spec examples (which show banner before content) look identical regardless of which fd the banner uses. The examples don't specify fd; OUT-06 does.

**Consequences:**
- `gdoc cat DOC > file.md` → file contains only doc content (correct)
- `gdoc cat DOC 2>/dev/null` → suppresses banner, shows only content
- `--json` stdout is always valid JSON without needing to suppress banners (banners are already on stderr)
- `--quiet` still saves 2 API calls (its purpose is overhead reduction, not just visual suppression)

**Source:** REQUIREMENTS.md OUT-06, `gdoc.md` Design Principle #4.

### 5. `--json` Mode: No Banner Suppression Needed

**Decision:** `--json` mode does not need to suppress banners because banners are on stderr (Decision #4). JSON stdout is always clean.

If `--json` is active, the pre-flight check still runs (unless `--quiet`), and the banner still prints to stderr. The JSON output on stdout is unaffected.

**Source:** Follows directly from Decision #4.

### 6. `--quiet` Short-Circuits Before Network I/O

**Decision:** When `--quiet` is passed, the pre-flight check is skipped entirely. No `files.get`, no `comments.list`. The `--quiet` flag must be checked **before** any pre-flight network call, not after fetching data.

**Implementation:** The notify module's check function returns immediately (no-op) when `--quiet` is set. This is the first check in the function body, before any API import or call.

**Rationale:** `gdoc.md` L180-181: "Skips the pre-flight check entirely — saves 2 API calls." The word "entirely" means no network I/O at all. An implementation that fetches data and then suppresses output would violate the "saves 2 API calls" guarantee.

**Note:** `--quiet` also suppresses conflict detection for `edit`/`write`. This is spec-compliant — the spec says `--quiet` skips the entire pre-flight, which includes conflict checks. If the user passes `--quiet --force`, no pre-flight runs and no conflict block applies.

**State freeze rule:** When `--quiet` skips the pre-flight, the post-success state update MUST NOT advance `last_comment_check`. This field is derived from `comments.list` results — if the call never happened, there is no data to update from. Advancing `last_comment_check` without fetching comments would permanently skip any comment activity that occurred before/during the quiet run.

**Exception — post-mutation comment IDs (interaction with Decision #9):** For comment-mutating commands (`comment`, `reply`, `resolve`, `reopen`), `known_comment_ids` and `known_resolved_ids` MUST still be updated from the mutation response, even under `--quiet`. These updates are derived from the command's own API response (e.g., the `id` returned by `comments.create`), not from a skipped `comments.list` call. Without this, the next non-quiet pre-flight would misclassify our own comment as external activity — a false-positive notification. The freeze rule protects against advancing state based on *absent data*; post-mutation IDs are *present data* from a call that actually happened.

**Summary of `--quiet` state updates** (simplified — see Decision #14 for the full per-command table):

| Field | Updated on `--quiet` run? | Source |
|-------|--------------------------|--------|
| `last_seen` | Yes | Clock |
| `last_version` | **Depends on command** — `info` and mutating commands: yes; `cat`: **no** (stale) | See Decision #14 |
| `last_read_version` | **Depends on command** — `info`: yes; `cat`: **no** (stale) | See Decision #14 |
| `last_comment_check` | **No** — frozen | Would require `comments.list` |
| `known_comment_ids` | **Only** for comment-mutation commands | Mutation API response (Decision #9) |
| `known_resolved_ids` | **Only** for comment-mutation commands | Mutation API response (Decision #9) |

**Source:** `gdoc.md` L174-181, AWARE-04. Version staleness for `--quiet cat` detailed in Decision #14.

### 7. Conflict Detection Uses `last_read_version`

**Decision:** Conflict detection (warn for `edit`, block for `write`) compares the current doc `version` against `last_read_version` from state, not `last_version`.

- If `last_read_version` is `None` (no prior read), treat as conflict — this is equivalent to "you haven't read the doc yet."
- `edit`: Print `⚠ WARNING: doc changed since your last read.` on stderr, then proceed.
- `write`: Print `ERR: doc modified since last read. Use --force to overwrite, or \`gdoc cat\` first.` on stderr, exit 1.

**Rationale:** `gdoc.md` L150: "the doc was edited since last `cat`." The conflict baseline is the last read, not the last interaction. If a user runs `gdoc comment DOC "text"` and then `gdoc edit DOC "old" "new"`, the conflict check should compare against the version from the last `cat`/`info`, not the version from the `comment` command.

**Source:** `gdoc.md` L150, L162-169.

### 8. First Interaction Banner

**Decision:** When no state file exists for a doc (first interaction), show a metadata + comment summary banner on stderr:

```
--- first interaction with this doc ---
 📄 "Q3 Planning Doc" by alice@co.com, last edited 2025-01-20
 💬 3 open comments, 1 resolved
---
```

This requires the same 2 pre-flight API calls. After the command succeeds, state is initialized with all fields.

**Source:** `gdoc.md` L183-193.

### 9. Post-Mutation Version Fetch

**Decision:** After mutating commands (`edit`, `write`, `comment`, `reply`, `resolve`, `reopen`) succeed, do an extra `files.get(fields="version")` call to capture the post-mutation version. Store this as both `last_version` and (for `edit`/`write`) do NOT update `last_read_version` — only `cat` and `info` set that.

**Comment-mutation state update:** For comment-mutating commands (`comment`, `reply`, `resolve`, `reopen`), the post-mutation state update MUST also update `known_comment_ids` and `known_resolved_ids` to reflect the IDs just created/changed. Two approaches:

1. **Preferred — use mutation response:** The `comments.create`, `comments.update`, and `replies.create` API responses return the comment/reply object with its `id` and `resolved` status. Add the new comment ID to `known_comment_ids`; for `resolve`, move the ID to `known_resolved_ids`; for `reopen`, remove it from `known_resolved_ids`.
2. **Fallback — post-mutation `comments.list`:** If the mutation response doesn't provide sufficient data (e.g., `resolve`/`reopen` might not return the full comment), do a targeted `comments.list(fileId, startModifiedTime=<now - 5s>, includeResolved=true)` and merge results into state.

Without this, the next pre-flight would misclassify our own comment activity as external (e.g., a reply we just created appears as "new reply" on the next run, or a comment we just resolved appears as a state change).

**`--quiet` interaction (see Decision #6):** This post-mutation comment-ID update applies even when `--quiet` is set. The `--quiet` state freeze rule only prevents advancing fields derived from skipped `comments.list` calls (`last_comment_check`). Post-mutation IDs come from the mutation response itself — a call that was not skipped — so they must always be recorded.

**Rationale:** Without the version fetch, the next pre-flight check would detect our own mutation as an "external change" and show a false-positive banner. Without the comment-state update, the same false-positive problem applies to comment notifications. The extra API call(s) cost ~100ms but prevent confusing notifications.

**Source:** `gdoc.md` L101 ("update stored state"), Codex review item #3.

### 10. Module Structure

**Decision:** New files in this phase:

| File | Purpose |
|------|---------|
| `gdoc/state.py` | Load/save per-doc state, atomic writes |
| `gdoc/notify.py` | Pre-flight check, banner formatting, conflict detection |

Per-handler integration in `cli.py` (no middleware). Each command handler that takes a DOC_ID calls `notify.pre_flight(doc_id, args)` at the top and `state.update(doc_id, ...)` after success.

**Source:** `gdoc.md` L238-239 (architecture).

### 11. Per-Handler Integration Pattern

**Decision:** No middleware or decorator. Each CLI handler calls:

```python
# At top of handler (after --quiet check):
changes = notify.pre_flight(doc_id, state, quiet=args.quiet)

# For edit/write only:
if changes and changes.has_conflict:
    if command == "write" and not args.force:
        # block
    elif command == "edit":
        # warn on stderr

# After successful operation:
state.update(doc_id, version=..., is_read=(command in ("cat", "info")))
```

**Rationale:** Different commands have different conflict behaviors (warn vs block vs none). A per-handler approach keeps this explicit and avoids over-engineering a middleware layer for 4 lines of integration code.

### 12. `last_comment_check` Advancement Rule

**Decision:** On non-quiet runs, `last_comment_check` is set to a **pre-request timestamp** captured immediately before the `comments.list` API call — not the post-success wall-clock time, and not the max `modifiedTime` from results.

**Implementation:**

```python
# In notify.pre_flight():
preflight_ts = datetime.now(timezone.utc).isoformat()
comments = comments_list(fileId, startModifiedTime=stored.last_comment_check, ...)

# In state.update() after command success:
state.last_comment_check = preflight_ts  # from pre_flight return value
```

**Why pre-request, not post-success?**

| Strategy | Risk |
|----------|------|
| Pre-request timestamp (T0) | Comments modified at exactly T0 may appear in both this run's results and the next run's `startModifiedTime=T0` query. But `known_comment_ids` deduplicates them — no false-positive notification. |
| Post-success timestamp (T2) | Comments arriving between T0 and T2 were never fetched, but `last_comment_check` jumps past them → **permanently missed**. |
| Max `modifiedTime` from results | If the result set is empty, there is no max — requires a fallback, which re-introduces one of the above two risks. |

**Why this is safe:** Even if the `startModifiedTime=T0` query returns a comment we already processed, `known_comment_ids` prevents duplicate notifications. The timestamp is a performance optimization (limiting API response size), not the correctness mechanism — the ID sets are.

**Source:** Gap identified by Codex review. Consistent with Decision #6 state freeze rule (which blocks this field under `--quiet` because no `comments.list` call happened).

### 13. `comments.list` Pagination

**Decision:** The `comments.list` call in the pre-flight MUST paginate to completion, consuming all `nextPageToken` values until the full result set is retrieved.

**Why this matters:**
- Google Drive API `comments.list` defaults to `pageSize=20` and uses `pageToken`/`nextPageToken` pagination.
- On **first interaction** (no `startModifiedTime`, per Decision #3), a doc with >20 comments returns only the first page. This leaves `known_comment_ids`/`known_resolved_ids` incomplete and the first-interaction banner counts wrong (Decision #8 shows "3 open comments, 1 resolved" — these must be accurate totals).
- On **long intervals** between interactions, the `startModifiedTime` window may span many comment changes, also exceeding one page.

**Implementation:** Follow the same pagination pattern already used in `api/drive.py:list_files()` — loop on `nextPageToken` until exhausted, accumulating all comment objects.

```python
def list_comments(file_id, start_modified_time=None, ...):
    all_comments = []
    page_token = None
    while True:
        params = {"fileId": file_id, "includeResolved": True, "fields": "..."}
        if start_modified_time:
            params["startModifiedTime"] = start_modified_time
        if page_token:
            params["pageToken"] = page_token
        resp = service.comments().list(**params).execute()
        all_comments.extend(resp.get("comments", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return all_comments
```

**Performance note:** For subsequent runs with a recent `startModifiedTime`, the result set is typically small (a few comments modified since last check), so pagination rarely activates. The loop adds no overhead in the common case.

**Source:** Gap identified by Codex review. Pagination pattern consistent with existing `list_files()` implementation.

### 14. `--quiet` Read Commands and Version Staleness

**Decision:** When `--quiet` is active and the command is `cat`, `last_version` and `last_read_version` are **not updated** — they remain at their prior values (stale). No extra `files.get` call is made.

**Problem:** `cat` calls `export_doc()`, which uses `files().export_media()` — an endpoint that returns only document content bytes, no metadata. On a non-quiet run, the pre-flight `files.get` provides the version. Under `--quiet`, the pre-flight is skipped and there is no other source for version data.

**Options considered:**

| Option | Pros | Cons |
|--------|------|------|
| **(A) Accept staleness** | Zero extra API calls; honors "saves 2 API calls" guarantee | `last_read_version` may be stale, causing false-positive conflict warnings on subsequent `edit`/`write` |
| (B) Post-command `files.get(fields="version")` | Correct version data | Only saves 1 API call instead of 2; partially defeats `--quiet` purpose |

**Chosen: (A) Accept staleness.**

**Rationale:**
1. The spec says `--quiet` "saves 2 API calls." Adding one back contradicts this.
2. `--quiet` is an expert/automation flag — users accept reduced guarantees.
3. Stale `last_read_version` causes false-positive conflicts (warning/blocking when unnecessary), not false-negatives (missing real conflicts). Erring on the side of caution is the safer failure mode.
4. Users can reset the version baseline at any time by running `cat` or `info` without `--quiet`.

**Note on `info`:** This gap does NOT affect `info`. `cmd_info` calls `get_file_info()` which uses `files.get(fields="...version...")` as part of its normal operation — version data is available from the command response itself, even under `--quiet`.

**Corrected `--quiet` state update table (supersedes Decision #6 table):**

| Field | `--quiet cat` | `--quiet info` | `--quiet` mutating cmd | Source |
|-------|--------------|----------------|----------------------|--------|
| `last_seen` | Yes | Yes | Yes | Clock |
| `last_version` | **No** — stale | Yes | Yes | `info` response / post-mutation `files.get` |
| `last_read_version` | **No** — stale | Yes | N/A (not a read) | `info` response |
| `last_comment_check` | **No** — frozen | **No** — frozen | **No** — frozen | Would require `comments.list` |
| `known_comment_ids` | No | No | **Only** for comment-mutation cmds | Mutation API response (Decision #9) |
| `known_resolved_ids` | No | No | **Only** for comment-mutation cmds | Mutation API response (Decision #9) |

**Source:** Gap identified by Codex review. `export_doc` implementation confirmed to return content only (`files().export_media()` → raw bytes).

---

## Kept As-Is (from previous round)

1. `~/.gdoc/state/{DOC_ID}.json` with atomic writes (temp + rename)
2. Post-success state updates (now with post-mutation version fetch)
3. Per-handler integration (no middleware)
4. `edit` warns / `write` blocks on conflict

## Previously Flagged — Now Resolved

| Item | Previous Status | Resolution |
|------|----------------|------------|
| Banner to stdout | Flagged for review | **Reversed.** Banner goes to stderr per OUT-06. |
| `known_resolved_ids` as spec extension | Flagged for review | **Kept.** Required for ✓/↺ detection. Dev-only extension policy (Phase 1 CONTEXT.md) applies: does not contradict spec, serves a spec-defined behavior. |
| Scope mismatch | Flagged by Codex | **Resolved.** Phase 3 = awareness only. Auth/CLI framework was Phase 1. |
| `last_read_version` vs `last_version` | Flagged by Codex | **Resolved.** Separate fields; see Decision #1 and #7. |
| Pre-flight for share/cp | Flagged by Codex | **Resolved.** Pre-flight applies to all DOC_ID commands; see Decision #2. |
| `--quiet` network behavior | Not previously captured | **Resolved.** Short-circuits before any API calls; see Decision #6. |
| `--quiet` state freeze | Not previously captured | **Resolved.** Quiet runs do not advance comment-related state fields; see Decision #6 state freeze rule. |
| `--json` + banner | Not previously captured | **Resolved.** No conflict — banners on stderr, JSON on stdout; see Decision #5. |
| Comment-mutation state gap | Not previously captured | **Resolved.** Post-mutation update includes comment IDs from response; see Decision #9. |
| First-interaction `startModifiedTime` | Not previously captured | **Resolved.** Omit parameter entirely on first interaction; see Decision #3. |
| `--quiet` + comment-mutation state conflict | Not previously captured | **Resolved.** Decision #6 freeze rule scoped to `last_comment_check` only; post-mutation comment-ID updates (Decision #9) always apply. See Decision #6 exception and Decision #9 `--quiet` interaction. |
| `last_comment_check` advancement unspecified | Flagged by Codex | **Resolved.** Pre-request timestamp strategy; `known_comment_ids` handles dedup. See Decision #12. |
| `comments.list` pagination missing | Flagged by Codex | **Resolved.** Must paginate to completion; follows existing `list_files` pattern. See Decision #13. |
| `--quiet cat` version staleness | Flagged by Codex | **Resolved.** Accept staleness — no extra API call. False-positive conflicts are the safer failure mode. Decision #6 table superseded by Decision #14 expanded table. See Decision #14. |

---

## Auto-Discuss Metadata

- **Rounds:** 6 (3 original + 3 fix passes)
- **Codex Available:** yes
- **Uncertainties Resolution:** all resolved
- **Timestamp:** 2026-02-07T21:02:43Z (original), updated 2026-02-07

<details>
<summary>Codex Review (Round 2) — preserved for reference</summary>

**Key Findings**
- The proposed state schema and pre-flight fields don't match the spec in `gdoc.md` (version/lastModifyingUser, last_comment_check, known_comment_ids) and will miss required awareness signals like replies/resolved/reopened. `gdoc.md#L82` `gdoc.md#L94` `gdoc.md#L138`
- "First interaction" behavior conflicts with the spec, which expects a banner and summary instead of silence. `gdoc.md#L183`
- State updates after mutating commands (edit/write/comment actions) need post-mutation metadata or you'll immediately report your own changes as "new."

**Decision Review**
1. [AGREE] `~/.gdoc/state/{doc_id}.json` aligns with the spec. Use atomic writes (temp + rename) and `mkdir` to avoid corruption/races.
2. [QUESTION] Spec uses `last_seen` as interaction time, plus `last_version`, `last_comment_check`, and `known_comment_ids` (and you'll need prior `resolved` state to detect reopen/resolve). `headRevisionId` is not in the spec and may be unreliable. `gdoc.md#L82`
3. [AGREE] Update after success is correct. For mutating commands, update from post-command metadata or a follow-up `files.get`; otherwise the next run will see your own change as "external."
4. [QUESTION] The spec calls for `files.get(fields="modifiedTime,version,lastModifyingUser")` and `comments.list(startModifiedTime=last_comment_check, ...)`. You also need `includeResolved=true` and enough comment/reply fields to produce the banner details. `gdoc.md#L94` `gdoc.md#L138`
5. [QUESTION] Banner format and placement diverge from the examples (`--- since last interaction ... ---`, `--- no changes ---`, icons). Decide stdout vs stderr explicitly; if stderr, update docs/tests to keep `--json` stdout clean. `gdoc.md#L105`
6. [SUGGEST] Docs list `notify.py` as the awareness module. Either align to that or update the architecture section. `gdoc.md#L197`
7. [AGREE] Per-handler integration is reasonable given different behaviors (warn vs block). Ensure `--quiet` short-circuits before any pre-flight calls.
8. [QUESTION] The block/warn condition should be "doc edited since last read" per spec. That likely requires a separate "last_read_version/time" (set on `cat`/`info`) rather than only "last interaction." `gdoc.md#L148`
9. [QUESTION] Spec implies pre-flight for any command targeting a DOC_ID. If so, add `--quiet` (and checks) for `share`/`cp` too, or document why they're excluded.
10. [QUESTION] Spec expects a "first interaction" banner with doc metadata and comment counts, not silence. `gdoc.md#L183`

**Additional Gaps**
- Scope mismatch: your milestone goal mentions auth/CLI framework consistency, but the proposal targets awareness. Confirm Phase 03 scope before implementing.
- Comment awareness needs prior `resolved` state (not just IDs) to detect resolve/reopen accurately.
- Tests missing for: `--quiet` skipping pre-flight; `--json` output staying valid; first-interaction banner; edit vs write conflict behavior; state updates after mutating commands; includeResolved/reply detection.

</details>
