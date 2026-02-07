# Feature Research

**Domain:** Google Docs/Drive CLI for AI agents
**Researched:** 2026-02-07
**Confidence:** MEDIUM (external research tools unavailable; based on training data knowledge of gdrive, rclone, google-drive-ocamlfuse, Google APIs; flagged where verification needed)

## Competitor Landscape

Before categorizing features, here is what existing tools actually offer:

### gdrive (glotlabs/gdrive v3, formerly prasmussen/gdrive)

**What it is:** Standalone Go binary for Google Drive file operations.
**What it does:**
- `list` / `download` / `upload` / `delete` / `info` / `mkdir`
- `share` (set permissions)
- `export` (Google Docs to pdf/docx/txt/etc.)
- `import` (upload with conversion to Google Docs format)
- Service account support
- Multiple account support

**What it does NOT do:**
- No document body editing (no replace, insert, append)
- No comments API
- No awareness/state tracking
- No markdown export
- No structured/JSON output mode
- No conflict detection
- Treats Google Docs as opaque files

### rclone (Google Drive backend)

**What it is:** Swiss-army knife for cloud storage with 40+ backends.
**What it does:**
- `ls` / `lsf` / `lsd` / `copy` / `move` / `delete` / `mkdir` / `cat` / `about`
- FUSE mount (`rclone mount`) -- virtual filesystem
- Bidirectional sync (`rclone bisync`)
- Server-side copy/move (no re-download)
- Export Google Docs as configurable format (pdf, docx, odt, txt, html, etc.)
- Import with conversion (upload .docx, get Google Doc)
- Shared drives / team drives support
- Bandwidth limiting, retries, checksums
- Filter rules (include/exclude patterns)

**What it does NOT do:**
- No Google Docs content editing (no replace/insert -- file-level only)
- No comments API
- No document structure awareness
- No awareness/state tracking
- No Google Docs API integration (only Drive API)
- Generic tool -- not optimized for any specific backend

### google-drive-ocamlfuse

**What it is:** FUSE filesystem for Google Drive (Linux-focused).
**What it does:**
- Mount Google Drive as local filesystem
- Standard file operations (read/write/delete/rename) via filesystem
- Google Docs exported as configurable format
- Multiple account support
- Trash support

**What it does NOT do:**
- No document editing API (file-level abstraction only)
- No comments
- No macOS support (Linux FUSE only)
- No awareness/state tracking
- Stale development (LOW confidence -- needs verification)

### Other tools (skicka, gdown, etc.)

- **skicka** (Google): Upload/download only, archived.
- **gdown**: Download public files only, no auth for private.
- **google-api-python-client**: Raw API wrapper, not a CLI.
- **insync / odrive**: Desktop sync apps, not CLI tools.

### Key Observation

**No existing CLI tool operates at the Google Docs content level.** Every tool treats Google Docs as opaque files to upload/download/export. None of them:
- Edit document content (replace, insert, delete text)
- Manage comments and replies
- Track document state or detect changes
- Provide token-efficient output for AI agents
- Offer conflict detection for concurrent editing

