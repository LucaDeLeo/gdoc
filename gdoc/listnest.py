"""Plan in-place nesting changes for Google Docs list items.

The Docs API has no request that sets a list item's nesting level. The
level comes from ``createParagraphBullets``, which, measured live, assigns

    level = base + (leading tabs - fewest leading tabs in the range)
            + any indent left behind by ``deleteParagraphBullets``

where *base* is the level of the paragraph just before the range when the
new bullets join that paragraph's list (they join only the immediately
preceding paragraph's list, and only when it is the same kind, numbered or
bullet). Paragraphs that already have bullets are skipped.

So nesting is a rebuild of a small window of items that ends up back in
the same list (same list ID, so a restarted start number and comment
anchors survive), in one revision-pinned batch:

1. insert a temporary empty paragraph at the window start, which pins the
   fewest-tabs term to 0;
2. remove the window's bullets and zero its indent;
3. insert (target level - base) tabs at the start of each item;
4. bullet the window again, which joins the list of the item before it and
   consumes the tabs;
5. remove the bullets from blank paragraphs in the window (loose lists)
   and restore their original indent;
6. delete the temporary paragraph.

The window starts at the first moved item and extends back over deeper
items (an unnest below a deeper sibling needs them rebuilt at their own
level) and over blank paragraphs, and forward over the moved items'
descendants. Every list item in the window, and the item before it, must
belong to the target's list: anything else would be merged into that list
or split off from it, so it is refused.
"""

from dataclasses import dataclass, field

from gdoc.util import GdocError

MAX_LEVEL = 8  # Docs lists have nesting levels 0-8

# The two presets gdoc and the Docs toolbar create, identified by the
# glyph of each level (they repeat every three levels). Joining and
# rebuilding were verified live only for these; other lists are refused.
_NUMBERED = "NUMBERED_DECIMAL_ALPHA_ROMAN"
_BULLET = "BULLET_DISC_CIRCLE_SQUARE"
_NUMBERED_GLYPHS = ("DECIMAL", "ALPHA", "ROMAN")
_BULLET_GLYPHS = ("●", "○", "■")  # ● ○ ■


def _usage(message: str) -> GdocError:
    return GdocError(message, exit_code=3)


def list_preset(lists: dict, list_id: str) -> str | None:
    """The bullet preset a list was made from, or None if it is another kind.

    Checkbox lists, custom glyphs and other presets return None.
    """
    levels = (
        lists.get(list_id, {}).get("listProperties", {}).get("nestingLevels", [])
    )
    if not levels:
        return None
    # glyphFormat tells "1." from "1)" (NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS)
    # and catches prefix/suffix edits made in List options.
    if all(
        lvl.get("glyphType") == _NUMBERED_GLYPHS[i % 3]
        and lvl.get("glyphFormat") == f"%{i}."
        for i, lvl in enumerate(levels)
    ):
        return _NUMBERED
    if all(
        lvl.get("glyphSymbol") == _BULLET_GLYPHS[i % 3]
        and lvl.get("glyphType", "GLYPH_TYPE_UNSPECIFIED")
        in ("GLYPH_TYPE_UNSPECIFIED", "NONE")
        and lvl.get("glyphFormat") == f"%{i}"
        for i, lvl in enumerate(levels)
    ):
        return _BULLET
    return None


def _paragraph(el: dict) -> dict | None:
    return el.get("paragraph")


def _text(el: dict) -> str:
    p = _paragraph(el) or {}
    return "".join(
        pe.get("textRun", {}).get("content", "") for pe in p.get("elements", [])
    )


def _bullet(el: dict) -> dict | None:
    return (_paragraph(el) or {}).get("bullet")


def _level(el: dict) -> int:
    return (_bullet(el) or {}).get("nestingLevel", 0)


