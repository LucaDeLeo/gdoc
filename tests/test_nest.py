"""Tests for `gdoc nest` / `gdoc unnest` and the planner in gdoc.listnest."""

import io
import json
from types import SimpleNamespace

import pytest

from gdoc import mcp
from gdoc.cli import build_parser, cmd_nest
from gdoc.listnest import check_result, list_preset, locate_item, plan_nesting
from gdoc.util import GdocError

TAB = "t.1"
NUM = "NUMBERED_DECIMAL_ALPHA_ROMAN"
BUL = "BULLET_DISC_CIRCLE_SQUARE"


def _levels(fmt, **per_level):
    """Nine list levels as the API returns them; fmt is "%{i}." or "%{i}"."""
    return {"listProperties": {"nestingLevels": [
        {"glyphFormat": fmt.format(i=i),
         "indentStart": {"magnitude": 36 * (i + 1), "unit": "PT"},
         "indentFirstLine": {"magnitude": 36 * (i + 1) - 18, "unit": "PT"},
         **{k: v[i % 3] for k, v in per_level.items()}}
        for i in range(9)
    ]}}


NUM_GLYPHS = ("DECIMAL", "ALPHA", "ROMAN")
LISTS = {
    "num": _levels("%{i}.", glyphType=NUM_GLYPHS),
    "num2": _levels("%{i}.", glyphType=NUM_GLYPHS),
    "parens": _levels("%{i})", glyphType=NUM_GLYPHS),
    "bul": _levels("%{i}", glyphSymbol=("●", "○", "■")),
    "check": _levels("%{i}", glyphType=("GLYPH_TYPE_UNSPECIFIED",) * 3),
    "diamond": _levels("%{i}", glyphSymbol=("❖", "➢", "■")),
}


def _tab(*lines):
    """A documentTab from (text, level, list_id) lines.

    list_id None is a plain paragraph; text "" is a blank line. A line
    may carry a 4th element: extra paragraph fields.
    """
    content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
    idx = 1
    for line in lines:
        text, level, lid = line[:3]
        t = text + "\n"
        para = {
            "elements": [{
                "startIndex": idx, "endIndex": idx + len(t),
                "textRun": {"content": t, "textStyle": {}},
            }],
            "paragraphStyle": {},
        }
        if lid:
            para["bullet"] = {"listId": lid, "nestingLevel": level}
        if len(line) > 3:
            para.update(line[3])
        content.append({"startIndex": idx, "endIndex": idx + len(t), "paragraph": para})
        idx += len(t)
    return {"body": {"content": content}, "lists": LISTS}


def _at(tab, text):
    return locate_item(tab["body"], text)


def _plan(tab, text, delta, to=None):
    first = _at(tab, text)
    last = _at(tab, to) if to else first
    return plan_nesting(tab, TAB, first, last, delta)


def _rng(s, e):
    return {"startIndex": s, "endIndex": e, "tabId": TAB}


def _zero(s, e):
    return {"updateParagraphStyle": {
        "range": _rng(s, e),
        "paragraphStyle": {
            "indentStart": {"magnitude": 0, "unit": "PT"},
            "indentFirstLine": {"magnitude": 0, "unit": "PT"},
        },
        "fields": "indentStart,indentFirstLine",
    }}


STD = [
    ("Intro", 0, None),
    ("Alpha", 0, "num"),   # 7..13
    ("Bravo", 0, "num"),   # 13..19
    ("Charlie", 0, "num"),  # 19..27
    ("Outro", 0, None),
]


