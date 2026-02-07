# Project Research Summary

**Project:** gdoc (Token-efficient CLI for Google Docs & Drive)
**Domain:** Python CLI wrapper for Google Docs/Drive APIs (AI agent-first design)
**Researched:** 2026-02-07
**Confidence:** MEDIUM

## Executive Summary

gdoc is a Python CLI tool that wraps Google Docs and Drive APIs with a focus on AI agent workflows. The product fills a unique gap in the CLI ecosystem: no existing tool (gdrive, rclone, ocamlfuse) operates at the document content level—they treat Google Docs as opaque files. gdoc enables surgical text editing (replace, insert, delete), comment management, and provides an "awareness system" that addresses agents' core challenge: lack of persistent visual context. The "what changed since I last looked" capability is the product's killer feature.

The recommended approach is simple and intentional: Python 3.10+, argparse (no extra CLI dependencies), google-api-python-client for direct API access, and markdown-native export/import using Drive's built-in conversion. The architecture is layered (CLI → Middleware → API Client → Auth) with clear separation of concerns. The key technical decisions prioritize zero dependencies over developer convenience (argparse vs click/typer) and token efficiency over human-friendly output (terse defaults, JSON mode, no colors).

The critical risks are OAuth2 edge cases (7-day token expiry in "Testing" status, scope mismatch on updates), batchUpdate index invalidation (operations must be reverse-sorted by position), and the push command silently destroying concurrent edits. These are all preventable with design choices made upfront: proper auth error handling, reverse-order operation sorting, and version guards on destructive writes. Markdown export fidelity is lossy (images, complex tables, headers/footers don't survive) — users must understand this is not a round-trip format for richly formatted docs.

## Key Findings

### Recommended Stack

Python 3.10+ with stdlib argparse for zero-dependency CLI parsing, google-api-python-client (>=2.150.0) for Drive v3 and Docs v1 APIs, and google-auth-oauthlib for OAuth2 installed-app flow. The stack is intentionally minimal: no markdown parsing library needed (Drive API handles conversion natively), no CLI framework beyond argparse (flat subcommand structure doesn't justify click/typer), and no external HTTP client (google-api-python-client manages its own transport).

**Core technologies:**
- **Python 3.10+**: Modern typing, match statements, and tomllib support — no reason to target older versions in 2026
- **argparse (stdlib)**: Zero dependencies, perfect for flat 15-subcommand structure, stays out of the way for custom output formatting
- **google-api-python-client**: Only official Python client for Drive/Docs APIs, no alternatives exist
- **google-auth-oauthlib**: Standard OAuth2 InstalledAppFlow for browser-based consent, no alternatives
- **hatchling + pyproject.toml**: Modern build system, simpler than setuptools, PEP 517 compliant

**Key decision: argparse over click/typer**. The project spec says "no dependencies, intentionally minimal" and the CLI is agent-first. Agents don't benefit from rich help formatting, color output, or shell completion. argparse's plain output is more token-efficient. The 15 subcommands are flat (no nested groups), so argparse's subparser pattern is perfectly sufficient.

**What NOT to use:**
- No markdown library (markdown/mistune/commonmark) — Drive API exports markdown natively, adds zero value
- No click/typer — adds dependencies to a zero-dependency project, rich output conflicts with token efficiency
- No requests/httpx — google-api-python-client has its own transport layer

### Expected Features

Research identified a stark competitor gap: no existing tool edits Google Docs at the content level. Every tool (gdrive, rclone) treats docs as opaque files. The entire "document content manipulation via CLI" category is unoccupied. This validates the product's core value proposition.

**Must have (table stakes):**
- OAuth2 authentication with token refresh and credentials.json management
- Read document content (cat) — export as markdown is core primitive
- List/search files (ls, find) — basic file discovery expected by CLI users
- Replace text (replace) — find-and-replace is the safest concurrent-edit primitive for agents
- Full document push — agents need wholesale writes despite concurrent edit risk
- Comment listing and CRUD — comments are primary collaboration channel
- URL-to-ID resolution — users paste docs.google.com URLs constantly
- JSON output mode (--json) — machine-parseable output essential for agents
- Exit codes — programmatic error detection for scripts

**Should have (competitive advantage):**
- **Awareness system** — "what changed since I last looked" solves agents' lack of visual context (HIGHEST VALUE)
- **Inline comment annotations** (cat --comments) — agents see comments anchored to text like humans do
- **Conflict detection** — prevent agents from silently overwriting human edits, critical trust feature
- **Token-efficient defaults** — terse output saves real money at scale (LLM API costs)
- Surgical edits (insert/delete at index) — complement to replace
- Anchored comments — comment on specific text, not whole doc
- Diff command — show what changed between remote and local
- Suggestion mode — agents propose changes, humans accept/reject (human-in-the-loop)

**Defer (v2+):**
- FUSE mount / bidirectional sync — massive scope creep, already solved by rclone
- Rich formatting control — Docs API formatting is verbose, markdown handles most cases
- Spreadsheets support — completely different API, deserves separate tool
- Interactive mode / TUI — agents can't use it, humans have Google Docs UI
- Version history browsing — API limitations make this unreliable
- Real-time websocket — Google doesn't expose real-time API

### Architecture Approach

Layered architecture with clear separation: CLI Layer (argparse dispatch) → Middleware Layer (awareness, formatting) → API Client Layer (Drive/Docs/Comments wrappers) → Auth Layer (OAuth2). Each layer depends only on layers below it (no upward imports). API wrappers return raw Python data structures (dicts/lists), CLI handlers orchestrate calls and pass results to formatters. State management is file-based JSON in ~/.gdoc/state/{DOC_ID}.json with atomic writes.

**Major components:**
1. **cli.py** — argparse subcommand dispatch, one handler per command (cat, push, replace, etc.), orchestrates pre-flight → API call → format → output
2. **api/drive.py, api/docs.py, api/comments.py** — Thin wrappers around google-api-python-client service objects, return raw data, zero formatting or CLI concerns
3. **notify.py** — Pre-flight awareness check (2 API calls: files.get + comments.list), compares against stored state, returns banner string for stderr
4. **state.py** — Per-doc JSON persistence (last version, last seen time, known comment IDs), atomic file writes, graceful corruption recovery
5. **format.py** — Output formatters (terse/json/verbose), pure functions that take dicts and return strings, no imports from API or CLI layers
6. **auth.py** — OAuth2 InstalledAppFlow with credential caching, lazy service factory with @lru_cache, transparent token refresh

**Build order:** Foundation (util, state, format) → Auth → API Client → Middleware (notify, annotate) → CLI. Each phase enables incremental delivery: Phases 1-3 give working cat/ls/auth, Phase 4 adds awareness banners, Phase 5 completes all commands.

**Key patterns:**
- Lazy service singleton (build API service once per invocation, cache with @lru_cache)
- Stderr for banners, stdout for data (pipe-safe design)
- Pre-flight as middleware (awareness check before every command unless --quiet)
- Output mode dispatch (--json/--verbose flag selects formatter)

### Critical Pitfalls

Research identified 7 critical pitfalls, all preventable with upfront design:

1. **batchUpdate index invalidation** — Multiple insert/delete operations in one batch invalidate indexes. Each mutation shifts downstream character positions. **Prevention:** Sort operations in reverse document order (highest index first). For gdoc, this affects insert/delete commands. The replace command (using replaceAllText) is immune because it doesn't use indexes.

2. **OAuth2 token expiry in "Testing" status** — Apps in "Testing" publish status have 7-day refresh token expiry. After 7 days, users must re-authenticate. **Prevention:** Set app to "In Production" before distributing. Handle invalid_grant errors gracefully (delete token.json, prompt re-auth). Store requested scopes in token.json and detect scope mismatches.

3. **push destroys concurrent edits** — files.update with media upload is last-write-wins, bypasses operational transforms. If a human is editing, their changes are silently destroyed. **Prevention:** Version guard in awareness system. Block push if version changed since last cat. Require --force to override with loud warning.

4. **Drive API rate limits (per-user AND per-project)** — Multiple dimensions: 12K QPM per-project shared across all users, ~12 QPM per-user for expensive ops. Awareness system (2 calls per command) hits limits fast in batch operations. **Prevention:** Implement exponential backoff on 429 errors, provide --quiet flag to skip awareness, consider caching awareness results for 30s.

5. **Markdown export fidelity is lossy** — Images export as links with ephemeral URLs, complex tables simplify, headers/footers are lost, nested formatting degrades. cat → push round-trip loses formatting. **Prevention:** Document limitations explicitly, never use push on docs created in Google UI with rich formatting, test with production-quality docs.

6. **Comment anchoring is fragile** — quotedFileContent.value matching is imprecise, anchors don't update when text moves, same text appears multiple times causes ambiguity. **Prevention:** Best-effort matching in annotated view (search for quoted text, anchor to first occurrence), warn if anchor text appears multiple times, handle orphaned comments gracefully.

7. **State file corruption and race conditions** — Concurrent CLI invocations, partial writes on crash, clock skew in timestamps. **Prevention:** Atomic writes (write to temp, os.rename), use version field not modifiedTime for change detection, graceful recovery from corrupted JSON (delete state file and start fresh).

## Implications for Roadmap

Based on combined research findings, the roadmap should follow dependency order from architecture research while addressing critical pitfalls early. The awareness system is architecturally complex but delivers the highest user value, so it must come early despite its dependencies.

### Phase 1: Foundation & Auth (No API Calls)
**Rationale:** Build leaf modules with zero Google API dependencies first. These are pure functions (util, format) or simple filesystem operations (state). Auth must come before any API calls. This phase establishes the base for all subsequent work.

**Delivers:**
- URL-to-ID extraction utility
- Output formatters (terse/JSON/verbose)
- Per-doc state persistence with atomic writes
- OAuth2 flow with token refresh and error handling
- Project structure and pyproject.toml

**Addresses:**
- Pitfall #7 (state corruption) via atomic writes from the start
- Pitfall #2 (OAuth edge cases) via comprehensive auth error handling

**Avoids:**
- Building on unstable foundation
- Auth failures blocking later testing

**Research flag:** Standard patterns, skip deep research. OAuth2 InstalledAppFlow is well-documented.

---

### Phase 2: Read Operations (cat, ls, find, info)
**Rationale:** Read operations are dependencies for the awareness system. Must be functional before building pre-flight checks. Enables early testing of API integration and markdown export quality. Delivers immediate value (basic doc reading).

**Delivers:**
- cat command (markdown export, no comments yet)
- ls/find commands (file listing and search)
- info command (document metadata)
- api/drive.py module
- Basic error handling and --json output

**Addresses:**
- Pitfall #5 (markdown fidelity) — test with production docs early to understand limitations
- Pitfall #4 (rate limits) — implement backoff/retry in API client from the start

**Uses:**
- argparse for subcommand dispatch (from STACK.md)
- google-api-python-client files.export for markdown (native conversion)

**Research flag:** Standard Drive API patterns, skip research. Markdown export fidelity needs testing but no additional research.

---

### Phase 3: Awareness System (Change Detection)
**Rationale:** Awareness is the killer feature and dependency for conflict detection (needed by push). Must come before write operations. Adds 2 API calls per command but delivers highest value for agents. Complexity: MEDIUM (state comparison logic, banner formatting).

**Delivers:**
- notify.py module (pre-flight checks)
- Version tracking and change detection
- Notification banners on stderr
- --quiet flag to skip awareness

**Addresses:**
- Pitfall #4 (rate limits) — --quiet flag essential for batch ops
- Core value prop: "what changed since I last looked"

**Implements:**
- Pre-flight middleware pattern from ARCHITECTURE.md
- State/API integration

**Research flag:** Standard patterns, skip research. Logic is straightforward state comparison.

---

### Phase 4: Write Operations (replace, insert, append, delete)
**Rationale:** Now that awareness exists, add write operations. Replace is safest (no index issues), build first. Insert/delete require reverse-order sorting. All write ops benefit from awareness (version tracking visible to agents).

**Delivers:**
- replace command (replaceAllText, OT-safe)
- insert/append/delete commands (index-based)
- api/docs.py module (batchUpdate wrappers)
- Reverse-order sorting for multi-operation batches

**Addresses:**
- Pitfall #1 (batchUpdate indexes) — reverse-order sorting from day one
- Table stakes: core editing primitives

**Implements:**
- Docs API batchUpdate pattern from ARCHITECTURE.md
- Index calculation and validation

**Research flag:** Skip research for replace (straightforward replaceAllText). Consider research for insert/delete if index calculation from markdown gets complex.

---

### Phase 5: Full Document Push
**Rationale:** Most dangerous operation (destroys concurrent edits). Must come after awareness system exists. Version guard is critical. Depends on Phase 2 (cat for version capture) and Phase 3 (awareness for version checking).

**Delivers:**
- push command with version guard
- Conflict detection (block if doc changed)
- --force override with warning
- Integration test: modify via UI, then push

**Addresses:**
- Pitfall #3 (push destroys edits) — version guard is required, not optional
- Pitfall #5 (markdown fidelity) — document push limitations

**Implements:**
- Conflict detection pattern from ARCHITECTURE.md
- Media upload with mime type conversion

**Research flag:** Standard Drive API files.update, skip research. Version guard logic is straightforward.

---

### Phase 6: Comments (CRUD, No Anchoring Yet)
**Rationale:** Basic comment operations (list, create, reply, resolve) are straightforward and high value. Defer inline anchoring to Phase 7 because anchoring is complex (text matching, ambiguity handling) and lower priority than core CRUD.

**Delivers:**
- comments command (list with pagination)
- comment command (create unanchored)
- reply/resolve/reopen commands
- api/comments.py module

**Addresses:**
- Table stakes: comment management
- Pitfall #6 (pagination) — must handle 100+ comments

**Uses:**
- Drive API comments + replies resources
- Format.py for output (terse/JSON)

**Research flag:** Standard Drive API, skip research. Comments API is well-documented.

---

### Phase 7: Inline Comment Annotations (cat --comments)
**Rationale:** High-value differentiator but complex. Depends on Phase 2 (cat) and Phase 6 (comments listing). Anchoring is best-effort due to API limitations. This phase delivers the "agents see comments in context" feature.

**Delivers:**
- annotate.py module
- cat --comments flag
- Comment injection into markdown
- Anchoring heuristics (first match, proximity)
- Orphaned comment handling

**Addresses:**
- Pitfall #6 (anchoring fragility) — document best-effort approach
- Differentiator: inline annotations

**Implements:**
- String processing for comment injection
- quotedFileContent matching logic

**Research flag:** Consider phase-specific research if anchoring heuristics are complex. Core pattern is straightforward (find quoted text, inject HTML comment), but handling edge cases (multiple matches, orphans) may need deeper thought.

---

### Phase 8: Polish & v1.x Features
**Rationale:** After core MVP (Phases 1-7), add quality-of-life features that improve usability without changing core architecture.

**Delivers:**
- diff command (compare remote vs local)
- share command (permissions)
- cp command (copy docs)
- export command (PDF/DOCX)
- --verbose mode for human users
- Service account auth (headless use)

**Addresses:**
- Post-launch feature requests
- Human users (vs pure agent focus)

**Research flag:** Skip research for share/cp (standard Drive API). Consider research for diff if diffing algorithm choice matters.

---

### Phase Ordering Rationale

- **Foundation → Auth → API** follows dependency graph from ARCHITECTURE.md. Can't call APIs without auth, can't test anything without foundation.
- **Read before Write** enables early testing and addresses markdown fidelity early (Pitfall #5). Also, awareness system (Phase 3) depends on read operations.
- **Awareness before Push** is critical. Push without version guard (Pitfall #3) is unacceptable. Awareness must exist first.
- **Replace before Insert/Delete** because replace is OT-safe and simpler (no index issues). Agents should prefer replace for most edits.
- **Comments CRUD before Anchoring** because basic comment management delivers value immediately. Anchoring is complex best-effort work that can come later.

### Research Flags

**Phases likely needing deeper research during planning:**
- **Phase 7 (Comment Anchoring):** Best-effort heuristics for text matching when quotedFileContent appears multiple times. May need experimentation with real docs.
- **Phase 8 (Diff):** If implementing smart diff (not just line-by-line), may need research on diff algorithms for markdown.

**Phases with standard patterns (skip research-phase):**
- **Phase 1-6:** All covered by well-documented API patterns. OAuth2, Drive API, Docs API batchUpdate, Comments API are mature and extensively documented.
- **Phase 8 (share/cp/export):** Standard Drive API operations, no complexity.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | argparse/google-api-python-client patterns are mature and stable. Version numbers are MEDIUM confidence (from training data, need verification with pip index). |
| Features | HIGH | Competitor gap analysis based on well-known tools (gdrive, rclone). Feature categorization (table stakes vs differentiators) is derived from first-principles agent needs analysis. |
| Architecture | HIGH | Layered architecture, separation of concerns, and build order follow established Python CLI patterns. google-api-python-client usage is well-documented. |
| Pitfalls | MEDIUM | All 7 pitfalls are documented in Google API docs or community knowledge, but external verification tools were unavailable. Rate limit numbers and OAuth "Testing" behavior should be verified against current Google Cloud Console documentation. |

**Overall confidence:** MEDIUM

The architectural recommendations and technical approach are HIGH confidence — these are based on mature, stable libraries and well-established patterns. The MEDIUM overall rating reflects uncertainty in version numbers (can't verify with live PyPI) and some Google Cloud Console specifics (7-day token expiry, current rate limit values) that should be verified during implementation.

### Gaps to Address

- **Exact version numbers:** All version pins in STACK.md are from training data (cutoff early 2025). Verify with `pip index versions <package>` or `uv pip compile` before pinning in pyproject.toml. The library choices themselves are HIGH confidence.

- **Markdown export current fidelity:** Google may have improved markdown export quality since training cutoff. Test with production docs (images, tables, footnotes) early in Phase 2 to understand current limitations. The fact that it's lossy is certain, but the degree may have changed.

- **OAuth2 "Testing" 7-day expiry:** Verify this is still enforced. Google may have changed OAuth policies. If still true, set app to "In Production" before distributing. Document this in auth setup.

- **Rate limit current values:** Google adjusts rate limits periodically. The ~12K QPM per-project and ~12 QPM per-user figures should be verified against current Drive API quotas documentation. The fact that limits exist and affect batch operations is certain, but exact numbers may differ.

- **InstalledAppFlow headless fallback:** Verify if run_console() is still available or fully deprecated. Google has been moving away from OOB flow. May need device flow fallback for headless environments. This affects service account auth (Phase 8) and SSH usage.

## Sources

### Primary (HIGH confidence)
- Google API Python Client official docs (googleapis.github.io/google-api-python-client) — API patterns, service construction, method chaining
- Google Auth Library docs (google-auth.readthedocs.io) — OAuth2 flow, token refresh, credential serialization
- Google Drive API v3 documentation (developers.google.com/drive/api) — export MIME types, files.list, comments API
- Google Docs API v1 documentation (developers.google.com/docs/api) — batchUpdate, replaceAllText, document structure
- Python argparse documentation (docs.python.org) — subparser patterns, argument parsing
- google-api-python-client source code and established usage patterns — testing with MagicMock, chained method calls

### Secondary (MEDIUM confidence)
- Training data knowledge of competitor tools (gdrive, rclone, ocamlfuse) through May 2025 — feature comparisons, capabilities, limitations
- Community patterns for Python CLI tools (pytest, ruff, hatchling adoption)
- Google Cloud Console OAuth consent screen behavior (Testing vs Production publish status)
- AI agent needs analysis based on first-principles reasoning about LLM constraints (token costs, stateless invocations, lack of visual UI)

### Tertiary (LOW confidence, needs verification)
- Exact version numbers for all dependencies (from training data, not live PyPI)
- Current rate limit values (Google adjusts these; verify before relying on specific numbers)
- Markdown export fidelity specifics (may have improved since training cutoff)
- OAuth2 run_console() availability (may be deprecated)

**Research limitation:** All external research tools (WebSearch, WebFetch, Context7, Exa, GitHub CLI) were unavailable during this research session. All findings are based on training data through early 2025. Architectural patterns and library choices are HIGH confidence (based on stable, mature APIs), but version-specific details and current Google Cloud policies should be verified during implementation.

---
*Research completed: 2026-02-07*
*Ready for roadmap: yes*
