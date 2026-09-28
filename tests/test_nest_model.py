"""Apply `gdoc nest`/`unnest` batches to an offline model of Google Docs.

tests/test_nest.py checks the requests the planner writes; this file checks
what those requests *do*. `Doc` models the behaviour measured live for the
requests the planner sends:

- insertText "\\n" at a paragraph start adds an empty paragraph that copies
  the paragraph's bullet and indent; other text is inserted in place.
- deleteParagraphBullets removes the bullet and leaves the level's standard
  indent behind as an explicit indent.
- updateParagraphStyle sets (or, with no value, clears) the indent.
- createParagraphBullets bullets every unbulleted paragraph in the range
  and consumes its leading tabs. The level is relative:
      base + (leading tabs - fewest leading tabs in the range)
           + leftover indent levels
  where base is the level of the paragraph just before the range when it
  is a bullet of the same kind (the new bullets join its list), else 0 in
  a new list. Already-bulleted paragraphs are skipped.
- deleteContentRange of the empty temporary paragraph removes it.

The expected result is computed independently: the chosen items and their
descendants change level by the requested amount, and nothing else changes.
"""

import contextlib
import copy
import io
import itertools
import random
from unittest import mock

import pytest

from gdoc.listnest import locate_item, plan_nesting, verify
from gdoc.util import GdocError

TAB = "t.0"
KIND = {"NUMBERED_DECIMAL_ALPHA_ROMAN": "num", "BULLET_DISC_CIRCLE_SQUARE": "bul"}


def _levels(fmt, **per_level):
    return {"listProperties": {"nestingLevels": [
        {"glyphFormat": fmt.format(i=i),
         "indentStart": {"magnitude": 36 * (i + 1), "unit": "PT"},
         "indentFirstLine": {"magnitude": 36 * (i + 1) - 18, "unit": "PT"},
         **{k: v[i % 3] for k, v in per_level.items()}}
        for i in range(9)
    ]}}


DEFS = {
    "num": _levels("%{i}.", glyphType=("DECIMAL", "ALPHA", "ROMAN")),
    "bul": _levels("%{i}", glyphSymbol=("●", "○", "■")),
}


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


