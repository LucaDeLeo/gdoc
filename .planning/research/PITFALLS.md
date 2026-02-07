# Pitfalls Research

**Domain:** Google Docs/Drive API CLI tool for AI agents
**Researched:** 2026-02-07
**Confidence:** MEDIUM (all findings based on training data through May 2025; external research tools were unavailable for live verification)

## Critical Pitfalls

### Pitfall 1: batchUpdate Index Invalidation (Reverse-Order Requirement)

**What goes wrong:**
When sending multiple `insertText`, `deleteContentRange`, or mixed operations in a single `batchUpdate` call, each operation shifts subsequent character indexes. Developers calculate indexes against the original document state, but requests execute sequentially -- the first insert shifts all downstream indexes, causing the second operation to target the wrong location. The result is corrupted document content or API errors for out-of-range indexes.

**Why it happens:**
The Docs API processes requests in a `batchUpdate` array in order, and each mutation changes the document's index space before the next request executes. This is explicitly documented by Google but easy to miss. Developers naturally think "I read the doc, I know the indexes" and send multiple operations against those original indexes.

**How to avoid:**
- **Sort operations in reverse document order** (highest index first). Mutations at higher offsets do not affect the index space of lower offsets. This is the canonical solution.
- For `replaceAllText`, this pitfall does not apply because `replaceAllText` does not use index-based addressing. This is why `replace` is the safer edit primitive for agents.
- For `insertText` + `deleteContentRange` in the same batch: compute cumulative offset deltas if forward-order is needed, or just reverse-sort.
- Alternatively, send one operation per `batchUpdate` call, but this is wasteful and slower.
- **Never mix `replaceAllText` with index-based operations in the same batch** unless you fully understand the interaction. `replaceAllText` can change document length unpredictably (the replacement string may differ in length from what was matched).

**Warning signs:**
- Text appearing at wrong positions after multi-operation edits
- "Invalid range" or "index out of bounds" errors on the second or third request in a batch
- Tests passing with single operations but failing with batched operations

**Phase to address:**
Phase 1 (core edit operations). The `insert` and `delete` commands must implement reverse-ordering from day one. `replace` (using `replaceAllText`) is immune and should be the recommended agent primitive.

---

### Pitfall 2: OAuth2 Token Refresh Failures and "Testing" App Status

**What goes wrong:**
Multiple failure modes:

1. **Refresh token expiration in "Testing" publish status:** Google Cloud Console apps in "Testing" status (not "In Production") have refresh tokens that expire after 7 days. After 7 days, the user must re-authenticate. Developers build the CLI, test it, ship it, and then users report "auth broke after a week" and nobody understands why.

2. **Refresh token revocation without notification:** Users can revoke access via Google Account settings (myaccount.google.com > Security > Third-party apps). The CLI gets a `invalid_grant` error on the next token refresh, which is often not handled gracefully.

3. **Credential leakage with `credentials.json`:** The OAuth client secret in `credentials.json` for "Desktop" app type is not truly secret (Google documents this). But developers still sometimes try to embed it in the binary or treat it as a real secret, causing confusion.

4. **Scope changes require re-consent:** If you add a new OAuth scope (e.g., adding `drive.file` or `spreadsheets`), existing `token.json` files lack that scope. The CLI must detect this and trigger re-auth, not just fail with a 403.

**Why it happens:**
OAuth2 has many edge cases and Google adds its own (the 7-day testing expiry is Google-specific). The happy path works perfectly during development, so these edge cases go undiscovered until production usage.

**How to avoid:**
- Set the Google Cloud Console app to "In Production" publish status before distributing. The 7-day limit disappears. This requires a quick OAuth consent screen configuration but does not require Google review for "internal" or limited-scope apps.
- Handle `invalid_grant` errors explicitly: delete `token.json` and trigger re-auth flow with a clear user-facing message ("Your authorization expired. Run `gdoc auth` to re-authenticate.").
- Store requested scopes in `token.json` alongside the token. On startup, compare stored scopes against required scopes. If they differ, trigger re-auth.
- Never treat `credentials.json` (Desktop type) as secret. Document that users create their own OAuth client in Google Cloud Console.

**Warning signs:**
- "Works for a week then breaks" user reports
- `invalid_grant` errors in logs
- 403 errors after adding new API features
- Works on developer machine but not on user machines