This is the fundamental gap gdoc fills. The entire "document content manipulation via CLI" category is unoccupied.

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Authentication (OAuth2)** | Can't do anything without it | MEDIUM | Service account support adds complexity; OAuth2 refresh token flow is standard |
| **Read document content (`cat`)** | Core read primitive; every CLI has this | LOW | Drive API `files.export` to markdown -- native since July 2024 |
| **List files (`ls`)** | Unix users expect `ls` | LOW | Drive API `files.list` with pagination |
| **Search files (`find`)** | Users need to locate docs | LOW | Drive `files.list` with query syntax |
| **Document info (`info`)** | Metadata inspection is fundamental | LOW | Single `files.get` call |
| **Find and replace (`replace`)** | Core edit primitive for agents and humans | LOW | Docs API `replaceAllText` -- single API call, safe for concurrent editing |
| **Full document push (`push`)** | Agents need to write full documents | MEDIUM | Drive API `files.update` with media upload; requires conflict detection |
| **Append text (`append`)** | Common write pattern -- add to end | LOW | Get doc length + `insertText` at end index |
| **URL-to-ID resolution** | Users paste URLs constantly | LOW | Regex extraction from Google Docs/Drive URLs |
| **Exit codes** | Scripts and agents need programmatic error detection | LOW | 0=success, nonzero=error type |
| **Stdout/stderr separation** | Data to stdout, messages to stderr | LOW | Essential for piping: `gdoc cat DOC > file.md` |
| **JSON output mode (`--json`)** | Machine-parseable output for scripts and agents | MEDIUM | Every command needs dual output paths |
| **Error messages (not stack traces)** | Clean errors, not Python tracebacks | LOW | Catch exceptions, print terse messages |
| **Create document (`new`)** | Agents and users need to create docs | LOW | Drive API `files.create` |
| **Comment listing (`comments`)** | Comments are primary collaboration channel | LOW | Drive API `comments.list` |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required in a generic Drive CLI, but critical for the AI-agent use case.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Awareness system (change detection)** | Agents lack persistent visual context; "what changed since I last looked" is the single most valuable feature for agents | MEDIUM | 2 API calls per command (pre-flight check); state stored in `~/.gdoc/state/` |
| **Token-efficient default output** | AI agents pay per token; terse output by default saves real money at scale | LOW | Design discipline, not code complexity |
| **Inline comment annotations (`cat --comments`)** | Agents see comments in context, anchored to the text they reference -- like a human reading the doc | HIGH | Requires comment anchor matching, paragraph injection, HTML comment formatting |
| **Conflict detection (push safety)** | Prevents agents from silently overwriting human edits -- critical trust feature | MEDIUM | Compare stored version against current before write operations |
| **Anchored comments** | Agents can comment on specific text, not just the whole doc -- enables precise feedback | MEDIUM | Requires `quotedFileContent` in comments API; need to find text position |
| **`--quiet` mode** | Batch operations skip awareness overhead (saves 2 API calls per command) | LOW | Skip pre-flight check |
| **Insert at index (`insert`)** | Surgical editing at specific positions -- needed for structured document manipulation | LOW | Docs API `insertText`; complexity is in the agent knowing the right index |
| **Delete range (`delete`)** | Surgical deletion -- complement to insert | LOW | Docs API `deleteContentRange` |
| **Suggestion mode (`suggest`)** | Agents propose changes as suggestions, humans accept/reject -- maintains human-in-the-loop | HIGH | Docs API `suggestInsertText` / `suggestDeleteContentRange`; suggestion state tracking |
| **Share management (`share`)** | Agents can grant access as part of workflows | LOW | Drive API `permissions.create` |
| **Copy document (`cp`)** | Agents can duplicate docs (template workflows) | LOW | Drive API `files.copy` |
| **Diff (`diff`)** | Show difference between remote doc and local file | MEDIUM | Requires markdown export + diff algorithm; very useful for agents to understand what changed |
| **Comment reply and resolution** | Full comment lifecycle management -- agents participate in review threads | LOW | Drive API `replies.create` with action field |
| **Pipe-friendly stdin** | Commands accept input from pipes: `echo "text" \| gdoc append DOC_ID` | LOW | Read from stdin when no file argument given |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems. Things to deliberately NOT build.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **FUSE mount** | "Just mount it as a filesystem" | Massive complexity (filesystem semantics vs API semantics); latency; caching nightmares; conflict resolution impossible; already solved by rclone/ocamlfuse | Use `cat`/`push` for content; use rclone mount if users need filesystem access |
| **Bidirectional sync** | "Keep local and remote in sync" | Conflict resolution is intractable for concurrent editing; Google Docs have no file-level merge; sync state machine is a project unto itself | `cat` to read, `push` to write; `diff` to compare; awareness system detects external changes |
| **Rich formatting control** | "Set text bold, add headers, change fonts" | Docs API formatting requests are extremely verbose (each style is a separate request); markdown round-trip handles most formatting; per-character formatting is an infinite surface area | Use markdown: `push` converts markdown to formatted Google Doc; for fine control, use the Google Docs UI |
| **Spreadsheet (Sheets) support** | "Support Google Sheets too" | Completely different API (Sheets API v4); different data model (cells vs paragraphs); scope creep; deserves its own tool | Separate `gsheet` CLI if needed; or use rclone for sheet export |
| **Real-time collaboration / websocket** | "Stream changes in real-time" | Google Docs does not expose a real-time stream API; polling is the only option; websocket simulation adds massive complexity | Polling via `watch` command at configurable interval; awareness system handles "what changed" |
| **Interactive mode / REPL** | "Interactive shell for exploration" | Agents cannot use interactive modes; adds UI complexity; testing burden; not composable | Stay non-interactive; all operations are single commands; agents chain commands naturally |
| **GUI / TUI** | "Terminal UI for browsing" | Agents cannot use TUIs; humans have the Google Docs web UI; TUI is a separate product | Clean CLI output; `--verbose` for human readability |
| **Version history / revision browsing** | "Show me all versions" | Drive Revisions API has limitations (revisions are not semantic versions); exporting old revisions is unreliable for Google Docs; creates false expectations | `info` shows last modified time and version number; awareness system tracks version changes |
| **Offline mode / caching** | "Work offline" | Google Docs is inherently online; cached content goes stale immediately; cache invalidation is a hard problem | Fail fast with clear error when offline; `cat > local.md` for explicit local copy |
| **Google Drive folder tree management** | "Full Drive file manager" | Scope creep; rclone and gdrive already do this well; gdoc's value is document content, not file management | Minimal file ops (`ls`, `find`, `new`, `cp`); defer to rclone for heavy file management |