class Doc:
    """One tab: paragraphs {text, bullet: (list_id, level) | None, indent}."""

    def __init__(self, paras, kinds):
        self.paras = copy.deepcopy(paras)
        self.kinds = dict(kinds)  # list_id -> "num" | "bul"
        self.new_ids = itertools.count(1)
        self.rev = 1

    def spans(self):
        idx, out = 1, []
        for p in self.paras:
            n = _utf16(p["text"])
            out.append((idx, idx + n))
            idx += n
        return out

    def find(self, index):
        for i, (s, e) in enumerate(self.spans()):
            if s <= index < e:
                return i, index - s
        raise AssertionError(f"index {index} is outside the body")

    def overlapping(self, s, e):
        return [i for i, (ps, pe) in enumerate(self.spans()) if ps < e and pe > s]

    def apply(self, requests):
        handlers = {
            "insertText": self.insert_text,
            "deleteParagraphBullets": self.delete_paragraph_bullets,
            "updateParagraphStyle": self.update_paragraph_style,
            "createParagraphBullets": self.create_paragraph_bullets,
            "deleteContentRange": self.delete_content_range,
        }
        for request in requests:
            (name, body), = request.items()
            handlers[name](body)
        self.rev += 1

    def insert_text(self, b):
        i, off = self.find(b["location"]["index"])
        p, text = self.paras[i], b["text"]
        if text == "\n":
            assert off == 0, "the model splits only at a paragraph start"
            new = copy.deepcopy(p)
            new["text"] = "\n"
            self.paras.insert(i, new)
        else:
            assert "\n" not in text
            p["text"] = p["text"][:off] + text + p["text"][off:]

    def delete_paragraph_bullets(self, b):
        for i in self.overlapping(b["range"]["startIndex"], b["range"]["endIndex"]):
            p = self.paras[i]
            if p["bullet"]:
                p["indent"] = 36.0 * (p["bullet"][1] + 1)
                p["bullet"] = None

    def update_paragraph_style(self, b):
        assert set(b["fields"].split(",")) == {"indentStart", "indentFirstLine"}
        value = b["paragraphStyle"].get("indentStart")
        for i in self.overlapping(b["range"]["startIndex"], b["range"]["endIndex"]):
            self.paras[i]["indent"] = None if value is None else value["magnitude"]

    def create_paragraph_bullets(self, b):
        idx = self.overlapping(b["range"]["startIndex"], b["range"]["endIndex"])
        todo = [i for i in idx if not self.paras[i]["bullet"]]
        if not todo:
            return
        kind = KIND[b["bulletPreset"]]
        prev = self.paras[idx[0] - 1] if idx[0] > 0 else None
        if prev and prev["bullet"] and self.kinds[prev["bullet"][0]] == kind:
            list_id, base = prev["bullet"]
        else:
            list_id, base = f"new{next(self.new_ids)}", 0
            self.kinds[list_id] = kind

        def tabs(p):
            return len(p["text"]) - len(p["text"].lstrip("\t"))

        fewest = min(tabs(self.paras[i]) for i in todo)
        for i in todo:
            p = self.paras[i]
            extra = round((p["indent"] or 0) / 36.0)
            p["bullet"] = (list_id, base + tabs(p) - fewest + extra)
            p["text"] = p["text"].lstrip("\t")
            p["indent"] = None

    def delete_content_range(self, b):
        s, e = b["range"]["startIndex"], b["range"]["endIndex"]
        i, off = self.find(s)
        assert e - s == 1 and off == 0 and self.paras[i]["text"] == "\n"
        del self.paras[i]

    def document_tab(self):
        content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
        for p, (s, e) in zip(self.paras, self.spans()):
            style = {}
            if p["indent"] is not None:
                style["indentStart"] = {"magnitude": p["indent"], "unit": "PT"}
                style["indentFirstLine"] = {
                    "magnitude": max(p["indent"] - 18, 0), "unit": "PT",
                }
            para = {"elements": [{"startIndex": s, "endIndex": e, "textRun": {
                "content": p["text"], "textStyle": {}}}], "paragraphStyle": style}
            if p["bullet"]:
                para["bullet"] = {"listId": p["bullet"][0],
                                  "nestingLevel": p["bullet"][1],
                                  "textStyle": {"underline": False}}
            content.append({"startIndex": s, "endIndex": e, "paragraph": para})
        lists = {lid: copy.deepcopy(DEFS[k]) for lid, k in self.kinds.items()}
        return {"body": {"content": content}, "lists": lists}

    def document(self):
        return {"revisionId": f"r{self.rev}", "tabs": [{
            "tabProperties": {"tabId": TAB, "title": "Main"},
            "documentTab": self.document_tab()}]}

    def state(self):
        return [(p["text"], p["bullet"]) for p in self.paras]


def para(text, level=None, lid="L"):
    return {"text": text + "\n", "indent": None,
            "bullet": (lid, level) if level is not None else None}


def expected(paras, first, last, delta):
    """Independent expectation: items first..last and their descendants
    (by list position, skipping blank lines) move by delta."""
    want = [(p["text"], p["bullet"]) for p in paras]
    lid = paras[first]["bullet"][0]
    items = [i for i in range(first, last + 1)
             if paras[i]["bullet"] and paras[i]["bullet"][0] == lid]
    shallowest = min(paras[i]["bullet"][1] for i in items)
    j = last + 1
    while j < len(paras):
        p = paras[j]
        if p["bullet"] is None and p["text"] == "\n":
            j += 1
        elif p["bullet"] and p["bullet"][1] > shallowest:
            items.append(j)
            j += 1
        else:
            break
    for i in items:
        list_id, level = paras[i]["bullet"]
        want[i] = (paras[i]["text"], (list_id, level + delta))
    return want


def run(paras, kinds, text, delta, to=None, mutate=None):
    """Plan with the real planner, apply to the model: (doc, plan)."""
    doc = Doc(paras, kinds)
    tab = doc.document_tab()
    first = locate_item(tab["body"], text)
    last = locate_item(tab["body"], to) if to else first
    plan = plan_nesting(tab, TAB, first, last, delta)
    requests = copy.deepcopy(plan.requests)
    if mutate:
        requests = mutate(requests)
    doc.apply(requests)
    return doc, plan