**Phase to address:**
Phase 1 (auth setup). The auth module must handle all refresh failure modes from the start. This is foundational -- every other feature depends on working auth.

---

### Pitfall 3: `push` (Full Doc Overwrite) Destroys Concurrent Edits

**What goes wrong:**
The `push` command uses `files.update` with media upload to overwrite the entire document. If a human is editing the document simultaneously, their changes are silently destroyed. Unlike collaborative editing in the Google Docs UI (which uses operational transforms), the Drive API `files.update` is a last-write-wins full replacement. There is no merge, no conflict detection at the API level, and no undo.

**Why it happens:**
Developers assume Google Docs' collaboration model extends to the API. It does not. The Docs API `batchUpdate` participates in operational transform (OT) and handles concurrent edits gracefully. But `files.update` with media upload bypasses OT entirely -- it replaces the document content wholesale.

**How to avoid:**
- **The awareness system's version check is critical.** Block `push` if `version` has changed since the last `cat`. This is already designed into gdoc (good).
- Store the version number from the last `cat` and pass it to the `push` pre-check. If versions diverge, refuse and instruct the agent to re-read.
- `--force` flag should exist but require explicit opt-in, and even then, log a loud warning.
- **Prefer `replace` over `push` whenever possible.** `replaceAllText` is OT-safe and handles concurrent editing gracefully. Only use `push` for wholesale document restructuring.
- Consider a diff-based approach for `push`: read the doc, compute the diff against the local file, and apply individual `insertText`/`deleteContentRange` operations instead of full replacement. This is more complex but OT-safe.

**Warning signs:**
- Users reporting "my edits disappeared"
- The awareness banner showing version changes immediately before a `push`
- Agent workflows that do `cat` then `push` with no version guard

**Phase to address:**
Phase 1 (push command implementation). The version guard must ship with the initial `push` command, not be added later.

---

### Pitfall 4: Google Drive API Rate Limits Are Per-User AND Per-Project

**What goes wrong:**
The Drive API has multiple rate limit dimensions that interact in non-obvious ways:
- **Per-project:** ~12,000 queries per minute (QPM) across all users of that OAuth client. Shared across everyone using the same `credentials.json` client ID.
- **Per-user per-project:** ~12 QPM for some expensive operations (e.g., `files.create`, `files.update` with media). Standard reads are higher.
- **Per-user:** Overall limits on how fast a single user can hit the API regardless of project.

The awareness system makes 2 API calls before every command. If an agent runs rapid-fire commands (e.g., iterating over 50 docs), that is 100+ API calls in seconds, easily hitting per-user rate limits.

**Why it happens:**
Developers test with one user on one doc and never hit limits. Rate limits only surface under real agent usage patterns (batch operations, rapid iteration, multiple docs).

**How to avoid:**
- Implement exponential backoff with jitter on 429 (rate limit) responses. Google's client libraries have built-in retry, but you must enable it.
- The `--quiet` flag (skipping awareness checks) is essential for batch operations. Document this prominently for agent usage.
- Consider a rate limiter in the CLI itself: cap at ~8-10 requests per second per user to stay safely under limits.
- Cache `files.get` responses briefly (e.g., 30 seconds). If the agent runs `replace` then immediately runs `comments`, the second command's awareness check can use the cached result from the first.
- For `ls` and `find` commands that paginate: count page fetches against the rate limit budget.

**Warning signs:**
- 429 "Rate Limit Exceeded" errors, especially during batch operations
- Intermittent failures that "work when I retry"
- Increasing latency as Google applies progressive throttling before hard 429s
- `userRateLimitExceeded` vs `rateLimitExceeded` error reasons (different limit dimensions)

**Phase to address:**
Phase 1 (API client setup). Backoff/retry must be built into the API client layer from the start. Rate awareness for batch operations can come in Phase 2.

---

### Pitfall 5: Markdown Export Fidelity Is Lossy

**What goes wrong:**
Google Drive's native markdown export (`text/markdown` MIME type) does not perfectly round-trip. Specific losses:
- **Images:** Exported as references/links but the actual image data is not inline. Agent sees `![image](...)` but the URL may be a temporary/internal URL.
- **Tables:** Complex table formatting (merged cells, colored cells) may simplify or lose structure.
- **Inline formatting edge cases:** Nested formatting (bold italic underline) may not all survive.
- **Comments and suggestions:** Not included in the markdown export. Must be fetched separately via the comments API.
- **Headers/footers:** Not included in the markdown body export.
- **Page breaks, footnotes:** May be lost or represented inconsistently.
- **Round-trip degradation:** `cat` (export as md) then `push` (import md) then `cat` again may produce different markdown than the first `cat`. Each cycle can lose formatting.