## Feature Dependencies

```
[Authentication (auth)]
    |
    +----> [Read ops: cat, ls, find, info]
    |          |
    |          +----> [Awareness system (state tracking)]
    |          |          |
    |          |          +----> [Conflict detection (for push/replace)]
    |          |          |
    |          |          +----> [Notification banner]
    |          |
    |          +----> [Inline comment annotations (cat --comments)]
    |                     |
    |                     +---requires---> [Comment listing (comments)]
    |
    +----> [Write ops: replace, insert, append, delete]
    |          |
    |          +---enhances---> [Conflict detection]
    |
    +----> [Push (full doc write)]
    |          |
    |          +---requires---> [Conflict detection]
    |
    +----> [Comment ops: comment, reply, resolve, reopen]
    |          |
    |          +---enhances---> [Awareness system]
    |
    +----> [File ops: new, cp, share]

[URL-to-ID resolution] ---enhances---> [All commands that take DOC_ID]

[JSON output mode] ---enhances---> [All commands]

[Token-efficient output] ---enhances---> [All commands]

[Suggestion mode] ---requires---> [Write ops infrastructure]
                  ---requires---> [Comment listing (to see suggestion state)]

[Diff] ---requires---> [cat (markdown export)]
```

### Dependency Notes

- **Awareness system requires read ops:** Must be able to `files.get` and `comments.list` before any command
- **Conflict detection requires awareness:** Built on top of version tracking from awareness system
- **Inline annotations require comments:** Must fetch and parse comments to inject them
- **Push requires conflict detection:** Full overwrite must check for concurrent edits
- **Suggestion mode requires write ops:** Built on top of the same Docs API batchUpdate infrastructure but with `suggest*` request types
- **All commands enhanced by URL-to-ID:** Resolution layer sits in front of all DOC_ID parameters

## AI Agent vs Human Needs Analysis

This is the critical dimension for gdoc. Features are annotated with who needs them most.

### AI Agents Need (Humans Don't Care Much)

| Need | Why | gdoc Feature |
|------|-----|--------------|
| Token efficiency | Agents pay ~$3-15/M tokens; verbose output wastes money | Terse default output, `--quiet` |
| State awareness | Agents forget between invocations; can't see the doc UI | Awareness system, notification banner |
| Conflict detection | Agents can't see the "someone else is editing" indicator | Version tracking, push blocking |
| Machine-parseable output | Agents parse output programmatically | `--json` flag on every command |
| No interactive prompts | Agents cannot respond to "are you sure?" | `--force` for destructive ops, never prompt |
| Predictable exit codes | Agents branch on exit codes | Defined exit code scheme |
| Inline comments in content | Agents need full document context in one read | `cat --comments` |
| Deterministic behavior | Same input should produce same output | No randomness, no interactive behavior |

