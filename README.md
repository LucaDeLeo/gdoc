# gdoc

A token-efficient CLI for AI agents to read, write, and collaborate on Google Docs.

`gdoc` gives AI coding agents (Claude Code, Cursor, Codex, etc.) a simple command-line interface to Google Docs and Drive. Every command is designed to minimize token usage while providing the context agents need — change detection banners, conflict prevention, structured output modes, and inline comment annotations.

## Install

```bash
uv tool install git+https://github.com/LucaDeLeo/gdoc.git
```

Or from a local clone:

```bash
git clone https://github.com/LucaDeLeo/gdoc.git
cd gdoc
uv tool install .
```

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

# Overwrite a document from a local file
gdoc write DOC_ID draft.md

# Create a new blank document
gdoc new "Meeting Notes"

# Create a document from a local markdown file (with image support)
gdoc new "Report" --file report.md

# Duplicate a document
gdoc cp DOC_ID "Copy of Report"
```

All commands accept a full Google Docs URL or a bare document ID:

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
| `tabs DOC` | List all tabs in a document |
| `info DOC` | Show title, owner, modified date, word count |
| `ls [FOLDER]` | List files in Drive root or a folder (`--type docs\|sheets\|all`) |
| `find QUERY` | Search files by name or content |

### Writing

| Command | Description |
|---------|-------------|
| `edit DOC OLD NEW` | Find and replace text with markdown formatting, including tables (`--all` for all) |
| `write DOC FILE` | Overwrite document from a local markdown file |
| `new TITLE` | Create a blank document (`--folder` to specify location, `--file` to import markdown with images) |
| `cp DOC TITLE` | Duplicate a document |

### Comments

| Command | Description |
|---------|-------------|
| `comments DOC` | List all open comments (`--all` to include resolved) |
| `comment DOC TEXT` | Add a comment (`--quote` to anchor to text) |
| `comment-info DOC ID` | Get a single comment with full detail |
| `reply DOC COMMENT_ID TEXT` | Reply to a comment |
| `resolve DOC COMMENT_ID` | Resolve a comment (`--message` to include a note) |
| `reopen DOC COMMENT_ID` | Reopen a resolved comment |
| `delete-comment DOC ID` | Delete a comment (`--force` to skip confirmation) |

### Other

| Command | Description |
|---------|-------------|
| `auth` | Authenticate with Google (`--no-browser` for headless) |
| `share DOC EMAIL` | Share a document (`--role reader\|writer\|commenter`) |

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

Comments whose anchor text has been deleted, is too short, or is ambiguous are grouped in an `[UNANCHORED]` section at the end.

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

## Import from file

Create a document from a local markdown file with `new --file`:

```bash
gdoc new "Report" --file report.md
```

Images in the markdown are handled automatically:
- **Remote images** (`https://...`) are inserted directly via URL
- **Local images** are uploaded to Drive temporarily, inserted, then cleaned up
- Supported formats: PNG, JPG, JPEG, GIF, WebP

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

Errors always print `ERR: <message>` to stderr, even in `--json` mode.

## Configuration

All files are stored under `~/.config/gdoc/`:

| File | Purpose |
|------|---------|
| `credentials.json` | OAuth client credentials (from Google Cloud Console) |
| `token.json` | Stored OAuth token (created by `gdoc auth`) |
| `state/<DOC_ID>.json` | Per-document state for change detection |

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

## License

MIT
