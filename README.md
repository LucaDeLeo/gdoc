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

# Replace the first tab from a local file
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
| `cat DOC` | Read the first tab as editable Markdown (`--plain` for text; `--max-bytes N` explicitly marks partial output) |
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
| `export DOC --out FILE` | Render to `pdf`, `docx`, `odt`, `epub`, `html`, `md`, `txt`, or `rtf` (format inferred from the extension, or `--format`; Markdown exports the first tab, or `--tab NAME`; other formats cover every tab) |
| `structure DOC` | Native document JSON — styles, tables, tab topology, UTF-16 index ranges (`--tab` to narrow, `--fields` for a raw field mask, `--suggestions-view-mode` to pick the suggestions rendering) |

### Writing

| Command | Description |
|---------|-------------|
| `edit DOC OLD NEW` | Find and replace text with Markdown formatting, including text inside tables (`--all` for all; `--normalize` to match through smart quotes/dashes; `-` reads an argument from stdin) |
| `edit DOC --cell ADDR NEW` | Replace a table cell by label or `ROW,COL` coordinates (`--col`, `--table`) |
| `suggest DOC OLD NEW` | Same find-and-replace as `edit`, made as a **suggested edit** the doc's reviewers accept or reject (same `--all`/`--normalize`/`--case-sensitive`/`--tab`/`--old-file`/`--new-file`/`-` flags; inline Markdown only — see below) |
| `nest DOC TEXT` / `unnest DOC TEXT` | Move a list item (and its sub-items) one level in or out, in place; `--to TEXT` for a range of items, `--levels N`, `--tab` — see below |
| `write DOC FILE` | Replace the first tab from Markdown; `--tab NAME` selects another tab and siblings survive |
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
| `diff DOC FILE` | Compare one tab's Markdown, as `cat` and `pull` read it, against a local file's body (unified diff; the file's pulled tab, `--tab NAME`, or the first tab) |
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

`comment --quote "some doc text"` requires a unique match across all tabs
(including child tabs), searching the body, headers, footers and footnotes.
Matches in the quote's own letter case take precedence; other casings count
only when none exist.
Use a longer quote to distinguish repeated text, `--tab TITLE_OR_ID` to
limit the search to one tab, or `--occurrence N` to pick the Nth match in a
stable order (tabs in outline order; within a tab the body, then headers,
footers and footnotes each sorted by segment ID). IDs take precedence over
titles; duplicate titles require an ID. Matching folds typography and Unicode spaces such as NBSP,
while preserving native UTF-16 coordinates. Comments can span non-text objects
within a paragraph, but quotes cannot cross table/cell boundaries.

With the OAuth project's
[Workspace Developer Preview](https://developers.google.com/workspace/preview)
access, the Docs API creates a native highlighted anchor. JSON/plain output
reports `anchored: true` and `tabId`. Only a definite preview or permission
rejection permits an unanchored Drive fallback: output reports
`anchored: false`, `reason: preview_unavailable`, and stderr explains the
rejection. The quote is stored as `quotedFileContent` metadata, without a
native highlight; fallback output omits tab/segment scope because no anchor
was created. Comments without `--quote` report `anchored: false` and
`reason: no_quote` in JSON/plain output.

No normalized match, ambiguity, or missing revision metadata refuses the
comment with **exit 3**. A rejected revision triggers one fresh read and
resolution; another revision rejection also exits 3. Successful anchors and
unanchored comments exit **0**. Uncertain writes (including a lost response,
incomplete save confirmation, or server error) exit **1**: inspect comments
before retrying, since the first write may have saved. Comment creation never
replays a wire request after a lost response and never falls back in that
case. Other API errors exit **1**, authentication errors **2**. Errors retain
the usual `ERR:` stderr format even with `--json`.

### Other

| Command | Description |
|---------|-------------|
| `auth` | Authenticate with Google (`--no-browser` for headless) |
| `share DOC EMAIL` | Share a document (`--role reader\|writer\|commenter`) |
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
  instead of a local file path. `write` of pulled text checks its
  `gdoc-revision` as for a file, but no file is updated afterwards; `cat`
  again before the next write.
- Parameters that name local files (`edit --old-file/--new-file`,
  `diff FILE`/`--out`, `images --download`, …) are not exposed: a chat
  client cannot see the server's filesystem, and hiding them keeps a
  prompt-injected model from reading or writing files on the host.
- `insert-image` and `replace-image` accept HTTP(S) image URLs.
- `auth`, `update`, `config`, `pull`, `push`, and `export` are not exposed:
  they need a browser, change the install, or operate on local files.
  Use `cat` and inline `write` for the same Markdown workflow over MCP.
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

If nothing changed, no banner is printed. Each comment-update category shows at
most three previews, with an omitted count and a command for full details.

### Editable Markdown and revision safety

CLI and MCP share the same handlers and content contract. Read a complete tab,
modify its Markdown, then replace that tab:

```bash
gdoc cat DOC --max-bytes 0 > draft.md
# Edit draft.md, including paragraph, list or table structure.
gdoc write DOC draft.md

# File workflows remember the selected tab in frontmatter.
gdoc pull DOC draft.md --tab Notes
gdoc push draft.md
```

The default is the first tab. `--tab` accepts an exact ID or a unique title;
other tabs, headers, footers and page setup survive a body replacement.
`cat --all-tabs` is an inspection view with tab headers, not a file to write back
as one tab. `structure --heading "Checklist"` and `structure --table 1 --tab Notes`
provide detail for a specific element; complete raw structure remains available.

Complete native Markdown reads establish a baseline for the tabs actually read.
Metadata, truncated output, plain text, annotated comments and structure selectors
do not establish a full-content baseline. Truncation is reported on stderr and in
JSON scope metadata; `--max-bytes 0` retrieves complete content. `cat --json`
also reports `tab_count`. When a tab holds content Markdown cannot show (footnotes,
chips, equations, page breaks, positioned objects, drawings, linked charts,
generated contents, custom named ranges, tables that cannot be pipe tables, or
lists, headings, rules and indented paragraphs inside table cells), `cat`, `pull` and
Markdown `export` name it on stderr, and JSON reports `complete: false` with an
`omitted` list. Such a read records only limited coverage of its revision:
targeted edits and `insert` keep the omitted content and proceed, and a rewrite of
the tab needs `--allow-lossy` to discard it while staying revision-protected. `pull` and Markdown
`export` use the same native serializer as `cat`.

`write` and `insert` accept at most one leading metadata block: `pull` frontmatter
with `key: value` lines, or an empty block of two `---` lines. `write` of a pulled
file (one whose `gdoc` names this document) replaces the tab it was pulled from
when `--tab` is absent, and refuses a different `--tab` or another document;
remove the frontmatter to copy the text elsewhere. It also checks the file's
`gdoc-revision` as `push` does (below), and after an acknowledged write it
updates the file's provenance fields as `push` does, so the next write or push of
the same file is not stale because of it. `write` of a `pull --revision` file needs
`--force`, because it replaces the live tab with older text. A `tab` field without
`gdoc` provenance is ignored. When a tab starts
with a horizontal rule, `cat` and Markdown `export` print that empty block first, so
the rule and the text after it stay content. Keep the empty block when writing such
a read back; `pull` files already carry their own metadata block. Without it, a
leading `---` block is metadata only when its first line (after `#` comments) is a
`key: value` line and so is every unindented line with a colon. Markdown-shaped
lines are not key lines: links, code, tables, list items, quotes, keys wrapped in
emphasis, paths and URLs such as `https://example.com`. Otherwise the block stays
content: `---`, a blank
line, `Note: keep me`, `---` is a rule, a paragraph and a rule. `---`, `Note: keep me`, `---` is
metadata; to start a body that way, write the empty block first or escape the
colon (`Note\: keep me`).

Writes compare that baseline with the native document revision and pin mutations
to the checked snapshot. A collaborator edit requires a fresh read. `--force`
explicitly authorizes replacement from the current snapshot; it never disables the
revision precondition. `--quiet` only suppresses notifications. A successful write
uses the revision acknowledged by Google, so successive own writes normally need
no extra read. Missing acknowledgments or a rebased recovery do not bless unseen
content; a rebased write says so with a stderr warning and `rebased: true` in JSON. An unchanged selected tab returns `already in sync` without mutation.
Matching Markdown is not a read: styles and pending suggestions do not appear in
Markdown, so an `already in sync` result never establishes a new baseline.

`push`, `write` of a pulled file and the sync hook also check the file's own `gdoc-revision`; reading a newer
copy elsewhere cannot authorize an older file. The revision covers the whole
document, so an older file is still accepted when its selected tab's native content
matches the `gdoc-tab-sha256` fingerprint recorded at that revision: edits to other
tabs, including your own push of a sibling tab's file, leave it pushable. A matching
fingerprint also serves as the tab's read baseline, so a pulled file stays pushable
on another machine or after local state is cleared. Any change
to the selected tab itself, including text colour or a pending suggestion, makes the
file stale; `--allow-lossy` does not override that. The fingerprint covers the
tab's text, styles, lists, named ranges, named and document styles, segments and
suggestions. A tab containing images has no fingerprint: Google issues a fresh
temporary image URI on every read, and a replaced image can keep its object ID and
size, so any later revision change makes such a file stale until a fresh pull.
Because a revision string alone is never a read baseline, such a file also needs a
fresh read or `--force` when this machine has no local state for the document,
even at the same revision (for example after moving the file to another machine). Files without revision provenance
need a fresh pull or an explicit `--force`. An acknowledged push, or `write` of a
pulled file, updates only its provenance fields, preserving other frontmatter, and reads the document once more
to fingerprint the written tab; if another edit already landed, the fingerprint is
left empty and the next push needs a fresh pull. `gdoc-body-sha256` records the
last pulled or acknowledged body. The pull hook leaves locally edited files in
place; pull a separate copy to reconcile them. When a hook skips or fails, it
prints the reason on stderr and, for Claude Code hook events, also returns it as
`additionalContext` so the agent sees it.

Local replacements retain the previous file at `FILE.gdoc-backup-UNIQUE-ID` and
print its path. This protects edits racing a pull or provenance update, including
writes through an editor's already-open file handle, even after gdoc finishes. A
concurrent save at the original path takes precedence. gdoc never deletes recovery
copies; remove them after comparing. A replacement with identical content leaves the
file in place without a copy. Replacement needs a filesystem with hard links;
elsewhere it fails before touching the file. A symlinked file is replaced through
its link.

Table creation and filling are revision-protected stages. Partial or uncertain
completion exits 1 and reports completed stages; a clean refusal before mutation
exits 3, including a pinned revision the server refuses. Docs API writes to an
existing document (tab content, edits, suggestions, image insertion and
replacement, tabs and page mode) and every new comment are sent once, and an
uncertain request is never automatically replayed. Replies, resolve/reopen, Drive
or Sheets changes, and the images `new --file` inserts into the document it
creates use the Google client's ordinary transport, which can resend a request
after a lost response. Inspect the document before retrying.

To recover from a partial, uncertain or rebased write, including one that
inserted or replaced images, do not rerun it: read the tab again, with `cat` for
text you write from or `pull` for a pulled file (a `cat` does not refresh a pulled
file's revision), and apply the remaining change to that read. The fresh read shows which
content and images landed and gives each image its current
`gdoc-image:OBJECT_ID`; references from the earlier read may no longer resolve,
so use the new ones. `--force-collapse-tabs` explicitly
removes sibling tabs after replacing the first tab, using revision-pinned native
requests; ordinary writes never collapse tabs.

### Supported Markdown

The [product design contract](docs/CONTRACT.md) defines the intended behavior and
shared CLI/MCP requirements. The implemented capabilities and remaining API gaps
are documented below.

**Paragraphs are lines.** In gdoc's format, as `cat` prints it and `write`,
`insert`, `push` and MCP read it, each line of text is one paragraph and each
blank line is one empty paragraph. `a` on one line and `b` on the next are two
paragraphs, and `a`, a blank line, then `b` puts an empty paragraph between them.
Write each paragraph on a single line and add blank lines only where the
document should have empty paragraphs; a hard-wrapped CommonMark file becomes one
paragraph per line. Inside list items, quotes and around code and tables, the
blank lines shown below are separators, as `cat` prints them. `new --file` is
different: it uses Google's Markdown import, which follows CommonMark paragraphs
(joined lines, blank-line separators) and does not create gdoc's code and
container ranges. To
create a document in gdoc's format, run `gdoc new TITLE`, then `gdoc write DOC
FILE`.

The canonical format supports paragraphs and meaningful blank paragraphs, headings
1–6, bold/italic/strike/inline code, external links, nested bullet and numbered lists,
fenced code, quotes, rules, rectangular tables with column alignment, and inline
images. Use a complete read–modify–write for paragraph splits/merges, section moves,
and row/column changes. Targeted `edit` preserves unrelated native content and does
not silently fall back to rewriting a rich tab.

`edit` and `suggest` search every tab's body, headers, footers and footnotes by
default. `--tab` narrows that search to one tab and its segments. A unique match
can therefore be outside the first tab's body; multiple matches require `--all`,
which applies to the entire selected scope. Use `--tab` to limit a bulk edit.

This is a semantic Markdown format, not complete CommonMark/GFM conformance.
Canonical export may escape punctuation or change fence spelling; code text and
meaningful whitespace survive. A fence's info string (such as `python`) is not
kept, and an empty code block reads back holding one empty line, the paragraph
Docs needs for it. Code blocks use native named ranges to retain their
identity; container ranges retain quotes and list item content nested to any depth
(list nesting itself stops at nine levels, below):
paragraphs, headings, rules, code, tables and quotes inside list items, and lists,
code and tables inside quotes, including a quote inside a quoted list item. Content
inside a list item is written with the item's content indent (`1. item`, blank line,
`   > quoted` or `   more text`); lists quoted there are their own lists, and the
item's list continues after the content. Raw indentation after a list item therefore
means item content: literal leading whitespace in a paragraph is written as a
numeric entity (`&#32; text`). A contained table's container is recorded on its first
cell, and the paragraph Docs keeps between two tables belongs to their container.
Contained paragraphs are indented 36pt per enclosing quote or list item; a
contained table keeps its container in Markdown but is not visually indented in
Docs. `edit` and `insert` create these ranges for new code and containers. Wording
edited inside a code line or quote stays in its block; a structural replacement
splits the block around it. A replacement inside code is literal text (`*`, links
and `#` stay characters); inside inline code in prose, a replacement written as one
code span such as `` `name` `` uses that span's content. Tab replacements remove gdoc's old ranges. Other named
ranges (for example from add-ons) are named as omitted by reads; targeted edits
leave them alone, and a rewrite of their tab needs `--allow-lossy` because it
deletes the text they mark. Edits made in Docs take precedence over these ranges: a
paragraph inside a code range that has become a heading or list item, or carries an
image, link or emphasis, is read as ordinary Markdown, and a paragraph whose quote or
list indent was removed is read without that container. Plain text typed or merged
into a code block stays code.
Reference links accept full, collapsed and shortcut forms with URI definitions.
A CommonMark link title (`[a](url "title")`, also on a definition) is dropped:
Docs links have no title, and the URL stays exact. Exported destinations
containing whitespace are bracketed (`<...>`).
Native horizontal rules sharing a paragraph with text export as separate rule and
text paragraphs, preserving text order and heading styles. Tables accept short
alignment delimiters such as `:--`, `--:` and `:-:` and do not gain incidental header bold.
As in GFM, table rows may end in whitespace and be indented up to three spaces.
Only the line after a table's header is its delimiter row, so data rows of dashes stay
rows. One blank line separates adjacent tables; further blank lines between them are
blank paragraphs. Docs keeps a paragraph before a table that starts a tab, between
two tables, and after a table that ends a tab. Reads show each as a blank line (so
adjacent tables read with two blank lines between them), and writing that read
back keeps exactly those paragraphs; blank paragraphs you add beyond them are kept
too. This is stable across rewrites. Syntax highlighting, native object IDs, pagination, custom fonts,
colors and arbitrary layout are outside the Markdown promise.

Write the canonical spellings that exports use; other spellings of the same
structure may read differently:

- Nest a list item two spaces per level under any marker (`- a` then `  - b`;
  `10. a` then `  - b`). Each further two spaces adds a level. gdoc writes
  nesting as native list levels, and a Docs list's
  [`nestingLevels`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#listproperties)
  hold nine. gdoc has no representation for deeper nesting (such as extra
  indents or separate lists), so it is unsupported: deeper items are written at
  the ninth level and `write`/`insert` warn with the affected lines. This is a
  gdoc limit, not one of the API gaps below.
- Open a fenced code block inside a list item on its own line: the item's
  text, a blank line, then the fence indented to the item's content. Input with
  a fence on the marker line itself (`` 1. ``` ``, `` - ~~~ ``, `` - > ``` ``)
  is refused with nothing sent.
- When emphasis spans close together, mark the inner one with underscores:
  `**bold _italic_**`, not `**bold *italic***`. Spans that open together read
  as CommonMark does (`***bold** then italic*`).
- Bold, italic or strikethrough on whitespace alone has no Markdown spelling and
  is not kept; a link on whitespace alone is (`[ ](url)`).
- An explicit line break inside a paragraph is Docs' soft break, the vertical-tab
  character U+000B, which gdoc reads and writes as that character; in table cells
  it is `<br>`. A trailing backslash or two trailing spaces is not a line break.
- To begin a file with two thematic breaks, spell them `***`: a file whose first
  two lines are `---` is read as an empty metadata block.

Images accept publicly fetchable HTTP(S) URLs. Existing images export as
`![](gdoc-image:OBJECT_ID)` (with alt text when available); a linked image is
`[![](gdoc-image:OBJECT_ID)](URL)`, and writes keep the link on the image. Those
references are valid only in their source document; each write resolves a fresh image URI from its
checked snapshot. Moving or rewriting an image may change its native ID. gdoc does
not publish existing private images. Drawing/chart identity, image crop and layout
are richer features.

Two verified API gaps have explicit best-effort behavior:

- A reconstructed numbered list starts at 1, even when Markdown asks for another
  starting value. gdoc warns with the affected list. Native numbering is retained;
  it is never replaced by plain numbered text. Export reads existing starts, and
  targeted text edits retain them. The [Docs bullet request](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#CreateParagraphBulletsRequest)
  provides presets but no start-number setter. Drive import does not provide the
  same revision-protected in-place write, and Apps Script list methods do not add
  a start-number setter. Restarts at 1 and continuation across prose or mixed
  bullet/numbered sections are supported. Independently numbered lists of the same
  style interleaved at the same depth can require a new non-1 start when rebuilt
  (for example, displayed items `1, 1, 2, 2`); that case also warns and may reset.
  Docs REST has no list-ID setter. Apps Script offers
  [setListId](https://developers.google.com/apps-script/reference/document/list-item#setListId(ListItem)),
  but, like its image setters, requires a separate execution service outside the
  revision-pinned Docs batch. Targeted text edits preserve existing list identity.
- Inserted/reconstructed images cannot receive Markdown alt text through the
  [Docs image request](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#InsertInlineImageRequest).
  gdoc warns when alt text cannot be written. Apps Script exposes an
  [alt-description setter](https://developers.google.com/apps-script/reference/document/inline-image#setAltDescription(String)),
  but requires a separately deployed script and does not join the revision-pinned
  Docs batch. This release does not introduce that separate execution service.

These are documented shortfalls, not claims of full support.

### Rich-content replacement safety

`write` and `push` inspect the selected body, including table cells, before mutation.
Ordinary supported Markdown needs no `--allow-lossy`. Chips, footnotes, equations,
generated contents, pending suggestions, internal native links, linked Sheets
charts (a rewrite keeps only their rendered image), drawings, custom named
ranges, complex tables,
table cells aligned unlike their column's header cell, and section/layout boundaries may require explicit loss consent. Rich content in
unmodified sibling tabs or separate document segments does not block a body write.
Use targeted edits when richer native content should survive. With consent, a
body with section breaks is rewritten as one section: the breaks and their
per-section layout are removed.

A pipe-table cell holds inline Markdown and `<br>` line breaks, so block content
inside a cell is a richer feature: a native bulleted or numbered paragraph, or a
heading, inside a cell has no Markdown spelling, nor does a native rule or an indented
paragraph in a cell. Reads name such a table as omitted content, a rewrite of it
is refused, and with `--allow-lossy` those cell paragraphs become plain text and
rules in cells are dropped; edit the
cell's wording with `edit --cell` to keep them. Lists around and beside tables,
and tables inside list items and quotes, are supported Markdown and never need
consent.

A changed tab rewrite deletes and reinserts the tab's body, so comments anchored in
it can lose their anchors even where the text is unchanged; targeted edits touch
only the replaced wording.

Markdown reads show a tab's text without its pending suggestions: suggested
insertions are left out and wording suggested for deletion stays. `cat` notes on
stderr how many suggestions are pending (`scope.pending_suggestions` with `--json`).
Writing that text back unchanged sends nothing. Resolve the suggestions in Docs
(accept or reject them) before a changed rewrite; otherwise it is refused unless
`--allow-lossy` is given, which discards the suggestions and keeps the text as read,
never applying them as direct edits. When a suggested paragraph break joins
paragraphs of a different style, list or code/quote container, gdoc cannot
reliably render that paragraph from the inline suggestion view, so it marks the
read incomplete (`scope.complete` is false, with `scope.suggestion_preview_gaps`);
such a read cannot authorize a rewrite until the suggestions are resolved.
`edit` refuses a match that touches text with a pending suggestion (exit 3,
nothing written), so a direct edit never settles someone's suggestion; edits
elsewhere in the document proceed. When the search covered several tabs, `edit`
names each changed tab (`tab ID: N`, or `tabs` in JSON).

```bash
gdoc write DOC draft.md --tab Notes --allow-lossy
```

Known style resets produce a concise warning. `--allow-lossy` accepts identified
rich-feature losses; it does not bypass revision conflicts. Automatic sync uses the
same native tab and revision checks and reports a skip when it cannot safely write.
The inventory is not a promise of pixel-perfect formatting or detection of properties
Google does not expose.

Files pulled by gdoc 0.21.1 to 0.22.0 carry only `gdoc-version: N`, the Drive
version their content came from, instead of `gdoc-revision`. When the doc has moved
past that version, `push`, `write DOC FILE` (and MCP `write` of such text) and the sync hook refuse
such a file with exit 3: nothing is sent and the file is left untouched. The sync hook
exits 2 instead, which Claude Code shows to the agent. The refusal prints recovery
steps: pull a fresh copy to a new path (`gdoc pull DOC draft.latest.md`), see what
changed with `gdoc diff DOC draft.md`, and carry your edits into the new file; `--force`
discards the newer changes in the doc. A file whose body already matches the target
tab's Markdown, or the doc's Markdown export for the first tab (ignoring only line
endings and one final newline), is reported `already in sync` and nothing is sent;
`--force-collapse-tabs` skips that shortcut. Otherwise, at the
current version such a file still has no revision provenance, so it needs a fresh
pull or `--force`, as above. The pull hook blocks an edit to a stale file (exit 2,
with the same recovery steps), re-pulls one that matches the doc, and leaves a
current file with local edits in place. `pull` now records `gdoc-revision` and the
tab fingerprint rather than `gdoc-version`, and a file with `gdoc-revision` ignores
any `gdoc-version`. When a file starts with a `---` block that mentions `gdoc`
anywhere, the block must be read exactly as gdoc frontmatter: closed by a `---`
line, every line `key: value`, naming the document with `gdoc: ID`. Otherwise
`write` (CLI and MCP) and `push` refuse it with exit 3 and nothing sent, and the
sync hook reports the refusal (exit 2); an unclosed block that mentions `gdoc` is
refused too. Fix the header, or remove it to copy the text.

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

Google Docs supports multiple tabs per document. The default `cat` command reads the first tab through the native Docs serializer. Use `--tab` to select another editable tab, or `--all-tabs` for combined inspection output:

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

Without `--tab`, `--comments` annotates the whole document's Markdown export and places comments from their live anchors. With `--tab`, it annotates that tab's Markdown and places comments where their quoted text occurs. Drive does not record which tab a quote is in, so a comment whose quote appears only in another tab is listed as `quoted text in another tab`, and one whose quote appears in several tabs as `quoted text ambiguous`. `--all-tabs` cannot be combined with `--comments`. Both tab flags work with `--json` and `--plain`.

Tab Markdown export escapes literal syntax: a plain `1. Hello` paragraph now
prints as `1\. Hello`, and `_`, `[`, and `<` gain backslashes. It also emits
`<!-- -->` between touching emphasis runs and `<!-- gdoc:TITLE --> ` or
`<!-- gdoc:SUBTITLE --> ` prefixes for those named paragraph styles. This visible
source noise is an accepted trade-off: escapes distinguish literal prose from
Markdown structure, empty comments separate styles without adding characters to
the document, and ordinary Markdown cannot express TITLE and SUBTITLE distinctly.
Keep these markers when reconstructing a tab with `write --tab`; arbitrary
Markdown tools and the separate Drive importer may not preserve them. For verbatim
prose, copying search strings, or text to match with `edit`, use
`gdoc cat --plain --tab "Notes" DOC`.

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

`edit` searches and replaces text inside table cells, not just plain paragraphs. For label/value grids (a label in the first column, the value in the next), address a cell directly instead of anchoring on its current text:

```bash
# Replace the cell to the right of a unique first-column label
gdoc edit DOC --tab "Tab 1" --cell "Discussion topics from JP" "Show and tell; Q2 planning"

# Address by ROW,COL coordinates (0-based) within the Nth table (--table, default 0)
gdoc edit DOC --cell 7,1 "new value"

# --col overrides which column to write (default: the one right of the label)
gdoc edit DOC --cell "Status" --col 2 "Done"
```

Labels must identify exactly one first-column row in the selected table(s). If the same text also appears in a value column, label mode refuses with exit code 3; use explicit `--table` and `--cell ROW,COL` coordinates.

Cell edits preserve the cell's paragraph structure; an empty cell is filled in place. The replacement supports the same Markdown formatting as a normal `edit`.

### Matching tolerance

By default matching is exact. If an anchor isn't found, `edit` explains why — most often a smart-quote apostrophe (`’` vs `'`) or a line break where the anchor had a space. Pass `--normalize` to match through smart-quote and dash differences:

```bash
gdoc edit DOC "JP's job" "JP's role" --normalize   # matches "JP's job" in the doc
```

### Links in replaced text

An edit inside one link's text stays in that link, so correcting or extending part
of a label keeps the link. When a match covers a whole link, or reaches past it,
the link follows only its own words that reappear once, as whole words, in the
replacement. Write `[label](url)` in the replacement to set a link explicitly.
Wording that does not keep a link also loses Docs' default link colour and
underline; a custom colour on the linked text stays.

### Editing list items

`edit` and `insert` change list items only in place. They can reword items,
each keeping its list, kind (numbered or bullet), level and number, and they can delete
whole items. So `edit DOC "b" "2. B"` or `edit DOC "b" "B"` in `1. a / 2. b / 3. c`
gives `1. a / 2. B / 3. c`, and one line per replaced item rewords several items.
Write an item inside a quote or list item without the container's markers
(`2. B`, not `> 2. B`); it keeps its container.
Any other list change is refused with nothing sent. `gdoc nest` and
`gdoc unnest` move items a level in or out ([Nesting list items](#nesting-list-items)), and `write --tab`
makes the rest:

- an item of another kind, level or number (a number the item doesn't show
  starts a new list in Markdown);
- an added or removed item (including one split by an encoded line break,
  `&#10;`), or a paragraph turned into an item or back;
- an item moved into or out of a quote or list item;
- deleting an item when anything but a blank line, top-level text or the next
  item at its level follows it (content paragraphs, sub-items, a quote, a rule,
  an empty heading, code or a table), since that may be the item's and would
  join the item above; delete it together with the item;
- inserted items that would, or may, join a list above them: beside it, below
  its deeper items, inside another item's content, or, for a numbered item
  asking for a number other than 1, after any numbered list of its level.

A later `edit --block` will make these as targeted edits. A refused level change
names `gdoc nest` only when it would accept those items and they have no
sub-items (`nest` moves sub-items with them); it does not yet move items
of a quoted list, of a loose list (blank lines between items), or an item that
follows another item's content paragraphs.

A table cell (`edit --cell`) holds inline text only: an item or
heading marker there stays literal, as the cell's `cat` spelling reads, and
`<br>` writes a line break. A cell replacement with a line break (from `<br>`, a
newline or `&#10;`), or one that changes the cell's number of lines, cannot include
an image; insert the image separately.

### Deleting across paragraphs

An empty replacement removes complete matched paragraphs, and only those: every
other paragraph keeps its style, list and container. A match that starts or ends
inside a paragraph and spans a paragraph break joins the remaining text into one
paragraph, which keeps the first paragraph's style and list. Matches are on the
document's text, so `edit DOC "lo\nwor" ""` on the paragraph `Hello` followed by
the heading `world` leaves the paragraph `Helld`. A join that takes a later list
item into the paragraph before it is refused, because it would remove that item
or its bullet; delete whole items instead. A join that starts at a list item's
first character is refused when the paragraph it joins has another list state,
because Docs would give the result that paragraph's list.

Docs cannot delete a tab's last paragraph break or the one before a table, so
removing the paragraphs there deletes the break of the paragraph above instead;
that paragraph keeps its own style and list. Two such removals are refused, with
nothing sent: one with no paragraph above (use `write --tab`), and one whose
paragraph above is an empty list item of another list, which cannot be kept.
A removal that would delete any character or paragraph break carrying a pending
suggestion, including a break outside the matched text, is refused too.

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
  those. A replacement inside one paragraph must not introduce paragraph
  breaks; block Markdown requires the whole paragraph as its target and must
  also satisfy the command's supported-format rules. An empty replacement
  suggests deleting the wording but keeps its paragraph; use `edit` to remove
  whole paragraphs. Fenced code blocks are
  accepted as code-font paragraphs only when the paragraph-boundary contract
  is satisfied.
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

`new --file` uses Google's Markdown import, which reads CommonMark paragraphs
(joined lines, blank-line separators) rather than gdoc's line-per-paragraph
format ([Supported Markdown](#supported-markdown)); code and container ranges,
table alignment and gdoc image references are not recreated. To create a
document from a file in gdoc's format, such as a `cat` or `pull` output, run
`gdoc new TITLE` and then `gdoc write DOC FILE`.

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
