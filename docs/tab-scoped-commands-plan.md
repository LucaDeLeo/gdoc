# Tab-scoped commands: `comments`, `images`, `toc`, `diff`

Status: **PROPOSAL — none of this is implemented.** (Discussed 2026-07-04/05,
JP + Claude. "PR #4" below = the `?tab=` URL support that shipped in 0.22.0;
as of 0.22.0, `diff`/`comments`/`images` print a discard NOTE.) Companion to
`per-tab-read-baselines-plan.md`; follows PR #4 (URL `?tab=` support). This is the "R3"
workstream: the commands PR #4 left silently discarding `?tab=`, now given real tab
behavior instead of a discard NOTE.

## Evidence base

All designs below were validated live on 2026-07-05 against a real 7-tab work doc with 33
comments (read-only probes). Key findings, which several designs depend on:

- **The Drive markdown export includes ALL tabs**, each preceded by a `# **{tab title}**`
  heading line, in Docs-API tab order. (README's "export only returns the first tab" was
  stale; corrected in 0.22.0, so this is no longer a follow-up for this workstream.)
- **`get_tab_text` emits plain text**, not markdown (0 bold/link/heading markers on a doc
  full of them). Per-tab *markdown* only exists inside the whole-doc export.
- **`gdoc images` returns wrong answers today**: `list_inline_objects` uses
  `get_document` (no `includeTabsContent`), which returns only the first tab's body.
  Verified: 0 objects reported on a doc with 2 (in a later tab). `toc`'s whole-doc path
  has the same mechanism (`get_document_headings` → `get_document`, docs.py:744).
- **Comment-anchor bucketing distribution** (33 real comments vs 7 tabs, exact raw
  matching): 42% matched exactly one tab (all spot-checked correct), 24% matched multiple
  tabs, 21% anchors under 4 chars, 12% stale (text edited/deleted). The multi-tab matches
  were systematic — draft tabs duplicating the canonical tab — so ambiguity is a routine
  case, not an edge case. A normalization layer (typography fold + whitespace collapse)
  changed **nothing**: quote and tab text share a representation. Table cells are fully
  present in `get_tab_text` (163/163).

## Shared conventions (from PR #4)

- URL `?tab=` acts as `--tab`; explicit flags win; `?tab=t.0` is ambient noise (= no tab).
- Whole-doc behavior stays the default; a tab only ever *restricts*.
- Tab identity: resolve via `resolve_tab` (title-first-then-ID); operate on resolved IDs.
- These commands honoring the tab **supersedes** the discard-NOTE idea for them.

---

## 1. `comments` — tab-scoped listing via anchor-text bucketing

Comments come from the Drive API, which is file-generic and carries **no tab
information**. The only positional breadcrumb is `quotedFileContent` — a plain-text
snapshot of the anchored passage at comment-creation time. Tab scoping is therefore a
heuristic: match each quote against each tab's `get_tab_text`.

### Core principle: partition, never filter

A filter ("show comments matching tab X") silently drops everything unmatchable — and
anchors go stale precisely when the doc is being actively edited, i.e. exactly when
comments are being triaged. Instead, every comment lands in one epistemic bucket, and a
tab-scoped view omits **only comments positively matched to a different tab**. Uncertainty
fails open: shown, labeled.

Buckets, relative to requested tab X:

1. **Quote found only in tab X** → show, anchored.
2. **Quote found in X and other tabs** → show, tagged `[also matches: <titles>]`.
   (Routine, not rare — 24% in the live data, driven by draft-tab duplication.)
3. **Quote found only in other tab(s)** → omit; count in a footer:
   `(N comments on other tabs not shown; use 'gdoc comments DOC' for all)`.
4. **Unplaceable** — no quote, quote < 4 chars, or quote found in no tab → show under
   `[UNPLACED — may be on any tab]` with the reason, reusing annotate.py's note vocabulary
   (`anchor too short`, `anchor deleted`). 33% of the live data; the footer/labels keep
   "tab X has N comments" from ever being silently wrong.

Matching is **raw substring** (`quote in tab_text`) with the <4-char guard. No
normalization layer — verified unnecessary (plain-vs-plain text already agrees on smart
quotes, NBSP, emoji). Within-tab multiplicity is irrelevant: a quote appearing 3× inside
tab X is still unambiguously X's (unlike annotate.py's line-pinning, which needs
uniqueness).

### Shape

- **Engine:** pure function `bucket_comments_by_tab(tabs, comments)` in a new module (or
  alongside annotate.py) — no API calls inside; unit-testable with fixture dicts.
- **CLI:** `gdoc comments '<url>?tab=X'` and `gdoc comments DOC --tab X`. No tab → today's
  whole-doc listing, byte-identical (no extra API call). With a tab → one extra Docs API
  fetch for tab texts.
- **JSON:** each included comment gains `placement: "matched" | "multi_tab" | "unplaced"`
  and `matched_tabs: [...]`; top-level `omitted_other_tabs: N`.
- **Terse output sketch:**

  ```text
  # tab: Budget (t.abc123)
  #AAABxyz [open] someone@example.com 2026-06-30
    "Can we cut this?"
    on "Our approach to..."
  #AAABdef [open] other@example.com 2026-07-01 [also matches: Drafts]
    ...
        [UNPLACED — may be on any tab]
  #AAABghi [open] ... [anchor deleted]
    ...
  (5 comments on other tabs not shown)
  ```