**Why it happens:**
Google Docs' internal representation is far richer than markdown. The export is necessarily a lossy projection. Developers assume "markdown export works" after a happy-path test with simple docs and don't test with richly-formatted production documents.

**How to avoid:**
- **Document the fidelity limitations explicitly.** Users and agents must know what survives the markdown round-trip and what does not.
- **Never use `push` on docs you didn't create from markdown.** If a doc was created in Google Docs UI with rich formatting, `cat` then `push` will destroy formatting that markdown cannot represent.
- **Test with production-quality docs** during development, not just simple test docs.
- For images: if the export includes image URLs, verify they are accessible outside the export context (they may be ephemeral).
- Consider offering `cat --html` as an alternative for richer export, since HTML preserves more formatting.

**Warning signs:**
- Users reporting "push destroyed my formatting"
- Image links in markdown output returning 404
- Tables rendering differently after a cat/push cycle
- Agents unable to "see" content that humans see in the doc (headers, footers, suggestions)

**Phase to address:**
Phase 1 (cat command) for initial awareness. Phase 2 for documentation and alternative export formats. The `push` command must warn about fidelity loss.

---

### Pitfall 6: Comment Anchoring Is Fragile and Read-Only

**What goes wrong:**
Comments in Google Docs are anchored to specific text ranges via `quotedFileContent.value`. Multiple issues:
1. **Anchoring is read-only via the API.** You can set `quotedFileContent` when creating a comment, but the anchor does not update when the document text changes. If the text moves or is edited, the comment may become orphaned or point to wrong text.
2. **`quotedFileContent.value` matching is imprecise.** If the same text appears multiple times in the doc, the comment may appear anchored to the wrong occurrence in the CLI's annotated view.
3. **Creating anchored comments via API is unreliable.** The `anchor` field in the comments API uses an opaque, undocumented internal format for precise positioning. `quotedFileContent` is the public API, but it only anchors by text content, not by position.
4. **Resolved comments disappear from default listing.** `includedDeleted=true` and `fields` parameter must be set correctly to see resolved comments.

**Why it happens:**
The Comments API was designed for the Google Docs UI, which has full document structure context. The API exposes a simplified view that doesn't carry all the positional information. Developers try to build features (like the annotated view) that require precise anchoring, which the API cannot guarantee.

**How to avoid:**
- For the annotated view (`cat --comments`): search for `quotedFileContent.value` in the markdown output. If found multiple times, anchor to the first occurrence (or use heuristics like proximity to previously anchored comments). Document that anchoring is best-effort.
- For creating anchored comments: use `quotedFileContent` with text that is unique in the document. Warn users if the anchor text appears multiple times.
- When listing comments, always request `fields="comments(id,content,author,resolved,quotedFileContent,replies,createdTime,modifiedTime)"` -- missing fields cause incomplete data.
- Handle the case where `quotedFileContent.value` text no longer exists in the document (the anchored text was edited/deleted). Show these as "orphaned" comments.

**Warning signs:**
- Comments appearing next to wrong paragraphs in annotated view
- Comments showing as "unanchored" when they should be anchored
- Resolved comments not appearing with `--all` flag
- Agent creating comments that don't appear anchored in the Google Docs UI

**Phase to address:**
Phase 2 (comments system). The basic comment CRUD can ship in Phase 1, but the annotated view with anchoring heuristics needs careful implementation in Phase 2.

---

### Pitfall 7: State File Race Conditions and Corruption

**What goes wrong:**
The per-doc state files (`~/.gdoc/state/{DOC_ID}.json`) can be corrupted or produce incorrect behavior:
1. **Concurrent CLI invocations:** If two CLI instances run simultaneously against the same doc (e.g., agent running in parallel), they both read the same state, both update it, and the last write wins. State file can contain stale data.
2. **Partial writes:** If the process crashes mid-write, the JSON file can be truncated/corrupted, causing all subsequent commands to fail with parse errors.
3. **Clock skew in `last_seen`:** If the system clock is wrong, time-based comparisons against `modifiedTime` from the API produce incorrect "changed since last interaction" results.
4. **State file accumulation:** Over time, state files accumulate for every doc ever accessed. No cleanup mechanism means `~/.gdoc/state/` grows unbounded.

