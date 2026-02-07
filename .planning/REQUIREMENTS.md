# Requirements: gdoc

**Defined:** 2026-02-07
**Core Value:** Agents can read, edit, and comment on Google Docs through terse bash commands with full situational awareness of concurrent changes.

## v1 Requirements

### Authentication

- [ ] **AUTH-01**: User can authenticate via OAuth2 browser flow with credentials.json in ~/.gdoc/
- [ ] **AUTH-02**: User can authenticate in headless environments (console-based fallback)
- [ ] **AUTH-03**: Access tokens refresh silently without user intervention

### Read Operations

- [ ] **READ-01**: User can export a doc as markdown to stdout (`cat DOC_ID`)
- [ ] **READ-02**: User can export a doc with inline comments as HTML comments (`cat DOC_ID --comments`)
- [ ] **READ-03**: User can export a doc as plain text (`cat DOC_ID --plain`)
- [ ] **READ-04**: User can list files in a folder with type filtering (`ls [FOLDER_ID] [--type docs|sheets|all]`)
- [ ] **READ-05**: User can search files by name/content (`find "query"`)
- [ ] **READ-06**: User can view doc metadata — title, owner, last modified, word count (`info DOC_ID`)

### Write Operations

- [ ] **WRITE-01**: User can find & replace all occurrences of text in a doc (`replace DOC_ID "old" "new"`)
- [ ] **WRITE-02**: User can insert text at a character index (`insert DOC_ID INDEX "text"`)
- [ ] **WRITE-03**: User can append text at end of doc (`append DOC_ID "text"`)
- [ ] **WRITE-04**: User can delete a text range by start/end index (`delete DOC_ID START END`)
- [ ] **WRITE-05**: User can overwrite entire doc body from a local markdown file (`push DOC_ID FILE.md`)
- [ ] **WRITE-06**: Push blocks when doc has been modified since last read unless `--force` is passed

### Comments

- [ ] **COMM-01**: User can list open comments on a doc (`comments DOC_ID`)
- [ ] **COMM-02**: User can list all comments including resolved (`comments DOC_ID --all`)
- [ ] **COMM-03**: User can add an unanchored comment to a doc (`comment DOC_ID "text"`)
- [ ] **COMM-04**: User can reply to a comment (`reply DOC_ID COMMENT_ID "text"`)
- [ ] **COMM-05**: User can resolve a comment (`resolve DOC_ID COMMENT_ID`)
- [ ] **COMM-06**: User can reopen a resolved comment (`reopen DOC_ID COMMENT_ID`)

### Awareness System

- [ ] **AWARE-01**: Every command runs a pre-flight check detecting changes since last interaction
- [ ] **AWARE-02**: Notification banner shows doc edits, new comments, replies, resolved/reopened comments
- [ ] **AWARE-03**: Conflict detection warns when doc changed since last read (warning for replace, block for push)
- [ ] **AWARE-04**: `--quiet` flag skips pre-flight checks entirely (saves 2 API calls)

### File Operations

- [ ] **FILE-01**: User can create a blank doc with optional folder (`new "Title" [--folder FOLDER_ID]`)
- [ ] **FILE-02**: User can duplicate a doc (`cp DOC_ID "Copy Title"`)
- [ ] **FILE-03**: User can share a doc with an email (`share DOC_ID EMAIL [--role reader|writer|commenter]`)

### Output & UX

- [ ] **OUT-01**: Token-efficient terse output by default (minimal text, no decoration)
- [ ] **OUT-02**: `--json` flag outputs machine-parseable JSON on all commands
- [ ] **OUT-03**: `--verbose` flag outputs human-friendly detailed text
- [ ] **OUT-04**: All commands accept Google Docs URLs or bare document IDs
- [ ] **OUT-05**: Exit codes: 0=success, 1=API error, 2=auth error, 3=usage error
- [ ] **OUT-06**: Data to stdout, banners/warnings to stderr (pipe-safe)

## v2 Requirements

### Extended Features

- **EXT-01**: `diff DOC_ID FILE.md` — show diff between remote and local
- **EXT-02**: `pull DOC_ID FILE.md` — alias for cat > file with state tracking
- **EXT-03**: `watch DOC_ID` — poll for changes/new comments
- **EXT-04**: `suggest DOC_ID "old" "new"` — suggestion mode edits
- **EXT-05**: `export DOC_ID --format pdf|docx|html` — multi-format export
- **EXT-06**: Service account authentication for unattended/server use
- **EXT-07**: Anchored comments (comment on specific text, not just the doc)

## Out of Scope

| Feature | Reason |
|---------|--------|
| FUSE mount | Massive complexity; already solved by rclone/ocamlfuse |
| Bidirectional sync | Conflict resolution is intractable for concurrent editing |
| Rich formatting control | Docs API formatting is extremely verbose; markdown handles most cases |
| Google Sheets editing | Completely different API; deserves separate tool |
| Interactive mode / TUI | Agents can't use it; humans have Google Docs UI |
| Real-time websocket | Google doesn't expose real-time API |
| Version history browsing | Drive Revisions API is unreliable for Google Docs |
| Offline mode / caching | Google Docs is inherently online; cache invalidation is intractable |

## Traceability

Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (populated by roadmap) | | |

**Coverage:**
- v1 requirements: 28 total
- Mapped to phases: 0
- Unmapped: 28 ⚠️

---
*Requirements defined: 2026-02-07*
*Last updated: 2026-02-07 after initial definition*
