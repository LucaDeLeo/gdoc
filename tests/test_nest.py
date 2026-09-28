"""Tests for `gdoc nest` / `gdoc unnest` and the planner in gdoc.listnest."""

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
        {"glyphFormat": fmt.format(i=i), **{k: v[i % 3] for k, v in per_level.items()}}
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
        with pytest.raises(GdocError, match="no revision ID"):
            cmd_nest(_args())
        api.write.assert_not_called()

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
