# Changelog

All notable changes to `gdoc` are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.23.0] — 2026-09-29

### Added

- Native read–modify–write supports structural Markdown changes through the same
  CLI and MCP handlers, including code, quotes, aligned tables and images.
- `write` and `push` refuse identified rich-content loss before mutation.
  `--allow-lossy` permits that loss independently of revision conflicts and
  explicit sibling-tab collapse. Supported Markdown needs no loss override.

### Changed

- Pulled files record the native `gdoc-revision` and a tab fingerprint instead
  of 0.21.1's Drive `gdoc-version`. A file stamped only with `gdoc-version` is
  still refused while stale, with 0.21.1's recovery steps, and otherwise needs a
  fresh pull or `--force`.
- `cat --comments` of the whole document places comments from live anchors, as
  in 0.21.x. With `--tab` it annotates that tab's native Markdown, placing
  comments by quoted text; a quote found only in another tab says so.
- Targeted edits that would move list items a level in or out point to
  `gdoc nest` and `gdoc unnest` when those would accept the items, and to
  `write` otherwise.
- A file that doesn't start with a header as `pull` writes it, but holds a
  `gdoc` or `gdoc-*` key line anywhere (after any prefix of spaces, `>`, list
  markers, backticks or `<!--`, in any case), is refused by `write`, `push` and
  MCP `write`, and the sync hook reports the refusal, instead of writing a
  quoted, wrapped or prefixed stale header without its stale-file checks.
  Ordinary text with such a line is refused too; a fresh pull, or escaping the
  colon (`gdoc\:`), writes it. Other front matter is unaffected.
- A file stamped only with `gdoc-version` whose body matches the doc is
  `already in sync`, as in 0.21.1; the pull hook blocks edits to a stale one
  and re-pulls a matching one.
- Deleting a list item is refused when anything but a blank line, top-level
  text or the next item at its level follows it, since that content may be the
  item's and would join the item above. In a table cell, unindented text after
  the item doesn't block the deletion. Inserted items that `write` would or
  may join to a list beside them are refused, including a numbered list
  starting at a number other than 1 when the tab has a numbered list.
- Code fences in list items and quotes are accepted only as `cat` prints them:
  on their own lines, every line carrying the container's indent or `> `, and
  closed inside the container. Any other line starting with a fence after
  spaces, quote markers and list markers is refused with nothing sent; the
  message gives the accepted spelling.
- `edit --cell` refuses an image when the cell's number of lines changes or an
  encoded line break (`&#10;`) is present, instead of dropping it.
- `cat --comments` ignores the `<!-- -->` separator between touching runs when
  matching quotes, and whole-document output reports truncation.

- Default `write` and `push` read Markdown in gdoc's format instead of Google's
  Markdown import, as `write --tab` and `insert` already did: each line is one
  paragraph and each blank line is an empty paragraph, matching `cat` output.
  Files written for CommonMark paragraph rules (hard-wrapped lines, blank-line
  separators) gain paragraph breaks and empty paragraphs; write one line per
  paragraph. `new --file` still uses Google's import.

- A pinned revision refused by the server exits 3 for `insert-image`,
  `replace-image` and `suggest`, as for other writes. The README now says that
  images inserted by `new --file` use the ordinary client transport.

- Link titles no longer enter link URLs; exported destinations with whitespace
  are bracketed. Table rows may end in whitespace or be indented up to three
  spaces. `***bold** then italic*` reads as CommonMark does. Whitespace-only
  links are written once. Rule-like list items no longer gain backslashes.
  `cat --comments` finds anchors that span emphasis or links. Inline parsing of
  long paragraphs is linear.

- Container context is a path of quote markers and list item indents, so
  paragraphs, headings, rules, code, tables and quotes nest inside list items
  and quotes to any depth, and blank paragraphs between contained tables keep
  their count across rewrites. New nestings use `gdoc:prefix:v3:` range names;
  v1/v2 ranges keep their meaning. Literal leading whitespace in a paragraph is
  exported as a numeric entity.

- Reads name native content their Markdown leaves out (footnotes, chips, page
  breaks and similar, including inside table cells) and report
  `complete: false` with an `omitted` list; `cat --json` reports `tab_count`.