def _index(paras, text):
    hits = [i for i, p in enumerate(paras) if text.lower() in p["text"].lower()]
    assert len(hits) == 1, text
    return hits[0]


def check(paras, kinds, text, delta, to=None, mutate=None):
    """True when the model's result equals the independent expectation."""
    doc, plan = run(paras, kinds, text, delta, to=to, mutate=mutate)
    # Content index = paragraph index + 1 (the section break comes first).
    want = expected(paras, _index(paras, text), _index(paras, to or text), delta)
    return doc.state() == want and verify(doc.document_tab(), plan) == (True, [])


NUM, BUL = {"L": "num"}, {"L": "bul"}

SCENARIOS = [
    ("numbered nest", [para("Intro"), para("Alpha", 0), para("Bravo", 0),
                       para("Charlie", 0), para("Outro")], NUM, "Bravo", 1, None),
    ("numbered unnest", [para("Intro"), para("Alpha", 0), para("Bravo", 1),
                         para("Charlie", 0)], NUM, "Bravo", -1, None),
    ("bullet nest", [para("Alpha", 0), para("Bravo", 0), para("Charlie", 0)],
     BUL, "Bravo", 1, None),
    ("list at the end of the tab", [para("Intro"), para("Alpha", 0),
                                    para("Bravo", 0)], NUM, "Bravo", 1, None),
    ("nest after a nested sibling", [para("Alpha", 0), para("Kilo", 1),
                                     para("Bravo", 0)], NUM, "Bravo", 1, None),
    ("unnest below a deeper sibling",
     [para("Alpha", 0), para("Kilo", 1), para("Lima", 2), para("Bravo", 1),
      para("Charlie", 0)], NUM, "Bravo", -1, None),
    ("subtree moves with its item",
     [para("Alpha", 0), para("Bravo", 0), para("Kilo", 1), para("Lima", 2),
      para("Charlie", 0)], NUM, "Bravo", 1, None),
    ("range", [para("Alpha", 0), para("Bravo", 0), para("Charlie", 0),
               para("Kilo", 1), para("Delta", 0)], NUM, "Bravo", 1, "Charlie"),
    ("levels 2", [para("Alpha", 0), para("Kilo", 1), para("Lima", 2),
                  para("Bravo", 0)], NUM, "Bravo", 2, None),
    ("unnest by 2", [para("Alpha", 0), para("Kilo", 1), para("Lima", 2),
                     para("Bravo", 0)], NUM, "Lima", -2, None),
    ("loose list nest", [para("Alpha", 0), para(""), para("Bravo", 0), para(""),
                         para("Charlie", 0)], NUM, "Bravo", 1, None),
    ("loose list unnest", [para("Alpha", 0), para(""), para("Bravo", 1), para(""),
                           para("Charlie", 0)], NUM, "Bravo", -1, None),
    ("list continued after prose",
     [para("Alpha", 0), para("prose"), para("Bravo", 0), para("Kilo", 1),
      para("Lima", 1)], NUM, "Lima", -1, None),
    ("numbered sub-list under bullets",
     [para("Papa", 0, "B"), para("Kilo", 1, "N"), para("Lima", 1, "N"),
      para("Quebec", 0, "B")], {"B": "bul", "N": "num"}, "Lima", 1, None),
    ("adjacent list below is untouched",
     [para("Xray", 0, "X"), para("Yankee", 0, "X"), para("Zulu", 0, "Y"),
      para("Mike", 0, "Y")], {"X": "num", "Y": "num"}, "Mike", 1, None),
    ("emoji before the item", [para("\U0001f600 Alpha", 0),
                               para("Bravo \U0001f600", 0)], NUM, "Bravo", 1, None),
]


@pytest.mark.parametrize(
    "paras,kinds,text,delta,to", [s[1:] for s in SCENARIOS],
    ids=[s[0] for s in SCENARIOS],
)
def test_batch_produces_the_intended_levels(paras, kinds, text, delta, to):
    assert check(paras, kinds, text, delta, to)


def test_list_ids_are_kept():
    paras = [para("Alpha", 0), para("Bravo", 0), para("Charlie", 0)]
    doc, _ = run(paras, NUM, "Bravo", 1)
    assert {p["bullet"][0] for p in doc.paras if p["bullet"]} == {"L"}