def _is_blank(el: dict) -> bool:
    """An empty paragraph without a bullet: the gap in a loose list."""
    p = _paragraph(el)
    if p is None or p.get("bullet"):
        return False
    elements = p.get("elements", [])
    return all("textRun" in pe for pe in elements) and _text(el) == "\n"


def _in_list(el: dict, list_id: str) -> bool:
    return (_bullet(el) or {}).get("listId") == list_id


def _custom_indent(el: dict, lists: dict) -> bool:
    """Whether a list item's own indent differs from its level's standard.

    The rebuild gives every item its level's standard indent (as Tab does
    in Docs), so an item indented by hand would silently lose that.
    """
    style = _paragraph(el).get("paragraphStyle", {})
    bullet = _bullet(el)
    levels = (
        lists.get(bullet["listId"], {})
        .get("listProperties", {}).get("nestingLevels", [])
    )
    level = levels[_level(el)] if _level(el) < len(levels) else {}
    for name in ("indentStart", "indentFirstLine"):
        if name in style:
            own = style[name].get("magnitude", 0)
            standard = level.get(name, {}).get("magnitude", 0)
            if abs(own - standard) > 0.01:
                return True
    return False


def _set_fields(style: dict) -> dict:
    """A text style without its false booleans (Docs reports
    {"underline": false} on every plain bullet)."""
    return {k: v for k, v in (style or {}).items() if v is not False}


def _marker_style(el: dict) -> dict:
    return _set_fields((_bullet(el) or {}).get("textStyle"))


# Marker fields a rebuild is known to keep when the item's text carries
# them too: live, a fully bold item's number is bold again afterwards. A
# font is not (an item ending in Georgia keeps a plain marker), and
# nothing else has been tested, so any other marker field is refused.
_MARKER_FIELDS_KEPT = frozenset({"bold"})


def _marker_differences(el: dict) -> list[str]:
    """Formatting on the item's bullet or number that a rebuild could drop.

    Refused: a marker field outside ``_MARKER_FIELDS_KEPT``, a kept field
    not carried by every text run, any explicit false (a hand-set
    override), and a style every run carries that the marker lacks.
    ``underline: false`` is on every plain marker and is ignored.
    """
    marker = (_bullet(el) or {}).get("textStyle") or {}
    runs = [
        pe["textRun"].get("textStyle", {})
        for pe in (_paragraph(el) or {}).get("elements", [])
        if pe.get("textRun", {}).get("content")
    ]
    odd = []
    for key, m in marker.items():
        if m is False:
            # Only underline:false is on every plain marker. Any other
            # explicit false is a hand-set override whose survival depends
            # on the text's effective (possibly inherited) style: refuse.
            if key != "underline":
                odd.append(key)
        elif key not in _MARKER_FIELDS_KEPT or not runs or any(
            r.get(key) != m for r in runs
        ):
            # Kept only when the whole item carries it (live: Docs gives a
            # marker bold only for a fully bold item).
            odd.append(key)
    # The mirror case: a style set on every run (the paragraph mark
    # included) that the marker lacks. Docs bolds the marker of a fully
    # bold item on its own, so a hand-unbolded one would likely come back
    # bold; fonts, sizes, colours and the rest are untested and refused
    # alike. An item that only ends in a style is not affected (live: an
    # item ending in Georgia keeps a plain marker).
    shared = set(runs[0]) if runs else set()
    for r in runs[1:]:
        shared &= set(r)
    for key in shared:
        values = [r[key] for r in runs]
        if values[0] is False or any(v != values[0] for v in values):
            continue
        if marker.get(key) != values[0]:
            odd.append(key)
    return sorted(set(odd))


def _label(el: dict) -> str:
    text = _text(el).strip()
    return repr(text[:40] + ("..." if len(text) > 40 else ""))


def _inner_paragraph_texts(node) -> list[str]:
    """Texts of every paragraph nested anywhere inside *node* (a table)."""
    texts = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            if "paragraph" in cur:
                texts.append(_text(cur))
            else:
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return texts


