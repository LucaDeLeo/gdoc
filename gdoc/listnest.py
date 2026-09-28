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


def _newline_style(el: dict) -> dict:
    """Text style of the run that ends the paragraph (holds its newline)."""
    for pe in reversed((_paragraph(el) or {}).get("elements", [])):
        run = pe.get("textRun")
        if run is not None and run.get("content", "").endswith("\n"):
            return run.get("textStyle", {})
    return {}


def _label(el: dict) -> str:
    text = _text(el).strip()
    return repr(text[:40] + ("..." if len(text) > 40 else ""))


def locate_item(body: dict, text: str) -> int:
    """Index into ``body["content"]`` of the one paragraph containing *text*.

    Matching is the same case-insensitive search ``edit`` uses. Raises a
    usage error (exit 3) when the text is missing, is found in more than
    one paragraph, spans paragraphs, or sits in a table.
    """
    from gdoc.api.docs import diagnose_no_match, find_text_in_document

    matches = find_text_in_document(None, text, body=body)
    if not matches:
        reason = diagnose_no_match(None, text, body=body)
        raise _usage(f"no match found for {text!r}" + (f"; {reason}" if reason else ""))
    content = body.get("content", [])
    found: set[int] = set()
    in_container = 0
    for m in matches:
        for i, el in enumerate(content):
            if el.get("startIndex", 0) <= m["startIndex"] < el.get("endIndex", 0):
                if _paragraph(el) is None:
                    in_container += 1
                elif m["endIndex"] > el["endIndex"]:
                    raise _usage(f"{text!r} spans more than one paragraph")
                else:
                    found.add(i)
                break
    if len(found) + in_container > 1:
        raise _usage(
            f"{text!r} matches {len(found) + in_container} paragraphs; use "
            "text unique to one list item"
        )
    if not found:
        raise _usage(
            f"{text!r} is inside a table or other container; only list "
            "items in the tab body can be nested"
        )
    return found.pop()


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
        below is not None and _in_list(below, list_id)
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
        marker = _marker_style(el)
        derived = _set_fields(_newline_style(el))
        odd = sorted(k for k, v in marker.items() if derived.get(k) != v)
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

    blanks = [i for i in window if _is_blank(content[i])]
    s0 = content[start]["startIndex"]
    e0 = content[end]["endIndex"]

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
    )


def check_result(document_tab: dict, plan: NestPlan) -> list[str]:
    """Differences between a re-read tab and what *plan* should have left."""
    content = document_tab.get("body", {}).get("content", [])
    problems = []
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
            problems.append(f"{_label(el)} lost its bullet or number formatting")
    for i in plan.blanks:
        if i >= len(content) or _bullet(content[i]) is not None:
            problems.append("a blank line between items kept a bullet")
    return problems