def test_blank_lines_keep_no_bullet_and_their_indent():
    paras = [para("Alpha", 0), para(""), para("Bravo", 0)]
    doc, _ = run(paras, NUM, "Bravo", 1)
    blank = doc.paras[1]
    assert blank["bullet"] is None and blank["indent"] is None


# -- randomized ------------------------------------------------------------


def random_list(rng):
    kind = rng.choice(["num", "bul"])
    paras = [para("Intro")] if rng.random() < 0.7 else []
    level, loose = None, rng.random() < 0.3
    n = rng.randint(2, 7)
    for k in range(n):
        level = 0 if level is None else rng.randint(0, min(level + 1, 4))
        paras.append(para(f"item{k}", level))
        if loose and k < n - 1 and rng.random() < 0.5:
            paras.append(para(""))
    if rng.random() < 0.4:
        paras.append(para("Outro"))
    return paras, {"L": kind}


def fuzz(seeds, mutate=None):
    """(applied, mismatches) over *seeds* random documents and moves."""
    applied = mismatches = 0
    for seed in seeds:
        rng = random.Random(seed)
        paras, kinds = random_list(rng)
        names = [p["text"].strip() for p in paras if p["bullet"]]  # item0..item6
        text = rng.choice(names)
        to = rng.choice(names) if rng.random() < 0.3 else None
        delta = rng.choice([1, 1, 2]) * rng.choice([1, -1])
        try:
            ok = check(paras, kinds, text, delta, to, mutate=mutate)
        except GdocError as e:
            assert e.exit_code == 3  # a refusal, before any request
            continue
        applied += 1
        mismatches += not ok
    return applied, mismatches


def test_random_moves_produce_the_intended_levels():
    applied, mismatches = fuzz(range(600))
    assert applied > 100
    assert mismatches == 0


def _drop_indent_reset(requests):
    return [r for r in requests if not (
        "updateParagraphStyle" in r
        and r["updateParagraphStyle"]["paragraphStyle"].get(
            "indentStart", {}).get("magnitude") == 0
    )]


def _one_extra_tab(requests):
    out = copy.deepcopy(requests)
    for r in out:
        text = r.get("insertText", {}).get("text", "")
        if text and set(text) == {"\t"}:
            r["insertText"]["text"] = text + "\t"
    return out


@pytest.mark.parametrize("mutate", [_drop_indent_reset, _one_extra_tab])
def test_a_broken_recipe_is_caught(mutate):
    """The model is sensitive: a deliberately broken recipe fails it."""
    _, mismatches = fuzz(range(600), mutate=mutate)
    assert mismatches > 0


# -- through the command ---------------------------------------------------


def _run_cli(doc, argv):
    from gdoc.cli import run_argv

    def get(doc_id):
        return copy.deepcopy(doc.document())

    def write(doc_id, requests, revision):
        assert revision == f"r{doc.rev}"
        doc.apply(copy.deepcopy(requests))

    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        for target, kwargs in [
            ("gdoc.api.docs.get_document_with_tabs", {"side_effect": get}),
            ("gdoc.api.docs.batch_update_pinned", {"side_effect": write}),
            ("gdoc.api.drive.get_file_version", {"return_value": {"version": 2}}),
            ("gdoc.notify.pre_flight", {"return_value": None}),
            ("gdoc.state.update_state_after_command", {}),
            ("gdoc.util.resolve_account", {"return_value": "someone@example.com"}),
        ]:
            stack.enter_context(mock.patch(target, **kwargs))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        code = run_argv(argv, check_updates=False)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("command,text,delta", [
    ("nest", "Kilo", 1), ("unnest", "Lima", -1), ("nest", "Charlie", 1),
])
def test_command_end_to_end_verifies_its_own_result(command, text, delta):
    paras = [para("Intro"), para("Alpha", 0), para("Bravo", 0), para("Kilo", 0),
             para("Lima", 1), para(""), para("Charlie", 0)]
    doc = Doc(paras, NUM)
    code, out, err = _run_cli(doc, [command, "doc1", text, "--quiet"])
    assert (code, err) == (0, "")
    assert out.startswith(f"OK {command}ed ")
    i = _index(paras, text)
    assert doc.state() == expected(paras, i, i, delta)