def locate_item(body: dict, text: str) -> int:
    """Index into ``body["content"]`` of the one paragraph containing *text*.

    Case-insensitive, like ``edit``, but matched per paragraph without
    character offsets: lowercasing can change a string's length (Turkish
    dotted capital I), which would shift offset-based matches onto the
    wrong paragraph. Raises a usage error (exit 3) when the text is
    missing, is found in more than one paragraph, spans paragraphs, or
    sits in a table.
    """
    if "\n" in text.strip("\n"):
        raise _usage(f"{text!r} spans more than one paragraph")
    needle = text.strip("\n").lower()
    if not needle:
        raise _usage("the item text is empty")
    found: list[int] = []
    in_container = 0
    for i, el in enumerate(body.get("content", [])):
        if _paragraph(el) is not None:
            if needle in _text(el).lower():
                found.append(i)
        elif "tableOfContents" not in el:
            # A table of contents repeats heading text; it is not a match.
            in_container += sum(
                needle in t.lower() for t in _inner_paragraph_texts(el)
            )
    if len(found) + in_container > 1:
        raise _usage(
            f"{text!r} matches {len(found) + in_container} paragraphs; use "
            "text unique to one list item"
        )
    if in_container:
        raise _usage(
            f"{text!r} is inside a table or other container; only list "
            "items in the tab body can be nested"
        )
    if not found:
        from gdoc.api.docs import diagnose_no_match

        # already_normalized: skip the "--normalize" hint, a flag nest
        # does not have.
        reason = diagnose_no_match(None, text, body=body, already_normalized=True)
        raise _usage(
            f"no match found for {text!r}" + (f"; {reason}" if reason else "")
        )
    return found[0]


@dataclass
class NestPlan:
    """Requests for one nesting change and the list state they should leave.

    ``expected`` maps each list item in the rebuilt window (by index into
    the tab's body content, which the change does not shift) to its
    level afterwards; ``blanks`` are window paragraphs that must end
    without a bullet; ``markers`` holds each rebuilt item's marker style,
    which must survive.
    """

    requests: list[dict]
    list_id: str
    moved: int
    expected: dict[int, int]
    blanks: list[int]
    markers: dict[int, dict] = field(default_factory=dict)
    texts: dict[int, str] = field(default_factory=dict)
    original: dict[int, int] = field(default_factory=dict)
    styles: dict[int, dict] = field(default_factory=dict)