- Default `cat`, `pull` and Markdown `export` use the native first-tab serializer.
  Use `--tab` for another editable tab or `cat --all-tabs` for inspection.
- Default `write` and `push` replace the first tab and preserve siblings.
  `--force-collapse-tabs` explicitly removes siblings through native requests.
- `insert`, `write` and `push` require complete per-tab content at the exact Docs
  revision. Metadata/partial reads cannot authorize replacement. Acknowledged
  writes advance known content; rebased or uncertain writes require a fresh read.
- `comment --quote` refuses ambiguous matches and reports candidates.
- `edit` refuses matches that touch pending suggestions, like `suggest`, and
  names the changed tab whenever its search covered several tabs.
- `edit` and `suggest` search all tabs, including headers, footers and footnotes.
  `--tab` selects one tab and its segments; `--all` replaces every match in scope.
- Pushes check the file's revision as well as the shared read baseline. An older
  file stays pushable only while its selected tab's native content (text, styles
  and suggestions) matches the recorded `gdoc-tab-sha256`; Markdown equality alone
  never blesses a newer revision. Pull hooks
  leave unsynced local bodies in place. Local replacements retain the previous
  file as a printed recovery copy, so an editor write racing the replacement is
  never lost. Files without `gdoc-revision` need a fresh pull or `--force`.

### Fixed

- A list quoted inside a nested list item keeps its own bullets or numbering;
  the enclosing list no longer absorbs it.
- Nested list items get their native nesting level, not only an indent: a
  nested restart, an orphan nested item or a nested list under another bullet
  type no longer reads back flat. A later nested numbered list no longer
  continues an earlier sublist's numbering.
- Editing list items keeps their native list: items reworded as items of the
  same kind and level keep their bullets and numbering. `edit` and `insert`
  refuse, with nothing sent, any other list change (another kind or level, an
  added or removed item, a container change, or items that would join the
  list beside them); `write --tab` makes it.
- `edit OLD ""` removes exactly the matched paragraphs. Removing the last
  paragraph, or one before a table, no longer also deletes an empty paragraph,
  rule or code line above it, and it no longer refuses after a list item. It is
  refused when it would delete a paragraph break carrying a suggestion.
- A whole-paragraph replacement with a table inside list-item content stays in
  the item instead of reading back as quotes, and a table replacing a paragraph
  between blank lines adds no extra blank paragraph.
- A rule or empty heading between two tables keeps its own quote or item.
- Whitespace between differently styled runs in a document with an explicit
  font reads as plain whitespace, not doubled spaces or empty `**` markers.
- An empty paragraph reads as `---` only when its bottom border is visible.
- A rebased write warns and reports `rebased: true`; `--plain` prints an in-sync
  result as TSV; an input file that is not UTF-8 exits 3.
- `suggest` refuses an empty replacement across a paragraph break, which
  accepting could not join.
- `**`, `__` and `~~` beside whitespace stay literal; `<...>` link destinations
  may hold parentheses; an image title is dropped from its URI; an incomplete
  entity name such as `&notes;` stays literal.
- `write` of a pulled file replaces the tab it came from and refuses another
  `--tab` or document. It checks the file's `gdoc-revision` as `push` does, so a
  later read cannot authorize an older file; a `pull --revision` file needs
  `--force`. Rule-first bodies whose lines contain a colon or a link
  stay content instead of being read as metadata.
- Linked Sheets charts, custom named ranges, and rules or indented paragraphs in
  table cells are named as read omissions and need `--allow-lossy` to rewrite.
- `insert` keeps the tab's first or last paragraph in its quote, list item or
  code block, does not pass those properties to appended content, and starts an
  appended numbered list as its own list.
- Reference definitions accept `<...>` destinations with spaces, and blank
  paragraphs before trailing definitions survive. Lists nested past nine levels
  warn. Comment, image and Drive commands no longer exit 1 after Google saved the
  change when local state cannot be written. `push` accepts a pulled file whose
  tab fingerprint matches without local state. `cat --no-images` keeps code text.
- Loose nested numbered lists retain parent numbering. Reference links retain
  destinations; quoted lists, quoted code, quoted tables, and fences or tables
  indented inside list items preserve their structure through changed Markdown
  round trips.
