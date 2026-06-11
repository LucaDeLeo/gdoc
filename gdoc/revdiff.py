"""Revision diff engine: REV selectors, block alignment, coalescing word-diff.

One diff model, many renderers (gdoc.diffrender for terminal/HTML;
richer artifacts like docx are built by external scripts from the
`--json` output). The model is the documented `gdoc diff --json`
schema::

    {
      "doc": {"id": "...", "name": "..."},
      "old": {"id": "69",  "modifiedTime": "..."},
      "new": {"id": "190", "modifiedTime": "..."},
      "hunks": [
        {"kind": "equal|insert|delete|replace",
         "block_type": "heading|paragraph|listitem",
         "level": 2,                      # headings only
         "marker": "2.",                  # list items only ("•" for bullets)
         "runs": [{"op": "equal|del|ins", "text": "..."}]}
      ],
      "comments": [                       # only with --with-comments
        {"id": "...", "author": "...", "createdTime": "...",
         "resolved": false, "content": "...", "quoted": "...",
         "hunk": 3,                       # anchored hunk index, or null
         "replies": [{"author": "...", "content": "...",
                      "createdTime": "..."}]}
      ]
    }

The CLI's ``--json`` output wraps this model with top-level ``ok`` and
``identical`` keys. The model is display-oriented, not a faithful
character diff: ``clean_text`` normalizes export escaping/whitespace
and replaces images with placeholders, and coalescing (``min_common``)
absorbs short unchanged spans into the surrounding change.
"""

import difflib
import html
import re
from datetime import datetime

from gdoc.mdimport import IMAGE_DEF_RE, IMAGE_REF_RE
from gdoc.util import GdocError

DEFAULT_MIN_COMMON = 24
DEFAULT_CONTEXT = 2

_HEADING_MARK = r"#{1,6}"
_BULLET_MARK = r"\\?[*\-]"
_ORDERED_MARK = r"\d+[.)\\]+"
_HEADING = re.compile(rf"^({_HEADING_MARK})\s")
_LISTITEM = re.compile(rf"^\s*({_BULLET_MARK}|{_ORDERED_MARK})\s")
_TOKEN = re.compile(r"\s+|\S+")
_HEAD_N = re.compile(r"^(?:head|latest)~(\d+)$")


# ---------------------------------------------------------------- selectors

def parse_rev_range(rev: str) -> tuple[str, str]:
    """Parse a --rev value into (old_selector, new_selector).

    "A..B" compares two revisions; a single selector "A" compares it
    against the latest.
    """
    if ".." in rev:
        old_sel, _, new_sel = rev.partition("..")
        if not old_sel or not new_sel:
            raise GdocError(
                f"invalid revision range: {rev!r} (use A..B)", exit_code=3,
            )
        return old_sel, new_sel
    return rev, "latest"


def resolve_selector(revisions: list[dict], selector: str) -> dict:
    """Resolve a REV selector against the retained-revisions list.

    Grammar: bare id | latest | head | prev | head~N | @ISO.
    head~N counts by list position — revision ids are sparse, so id
    arithmetic is never valid.
    """
    if not revisions:
        raise GdocError(
            "no revisions retained for this document", exit_code=3,
        )
    s = selector.strip()
    lowered = s.lower()
    if lowered in ("latest", "head"):
        return revisions[-1]
    if lowered == "prev":
        return _nth_before_latest(revisions, 1, label="prev")
    match = _HEAD_N.match(lowered)
    if match:
        return _nth_before_latest(revisions, int(match.group(1)))
    if s.startswith("@"):
        return resolve_at_timestamp(revisions, s[1:])
    for rev in revisions:
        if rev.get("id") == s:
            return rev
    raise pruned_error(s)


def pruned_error(revision_id: str) -> GdocError:
    """The shared not-found/pruned error (also raised by the API layer)."""
    return GdocError(
        f"revision not found: {revision_id} (it may have been pruned — "
        "Google drops non-pinned revisions over time). "
        "Run `gdoc revisions DOC` to see retained revisions.",
        exit_code=3,
    )


def _nth_before_latest(
    revisions: list[dict], n: int, label: str | None = None,
) -> dict:
    index = len(revisions) - 1 - n
    if index < 0:
        raise GdocError(
            f"{label or f'head~{n}'} is out of range (only "
            f"{len(revisions)} "
            f"revision{'s' if len(revisions) != 1 else ''} retained). "
            "Run `gdoc revisions DOC`.",
            exit_code=3,
        )
    return revisions[index]


