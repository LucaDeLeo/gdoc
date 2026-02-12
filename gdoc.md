# gdoc — CLI for Google Docs & Drive

A token-efficient CLI designed for AI agents to interact with Google Docs and Google Drive via bash.

## Design Principles

1. **Token-efficient output** — terse by default, `--json` for machine parsing, `--verbose` for humans
2. **Intuitive verbs** — `ls`, `cat`, `edit`, `write`, `comment` feel like unix commands
3. **Minimal round-trips** — compound operations in single commands where possible
4. **Pipe-friendly** — reads stdin, writes stdout, exits with proper codes

## Command Reference

```
gdoc auth                                    # OAuth2 flow, stores creds in ~/.config/gdoc/
gdoc ls [FOLDER_ID] [--type docs|sheets|all] # List files (default: root)
gdoc find "query"                            # Search by name/content
gdoc cat DOC_ID                              # Export doc as markdown to stdout
gdoc cat DOC_ID --comments                   # Line-numbered with comment annotations
gdoc cat DOC_ID --plain                      # Export as plain text
gdoc cat DOC_ID > local.md                   # Save locally

gdoc edit DOC_ID "old text" "new text"       # Find unique match & replace
gdoc edit DOC_ID "old text" "new text" --all # Replace all occurrences
gdoc write DOC_ID FILE.md                    # Overwrite doc body from local markdown

gdoc comments DOC_ID                         # List open comments
gdoc comments DOC_ID --all                   # Include resolved
gdoc comment DOC_ID "comment text"           # Add unanchored comment
gdoc reply DOC_ID COMMENT_ID "reply text"    # Reply to a comment
gdoc resolve DOC_ID COMMENT_ID              # Resolve a comment
gdoc reopen DOC_ID COMMENT_ID               # Reopen a resolved comment

gdoc info DOC_ID                             # Title, owner, last modified, word count
gdoc share DOC_ID EMAIL [--role reader|writer|commenter]
gdoc new "Document Title" [--folder FOLDER_ID]  # Create blank doc
gdoc cp DOC_ID "Copy Title"                  # Duplicate a doc
```

### `cat --comments` (Annotated View)

