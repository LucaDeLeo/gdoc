# Changelog

All notable changes to `gdoc` are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.6] — 2026-06-02

### Added
- **`gdoc edit` now works inside tables.** `find_text_in_document` descends
  into table cells (and nested tables), so search/replace finds text that was
  previously invisible — `edit` used to return "no match found" for in-table
  text that `cat` could read.
- **`gdoc edit --cell ADDR`** — address a table cell directly instead of
  anchoring on its text. Label mode (`--cell "Discussion topics"`) replaces the
  cell to the label's right (`--col` to override); coordinate mode
  (`--cell ROW,COL`, `--table N`) indexes a cell by position. Empty cells are
  filled in place.
- **`gdoc edit --normalize`** — match through smart-quote/dash differences
  (e.g. `’` matches `'`). Exact by default.
- **`-` reads an argument from stdin** for `gdoc edit`, enabling heredocs and
  pipes for multi-line anchors/replacements (at most one `-`).

### Changed
- A failed `edit` match now explains why (smart-quote or whitespace near-match)
  instead of a bare "no match found".

## [0.7.5] — 2026-06-01

### Fixed
- **`gdoc toc --tab`** now emits heading deep links in Google's own
  canonical form — `…/edit?tab=t.<id>#heading=h.<anchor>`. Previously the
  tab id was double-prefixed (`t.t.<id>`, because `tabProperties.tabId`
  already carries the `t.` prefix) and `&tab=…` was appended inside the
  URL fragment instead of as a query parameter, so the links didn't
  reliably open the right tab. `cmd_toc` now builds the URL via the
  shared `build_doc_url()` helper. PR #18.

## [0.7.4] — 2026-05-23

### Added
- **`gdoc auth --set-default ACCOUNT`** — configure which authenticated
  named account bare `gdoc` commands use when `--account` and
  `GDOC_ACCOUNT` are omitted.

### Fixed
- The default account now resolves to the configured named account token
  instead of requiring a separate `~/.config/gdoc/token.json` credential.
  The legacy token remains as a fallback when no default account is
  configured.

## [0.7.3] — 2026-05-23

### Added
- **`gdoc push --force-collapse-tabs`** — opt-in flag mirroring
  `gdoc write`. Without it, `push` now refuses to overwrite a
  multi-tab document (exits 3 before any API write) and points you at
  `gdoc edit --tab`, `gdoc insert --tab`, or the new flag.

### Changed
- **`gdoc push`** and **`gdoc _sync-hook`** now refuse to silently
  collapse multi-tab documents into one tab — extending the safety
  guard 0.7.1 added to `gdoc write` across the remaining destructive
  paths. A `pull`/`push` round-trip on a multi-tab doc previously
  deleted every tab but the first with no warning. `_sync-hook` runs
  non-interactively, so it hard-skips multi-tab docs and logs
  `SYNC: skipped "<title>" (multi-tab doc; ...)` to stderr.

## [0.7.2] — 2026-05-07

### Fixed
- **`gdoc write`** no longer fails the per-write multi-tab safety
  check. The Docs API now rejects the recursive `childTabs` field
  mask, so `count_document_tabs` calls `documents.get` without a
  mask. Issue #14.

## [0.7.1] — 2026-04-11

### Added
- **`gdoc insert DOC --tab NAME FILE`** — new command for populating a
  specific tab from a local markdown file. Works on empty tabs, which
  was previously impossible via the CLI (`add-tab` + `edit --tab`
  couldn't find an anchor in an empty body). Strips YAML frontmatter
  automatically. `--position start|end` controls where in the tab body
  to insert.
- **`gdoc write --tab NAME`** — scoped write that replaces exactly one
  tab's body via the Docs API, leaving sibling tabs untouched.
- **`gdoc add-tab`** now prints a clickable
  `https://docs.google.com/document/d/DOC/edit?tab=ID` URL alongside
  the bare `tabId`.
- New `insert_markdown_into_tab` and `count_document_tabs` helpers in
  `gdoc.api.docs`. `count_document_tabs` uses a fields mask so the
  new per-write safety check fetches only tab IDs — no body content.

### Changed
- **`gdoc write`** now refuses to collapse multi-tab documents into a
  single tab. When the remote doc has more than one tab and you don't
  pass `--tab`, `write` exits with code 3 and points you at
  `--tab NAME`, `gdoc insert`, or the new `--force-collapse-tabs`
  opt-in. The old collapsing behavior remains available, but you have
  to ask for it. This closes the biggest footgun in the previous
  `pull`/`write` asymmetry.
- **`gdoc write`** now strips YAML frontmatter from the input file
  before upload. `pull` adds frontmatter; leaving it in the upload
  used to dump visible YAML into the doc body.
- **`gdoc edit --old-file FILE`** is now usable on its own — it deletes
  the matched range. Previously `--old-file` and `--new-file` were
  required together. `--new-file` alone still errors (no anchor text)
  and now points users at `gdoc insert` for anchorless writes.
- `gdoc write --help` documents the single-tab limitation explicitly.

### Fixed
- `replace_formatted` no longer builds `deleteContentRange` requests
  for zero-width matches. The Docs API rejects empty ranges with
  `"The range should not be empty"`, which broke any flow that tried
  to use a zero-width match as a pure insert (e.g., `edit --tab` on
  an empty tab).
- `parse_frontmatter` no longer strips a leading `---\n...\n---\n`
  block unless it contains at least one `key: value` line. Previous
  behavior could silently eat content from markdown files that open
  with a thematic break followed by another `---`. All
  frontmatter-consuming commands (`write`, `insert`, `push`,
  `_pull-hook`, etc.) benefit.
- `__version__` was drifting from `pyproject.toml` again; resynced.

## [0.7.0] — 2026-04-09

### Added
- `gdoc toc DOC` — table of contents with deep links to headings.
- Multi-account support via `--account` flag.
- `--no-images` flag on `gdoc cat` to skip image placeholders.
- `supportsAllDrives=True` on all Drive API calls.
- `modifiedByMeTime` in `list_files` response.

### Fixed
- Trailing newline handling in `replace_formatted`.

## [0.6.0] — Earlier releases

See the git history prior to 0.7.0 for detail. Earlier releases covered
authentication, read operations, the awareness system, write operations,
comments and annotations, file management, local-file sync
(`pull`/`push`/`_sync-hook`), and the `gogcli` feature set (byte
truncation, native tables, image import).