def plan_nesting(
    document_tab: dict, tab_id: str, first: int, last: int, delta: int,
) -> NestPlan:
    """Plan moving list items ``first..last`` (and descendants) by *delta* levels.

    *first* and *last* index ``document_tab["body"]["content"]``. A
    positive *delta* nests, a negative one unnests. Raises a usage error
    (exit 3) for every case the rebuild cannot do without changing
    anything but the chosen items' levels.
    """
    if delta == 0:
        raise _usage("levels must be at least 1")
    content = document_tab.get("body", {}).get("content", [])
    lists = document_tab.get("lists", {})
    head = content[first]
    if not _bullet(head):
        raise _usage(f"{_label(head)} is not a list item")
    list_id = _bullet(head)["listId"]
    if last < first:
        raise _usage("the --to item comes before the first item")
    if not _in_list(content[last], list_id):
        raise _usage(
            f"{_label(content[last])} is not in the same list as {_label(head)}"
        )

    preset = list_preset(lists, list_id)
    if preset is None:
        raise _usage(
            "only default numbered (1. a. i.) and bullet (● ○ ■) lists can be "
            "nested; this list uses other glyphs (checkbox or custom)"
        )

    # The chosen range: items of this list, with blank paragraphs allowed
    # between them.
    for el in content[first:last + 1]:
        if _in_list(el, list_id) or _is_blank(el):
            continue
        if _bullet(el):
            raise _usage(
                f"the range includes {_label(el)} from a different list; "
                "nest each list separately"
            )
        raise _usage(f"the range includes {_label(el)}, which is not a list item")
    shallowest = min(
        _level(el) for el in content[first:last + 1] if _in_list(el, list_id)
    )

    # Descendants move with their ancestors.
    end = last
    j = last + 1
    while j < len(content):
        k = j
        while k < len(content) and _is_blank(content[k]):
            k += 1
        nxt = content[k] if k < len(content) else {}
        if not _bullet(nxt) or _level(nxt) <= shallowest:
            break
        if not _in_list(content[k], list_id):
            raise _usage(
                f"{_label(content[end])} has sub-items in a separate list "
                f"({_label(content[k])}); moving them together would merge "
                "that list into this one"
            )
        end = k
        j = k + 1
    moved = [i for i in range(first, end + 1) if _in_list(content[i], list_id)]
    target = {i: _level(content[i]) + delta for i in moved}

    too_shallow = [i for i in moved if target[i] < 0]
    if too_shallow:
        raise _usage(
            f"{_label(content[too_shallow[0]])} is at level "
            f"{_level(content[too_shallow[0]])}; it cannot be unnested by {-delta}"
        )
    if max(target.values()) > MAX_LEVEL:
        raise _usage(f"Google Docs lists have at most {MAX_LEVEL + 1} levels")

    def neighbour(i: int, step: int) -> dict | None:
        i += step
        while 0 <= i < len(content) and _is_blank(content[i]):
            i += step
        return content[i] if 0 <= i < len(content) else None

    above = neighbour(first, -1)
    if delta > 0:
        if above is None or not _bullet(above):
            raise _usage(
                f"{_label(head)} has no list item above it to nest it under"
            )
        if not _in_list(above, list_id):
            raise _usage(
                f"{_label(head)} follows {_label(above)}, an item of a "
                "different list; it can only be nested under an item of its "
                "own list"
            )
        if target[first] > _level(above) + 1:
            raise _usage(
                f"nesting {_label(head)} by {delta} "
                f"level{'s' if delta > 1 else ''} would put it more "
                f"than one level below {_label(above)}"
            )
    below = neighbour(end, 1)
    if (
        # Any list item, including one of another list (a mixed sub-list),
        # would be left dangling below the moved item.
        below is not None and _bullet(below)
        and _level(below) > target[end] + 1
    ):
        raise _usage(
            f"unnesting by {-delta} level{'s' if delta < -1 else ''} would "
            f"leave {_label(below)} more "
            f"than one level below {_label(content[end])}"
        )

    # The window: extend back over deeper items (rebuilt at their own
    # level) and over the blanks of a loose list, until the item before it
    # is no deeper than every level in the window. That item's list is the
    # one the rebuilt bullets join; its level is the base.
    start = first
    levels = dict(target)
    while True:
        k = start - 1
        while k >= 0 and _is_blank(content[k]):
            k -= 1
        prev = content[k] if k >= 0 else None
        if prev is None or not _bullet(prev):
            raise _usage(
                f"{_label(content[start])} does not follow another item of its "
                "list, so its list could not be kept; items after a "
                "non-list paragraph cannot be moved"
            )
        if not _in_list(prev, list_id):
            raise _usage(
                f"{_label(content[start])} follows {_label(prev)}, an item of a "
                "different list; moving it would split or merge those lists"
            )
        if _level(prev) > min(levels.values()):
            start = k
            levels[k] = _level(prev)
            continue
        start = k + 1
        base = _level(prev)
        break

    window = range(start, end + 1)
    for i in window:
        el = content[i]
        if _is_blank(el):
            continue
        if not _in_list(el, list_id):
            raise _usage(
                f"{_label(el)} belongs to a different list and sits among "
                "the items that would be rebuilt"
            )
        if _text(el).startswith("\t"):
            raise _usage(f"{_label(el)} starts with a tab character")
        odd = _marker_differences(el)
        if odd:
            # A rebuilt marker takes the style of the item's own text (a
            # fully bold item gets a bold number, live-tested); formatting
            # set on the marker alone would be lost.
            raise _usage(
                f"{_label(el)} has a formatted bullet or number ("
                + ", ".join(odd)
                + "); rebuilding it would reset that formatting"
            )
        if _custom_indent(el, lists):
            raise _usage(
                f"{_label(el)} has a hand-set indent; rebuilding it would "
                "reset the indent to the list's standard one"
            )
    from gdoc.api.docs import _walk_suggestion_ids

    suggestions: set[str] = set()
    _walk_suggestion_ids([content[i] for i in window], suggestions)
    if suggestions:
        raise _usage(
            "the items to move contain pending suggestions ("
            + ", ".join(sorted(suggestions))
            + "); accept or reject them first"
        )
    # The rebuilt bullets join the list of the item just above; if that
    # item is itself a pending suggestion, rejecting it later would leave
    # the rebuilt items anchored to a list that is no longer there.
    anchor = start - 1
    while anchor >= 0 and _is_blank(content[anchor]):
        anchor -= 1
    _walk_suggestion_ids(content[anchor], suggestions)
    if suggestions:
        raise _usage(
            f"{_label(content[anchor])}, the item the moved items join, has "
            "pending suggestions ("
            + ", ".join(sorted(suggestions))
            + "); accept or reject them first"
        )

    blanks = [i for i in window if _is_blank(content[i])]
    s0 = content[start]["startIndex"]
    e0 = content[end]["endIndex"]

    # The rebuild inserts and later deletes a paragraph break at the window
    # start; the Docs API warns that deleting across a paragraph boundary
    # can move positioned objects and change named ranges. Untested, so
    # refused.
    for i in window:
        if (_paragraph(content[i]) or {}).get("positionedObjectIds"):
            raise _usage(
                f"{_label(content[i])} has a floating image or drawing "
                "anchored to it, which the rebuild could move"
            )
    for name, named in (document_tab.get("namedRanges") or {}).items():
        for nr in named.get("namedRanges", []):
            for r in nr.get("ranges", []):
                if r.get("startIndex", 0) <= e0 and r.get("endIndex", 0) >= s0:
                    raise _usage(
                        f"the items to rebuild overlap the named range {name!r}, "
                        "which the rebuild could change"
                    )

    def rng(s: int, e: int) -> dict:
        return {"startIndex": s, "endIndex": e, "tabId": tab_id}

    def zero_indent(s: int, e: int) -> dict:
        return {"updateParagraphStyle": {
            "range": rng(s, e),
            "paragraphStyle": {
                "indentStart": {"magnitude": 0, "unit": "PT"},
                "indentFirstLine": {"magnitude": 0, "unit": "PT"},
            },
            "fields": "indentStart,indentFirstLine",
        }}

    # Indexes below are original ones plus 1 for the temporary paragraph;
    # tab inserts go bottom-up so each lands at an unshifted index.
    requests: list[dict] = [
        {"insertText": {"location": {"index": s0, "tabId": tab_id}, "text": "\n"}},
        {"deleteParagraphBullets": {"range": rng(s0, e0 + 1)}},
        zero_indent(s0, e0 + 1),
    ]
    tabs_added = 0
    for i in reversed(window):
        n = levels.get(i, base) - base
        if n:
            requests.append({"insertText": {
                "location": {"index": content[i]["startIndex"] + 1, "tabId": tab_id},
                "text": "\t" * n,
            }})
            tabs_added += n
    requests.append({"createParagraphBullets": {
        "range": rng(s0, e0 + 1 + tabs_added), "bulletPreset": preset,
    }})
    # createParagraphBullets consumed the tabs, so indexes are back to
    # original + 1.
    for i in blanks:
        el = content[i]
        s, e = el["startIndex"] + 1, el["endIndex"] + 1
        requests.append({"deleteParagraphBullets": {"range": rng(s, e)}})
        style = el["paragraph"].get("paragraphStyle", {})
        kept = {k: style[k] for k in ("indentStart", "indentFirstLine") if k in style}
        # Listing a field without a value clears it back to inherited.
        requests.append({"updateParagraphStyle": {
            "range": rng(s, e), "paragraphStyle": kept,
            "fields": "indentStart,indentFirstLine",
        }})
    requests.append({"deleteContentRange": {"range": rng(s0, s0 + 1)}})

    return NestPlan(
        requests=requests, list_id=list_id, moved=len(moved),
        expected={i: lvl for i, lvl in levels.items()}, blanks=blanks,
        markers={i: _marker_style(content[i]) for i in levels},
        # The item the window joins is fingerprinted too, so positions
        # are checked against their surroundings.
        texts={i: _text(content[i]) for i in [start - 1, *levels, *blanks]},
        original={i: _level(content[i]) for i in levels},
        styles={i: _kept_style(content[i]) for i in levels},
    )