**Why it happens:**
File-based state is simple to implement but has no built-in concurrency control. Developers test with sequential single-user access and never encounter race conditions.

**How to avoid:**
- Use atomic writes: write to a temp file, then `os.rename()` (which is atomic on POSIX). Never write directly to the state file.
- Add a JSON schema version to state files so future format changes can be migrated.
- Handle corrupted state files gracefully: if JSON parse fails, delete the state file and treat as first interaction. Log a warning but don't crash.
- For clock skew: use the `version` field (monotonically increasing integer) for change detection, not `modifiedTime`. The version number is a reliable indicator of changes regardless of clock accuracy.
- Consider a state pruning mechanism: delete state files not accessed in 30+ days.

**Warning signs:**
- Corrupted JSON in state files (truncated, invalid syntax)
- "No changes" banner when changes actually occurred
- Duplicate notifications (same change reported multiple times)
- `~/.gdoc/state/` directory growing to hundreds of files

**Phase to address:**
Phase 1 (state system). Atomic writes and corruption recovery from the start. Pruning can wait for a later phase.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| No retry/backoff on API calls | Simpler implementation | Intermittent failures under real usage, poor agent experience | Never -- must ship with retry |
| Storing all state in flat JSON files | Simple, no dependencies | Race conditions, no query capability, accumulation | MVP only. Acceptable if atomic writes are used |
| No scope checking on token refresh | Fewer auth edge cases to handle | Silent 403s when new features need new scopes | Never -- scope mismatch causes confusing errors |
| Hardcoded `fields` parameters in API calls | Quick implementation | Over-fetching wastes quota, under-fetching causes missing data bugs | MVP only. Optimize `fields` before shipping batch operations |
| No pagination on `comments.list` | Simple implementation | Missing comments on docs with 100+ comments (API pages at 20-100) | Never for comments listing. Must paginate. |
| Single-threaded awareness checks | Simpler code flow | 200ms overhead per command becomes 400ms if doing doc check + comment check sequentially | MVP. Parallelize awareness checks in Phase 2 |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Drive API `files.export` | Requesting `text/markdown` for non-Google Docs files (Sheets, Slides) | Check `mimeType` before export. Only Google Docs support markdown export. Sheets/Slides need different MIME types. |
| Drive API `files.list` | Not including `trashed=false` in the query | Always add `and trashed=false` to search queries, or trashed files appear in results |
| Drive API `files.list` | Using `files.list` with no `q` parameter to "list all files" | Without a query, this returns ALL files in the user's Drive including shared files, which can be thousands. Always scope with folder ID or query. |
| Drive API `files.update` (push) | Not setting `mimeType` in media upload metadata | Must set `mimeType='application/vnd.google-apps.document'` for markdown-to-Docs conversion. Without it, the file becomes a raw .md file in Drive, not a Google Doc. |
| Docs API `documents.get` | Requesting full document body when only metadata is needed | Use `fields` parameter to request only what you need. Full document body can be very large (megabytes for long docs). |
| Docs API `batchUpdate` | Assuming `batchUpdate` is transactional | If the 3rd request in a batch of 5 fails, requests 1 and 2 have already been applied and are NOT rolled back. The document is in a partially-modified state. |
| Comments API `replies.create` for resolve | Using `PATCH comments/{id}` to set resolved=true | The comments resource does not support setting `resolved` directly. Must create a reply with `action: "resolve"`. |
| OAuth2 `InstalledAppFlow` | Using `run_local_server()` in a headless/SSH environment | Detect if a browser is available. Fall back to `run_console()` for headless environments (copy/paste URL flow). Google deprecated `run_console()` -- may need OOB or device flow fallback. |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Awareness checks on every command | 200ms+ overhead per command; 429 errors during batch operations | Implement `--quiet` flag; cache awareness results for 30s; skip awareness for read-only commands in batch mode | At ~5+ commands per second against same doc |
| Full document export for every `cat` | Slow `cat` on large docs (10+ pages); high bandwidth usage | Cache exports with version-based invalidation; only re-export if version changed | Docs over 50 pages; rapid repeated reads |
| Unpaginated `files.list` results | Truncated file listings; missing search results | Always handle `nextPageToken`; set appropriate `pageSize` (max 1000 for files.list) | Folders with 100+ files |
| Comment listing without `fields` filter | Slow response; excessive data transfer; quota waste | Specify exact `fields` parameter: only request id, content, author.displayName, resolved, quotedFileContent, replies | Docs with 50+ comments with long reply threads |
| Sequential API calls where parallel is possible | 400ms for awareness (2 serial calls) instead of 200ms | Use `asyncio` or threading to parallelize `files.get` and `comments.list` awareness checks | At scale, doubles latency budget unnecessarily |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing `token.json` with world-readable permissions | Any local user/process can steal the OAuth token and access the user's Google Drive | Set file permissions to 600 (`owner read/write only`) on `token.json`. Check permissions on read. |
| Logging API responses that contain document content | Sensitive document content appears in log files, shell history, or error reports | Never log full API responses. Log only status codes and error messages. Redact document content in debug output. |
| Not validating doc IDs extracted from URLs | Malformed input could cause unexpected API behavior or error messages that leak info | Validate extracted IDs match expected format (`[a-zA-Z0-9_-]+`) before making API calls |
| Using `https://www.googleapis.com/auth/drive` (full Drive scope) | Grants full read/write access to entire Google Drive, far more than needed | Use `https://www.googleapis.com/auth/drive.file` (only files opened/created by app) if possible. However, this scope limits visibility to files the app created, which may not work for a general-purpose CLI. Document the scope choice and rationale. |
| Accepting arbitrary URLs as doc IDs without sanitization | Potential for SSRF-like issues if the CLI makes requests to attacker-controlled URLs | Only extract doc IDs from known Google Docs/Drive URL patterns. Reject URLs from other domains. |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| `replace` silently reports "0 occurrences" when text not found | Agent thinks edit succeeded but nothing changed; no error code distinguishes "replaced 0" from "replaced N" | Return exit code 0 for "replaced N>0", and a distinct exit code (e.g., 4) for "replaced 0" so agents can detect no-match programmatically |
| `insert` requiring character index from agent | Agents must calculate character offsets from markdown (which differs from doc internal indexes), leading to wrong positions | Provide helper: `gdoc index DOC_ID "text to find"` that returns the character index of a string. Or prefer `replace` over `insert` for agent workflows. |
| Awareness banner mixing with command output on stdout | Piping `gdoc cat DOC_ID` to a file captures the awareness banner along with the document content, corrupting the output | Print awareness banner to stderr, document content to stdout. This allows `gdoc cat DOC_ID > file.md` to work correctly while banner still shows in terminal. |
| No way to suppress first-interaction summary | Agent sees "3 open comments, 1 resolved" but can't act on it without a separate `comments` call | Include comment IDs and brief content in first-interaction summary, not just counts |
| Error messages only in English with technical API details | Non-technical users get "HttpError 403: insufficient permissions for this file" | Map common API errors to actionable messages: "You don't have access to this file. Ask the owner to share it with you." |

