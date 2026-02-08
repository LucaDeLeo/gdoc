Phase 02 CONTEXT.md is at `.planning/phases/02-phase-02/CONTEXT.md`. Full detail in `.planning/phases/02/CONTEXT.md`.

**Incorporated (6 items):**
1. **API package layout** (Q1/Q8) — Using `gdoc/api/__init__.py` + `gdoc/api/drive.py` per `gdoc.md` architecture, not a flat `api.py`
2. **JSON schema consistency** (Q3) — New `format_json(**data)` helper for per-command structured JSON; `format_success` retained for simple messages
3. **`find` query** (Q5) — Combined `(name contains 'Q' or fullText contains 'Q')` with input escaping for single quotes and backslashes (see Resolved decision #3)
4. **`ls` output** (Q9) — Three columns `ID\tTITLE\tMODIFIED` matching `gdoc.md` examples, not two
5. **401 → AuthError** (Q11) — HTTP 401 maps to `AuthError` (exit 2); ERR: prefix stays in CLI layer only
6. **`ls` default scope** (Uncertainty 2) — `'root' in parents` per `gdoc.md`'s "default: root" comment

**Kept as-is (5 items):** markdown export, raw stdout for `cat`, `files.list` with pagination, `extract_doc_id` in handlers, word count via text/plain export (Option A)

**Resolved decisions (previously flagged):**

1. **Shared drive support** — Defer to a future phase. Phase 2 scope stays tight: personal Drive only. When shared drive support is added later, pass `supportsAllDrives=True`, `includeItemsFromAllDrives=True`, and `corpora="allDrives"` to `files.list`. No code for this in Phase 2.

2. **`ls` default scope** — `'root' in parents` (strict root-only) per `gdoc.md`. Users use `find` for broader search. Can revisit based on user feedback.

3. **Query escaping** — Escape both single quotes (`'` → `\'`) and backslashes (`\` → `\\`) in user input before embedding in Drive `q` strings. Applied in `find` and any command that builds `q` parameters.

4. **Non-Google-Docs IDs in `cat`/`info`** — Explicit error. If `files.export` fails because the file is not a Google Docs editor file (e.g., a PDF, image, or binary), raise `GdocError("Cannot export file as markdown: {reason}")` with exit code 1. The `info` command uses `files.get` which works on any Drive file, so no special handling needed there.

---

## Auto-Discuss Metadata

- **Rounds:** 3
- **Codex Available:** yes
- **Uncertainties Resolution:** 2 of 2 resolved (word count: Option A — always compute; `ls` default: `'root' in parents`)
- **Timestamp:** 2026-02-07T19:33:15Z

<details>
<summary>Codex Review (Round 2)</summary>

1. [QUESTION] A single `gdoc/api.py` conflicts with the planned package layout in `gdoc.md` (`gdoc/api/drive.py`, `gdoc/api/docs.py`). I'd put the Drive factory in `gdoc/api/drive.py` (or re-export from `gdoc/api/__init__.py`) to avoid a later refactor.
2. [AGREE] Defaulting `cat` to `files.export(..., text/markdown)` with `--plain` as `text/plain` matches the spec in `gdoc.md`. Consider a clear fallback error if export is unsupported or too large.
3. [QUESTION] Raw stdout is right for `cat`, but the proposed JSON shape (`{"ok": true, "content": ...}`) conflicts with `format_success` in `gdoc/format.py` and the JSON example in `gdoc.md`. Decide on a consistent JSON schema and encode it once.
4. [AGREE] `files.list` with `q`, limited `fields`, and pagination is sound. If shared drives are in scope, add `supportsAllDrives=True` and `includeItemsFromAllDrives=True`.
5. [QUESTION] `fullText contains` alone may miss name-only matches and requires escaping. Safer: `q="(name contains 'Q' or fullText contains 'Q') and trashed=false"` with proper escaping of quotes/backslashes.
6. [AGREE] Word count via `text/plain` export is acceptable for v1. Make sure `files.get` fields include `owners(emailAddress)` and `lastModifyingUser(emailAddress)` so required outputs are actually present.
7. [AGREE] Using `extract_doc_id` in handlers and translating `ValueError` to `GdocError` is consistent with existing patterns.
8. [QUESTION] The single-file `api.py` plan conflicts with the documented architecture in `gdoc.md`. Prefer a package now to keep Phase 3+ aligned.
9. [QUESTION] `ID\tNAME` as default output conflicts with the `gdoc ls` example in `gdoc.md` (ID, title, modified) and with `format.py`'s JSON helper. Pick the canonical default/verbose/JSON format and update docs/helpers to match.
10. [AGREE] Auto-paginate all results for now is fine; limits can be added later.
11. [QUESTION] Map 401 to `AuthError` (exit 2) rather than generic `GdocError`; 403 can be permission vs export limitations. Also avoid prefixing `ERR:` in the API layer since `cli.py` already handles that.

**Uncertainties**
1. Word count: choose Option A (always compute) to meet the explicit requirement in `gdoc.md`.
2. `ls` default scope: `gdoc.md` says "default: root," so use `'root' in parents` unless you explicitly change the spec.

**Gaps**
- ~~Define and enforce a single JSON schema across commands~~ → Resolved: `format_json(**data)` helper.
- ~~Escape user input in Drive `q` strings~~ → Resolved: escape both `'` and `\` before embedding.
- ~~Decide shared-drive support~~ → Resolved: defer to future phase.
- ~~Clarify behavior for non-Google-Docs IDs in `cat`/`info`~~ → Resolved: explicit `GdocError` for non-exportable files in `cat`; `info` works on any Drive file via `files.get`.

</details>