- Native rules sharing a paragraph preserve surrounding text, escaped comment
  anchors match correctly, and backticks in link destinations round-trip.
- A failed display-version lookup no longer hides an acknowledged write. Image
  and suggestion commands carry exact acknowledged revision provenance forward.
- Tab replacements reset inherited bullets and direct paragraph/text styles;
  nested list items share a list-creation range and final rules use the retained
  paragraph mark without adding a blank paragraph. `insert --end` applies the
  same rule handling, so an appended `---` no longer leaves a blank paragraph.
- Unchanged single-tab `write`/`push` uploads skip reconstruction even without
  a conflict and report that nothing was written.
- A table data row of dashes stays a row; one blank line separates adjacent
  tables. A tab starting with a rule reads with an empty metadata block first,
  so `write` keeps the rule and the text after it.
- `edit` and `insert` create and maintain code and container ranges, and tab
  replacements remove gdoc's old ones. Loose numbered lists nested under
  bullets keep their numbering, a list item containing a rule stays one item,
  and custom first-level list indents no longer read as nesting.
- An indented paragraph's heading style and literal punctuation survive inside
  its quote. Cells aligned unlike their column header need loss consent.
- A table directly beside a heading, list item, quote or rule keeps those
  paragraphs' styles and list membership through rewrites and `insert --end`;
  removing a final paragraph, or one before a table, restores the paragraph
  before it. A numbered list continues across a table or image.
- Inserted Markdown no longer inherits a neighboring quote indent, rule border,
  bold, link or code style, and a tab holding only a rule or empty heading keeps it.
- Images whose alt text has backticks or line breaks, list items of dashes,
  escaped backticks in table cells, non-space cell edges and definition-shaped
  links survive rewrites. Tables separated by a blank line in another container
  no longer gain a paragraph per rewrite.
- An edit inside a link's label keeps the link. `--allow-lossy` rewrites a tab
  with section breaks as one section. Table shading, borders and fixed widths
  produce the style warning. `comment --quote` prefers its own letter case.
- A pulled file stays pushable after a collaborator edits another tab. An
  unreadable add-tab reply is reported as an uncertain outcome (still an
  `ERR:` with exit 1) that says to list the tabs before retrying, instead of
  a generic unexpected-response error.
- Replacement wording inside code is literal text. Markdown reads show a tab
  without its pending suggestions and say how many are pending, so a consented
  rewrite discards suggestions instead of applying them. A read whose preview
  gdoc cannot render reliably is reported as incomplete.
- A quote inside a list item stays in that item, including under nested items;
  its container range records the item's indent (`gdoc:prefix:v2`).
- Leading tabs of code inside list items survive bullet creation, and restored
  content tabs keep their own style. A blank line between a container and a
  table stays outside code. Empty scaffolding and emptied paragraphs are removed
  one paragraph per deletion, the shape observed to keep the following
  paragraph's style, so two different lists around a table keep their identity.
- An empty replacement across a paragraph break joins the paragraphs in the
  first paragraph's style. Appending a table or trailing blank line leaves a
  plain final paragraph. Wording that loses its link loses the default link
  colour and underline, and a link with mixed styles exports as one link.
- A soft break before `=== Tab:` text and a rule before a final empty code block
  can be written back. Zero-width table borders produce the style warning.
- `edit` and `insert` record an acknowledged write before the optional version
  lookup. Markdown `export` and `pull` name the tab they read. Sync hooks
  report skips to the agent, identical local replacements leave the file in
  place, and comment anchors no longer match through escapes in code.


## [0.22.0] — 2026-09-28