def _kept_style(el: dict) -> dict:
    """Paragraph style the rebuild must keep: everything but the indent,
    which the new level sets (headingId, named style, spacing, ...)."""
    style = (_paragraph(el) or {}).get("paragraphStyle", {})
    return {
        k: v for k, v in style.items()
        if k not in ("indentStart", "indentFirstLine")
    }


def is_unchanged(document_tab: dict, plan: NestPlan) -> bool:
    """Whether a re-read tab still shows the window exactly as planned from.

    Same text at every fingerprinted position, every rebuilt item still in
    the list at its original level with its original marker style, and no
    blank line bulleted: proof that the planned write did not land.
    """
    content = document_tab.get("body", {}).get("content", [])
    for i, want in plan.texts.items():
        if i >= len(content) or _text(content[i]) != want:
            return False
    for i, level in plan.original.items():
        el = content[i]
        if (
            not _in_list(el, plan.list_id) or _level(el) != level
            or _marker_style(el) != plan.markers.get(i, _marker_style(el))
        ):
            return False
    return all(_bullet(content[i]) is None for i in plan.blanks)


def check_result(document_tab: dict, plan: NestPlan) -> list[str]:
    """Differences between a re-read tab and what *plan* should have left."""
    content = document_tab.get("body", {}).get("content", [])
    problems = []
    for i, want in plan.texts.items():
        el = content[i] if i < len(content) else {}
        if _text(el) != want:
            # Positions are compared, so a shifted or altered paragraph
            # (another edit, a leftover tab) makes the rest meaningless.
            shown = repr(want.strip()[:40]) if want.strip() else "a blank line"
            return [
                f"{shown} is no longer where it was (the tab changed, "
                "or text was left behind)"
            ]
    for i, want in plan.expected.items():
        el = content[i] if i < len(content) else {}
        bullet = _bullet(el)
        if bullet is None:
            problems.append(f"{_label(el)} lost its bullet")
        elif bullet.get("listId") != plan.list_id:
            problems.append(f"{_label(el)} moved to another list")
        elif bullet.get("nestingLevel", 0) != want:
            problems.append(
                f"{_label(el)} is at level {bullet.get('nestingLevel', 0)}, "
                f"expected {want}"
            )
        elif _marker_style(el) != plan.markers.get(i, _marker_style(el)):
            problems.append(f"{_label(el)} has changed bullet or number formatting")
        elif i in plan.styles and _kept_style(el) != plan.styles[i]:
            before, after = plan.styles[i], _kept_style(el)
            fields = sorted(
                k for k in set(before) | set(after) if before.get(k) != after.get(k)
            )
            problems.append(
                f"{_label(el)} has a changed paragraph style ({', '.join(fields)})"
            )
    for i in plan.blanks:
        if i >= len(content) or _bullet(content[i]) is not None:
            problems.append("a blank line between items kept a bullet")
    return problems