## "Looks Done But Isn't" Checklist

- [ ] **OAuth2 auth flow:** Often missing headless/SSH fallback -- verify auth works without a local browser (e.g., in Docker, over SSH, in CI)
- [ ] **Token refresh:** Often missing scope mismatch detection -- verify that adding a new scope triggers re-auth, not a silent 403
- [ ] **`files.list` pagination:** Often missing `nextPageToken` handling -- verify listing works with folders containing 200+ files
- [ ] **`comments.list` pagination:** Often missing pagination -- verify comments listing works on docs with 100+ comments
- [ ] **`batchUpdate` error handling:** Often missing partial failure handling -- verify that if request 3 of 5 fails, the CLI reports which operations succeeded and which failed
- [ ] **Markdown export:** Often missing image/table/formatting edge cases -- verify export with a richly formatted doc (images, tables, footnotes, headers)
- [ ] **`push` version guard:** Often missing race window -- verify that version check and upload are as close together as possible (no TOCTOU gap)
- [ ] **State file corruption:** Often missing atomic write -- verify CLI recovers gracefully from truncated/invalid state JSON
- [ ] **Rate limit handling:** Often missing backoff on 429 -- verify CLI retries with exponential backoff, not immediate retry
- [ ] **URL-to-ID extraction:** Often missing edge cases -- verify extraction works with `/edit`, `/edit#heading=...`, `/preview`, `/copy`, `/comment` URL suffixes
- [ ] **stderr vs stdout separation:** Often mixed -- verify `gdoc cat DOC_ID > file.md` produces clean markdown without banners or warnings in the file

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| batchUpdate index corruption (wrong positions) | MEDIUM | Undo is not available via API. Read the doc, manually identify the corruption, use `replace` operations to fix, or restore from version history in Google Docs UI. |
| `push` destroyed concurrent edits | HIGH | Check Google Docs version history (UI only -- not available via API). Restore the previous version manually. Implement version guard to prevent recurrence. |
| OAuth token expired (7-day testing limit) | LOW | Delete `token.json`, run `gdoc auth` again. Set app to "In Production" status to prevent recurrence. |
| State file corruption | LOW | Delete the corrupted state file. Next command treats it as first interaction. No data loss -- state is reconstructed from API. |
| Rate limit exceeded (429) | LOW | Wait and retry. Implement exponential backoff. For immediate relief, use `--quiet` to reduce API calls. |
| Comment anchored to wrong text | LOW | Delete the misanchored comment, create a new one with unique anchor text. No automated recovery -- comment anchoring is best-effort. |
| Markdown round-trip degradation | HIGH | Formatting lost via cat/push cycle cannot be recovered via API. Must restore from version history in Google Docs UI. Prevent by never using `push` on docs with rich formatting not created from markdown. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| batchUpdate index invalidation | Phase 1: Core edit commands | Unit test: multi-operation batch with reverse-order sorting; integration test with real doc |
| OAuth2 token refresh failures | Phase 1: Auth module | Test: set app to Testing, wait 8 days, verify re-auth prompt. Test: revoke token in Google Account, verify graceful re-auth |
| `push` destroys concurrent edits | Phase 1: Push command | Integration test: modify doc via UI, then `push` -- verify version guard blocks. Test `--force` override |
| Drive API rate limits | Phase 1: API client layer | Load test: 50 rapid sequential commands. Verify 429 handling with backoff. Verify `--quiet` reduces call count |
| Markdown export fidelity loss | Phase 1: Cat command (awareness), Phase 2: Documentation | Create a "test doc" with tables, images, footnotes. Verify export. Document known limitations. |
| Comment anchoring fragility | Phase 2: Comments + annotated view | Test: comment on text that appears multiple times. Verify anchoring heuristic. Test orphaned comments. |
| State file race conditions | Phase 1: State module | Test: concurrent CLI invocations against same doc. Verify atomic writes. Test corrupted state recovery. |
| Scope mismatch on token refresh | Phase 1: Auth module | Test: auth with scope A, then add scope B to code. Verify re-auth triggered, not 403. |
| `batchUpdate` partial failure | Phase 1: Core edit commands | Test: send batch where request N fails. Verify error message includes which operations succeeded. |
| Awareness banner on stdout | Phase 1: Output design | Test: `gdoc cat DOC_ID > file.md`. Verify file contains only markdown, no banners. |

## Sources

- Google Docs API official documentation (developers.google.com/docs/api) -- batchUpdate request ordering, document structure indexes
- Google Drive API v3 official documentation (developers.google.com/drive/api) -- rate limits, export MIME types, comments API
- Google OAuth2 documentation (developers.google.com/identity) -- InstalledAppFlow, token refresh, testing vs production app status
- Google Cloud Console documentation -- OAuth consent screen, publishing status
- Training data from Google API community discussions, Stack Overflow Q&A, and GitHub issues through May 2025

**Note:** All external research tools (WebSearch, WebFetch, Context7, Exa) were unavailable during this research session. All findings are based on training knowledge through May 2025. Confidence is MEDIUM overall -- findings are consistent with well-documented API behaviors but should be verified against current documentation before implementation, particularly:
- Current rate limit numbers (Google adjusts these periodically)
- Whether `run_console()` OOB flow is still available or fully deprecated
- Current markdown export fidelity (Google may have improved it since training cutoff)
- Whether the 7-day refresh token expiry for Testing apps is still enforced

---
*Pitfalls research for: Google Docs/Drive API CLI tool (gdoc)*
*Researched: 2026-02-07*
