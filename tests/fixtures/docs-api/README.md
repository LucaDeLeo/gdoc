# Anonymous Docs API shape fixtures

These three frozen fixtures protect edit ranges around native elements, object
identity/position, footnote body scoping, and selection of a blank sibling tab.
Run them offline with `uv run pytest tests/test_api_captures.py`.

Replacement matching treats native inline elements as barriers. Non-destructive
comment anchors and image `--after` anchors explicitly allow gaps left by native
elements in plain-text extraction, preserving their existing behavior. The tests
cover both exact matching and typography-folding fallback for these callers.

## Provenance and limits

These are **synthetic equivalents**, constructed on 2026-09-09 from the public
[Docs v1 resource schema](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents),
[tab response documentation](https://developers.google.com/workspace/docs/api/how-tos/tabs),
and the upstream synthetic object/tab tests (`tests/test_images.py` and
`tests/test_tabs_api.py`, base commit `dbfa4c34bfa699ee8dd9839da85eea1fac177d44`).
They preserve the nesting and reference relationships of API responses, with
invented values. They were not copied from private captures or fetched from a
live document. We cannot claim direct real-capture provenance for these files.
Future fixtures derived from real API captures must say so in their metadata,
while replacing all source values with anonymous equivalents.

- `inline-positioned-v1.json`: text/image/text with UTF-16 offsets and a
  paragraph-anchored drawing. Tests object references, classification and size.
- `footnote-v1.json`: text/reference/text and a separately indexed footnote body.
  Tests the range boundary and explicit body scoping, not footnote rendering.
- `sibling-tabs-v1.json`: rich first tab and blank selected second tab. Tests
  selection, empty deletion range and object ownership.

This is a deliberately small sample, not exhaustive API coverage or a promise
that every write operation preserves these structures. It does not depend on
any separate lossy-write-guard work. There are no document IDs, account data,
comments, names, URLs, revision IDs or temporary image URIs in the payloads.
Object, footnote and tab identifiers are invented local join keys. Empty image
properties retain classification without testing downloading.

The envelope records local `fixture_schema` and `fixture_version`, API version,
method, tab request mode, local `api_shape` label and construction provenance.
The API shape labels are ours, not Google release numbers. The v1 loader checks
a closed vocabulary and field types, then passes the document through unchanged.
Tests assert semantic ranges and object properties, never raw JSON equality.
An in-memory equality check only verifies that read helpers do not mutate input.
The loader is a test helper, not a general Google schema validator or live schema
monitor; an offline suite cannot discover changes Google has not yet captured.

## Supersession

1. Obtain a response from a purpose-built anonymous document or public evidence.
   Record whether it is a real capture or a synthetic equivalent, the capture or
   construction date, API/method/request mode and a public source when available.
   Never copy private source files into this repository. Remove document/account
   identifiers, comments, URLs and transient fields; replace text and local join
   keys, recomputing UTF-16 indices. Keep the native structure being tested.
2. Add a new file such as `inline-positioned-v2.json`; do not rewrite v1 to make a
   failing test green. Give it a new `fixture_version` and, for a changed response
   shape, a new `api_shape`. Change `fixture_schema` only if the envelope changes.
3. Add explicit loader support for that version/shape and semantic assertions for
   the behavior it exercises. Unknown keys, types or versions must fail with the
   offending fixture/path and a link to this procedure. Do not skip, auto-update,
   strip unknown structure or loosen the old validator to accept a new shape.
4. Retain the old fixture and tests while the code still supports that API shape.
   Remove it only when support is removed, explaining that decision in the PR.
   A newer Google response alone is not a reason to discard an older regression.
5. Run the focused tests, full suite, `uv run ruff check gdoc/ tests/`, and
   `bash scripts/check-no-stubs.sh`. Review every added payload for anonymity.
