# gdoc

A token-efficient CLI for AI agents to read, write, and collaborate on Google Docs.

`gdoc` gives AI coding agents (Claude Code, Cursor, Codex, etc.) a simple command-line interface to Google Docs and Drive. Every command is designed to minimize token usage while providing the context agents need — change detection banners, conflict prevention, structured output modes, and inline comment annotations.

## Install

`gdoc` is installed via [uv](https://github.com/astral-sh/uv). If you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

See the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/) for other options (Homebrew, pipx, Windows PowerShell).

Then install `gdoc`:

```bash
uv tool install git+https://github.com/LucaDeLeo/gdoc.git
```

Or from a local clone:

```bash
git clone https://github.com/LucaDeLeo/gdoc.git
cd gdoc
uv tool install .
```

## Updating

```bash
gdoc update
```

`gdoc` also keeps itself fresh: running bare `gdoc`, `gdoc --help`, or `gdoc -h` upgrades to the latest release before printing help, so agents inspecting the CLI surface always see current help text. This only applies to `uv tool` installs, checks at most once per hour, and silently skips on any failure (offline, install error). Set `GDOC_AUTO_UPDATE=0` to disable it.

Other commands never auto-update — they print a notice to stderr (at most once per day) when a newer version is available.

## Setup

1. Create a project in the [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the **Google Drive API** and **Google Docs API**
3. Create **OAuth 2.0 credentials** (Desktop application type)
4. Download the credentials JSON and place it at `~/.config/gdoc/credentials.json`
5. Authenticate:

```bash
gdoc auth
```

This opens a browser for the OAuth flow. Use `--no-browser` for headless environments (prints a URL to visit manually).

For multiple Google accounts, authenticate each named account with
`--account`:

```bash
gdoc auth --account pete@example.com
gdoc auth --account work@example.com
```

The first named account you authenticate becomes the default for bare
`gdoc` commands. To change it later without reauthenticating:

```bash
gdoc auth --set-default pete@example.com
```

### Org-wide setup (shared OAuth client)

For company rollouts, an admin creates **one** Google Cloud project with an
**Internal** OAuth consent screen and a Desktop-app OAuth client, then
distributes that client file so users never touch the Cloud Console. Each
user authenticates with one command. `gdoc` accepts the client config from
any of these sources (first match wins):

1. `GDOC_CLIENT_ID` + `GDOC_CLIENT_SECRET` — env vars (set via MDM/dotfiles)
2. `GDOC_CLIENT_CREDENTIALS` — path to an OAuth client JSON file
3. `~/.config/gdoc/credentials.json` — the default location

To fetch the client file from an internal URL and authenticate in one step:

```bash
gdoc auth --setup-url https://internal.example.com/gdoc-credentials.json
```

If `GDOC_SETUP_URL` is set, plain `gdoc auth` fetches from it automatically
when no client config is present yet — so with env vars pre-set, onboarding
is just `uv tool install gdoc && gdoc auth`.

Pass `--domain company.com` (or set `GDOC_AUTH_DOMAIN`) to pre-filter the
Google account chooser to your Workspace domain so users don't accidentally
pick a personal account. This is a UI hint only — domain enforcement comes
from the Internal consent screen.

## Quick start

```bash
# List files in Drive root
gdoc ls

# Search for a document
gdoc find "quarterly report"

# Read a document as markdown
gdoc cat DOC_ID

# Read with byte limit (UTF-8-safe truncation)
gdoc cat --max-bytes 5000 DOC_ID

# Read a specific tab
gdoc cat --tab "Notes" DOC_ID

# Read all tabs
gdoc cat --all-tabs DOC_ID

# List tabs in a document
gdoc tabs DOC_ID

# Read with inline comment annotations
gdoc cat --comments DOC_ID

# Get document metadata
gdoc info DOC_ID

# Find and replace text (supports markdown formatting)
gdoc edit DOC_ID "old text" "**new bold text**"

# Same replacement, but as a suggested edit for a human to accept
gdoc suggest DOC_ID "old text" "new text"

# Overwrite a document from a local file
gdoc write DOC_ID draft.md

# Create a new blank document
gdoc new "Meeting Notes"

# Create a document from a local markdown file (with image support)
gdoc new "Report" --file report.md

# Duplicate a document
gdoc cp DOC_ID "Copy of Report"

# List images, charts, and drawings
gdoc images DOC_ID

# Download images to a local directory
gdoc images --download /tmp/imgs DOC_ID

# Export a rendered PDF/DOCX/HTML file
gdoc export DOC_ID --out report.pdf

# Insert an image into an existing doc
gdoc insert-image DOC_ID diagram.png --after "Architecture"

# Replace an image's content in place (IDs from `gdoc images`)
gdoc replace-image DOC_ID kix.abc123 diagram-v2.png

# Read a spreadsheet (markdown table; --plain for TSV)
gdoc cat SHEET_ID

# Read a specific worksheet / range
gdoc cat --tab "Data" --range B2:D10 SHEET_ID

# Write cell values to a spreadsheet
gdoc cells SHEET_ID B2 -v "Yes"
gdoc cells SHEET_ID A1 --file rows.csv
```

All commands accept a full Google Docs/Sheets URL or a bare document ID:

```bash
gdoc cat https://docs.google.com/document/d/1aBcDeFg.../edit
gdoc cat 1aBcDeFg...
```

## Commands

### Reading

| Command | Description |
|---------|-------------|
| `cat DOC` | Export document as markdown (or `--plain` for plain text, `--max-bytes N` to truncate) |
| `cat --tab NAME DOC` | Read a specific tab by title or ID |
| `cat --all-tabs DOC` | Read all tabs with headers |
| `cat --comments DOC` | Line-numbered content with inline comment annotations |
| `cat SHEET` | Print a spreadsheet as a markdown table (`--plain` for TSV, `--range A1:C10` for a slice; `--tab`/`--all-tabs` select worksheets) |
| `tabs DOC` | List all tabs in a document (or worksheets in a spreadsheet) |
| `info DOC` | Show title, owner, modified date, word count (tab list for spreadsheets) |
| `ls [FOLDER]` | List files in Drive root or a folder (`--type docs\|sheets\|all`) |
| `images DOC` | List images, charts, and drawings (`--download DIR` to save locally) |
| `find QUERY` | Search files by name or content (`--raw` to pass a full [Drive query](https://developers.google.com/workspace/drive/api/guides/search-files) verbatim) |
| `drives` | List shared drives |
| `export DOC --out FILE` | Render to `pdf`, `docx`, `odt`, `epub`, `html`, `md`, `txt`, or `rtf` (format inferred from the extension, or `--format`; all tabs included) |
| `structure DOC` | Native document JSON — styles, tables, tab topology, UTF-16 index ranges (`--tab` to narrow, `--fields` for a raw field mask, `--suggestions-view-mode` to pick the suggestions rendering) |

### Writing

| Command | Description |
|---------|-------------|
| `edit DOC OLD NEW` | Find and replace text with Markdown formatting, including text inside tables (`--all` for all; `--normalize` to match through smart quotes/dashes; `-` reads an argument from stdin) |
| `edit DOC --cell ADDR NEW` | Replace a table cell by label or `ROW,COL` coordinates (`--col`, `--table`) |
| `suggest DOC OLD NEW` | Same find-and-replace as `edit`, made as a **suggested edit** the doc's reviewers accept or reject (same `--all`/`--normalize`/`--case-sensitive`/`--tab`/`--old-file`/`--new-file`/`-` flags; inline Markdown only — see below) |
| `nest DOC TEXT` / `unnest DOC TEXT` | Move a list item (and its sub-items) one level in or out, in place; `--to TEXT` for a range of items, `--levels N`, `--tab` — see below |
| `write DOC FILE` | Overwrite document from a local markdown file |
| `cells SHEET RANGE` | Write values into a spreadsheet range (`-v VALUE` per cell, `--file rows.csv`, `--stdin` for TSV; `--append` adds rows, `--user-entered` parses formulas/dates) |
| `new TITLE` | Create a blank document (`--folder` to specify location, `--file` to import markdown with images) |
| `insert-image DOC IMG` | Insert a local image or public URL (`--after TEXT`, `--index N`, or `--end`; `--tab` for multi-tab docs; `--width`/`--height` in points) |
| `replace-image DOC ID IMG` | Swap an image's content in place, keeping its size (IDs from `gdoc images`) |
| `cp DOC TITLE` | Duplicate a document |

### Revisions & diffs

| Command | Description |
|---------|-------------|
| `revisions DOC` | List retained revisions — id, modified time, author, `[keep]` marker (`--limit N`; alias: `history`) |
| `cat --revision REV DOC` | Export a past revision to stdout |
| `pull --revision REV DOC FILE` | Download a past revision (gets `source:`/`revision:` frontmatter, not `gdoc:`, so it can't be pushed back by accident) |
| `diff DOC FILE` | Compare the current doc against a local file (unified diff) |
| `diff DOC --rev A..B` | Word-diff two revisions (`--rev A` compares A against latest) |
| `diff DOC --since ISO` | What changed since a timestamp (last revision at/before it vs latest) |
| `diff DOC --rev A..B --format html` | Write a styled diff artifact (`--out PATH`, `--with-comments` to anchor comment threads) |

### Comments

| Command | Description |
|---------|-------------|
| `comments DOC` | List all open comments (`--all` to include resolved) |
| `comment DOC TEXT` | Add a comment (`--quote` to anchor it to text — see below) |
| `comment-info DOC ID` | Get a single comment with full detail |
| `reply DOC COMMENT_ID TEXT` | Reply to a comment |
| `resolve DOC COMMENT_ID` | Resolve a comment (`--message` to include a note) |
| `reopen DOC COMMENT_ID` | Reopen a resolved comment |
| `delete-comment DOC ID` | Delete a comment (`--force` to skip confirmation) |

### Suggestions (Docs API developer preview)

| Command | Description |
|---------|-------------|
| `suggestions DOC` | List open suggestion threads with author, summary, and the tab/UTF-16 range(s) each touches (`--all` to include accepted/rejected) |
| `suggestion-info DOC ID` | One suggestion thread in full (`--json` returns the raw thread plus derived `locations`) |
| `accept-suggestion DOC ID` | Accept a suggested edit (requires edit access) |
| `reject-suggestion DOC ID` | Reject a suggested edit (edit access, or the suggestion's author) |
| `delete-suggestion DOC ID` | Delete a suggestion thread you authored (`--force` to skip confirmation) |

These read Google's native suggestion threads (`documents.get` with
`commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED`) and send one
`acceptSuggestion`/`rejectSuggestion`/`deleteSuggestion` request per command.
`accept-suggestion` is always pinned to the revision that was just read (it
needs edit access, which is also what Google requires for a `revisionId`);
`reject-suggestion`/`delete-suggestion` are pinned whenever the read returned a
revision and are sent unpinned only when it did not (a suggestion's author may
be a commenter). Unlike comments there is **no Drive fallback**: the OAuth client's Cloud project must be enrolled in the
[Workspace Developer Preview Program](https://developers.google.com/workspace/preview),
otherwise the commands fail with an explicit "not enrolled" error (exit 1) and
nothing is changed. A decision is only reported as `OK` after a read-back shows
the thread in the requested state; accepted and rejected threads stay listed
under `--all` with that status, deleted ones disappear. Suggestion threads carry
no range of their own, so the `@Tab start-end kind` lines (and `locations` in
`--json`) are derived from the `SUGGESTIONS_INLINE` document structure keyed by
suggestion ID — they are kept separate from the raw thread. Header, footer,
and footnote ranges are labelled with their segment ID (their indexes restart
at 0); marks with no range at all (document/named styles) print `(no range)`.

`comment --quote "some doc text"` anchors the comment to the first occurrence
of that text (all tabs are searched). When the OAuth client's Cloud project is
enrolled in the
[Google Workspace Developer Preview Program](https://developers.google.com/workspace/preview),
this creates a **real anchored comment** via the Docs API `insertComment`
request — highlighted in the Docs UI exactly like a comment made by hand
(`OK comment #ID (anchored)`; `"anchored": true` in `--json`). Without preview
access (or with comment-only permission on the doc, which can't `batchUpdate`),
or when the quoted text isn't found in the document, it falls back
transparently to the Drive API path: the comment is created unanchored
(`anchored: false` in `--json`/`--plain`) with the quote stored as
`quotedFileContent` metadata, which `cat --comments` places by matching that
text but the Docs UI does not highlight. Same command either way — anchoring
problems never fail the comment (though unrelated API errors, like a missing doc or
expired auth, still do).

### Other

| Command | Description |
|---------|-------------|
| `auth` | Authenticate with Google (`--no-browser` for headless) |
| `share DOC EMAIL` | Share a document (`--role reader\|writer\|commenter`; `--no-notify` skips Google's notification email) |
| `share DOC --domain D` / `--anyone` | Link-based sharing with a Workspace domain or anyone with the link (`--discoverable` to also surface in search) |
| `mkdir TITLE` | Create a Drive folder (`--parent FOLDER`) |
| `mv DOC FOLDER` | Move a file into a folder (alias: `move`) |
| `rename DOC TITLE` | Rename a file |
| `mcp` | Serve gdoc to desktop chat apps over MCP (`--read-only`, `--allow`) |
| `update` | Update gdoc to the latest release |

## Desktop chat apps (MCP)

`gdoc mcp` runs gdoc as a [Model Context Protocol](https://modelcontextprotocol.io)
server on stdio, so clients that launch a local server — Claude Desktop,
the Codex CLI, and others — can use gdoc without shell access.
Each supported subcommand becomes a tool (`gdoc_cat`, `gdoc_edit`, …),
with its parameters derived from the CLI itself.

Authenticate first — the server cannot open a browser for the OAuth flow:

```bash
gdoc auth
```

**Claude Desktop** — add to `claude_desktop_config.json` (Settings →
Developer → Edit Config), then restart the app:

```json
{
  "mcpServers": {
    "gdoc": {
      "command": "gdoc",
      "args": ["mcp"]
    }
  }
}
```

If the app can't find `gdoc` on its PATH, use the absolute path from
`which gdoc`.

**Codex CLI** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.gdoc]
command = "gdoc"
args = ["mcp"]
```

ChatGPT desktop itself only connects to *remote* MCP servers over HTTPS,
so it cannot launch `gdoc mcp` directly.

Useful flags:

```bash
# Reading only — nothing that can modify a Doc or Drive is exposed
gdoc mcp --read-only

# Expose a specific subset
gdoc mcp --allow cat,find,comments,comment

# Default account for every tool call (an explicit `account`
# argument on a call still wins)
gdoc mcp --account work
```

If `GDOC_ALLOW_COMMANDS` is set in the environment the client launches
the server with, it restricts the tool surface too — and must include
`mcp` for the server to start at all.

How the tools differ from the CLI:

- `write`, `insert`, and `new` take markdown content as inline `text`
  instead of a local file path.
- Parameters that name local files (`edit --old-file/--new-file`,
  `diff FILE`/`--out`, `images --download`, …) are not exposed: a chat
  client cannot see the server's filesystem, and hiding them keeps a
  prompt-injected model from reading or writing files on the host.
- `auth`, `update`, `config`, `pull`, `push`, `export`, `insert-image`,
  and `replace-image` are not exposed: they need a browser, change the
  install, or only work on local paths.
- `diff` reporting "differences found" (exit code 1 in the CLI,
  diff-style) is a normal result, not an error.

## Output modes

Every command supports four output modes:

```bash
gdoc info DOC              # terse (default) — compact, human-readable
gdoc info --verbose DOC    # verbose — all fields, full timestamps
gdoc info --json DOC       # json — machine-readable, wrapped in {"ok": true, ...}
gdoc info --plain DOC      # plain — stable TSV, no decoration, suitable for piping
```

The `--json`, `--verbose`, and `--plain` flags are mutually exclusive and can go before or after the subcommand.

Plain mode produces tab-separated output with no headers or decoration. Action commands emit `key\tvalue` pairs; list commands emit one row per item with tab-separated fields.

## Awareness system

`gdoc` tracks per-document state to help agents stay aware of external changes. Before most commands, a **pre-flight check** runs automatically and prints a banner to stderr:

```
--- first interaction with this doc ---
 📄 "Project Spec" by alice@example.com, last edited 2026-02-07
 💬 3 open comments, 1 resolved
---
```

On subsequent interactions:

```
--- since last interaction (12 min ago) ---
 ✎ doc edited by bob@example.com (v4 → v6)
 💬 new comment #abc by carol@example.com: "Should we add error handling here?"
 ✓ comment #def resolved by alice@example.com
---
```

If nothing changed: `--- no changes ---`

### Conflict prevention

The `write` command blocks if the document was modified since your last read:

```bash
gdoc cat DOC               # establishes a read baseline
# ... someone else edits the doc ...
gdoc write DOC draft.md    # ERR: doc changed since last read
gdoc cat DOC               # re-read to update baseline
gdoc write DOC draft.md    # OK written
```

Use `--force` to skip conflict detection. Use `--quiet` to skip pre-flight checks entirely (saves 2 API calls).

Files from `gdoc pull` carry their own baseline. `pull` stamps the file with `gdoc-version: N`, the Drive version its content came from, and `push`, `write DOC FILE`, and the sync hook compare that stamp with the doc's current version instead of this machine's last read. If anyone has edited the doc since the pull, the upload is refused (exit 3), even when a later `gdoc cat` on this machine has seen the newer version. Nothing is sent and the file is left untouched. To recover, pull a fresh copy to a new path (`gdoc pull DOC draft.latest.md`), see what changed with `gdoc diff DOC draft.md`, and carry your edits into the new file before pushing it; `--force` discards the newer changes in the doc. The sync hook reports the same refusal with exit 2, which Claude Code shows to the agent, and the pull hook will not overwrite a stale stamped file that differs from the doc. A successful upload advances the stamp to the version the upload created, so you can push, edit, and push again. Any change to the doc makes the file stale, including edits in other tabs. Files without a stamp (hand-written, or pulled by an older gdoc) keep the read-baseline rule above.

## Spreadsheets

`cat`, `tabs`, and `info` detect Google Sheets automatically — point them at a
spreadsheet URL and they read cell values instead of exporting markdown.
`cat` prints a markdown table by default, TSV with `--plain`, and raw rows
with `--json`; `--tab` selects a worksheet by title or numeric sheet id
(the `gid` in the URL), and `--range` limits output to an A1 range.
Reading defaults to the first worksheet — a stderr hint tells you when more
tabs exist.

Writes go through `gdoc cells`:

```bash
# One row of values, starting at B2
gdoc cells SHEET_ID B2:C2 -v "Y" -v "quote here"

# Bulk rows from a CSV (or TSV) file
gdoc cells SHEET_ID A2 --file rows.csv

# Pipe TSV from another tool
grep done report.tsv | gdoc cells SHEET_ID A2 --stdin

# Append below the existing table; parse values like the UI would
gdoc cells SHEET_ID A1 --append --user-entered -v "=SUM(B:B)"
```

Values are written literally by default (`RAW`); use `--user-entered` for
formulas, dates, and number parsing. The existing OAuth scope already covers
the Sheets API, so no re-authentication is needed.

## Annotated view

`cat --comments` produces line-numbered output with comments placed inline next to the text they reference:

```
     1	# Project Spec
     2
     3	The system should handle up to 1000 concurrent users.
      	  [#abc open] alice@example.com on "up to 1000 concurrent users":
      	    "Is this enough? We had 1500 at peak last month."
      	    > bob@example.com: "Good point, let's bump to 2000."
     4
     5	Authentication uses OAuth2.
```

Comments are placed from their live anchors: the text each comment covers now, as the Docs UI highlights it. This needs the same [Developer Preview](https://developers.google.com/workspace/preview) enrollment as anchored `comment --quote`. A comment whose anchored text is all gone is listed as `[detached]` (Docs shows "Original content deleted"); a `write --tab` that changes the tab detaches every comment in it, resolved ones included. A comment that is still attached is listed as `[attached, location not found]` when gdoc can't pin its text to one markdown line: for example, a comment on an image, on footnote text, or on heading text that a table of contents may repeat. The same label is used when the document kept changing while gdoc read it; a `WARN` says so. Comments that aren't placed inline are grouped in an `[UNANCHORED]` section at the end.

With `--json`, `"anchors"` says where the labels came from:

- `"live"`: live anchors, with comments placed on their lines.
- `"live_no_locations"`: live anchors for `[detached]` status, but no lines, because the document or its comments kept changing during the read (or its version couldn't be read). The status comes from a read just before the text shown, so it may miss an edit made in between.
- `"quoted_text"`: live anchors couldn't be read (see below).
- `"none"`: there were no comments, so nothing was read.

When live anchors can't be read (no preview access, no comment access on the document, or the request fails), gdoc prints a `WARN` and places each comment where its quoted text occurs (`"anchors": "quoted_text"`). Drive never updates a comment's quoted text, so `[quoted text found]` is a location guess, not proof the comment is still attached, and `[quoted text not found (edited or detached)]` covers both a reworded anchor and a detached comment. If comments keep being added or removed during the read, a `WARN` says so and no comment is placed.

## Revision history & diffs

Google Docs' "Version history" UI has no public API, but the Drive API exposes **milestone revisions** for native Docs, and each one is exportable. Two caveats baked into the tooling: revision ids are **sparse** (1, 3, 7, 20, …), and non-pinned revisions are **pruned by Google over time** — so `gdoc revisions` is the starting point, and a pruned revision produces a clear error pointing back to it.

```bash
# List retained revisions (oldest first; [keep] = pinned forever)
gdoc revisions DOC_ID

# What changed in the most recent edit?
gdoc diff DOC_ID --rev prev

# What changed since I last read it?
gdoc diff DOC_ID --since 2026-06-10T19:00:00Z

# Compare two specific revisions, chunkier word-diff
gdoc diff DOC_ID --rev 69..190 --min-common 30

# Styled artifact with the doc's comment threads anchored inline
gdoc diff DOC_ID --rev 69..190 --format html --with-comments --out review.html

# Read or download a past revision
gdoc cat DOC_ID --revision head~2
gdoc pull DOC_ID old-draft.md --revision @2026-06-01
```

**REV selectors** (shared by `cat`, `pull`, and `diff`): a bare revision id (`190`), `latest`/`head`, `prev`, `head~N` (N back from latest by list position), or `@ISO` (last revision at/before the timestamp; naive timestamps are local time).

Revision diffs print a colored word-diff to a TTY (plain text when piped; `--format` overrides). Rewritten sentences render as one contiguous removed/added chunk rather than word salad — shared scraps shorter than `--min-common` characters (default 24) are absorbed into the change. `--context N` controls how many unchanged blocks are kept around each change; the rest collapse to `⋯ N unchanged ⋯` (headings always stay). `--json` emits the documented diff model (`doc`/`old`/`new`/`hunks`, each hunk a list of `equal|del|ins` runs, plus `comments` with their anchored hunk index when `--with-comments` is set) wrapped with top-level `ok` and `identical` keys; combined with `--format html` it instead prints a JSON write confirmation (`path`, `format`, `changed`, `identical`). Exit code follows `diff DOC FILE`: 1 when the revisions differ, 0 when identical.

The diff model is display-oriented, not a faithful character diff: export escaping and whitespace are normalized, images become `⟦diagram⟧` placeholders, ordered-list renumbering alone doesn't register as a change, and coalescing relabels short unchanged spans as part of the surrounding change (pass `--min-common 0` for the uncoalesced word diff). The engine also parses Google's current markdown-export conventions (one line per paragraph, `![][imageN]` references) — like revision pruning, this is undocumented Google behavior that may change.

HTML output has no extra dependencies. Richer artifacts (docx, PDF, …) are deliberately not built in: `--json` emits the full diff model (including comment anchoring and list markers), and an external script or the calling agent renders it however it likes.

## Tabs

Google Docs supports multiple tabs per document. The default `cat` command uses Drive export which only returns the first tab. Use `--tab` or `--all-tabs` to read tab content via the Docs API:

```bash
# List tabs in a document
gdoc tabs DOC
# t.0	Tab 1
# t.abc	Notes

# Read a specific tab by title (case-insensitive) or ID
gdoc cat --tab "Notes" DOC

# Read all tabs with headers
gdoc cat --all-tabs DOC
# === Tab: Tab 1 ===
# ...content...
# === Tab: Notes ===
# ...content...
```

`--tab` and `--all-tabs` are mutually exclusive with `--comments`. They work with `--json` and `--plain`.

## Byte truncation

Use `--max-bytes` on `cat` to limit output size. Truncation is UTF-8-safe (never splits a multi-byte character):

```bash
gdoc cat --max-bytes 5000 DOC   # first ~5KB of content
```

Works with all `cat` modes: default, `--tab`, `--all-tabs`, `--comments`. In `--json` mode, truncation applies to the content field, not the JSON envelope.

## Native table insertion

`edit` supports markdown tables in replacement text. Tables are inserted as native Google Docs tables:

```bash
gdoc edit DOC "placeholder" "| Name | Score |
|------|-------|
| Alice | 95 |
| Bob | 87 |"
```

Tables require a single match — use without `--all` when the replacement contains a table.

## Editing inside tables

`edit` searches and replaces text inside table cells, not just plain paragraphs. For label/value grids (a label in one column, the value in the next), address a cell directly instead of anchoring on its current text:

```bash
# Replace the cell to the right of a label
gdoc edit DOC --tab "Tab 1" --cell "Discussion topics from JP" "Show and tell; Q2 planning"

# Address by ROW,COL coordinates (0-based) within the Nth table (--table, default 0)
gdoc edit DOC --cell 7,1 "new value"

# --col overrides which column to write (default: the one right of the label)
gdoc edit DOC --cell "Status" --col 2 "Done"
```

Cell edits preserve the cell's paragraph structure; an empty cell is filled in place. The replacement supports the same Markdown formatting as a normal `edit`.

### Matching tolerance

By default matching is exact. If an anchor isn't found, `edit` explains why — most often a smart-quote apostrophe (`’` vs `'`) or a line break where the anchor had a space. Pass `--normalize` to match through smart-quote and dash differences:

```bash
gdoc edit DOC "JP's job" "JP's role" --normalize   # matches "JP's job" in the doc
```

### Multi-line arguments from stdin

Pass `-` for the old or new argument to read it from stdin (one stream, so at most one `-`):

```bash
printf 'line one\nline two' | gdoc edit DOC --cell "Notes" -
```

## Suggesting edits

`suggest` is `edit` in suggest mode: the same anchor matching, but the change is
written with the Docs API's `writeControl.writeMode: SUGGEST`, so the original
text stays in place and the replacement appears as a pending suggestion with an
accept/reject control — exactly as if a reviewer had typed it in *Suggesting*
mode.

```bash
gdoc suggest DOC "ship in Q3" "ship in Q4"
gdoc suggest DOC "colour" "color" --all --case-sensitive
gdoc suggest DOC "old wording" "**bold** with a [link](https://example.com)"
gdoc suggest DOC --tab "Draft" "old" "new"
gdoc suggest DOC --old-file before.txt --new-file after.txt
gdoc suggest DOC "old" "new" --json
# → {"ok": true, "suggested": 1, "suggestionIds": ["suggest.abc"],
#    "createdSuggestionIds": ["suggest.abc"], "updatedSuggestionIds": []}
```

Terse output names the review object: `OK suggested 1 occurrence (#suggest.abc)`.
`--plain` prints `id`, `status suggested`, `suggested N`, and `suggestion_ids`.
Google may fold an edit adjacent to your own open suggestion into it instead of
creating a new one; those IDs are reported under `updatedSuggestionIds`.

Requirements and limits:

- **Developer Preview.** Suggest mode is a Docs API
  [Workspace Developer Preview](https://developers.google.com/workspace/preview)
  feature gated by the OAuth client's Cloud project. Because an unenrolled
  backend has been seen silently applying `writeMode: SUGGEST` as a direct
  edit, `suggest` first proves enrollment with a non-mutating preview-only
  read (`commentsViewMode`); with an unenrolled project that read is rejected,
  the command fails with `suggest mode not available`, and nothing is written.
  Unlike `comment --quote`, there is **no fallback**: `suggest` never degrades to
  a direct edit.
- **Comment or edit access.** The write is pinned to the revision the text was
  matched at (`requiredRevisionId`) and the document is read with suggestions
  inline; both editors and commenters get that view and the revision ID, while
  a reader is refused (`Permission denied`). If a read ever comes back without
  a `revisionId`, the command refuses rather than write unpinned.
- **Inline Markdown only.** Bold, italic, strikethrough, inline code, and links
  are suggested along with the text. Headings, lists, blockquotes, horizontal
  rules, tables, and `--cell` are rejected before any API call — use `edit` for
  those. Newlines are fine: they become suggested paragraph breaks, and the new
  paragraphs inherit the anchor paragraph's style (unlike `edit`, which resets
  inserted paragraphs to normal text). Fenced code blocks are accepted as
  code-font paragraphs.
- **No overlap with existing suggestions.** The document is read with
  suggestions inline; a match that touches text someone else has already
  suggested inserting, deleting, or restyling is refused, so a review thread is
  never silently modified.
- **Verified, not assumed.** Success requires `commentUpdateState: ALL_SAVED`,
  at least one suggestion ID in the response, and a read-back showing every ID
  as a pending suggestion. Any other outcome is an error that tells you to
  inspect the document.

Like `edit`, a suggestion is a partial write: the awareness state records the
new document version but does not advance the read baseline.

## Nesting list items

`nest` and `unnest` move list items one level in or out, like pressing Tab or
Shift-Tab in Google Docs. The items keep their native list: same list ID,
so numbering continues, a list restarted at 5 stays at 5, and comments on the
items stay attached. Only the moved items, any deeper items directly above
them, and blank lines between them are rebuilt; the rest of the tab is not
touched.

```bash
gdoc nest DOC "Bravo"                     # Bravo becomes a sub-item of the item above
gdoc unnest DOC "Bravo"                   # and back
gdoc nest DOC "Bravo" --to "Delta"        # every item from Bravo through Delta
gdoc unnest DOC "grandchild" --levels 2   # two levels out
gdoc unnest DOC --tab "Draft" "Bravo" --json
# → {"ok": true, "moved": 1, "levels": -1, "verified": true}
#   (levels is negative for unnest)
```

`TEXT` is matched like `edit` (case-insensitive) and must occur in exactly one
paragraph of the tab, which must be a list item. Sub-items move with their
items. Terse output is `OK nested 1 item by 1 level`; `--plain` prints `id`
and `status updated`.

How it works: the Docs API cannot set a list level directly, so the command
rebuilds the moved items (and, when unnesting below a deeper sibling, that
sibling) in one batch pinned to the revision it read (`requiredRevisionId`),
so the items rejoin their own list at the new level. It then reads the tab
back, finds the moved items by their text (so edits elsewhere in the tab do
not matter), and checks each item's level, list, marker, paragraph style and
indent, each blank line's indent, and that the paragraphs just before and
after the moved items are unchanged.
The change is saved either way, so the command still exits 0; if the result
is not as planned, or cannot be verified, it says so on stderr and reports
`"verified": false` (`--plain`: `verified no`). Check the list then rather
than re-running, which would move the items again. If the document is
edited between the read and the
write, the write is rejected: the command says `re-run it` when the list is
provably untouched, and otherwise that the outcome is unknown and to inspect
the list first (the operation is not safe to repeat blindly).

Refused before any write (exit 3), with a message naming the item:

- text that matches no paragraph or several, is not a list item, or is inside
  a table;
- any move whose rebuilt items would not directly follow an item of their
  own list: nesting a list's first item, unnesting the first items of a list
  that starts indented, or moving the first item after a non-list paragraph
  in a list that continues past it; nesting an item more than one level
  deeper than the item above it; unnesting an item at the top level, or so far that the
  next item would sit two levels below it;
- checkbox lists and lists with custom glyphs (only the default numbered
  `1. a. i.` and bullet `● ○ ■` lists are supported);
- moves that would merge or split lists: a range spanning two lists, an item
  directly after an item of another list (for example a bullet item after a
  numbered sub-list), an unnest whose rebuild would take in a deeper item of
  another list above it, or an item whose sub-items are a separate list;
- items that start with a tab character, carry a hand-set indent, contain
  pending suggestions, have a floating image or drawing anchored to them, or
  overlap a named range;
- items whose bullet or number has its own formatting, which the rebuild
  could change: marker formatting other than the bold, font and size of a
  fully formatted item (those are kept), or a style that covers the whole
  item but not its marker. Items with only some words formatted are fine.

Kept through a rebuild (tested live): list ID and a restarted start number,
text styles and paragraph spacing, heading IDs, comment anchors and
bookmarks, and the bold, font and size of a fully formatted item's marker.

Blank lines between items (loose lists) are kept, with their original
indentation. Like `edit`, this is a partial write: the awareness state records
the new version but does not advance the read baseline.

## Import from file

Create a document from a local markdown file with `new --file`:

```bash
gdoc new "Report" --file report.md
```

Images in the markdown are handled automatically:
- **Remote images** (`https://...`) are inserted directly via URL
- **Local images** are uploaded to Drive temporarily, inserted, then cleaned up
- Supported formats: PNG, JPG, JPEG, GIF, WebP

## Image inspection

List and download images, charts, and drawings embedded in a document:

```bash
# List all images with metadata
gdoc images DOC
# kix.abc  image  "Company Logo"  200x100pt
# kix.def  chart  "Q1 Revenue"    400x300pt
# kix.ghi  drawing  (not exportable)  150x150pt

# Download images to a local directory
gdoc images --download /tmp/imgs DOC
# /tmp/imgs/kix.abc.png
# /tmp/imgs/kix.def.png
# WARN: kix.ghi is a drawing (cannot export)

# Download a specific image by object ID
gdoc images --download /tmp/imgs DOC kix.abc
```

Drawings cannot be exported (the Google API exposes no content for them). Charts are rendered as images via their content URI. Downloaded files can be viewed directly by multimodal AI agents.

## Image editing

Add an image to an existing document, or swap one's content in place:

```bash
# Insert after anchor text (two matches = error; use a longer anchor)
gdoc insert-image DOC diagram.png --after "Architecture"

# Append at the end of a tab, with an explicit display size
gdoc insert-image DOC https://example.org/chart.png --tab Notes --end --width 400

# Replace an image's content, keeping its current size (center-cropped)
gdoc replace-image DOC kix.abc123 diagram-v2.png
```

Multi-tab documents require `--tab` so the insert can't land in the wrong
tab. Local files must be PNG, JPG, or GIF — the Docs API rejects WebP, so
gdoc refuses it up front (markdown import via `new --file` still accepts
WebP). Local files are uploaded to Drive as a temporary public-read file
(Google's servers fetch the image by URL), then deleted immediately after
the insert — if that cleanup ever fails, gdoc warns with the file ID
instead of leaving the exposure silent.
## Native structure

`cat` is a prose view; `structure` is the editing model. It dumps the raw
Docs API document JSON so an agent can derive exact native mutation
targets — paragraph styles, table geometry, tab topology, inline objects,
named ranges, and the UTF-16 `startIndex`/`endIndex` values every
`batchUpdate` range needs:

```bash
# Whole document (compact JSON; --verbose to indent)
gdoc structure DOC

# One tab's subtree, plus documentId/revisionId
gdoc structure DOC --tab Notes

# Trim the payload with a raw field mask
gdoc structure DOC --fields 'revisionId,tabs(tabProperties)'

# Render suggestions as accepted/rejected before reading indexes
gdoc structure DOC --suggestions-view-mode preview_suggestions_accepted
```

Two index caveats: Docs indices count UTF-16 code units (an emoji is two
units, a smart chip is one), and the suggestions view mode changes both
content and indexes — the mode used is echoed in the output.

## Command allowlist

Restrict which subcommands are available using `--allow-commands` or the `GDOC_ALLOW_COMMANDS` environment variable. Useful for sandboxing AI agents to read-only operations:

```bash
# Only allow read commands
gdoc --allow-commands cat,ls,find,info,comments cat DOC

# Via environment variable
export GDOC_ALLOW_COMMANDS=cat,ls,find,info,comments
gdoc edit DOC "old" "new"  # ERR: command not allowed: edit
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | API or unexpected error |
| 2 | Authentication error (run `gdoc auth`) |
| 3 | Usage or validation error |

Exception: `gdoc diff` follows `diff(1)` semantics — exit 1 means the contents differ, 0 means identical.

Errors always print `ERR: <message>` to stderr, even in `--json` mode.

## Configuration

All files are stored under `~/.config/gdoc/`:

| File | Purpose |
|------|---------|
| `credentials.json` | OAuth client credentials (from Google Cloud Console) |
| `token.json` | Legacy default OAuth token (created by older `gdoc auth` flows) |
| `accounts/<ACCOUNT>/token.json` | OAuth token for a named account |
| `config.json` | Default account preference and other local configuration |
| `state/<DOC_ID>.json` | Per-document state for change detection |
| `update_check.json` | Cached result of the last update check |

## Development

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest tests/ -v

# Run a single test
uv run pytest tests/test_cat.py -k "test_name" -v

# Lint
uv run ruff check gdoc/ tests/
```

## Changelog

Release notes and upgrade highlights live in [CHANGELOG.md](./CHANGELOG.md).

## License

MIT
