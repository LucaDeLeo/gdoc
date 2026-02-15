# gogcli-Inspired Feature Plan

Features inspired by [gogcli](https://github.com/steipete/gogcli), assessed against gdoc's architecture and AI-agent-first design.

**Status**: All 9 features implemented.

| # | Feature | Status |
|---|---------|--------|
| 6 | Get single comment by ID | **Done** |
| 7 | Resolve with message | **Done** |
| 9 | `--plain` output mode (TSV) | **Done** |
| 11 | Confirmation for destructive ops | **Done** |
| 12 | Command allowlist | **Done** |
| 2 | Tabs support | **Done** |
| 1 | `--max-bytes` truncation on `cat` | **Done** |
| 4 | Native table insertion | **Done** |
| 5 | Image import on `new --file` | **Done** |

---

## 1. `--max-bytes` truncation on `cat` -- DONE

**What**: Client-side UTF-8-safe truncation after export, via `--max-bytes N` flag on `cat`.

**Implemented**:

- `_truncate_bytes(text, max_bytes)` helper: encodes to UTF-8, slices bytes, decodes with `errors="ignore"` for multi-byte safety
- `--max-bytes N` argument on `cat` (default 0 = unlimited)
- Applied across all 3 `cmd_cat` branches: tabs, comments, normal export
- Truncation applies to content, not the JSON envelope in `--json` mode
- 12 new tests in `tests/test_cat.py` (unit + integration)

**Files modified**: `gdoc/cli.py`, `tests/test_cat.py`.

---

## 2. Tabs support -- DONE

**What**: Support multi-tab Google Docs via `--tab` and `--all-tabs` flags on `cat`, plus a `tabs` subcommand to list tabs.

**Implemented in v0.3.0**:

- `gdoc tabs DOC` — lists all tabs (terse/plain/verbose/json), nested tabs indented
- `gdoc cat --tab NAME_OR_ID DOC` — reads a specific tab by title (case-insensitive) or ID
- `gdoc cat --all-tabs DOC` — reads all tabs with `=== Tab: Title ===` headers
- API: `flatten_tabs()`, `get_document_tabs()`, `get_tab_text()`, `_extract_paragraphs_text()` in `gdoc/api/docs.py`
- `--tab`/`--all-tabs` mutually exclusive with each other (argparse) and with `--comments` (handler)
- 45 new tests across `test_tabs_api.py`, `test_tabs_cmd.py`, `test_cat_tabs.py`

**Files modified**: `gdoc/api/docs.py`, `gdoc/cli.py`, `tests/test_cat.py`, 3 new test files.

---

## 4. Markdown-to-Docs native table insertion -- DONE

**What**: Native Google Docs table insertion via markdown tables in `edit` replacement text.

**Implemented**:

- `TableData` dataclass and table parsing in `gdoc/mdparse.py` — detects `| header |` + `|---|` separator pattern, consumes all data rows, normalizes column count
- `_find_table_cell_indices()` and `_insert_table()` in `gdoc/api/docs.py` — 3-step: insertTable, read-back for cell indices, insertText into cells (reverse order)
- `replace_formatted()` handles tables after main batchUpdate
- Tables in replacement text blocked with `--all` (multi-match), raises `GdocError(exit_code=3)`
- 8 new table parsing tests in `tests/test_mdparse.py`, 8 new table insertion tests in `tests/test_table_insert.py`

**Files modified**: `gdoc/mdparse.py`, `gdoc/api/docs.py`, `gdoc/cli.py`, new `tests/test_table_insert.py`.

---

## 5. Image import on `new --file` -- DONE

**What**: `gdoc new "Title" --file ./doc.md` creates a doc from a local markdown file with image support.

**Implemented**:

- New `gdoc/mdimport.py`: `extract_images()` extracts `![alt](path)`, replaces with `<<IMG_N>>` placeholders, validates local paths (no traversal, supported formats: png/jpg/jpeg/gif/webp)
- `ImageRef` dataclass with full metadata (is_remote, resolved_path, mime_type)
- `create_doc_from_markdown()`, `upload_temp_image()`, `delete_file()` in `gdoc/api/drive.py`
- `_cmd_new_from_file()` and `_insert_images()` in `gdoc/cli.py`
- Remote images: direct `insertInlineImage` with URL
- Local images: upload to Drive as temp, make public, insert, cleanup in `finally` block
- 11 tests in `tests/test_mdimport.py`, 9 tests in `tests/test_new_file.py`

**Files modified**: `gdoc/cli.py`, `gdoc/api/drive.py`, new `gdoc/mdimport.py`, new `tests/test_mdimport.py`, new `tests/test_new_file.py`.

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

Completed (v0.3.0):

6. ~~**#2 Tabs support**~~ -- Done

Completed (v0.4.0):

7. ~~**#1 `--max-bytes` truncation**~~ -- Done
8. ~~**#4 Native table insertion**~~ -- Done
9. ~~**#5 Image import (`new --file`)**~~ -- Done
