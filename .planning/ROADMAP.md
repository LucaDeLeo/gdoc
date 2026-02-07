# Roadmap: gdoc

## Overview

gdoc delivers a token-efficient CLI for AI agents to interact with Google Docs and Drive. The roadmap follows the natural dependency chain: foundation and auth first, then read operations (needed before anything else works), then the awareness system (the killer feature that must exist before writes), then write operations (which depend on awareness for conflict detection), then comments and inline annotations, and finally file management operations. Six phases deliver all 31 v1 requirements in dependency order.

## Milestones

- 🚧 **v1.0** — Core CLI with full read/write/comment capabilities — Phases 1-6

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation & Auth** - Project scaffolding, OAuth2, output formatting, and CLI infrastructure
- [x] **Phase 2: Read Operations** - Export docs as markdown/plain text, list and search files, view metadata
- [x] **Phase 3: Awareness System** - Pre-flight change detection, notification banners, conflict detection
- [x] **Phase 4: Write Operations** - String-match editing and full document write with version guards
- [ ] **Phase 5: Comments & Annotations** - Comment CRUD and line-numbered comment annotations in doc output
- [ ] **Phase 6: File Management** - Create, duplicate, and share documents

## Phase Details

### Phase 1: Foundation & Auth
**Goal**: Users can authenticate and the CLI framework handles input parsing, output formatting, and error reporting consistently across all future commands
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, OUT-01, OUT-02, OUT-03, OUT-04, OUT-05, OUT-06
**Success Criteria** (what must be TRUE):
  1. User can run `gdoc` and see help text listing available commands
  2. User can authenticate via browser-based OAuth2 flow and credentials persist in `~/.gdoc/`
  3. User can authenticate in a headless environment (no browser available)
  4. Subsequent commands reuse stored credentials without re-prompting (silent token refresh)
  5. Any Google Docs URL or bare document ID is accepted interchangeably as input
**Plans**: 3 plans in 3 waves

Plans:
- [x] 01-01-PLAN.md — Project scaffolding, core utilities (URL-to-ID, error classes), output formatting
- [x] 01-02-PLAN.md — CLI infrastructure (custom parser, exit codes, stub subcommands, CI gate)
- [x] 01-03-PLAN.md — OAuth2 authentication (browser + headless flows, token management)

### Phase 2: Read Operations
**Goal**: Users can read document content, list files, search for documents, and view metadata through the CLI
**Depends on**: Phase 1
**Requirements**: READ-01, READ-03, READ-04, READ-05, READ-06
**Success Criteria** (what must be TRUE):
  1. User can export a Google Doc as markdown to stdout with `cat DOC_ID`
  2. User can export a Google Doc as plain text with `cat DOC_ID --plain`
  3. User can list files in Drive with `ls` and filter by folder and type
  4. User can search for documents by name or content with `find "query"`
  5. User can view doc metadata (title, owner, last modified, word count) with `info DOC_ID`
**Plans**: 3 plans in 3 waves

Plans:
- [x] 02-01-PLAN.md — API layer (Drive service factory, API wrappers, error translation), format_json, folder URL support
- [x] 02-02-PLAN.md — cat + info commands (document export, metadata display)
- [x] 02-03-PLAN.md — ls + find commands (file listing, search)

### Phase 3: Awareness System
**Goal**: Users get automatic situational awareness of what changed in a document since their last interaction, enabling safe concurrent human-agent collaboration
**Depends on**: Phase 2
**Requirements**: AWARE-01, AWARE-02, AWARE-03, AWARE-04
**Success Criteria** (what must be TRUE):
  1. Every command automatically shows a banner (on stderr) if the document was edited, or comments were added/resolved since the last interaction
  2. Running `edit` on a doc that changed since last read shows a warning but proceeds
  3. Running `write` on a doc that changed since last read is blocked with an error (requires `--force` to override)
  4. User can pass `--quiet` to any command to skip pre-flight checks entirely
**Plans**: 2 plans in 2 waves

Plans:
- [x] 03-01-PLAN.md — State persistence, comments API, pre-flight check, banner formatting
- [x] 03-02-PLAN.md — CLI integration (cmd_cat, cmd_info), state update lifecycle, --quiet behavior

### Phase 4: Write Operations
**Goal**: Users can edit document content through string-match replacement and full-document overwrite, with conflict safety enforced by the awareness system
**Depends on**: Phase 3
**Requirements**: WRITE-01, WRITE-02, WRITE-03
**Success Criteria** (what must be TRUE):
  1. User can find a unique string and replace it with `edit DOC_ID "old" "new"`, with `--all` to replace all occurrences
  2. User can overwrite an entire doc from a local markdown file with `write DOC_ID FILE.md`, and write is blocked when the doc changed since last read unless `--force` is passed
**Plans**: 2 plans in 2 waves

Plans:
- [x] 04-01-PLAN.md — `edit` command: Docs API v1 wrapper, find-and-replace with uniqueness pre-check, state update extension
- [x] 04-02-PLAN.md — `write` command: Drive API media upload, full-doc overwrite with conflict safety

### Phase 5: Comments & Annotations
**Goal**: Users can manage document comments (list, create, reply, resolve, reopen) and view documents with line-numbered comment annotations for full collaboration context
**Depends on**: Phase 2, Phase 4
**Requirements**: COMM-01, COMM-02, COMM-03, COMM-04, COMM-05, COMM-06, READ-02
**Success Criteria** (what must be TRUE):
  1. User can list open comments on a doc with `comments DOC_ID`
  2. User can list all comments (including resolved) with `comments DOC_ID --all`
  3. User can add a comment, reply to a comment, resolve a comment, and reopen a comment
  4. User can export a doc with line-numbered content and comment annotations on un-numbered lines using `cat DOC_ID --comments`
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: File Management
**Goal**: Users can create, duplicate, and share Google Docs from the CLI
**Depends on**: Phase 1
**Requirements**: FILE-01, FILE-02, FILE-03
**Success Criteria** (what must be TRUE):
  1. User can create a blank doc with `new "Title"` and optionally place it in a folder with `--folder FOLDER_ID`
  2. User can duplicate a doc with `cp DOC_ID "Copy Title"`
  3. User can share a doc with an email address and set the role (reader, writer, commenter) with `share DOC_ID EMAIL --role writer`
**Plans**: TBD

Plans:
- [ ] 06-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 --> 2 --> 3 --> 4 --> 5 --> 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Auth | 3/3 | Complete | 2026-02-07 |
| 2. Read Operations | 3/3 | Complete | 2026-02-07 |
| 3. Awareness System | 2/2 | Complete | 2026-02-07 |
| 4. Write Operations | 2/2 | Complete | 2026-02-07 |
| 5. Comments & Annotations | 0/2 | Not started | - |
| 6. File Management | 0/1 | Not started | - |