class TestPlanRequests:
    def test_nest_second_item_is_the_anchored_rebuild(self):
        plan = _plan(_tab(*STD), "Bravo", 1)
        assert plan.requests == [
            {"insertText": {"location": {"index": 13, "tabId": TAB}, "text": "\n"}},
            {"deleteParagraphBullets": {"range": _rng(13, 20)}},
            _zero(13, 20),
            {"insertText": {"location": {"index": 14, "tabId": TAB}, "text": "\t"}},
            {"createParagraphBullets": {"range": _rng(13, 21), "bulletPreset": NUM}},
            {"deleteContentRange": {"range": _rng(13, 14)}},
        ]
        assert plan.moved == 1
        assert plan.expected == {3: 1}
        assert plan.list_id == "num"

    def test_bullet_list_uses_the_bullet_preset(self):
        tab = _tab(("Alpha", 0, "bul"), ("Bravo", 0, "bul"))
        create = [r for r in _plan(tab, "Bravo", 1).requests
                  if "createParagraphBullets" in r]
        assert create[0]["createParagraphBullets"]["bulletPreset"] == BUL

    def test_base_is_the_level_of_the_item_before_the_window(self):
        # Charlie joins at Bravo's level 1, so it needs 2 - 1 = 1 tab.
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 1, "num"), ("Charlie", 1, "num"))
        plan = _plan(tab, "Charlie", 1)
        tabs = [r["insertText"]["text"] for r in plan.requests
                if "insertText" in r and r["insertText"]["text"] != "\n"]
        assert tabs == ["\t"]
        assert plan.expected == {3: 2}

    def test_unnest_below_a_deeper_sibling_rebuilds_the_sibling(self):
        # Alpha / a-one (1) / a-two (2) / Bravo (1) -> unnest Bravo to 0:
        # the window extends back to a-one, the first item no deeper
        # than 0 is Alpha, so the base is 0.
        tab = _tab(
            ("Alpha", 0, "num"), ("a-one", 1, "num"), ("a-two", 2, "num"),
            ("Bravo", 1, "num"),
        )
        plan = _plan(tab, "Bravo", -1)
        assert plan.expected == {2: 1, 3: 2, 4: 0}
        a_one = tab["body"]["content"][2]["startIndex"]
        assert plan.requests[0]["insertText"]["location"]["index"] == a_one
        tabs = [(r["insertText"]["location"]["index"], r["insertText"]["text"])
                for r in plan.requests[3:]
                if "insertText" in r]
        # bottom-up: Bravo gets none, a-two 2, a-one 1
        assert tabs == [
            (tab["body"]["content"][3]["startIndex"] + 1, "\t\t"),
            (a_one + 1, "\t"),
        ]

    def test_descendants_move_with_the_item(self):
        tab = _tab(
            ("Alpha", 0, "num"), ("Bravo", 0, "num"), ("b-one", 1, "num"),
            ("b-two", 2, "num"), ("Charlie", 0, "num"),
        )
        plan = _plan(tab, "Bravo", 1)
        assert plan.moved == 3
        assert plan.expected == {2: 1, 3: 2, 4: 3}

    def test_range_moves_every_item_and_descendants(self):
        tab = _tab(
            ("Alpha", 0, "num"), ("Bravo", 0, "num"), ("Charlie", 0, "num"),
            ("c-one", 1, "num"), ("Delta", 0, "num"),
        )
        plan = _plan(tab, "Bravo", 1, to="Charlie")
        assert plan.expected == {2: 1, 3: 1, 4: 2}

    def test_levels_greater_than_one(self):
        tab = _tab(("Alpha", 0, "num"), ("a-one", 1, "num"), ("a-two", 2, "num"),
                   ("Bravo", 0, "num"))
        assert _plan(tab, "Bravo", 2).expected == {4: 2}
        assert _plan(tab, "a-two", -2).expected == {2: 1, 3: 0}

    def test_loose_list_extends_over_blanks_and_restores_their_indent(self):
        blank_style = {"paragraphStyle": {
            "indentStart": {"magnitude": 36, "unit": "PT"},
        }}
        tab = _tab(
            ("Alpha", 0, "num"), ("", 0, None, blank_style), ("Bravo", 0, "num"),
            ("", 0, None), ("Charlie", 0, "num"),
        )
        plan = _plan(tab, "Bravo", 1)
        blank = tab["body"]["content"][2]
        # window starts at the blank after Alpha, so Bravo joins Alpha's list
        start = plan.requests[0]["insertText"]["location"]["index"]
        assert start == blank["startIndex"]
        assert plan.blanks == [2]
        s = blank["startIndex"] + 1
        assert {"deleteParagraphBullets": {"range": _rng(s, s + 1)}} in plan.requests
        restore = {"updateParagraphStyle": {
            "range": _rng(s, s + 1),
            "paragraphStyle": {"indentStart": {"magnitude": 36, "unit": "PT"}},
            "fields": "indentStart,indentFirstLine",
        }}
        assert restore in plan.requests
        # the blank after Bravo is not in the window
        assert plan.expected == {3: 1}

    def test_blank_without_own_indent_gets_the_fields_cleared(self):
        tab = _tab(("Alpha", 0, "num"), ("", 0, None), ("Bravo", 0, "num"))
        plan = _plan(tab, "Bravo", 1)
        clears = [r for r in plan.requests if "updateParagraphStyle" in r
                  and r["updateParagraphStyle"]["paragraphStyle"] == {}]
        assert len(clears) == 1

    def test_list_at_the_end_of_the_body(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        plan = _plan(tab, "Bravo", 1)
        assert plan.requests[-1] == {"deleteContentRange": {"range": _rng(7, 8)}}

    def test_nested_first_list_item_under_another_list(self):
        # A numbered sublist (own list, starting at level 1) under a bullet
        # parent: moving its second item stays inside that sublist.
        tab = _tab(("Parent", 0, "bul"), ("n-one", 1, "num"), ("n-two", 1, "num"))
        assert _plan(tab, "n-two", 1).expected == {3: 2}


class TestRefusals:
    def _refused(self, tab, text, delta, match, to=None):
        with pytest.raises(GdocError, match=match) as exc:
            _plan(tab, text, delta, to=to)
        assert exc.value.exit_code == 3

    def test_no_match(self):
        with pytest.raises(GdocError, match="no match") as exc:
            _at(_tab(*STD), "Zulu")
        assert exc.value.exit_code == 3

    def test_ambiguous_text(self):
        with pytest.raises(GdocError, match="matches 3 paragraphs") as exc:
            _at(_tab(*STD), "a")  # Alpha, Charlie (and Bravo) all contain a
        assert exc.value.exit_code == 3

    def test_repeated_text_in_one_item_is_not_ambiguous(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo bravo", 0, "num"))
        assert _at(tab, "bravo") == 2

    def test_text_spanning_paragraphs(self):
        with pytest.raises(GdocError, match="more than one paragraph"):
            _at(_tab(*STD), "Alpha\nBravo")

    def test_text_in_a_table(self):
        tab = _tab(*STD)
        cell_para = {"startIndex": 34, "endIndex": 39, "paragraph": {"elements": [{
            "startIndex": 34, "endIndex": 39, "textRun": {"content": "Cell\n"}}]}}
        tab["body"]["content"].append({
            "startIndex": 33, "endIndex": 40,
            "table": {"tableRows": [{"tableCells": [{"content": [cell_para]}]}]},
        })
        with pytest.raises(GdocError, match="inside a table"):
            _at(tab, "Cell")

    def test_table_match_counts_toward_ambiguity(self):
        tab = _tab(*STD)
        cell_para = {"startIndex": 34, "endIndex": 40, "paragraph": {"elements": [{
            "startIndex": 34, "endIndex": 40, "textRun": {"content": "Bravo\n"}}]}}
        tab["body"]["content"].append({
            "startIndex": 33, "endIndex": 41,
            "table": {"tableRows": [{"tableCells": [{"content": [cell_para]}]}]},
        })
        with pytest.raises(GdocError, match="matches 2 paragraphs"):
            _at(tab, "Bravo")

    def test_no_match_hint_does_not_offer_normalize(self):
        tab = _tab(("Alpha", 0, "num"), ("Don\u2019t stop", 0, "num"))
        with pytest.raises(GdocError) as exc:
            _at(tab, "Don't stop")
        assert "--normalize" not in str(exc.value)

    def test_not_a_list_item(self):
        self._refused(_tab(*STD), "Intro", 1, "not a list item")

    def test_first_item_cannot_nest(self):
        self._refused(_tab(*STD), "Alpha", 1, "no list item above")

    def test_top_level_item_cannot_unnest(self):
        self._refused(_tab(*STD), "Bravo", -1, "cannot be unnested")

    def test_nesting_more_than_one_level_below_the_item_above(self):
        self._refused(_tab(*STD), "Bravo", 2, "more than one level below")

    def test_unnest_that_would_strand_the_next_item(self):
        tab = _tab(("Alpha", 0, "num"), ("a-one", 1, "num"), ("a-two", 2, "num"),
                   ("a-three", 2, "num"))
        self._refused(tab, "a-two", -2, "more than one level below")

    def test_levels_past_the_deepest(self):
        lines = [("L0", 0, "num")] + [(f"L{i}", i - 1, "num") for i in range(1, 10)]
        tab = _tab(*[(t, min(lvl, 8), lid) for t, lvl, lid in lines])
        self._refused(tab, "L9", 1, "at most 9 levels")

    def test_checkbox_list(self):
        tab = _tab(("Alpha", 0, "check"), ("Bravo", 0, "check"))
        self._refused(tab, "Bravo", 1, "checkbox or custom")

    def test_custom_glyph_list(self):
        tab = _tab(("Alpha", 0, "diamond"), ("Bravo", 0, "diamond"))
        self._refused(tab, "Bravo", 1, "checkbox or custom")

    def test_range_across_two_lists(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"), ("X-one", 0, "num2"),
                   ("X-two", 0, "num2"))
        self._refused(tab, "Bravo", 1, "not in the same list", to="X-two")

    def test_range_through_a_different_list(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"), ("inner", 1, "bul"),
                   ("Charlie", 0, "num"))
        self._refused(tab, "Bravo", 1, "from a different list", to="Charlie")

    def test_range_through_prose(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"), ("prose", 0, None),
                   ("Charlie", 0, "num"))
        self._refused(tab, "Bravo", 1, "not a list item", to="Charlie")

    def test_to_before_the_first_item(self):
        self._refused(_tab(*STD), "Charlie", 1, "comes before", to="Bravo")

    def test_sub_items_in_another_list(self):
        # Bravo's child is its own (mixed) list: rebuilding Bravo's window
        # would pull it into Bravo's list.
        tab = _tab(("Alpha", 0, "bul"), ("Bravo", 0, "bul"), ("b-one", 1, "num"))
        self._refused(tab, "Bravo", 1, "sub-items in a separate list")

    def test_item_after_a_sublist_of_another_list(self):
        # Charlie follows Alpha's numbered sublist: its bullets would not
        # join the bullet list, so Charlie would leave it.
        tab = _tab(("Alpha", 0, "bul"), ("a-one", 1, "num"), ("Charlie", 0, "bul"))
        self._refused(tab, "Charlie", 1, "different list")

    def test_unnest_that_would_rehome_a_sibling_list(self):
        # Unnesting Bravo extends the window back over a-one, which is its
        # own list: it would be merged into Bravo's list.
        tab = _tab(("Alpha", 0, "num"), ("a-one", 2, "num2"), ("Bravo", 1, "num"))
        self._refused(tab, "Bravo", -1, "different list")

    def test_first_item_of_a_sublist_cannot_unnest_into_the_parent_list(self):
        tab = _tab(("Parent", 0, "bul"), ("n-one", 1, "num"), ("n-two", 1, "num"))
        self._refused(tab, "n-one", -1, "different list")

    def test_item_after_a_non_list_paragraph(self):
        tab = _tab(("Alpha", 0, "num"), ("prose", 0, None), ("Bravo", 0, "num"))
        self._refused(tab, "Bravo", 1, "no list item above")

    def test_list_continued_after_prose_still_moves(self):
        # Bravo continues Alpha's list after a prose paragraph; unnesting
        # b-two rebuilds b-one and joins at Bravo.
        tab = _tab(("Alpha", 0, "num"), ("prose", 0, None), ("Bravo", 0, "num"),
                   ("b-one", 1, "num"), ("b-two", 1, "num"))
        assert _plan(tab, "b-two", -1).expected == {4: 1, 5: 0}

    def test_leading_tab_in_item_text(self):
        tab = _tab(("Alpha", 0, "num"), ("\tBravo", 0, "num"))
        self._refused(tab, "Bravo", 1, "starts with a tab")

    def test_hand_set_indent_on_a_moved_item(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num", {"paragraphStyle": {
            "indentStart": {"magnitude": 60, "unit": "PT"},
        }}))
        self._refused(tab, "Bravo", 1, "hand-set indent")

    def test_hand_set_indent_on_a_rebuilt_sibling(self):
        # Unnesting Bravo rebuilds a-one, whose own indent would be lost.
        tab = _tab(("Alpha", 0, "num"), ("a-one", 2, "num", {"paragraphStyle": {
            "indentFirstLine": {"magnitude": 10, "unit": "PT"},
        }}), ("Bravo", 1, "num"))
        self._refused(tab, "Bravo", -1, "hand-set indent")

    def test_standard_indent_is_not_hand_set(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num", {"paragraphStyle": {
            "indentStart": {"magnitude": 36, "unit": "PT"},
            "indentFirstLine": {"magnitude": 18, "unit": "PT"},
        }}))
        assert _plan(tab, "Bravo", 1).moved == 1

    def test_formatted_marker_on_a_moved_item(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        tab["body"]["content"][2]["paragraph"]["bullet"]["textStyle"] = {
            "underline": False, "bold": True,
        }
        self._refused(tab, "Bravo", 1, "formatted bullet or number \\(bold\\)")

    def test_formatted_marker_on_a_rebuilt_sibling(self):
        tab = _tab(("Alpha", 0, "num"), ("a-one", 2, "num"), ("Bravo", 1, "num"))
        tab["body"]["content"][2]["paragraph"]["bullet"]["textStyle"] = {
            "foregroundColor": {"color": {"rgbColor": {"red": 1}}},
        }
        self._refused(tab, "Bravo", -1, "formatted bullet or number")

    def test_marker_style_that_follows_the_text_is_kept(self):
        # A fully bold item has a bold number; the rebuilt marker takes the
        # text's style again (live-tested), so it is not refused.
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        para = tab["body"]["content"][2]["paragraph"]
        para["bullet"]["textStyle"] = {"bold": True}
        para["elements"][0]["textRun"]["textStyle"] = {"bold": True}
        plan = _plan(tab, "Bravo", 1)
        assert plan.markers == {2: {"bold": True}}

    @pytest.mark.parametrize("key", ["bold", "italic"])
    def test_explicitly_unformatted_marker_on_a_formatted_item(self, key):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        para = tab["body"]["content"][2]["paragraph"]
        para["bullet"]["textStyle"] = {key: False, "underline": False}
        para["elements"][0]["textRun"]["textStyle"] = {key: True}
        self._refused(tab, "Bravo", 1, f"formatted bullet or number \\({key}\\)")

    def test_plain_marker_on_styled_text_is_not_refused(self):
        # Live: an item ending in Georgia (or a link) keeps a plain marker
        # through the rebuild.
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        para = tab["body"]["content"][2]["paragraph"]
        para["bullet"]["textStyle"] = {"underline": False}
        para["elements"][0]["textRun"]["textStyle"] = {
            "weightedFontFamily": {"fontFamily": "Georgia", "weight": 400},
            "underline": True,
        }
        assert _plan(tab, "Bravo", 1).moved == 1

    @pytest.mark.parametrize("style", [
        {"weightedFontFamily": {"fontFamily": "Georgia", "weight": 400}},
        {"foregroundColor": {"color": {"rgbColor": {"red": 1}}}},
        {"italic": True},
    ])
    def test_marker_style_without_live_evidence_is_refused(self, style):
        # Even when the text carries the same style: only bold is known to
        # survive the rebuild.
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        para = tab["body"]["content"][2]["paragraph"]
        para["bullet"]["textStyle"] = dict(style)
        para["elements"][0]["textRun"]["textStyle"] = dict(style)
        self._refused(tab, "Bravo", 1, "formatted bullet or number")

    def test_bold_marker_on_partly_bold_text_is_refused(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        para = tab["body"]["content"][2]["paragraph"]
        para["bullet"]["textStyle"] = {"bold": True, "underline": False}
        start = para["elements"][0]["startIndex"]
        para["elements"] = [
            {"startIndex": start, "endIndex": start + 3,
             "textRun": {"content": "Bra", "textStyle": {}}},
            {"startIndex": start + 3, "endIndex": start + 6,
             "textRun": {"content": "vo\n", "textStyle": {"bold": True}}},
        ]
        self._refused(tab, "Bravo", 1, "formatted bullet or number \\(bold\\)")

    def test_plain_marker_style_is_not_formatting(self):
        tab = _tab(("Alpha", 0, "num"), ("Bravo", 0, "num"))
        tab["body"]["content"][2]["paragraph"]["bullet"]["textStyle"] = {
            "underline": False,
        }
        assert _plan(tab, "Bravo", 1).moved == 1

    def test_pending_suggestion(self):
        tab = _tab(("Alpha", 0, "num"),
                   ("Bravo", 0, "num", {"suggestedBulletChanges": {"s.1": {}}}))
        self._refused(tab, "Bravo", 1, "pending suggestions")

    def test_zero_levels(self):
        with pytest.raises(GdocError, match="at least 1"):
            plan_nesting(_tab(*STD), TAB, 3, 3, 0)


class TestPresets:
    def test_default_presets(self):
        assert list_preset(LISTS, "num") == NUM
        assert list_preset(LISTS, "bul") == BUL

    def test_other_lists(self):
        assert list_preset(LISTS, "check") is None
        assert list_preset(LISTS, "parens") is None
        assert list_preset(LISTS, "diamond") is None
        assert list_preset(LISTS, "missing") is None


class TestCheckResult:
    def test_matching_tab_passes(self):
        tab = _tab(*STD)
        plan = _plan(tab, "Bravo", 1)
        after = _tab(("Intro", 0, None), ("Alpha", 0, "num"), ("Bravo", 1, "num"),
                     ("Charlie", 0, "num"), ("Outro", 0, None))
        assert check_result(after, plan) == []

    def test_lost_marker_formatting_is_reported(self):
        tab = _tab(*STD)
        para = tab["body"]["content"][3]["paragraph"]
        para["bullet"]["textStyle"] = {"bold": True}
        para["elements"][0]["textRun"]["textStyle"] = {"bold": True}
        plan = _plan(tab, "Bravo", 1)
        after = _tab(("Intro", 0, None), ("Alpha", 0, "num"), ("Bravo", 1, "num"),
                     ("Charlie", 0, "num"), ("Outro", 0, None))
        assert "formatting" in check_result(after, plan)[0]
        after["body"]["content"][3]["paragraph"]["bullet"]["textStyle"] = {
            "bold": True, "underline": False,
        }
        assert check_result(after, plan) == []

    def test_shifted_paragraphs_are_reported_not_trusted(self):
        # A paragraph inserted above the list shifts every position; the
        # items now at the planned positions must not pass for the moved ones.
        plan = _plan(_tab(*STD), "Bravo", 1)
        shifted = _tab(("New", 0, None), ("Intro", 0, None), ("Alpha", 1, "num"),
                       ("Bravo", 0, "num"), ("Charlie", 0, "num"))
        assert "no longer where it was" in check_result(shifted, plan)[0]

    def test_an_inserted_twin_item_does_not_pass_for_the_target(self):
        # A new item with the target's text and planned level lands at the
        # target's position; the anchor fingerprint (Alpha above) catches it.
        plan = _plan(_tab(*STD), "Bravo", 1)
        twin = _tab(("Intro", 0, None), ("New", 0, "num"), ("Bravo", 1, "num"),
                    ("Bravo", 0, "num"), ("Charlie", 0, "num"))
        assert check_result(twin, plan)

    def test_changed_heading_id_is_reported(self):
        tab = _tab(*STD)
        tab["body"]["content"][3]["paragraph"]["paragraphStyle"] = {
            "namedStyleType": "HEADING_2", "headingId": "h.abc",
        }
        plan = _plan(tab, "Bravo", 1)
        after = _tab(("Intro", 0, None), ("Alpha", 0, "num"), ("Bravo", 1, "num"),
                     ("Charlie", 0, "num"), ("Outro", 0, None))
        after["body"]["content"][3]["paragraph"]["paragraphStyle"] = {
            "namedStyleType": "HEADING_2", "headingId": "h.new",
            "indentStart": {"magnitude": 72, "unit": "PT"},
        }
        assert "paragraph style (headingId)" in check_result(after, plan)[0]
        style = after["body"]["content"][3]["paragraph"]["paragraphStyle"]
        style["headingId"] = "h.abc"
        assert check_result(after, plan) == []

    def test_wrong_level_or_list_is_reported(self):
        plan = _plan(_tab(*STD), "Bravo", 1)
        stayed = _tab(*STD)
        assert "expected 1" in check_result(stayed, plan)[0]
        split = _tab(("Intro", 0, None), ("Alpha", 0, "num"), ("Bravo", 1, "num2"))
        assert "another list" in check_result(split, plan)[0]


# -- command handler -----------------------------------------------------


def _args(**overrides):
    defaults = {
        "command": "nest", "doc": "doc1", "text": "Bravo", "to": None,
        "levels": 1, "tab": None, "quiet": True,
        "json": False, "verbose": False, "plain": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _doc(document_tab, revision="rev1"):
    return {"revisionId": revision, "tabs": [{
        "tabProperties": {"tabId": TAB, "title": "Main"},
        "documentTab": document_tab,
    }]}


NESTED = _tab(("Intro", 0, None), ("Alpha", 0, "num"), ("Bravo", 1, "num"),
              ("Charlie", 0, "num"), ("Outro", 0, None))


@pytest.fixture
def api(mocker):
    mocker.patch("gdoc.notify.pre_flight", return_value=None)
    get = mocker.patch(
        "gdoc.api.docs.get_document_with_tabs",
        side_effect=[_doc(_tab(*STD)), _doc(NESTED)],
    )
    write = mocker.patch("gdoc.api.docs.batch_update_pinned")
    mocker.patch("gdoc.api.drive.get_file_version", return_value={"version": 9})
    state = mocker.patch("gdoc.state.update_state_after_command")
    return SimpleNamespace(get=get, write=write, state=state)


class TestCommand:
    def test_nest_writes_one_pinned_batch(self, api, capsys):
        assert cmd_nest(_args()) == 0
        doc_id, requests, revision = api.write.call_args.args
        assert (doc_id, revision) == ("doc1", "rev1")
        assert requests == plan_nesting(_tab(*STD), TAB, 3, 3, 1).requests
        assert capsys.readouterr().out == "OK nested 1 item by 1 level\n"
        assert api.state.call_args.kwargs["command"] == "nest"

    def test_unnest_moves_the_other_way(self, api, capsys):
        api.get.side_effect = [_doc(NESTED), _doc(_tab(*STD))]
        assert cmd_nest(_args(command="unnest")) == 0
        expected = plan_nesting(NESTED, TAB, 3, 3, -1).requests
        assert api.write.call_args.args[1] == expected
        assert capsys.readouterr().out == "OK unnested 1 item by 1 level\n"

    def test_json_output(self, api, capsys):
        cmd_nest(_args(json=True))
        out = json.loads(capsys.readouterr().out)
        assert out == {"ok": True, "moved": 1, "levels": 1}

    def test_refusal_writes_nothing(self, api):
        with pytest.raises(GdocError) as exc:
            cmd_nest(_args(text="Alpha"))
        assert exc.value.exit_code == 3
        api.write.assert_not_called()

    def test_tab_is_resolved_by_title(self, api, mocker):
        other = {"tabProperties": {"tabId": "t.0", "title": "Other"},
                 "documentTab": _tab(("Nothing here", 0, None))}
        first = _doc(_tab(*STD))
        first["tabs"].insert(0, other)
        second = _doc(NESTED)
        second["tabs"].insert(0, other)
        api.get.side_effect = [first, second]
        assert cmd_nest(_args(tab="main")) == 0

    def test_default_tab_is_the_first(self, api):
        other = {"tabProperties": {"tabId": "t.0", "title": "Other"},
                 "documentTab": _tab(("Nothing here", 0, None))}
        first = _doc(_tab(*STD))
        first["tabs"].insert(0, other)
        api.get.side_effect = [first]
        with pytest.raises(GdocError, match="no match"):
            cmd_nest(_args())

    def test_unknown_tab(self, api):
        with pytest.raises(GdocError, match="tab not found") as exc:
            cmd_nest(_args(tab="Nope"))
        assert exc.value.exit_code == 3

    def test_zero_levels_is_refused_before_any_call(self, api):
        with pytest.raises(GdocError, match="at least 1"):
            cmd_nest(_args(levels=0))
        api.get.assert_not_called()

    def test_result_that_did_not_land_is_an_error(self, api):
        api.get.side_effect = [_doc(_tab(*STD)), _doc(_tab(*STD))]
        with pytest.raises(GdocError, match="saved but the list is not") as exc:
            cmd_nest(_args())
        assert exc.value.exit_code == 1

    def test_failed_read_back_after_the_write_is_a_warning(self, api, capsys):
        api.get.side_effect = [_doc(_tab(*STD)), GdocError("API error (503)")]
        assert cmd_nest(_args()) == 0
        out = capsys.readouterr()
        assert out.out == "OK nested 1 item by 1 level\n"
        assert "could not be read back" in out.err

    def test_failed_version_refresh_is_a_warning(self, api, mocker, capsys):
        mocker.patch("gdoc.api.drive.get_file_version",
                     side_effect=ConnectionError("reset"))
        assert cmd_nest(_args()) == 0
        assert "awareness state not updated" in capsys.readouterr().err
        api.state.assert_not_called()

    def test_failed_state_write_after_the_write_is_a_warning(self, api, capsys):
        api.state.side_effect = OSError("read-only file system")
        assert cmd_nest(_args()) == 0
        assert api.write.call_count == 1
        assert "awareness state was not persisted" in capsys.readouterr().err

    def test_missing_revision_refuses_to_write(self, api):
        api.get.side_effect = [_doc(_tab(*STD), revision="")]
        with pytest.raises(GdocError, match="needs edit access") as exc:
            cmd_nest(_args())
        assert exc.value.exit_code == 3
        api.write.assert_not_called()

    def test_stale_revision_after_an_applied_resend_is_success(self, api, capsys):
        from gdoc.api.docs import StaleRevisionError

        api.write.side_effect = StaleRevisionError("document changed")
        assert cmd_nest(_args()) == 0
        captured = capsys.readouterr()
        assert captured.out == "OK nested 1 item by 1 level\n"
        assert "already exactly as planned" in captured.err

    def test_stale_revision_with_a_third_state_is_an_unknown_outcome(self, api):
        from gdoc.api.docs import StaleRevisionError

        other = _tab(("Intro", 0, None), ("Alpha", 0, "num"), ("Bravo", 2, "num"),
                     ("Charlie", 0, "num"), ("Outro", 0, None))
        api.get.side_effect = [_doc(_tab(*STD)), _doc(other)]
        api.write.side_effect = StaleRevisionError("document changed; re-run it")
        with pytest.raises(GdocError, match="may already be applied") as exc:
            cmd_nest(_args())
        assert not isinstance(exc.value, StaleRevisionError)

    def test_applied_resend_with_closed_stderr_still_succeeds(self, api, mocker):
        from gdoc.api.docs import StaleRevisionError

        class Closed(io.StringIO):
            def write(self, _):
                raise BrokenPipeError

            def fileno(self):
                raise io.UnsupportedOperation

        api.write.side_effect = StaleRevisionError("document changed")
        mocker.patch("sys.stderr", Closed())
        assert cmd_nest(_args()) == 0

    def test_stale_revision_with_the_list_unchanged_says_re_run(self, api):
        from gdoc.api.docs import StaleRevisionError

        api.get.side_effect = [_doc(_tab(*STD)), _doc(_tab(*STD))]
        api.write.side_effect = StaleRevisionError("document changed; re-run it")
        with pytest.raises(StaleRevisionError, match="re-run"):
            cmd_nest(_args())

    def test_stale_revision_then_failed_reread_is_an_unknown_outcome(self, api):
        from gdoc.api.docs import StaleRevisionError

        api.get.side_effect = [_doc(_tab(*STD)), GdocError("API error (503)")]
        api.write.side_effect = StaleRevisionError("document changed; re-run it")
        with pytest.raises(GdocError, match="may already be applied") as exc:
            cmd_nest(_args())
        assert "re-run" not in str(exc.value)

    def test_read_back_finds_the_tab_by_id_not_title(self, api):
        # Another tab is titled like this tab's ID; the read-back must still
        # check this tab.
        decoy = {"tabProperties": {"tabId": "t.9", "title": TAB},
                 "documentTab": _tab(("Nothing here", 0, None))}
        second = _doc(NESTED)
        second["tabs"].insert(0, decoy)
        api.get.side_effect = [_doc(_tab(*STD)), second]
        assert cmd_nest(_args()) == 0

    def test_closed_stdout_after_the_write_still_succeeds(self, api, mocker):
        class Closed(io.StringIO):
            def write(self, _):
                raise BrokenPipeError

            def fileno(self):
                raise io.UnsupportedOperation

        mocker.patch("sys.stdout", Closed())
        assert cmd_nest(_args()) == 0
        api.state.assert_called_once()

    def test_full_disk_on_output_after_the_write_still_succeeds(self, api, mocker):
        import errno

        class Full(io.StringIO):
            def flush(self):
                raise OSError(errno.ENOSPC, "No space left on device")

            def fileno(self):
                raise io.UnsupportedOperation

        mocker.patch("sys.stdout", Full())
        assert cmd_nest(_args()) == 0
        api.state.assert_called_once()

    def test_parser(self):
        args = build_parser().parse_args(
            ["unnest", "doc1", "Bravo", "--to", "Delta", "--levels", "2", "--tab", "T"],
        )
        assert (args.func, args.text, args.to, args.levels, args.tab) == (
            cmd_nest, "Bravo", "Delta", 2, "T",
        )


# -- MCP parity ----------------------------------------------------------


@pytest.mark.parametrize("command", ["nest", "unnest"])
def test_mcp_exposes_the_same_parameters(command):
    tool = mcp.build_tools()[f"gdoc_{command}"]
    props = tool["inputSchema"]["properties"]
    assert {"doc", "text", "to", "levels", "tab", "quiet", "account"} <= set(props)
    assert set(tool["inputSchema"]["required"]) == {"doc", "text"}
    assert f"gdoc_{command}" not in mcp.build_tools(read_only=True)


@pytest.mark.parametrize("command", ["nest", "unnest"])
def test_mcp_call_runs_the_cli_handler_with_the_same_argv(mocker, command):
    seen = {}

    def fake_run(argv, check_updates=True):
        seen["args"] = build_parser().parse_args(argv)
        return 0

    mocker.patch("gdoc.cli.run_argv", side_effect=fake_run)
    mcp.call_command(command, {
        "doc": "doc1", "text": "-Bravo", "to": "Delta", "levels": 2,
        "tab": "Main", "account": "work",
    })
    args = seen["args"]
    assert (args.func, args.command, args.text, args.to, args.levels, args.tab,
            args.account) == (cmd_nest, command, "-Bravo", "Delta", 2, "Main", "work")


# -- pinned write error classes -----------------------------------------


def _http_error(status, content=b""):
    import httplib2
    from googleapiclient.errors import HttpError

    return HttpError(httplib2.Response({"status": str(status)}), content)


@pytest.fixture
def execute(mocker):
    svc = mocker.patch("gdoc.api.docs.get_docs_service")
    return svc.return_value.documents.return_value.batchUpdate.return_value.execute


class TestBatchUpdatePinned:
    def test_sends_the_revision_pin(self, execute):
        from gdoc.api.docs import batch_update_pinned, get_docs_service

        batch_update_pinned("doc1", [{"x": 1}], "rev1")
        body = get_docs_service().documents().batchUpdate.call_args.kwargs["body"]
        assert body == {"requests": [{"x": 1}],
                        "writeControl": {"requiredRevisionId": "rev1"}}

    def test_stale_revision_says_re_run(self, execute):
        from gdoc.api.docs import batch_update_pinned

        execute.side_effect = _http_error(400, b"The required revision ID is stale")
        with pytest.raises(GdocError, match="re-run it"):
            batch_update_pinned("doc1", [], "rev1")

    @pytest.mark.parametrize("error", [
        _http_error(503), TimeoutError("timed out"), ConnectionResetError(),
    ])
    def test_server_or_transit_failure_is_an_unknown_outcome(self, execute, error):
        from gdoc.api.docs import batch_update_pinned

        execute.side_effect = error
        with pytest.raises(GdocError, match="outcome is unknown"):
            batch_update_pinned("doc1", [], "rev1")

    def test_credentials_failure_before_sending_is_not_unknown(self, mocker):
        from gdoc.api.docs import batch_update_pinned
        from gdoc.util import AuthError

        mocker.patch("gdoc.api.docs.get_docs_service",
                     side_effect=AuthError("Authentication expired"))
        with pytest.raises(AuthError):
            batch_update_pinned("doc1", [], "rev1")

    def test_token_refresh_failure_changed_nothing(self, execute):
        from google.auth.exceptions import TransportError

        from gdoc.api.docs import batch_update_pinned

        execute.side_effect = TransportError("dns")
        with pytest.raises(GdocError, match="No change was made"):
            batch_update_pinned("doc1", [], "rev1")
