# Markdown task routes and shared interfaces

The product contract applies equally to CLI and MCP: reliable requested Markdown changes, then agent simplicity, then whole-task completion time. The general route is a complete native Markdown read of a selected tab, modification of that Markdown, and a revision-pinned native replacement of that tab. Targeted edits retain richer neighbors. Default reads identify their first-tab scope; sibling tabs are never implicitly collapsed.

## Task routes

| Tasks | Route and representation | Required evidence |
| --- | --- | --- |
| T01, T05, T13 | Targeted native edits; parsed inline styles with exact UTF-16 ranges. General rewrite only when requested. | Requested styles/text plus untouched supported and richer neighbors; no automatic rich rewrite. |
| T02, T03 | Native tab replacement of revised Markdown; paragraph boundaries and all moved blocks reconstructed. | Split/merge/move task success alongside protected sibling tabs. |
| T04, T08 | Shared native exporter/parser for default and selected-tab Markdown; code, quotes and rules retain explicit identity. | Changed combination round trip as well as unchanged content; literal code/fences/whitespace. |
| T06 | Native list requests, nesting tabs and explicit parsed starts; native list semantics retained. | Mixed nesting, empty items, starts/restarts and displayed order. API-limited numbering must be reported separately. |
| T07 | Rectangular TableData plus column alignments; native table stages and inline cell formatting. | Row/column changes and semantic headers; no incidental header bolding during unrelated edits. |
| T09 | One exact-ID-first, ambiguity-checking resolver shared by all routes. | Same selection through CLI and MCP, including ID/title collisions. |
| T10, T12 | Per-tab content exposure pinned to Docs revisions; gdoc's own state records each acknowledged revision. | Own successive writes, foreign edit, lost response, metadata/partial/full-read counterexamples. |
| T11 | Complete raw structure plus existing scoped selectors; complete export and explicit truncation metadata. | Actual locating and inspection task burden, without arbitrary default caps. |
| T14 | Parsed image references and native image operations; refresh existing references against current snapshot. | Insert/replace/move/remove and unrelated rewrite preservation; source limitations explicit. |
| T15 | Existing single-send comment routes and deterministic normalized matching. | Unicode-space annotation, early invalid-occurrence rejection, ambiguity/conflict outcomes. |

## Shared interfaces

- Parser offsets are Python code points; native request generation converts them to UTF-16 and accounts for nesting tabs, tables and image placeholders.
- Code blocks use `gdoc:code:v1` named ranges. Tables carry column alignments. Existing image references resolve against the checked native snapshot; acknowledged insertion replies carry old-to-new image aliases for successive writes.
- Writes record the acknowledged revision in gdoc's state, and a pulled file's frontmatter, rather than in every command's output; a rebased write warns and reports `rebased: true` in JSON. State advances only content actually exposed or sent at those revisions. A sampled Drive version never substitutes for that acknowledgment.
- Complete native reads record per-tab revision coverage. Partial output, metadata and summaries do not. Both interfaces invoke the same handlers; MCP accepts URL/image-reference inputs without requiring shell-local paths.

## Verified boundary

Phase 0 settled native mixed-list creation and current-snapshot image reuse, then
implemented both routes. Mixed child presets retain parent list identity; physical
paragraph indentation recovers nesting when Google's child list metadata resets it.
Arbitrary reconstructed numbering starts and image alt-text setters remain the exact
best-effort gaps documented in [README.md](README.md#supported-markdown). Native
numbering is retained, unavailable starts warn, and images are never silently omitted.

Offline acceptance covers both interfaces, changed combinations and protected scope.
The live combined fixture confirms native code identity, mixed numbering, aligned
styled tables, image references and identical CLI/MCP reads. Final verification is
reported with the replacement PR; task timings distinguish offline execution from
Google latency.

Root and nested restarts at 1, continuation across prose and mixed lists, and literal
tabs passed a combined live readback. Each item's native nesting level matches its
Markdown depth, including restarted, orphan and mixed nested lists, checked live. Independent same-style interleaved lists that
require a new non-1 start remain within the explicit numbering shortfall.
