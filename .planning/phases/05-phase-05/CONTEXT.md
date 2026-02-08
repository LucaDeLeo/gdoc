CONTEXT.md is written. Here's a summary of how Codex feedback was incorporated:

**Accepted corrections (3):**
- **`cat --comments` uses markdown**, not plain text — the spec says "finds that substring in the markdown" and this aligns with `cat`'s default behavior
- **Default `comments` output is multi-line** per the spec example (`#1 [open] author date` + indented content/replies), not tab-separated terse
- **State field names** fixed: `last_comment_check` and `known_comment_ids`/`known_resolved_ids` (not `last_comment_time`)

**Incorporated suggestion (1):**
- **`include_resolved` parameter at API level** — `list_comments` gains `include_resolved` parameter. Pre-flight always passes `True`. The `comments` command makes its own full API call (see Decision: No Pre-flight Data Reuse for `comments` Command below).

**Resolved gaps (2):**
- **`quotedFileContent` field** — added `include_anchor` parameter to `list_comments` so only `cat --comments` requests the extra field
- **Mutation output format** — codified per spec: `OK resolved comment #1`, `OK comment #NEW_ID`, etc.

**Kept as-is (items marked [AGREE]):** Comment CRUD location, resolve/reopen via reply action, separate handler functions, `--json` support, two-plan split.

**Resolved decisions (4 — previously open):**

### Decision: Anchor → Line Mapping Strategy

**Problem:** `cat --comments` must place comment annotations next to the line they reference. The spec says to search `quotedFileContent.value` in the markdown output and annotate the line containing that substring. First-occurrence search can mis-place annotations when the quoted text appears multiple times.

**Decision:** Use first-occurrence search with ambiguity detection and fallback.

1. Search the markdown for `quotedFileContent.value`.
2. If exactly one match → annotate after that line. This is the happy path and covers the vast majority of real comments.
3. If multiple matches → treat as **unanchored** with marker `[anchor ambiguous]` and place at bottom with other unanchored comments. This avoids silent mis-placement.
4. If zero matches (anchor text was edited away) → treat as **unanchored** with marker `[anchor deleted]`.

**Rationale:** The Drive comments API does not expose Docs API document offsets, so precise positional mapping is impossible. First-occurrence with fallback is simple, predictable, and fails visibly rather than silently. The `annotate.py` module owns this logic and can be improved later without changing the CLI surface.

**Spec relationship — additive gap-filling, not contradictory divergence.** The spec (`gdoc.md:65-67`) says "finds that substring in the markdown and places the annotation after the line containing it." Phase 05's rules handle cases the spec does not address (what happens when the simple algorithm fails). No rule contradicts the spec's described behavior — they extend it to cover edge cases:

| Rule | Spec says | Phase 05 adds | Relationship |
|------|-----------|---------------|-------------|
| Single match → annotate after line | Described at `gdoc.md:65` | (no change) | Identical |
| Multiple matches → `[anchor ambiguous]`, unanchored | Not addressed | Prevents silent mis-placement | Additive |
| Zero matches → `[anchor deleted]`, unanchored | Not addressed | Handles edited-away anchors | Additive |
| Short anchor (<4 chars) → `[anchor too short]`, unanchored | Not addressed | Prevents false-positive substring matches | Additive |
| Multi-line anchor → annotate after last line of span | Not addressed | Natural extension of single-line rule | Additive |

Phase 05 tests validate against these CONTEXT.md rules. There are no spec-contradictory behaviors to reconcile — the spec describes the happy path, and these rules fill gaps for error/edge cases that the spec is silent on.

**Edge case: very short anchor text.** If `quotedFileContent.value` is fewer than 4 characters, treat as unanchored (`[anchor too short]`) to avoid false positives from substring matching.

**Edge case: multi-line anchor text.** `quotedFileContent.value` can span multiple markdown lines (e.g., a selection covering a paragraph break). The search operates on the full markdown string (not line-by-line), so a multi-line anchor can still match. When matched, annotate after the **last line** of the match span — this places the annotation immediately below the highlighted region, which is where a human would expect to see it. If the multi-line anchor has multiple matches or zero matches, the same ambiguity/deleted fallback rules apply. Implementation: `annotate.py` uses `str.find()` on the full markdown text, then counts newlines up to match-end to determine the insertion line.

