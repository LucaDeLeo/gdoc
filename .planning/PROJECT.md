# gdoc

## What This Is

A token-efficient CLI that lets AI agents interact with Google Docs and Google Drive via bash. Unix-like commands (`cat`, `ls`, `replace`, `comment`) give agents the same read/write/comment capabilities as a human in the Google Docs UI, with built-in awareness of what changed since the last interaction. Distributed via `uv tool`.

## Core Value

Agents can read, edit, and comment on Google Docs through terse bash commands with full situational awareness of concurrent changes — enabling real-time human-agent collaboration on documents.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] OAuth2 authentication flow with credential storage in `~/.gdoc/`
- [ ] `ls` — list files in Drive (with folder filtering, type filtering)
- [ ] `find` — search by name/content
- [ ] `cat` — export doc as markdown to stdout
- [ ] `cat --comments` — annotated view with line-numbered content and comment annotations
- [ ] `cat --plain` — export as plain text
- [ ] `edit` — find unique string match and replace (`--all` for all occurrences)
- [ ] `write` — overwrite doc body from local markdown file
- [ ] `comments` — list open comments (with `--all` for resolved)
- [ ] `comment` — add unanchored comment
- [ ] `reply` — reply to a comment
- [ ] `resolve` / `reopen` — resolve or reopen a comment
- [ ] `info` — doc metadata (title, owner, modified, word count)
- [ ] `share` — share doc with email (reader/writer/commenter roles)
- [ ] `new` — create blank doc (with optional folder)
- [ ] `cp` — duplicate a doc
- [ ] Awareness system — pre-flight change detection on every command
- [ ] Conflict detection — warn on edits since last read, block `write` unless `--force`
- [ ] `--quiet` flag to skip pre-flight checks
- [ ] `--json` output mode for machine parsing
- [ ] `--verbose` output mode for humans
- [ ] URL-to-ID resolution (accept full Google Docs URLs or bare IDs)
- [ ] Proper exit codes (0=success, 1=API error, 2=auth error, 3=usage error)

### Out of Scope

- `diff` command (compare remote vs local) — post-v1
- `pull` command (alias for cat > file) — post-v1
- `watch` command (poll for changes) — post-v1
- `suggest` command (suggestion mode edits) — post-v1
- `export` to PDF/DOCX/HTML — post-v1
- Google Sheets content editing — different API surface, separate tool
- Web UI or GUI — this is a CLI

## Context

- **Primary users are AI agents** — token efficiency is a first-class design concern, not an afterthought
- **Secondary users are power users** who want CLI access to Google Docs
- Agents use this as both author (create/write docs) and collaborator (read/edit/comment on existing docs)
- The awareness system is the key differentiator — agents get situational context about concurrent human edits before every operation
- Google Drive API v3 handles file operations, comments, and markdown export (native since July 2024)
- Google Docs API v1 handles surgical text edits via `batchUpdate`
- Comments CRUD is exclusively through Drive API v3 (not Docs API)
- `edit` uses `replaceAllText` with uniqueness check — no index math needed, mirrors Claude Code Edit tool
- `write` uses Drive `files.update` with media upload — markdown auto-converts to Google Doc format
- Comment resolution requires creating a reply with `action: "resolve"` (can't set `resolved=true` directly)

## Constraints

- **Dependencies**: Only `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2` — intentionally minimal
- **Python**: Standard Python package, installable via `uv tool install`
- **Auth**: OAuth2 with user-provided `credentials.json` — no service account flow in v1
- **API limits**: Awareness system adds ~200ms overhead (2 lightweight API calls per command)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python over Go/Rust | Google API client libraries are best in Python; target audience (AI agents) doesn't need binary distribution | — Pending |
| Unix verb names (cat, ls, cp) | Intuitive for agents trained on unix commands; reduces prompt engineering needed | — Pending |
| Awareness system on by default | Agents need situational context; opt-out via `--quiet` for batch ops | — Pending |
| `write` blocks on stale read | Full overwrite is destructive; `edit` warns but doesn't block (it's safe) | — Pending |
| Line-numbered annotation for `--comments` | Mirrors Claude Code Read/Edit pattern — numbered lines are content, un-numbered are metadata. No content/annotation confusion. | — Decided |
| Drive API for comments | Docs API can read comment structure but CRUD is Drive API v3 only | — Pending |
| `edit` mirrors Claude Code Edit tool | Agents already know unique-string-match pattern; drop index-based ops (insert/delete/append) | — Decided |

---
*Last updated: 2026-02-07 after initialization*