### Tests

Pure-function tests for all four buckets, the <4-char guard, within-tab multiplicity
(still bucket 1), multi-tab quotes, no-quote comments; CLI tests for URL-tab and `--tab`
paths, footer counts, JSON placement fields, `--all` orthogonality, and the no-tab path
being unchanged.

~250 LOC incl. tests; ~half a day.

---

## 2. `images` (and `toc`'s whole-doc default) — mechanical fix + scoping

No heuristics needed: objects and headings are structurally located per tab.

- **Bug fix (default, whole doc):** `list_inline_objects` walks **all tabs**; each result
  row gains the containing tab (id + title). `get_document_headings`'s whole-doc path
  likewise walks all tabs (group headings by tab in `toc` output). This changes existing
  output on multi-tab docs — that's the point; today's output is wrong (missing content).
- **Scoping (feature):** `images '<url>?tab=X'` / `--tab X` walks only tab X's body.
  `toc`'s `--tab`/URL path already works (PR #4).
- **Plumbing:** `get_document_tabs`/`flatten_tabs` currently keep only id/title/body per
  tab; retain the per-tab `inlineObjects`/`positionedObjects` maps (the
  `includeTabsContent=true` response provides them per `documentTab`).

Tests: multi-tab fixture with objects in a non-first tab (the live bug); per-tab
filtering; positioned + inline objects; toc grouping. ~Half a day for both commands.

---

## 3. `diff` — tab scoping by slicing exports at tab-title headings

Historical content only exists as whole-doc revision exports (there is **no** per-tab
revision API), so the only route to tab-scoped revision diffs is slicing the export. The
same mechanism serves all three modes, and beats `get_tab_text` for the current side too
(markdown vs. markdown against pulled files, instead of plain text vs. markdown):

- `diff '<url>?tab=X' FILE` → slice the current export; diff tab X's slice vs. FILE.
- `diff '<url>?tab=X' --rev A..B` (and `--since`) → slice both revision exports; diff the
  two X-slices.

### Slicing algorithm

Resolve X → current title via the Docs API tab list. Scan the export **in tab order**:
find each tab's `# **{title}**` heading line sequentially (each search starting after the
previous boundary); tab X's slice runs from its heading to the next tab's heading (or
EOF). Ordered scanning is what contains the main threat — a user-authored heading
identical to a *later* tab's title. It does not *eliminate* it: a `# **B**` a user wrote
inside tab A is found before the real tab-B heading and truncates A's slice early, in
order, so the out-of-order check below can't see it. On the current side the oracle
catches this; on a revision there is no oracle, which is the residual risk the rejection
rules must be sized against — treat any tab whose slice comes back shorter than its
neighbours' heading spacing suggests as ambiguous, and refuse.

### Failure semantics — the opposite of comments: fail LOUD, never open

A comments listing that includes too much is safe; a diff computed on the wrong slice is a
silent wrong answer. Exit 3 (never a fallback diff) when:

- two tabs share a title ("tab title ambiguous; rename or diff the whole doc");
- a tab's heading isn't found in a revision export (tab renamed since, or didn't exist at
  that revision — name the revision in the error);
- boundaries come back out of order / overlapping;
- **oracle check fails** (current doc only): normalized slice text should roughly agree
  with normalized `get_tab_text` — we have both in hand. Mismatch means the slicer
  misfired (or Google changed the heading format); refuse rather than emit a
  plausible-looking wrong diff. Revisions have no oracle; the current-side check is the
  canary for format drift.

Ship revision slicing in v1 (not a "not supported" stub): the boundary format verified
clean on real data, and loud-fail bounds the worst case at an error message.

Tests: slicer unit tests (fixture exports: clean case, duplicate-title error, missing-tab
error, user heading colliding with a later tab title, out-of-order detection, oracle
mismatch); mode wiring tests for FILE / `--rev` / `--since`; `?tab=t.0` stays whole-doc.
~A day incl. tests.

---

## Future unlocks (file, don't build)

- **High-fidelity `cat --tab`:** the same slicer would give per-tab *markdown* (links,
  bold, tables) instead of `get_tab_text`'s plain-text subset — the exact limitation
  PR #4's `t.0` rationale worked around. Natural sequel once the slicer proves itself in
  `diff`.
- **`cat --comments --tab X`:** annotate tab content with bucket-1/2 comments (turns
  PR #4's `cat --comments <tab-url>` error into real behavior). Second customer for
  `bucket_comments_by_tab`.
- **Heuristic upgrade for annotate.py:** whole-doc `cat --comments` could use tab
  bucketing to shrink its `anchor ambiguous` pile (9/33 in live data) by resolving
  cross-tab duplicates to per-tab line matches.

## Sequencing within this workstream

1. `images`/`toc` fixes (mechanical, wrong answers today, no design risk),
2. `comments` bucketing (validated, pure-function core),
3. `diff` slicing (heuristic, loud-fail, benefits from the others' tab plumbing).

Each is independently shippable. (README's stale export claim was the one cross-cutting
follow-up here; it was corrected in 0.22.0 and needs no further work.)