### Decision: No Pre-flight Data Reuse for `comments` Command

**Problem:** On subsequent runs, `pre_flight()` calls `list_comments(doc_id, start_modified_time=state.last_comment_check)`, which returns only comments **modified after** `last_comment_check` — not the full comment list. This incremental fetch is correct for change detection (identifying new comments, new replies, resolves, reopens) but **cannot** be reused for the `comments` command's display output, which must show all comments on the document.

An earlier draft proposed adding an `all_comments` field to `ChangeInfo` and reusing pre-flight data. This was incorrect: on subsequent runs the pre-flight comment list omits unchanged comments and resolved comments not touched since the last check, producing incomplete output.

**Decision:** The `comments` command always makes its own `list_comments` call with `start_modified_time=""` (full fetch). Pre-flight data is used only for the notification banner and state updates — never for command display output.

- **Non-quiet mode:** Pre-flight runs its incremental fetch for change detection (banner + state). The `comments` handler then makes a separate full `list_comments` call with `include_resolved` set based on `--all`. **Two comment API calls total** (one incremental for pre-flight, one full for display). The incremental call is cheap (typically few or zero results) and serves a different purpose.
- **Quiet mode (`--quiet`):** Pre-flight is skipped. The `comments` handler makes its own full `list_comments` call with `include_resolved` set based on `--all`. **One API call total.**
- **`cat --comments`:** Always makes its own full `list_comments` call with `include_anchor=True` to get `quotedFileContent`. Same rationale as above, plus the anchor fields are not requested by pre-flight. `cat --comments` respects `--all` for resolved filtering (see below).

**Implementation:** No changes to `ChangeInfo` — no `all_comments` field needed. The `comments` handler imports and calls `list_comments` directly. Pre-flight remains command-agnostic.

**Rationale for accepting the extra API call:** Pre-flight's incremental fetch and the `comments` command's full fetch serve fundamentally different purposes (change detection vs. display). Coupling them would require either (a) making pre-flight always fetch all comments (wasteful for every other command) or (b) making pre-flight command-aware (breaks its clean separation). One extra API call for `comments` is the correct tradeoff — it's the same pattern `cat --comments` already uses.

### Decision: `cat --comments` Resolved Filtering

**Problem:** `cat --comments` makes its own `list_comments` call, but the spec doesn't specify how it handles resolved comments. If `include_resolved` defaults to `True`, the annotated view will show resolved comment annotations inline even though the default CLI behavior elsewhere hides resolved comments.

**Decision:** `cat --comments` follows the same resolved-filtering convention as the `comments` command:
- **Default (no `--all`):** `list_comments` is called with `include_resolved=False`. Only open comments are annotated inline and shown in the unanchored section. This matches user expectations — resolved comments are "done" and shouldn't clutter the annotated view.
- **With `--all`:** `list_comments` is called with `include_resolved=True`. Resolved comments are annotated with `[#ID resolved]` instead of `[#ID open]` to visually distinguish them.

This reuses the existing `--all` flag from the `comments` subcommand (already in the parser). The `cat` subparser adds `--comments` as a flag; when `--comments` is active, `--all` controls resolved inclusion.

### Decision: State Updates for Comment Mutations

**Problem:** Comment mutations (`comment`, `reply`, `resolve`, `reopen`) create state that pre-flight cannot know about — pre-flight runs *before* the mutation. In non-quiet mode, `update_state_after_command()` writes ID sets from pre-flight's merged result, which doesn't include the just-performed mutation. In quiet mode, pre-flight is skipped entirely and ID sets aren't updated at all. Both paths leave `known_comment_ids`/`known_resolved_ids` stale with respect to the current mutation.

**Important: how pre-flight builds ID sets (existing code in `notify.py`).** On subsequent runs, pre-flight fetches comments incrementally (`start_modified_time=state.last_comment_check`), but it does **not** use the raw incremental list as the new ID sets. Instead it performs a **merge/union**: it starts from `state.known_comment_ids` / `state.known_resolved_ids` and then adds/updates from the incremental results. Specifically (notify.py lines 153-165):
1. `all_ids = set(state.known_comment_ids)` — seed from existing state.
2. For each comment in the incremental fetch: add its ID to `all_ids`; add to `all_resolved` if resolved, discard from `all_resolved` if reopened.
3. `change_info.all_comment_ids = sorted(all_ids)` — the merged result.