### Added
- **List nesting: `gdoc nest DOC TEXT` and `gdoc unnest DOC TEXT`.** Move a
  list item, or a range of items (`--to TEXT`), with their sub-items by
  `--levels N` (default 1), like Tab and Shift-Tab in Google Docs. The items
  are rebuilt in place in one `requiredRevisionId`-pinned batch so they rejoin
  their own native list: the list ID, a UI-restarted start number, text and
  marker styles, paragraph spacing, heading IDs, comment anchors and
  bookmarks are kept, and nothing else in the tab is rewritten. The result
  is read back, with the moved items found by their text so edits elsewhere
  in the tab do not matter; an item not at its planned level, list, marker,
  paragraph style or indent, a blank line whose indent changed, or a
  changed paragraph just before or after the moved items is reported as a
  warning with `"verified": false` (the change is saved, exit 0).
  Refused before any write (exit 3):
  - ambiguous or missing text, non-list or in-table targets;
  - moves that would rebuild a list's first item, skip a level, or unnest past
    the top level;
  - checkbox and custom-glyph lists, and numbered presets other than
    `1. a. i.` (for example `1) a) i)`);
  - items that start with a tab, carry a hand-set indent, hold pending
    suggestions, anchor a floating image or drawing, or overlap a named range;
  - items whose bullet or number has its own formatting (anything but the
    bold, font and size of a fully formatted item), or a style covering the
    whole item but not its marker;
  - moves that would merge, split or re-home lists (ranges across lists, items
    right after another list's item, an unnest that would sweep in a deeper
    item of another list, sub-items that are a separate list).

  A 5xx or dropped connection on the write says the outcome is unknown, a
  stale-revision error is checked against the document before it says
  "re-run", and failures after a saved write are warnings, so a caller never
  retries a change that was applied. Honors `--tab` and `--account`; exposed
  over MCP as `gdoc_nest` and `gdoc_unnest` (write tools).

## [0.21.1] — 2026-09-28

### Fixed
- **A stale pulled file can no longer overwrite newer edits.** Conflict
  detection compared the doc against the version this machine last read,
  and a pulled file recorded no version of its own. So `gdoc pull DOC
  draft.md` at version 1, a collaborator's edit (version 2), and any
  `gdoc cat DOC` on this machine let `gdoc push draft.md` delete the
  collaborator's edit. `pull` now stamps the file with `gdoc-version:
  <Drive version>` (read before the export, so the stamp is never newer
  than the content; `pull --revision` files get no stamp), and `push`,
  `write DOC FILE` (when the file's `gdoc:` names DOC), the sync hook,
  and MCP `write` use that stamp as the baseline: a mismatch refuses with
  exit 3 unless `--force` or the doc already equals the file. The refusal
  sends nothing, leaves the file untouched, and names a recovery that
  pulls into a new path (`gdoc pull DOC draft.latest.md`, then `gdoc diff`),
  since re-pulling the refused file would overwrite its edits. The sync
  hook reports this refusal with exit 2 so Claude Code shows it to the
  agent; its other skips stay silent. After an
  upload the stamp advances to the version in the upload response; a tab
  write, which reports no version of its own, leaves the stamp and warns.
  The advance writes a sibling file and swaps it in, so a failed write
  never truncates the source. The pull hook stamps what it pulls; when a
  stamped file is behind the doc it refreshes the file only if the file
  already matches the doc, and otherwise leaves it untouched and exits 2
  with the same recovery steps. Unstamped files keep the old rule.

## [0.21.0] — 2026-08-26

### Added
- **Suggested edits: `gdoc suggest DOC OLD NEW`.** The same find-and-replace
  as `edit` (`--all`, `--case-sensitive`, `--normalize`, `--tab`,
  `--old-file`/`--new-file`, `-` for stdin), but the batchUpdate runs with
  `writeControl.writeMode: SUGGEST` (Docs API Developer Preview), so the
  change lands as a pending suggestion with an accept/reject control and
  the original text stays until a reviewer accepts it. Output names the
  review object (`OK suggested 1 occurrence (#suggest.abc)`; `--json`
  reports `suggestionIds`, `createdSuggestionIds`, `updatedSuggestionIds`).
  Success is verified, not assumed: HTTP 200 plus `commentUpdateState:
  ALL_SAVED`, at least one suggestion ID in `suggestionResponses`, and a
  `SUGGESTIONS_INLINE` read-back that shows every ID — anything less is an
  error, and there is deliberately no fallback to a direct edit: a
  non-mutating preview-only read proves the project is enrolled *before*
  the write (an unenrolled project fails with `suggest mode not
  available`). Inline style ranges in the replacement are converted to
  UTF-16 (`to_docs_requests`, also fixing `edit`/`insert` with emoji in
  formatted replacement text). The document
  is read with suggestions inline before matching and a match that touches
  an existing suggested insertion, deletion, or style change is refused, so
  another reviewer's thread is never modified by accident. Replacement text
  may use inline Markdown only (bold, italic, strikethrough, code, links);
  headings, lists, blockquotes, horizontal rules, tables, and `--cell` are
  rejected before any API call. Needs comment or edit access on the doc.
  Exposed over MCP as `gdoc_suggest` (a write tool). `edit` and `suggest`
  share one text-resolution/matching front half (`_resolve_replacement_text`,
  `_prepare_text_replacement`) and one request builder
  (`_build_replacement_requests`); `edit` behaviour is unchanged.