def parse_timestamp(value: str) -> datetime:
    """Parse a user-supplied ISO timestamp.

    Accepts dates ("2026-06-10") and datetimes with or without an
    offset; naive values are interpreted as local time.
    """
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise GdocError(
            f"invalid timestamp: {value!r} (use ISO format, e.g. "
            "2026-06-10T19:00:00Z or 2026-06-10)",
            exit_code=3,
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _revision_time(rev: dict) -> datetime:
    return datetime.fromisoformat(
        rev.get("modifiedTime", "").replace("Z", "+00:00")
    )


def resolve_at_timestamp(revisions: list[dict], value: str) -> dict:
    """Resolve to the last revision at/before a timestamp (--since, @ISO)."""
    if not revisions:
        raise GdocError(
            "no revisions retained for this document", exit_code=3,
        )
    target = parse_timestamp(value)
    prior = [r for r in revisions if _revision_time(r) <= target]
    if not prior:
        earliest = revisions[0].get("modifiedTime", "?")
        raise GdocError(
            f"no revision at/before {value} (earliest retained is "
            f"{earliest}). Run `gdoc revisions DOC`.",
            exit_code=3,
        )
    return prior[-1]


# ------------------------------------------------------------ text cleanup

def clean_text(s: str) -> str:
    """Clean one exported-markdown block for display.

    The markdown export escapes punctuation (``\\>``, ``\\=``, sometimes
    double-escaped): de-escape in a loop, drop a leading blockquote
    marker, replace image refs with a placeholder, and collapse spaces.
    """
    s = html.unescape(s)
    s = IMAGE_REF_RE.sub("⟦diagram⟧", s)
    for _ in range(3):  # loop handles double-escapes
        unescaped = re.sub(r"\\([^\w\s])", r"\1", s)
        if unescaped == s:
            break
        s = unescaped
    s = re.sub(r"^>\s*", "", s)
    s = s.replace("\u00a0", " ")  # nbsp
    return re.sub(r"[ \t]+", " ", s).strip()


def _norm(s: str) -> str:
    """Normalize text for matching (block alignment, comment anchoring)."""
    return re.sub(r"\s+", " ", html.unescape(s)).strip().lower()


def _align_key(block: str) -> str:
    """Key for pairing blocks across revisions.

    Cleans before normalizing so escape-only noise between exports
    doesn't break pairing; the paired blocks' *final* equal-vs-replace
    kind is decided from their cleaned display text in `_make_hunk`.
    """
    return _norm(clean_text(block))


def load_blocks(text: str) -> list[str]:
    """Split exported markdown into non-blank blocks.

    The export emits one long line per paragraph. Drops blank lines,
    ``[imageN]: ...`` definitions, and stray base64 ``data:image``
    blob lines (prose that merely mentions data:image is kept).
    """
    blocks = []
    for raw in text.splitlines():
        if IMAGE_DEF_RE.match(raw) or raw.lstrip().startswith(
            ("data:image", "<data:image"),
        ):
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        blocks.append(stripped)
    return blocks


def classify_block(block: str) -> str:
    """Classify a raw markdown block: heading, listitem, or paragraph."""
    if _HEADING.match(block):
        return "heading"
    if _LISTITEM.match(block):
        return "listitem"
    return "paragraph"


def heading_level(block: str) -> int:
    match = _HEADING.match(block)
    return len(match.group(1)) if match else 2


def strip_marker(block: str) -> str:
    """Strip the leading markdown marker (#, bullet, or number)."""
    block = re.sub(rf"^{_HEADING_MARK}\s+", "", block)
    block = re.sub(rf"^\s*{_BULLET_MARK}\s+", "", block)
    block = re.sub(rf"^\s*{_ORDERED_MARK}\s+", "", block)
    return block


def _list_marker(block: str) -> str:
    """Display marker for a list item: the real ordinal, or •."""
    match = re.match(rf"^\s*({_BULLET_MARK}|{_ORDERED_MARK})\s", block)
    if match and match.group(1)[0].isdigit():
        return clean_text(match.group(1))
    return "•"


def _block_structure(block: str) -> tuple:
    """Structure key for same-text equality: type, level, list shape.

    Distinguishes heading levels and bullet vs ordered lists, but not
    the ordinal of an ordered item, so renumbering reads as equal.
    (List nesting depth can't be tracked here: load_blocks strips
    leading whitespace.)
    """
    block_type = classify_block(block)
    if block_type == "heading":
        return ("heading", heading_level(block))
    if block_type == "listitem":
        match = re.match(
            rf"^\s*({_BULLET_MARK}|{_ORDERED_MARK})\s", block,
        )
        kind = (
            "ordered" if match and match.group(1)[0].isdigit()
            else "bullet"
        )
        return ("listitem", kind)
    return ("paragraph",)


# -------------------------------------------------------------- word diff

def word_diff_runs(
    old: str, new: str, min_common: int = DEFAULT_MIN_COMMON,
) -> list[dict]:
    """Word-level diff with island coalescing.

    Plain word-level difflib clings to scraps ("the", "—") inside
    rewritten sentences and produces unreadable salad. Shared runs
    shorter than `min_common` stripped chars, flanked by changes on
    both sides, are absorbed into the change; consecutive changed
    segments then merge into one contiguous del + ins pair.
    """
    a = _TOKEN.findall(old)
    b = _TOKEN.findall(new)
    segments = [
        [op, "".join(a[i1:i2]), "".join(b[j1:j2])]
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
            a=a, b=b, autojunk=False,
        ).get_opcodes()
    ]
    # Interior equal runs are flanked by changes on both sides by
    # construction: get_opcodes never emits adjacent equal ops.
    for k in range(1, len(segments) - 1):
        if (
            segments[k][0] == "equal"
            and len(segments[k][1].strip()) < min_common
        ):
            segments[k][0] = "replace"

    runs: list[dict] = []
    i = 0
    while i < len(segments):
        if segments[i][0] == "equal":
            runs.append({"op": "equal", "text": segments[i][1]})
            i += 1
            continue
        deleted, inserted = [], []
        while i < len(segments) and segments[i][0] != "equal":
            op, old_text, new_text = segments[i]
            if op in ("delete", "replace"):
                deleted.append(old_text)
            if op in ("insert", "replace"):
                inserted.append(new_text)
            i += 1
        deleted_text = "".join(deleted)
        inserted_text = "".join(inserted)
        if deleted_text.strip():
            runs.append({"op": "del", "text": deleted_text})
        if inserted_text.strip():
            runs.append({"op": "ins", "text": inserted_text})
    return runs