Then `update_state_after_command()` replaces `state.known_comment_ids` with `change_info.all_comment_ids` — this is a field-level replacement, but the **value** is already the merged superset, not the raw incremental data. Unchanged comments from prior state are preserved.

**Limitation (accepted):** Deleted comments are never pruned from the ID sets because the incremental fetch doesn't return deleted comments and the merge is union-only. This is acceptable: comment IDs are unique (not reused by Google), and the sets are only used for new-comment detection. A stale deleted-comment ID in `known_comment_ids` has no observable effect — it simply prevents a phantom "new comment" notification if a comment with the same ID were ever to reappear, which cannot happen.

**Decision:** Comment mutation handlers always perform targeted state patches after a successful API call, in **both** quiet and non-quiet modes. No extra API calls are needed — the mutation response provides the necessary data.

| Command   | State patch                                                        |
|-----------|--------------------------------------------------------------------|
| `comment` | Append new comment ID to `known_comment_ids`                       |
| `resolve` | Add comment ID to `known_resolved_ids`                             |
| `reopen`  | Remove comment ID from `known_resolved_ids`                        |
| `reply`   | No ID set changes (replies don't have top-level tracking)          |

Rules:
- `last_comment_check` does **not** advance in quiet mode (no full comment list was fetched, so advancing the timestamp could cause missed changes on next non-quiet run).
- `last_seen` always updates (already the case for all commands).
- These patches are applied in `update_state_after_command()` via a new `comment_state_patch` parameter (dict with keys `add_comment_id`, `add_resolved_id`, `remove_resolved_id`), keeping the function signature extensible.

**Application order in non-quiet mode:**
1. Pre-flight merged IDs (existing state ∪ incremental fetch) are written to state (captures all external changes since last interaction while preserving unchanged comment IDs).
2. The targeted `comment_state_patch` is applied **on top** of the merged result (captures the just-performed mutation).

This two-step approach ensures both external changes and the current mutation are reflected in state. Without step 2, a `gdoc comment DOC "text"` in non-quiet mode would save state that doesn't include the just-created comment, causing a spurious "new comment" notification on the next run.

**Quiet mode:** Only step 2 applies (no pre-flight data). `last_comment_check` is not advanced.

### Test Plan: Phase 05 Coverage Requirements

**Comment CRUD output tests (unit, mocked API):**
- `comment` → stdout: `OK comment #NEW_ID`, exit 0; `--json` → `{"id": "...", "status": "created"}`
- `reply` → stdout: `OK reply on #COMMENT_ID`, exit 0; `--json` → `{"commentId": "...", "replyId": "...", "status": "created"}`
- `resolve` → stdout: `OK resolved comment #ID`, exit 0; `--json` → `{"id": "...", "status": "resolved"}`
- `reopen` → stdout: `OK reopened comment #ID`, exit 0; `--json` → `{"id": "...", "status": "reopened"}`
- Error cases: non-existent comment ID → `ERR:` on stderr, exit 1; auth expired → exit 2

**Resolved filtering tests:**
- `comments DOC` (no `--all`, non-quiet) → calls `list_comments(include_resolved=False)` with full fetch (no `start_modified_time`), separate from pre-flight
- `comments DOC --all` (non-quiet) → calls `list_comments(include_resolved=True)` with full fetch, separate from pre-flight
- `comments DOC --quiet` (no `--all`) → calls `list_comments(include_resolved=False)` directly, no pre-flight
- `comments DOC --quiet --all` → calls `list_comments(include_resolved=True)` directly, no pre-flight
- Pre-flight always uses `includeResolved=True` with incremental `start_modified_time` (unchanged)

**Anchor mapping edge cases (`annotate.py`, unit tests — no API mocking needed):**
- Single match: annotation placed after correct line
- Multiple matches: comment treated as unanchored with `[anchor ambiguous]`
- Zero matches (deleted anchor): comment treated as unanchored with `[anchor deleted]`
- Short anchor (<4 chars): comment treated as unanchored with `[anchor too short]`
- Multi-line anchor (spans 2+ lines): annotation placed after last line of match span
- Multi-line anchor with multiple matches: treated as unanchored with `[anchor ambiguous]`
- Unanchored comment (no `quotedFileContent`): placed in bottom unanchored section
- Mixed: doc with both anchored and unanchored comments renders correctly
- Empty document with comments: all comments in unanchored section

**`cat --comments` resolved filtering tests:**
- `cat DOC --comments` (no `--all`) → calls `list_comments(include_resolved=False, include_anchor=True)`, only open comments annotated
- `cat DOC --comments --all` → calls `list_comments(include_resolved=True, include_anchor=True)`, resolved shown with `[#ID resolved]`

**State update tests (mutation commands):**
- `comment` in `--quiet` → `known_comment_ids` gains new ID, `last_comment_check` unchanged
- `resolve` in `--quiet` → `known_resolved_ids` gains ID, `last_comment_check` unchanged
- `reopen` in `--quiet` → `known_resolved_ids` loses ID, `last_comment_check` unchanged
- `reply` in `--quiet` → no ID set changes
- `comment` in non-quiet → pre-flight merged IDs (existing state ∪ incremental) written, THEN patch adds new comment ID
- `resolve` in non-quiet → pre-flight merged IDs written, THEN patch adds resolved ID
- `reopen` in non-quiet → pre-flight merged IDs written, THEN patch removes resolved ID

---

## Auto-Discuss Metadata

- **Rounds:** 3
- **Codex Available:** yes
- **Uncertainties Resolution:** none
- **Timestamp:** 2026-02-07T22:44:04Z

<details>
<summary>Codex Review (Round 2)</summary>

**Findings**
- High: `cat --comments` is specified to annotate **markdown** and to locate anchors by searching `quotedFileContent.value` in the markdown; the proposal switches to plain text and a different annotation layout. This diverges from the spec and will shift line numbers/anchors. See `gdoc.md:40` and `gdoc.md:65`.
- Medium: The proposed default `comments` output (tab‑separated snippet) conflicts with the documented default multi‑line output that includes full comment text and replies. See `gdoc.md:195`.
- Medium: `cat --comments` needs `quotedFileContent` (and/or `anchor`) from the comments API, but `list_comments` currently does not request those fields. See `gdoc/api/comments.py:43`.

**Decision Review**
1. [AGREE] Co-locating comment CRUD in `gdoc/api/comments.py` matches current structure and error translation. Ensure new calls use `_translate_http_error` and tight `fields` to keep payloads small.
2. [AGREE] Resolve/reopen via reply `action` matches the documented API constraint.
3. [SUGGEST] Keep `list_comments` defaulting to `includeResolved=True` for pre‑flight, but let `comments` command set `includeResolved=False` at the API level when `--all` is not set to reduce payloads.
4. [QUESTION] The annotation format and plain‑text base don’t match the spec. The spec’s example uses markdown and `[#{id} open]` blocks. See `gdoc.md:46`.
5. [QUESTION] Searching `quotedFileContent` in **plain text** conflicts with the spec’s “search in markdown.” See `gdoc.md:65`. If you change this, update the spec and be explicit about the tradeoff.
6. [AGREE] Separate handlers per command align with existing CLI patterns; swap the current `cmd_stub` defaults.
7. [QUESTION] The proposed terse format diverges from the documented default output. If you want a terse single‑line view, either update the spec or introduce a new flag; default should match `gdoc.md:195`.
8. [AGREE] `--comments` should still honor `--json`; your plan to wrap annotated content + structured comments is reasonable.
9. [QUESTION] State tracking uses `last_comment_check` and comment ID sets, not `last_comment_time`. See `gdoc/state.py:18`. Clarify how `--quiet` comment mutations should update state (today, quiet mode skips comment set updates).
10. [AGREE] Two‑wave split is sensible; `cat --comments` is the riskier piece.

**Gaps**
- Add `quotedFileContent` (and potentially `anchor`) to the `comments.list` fields if `cat --comments` will map anchors, or make a dedicated call for annotated view. See `gdoc/api/comments.py:43`.
- Decide and document outputs for `comment`, `reply`, `resolve`, `reopen` (IDs, resolved status, JSON shape). The spec shows `OK resolved comment #1`. See `gdoc.md:218`.
- Tests: no test plan for comment CRUD responses, resolved filtering, or anchor mapping edge cases (duplicate quoted text, missing anchor).

**Open Questions**
- Do you want to adhere to the current markdown‑based annotated view spec, or intentionally switch to plain text and update `gdoc.md`?

</details>