### Fixed
- **`edit --all` with a self-overlapping anchor** (`aa` matching twice in
  `aaa`) is refused before any write (exit 3) instead of corrupting the
  document — the last-to-first delete/insert plan would land on
  already-shifted text. The same guard `suggest` ships with.
- **`edit --cell` on a cell containing characters outside the Basic
  Multilingual Plane** (emoji) computed the cell's editable end in code
  points instead of UTF-16 units, so the replacement range could split a
  surrogate pair (a 400 from the API) or leave the cell's last character
  behind.
- **MCP: a literal `-` in `old_text`/`new_text` of `gdoc_edit` and
  `gdoc_suggest` is rejected.** The CLI reads `-` from stdin, but over MCP
  stdin is the JSON-RPC stream (shielded to empty for the call), so
  `new_text: "-"` silently became an empty replacement — deleting the
  matched text instead of erroring.
- **MCP: an unpinned tool call resolves the configured default account
  once at call entry** instead of per service access, so a
  `gdoc auth --set-default` made while a command runs can no longer hand
  the same command's read and write to different accounts.

## [0.20.1] — 2026-08-15

### Changed
- **Per-request credential injection.** The active account is now a
  `contextvars.ContextVar` scoped per call (`account_context()`), and the
  four cached API service objects are keyed by the resolved account
  instead of one-per-process, with `get_credentials()` accepting the
  resolved account so a cache key can never disagree with the credentials
  behind it. Two accounts used concurrently in one process each see only
  their own credentials and services, an unpinned call in a long-lived
  process picks up `gdoc auth --set-default` changes at call time, and the
  token file's on-disk identity travels in the cache key so a token
  re-authenticated or removed in another terminal is never served from a
  stale cached service. The CLI
  behaves identically; the MCP server's account-reset machinery collapses
  into one `account_context` per tool call. Groundwork for the hosted
  multi-user server (#45).

## [0.20.0] — 2026-08-14

### Added
- **MCP server: `gdoc mcp`.** Serves gdoc over the Model Context Protocol
  on stdio, so clients that launch a local server (Claude Desktop, the
  Codex CLI) can read and edit Docs without shell access —
  previously gdoc was only reachable from a coding agent. 29 subcommands
  are exposed as tools, with input schemas derived from the argparse
  parser so new flags surface automatically. `--read-only` restricts the
  surface to commands that cannot modify Docs or Drive, `--allow` takes
  an explicit subset (`GDOC_ALLOW_COMMANDS` is honoured too), and
  `--account` sets the account for every call (an explicit `account`
  argument on a call wins). `write`, `insert`, and `new` take markdown
  content as inline `text`, since a chat client has no filesystem to
  write a markdown file to; parameters that name local files
  (`edit --old-file/--new-file`, `diff FILE`/`--out`/`--format html`,
  `images --download`, `cells --file/--stdin`) are not exposed at all,
  so a prompt-injected model cannot read or write files on the host.
  `diff` reporting "differences found" (CLI exit code 1) is a normal
  result, not a tool error. No new dependencies: the stdio transport is
  newline-delimited JSON-RPC 2.0, implemented in `gdoc/mcp.py`. Commands
  run in-process through the same dispatch as the CLI, factored out of
  `main()` as `run_argv()`; each call runs with stdin detached so a
  stdin-reading command can never swallow the protocol stream.

### Fixed
- Markdown file reads and writes (`write`, `insert`, `new --file`,
  `push`, `pull`, sync hooks) now use explicit UTF-8 instead of the
  locale default encoding.

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