# ------------------------------------------------------------- diff model

def _make_hunk(
    kind: str,
    old_block: str | None,
    new_block: str | None,
    min_common: int,
) -> dict:
    src = new_block if new_block is not None else old_block
    block_type = classify_block(src)
    old_text = (
        clean_text(strip_marker(old_block)) if old_block is not None else ""
    )
    new_text = (
        clean_text(strip_marker(new_block)) if new_block is not None else ""
    )
    hunk: dict = {"kind": kind, "block_type": block_type}
    if block_type == "heading":
        hunk["level"] = heading_level(src)
    elif block_type == "listitem":
        hunk["marker"] = _list_marker(src)
    if kind == "equal":
        hunk["runs"] = [{"op": "equal", "text": new_text}]
    elif kind == "insert":
        hunk["runs"] = [{"op": "ins", "text": new_text}]
    elif kind == "delete":
        hunk["runs"] = [{"op": "del", "text": old_text}]
    else:
        hunk["runs"] = word_diff_runs(old_text, new_text, min_common)
    return hunk


def _pair_hunks(old_block: str, new_block: str, min_common: int) -> list[dict]:
    """Hunks for an aligned old/new block pair.

    Alignment pairs blocks loosely (case-insensitive), so a pair can
    arrive looking equal or changed either way; the final kind depends
    on what a reader actually sees — the cleaned text plus the block
    structure. A marker-only change (heading level, paragraph→bullet)
    becomes delete+insert: a replace hunk would carry only equal runs
    and render with no visible difference, and this way the old marker
    is shown too. Ordered-list renumbering still reads as equal.
    """
    old_text = clean_text(strip_marker(old_block))
    new_text = clean_text(strip_marker(new_block))
    if old_text != new_text:
        return [_make_hunk("replace", old_block, new_block, min_common)]
    if _block_structure(old_block) == _block_structure(new_block):
        return [_make_hunk("equal", old_block, new_block, min_common)]
    return [
        _make_hunk("delete", old_block, None, min_common),
        _make_hunk("insert", None, new_block, min_common),
    ]


