These offline tests exercise human editing tasks through real CLI dispatch and
MCP `tools/call`. Google service boundaries are replaced, sockets are blocked,
and per-document state is isolated. No xfails or loss-consent flags hide missing
core behavior.

The final implementation passes **3,343 offline tests** across the full suite.
The exact historical bindings select **485 passing existing cases**. The
no-stubs check passes. These are offline results, not a universal live fidelity
guarantee; the release PR records the bounded live checks separately.

```sh
uv sync --extra dev
uv run pytest tests/acceptance tests/test_write.py tests/test_push.py tests/test_edit.py -q --acceptance-results /tmp/acceptance.json
uv run python tests/acceptance/run_bindings.py --existing-only
uv run ruff check tests/acceptance tests/test_write.py tests/test_push.py tests/test_edit.py
```

`finding-bindings.json` maps all 38 historical families and 16 deferred items to
exact assertions, baseline results and integrated results. F8 remains an
observation, D03 is a now-corrected parser comment, and D04 is a test-library
style preference; those three do not need runtime fix assertions.

Run reports are generated on demand with `--acceptance-results`; they are not
product fixtures. Timings are single-run offline Python measurements, not Google
latency or agent reasoning time. Mocked helper counts are diagnostics, not actual
HTTP attempts. Live verification is reported separately in the release PR.

| Task | Independent evidence | Remaining limit |
| --- | --- | --- |
| T01 | Text replay and untouched heading/link/list ranges | No live styled result |
| T02 | Split, reread and merge by general native rewrite | Limited text application model |
| T03 | Move section containing prose/list/table, add section, fill styled aligned cells, reread through CLI/MCP | Fixed 2×1 API table scaffold, not general layout simulation |
| T04 | Apply requests, reread heading/link/emphasis/list/code/quote/rule combination unchanged, then change a phrase and reread | Combined table/image/list/code/quote content also passed live readback |
| T05 | Apply actual style masks for emphasis and URL removal/retargeting | Backend styles not observed live |
| T06 | Mixed/empty lists emit native requests; non-1 starts give explicit warning | Root/nested restarts at 1 and mixed continuation passed live; arbitrary new non-1 starts remain best effort |
| T07 | Cell text/styles/alignment and table-containing movement readback | Arbitrary row/column transformations not exhaustively simulated |
| T08 | Code markers, literal backticks, blank code paragraphs, quote/rule identity after changed reconstruction | Combined live readback retained code, quote and rule semantics |
| T09 | CLI/MCP ID-before-title selection; existing case-ambiguity bindings | Selected-tab live write preserved a rich sibling and its image IDs |
| T10 | Successive writes; collaborator/lost-response refusal; edit acknowledgment/rebase/missing-ack counterexamples; push state | Service fault injection only |
| T11 | Same fixture: heading discovery/raw inspection versus direct selector; partial scope; candidate IDs on ambiguity | No agent-driven timing |
| T12 | Prefix/other-tab/metadata/image-filtered reads cannot authorize unseen replacement; force remains revision-pinned | Offline snapshot provenance |
| T13 | Local edit beside image/footnote; rich-footnote rebuild refusal | Native object requests and ranges, not live object inspection |
| T14 | Insert/move/replace/remove image requests; unrelated rewrite refreshes current-snapshot image URI | Live checks separately established URL insertion, movement and repeated reconstruction; replacement/removal remain offline checks |
| T15 | Unicode annotation, ambiguity refusal, zero-call invalid occurrence; existing normalized-anchor/fallback/conflict checks | No new backend anchoring guarantee |

`test_readback.py` reuses the existing UTF-16 text and style-mask appliers. Its
paragraph helper accepts only complete rewrites, rejects nesting tabs and
unknown request types, and checks tab addresses. It applies named code ranges,
paragraph styles and simple bullets. Its table helper handles one fixed 2×1
empty-table response, checks every fill/style address, and applies the cell
text and masks before rereading. It does not simulate Google layout, arbitrary
edits, nested tables, native mixed-list numbering or image rendering.

The invented `harbor-before.md` and `harbor-after.md` remain the larger final
combination target. The executable table movement test covers a smaller
section with prose, a list and a table. Keeping those evidence levels separate
prevents a request-level pass from becoming a claim of complete live support.

The write/push migrations retain file/frontmatter/URL handling, output modes,
errors, no-op behavior, quiet/force behavior and state checks. They replace
Drive-version authority with complete tab reads at native snapshot revisions.
Default writes select the first tab and preserve siblings; explicit collapse
requires every affected tab to be read or an explicit force. Edit tests retain
range/context checks and add acknowledged-revision propagation. Single-line
block-looking text inside a segment is tested as a literal inline replacement;
actual multiline structural segment changes still refuse.

On the same 400-paragraph starting fixture, `toc` followed by raw tab structure
uses two commands and about 134 KB of inspected output. The direct heading
selector returns the same target formatting in one command: **486 CLI bytes or
494 MCP bytes**, including first-interaction notes. Each alternative starts
with isolated empty state; neither retries. Complete raw output remains
available. These byte measurements support the optional selector, not a change
to raw defaults or a network-latency claim.

Prefix, metadata and other-tab reads now refuse the subsequent unseen rewrite
in two-command sequences with zero recovery retries. An explicit forced
replacement is separately successful and pinned to the native snapshot.
Successful own writes no longer require a full reread solely to advance the
read baseline. No speed comparison counts an unsafe write or refusal as task
completion.
