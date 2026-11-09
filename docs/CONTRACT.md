# Product contract for the closure release

## What gdoc is

gdoc lets people ask their agents to make any change expressible in its supported Markdown format to a Google Doc, and get the intended result reliably. People should not need to understand Google Docs internals or gdoc implementation details to request those changes. Agents should perform ordinary edits as straightforwardly as edits to Markdown files. Ordinary tasks should finish quickly.

The priorities are, in order:

1. Humans can obtain any requested change within the supported Markdown feature set. Implementation correctness and feature completeness are the binding requirements.
2. Agents can perform ordinary tasks with familiar commands and little troubleshooting.
3. Tasks complete quickly in wall-clock time, including all commands, reads, reasoning over output and recovery.

An agent may choose several commands or understand technical details to fulfill the primary promise. Humans should not have to formulate their requests around accidental parser or API limitations.

## Proposed Markdown feature boundary

This is a semantic contract, not a claim of complete CommonMark/GFM parser conformance. Agents may use documented canonical spellings. Different Markdown strings may express the same content; exact source bytes need not survive serialization. Literal characters, meaningful whitespace and requested structure must survive.

| Feature | Required result |
| --- | --- |
| Paragraphs and text | Insert, delete, split, merge and reorder paragraphs/sections; preserve literal Unicode text, meaningful blank paragraphs and explicit line breaks. |
| Headings | Create/change/remove levels 1–6 and move their sections; adjacent paragraphs keep their intended styles. |
| Inline formatting | Apply/remove/combine bold, italic, strikethrough and inline code; replace link labels and external destinations, including balanced parentheses. |
| Lists | Create/reorder/nest/unnest bullet and numbered items, including mixed nesting, empty items, starting numbers and restarts; preserve meaningful text and displayed order. Native numbering semantics must not silently become plain numbered text. |
| Code blocks, quotes and rules | Create/edit/remove fenced code, quoted blocks and thematic breaks; preserve literal code and whitespace. Syntax highlighting and a particular native representation are not required. |
| Rectangular tables | Create/edit/reorder/remove rows and columns and their text/inline formatting; preserve header semantics and Markdown column alignment. Merged/nested tables are richer features. |
| Markdown images | Include ordinary image insertion, movement, replacement, removal and preservation in the intended scope. Verify source retrieval, round-trip references and available native controls. Existing images must not disappear during unrelated rewrites. Crop/layout beyond Markdown is richer formatting. |

The list/table/code/quote combinations are part of the promise, not unrelated features whose individual tests suffice. Phase 0 must check their feasibility and settle the exact representation and ownership before parallel implementation. Non-default numbering, column alignment and image handling have not been certified by the audit. The user explicitly permits best-effort behavior and documented gaps where Google APIs genuinely prevent full control. First check current official documentation, existing code and credible alternative routes; use a minimal probe only if that would resolve a material uncertainty. Record the exact limitation, alternatives investigated, best reliable behavior and user-visible consequence. An unimplemented feature is not an API limitation, and a refused task is not a completed task. Escalate consequential destructive tradeoffs or substantially larger work; do not ask again merely to document a verified API gap.

A native document's default fonts, page dimensions or incidental metadata do not exclude it from core use. The core promises requested Markdown meaning, not identical pagination, object IDs or every direct-style override. Read scope is explicit: a selected tab is an editable Markdown unit; default reads must make their tab coverage apparent. A general document rewrite must not silently flatten or discard sibling tabs.

## Correctness and richer documents

A targeted change preserves supported content and formatting outside its intended scope. This includes neighboring headings, lists, links and emphasis; it is not optional fidelity work. Style assignment inside wholly rewritten text may be specified by the agent with Markdown; gdoc needs deterministic documented behavior, not inference of human intent from arbitrary native runs.

Richer properties—custom spacing, colors, fonts, chips, footnotes, review objects and complex tables—are an extension track. Targeted edits should preserve unrelated native features where practical. A richer feature elsewhere in the document must not automatically block an unrelated text edit. Do not implement a general formatting framework during this run.

Warnings/consent address known losses of richer features. They are not a remedy for corruption of supported Markdown. A pure supported-Markdown task must have a successful supported route without `--allow-lossy`. A Markdown table header is semantic; an existing unbolded table first row must not gain bold during an unrelated targeted edit. Exact extra visual styling on a rich table may fall outside the core only when that boundary is explicit.

Hard stops remain appropriate for ambiguous targets, unresolvable revision conflicts and uncertain mutations. A stop is an operational outcome, not evidence that an editing capability exists. Do not silently turn suggestions into direct edits or replay an uncertain mutation. Revision checks guard the write; authorization to discard formatting does not authorize overwriting another person's changes unseen.

## CLI and MCP parity

The same contract applies to CLI and MCP. Equivalent human tasks must succeed through both interfaces, with the same content preservation, tab scope, read coverage, revision safety and truthful partial/uncertain outcomes. Interface-specific output may differ; CLI-only success does not establish MCP support.

## The general route and convenience routes

A reliable read → modify Markdown → write route establishes expressive completeness. Targeted edit and insertion commands improve convenience and preservation. A native targeted command need not implement every transformation if the general route succeeds safely and predictably for the supported scope. Ordinary paragraph split/merge is required as a task; a particular `edit` command syntax is not mandated.

The general route must preserve supported semantics on unchanged AND changed round trips. No-op skipping alone does not prove the writer can reconstruct a document. Run changed cases containing multiple supported features, and reread the result through the same interface the agent uses. Do not silently rebuild a rich document as the fallback for a targeted edit.

## Output and performance

Useful output minimizes time to complete a task. Keep complete export available for file workflows; identify partial reads and provide a direct way to retrieve necessary detail. Do not add an overview-only call before every edit. Existing raw structure output remains available; do not change its default contract merely to meet an arbitrary byte budget. Choose summaries, selectors or limits only after testing representative agent tasks.

Measure successful task completion first, then elapsed time and avoidable command/recovery steps. API counts and byte sizes diagnose cost, but are not hard ceilings that override correctness or simplicity. Prefer local/offline measurement initially, with a small final live sample; never present mocked timings as Google latency.