def build_hunks(
    old_md: str, new_md: str, min_common: int = DEFAULT_MIN_COMMON,
) -> list[dict]:
    """Align blocks of two markdown exports and emit the hunk list."""
    old_blocks = load_blocks(old_md)
    new_blocks = load_blocks(new_md)
    matcher = difflib.SequenceMatcher(
        a=[_align_key(b) for b in old_blocks],
        b=[_align_key(b) for b in new_blocks],
        autojunk=False,
    )
    hunks: list[dict] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                hunks.extend(_pair_hunks(
                    old_blocks[i1 + k], new_blocks[j1 + k], min_common,
                ))
        elif op == "delete":
            for k in range(i1, i2):
                hunks.append(_make_hunk(
                    "delete", old_blocks[k], None, min_common,
                ))
        elif op == "insert":
            for k in range(j1, j2):
                hunks.append(_make_hunk(
                    "insert", None, new_blocks[k], min_common,
                ))
        else:
            # replace: pair blocks positionally; extras become del/ins
            old_chunk = old_blocks[i1:i2]
            new_chunk = new_blocks[j1:j2]
            for k in range(max(len(old_chunk), len(new_chunk))):
                o = old_chunk[k] if k < len(old_chunk) else None
                n = new_chunk[k] if k < len(new_chunk) else None
                if o is not None and n is not None:
                    hunks.extend(_pair_hunks(o, n, min_common))
                elif o is not None:
                    hunks.append(_make_hunk("delete", o, None, min_common))
                else:
                    hunks.append(_make_hunk("insert", None, n, min_common))
    return hunks


def hunk_changed(hunk: dict) -> bool:
    return hunk["kind"] != "equal"


def hunk_side_text(hunk: dict, side: str) -> str:
    """Concatenate a hunk's old or new side text from its runs."""
    ops = ("equal", "del") if side == "old" else ("equal", "ins")
    return "".join(r["text"] for r in hunk["runs"] if r["op"] in ops)


def build_diff_model(
    doc_id: str,
    doc_name: str,
    old_rev: dict,
    new_rev: dict,
    old_md: str,
    new_md: str,
    min_common: int = DEFAULT_MIN_COMMON,
) -> dict:
    """Assemble the full diff model (sans comments)."""
    return {
        "doc": {"id": doc_id, "name": doc_name},
        "old": {
            "id": old_rev.get("id"),
            "modifiedTime": old_rev.get("modifiedTime", ""),
        },
        "new": {
            "id": new_rev.get("id"),
            "modifiedTime": new_rev.get("modifiedTime", ""),
        },
        "hunks": build_hunks(old_md, new_md, min_common),
    }


# --------------------------------------------------------------- comments

_ANCHOR_STOPWORDS = {"this"}
_ANCHOR_KEY_LEN = 45
_ANCHOR_MIN_LEN = 4


def attach_comments(hunks: list[dict], comments: list[dict]) -> list[dict]:
    """Anchor comment threads to hunks by quoted-snippet matching.

    Best-effort: the API gives only the quoted snippet, not a position.
    Prefers a *changed* hunk containing the snippet over the first
    match (so a short anchor lands on the section under discussion,
    not the first stray occurrence). Unmatched threads get hunk=None
    and render as an appendix — they are never dropped.
    """
    # Sides matched separately: concatenating old + new would let a
    # key false-match across the artificial junction of a replace hunk.
    match_texts = [
        (_norm(hunk_side_text(h, "old")), _norm(hunk_side_text(h, "new")))
        for h in hunks
    ]
    model_comments = []
    for c in sorted(comments, key=lambda c: c.get("createdTime", "")):
        quoted = (c.get("quotedFileContent") or {}).get("value", "")
        anchor = _norm(quoted)
        key = anchor[:_ANCHOR_KEY_LEN]
        target = None
        if (
            key
            and len(anchor) >= _ANCHOR_MIN_LEN
            and anchor not in _ANCHOR_STOPWORDS
        ):
            # Preference ladder: a changed hunk whose *new* side holds
            # the anchor (the current content under discussion), then a
            # changed hunk matching only its old side (quoted text that
            # was deleted), then the first match anywhere. Matters for
            # split delete+insert pairs, which share the same text.
            old_changed = None
            first_match = None
            for idx, (old_side, new_side) in enumerate(match_texts):
                in_new = key in new_side
                if not in_new and key not in old_side:
                    continue
                if first_match is None:
                    first_match = idx
                if hunk_changed(hunks[idx]):
                    if in_new:
                        target = idx
                        break
                    if old_changed is None:
                        old_changed = idx
            if target is None:
                target = (
                    old_changed if old_changed is not None else first_match
                )
        replies = [
            {
                "author": (r.get("author") or {}).get("displayName", "?"),
                "content": r.get("content", ""),
                "createdTime": r.get("createdTime")
                or r.get("modifiedTime", ""),
            }
            for r in c.get("replies", [])
            if r.get("content")  # skip action-only replies
        ]
        model_comments.append({
            "id": c.get("id", ""),
            "author": (c.get("author") or {}).get("displayName", "?"),
            "createdTime": c.get("createdTime", ""),
            "resolved": c.get("resolved", False),
            "content": clean_text(c.get("content", "")),
            "quoted": clean_text(quoted),
            "replies": replies,
            "hunk": target,
        })
    return model_comments
