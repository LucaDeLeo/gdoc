# Per-tab read baselines for write-conflict detection

Status: **shipped in 0.22.0** (planned 2026-07-04/05 as 0.14.0, JP + Claude; renumbered
after merging main, which had reused 0.13-0.21). Builds on the URL `?tab=` support in
the same release. Kept as a design record — see CHANGELOG 0.22.0 for what shipped.

> **Premise correction (2026-07-05, verified live):** the Drive markdown export includes
> **all tabs**, not just the first — confirmed empirically against a real 7-tab doc (unique
> tokens from every tab present in the export; tab titles inserted as headings). README's
> "Drive export only returns the first tab" (Working-with-tabs section) is stale and needs
> fixing separately. This changed the check rule below from *strict* to *lenient*:
> plain `cat`/`pull` are genuine full-document reads, so the global baseline they advance
> legitimately covers every tab.

## Problem

The write-conflict guard tracks one `last_read_version` per **document**, but tab-scoped
reads and writes are per-**tab**. On multi-tab docs this produces both false negatives and
false positives:

1. **False negative — replace a tab you've never seen.** `write --tab B` checks the
   doc-global baseline, which is satisfied by any prior read — including `cat --tab A` or
   a pasted `?tab=A` URL, which showed you nothing of tab B. "Read tab A → `write --tab B`"
   replaces all of tab B with no warning. Pre-existing; PR #4 widened the on-ramp (pasted
   URLs now route into tab reads).

2. **False positive — can't write the same tab twice.** `cat --tab X` (baseline → v100) →
   `write --tab X` (doc → v101; tab writes deliberately don't advance the baseline,
   state.py) → second `write --tab X` → "doc changed since last read", exit 3. The agent's
   own write strands it; the workaround is re-`cat` or `--force`, and habituating agents to
   `--force` defeats the guard.

3. **R2 from PR #4's review** — a tab-scoped `cat` advances the whole-doc baseline. Less
   severe than first assessed: whole-doc `write`/`push` on multi-tab docs are already
   blocked by the collapse guard (`count_document_tabs > 1` → exit 3) unless
   `--force-collapse-tabs`, so the baseline was never the last line of defense for sibling
   tabs. Fixing 1–2 fixes R2 as a byproduct.

A naive R2 fix (tab reads stop advancing the baseline) is **not** viable: it would break
the core tab workflow (`cat --tab X` → `write --tab X` would hit "no read baseline").
Per-tab state is the minimal correct fix.

## Spec

### State schema (`gdoc/state.py`)

```python
@dataclass
class DocState:
    ...
    tab_read_versions: dict[str, int] = field(default_factory=dict)
    # tab_id -> doc version at last read of that tab's full content
```

- Keys are **resolved tab IDs** (`resolve_tab(...)["id"]`), never user-typed titles, so
  `--tab "Notes"` and `?tab=t.abc` share an entry and renames don't fragment state.
- Values are the doc-global Drive `version` (already int-coerced at the API boundary,
  `api/drive.py`), so int comparison is safe.
- Compat: old state files load with an empty dict (`default_factory`); old gdoc versions
  ignore the new key (`load_state` filters to known fields). No migration step.

### Who advances what

| Action | `last_read_version` | `tab_read_versions` |
|---|---|---|
| `cat` (plain, Drive export — includes ALL tabs) / `pull` | advance (correct: full read) | — |
| `cat --tab X` / `cat '<url>?tab=X'` | **no longer advanced** — except on a single-tab doc, where the one tab IS the whole doc (advance) | `[X] = current` |
| `cat --all-tabs` | advance | — (subsumed by global under the lenient rule) |
| `cat --revision`, `pull --revision`, `toc`, `info` | unchanged (status quo) | — |
| `write` / `push` (full doc, success) | `= post-write version` (status quo) | — |
| `write --tab X` (success, full-tab replace) | — | `[X] = post-write version` |
| `insert --tab X` | — | — (append ≠ read; the rest of the tab is unseen) |
| `edit` (whole doc or `--tab`) | unchanged | — |

Quiet reads (`--quiet`) skip pre-flight and have no version in hand; they don't stamp
anything — same as today's quiet behavior for `last_read_version`.

### Conflict check rules

- **Whole-doc `write`/`push`:** unchanged — check `last_read_version` (plus the existing
  `_doc_matches` in-sync rescue and the collapse guard).
- **`write --tab X`:** check the **effective tab baseline** (lenient rule):
  - Resolve X → `tab_id` first. Effective baseline =
    `max(last_read_version, tab_read_versions.get(tab_id))` (None-aware; versions are
    monotonic ints).
  - The global component is legitimate because plain `cat`/`pull` genuinely read every
    tab (see premise correction). The false-negative fix comes from tab reads no longer
    advancing the global baseline: after `cat --tab A`, tab B has no baseline at all.
  - No effective baseline → exit 3: `no read baseline for tab 'X'. Run 'gdoc cat --tab X'
    (or 'gdoc cat DOC' for the whole doc) first, or use --force to overwrite.`
  - Baseline set but `current_version != baseline` → exit 3: `tab 'X' may have changed
    since last read (doc moved v{base} -> v{cur}). Run 'gdoc cat --tab X' first, or use
    --force to overwrite.` ("may" is honest — the version is doc-global; see Limits.)
  - `--force` bypasses, as today.

