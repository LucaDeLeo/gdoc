# Changelog

All notable changes to `gdoc` are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.19.0] — 2026-08-07

### Added
- **Folder and file management: `mkdir`, `mv`, `rename`, `drives`.**
  `gdoc mkdir TITLE [--parent FOLDER]` creates a Drive folder;
  `gdoc mv DOC FOLDER` (alias `move`) moves a file, replacing all its
  current parents so it lands in exactly one place and reporting the
  final location; `gdoc rename DOC TITLE` retitles a file; `gdoc drives`
  lists shared drives. Moves and renames run the pre-flight awareness
  check and fold their own version bump into the doc's state baseline so
  the next command doesn't report a spurious edit; since they never touch
  content, a read baseline that was current at pre-flight is carried
  forward too, so a following `edit`/`write`/`push` doesn't see gdoc's
  own metadata bump as an external conflict. (#39)
- **Raw Drive queries: `find --raw`.** `gdoc find --raw "QUERY"` passes
  the query string to the Drive API verbatim (full query language:
  `mimeType=…`, `'me' in owners`, `modifiedTime > …`), while plain
  `find QUERY` keeps its simple escaped name/content search. Raw queries
  search the `allDrives` corpus — personal Drive plus every shared drive
  the user is a member of — and warn on stderr if Google reports the
  search came back incomplete. (#39)
- **Domain and anyone-with-link sharing.** `gdoc share DOC --domain
  example.org` and `gdoc share DOC --anyone` create link-based
  permissions alongside the existing per-user email shares.
  Discoverability (`allowFileDiscovery`) is never inferred: it's off
  unless `--discoverable` is passed, and that flag is rejected for
  per-user shares. Exactly one share target (email, `--domain`,
  `--anyone`) is required. User-share output keys are unchanged;
  domain/anyone shares report `target`, `type`, and `discoverable`. (#39)

## [0.18.0] — 2026-08-07

### Added
- **`gdoc export` — render a document to PDF, DOCX, and more.**
  `gdoc export DOC --out report.pdf` writes a rendered artifact via Drive
  export; the format is inferred from the `--out` extension or set with
  `--format` (`pdf`, `docx`, `odt`, `epub`, `html`, `md`, `txt`, `rtf`).
  Binary formats require `--out` (no PDF bytes to a terminal); text
  formats print to stdout without it. Exports cover the whole document,
  all tabs included. (#35)
- **`gdoc insert-image` — add an image to an existing document.**
  Takes a local file (PNG/JPG/GIF — the Docs API rejects WebP, so it is
  refused before any upload) or a public image URL, anchored
  by `--after TEXT` (retries with smart-quote folding; ambiguous anchors
  are refused), a raw `--index`, or `--end`; `--width`/`--height` set the
  display size in points (validated as positive finite numbers before
  anything is uploaded). Multi-tab documents require `--tab`, and the
  write is pinned to the read revision via
  `writeControl.requiredRevisionId`. Local files are uploaded to Drive as
  a temporary public-read file and deleted after the insert — a failed
  cleanup warns with the file ID instead of leaving the exposure silent,
  and if a Workspace policy blocks the public share the just-created
  file is deleted rather than orphaned. Prints the new image object
  ID. (#35)
- **`gdoc replace-image` — swap an image's content by object ID.**
  `gdoc replace-image DOC OBJECT_ID new.png` replaces the image content
  in place (IDs from `gdoc images`), keeping the existing display size
  (CENTER_CROP). The owning tab is located automatically, including
  nested child tabs. (#35)

### Fixed
- **`gdoc images` now lists objects from every tab.** It previously
  walked only the legacy first-tab fields, so images in other tabs were
  invisible — including to the `replace-image` workflow that points
  users at it for object IDs. Entries gain a `tab` field (`--plain`
  appends it as a final column).

## [0.17.0] — 2026-08-07

### Added
- **`gdoc structure` — native document JSON for structure-aware edits.**
  Dumps the raw `documents.get` response: tab topology, paragraph and
  text styles, tables, inline objects, named ranges, headers/footers,
  and the UTF-16 `startIndex`/`endIndex` values needed to derive safe
  native mutation ranges without parsing Markdown (Docs indices are
  UTF-16 code units, not Python character offsets — a smart chip
  occupies one code unit). Tab content is always included; narrow big
  documents with `--tab TITLE|ID` (returns that tab's raw subtree plus
  `documentId`/`revisionId`) or `--fields MASK` (passed verbatim;
  Google rejects masks that recursively expand `childTabs`).
  `--suggestions-view-mode` selects how suggestions are rendered — it
  changes content and indexes, so the mode used is echoed in the
  output. Output is always JSON: compact by default, indented with
  `--verbose`, wrapped in the standard `ok` envelope with `--json`.
  Read-only; runs pre-flight and counts as a full read for the
  awareness baseline (like `cat`). (#33)

## [0.14.0] — 2026-08-07

### Added
- **Real anchored comments via the Docs API (Developer Preview).**
  `comment --quote "doc text"` now tries the Docs API `insertComment`
  batchUpdate request first: the quote is located in the document (every
  tab is searched, retrying with smart-quote/dash folding) and the comment
  is anchored to that range — pinned to the searched revision via
  `writeControl.requiredRevisionId` — so it shows up highlighted in the
  Docs UI like a comment made by hand. The request is gated behind the
  Google Workspace Developer Preview Program; when it's unavailable —
  project not enrolled (400 for the unknown request type), comment-only
  access that can't `batchUpdate` (403), or the doc changed between read
  and write — the command falls back transparently to the existing Drive
  `quotedFileContent` path, so behavior for non-preview users is unchanged.
  Terse output gains an `(anchored)` suffix on success; `--json` and
  `--plain` report `anchored` true/false whenever `--quote` is given. Adds
  the `insert_comment` Docs API helper and a `PreviewUnavailableError`
  sentinel (a `GdocError` subclass callers catch to fall back — it never
  surfaces to the user).

### Fixed
- **Text search used Python code-point indices, not UTF-16.** Docs API
  indices count UTF-16 code units, so in documents containing non-BMP
  characters (emoji), `find_text_in_document` returned ranges shifted left
  of the real match — affecting `edit` replacements as well as comment
  anchoring. Offsets now advance by UTF-16 width.

## [0.13.0] — 2026-07-14

### Added
- **Configurable page mode for `gdoc new`.** New docs can be created pageless
  or paged. Drive's markdown importer always produces *paged* docs (it ignores
  the account's pageless default), so docs made via `new --file` came out
  paged regardless of preference. `gdoc new` now applies an explicit page mode
  after creating the doc: a `--pageless` / `--paged` flag overrides a persisted
  default set with `gdoc config --page-mode {pageless,paged}` (stored in
  `~/.config/gdoc/config.json`). With **no flag and no configured default**,
  the doc is left exactly as the create path produced it — a blank `gdoc new`
  still inherits the account's pageless/paged default, and markdown imports
  stay paged — so the feature never silently overrides an account preference.
  Applying the mode is best-effort — a failure (of any kind) warns on stderr
  but does not fail the creation, and the write's version bump is folded into
  the doc's state baseline so it doesn't surface as a spurious change. Adds a
  `config` subcommand (honors `--json`/`--verbose`/`--plain`) and the
  `set_page_mode` Docs API helper
  (`updateDocumentStyle` → `documentFormat.documentMode`).

### Fixed
- **`new --file` with images seeded a stale version baseline.** Image inserts
  advance the doc's Drive version after creation, but state was seeded with
  the create-time version, so the next command reported a spurious
  "doc edited" change. The version is now re-read (best-effort) after image
  insertion, matching the page-mode write's baseline handling.

## [0.12.1] — 2026-07-07

### Fixed
- **Per-tab `cat` now preserves headings.** `cat --tab` / `cat --all-tabs`
  built their output from a plain-text extractor that ignored
  `paragraphStyle`, so headings came back as plain paragraphs (the whole-doc
  Drive export already emitted `#` headings). A read-modify-write cycle
  through `cat --tab` → `edit`/`insert` therefore silently demoted the
  previous heading to body text. `get_tab_text(..., markdown=True)` now
  prefixes heading paragraphs with the matching number of `#` marks, and
  default `cat --tab`/`--all-tabs` request it. `cat --plain --tab` is
  unchanged — it still returns the verbatim text `edit` matches against.
- **Per-tab `cat` now renders inline formatting and lists.** Extending the
  markdown export above, `cat --tab` / `cat --all-tabs` now emit `**bold**`,
  `*italic*`, `~~strikethrough~~`, `[text](url)` links, and bullet/numbered
  lists (nested, two spaces per level, ordered items counted 1, 2, 3 —
  ordered vs bullet read from the tab's list glyph map). Previously these all
  flattened to plain text on read even though `insert`/`write --tab` could
  produce them. `cat --plain --tab` remains verbatim. Not yet rendered:
  inline code, blockquotes, and markdown tables (tables still export as
  tab-separated cells).

## [0.12.0] — 2026-06-22

### Added
- **Per-tab markdown writes now render a much larger subset.** `write --tab` /
  `insert --tab` (the hand-built Docs API path, not Drive's importer) now
  support: nested emphasis (`**bold _italic_**`), strikethrough (`~~x~~`),
  blockquotes (indented), horizontal rules, fenced code blocks (monospace),
  nested bullet/numbered lists (indented), and inline formatting inside table
  cells. See `gdoc.md` for the supported set and known gaps (images render as
  `!` + link; nested-list glyphs don't cycle by depth).

### Fixed
- **Per-tab markdown writes preserve bold/italic.** `write --tab` /
  `insert --tab` emitted `updateTextStyle` before `updateParagraphStyle`;
  applying a `namedStyleType` re-resolves a run's direct character
  formatting and cleared the just-set bold/italic (links survived a
  named-style reset, so only bold/italic broke). Paragraph-style and bullet
  requests are now emitted before text styles, so character formatting
  wins. Whole-doc `write` (Drive's importer) was unaffected.
- **Per-tab markdown writes honor backslash escapes.** `_parse_inline` had
  no escape pass, so `\*x\*` kept the backslash *and* still rendered italic.
  Escapes now follow CommonMark: the escaping backslash is stripped and the
  escaped character can no longer open or close an inline span, so `\*`,
  `\[`, etc. produce literal text. Code spans stay literal (a regex like
  `` `\d+\.\d+` `` keeps its backslashes).
- **Ordered lists number continuously.** Bullets are created top-to-bottom so
  Docs attaches each item to the list above it (`1, 2, 3`, not three `1`s).
- **Horizontal rules survive at end of input.** The trailing-newline trim no
  longer collapses a final horizontal rule.

## [0.11.0] — 2026-06-11

### Added
- **`gdoc revisions DOC`** (alias `history`) — list the milestone
  revisions the Drive API retains for a document (Google's rich
  "Version history" has no public API; Drive milestones are the
  reconstructable subset). Human table, `--json`, and `--plain` modes;
  `--limit N`; `[keep]` marker for pinned revisions.
- **`gdoc cat/pull --revision REV`** — export or download a past
  revision. REV selector grammar shared with `diff`: bare id,
  `latest`/`head`, `prev`, `head~N` (by list position — ids are
  sparse), `@ISO` (last revision at/before a timestamp).
  `pull --revision` writes `source:`/`revision:` frontmatter instead
  of `gdoc:` so a stale revision can't be pushed back by accident,
  and neither command advances the read baseline used by
  write-conflict checks.
- **`gdoc diff DOC --rev A..B | --rev REV | --since ISO`** — diff two
  revisions (or a revision against latest) with a readable
  **coalescing word-diff**: shared scraps shorter than `--min-common`
  chars (default 24) are absorbed so a rewritten sentence renders as
  one removed chunk + one added chunk, not word salad. Colored
  word-diff on a TTY, plain `[-…-]`/`{+…+}` text when piped, `--json`
  for a stable documented diff model, and `--format html --out F`
  for a styled review artifact (GitHub-style colors, collapsed
  unchanged runs, `--context N`). Existing `diff DOC FILE` behavior
  is unchanged.
- **`gdoc diff --with-comments`** — pull the doc's comment threads and
  anchor each to the diff hunk containing its quoted text (changed
  hunks preferred); threads whose anchor isn't visible render in an
  "Other comment threads" appendix. Color-coded by author in html.
- Richer artifacts (docx, PDF, …) are deliberately not built in —
  external scripts render them from the `--json` diff model.

### Changed
- A pruned or unknown revision produces a clear exit-3 error pointing
  at `gdoc revisions` (Drive prunes non-pinned revisions over time).
- `comments.list` now also requests reply `createdTime` (used by the
  diff comment rendering).
- `requests` is now a declared dependency (it was already pulled in
  transitively); revision exports use it directly via
  `google.auth.transport.requests`.
- `push` on a `pull --revision` file explains that revision pulls are
  not pushable, instead of "no gdoc frontmatter found".
- Frontmatter values are flattened to one line on write, so a doc
  title containing a newline can't inject frontmatter keys.

## [0.10.2] — 2026-06-09

### Fixed
- **Tab writes no longer claim full-doc knowledge.** 0.10.1's baseline
  advance applied to `write --tab` too, so a forced tab write after unseen
  remote changes let the next full-doc `push`/`write` skip conflict
  detection and overwrite them. The baseline now advances only for actual
  full-content writes (`push`, full-doc `write`, the sync hook).
  (Codex review on #24.)
- **Replacing credential files now enforces 0600.** `os.open`'s mode only
  applies on creation, so `gdoc auth --setup-url` over an existing
  world-readable `credentials.json` kept it world-readable. Credential and
  token files are now written to a fresh 0600 temp file and atomically
  swapped in. (Codex review on #23.)

## [0.10.1] — 2026-06-09

### Fixed
- **False write conflicts against your own pushes.** A successful `push` or
  `write` (including the `_sync-hook` path) now advances the conflict
  baseline (`last_read_version`) — the doc contains exactly what was sent,
  so the write doubles as a read. Previously only `cat`/`info`/`pull`
  advanced it, so a second push after your own write failed with
  "doc changed since last read".
- **Content-aware conflict detection.** When the version check fails for a
  full-doc `push`/`write`, gdoc now exports the doc and compares it to the
  content being written. If they match (own earlier write, cosmetic Docs
  version bump), the command succeeds as a no-op — "OK already in sync" —
  and heals the baseline instead of erroring. Tab writes are excluded
  (a tab body never equals the whole-doc export).

## [0.10.0] — 2026-06-09

### Added
- **Org-friendly auth.** The OAuth client config can now come from
  `GDOC_CLIENT_ID`/`GDOC_CLIENT_SECRET` env vars, a `GDOC_CLIENT_CREDENTIALS`
  file path, or the existing `~/.config/gdoc/credentials.json` (in that
  order), so companies can distribute one shared Internal OAuth client via
  MDM/dotfiles instead of every user creating a Cloud project.
- `gdoc auth --setup-url <url>` fetches the org's OAuth client file from an
  internal URL, validates it, and stores it at
  `~/.config/gdoc/credentials.json` (0600) before running the flow. With
  `GDOC_SETUP_URL` set, plain `gdoc auth` does this automatically when no
  client config exists yet.
- `gdoc auth --domain <domain>` (or `GDOC_AUTH_DOMAIN`) passes an `hd` hint
  to the Google account chooser so it pre-filters to the Workspace domain;
  named accounts that look like emails are passed as `login_hint`.
- README: documented org-wide setup with a shared Internal OAuth client.

## [0.9.0] — 2026-06-09

### Added
- **Auto-update on help.** Bare `gdoc`, `gdoc --help`, and `gdoc -h` now
  upgrade to the latest release before printing help, so agents inspecting
  the CLI surface always see current help text. Only applies to `uv tool`
  installs, checks at most once per hour, and silently falls back to the
  current version on any failure (offline, install error). Opt out with
  `GDOC_AUTO_UPDATE=0`.
- README: documented installing `uv` itself, the `gdoc update` command,
  and the new auto-update behavior.

## [0.8.1] — 2026-06-05

### Fixed
- `gdoc update` compared versions with plain inequality, so a stale GitHub
  raw cache reporting an *older* version produced a backwards
  "Update available: 0.8.0 → 0.7.6" notice — and `gdoc update` would
  actually downgrade. Versions are now compared numerically and only
  strictly-newer remotes trigger the notice/install.

## [0.8.0] — 2026-06-05

### Added
- **Google Sheets support.** `cat`, `tabs`, and `info` now detect
  spreadsheets and read cell values via the Sheets API: `cat` prints a
  markdown table (`--plain` for TSV, `--json` for raw rows), `--tab` selects
  a worksheet by title or sheet id, and `--range A1:C10` reads a slice.
  `tabs` lists worksheets with their dimensions; `info` shows them instead
  of a word count.
- **`gdoc cells SHEET RANGE`** — write values into a spreadsheet range from
  `-v` flags, a CSV/TSV file (`--file`), or TSV on stdin (`--stdin`).
  `--append` inserts rows below the existing table; `--user-entered` parses
  values as if typed in the UI (formulas, dates, numbers). Uses the existing
  OAuth scope — no re-authentication needed.

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