### Humans Need (Agents Don't Care Much)

| Need | Why | gdoc Feature |
|------|-----|--------------|
| Pretty formatting | Humans read terminal output visually | `--verbose` flag |
| Progress indicators | Humans want feedback during long operations | Progress bars for uploads (optional) |
| Tab completion | Humans type commands manually | Shell completion scripts (optional) |
| Help text | Humans forget command syntax | `--help` on every command |
| Color output | Humans scan colored text faster | ANSI colors in `--verbose` mode |
| Config file | Humans set personal preferences | `~/.gdoc/config.yaml` (optional) |

### Both Need

| Need | Why | gdoc Feature |
|------|-----|--------------|
| Authentication | Both need to authorize | OAuth2 flow |
| Read content | Both need to see doc content | `cat` |
| Edit content | Both need to change docs | `replace`, `insert`, `append`, `delete` |
| Comment management | Both participate in reviews | `comments`, `comment`, `reply`, `resolve` |
| Search | Both need to find docs | `ls`, `find` |
| Error handling | Both need to know what went wrong | Clean error messages + exit codes |

## MVP Definition

### Launch With (v1)

Minimum viable product -- what's needed to validate that AI agents can effectively use gdoc.

- [ ] `auth` -- OAuth2 flow with token storage (nothing works without this)
- [ ] `cat` -- Export doc as markdown (core read operation)
- [ ] `cat --comments` -- Inline comment annotations (key differentiator, validates the concept)
- [ ] `ls` -- List files in folder
- [ ] `find` -- Search by name/content
- [ ] `info` -- Document metadata
- [ ] `replace` -- Find and replace text (core agent edit primitive)
- [ ] `insert` -- Insert text at index
- [ ] `append` -- Append text to end
- [ ] `delete` -- Delete text range
- [ ] `push` -- Full document overwrite from markdown
- [ ] `comments` -- List comments
- [ ] `comment` -- Add comment (unanchored)
- [ ] `reply` -- Reply to comment
- [ ] `resolve` / `reopen` -- Comment lifecycle
- [ ] Awareness system -- Change detection on every command
- [ ] Conflict detection -- Block push when doc changed since last read
- [ ] URL-to-ID resolution
- [ ] `--json` output mode
- [ ] `--quiet` mode (skip awareness)
- [ ] `new` -- Create document
- [ ] Exit codes (0/1/2/3)
- [ ] Stdout/stderr separation

### Add After Validation (v1.x)

Features to add once core is working and agents are using it.

- [ ] `diff` -- Compare remote vs local (frequently requested once agents start editing)
- [ ] `share` -- Permission management (workflow automation)
- [ ] `cp` -- Copy/duplicate documents (template workflows)
- [ ] `suggest` -- Suggestion mode edits (human-in-the-loop workflows)
- [ ] `export` -- Export as PDF/DOCX/HTML
- [ ] `--verbose` mode -- Pretty output for human CLI users
- [ ] Shell completion scripts
- [ ] Service account authentication (for unattended/server use)
- [ ] `cat --plain` -- Plain text export (alternative to markdown)
- [ ] Anchored comments (comment on specific text, not just the doc)

### Future Consideration (v2+)

Features to defer until product-market fit is established.