### Known limits (shipped as-is)

- **Sibling-edit false positives remain.** Drive versions are doc-global, so an edit to
  tab B still conflicts a `write --tab X` whose baseline predates it. Same conservatism as
  today — no regression, and the post-write self-advancement removes the most common case
  (our own sequential writes). If real-world friction shows up, layer 2 is a per-tab
  content hash stamped at read time and re-checked (one extra Docs fetch) only on the
  conflict path. Deferred deliberately.
- **`edit`/`insert` strand baselines.** Any version-bumping mutation that isn't a full-tab
  replace leaves other baselines behind; the next tab write may false-conflict. Same class
  of conservatism as today (`edit` → `push` already behaves this way, rescued only by
  `_doc_matches`).
- **Truncated reads still count.** `cat --max-bytes` advances baselines despite showing a
  prefix. Pre-existing looseness; out of scope.
- `writeControl.requiredRevisionId` (already sent by `insert_markdown_into_tab`) continues
  to guard the fetch→write race at the API level, independent of this state model.

## Implementation

1. **`gdoc/state.py`**
   - Add the field. Extend `update_state_after_command` with `read_tab_id: str | None`
     and `written_tab_id: str | None`. When `read_tab_id` is set, the command must NOT be
     treated as a whole-doc read (`is_read` stays False for it — introduce e.g.
     `command="cat-tab"` or gate on the param; pick one, don't do both).

2. **`gdoc/cli.py` — read side (`cmd_cat`)**
   - Tab path (`resolve_tab` already called here): pass `match["id"]` as `read_tab_id`;
     stop advancing `last_read_version` for this path. **Exception:** when the doc has
     exactly one tab, reading that tab is a whole-doc read — take the normal whole-doc
     path (advance `last_read_version`) instead, or a subsequent whole-doc write
     false-conflicts against a read that did cover everything. `write --tab X` applies
     the mirror-image rule (`full_doc_write=(len(all_tabs) == 1)`).
   - `--all-tabs` path: unchanged (whole-doc semantics; global baseline covers all tabs).

3. **`gdoc/cli.py` — write side (`cmd_write` tab branch)**
   - Resolve before checking: fetch the doc once (`get_document_with_tabs`), `resolve_tab`
     in the CLI, then run the new `_check_tab_write_conflict(doc_id, tab_id, tab_label,
     quiet, force)` (both quiet and pre-flight branches, mirroring
     `_check_write_conflict`; banner/pre-flight still runs first).
   - Extend `insert_markdown_into_tab` to accept an optional pre-fetched `doc` dict so the
     resolution fetch isn't duplicated (today it fetches internally; keep that as the
     default for other callers).
   - On success: `update_state_after_command(..., written_tab_id=tab_id)`.

4. **Docs**: README "Working with tabs" — fix the stale "export only returns the first
   tab" claim, and add a short "Conflict detection and tabs" paragraph (per-tab baselines,
   the lenient rule, the `--force` escape, the sibling-edit caveat). CHANGELOG 0.22.0.
   Bump version; sync `uv.lock`.

## Tests

Mock at the `gdoc.api` boundary per suite convention.

- `test_state.py` (or new `test_state_tabs.py`): tab read stamps `tab_read_versions` and
  leaves `last_read_version` alone; tab write advances its own entry; old-format state
  file loads with empty dict.
- `test_write.py`:
  - `cat --tab A` only → `write --tab B` → exit 3 "no read baseline for tab 'B'" (false-negative fix).
  - plain `cat` → `write --tab B` → succeeds (lenient rule: export is a full read).
  - `cat --tab X` → `write --tab X` → `write --tab X` again → **both succeed** (false-positive fix).
  - sibling version bump between tab read and tab write → exit 3 with the "may have changed" message; `--force` bypasses.
  - `--quiet` variants of the above (state-file-driven branch).
  - URL-tab variants (`write '<url>?tab=X'`) behave identically to `--tab`.
- `test_cat_tabs.py`: tab read no longer advances `last_read_version` (regression lock for R2).
- Whole-doc `write`/`push` tests unchanged — assert no behavioral drift (existing suite covers).

## Verification

1. `uv run pytest tests/ -q` and `uv run ruff check gdoc/ tests/`.
2. Live smoke on a scratch 2-tab doc: cat tab B → write tab B twice (both succeed);
   write tab A without reading anything (exit 3); plain cat then write tab A (succeeds —
   lenient); `--force` path.

## Related follow-ups surfaced by the 2026-07-05 live verification (separate work)

Designs for tab-scoped `comments`, the `images`/`toc` first-tab-only bug fixes, `diff`
export-slicing, and the stale README export claim live in
`docs/tab-scoped-commands-plan.md`.
