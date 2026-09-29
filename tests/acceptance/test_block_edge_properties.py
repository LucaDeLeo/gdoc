"""Invariants I1-I6 of targeted block edits, over generated tabs.

A seeded generator builds tabs of 3-8 blocks: plain paragraphs, headings,
blank paragraphs, rules, bullet and numbered items at levels 0-1, quote
lines, code blocks (some ending in an empty line), list-item content and
tables. Every property runs through CLI and MCP on the native model, which
applies the Docs rules observed live (tests/native_model.py).

- I1-I4 (``edit OLD ""`` over every kind of run of whole paragraphs): the
  tab afterwards is the original minus exactly those paragraphs. Every other
  paragraph keeps its text, style, list identity and level, indent, rule and
  gdoc container ranges, including one whose mark a removal borrowed. The
  only refusals are the documented ones, and they send nothing.
- I2: marking any character the removal deletes as suggested refuses it
  with nothing sent; marking the characters just outside does not.
- I4/I6 (``insert`` at the start and end): the result equals writing the
  concatenated Markdown, natively and on read. Numbered lists keep their
  identity exactly; bullet lists, which show no numbers, may split.
- I5: a written tab reads back stable, and a changed rewrite reads back
  exactly.
"""

import random

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.acceptance.test_round8_insert import _native
from tests.native_model import NativeDoc

SEEDS = range(24)
RUNS_PER_TAB = 4


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _markdown(rng, low=3, high=8):
    """A tab's Markdown; every block with text carries a unique token."""
    counter = iter(range(1, 1000))
    lines, previous = [], None
    for _ in range(rng.randint(low, high)):
        kind = rng.choice(["plain", "heading", "blank", "rule", "bullet",
                           "numbered", "quote", "code", "content", "table"])
        k = next(counter)
        nested = ("  " if previous in ("bullet", "numbered") and rng.random() < .4
                  else "")
        block = {
            "plain": [f"P{k} words"],
            "heading": [f"## H{k}"],
            "blank": [""],
            "rule": ["---"],
            "bullet": [f"{nested}- B{k}"],
            "numbered": [f"{nested}1. N{k}"],
            "quote": [f"> Q{k}"],
            "code": ["```", f"C{k}", *([""] if rng.random() < .5 else []), "```"],
            "content": [f"- I{k}", "", f"  K{k}"],
            "table": ["", f"| A{k} |", "| --- |", f"| V{k} |", ""],
        }[kind]
        lines += block
        previous = kind
    return "\n".join(lines) + "\n"