- [ ] `watch` -- Poll for changes at interval (long-running awareness)
- [ ] Batch operations (multiple edits in single batchUpdate call)
- [ ] Multiple account support
- [ ] Config file (`~/.gdoc/config.yaml`)
- [ ] `pull` -- Alias for `cat > file` with state tracking
- [ ] Shared drive support
- [ ] Folder operations (mkdir, mv)
- [ ] `--format` flag for cat (choose export format)

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| auth | HIGH | MEDIUM | P1 |
| cat (markdown) | HIGH | LOW | P1 |
| cat --comments | HIGH | HIGH | P1 |
| replace | HIGH | LOW | P1 |
| awareness system | HIGH | MEDIUM | P1 |
| conflict detection | HIGH | MEDIUM | P1 |
| ls / find | HIGH | LOW | P1 |
| insert / append / delete | MEDIUM | LOW | P1 |
| push | HIGH | MEDIUM | P1 |
| comments CRUD | HIGH | LOW | P1 |
| --json | HIGH | MEDIUM | P1 |
| URL-to-ID | HIGH | LOW | P1 |
| new | MEDIUM | LOW | P1 |
| info | MEDIUM | LOW | P1 |
| diff | HIGH | MEDIUM | P2 |
| share | MEDIUM | LOW | P2 |
| cp | MEDIUM | LOW | P2 |
| suggest | HIGH | HIGH | P2 |
| export (pdf/docx) | LOW | LOW | P2 |
| watch | MEDIUM | MEDIUM | P3 |
| service accounts | MEDIUM | MEDIUM | P2 |
| batch operations | MEDIUM | HIGH | P3 |
| shell completions | LOW | LOW | P3 |
| config file | LOW | MEDIUM | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | gdrive | rclone | ocamlfuse | gdoc (ours) |
|---------|--------|--------|-----------|-------------|
| List files | Yes | Yes | Yes (filesystem) | Yes |
| Upload/download files | Yes | Yes | Yes (filesystem) | Push/cat only |
| Export Google Docs | Yes (limited formats) | Yes (configurable) | Yes (configurable) | Yes (markdown native) |
| Import to Google Docs | Yes | Yes | Yes (write to mount) | Yes (push markdown) |
| Edit document content | No | No | No | **Yes (replace/insert/delete)** |
| Comments CRUD | No | No | No | **Yes** |
| Inline comment annotations | No | No | No | **Yes** |
| Awareness / state tracking | No | No | No | **Yes** |
| Conflict detection | No | No | No | **Yes** |
| Token-efficient output | No | No | N/A | **Yes** |
| JSON output | No | Yes (some commands) | N/A | **Yes** |
| FUSE mount | No | Yes | Yes | No (anti-feature) |
| Bidirectional sync | No | Yes | No | No (anti-feature) |
| Multiple cloud backends | No | Yes (40+) | No | No (Google only) |
| Share/permissions | Yes | No | No | Yes |
| Service accounts | Yes | Yes | Yes | Planned (v1.x) |
| Suggestion mode | No | No | No | Planned (v1.x) |
| Search | No | No | N/A | Yes |

**Key takeaway:** gdoc occupies a completely unique niche. No existing tool edits Google Docs content, manages comments, or provides agent-oriented features. The competition is in file management (upload/download/sync), not document manipulation. gdoc does not compete with rclone or gdrive -- it complements them.

## Sources

- gdrive (glotlabs/gdrive): Training data knowledge of v3 CLI commands and capabilities. **Confidence: MEDIUM** -- needs verification against current GitHub README.
- rclone: Training data knowledge of rclone Google Drive backend. **Confidence: HIGH** -- well-documented, stable feature set.
- google-drive-ocamlfuse: Training data knowledge. **Confidence: LOW** -- project may have changed status; needs verification.
- Google Docs API v1: Training data knowledge of batchUpdate, suggestions, comments via Drive API. **Confidence: HIGH** -- API is stable and well-documented.
- Google Drive API v3: Training data knowledge. **Confidence: HIGH** -- API is stable.
- AI agent needs analysis: Based on first-principles reasoning about LLM agent constraints (context windows, token costs, lack of visual UI, stateless invocations). **Confidence: HIGH** -- derived from well-understood constraints.

**Research limitation:** All external research tools (WebSearch, WebFetch, Exa, GitHub CLI, Context7) were unavailable during this research session. All findings are based on training data (cutoff: May 2025). Feature claims about competitors should be verified against current documentation before making final decisions.

---
*Feature research for: Google Docs/Drive CLI for AI agents*
*Researched: 2026-02-07*