This is the "full picture" mode — what a human sees when they look at the doc.
Output uses line-numbered format (like Claude Code's Read tool) with comment
annotations on un-numbered lines. Numbered lines = content, un-numbered = metadata.

```bash
$ gdoc cat 1aBcDeFg --comments
--- no changes ---
     1	# Q3 Planning Doc
     2
     3	We need to ship the roadmap by end of month.
      	  [#1 open] alice@co.com on "ship the roadmap":
      	    "This paragraph needs a citation"
      	    > bob@co.com: "Added, see line 42"
     4
     5	The budget is $2M for infrastructure.
      	  [#3 open] carol@co.com on "budget is $2M":
      	    "Can we add metrics here?"
```

The agent sees exactly what it can `edit` (numbered lines) and what's discussion
context (un-numbered lines). This matches the Read/Edit pattern from Claude Code —
line numbers are a display prefix, not part of the content.

The anchoring uses `quotedFileContent.value` from the comments API — the text
the comment was attached to. The CLI finds that substring in the markdown and
places the annotation after the line containing it.

Unanchored comments go at the bottom:

```
      	[UNANCHORED]
      	  [#5 open] dave@co.com: "General feedback: great doc"
```

## Awareness System — "What Changed?"

The core insight: every time the CLI runs a command against a doc, it first checks
what changed since the last interaction. This gives the agent the same situational
awareness as a human staring at the Google Docs tab.

### State Tracking

```
~/.config/gdoc/state/{DOC_ID}.json
{
  "last_seen": "2025-01-20T14:30:00Z",     // last time we interacted
  "last_version": 847,                       // doc version number
  "last_comment_check": "2025-01-20T14:30:00Z",
  "known_comment_ids": ["AAA", "BBB"]        // to detect new vs updated
}
```

### On Every Command (pre-flight check)

Before executing any command targeting a DOC_ID, the CLI does:

1. `files.get(fileId, fields="modifiedTime,version,lastModifyingUser")` — 1 API call
2. `comments.list(fileId, startModifiedTime=last_comment_check, fields="...")` — 1 API call
3. Compare against stored state → build notification list
4. Print notifications → execute actual command → update stored state

This adds ~200ms overhead (2 lightweight API calls) but gives the agent full awareness.

### Notification Banner

```bash
$ gdoc edit 1aBcDeFg "old text" "new text"
--- since last interaction (3 min ago) ---
 ✎ doc edited by alice@co.com (v847 → v851)
 💬 new comment #3 by carol@co.com: "Can we add metrics here?"
 ↩ new reply on #1 by bob@co.com: "Done, added the citation"
 ✓ comment #2 resolved by alice@co.com
---
OK replaced 1 occurrence
```

```bash
$ gdoc cat 1aBcDeFg
--- since last interaction (10 min ago) ---
 ✎ doc edited by alice@co.com (v851 → v853)
---
# Q3 Planning Doc

We need to ship the roadmap by...
```

```bash
$ gdoc cat 1aBcDeFg
--- no changes ---
# Q3 Planning Doc
...
```

When there are no changes, the banner is a single line so it's cheap on tokens.
When there ARE changes, the agent sees them before its own command output.

### Notification Types

| Symbol | Meaning | Detection Method |
|--------|---------|-----------------|
| `✎` | Doc body edited | `version` field changed |
| `💬` | New comment | comment ID not in `known_comment_ids` |
| `↩` | New reply on existing comment | comment's `modifiedTime` changed, check replies |
| `✓` | Comment resolved | `resolved: true` on previously open comment |
| `↺` | Comment reopened | `resolved: false` on previously resolved comment |

### Conflict Awareness

If the agent is about to `edit` or `write`, but the doc was edited since last `cat`:

```bash
$ gdoc edit 1aBcDeFg "old text" "new text"
--- since last interaction (5 min ago) ---
 ✎ doc edited by alice@co.com (v847 → v851)
 ⚠ WARNING: doc changed since your last read. Run `gdoc cat` to refresh.
---
OK replaced 1 occurrence
```

The warning is informational (doesn't block) because `replaceAllText` is safe — it
matches current text. But `write` (full overwrite) WILL block:

```bash
$ gdoc write 1aBcDeFg local.md
--- since last interaction (5 min ago) ---
 ✎ doc edited by alice@co.com (v847 → v851)
---
ERR: doc modified since last read. Use --force to overwrite, or `gdoc cat` first.
```

### `--quiet` Flag

For batch operations or when the agent doesn't need awareness:

```bash
$ gdoc edit 1aBcDeFg "old" "new" --quiet
OK replaced 1 occurrence
```

Skips the pre-flight check entirely — saves 2 API calls.

### First Interaction (no stored state)

```bash
$ gdoc cat 1aBcDeFg
--- first interaction with this doc ---
 📄 "Q3 Planning Doc" by alice@co.com, last edited 2025-01-20
 💬 3 open comments, 1 resolved
---
# Q3 Planning Doc
...
```

## Output Design (token-efficient by default)

```bash
$ gdoc ls
ID                             TITLE                    MODIFIED
1aBcDeFgHiJkLmNoPqRsTuVwXyZ   Q3 Planning Doc          2025-01-15
2xYzAbCdEfGhIjKlMnOpQrStUv   Meeting Notes 2025-01    2025-01-20

$ gdoc comments 1aBcDeFg
#1 [open] alice@co.com 2025-01-15
  "This paragraph needs a citation"
  → bob@co.com: "Added, see line 42"
#2 [open] carol@co.com 2025-01-18
  "Should we include the Q2 comparison?"

$ gdoc cat 1aBcDeFg
# Q3 Planning Doc

We need to finalize the roadmap by...

$ gdoc edit 1aBcDeFg "finalize the roadmap" "ship the roadmap"
OK replaced 1 occurrence

$ gdoc resolve 1aBcDeFg 1
OK resolved comment #1

$ gdoc info 1aBcDeFg --json
{"id":"1aBcDeFg","title":"Q3 Planning Doc","owner":"alice@co.com","modified":"2025-01-15T10:30:00Z"}
```

## Architecture

```
gdoc/
├── __init__.py
├── __main__.py          # entry: `python -m gdoc` or `gdoc` via pip
├── cli.py               # argparse command routing
├── auth.py              # OAuth2 flow + credential storage
├── api/
│   ├── __init__.py
│   ├── drive.py         # Drive API v3: list, search, share, export, upload
│   ├── docs.py          # Docs API v1: batchUpdate (replaceAllText)
│   └── comments.py      # Drive API v3: comments + replies CRUD
├── state.py             # Per-doc state tracking (~/.config/gdoc/state/)
├── notify.py            # Pre-flight change detection + banner formatting
├── annotate.py          # Line-numbered comment annotation for `cat --comments`
├── format.py            # Output formatters (table, json, plain)
└── util.py              # ID extraction from URLs, error handling
```

## API Mapping

### Read Operations (Drive API v3)

| Command | API Call | Notes |
|---------|----------|-------|
| `ls` | `files.list(q="...", fields="files(id,name,modifiedTime)")` | Paginates automatically |
| `find` | `files.list(q="name contains '...' or fullText contains '...'")` | Drive search syntax |
| `cat` | `files.export(fileId, mimeType='text/markdown')` | Native md export since 2024 |
| `cat --plain` | `files.export(fileId, mimeType='text/plain')` | |
| `info` | `files.get(fileId, fields="id,name,owners,modifiedTime,...")` | |

### Write Operations (Docs API v1 — batchUpdate)

| Command | Request Type | Notes |
|---------|-------------|-------|
| `edit` | `replaceAllText` | Checks uniqueness first; `--all` skips uniqueness check. Supports `--case-sensitive` |

### Write (Full Doc Replace via Drive API v3)

| Command | API Call | Notes |
|---------|----------|-------|
| `write` | `files.update` with media upload | Upload .md, set `mimeType='application/vnd.google-apps.document'` to convert |

The `write` command does a full overwrite. Strategy:
1. Read the local `.md` file
2. Upload it as media to Drive via `files.update` on the existing doc ID
3. Drive auto-converts markdown → Google Doc formatting

**Caveat**: This is a full overwrite — it replaces the entire doc body. For surgical edits, use `edit`.

### Comments (Drive API v3)

| Command | API Call | Notes |
|---------|----------|-------|
| `comments` | `comments.list(fileId, fields="comments(id,content,author,resolved,replies,...)")` | |
| `comment` | `comments.create(fileId, body={content: "..."})` | Unanchored comment |
| `reply` | `replies.create(fileId, commentId, body={content: "..."})` | |
| `resolve` | `replies.create(fileId, commentId, body={action: "resolve"})` | Must be a reply with `action` field |
| `reopen` | `replies.create(fileId, commentId, body={action: "reopen"})` | |

**Key insight from API research**: You can't directly set `resolved=true` on a comment. You must create a reply with `action: "resolve"`. This is how the Google Docs UI works too.

## Auth Strategy

```python
# ~/.config/gdoc/credentials.json  — OAuth2 client secrets (user provides)
# ~/.config/gdoc/token.json         — cached access/refresh token

SCOPES = [
    'https://www.googleapis.com/auth/drive',           # full drive access
    'https://www.googleapis.com/auth/documents',        # docs read/write
]
```

Flow:
1. `gdoc auth` — prompts user to place `credentials.json` in `~/.config/gdoc/`
2. Opens browser for OAuth2 consent
3. Stores refresh token in `~/.config/gdoc/token.json`
4. All subsequent commands auto-refresh silently

## URL-to-ID Resolution

Agents and users often paste full URLs. The CLI should accept both:

```bash
gdoc cat 1aBcDeFgHiJkLmNoPqRsTuVwXyZ
gdoc cat "https://docs.google.com/document/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ/edit"
```

Regex: `r'/d/([a-zA-Z0-9_-]+)'` extracts the ID from any Google Docs/Drive URL.

## Error Handling

```
$ gdoc cat nonexistent_id
ERR: file not found (404)

$ gdoc edit 1aBc "text not in doc" "new"
ERR: no match found for "text not in doc"

$ gdoc edit 1aBc "common word" "new"
ERR: multiple matches (3 found). Use --all to replace all occurrences.
```

Exit codes: 0=success, 1=API error, 2=auth error, 3=usage error

## Dependencies

```
google-api-python-client    # Drive + Docs API
google-auth-oauthlib        # OAuth2 flow
google-auth-httplib2        # HTTP transport
```

No other dependencies. Intentionally minimal.

## Key Implementation Notes

1. **Markdown export is native** — Drive API supports `text/markdown` as an export MIME type since July 2024. No conversion library needed.

2. **Markdown import via Drive** — Upload `.md` with `mimeType='application/vnd.google-apps.document'` and Drive converts it. Alternatively, `files.copy` a markdown file with that mimeType.

3. **`edit` is the workhorse for agents** — mirrors Claude Code's Edit tool. Agents `cat` the doc, find the text to change, and `edit` it with an exact unique match. No index math needed.

4. **Comments use Drive API, not Docs API** — The Docs API can read comments embedded in the document structure, but CRUD operations on comments are exclusively through the Drive API v3.

5. **`write` is destructive** — Full doc replacement. Blocked when doc changed since last read unless `--force` is passed. Agents should prefer `edit` for targeted edits.

## Future Extensions

- `gdoc diff DOC_ID FILE.md` — show diff between remote and local
- `gdoc pull DOC_ID FILE.md` — alias for `cat DOC_ID > FILE.md`  
- `gdoc watch DOC_ID` — poll for changes / new comments
- `gdoc suggest DOC_ID "old" "new"` — make a suggestion instead of direct edit (Docs API supports `suggestInsertText` etc.)
- `gdoc export DOC_ID --format pdf|docx|html`
