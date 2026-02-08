# Phase 04 — Write Operations: Context & Decisions

## Incorporated from Codex Review

### #2 — `write` mimeType (QUESTION → resolved)
Codex was right. Must set `body={'mimeType': 'application/vnd.google-apps.document'}` in `files.update` metadata to trigger conversion, not just upload as `text/markdown`.

### #5 — Uniqueness pre-check (QUESTION → resolved with explicit trade-offs)
Kept text/plain export as a best-effort safety gate. See **Uniqueness Pre-Check Strategy** below for the full decision.

### #9 — `edit` output (SUGGEST → adopted)
Changed from `Replaced N` to `OK replaced N occurrence(s)` to match `gdoc.md` exactly. No old/new text echo in verbose.

### #10 — `write` output (SUGGEST → adopted)
Changed to `OK written` for terse mode.

### #12 — Post-write version (QUESTION → resolved)
Extended state handling — `write` gets version from `files.update` response (no extra call), `edit` needs one `get_file_version()` call. `update_state_after_command` will be extended to accept `command_version` for edit/write. **Clarification:** `command_version` updates `last_version` only (for banner tracking); `last_read_version` remains unchanged for edit/write (see Decision #4 below).

---

## Resolved Decisions

### 1. Case-Sensitivity Default: **case-insensitive** (LOCKED)

**Decision:** Default is case-insensitive. `--case-sensitive` is an opt-in flag.

**Rationale (three-way agreement):**
- **Spec** (`gdoc.md` line 261): `edit` "Supports `--case-sensitive`" — phrasing implies it's an opt-in override, not the default.
- **Docs API**: `replaceAllText` has `matchCase` which defaults to `false` (case-insensitive).
- **Codex** (item #6): "[AGREE] Default case-insensitive with `--case-sensitive` matches the parser and Docs API default."

No ambiguity remains. Removed from human-review queue.

### 2. `--quiet` and Write Conflict Block: **`--quiet` does NOT bypass safety** (LOCKED)

**Decision:** For `write`, `--quiet` skips the full 2-call pre-flight (matching the spec's "saves 2 API calls" claim) but adds back one lightweight `files.get(fields="version")` call specifically for conflict detection. The conflict block remains active. Only `--force` overrides the block.

**Spec reconciliation:**
- The spec (line 181) says `--quiet` "Skips the pre-flight check entirely — saves 2 API calls." The example uses `edit`, not `write`.
- For non-write commands (`cat`, `edit`, `info`, etc.), `--quiet` skips pre-flight entirely — full 2-call savings, exactly as the spec says.
- For `write`, the full pre-flight is still skipped (2 calls saved), but a separate minimal version check is added back (1 call). **Net savings for `write --quiet`: 1 API call**, not 2.
- This is a deliberate safety/efficiency tradeoff for `write` only. The spec's "saves 2 API calls" describes the general `--quiet` behavior and is accurate for all commands except `write`.

**Rationale:**
- The spec defines two distinct mechanisms with different purposes:
  - `--quiet` (lines 173-181): efficiency optimization — about reducing tokens and latency for batch operations.
  - `--force` (line 169): safety override — the explicit opt-in to bypass the write conflict block.
- If `--quiet` implicitly bypassed safety, `--force` would be meaningless when `--quiet` is used, collapsing two distinct concepts.
- The spec says (line 352): "`write` is destructive — Blocked when doc changed since last read unless `--force` is passed." No mention of `--quiet` as an alternative bypass.
- The version-check call is NOT the pre-flight — it's a separate, minimal call (`files.get(fields="version")`) that returns only the version number, not the full pre-flight metadata.

**Implementation:** When `--quiet` is set on `write`:
- Skip full pre-flight (both `files.get` with full fields and `comments.list`) — saves 2 API calls
- Skip banner output (saves tokens)
- **If `--force` is NOT set:** add back one lightweight `files.get(fields="version")` call — costs 1 API call. If version mismatch → block with `ERR:` just like normal mode. **Net: 1 API call saved** (2 skipped - 1 added back).
- **If `--force` IS set:** skip the version-check call entirely — no conflict detection needed. **Net: 2 API calls saved** (full savings, matching the spec exactly).

For non-write commands (`cat`, `edit`, `info`, etc.), `--quiet` skips pre-flight entirely as the spec says — **full 2-call savings**.

**Spec divergence note:** `gdoc.md` line 181 says `--quiet` "saves 2 API calls" unconditionally. Our implementation saves only 1 for `write` (without `--force`). This is an intentional, documented deviation — the spec's wording describes the general case and is accurate for all commands except `write` without `--force`. When `write --quiet --force` is used, the full 2-call savings is achieved (matching the spec). **Action:** When `gdoc.md` is next updated, add a parenthetical or footnote: _"For `write` without `--force`, `--quiet` still performs a lightweight version check (1 call) for conflict safety; net savings: 1 API call. With `--force`, full 2-call savings."_

### 3. Uniqueness Pre-Check Strategy: **best-effort gate + post-call reconciliation** (LOCKED)

**Problem:** The text/plain export can diverge from what the Docs API's `replaceAllText` sees. Known divergence sources:
- **Tables, smart quotes, and special formatting** may cause the plain-text occurrence count to differ from the API's match count.
- **Unicode case folding** (case-insensitive mode only): Python's `str.lower()` uses simple Unicode lowering, while the Docs API may use ICU-style case folding. These differ for locale-sensitive characters (e.g., Turkish İ/ı vs I/i, German ß vs SS). This is an additional divergence source for case-insensitive matching but is handled by the same post-call reconciliation — if the counts differ, the warning surfaces the mismatch.

**Decision:** Accept text/plain pre-check as a best-effort safety gate. Always reconcile with `occurrencesChanged` from the API response. Document the limitation explicitly.

**Flow without `--all`:**
1. Export doc as text/plain
2. Count occurrences (respecting `--case-sensitive`)
3. If count == 0 → `ERR: no match found` (exit 3, no API call)
4. If count > 1 → `ERR: multiple matches (N found). Use --all` (exit 3, no API call)
5. If count == 1 → call `replaceAllText`
6. Check `occurrencesChanged` in API response:
   - If 1 → success, `OK replaced 1 occurrence` (exit 0)
   - If 0 → `ERR: no match found` (exit 3) — pre-check was a false positive
   - If > 1 → `OK replaced N occurrence(s)` (exit 0) + `WARN: expected 1 match but API replaced N; text/plain export may differ from API matching` on stderr

**Flow with `--all`:**
1. Skip pre-check entirely
2. Call `replaceAllText`
3. If `occurrencesChanged == 0` → `ERR: no match found` (exit 3)
4. If `occurrencesChanged >= 1` → `OK replaced N occurrence(s)` (exit 0)

**Overlapping-count semantics:** Non-overlapping (LOCKED).
- The pre-check uses Python's `str.count()` (or `str.lower().count()` for case-insensitive), which counts **non-overlapping** occurrences (e.g., searching for `"aa"` in `"aaa"` returns 1, not 2).
- The Docs API's `replaceAllText` also performs **non-overlapping** replacement — it processes the document text sequentially and consumes matched text.
- Since both sides use non-overlapping semantics, overlapping-count mismatches are **not a divergence source**. The remaining divergence sources (tables, smart quotes, formatting) are already documented above and handled by post-call reconciliation.

**Why this is acceptable:**
- The pre-check blocks the most common error case (obvious duplicates) without an API call
- False negatives in pre-check (count=0 when API would match) are safe — they prevent accidental edits; user can investigate
- False positives in pre-check (count=1 when API matches more) are caught by post-call reconciliation with a warning
- The warning is on stderr (always visible, even in `--json` mode per existing convention)
- This is an **explicitly documented limitation**, not a hidden gotcha

### 4. State Update for edit/write: **`last_version` only, never `last_read_version`** (LOCKED)

**Decision:** When `update_state_after_command` receives a `command_version` for `edit` or `write`, it updates `last_version` only. `last_read_version` is **never** updated by edit/write commands, regardless of whether `command_version` is provided.

**Rationale:**
- `last_read_version` tracks the version the agent last *read* (via `cat` or `info`). It answers: "what content has the agent seen?" This is the basis for write conflict detection — if someone else edits between the agent's last read and a `write`, the version mismatch blocks the overwrite.
- `last_version` tracks the version the agent last *interacted with* (any command). It answers: "when did the agent last touch this doc?" This drives the "doc edited since last interaction" banner on the next command.
- If `edit`/`write` updated `last_read_version`, a subsequent `write` would not detect third-party changes that occurred between the agent's last `cat` and the edit — defeating the conflict detection mechanism.

**Implementation in `update_state_after_command`:**
- `command_version` for `edit`/`write` → `state.last_version = command_version` (prevents false "doc edited" banner)
- `state.last_read_version` → unchanged (only `cat`/`info` set this, via `is_read` guard)
- This matches the existing code pattern where `is_read = command in ("cat", "info")` gates `last_read_version` updates

### 5. Write Conflict When `last_read_version` Is Absent: **block unless `--force`** (LOCKED)

**Problem:** The write conflict block compares the current doc version against `last_read_version` from stored state. But `last_read_version` can be `None` in several scenarios: first-ever `write` on a doc (no prior `cat`/`info`), state file deleted or corrupted, or state exists but only from `edit` commands (which don't set `last_read_version` per Decision #4).

**Decision:** If `last_read_version` is `None` when `write` is invoked, **block** with the same conflict error and suggest `cat` first or `--force`. The agent must have read the doc before overwriting it.

**Rationale:**
- The spec says (line 352): "`write` is destructive — Blocked when doc changed since last read unless `--force` is passed." If there *is* no last read, the precondition is not met — the agent hasn't seen what it's about to overwrite.
- Allowing `write` without a baseline read version would silently skip conflict detection, defeating the safety mechanism.
- Requiring `cat` first is a trivial cost (one extra command) and establishes the conflict-detection baseline.
- `--force` remains the explicit override for agents that know what they're doing.

**Implementation:**
- In `cmd_write`, before proceeding: if `state is None` or `state.last_read_version is None` → `ERR: no read baseline. Run 'gdoc cat DOC_ID' first, or use --force to overwrite.` (exit 3)
- `--force` bypasses this check entirely (same as version-mismatch bypass)
- `--quiet` does NOT bypass this check (consistent with Decision #2 — `--quiet` never bypasses safety)

### 6. `write --quiet --force`: **skip version-check call entirely** (LOCKED)

**Problem:** Decision #2 adds a lightweight `files.get(fields="version")` call when `write --quiet` is used, specifically for conflict detection. But `--force` is defined as the explicit bypass for conflict detection (Decision #2 rationale, spec line 169/352). If both flags are present, the version-check call serves no purpose — its result would be ignored since `--force` suppresses the block regardless.

**Decision:** When `--force` is set, skip the version-check call. The `--quiet --force` combination on `write` achieves full 2-call savings, matching the spec's `--quiet` description exactly.

**Rationale:**
- `--force` means "I accept the risk of overwriting." Performing a version check only to ignore the result wastes an API call and adds latency — directly contradicting `--quiet`'s purpose of efficiency.
- This makes `--quiet`'s behavior consistent: it always skips the full pre-flight (2 calls saved). The only call added back is the version-check, and only when conflict detection is actually active (i.e., `--force` is not set).
- The spec's "saves 2 API calls" claim is fully accurate for `write --quiet --force`.

**Implementation in `cmd_write`:**
```
if quiet and not force:
    # lightweight version check (1 call)
    current_version = get_file_version(doc_id)
    if state.last_read_version != current_version:
        ERR → exit 3
elif quiet and force:
    # skip everything — full 2-call savings
    pass
elif not quiet:
    # normal pre-flight (2 calls) with banner
    ...
```

**Also applies to `last_read_version` absent check (Decision #5):** `--force` bypasses the "no read baseline" block as well, so when `--quiet --force` is set, neither the baseline check nor the version-check call is performed.

---

## Other Locked Decisions

- **Pre-check "no match" or "multiple matches" → exit code 3** (usage errors). Scope: this applies to the *pre-check* phase only (Decision #3, steps 3–4). Once the API call is made, the exit code is determined by `occurrencesChanged` from the API response — `--all` with matches ≥ 1 exits 0, and post-call reconciliation with unexpected count exits 0 + warning. Zero matches from the API also exits 3 regardless of `--all`.
- `--all` with zero API matches still errors (exit 3)
- Pre-check counting respects `--case-sensitive`
- Two sub-plans is a reasonable split (edit+write)

---

## Auto-Discuss Metadata

- **Rounds:** 3 + 3 + 1 (sprint fix rounds)
- **Codex Available:** yes
- **Uncertainties Resolution:** all 3 original items resolved + 3 follow-up items resolved + 4 Codex-feedback items resolved (exit-code scope clarification, quiet spec divergence tracking, absent last_read_version handling, Unicode case-folding divergence) + 2 sprint-fix items resolved (spec divergence severity for `write --quiet`, `write --quiet --force` gap)
- **Timestamp:** 2026-02-07T22:00:07Z (original), updated 2026-02-07

<details>
<summary>Codex Review (Round 2)</summary>

1. [AGREE] Docs API v1 `documents.batchUpdate` + `replaceAllText` is the correct API for `edit` per `gdoc.md`.
2. [QUESTION] `files.update` should set metadata `mimeType='application/vnd.google-apps.document'` (target) and upload media as `text/markdown`; otherwise you may just update a markdown file without conversion.
3. [AGREE] `get_docs_service()` alongside `get_drive_service()` with `@lru_cache` fits existing patterns.
4. [AGREE] A dedicated `gdoc/api/docs.py` wrapper mirrors `gdoc/api/drive.py` appropriately.
5. [QUESTION] Uniqueness via `export text/plain` can diverge from Docs API matching (tables, formatting, smart quotes). Consider counting via `documents.get` text runs or, at minimum, reconcile with `occurrencesChanged` and document limitations.
6. [AGREE] Default case-insensitive with `--case-sensitive` matches the parser and Docs API default.
7. [AGREE] Warn on `edit` conflict, block `write` unless `--force` aligns with `gdoc.md`.
8. [AGREE] Local file read with missing file as exit code 3 is consistent with usage errors.
9. [SUGGEST] Match the output contract in `gdoc.md`: terse `OK replaced N occurrence(s)`; avoid echoing old/new text in verbose to keep tokens low.
10. [SUGGEST] Consider terse `OK written` and keep extra details only in verbose/JSON for consistency.
11. [AGREE] `edit`/`write` should not update `last_read_version`.
12. [QUESTION] Post-edit/write `get_file_version` adds an API call and `update_state_after_command` doesn't use `command_version` for non-`info`. Either accept the "doc edited" banner next run, or extend state update and (for `write`) request `fields="version"` from `files.update` to avoid the extra call.
13. [AGREE] Two sub-plans is a reasonable split.

**Gaps** (all resolved)
- Ensure `edit` errors ("no match", "multiple matches") are treated as usage errors (exit 3) per `gdoc.md`. → **Resolved:** Decision #3 flow + Other Locked Decisions.
- For `--all`, still error if `occurrencesChanged == 0`; use `occurrencesChanged` for output even if you pre-check. → **Resolved:** Decision #3 `--all` flow.
- Uniqueness counting must respect `--case-sensitive` and avoid overlapping-count mismatches. → **Resolved:** Decision #3 overlapping-count semantics section.
- Decide explicitly what `--quiet` should do for `write` conflicts (it will otherwise bypass blocking). → **Resolved:** Decision #2.

</details>