def _written(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    return doc


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _body(doc):
    """Top-level body elements: (kind, text, start, end, paragraph)."""
    elements = []
    for element in doc.document_tab()["body"]["content"]:
        if "paragraph" in element:
            paragraph = element["paragraph"]
            text = "".join(run.get("textRun", {}).get("content", "")
                           for run in paragraph["elements"]).removesuffix("\n")
            elements.append(("p", text, element["startIndex"],
                             element["endIndex"], paragraph))
        elif "table" in element:
            elements.append(("t", "", element["startIndex"], element["endIndex"],
                             None))
    return elements


def _shape(doc):
    """Each top-level element's text, paragraph style, list shape (lists
    numbered by first appearance), indent, rule and gdoc containers."""
    lists, rows = {}, []
    for kind, text, start, _, paragraph in _body(doc):
        containers = frozenset(name for name, a, b in doc.named
                               if name and a <= start < b)
        if kind == "t":
            rows.append(("table", containers))
            continue
        style = paragraph.get("paragraphStyle", {})
        bullet = paragraph.get("bullet")
        rows.append((
            text, style.get("namedStyleType"),
            bullet and (lists.setdefault(bullet["listId"], len(lists)),
                        bullet.get("nestingLevel", 0)),
            style.get("indentStart", {}).get("magnitude", 0),
            "borderBottom" in style, containers,
        ))
    return rows


def _canonical(rows):
    """Renumber list identities by first appearance after rows are removed."""
    lists, result = {}, []
    for row in rows:
        if row[0] != "table" and row[2]:
            row = (*row[:2], (lists.setdefault(row[2][0], len(lists)), row[2][1]),
                   *row[3:])
        result.append(row)
    return result


def _runs(rng, body):
    """Runs of whole top-level paragraphs an exact match can select."""
    everything = "\n".join(text for kind, text, *_ in body)
    runs = []
    for i in range(len(body)):
        for j in range(i, len(body)):
            if body[j][0] != "p":
                break
            if not body[i][1] or not body[j][1]:
                continue
            old = "\n".join(text for _, text, *_ in body[i:j + 1])
            if everything.count(old) == 1:
                runs.append((i, j, old))
    return rng.sample(runs, min(RUNS_PER_TAB, len(runs)))


def _borrows(body, j):
    return j == len(body) - 1 or body[j + 1][0] == "t"


def _refusal_expected(body, i, j, doc=None):
    """The documented refusals: a removal that must borrow a mark from a
    table or nothing, or from an empty list item of another list state; and
    one that deletes list items whose content (item content paragraphs or
    deeper items) would then join the item above."""
    if doc is not None and _orphans_item_content(doc, body, i, j):
        return True
    if not _borrows(body, j):
        return False
    if i == 0 and j == len(body) - 1:
        return False
    if i == 0 or body[i - 1][0] == "t":
        return True
    kept, removed = body[i - 1][4], body[j][4]
    identity = [(p.get("bullet") or {}).get("listId") and (
        p["bullet"]["listId"], p["bullet"].get("nestingLevel", 0))
        for p in (kept, removed)]
    return not body[i - 1][1] and bool(kept.get("bullet")) and (
        identity[0] != identity[1])


def _orphans_item_content(doc, body, i, j):
    """Mirror of the product rule: after the removed run, past plain blanks,
    only the end, top-level text, or an item at the removed items' level or
    shallower in their container makes the removal safe."""
    from gdoc.api.docs import _parse_prefix_range_name

    items = [(p["bullet"].get("nestingLevel", 0), start)
             for kind, _, start, _, p in body[i:j + 1]
             if kind == "p" and p.get("bullet")]
    if not items:
        return False

    def covering(start):
        return [name for name, a, b in doc.named if name and a <= start < b]

    def containers(start):
        return frozenset(path for name in covering(start)
                         if (path := _parse_prefix_range_name(name)))

    level = min(item_level for item_level, _ in items)
    home = containers(min(start for _, start in items))
    for kind, text, start, _, paragraph in body[j + 1:]:
        if kind == "t":
            return True
        style = paragraph.get("paragraphStyle", {})
        bullet = paragraph.get("bullet")
        if (not text and not bullet and not covering(start)
                and style.get("namedStyleType", "NORMAL_TEXT") == "NORMAL_TEXT"
                and not (style.get("borderBottom") or {}).get("width", {}).get(
                    "magnitude")):
            continue
        if bullet:
            return not (bullet.get("nestingLevel", 0) <= level
                        and containers(start) == home)
        return bool(covering(start)) or not text
    return False


def _plan_span(body, i, j):
    """The characters the removal deletes (see _plan_paragraph_run)."""
    if i == 0 and j == len(body) - 1:
        return body[i][2], body[j][3] - 1  # wording only; one mark stays
    if _borrows(body, j):
        return body[i - 1][3] - 1, body[j][3] - 1
    return body[i][2], body[j][3]


@pytest.mark.parametrize("seed", SEEDS)
def test_i1_to_i4_removing_whole_paragraphs(route, seed):
    rng = random.Random(seed)
    markdown = _markdown(rng)
    doc = _written(route, markdown)
    body, shape = _body(doc), _shape(doc)
    for i, j, old in _runs(rng, body):
        doc = _written(route, markdown)
        # Judged on the document before the edit changes it.
        refusal = _refusal_expected(body, i, j, doc)
        batches = len(route.service.batches)
        code, output, error = route.call("edit", old_text=old, new_text="")
        context = (markdown, old, output + error)
        if refusal:
            assert code != 0 and len(route.service.batches) == batches, context
            assert "unexpected error" not in output + error, context
            continue
        assert code == 0, context
        if i == 0 and j == len(body) - 1:
            # The segment's only paragraphs: their wording goes, one mark stays.
            assert [row[0] for row in _shape(doc)] == [""], context
            continue
        assert _canonical(_shape(doc)) == _canonical(shape[:i] + shape[j + 1:]), context
        _read(route)


@pytest.mark.parametrize("seed", SEEDS[:12])
def test_i2_suggestions_on_deleted_characters_refuse(route, seed):
    rng = random.Random(seed)
    markdown = _markdown(rng)
    doc = _written(route, markdown)
    body = _body(doc)
    runs = [(i, j, old) for i, j, old in _runs(rng, body)
            if not _refusal_expected(body, i, j, doc)]
    checked = 0
    for i, j, old in runs[:2]:
        low, high = _plan_span(body, i, j)
        for index, refused in [(low, True), (high - 1, True),
                               ((low + high) // 2, True),
                               (low - 1, False), (high, False)]:
            doc = _written(route, markdown)
            unit = doc.units[index] if 0 < index < len(doc.units) else None
            if unit is None or unit.kind != "text" or (
                    not refused and body[0][2] <= index and any(
                        a <= index < b for _, _, a, b, _ in body[i:j + 1])):
                continue
            unit.suggested = "suggest.1"
            batches = len(route.service.batches)
            code, output, error = route.call("edit", old_text=old, new_text="")
            context = (markdown, old, index, output + error)
            if refused:
                assert code != 0 and "suggest" in output + error, context
                assert len(route.service.batches) == batches, context
                checked += 1
            else:
                assert code == 0, context
    assert checked or not runs


FRAGMENTS = ["1. n1\n2. n2\n", "  1. n\n", "- x\n  - y\n", "> q\n", "---\n",
             "```\ncode\n\n```\n", "| a |\n| --- |\n| b |\n", "text\n\n- z\n"]


@pytest.mark.parametrize("seed", SEEDS[:12])
@pytest.mark.parametrize("position", ["start", "end"])
def test_i4_i6_insert_matches_writing_the_concatenation(route, seed, position):
    rng = random.Random(seed)
    base = _markdown(rng, 2, 5)
    inserted = rng.choice(FRAGMENTS)
    _written(route, base)
    base_read = _read(route)
    concatenated = (base_read + inserted if position == "end"
                    else inserted + base_read)
    expected_doc = _written(route, concatenated)
    expected = (_read(route), _numbered_identity(_native(expected_doc)))
    doc = _written(route, base)
    batches = len(route.service.batches)
    code, output, error = route.call("insert", text=inserted, tab="t.0",
                                     position=position)
    if code != 0:
        # A refusal (items that may join a list above) sends nothing; the
        # write of the concatenation above is the working route.
        assert "list's structure" in output + error
        assert len(route.service.batches) == batches
        return
    assert (_read(route), _numbered_identity(_native(doc))) == expected, (
        base_read, inserted)


def _numbered_identity(native):
    """Compare list identity only where it shows: numbering. The parser
    continues a bullet list across other blocks, which an insert cannot
    join, and bullets read and display the same either way."""
    paragraphs, ranges = native
    return [(*row[:2], row[2] and (
        row[2][0] if row[2][2].startswith("NUMBERED") else None, *row[2][1:]),
        *row[3:]) for row in paragraphs], ranges


@pytest.mark.parametrize("seed", SEEDS)
def test_i5_written_tabs_read_back_stable(route, seed):
    rng = random.Random(seed)
    markdown = _markdown(rng)
    doc = _written(route, markdown)
    first = _read(route)
    shape = _shape(doc)
    batches = len(route.service.batches)
    route.ok("write", text=first)
    assert len(route.service.batches) == batches, first
    token = next(word for word in first.replace("\n", " ").split()
                 if word[:1] in "PHBNQCIKAV" and word[1:].isdigit())
    changed = first.replace(token, token + "x", 1)
    route.ok("write", text=changed)
    assert _read(route) == changed
    assert [row[1:] for row in _shape(doc)] == [row[1:] for row in shape]
