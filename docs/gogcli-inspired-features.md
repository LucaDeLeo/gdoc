# gogcli-Inspired Feature Plan

Features inspired by [gogcli](https://github.com/steipete/gogcli), assessed against gdoc's architecture and AI-agent-first design.

**Status**: 5 of 9 features implemented (v0.2.0). Remaining 4 are larger efforts.

| # | Feature | Status |
|---|---------|--------|
| 6 | Get single comment by ID | **Done** |
| 7 | Resolve with message | **Done** |
| 9 | `--plain` output mode (TSV) | **Done** |
| 11 | Confirmation for destructive ops | **Done** |
| 12 | Command allowlist | **Done** |
| 1 | Docs API `cat` | Planned |
| 2 | Tabs support | Planned |
| 4 | Native table insertion | Planned |
| 5 | Image import on `new --file` | Planned |

---

## 1. Docs API `cat` for finer-grained control

**What**: Add a `--body` flag to `cat` that reads document content via the Docs API (`documents().get()`) instead of Drive export. Extract plain text by walking `body.content` elements.

**Why**: Drive export returns markdown/plaintext but has no truncation control. The Docs API lets us add `--max-bytes` truncation (gogcli does this), tab-aware reading, and structured access to tables/headings/lists.

**Current gdoc**: `cat` calls `export_doc(doc_id, mime_type="text/markdown")` via Drive API. No truncation, no tab awareness.

**gogcli approach**: `DocsCatCmd` calls `svc.Documents.Get(id)` then walks `doc.Body.Content` elements, extracting text from paragraphs (including nested table cells and ToC). Has `--max-bytes` with a `appendLimited()` helper that stops once the byte budget is hit.

**Implementation plan**:

1. **New API function** in `gdoc/api/docs.py`:
   - `get_document_text(doc_id, max_bytes=0) -> str` — calls `documents().get()`, walks body elements, extracts plain text with optional truncation.
   - Walk `paragraph.elements[].textRun.content` for text, handle `table` elements by tab-joining cells, handle `tableOfContents`.
   - We already have `get_document()` that returns the full document — build on it.

2. **New flag** on `cat`:
   - `--max-bytes N` — truncate output at N bytes (0 = unlimited, default 0).
   - When `--max-bytes` is set, use the Docs API path automatically (Drive export can't truncate server-side).
   - The existing `--plain` flag already uses Drive export with `text/plain`. With `--max-bytes`, we use Docs API plain text extraction instead.

3. **Tests**: Mock `documents().get()` response with body content, verify text extraction and truncation.

**Files to modify**: `gdoc/api/docs.py`, `gdoc/cli.py` (cat parser + handler), new test file `tests/test_cat_body.py`.

---

## 2. Tabs support

**What**: Support multi-tab Google Docs via `--tab` and `--all-tabs` flags on `cat`, plus a `tabs` subcommand to list tabs.

**Why**: Google Docs now supports multiple tabs per document. Without tab awareness, `cat` only returns the default tab's content, silently ignoring others.

**Current gdoc**: No tab awareness. `cat` exports via Drive (returns default tab only). `get_document()` in `api/docs.py` doesn't pass `includeTabsContent`.

**gogcli approach**: Uses `IncludeTabsContent(true)` on the Docs API. `flattenTabs()` recursively collects all tabs (including nested child tabs). `findTab()` looks up by ID or case-insensitive title. `--tab "Notes"` reads one tab, `--all-tabs` reads all with headers.

**Implementation plan**:

1. **New API functions** in `gdoc/api/docs.py`:
   - `get_document_tabs(doc_id) -> list[dict]` — calls `documents().get(includeTabsContent=True)`, returns flat list of tab dicts with `{id, title, index, body}`.
   - `get_tab_text(tab, max_bytes=0) -> str` — extract text from a single tab's body.
   - Helper: `flatten_tabs(tabs)` — recursive flattener for nested child tabs.

2. **New `tabs` subcommand**:
   - `gdoc tabs DOC` — list all tabs (id, title, index).
   - Terse: `tab_id\ttitle`, verbose adds index + nesting level, JSON returns full list.

3. **New flags on `cat`**:
   - `--tab NAME_OR_ID` — read only this tab (case-insensitive title match, falls back to ID match).
   - `--all-tabs` — read all tabs, separated by `=== Tab: Title ===` headers.
   - `--tab` and `--all-tabs` mutually exclusive.
   - These flags force the Docs API path (Drive export doesn't support tabs).

4. **Tests**: Mock multi-tab document response, verify tab listing, single tab read, all-tabs output.

**Files to modify**: `gdoc/api/docs.py`, `gdoc/cli.py` (new subcommand + cat flags), new tests.

---

## 4. Markdown-to-Docs native table insertion

**What**: Enhance our `edit` command's markdown formatting to support native Google Docs table insertion via the Docs API.

**Current gdoc**: `gdoc/mdparse.py` parses markdown and generates `batchUpdate` requests for headings, bold, italic, code, links, and bullet lists. Tables are **not supported** — if the replacement text contains a markdown table, it's inserted as plain text.

**gogcli approach**: `docs_formatter.go` has a `TableData` struct. During markdown parsing, tables are collected separately. After the main `batchUpdate` inserts text + formatting, a second pass uses `TableInserter` to create native Google Docs tables via `insertTable` + cell-by-cell `insertText` requests.

**Implementation plan**:

1. **Extend `gdoc/mdparse.py`**:
   - Add `MDTable` element type with a `cells: list[list[str]]` field.
   - Parser recognizes markdown table syntax (`| col1 | col2 |` with separator row `|---|---|`).
   - `to_docs_requests()` emits a placeholder newline for each table and returns `tables: list[TableData]` alongside the existing requests.

2. **New table insertion logic** in `gdoc/api/docs.py`:
   - `insert_native_table(doc_id, index, cells) -> int` — inserts a table via `insertTable` request, then populates cells with `insertText` requests.
   - Each cell needs its own `batchUpdate` call after the table structure exists (or read-back the document to find cell positions).

3. **Integration in `replace_formatted()`**:
   - After the main `batchUpdate`, loop over `tables` and call `insert_native_table()` for each, adjusting indices for document growth.

4. **Tests**: Mock Docs API batchUpdate, verify table parsing + request generation.

**Files to modify**: `gdoc/mdparse.py`, `gdoc/api/docs.py`, `gdoc/cli.py` (no CLI changes needed), tests.

---

## 5. Image import on `new --file`

**What**: Add `--file` flag to `gdoc new` that creates a doc from a local markdown file, including embedded images.

**Current gdoc**: `new` creates a blank document via `create_doc()`. No file import.

**gogcli approach**: Two-pass:
1. Parse markdown, extract `![alt](path)` references, replace with `<<IMG_N>>` placeholders, upload cleaned markdown via Drive (auto-converts to Docs).
2. Read back the created doc to find placeholder positions, upload local images to Drive as temp files (set public read), insert inline images at placeholder positions via `batchUpdate`, then clean up temp files.

**Implementation plan**:

1. **New `--file` flag on `new`**:
   - `gdoc new "Title" --file ./doc.md` — creates doc from markdown file.
   - Without `--file`, behaves as today (blank doc).

2. **Image extraction** in new `gdoc/mdimport.py`:
   - `extract_images(content, base_dir) -> (cleaned_content, list[ImageRef])` — regex-extracts `![alt](path)`, replaces with `<<IMG_N>>` placeholders, returns cleaned markdown + image metadata.
   - `ImageRef` has `index`, `alt`, `original_ref`, `is_remote`.

3. **Create flow**:
   - Upload cleaned markdown via `create_doc()` with `media_body` (Drive handles markdown→Docs conversion).
   - Modify `gdoc/api/drive.py`: add `create_doc_from_content(title, content, mime_type, folder_id)` that uses `files().create().media()`.
   - Read back document via Docs API to find placeholder positions.
   - For local images: upload to Drive as temp file, set public permission, get URL.
   - Insert inline images via `batchUpdate` with `insertInlineImage` requests.
   - Clean up temp Drive files.

4. **Security**: Validate image paths resolve within the markdown file's directory (no path traversal). Only allow png/jpg/gif.

5. **Tests**: Mock Drive + Docs API calls, verify placeholder extraction, image insertion requests.

**Files to modify**: `gdoc/api/drive.py`, `gdoc/api/docs.py`, `gdoc/cli.py`, new `gdoc/mdimport.py`, tests.

---

## 6. Get single comment by ID -- DONE

**What**: Add `gdoc comment-info DOC COMMENT_ID` to fetch a single comment with full detail.

**Current gdoc**: `comments` lists all comments. No way to get a single comment by ID without listing all.

**gogcli approach**: `DocsCommentsGetCmd` calls `svc.Comments.Get(docID, commentID)` with fields including anchor, replies, quoted content.

**Implementation plan**:

1. **New API function** in `gdoc/api/comments.py`:
   - `get_comment(file_id, comment_id) -> dict` — calls `comments().get()` with full field set including `quotedFileContent`, `anchor`, `replies`.

2. **New `comment-info` subcommand**:
   - `gdoc comment-info DOC COMMENT_ID`
   - Terse: id, author, content, resolved status, reply count.
   - Verbose: adds created/modified times, quoted text, anchor, full reply list.
   - JSON: full comment dict from API.

3. **Tests**: Mock `comments().get()`, verify output formatting.

**Files to modify**: `gdoc/api/comments.py`, `gdoc/cli.py`, tests.

---

## 7. Resolve with message -- DONE

**What**: Add `--message` / `-m` flag to `gdoc resolve` to include a message when resolving.

**Current gdoc**: `cmd_resolve()` calls `create_reply(doc_id, comment_id, action="resolve")` with no content.

**gogcli approach**: `DocsCommentsResolveCmd` has `--message` that sets `reply.Content` alongside `action: "resolve"`.

**Implementation plan**:

1. **CLI change**: Add `--message` / `-m` optional arg to `resolve` subparser.

2. **Handler change**: Pass `content=args.message` to `create_reply()` when provided. Our `create_reply()` already supports both `content` and `action` params — no API layer changes needed.

3. **Tests**: Verify resolve with and without message.

**Files to modify**: `gdoc/cli.py`, tests.

---

## 9. `--plain` output mode (TSV) -- DONE

**What**: Add `--plain` as a fourth output mode alongside `--json` and `--verbose`. Produces stable tab-separated output with no decoration, suitable for piping to `cut`, `awk`, etc.

**Current gdoc**: Three modes — terse (default), verbose, json. Terse already uses tabs in some places (ls, comments) but isn't consistently TSV and includes decorative text.

**gogcli approach**: `--plain` produces stable TSV on stdout (tabs preserved, no alignment, no colors). Human-facing hints go to stderr.

**Implementation plan**:

1. **New flag** in parser: `--plain` added to the mutually exclusive group with `--json` and `--verbose`.

2. **`get_output_mode()`** in `gdoc/format.py`: return `"plain"` when `--plain` is set.

3. **Output changes per command**: Each command handler checks for `mode == "plain"` and emits clean TSV lines (no headers, no decorative text, no "OK" prefixes). Key-value data uses `key\tvalue\n` format. List data uses one row per item with tab-separated fields.

4. **Incremental rollout**: Start with the commands agents use most (`cat`, `ls`, `find`, `comments`, `info`), then extend to all.

5. **Tests**: Verify plain output has no decoration, stable field order.

**Files to modify**: `gdoc/format.py`, `gdoc/cli.py`, tests.

---

## 11. Confirmation for destructive operations -- DONE

**What**: Add a confirmation prompt before destructive operations (delete-comment) unless `--force` is passed.

**Current gdoc**: `delete-comment` executes immediately with no confirmation. No `--force` flag on delete operations.

**gogcli approach**: `confirmDestructive()` prompts the user unless `--force` is set. For non-interactive environments (`--no-input`), it fails instead of prompting.

**Implementation plan**:

1. **New utility** in `gdoc/util.py`:
   - `confirm_destructive(message, force=False) -> None` — if not force, prints message to stderr and prompts for `y/N`. Raises `GdocError(exit_code=3)` on decline or non-interactive stdin.

2. **Apply to `delete-comment`**:
   - Add `--force` flag to `delete-comment` parser.
   - Call `confirm_destructive()` before the API call.

3. **Agents bypass**: Agents using `gdoc` will always pass `--force` (or `--quiet` could imply `--force` for destructive ops). This is agent-friendly because agents know what they're doing; the prompt protects human users.

4. **Tests**: Verify prompt appears when not forced, verify `--force` skips it, verify non-TTY raises error.

**Files to modify**: `gdoc/util.py`, `gdoc/cli.py`, tests.

---

## 12. Command allowlist -- DONE

**What**: Add `--allow-commands` flag (and `GDOC_ALLOW_COMMANDS` env var) to restrict which subcommands are available.

**Why**: When `gdoc` is used by an untrusted or sandboxed AI agent, the operator may want to restrict it to read-only commands (e.g., only `cat`, `comments`, `info`, `ls`, `find`).

**gogcli approach**: `--enable-commands calendar,tasks` allowlists top-level command groups. Unlisted commands are rejected at dispatch time.

**Implementation plan**:

1. **New global flag**: `--allow-commands CMDS` (comma-separated list of allowed subcommands).

2. **Env var**: `GDOC_ALLOW_COMMANDS` as fallback.

3. **Enforcement in `main()`**: After parsing, before dispatch, check if `args.command` is in the allowlist. If not, print `ERR: command not allowed: {command}` and exit 3.

4. **Tests**: Verify allowed commands work, disallowed commands rejected, env var works.

**Files to modify**: `gdoc/cli.py`, tests.

---

## Implementation Order

Completed (v0.2.0):

1. ~~**#9 `--plain` output mode**~~ -- Done
2. ~~**#7 Resolve with message**~~ -- Done
3. ~~**#6 Get single comment**~~ -- Done
4. ~~**#11 Destructive confirmation**~~ -- Done
5. ~~**#12 Command allowlist**~~ -- Done

Remaining (larger efforts):

6. **#1 Docs API cat** — 2 files, ~80 lines
7. **#2 Tabs support** — 2 files, ~120 lines (builds on #1)
8. **#4 Native table insertion** — 2 files, ~100 lines
9. **#5 Image import** — 4 files, ~200 lines (most complex)
