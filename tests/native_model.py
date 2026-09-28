"""A small native Docs model that applies batchUpdate requests offline.

It models the Docs behaviors gdoc's writers depend on, one UTF-16 unit per
list entry, and fails loudly on requests Docs rejects:

- A paragraph's style and list membership belong to its paragraph mark (the
  newline). Inserting a newline splits a paragraph and gives both halves its
  style and list membership (observed live for named styles, indents,
  borders, alignment, spacing and bullets at every split position).
- A deletion that removes one or more marks merges what remains into one
  paragraph, which keeps the style and list membership of the paragraph
  where the deletion starts, unless the deletion starts at that paragraph's
  first index (so none of it survives); then the paragraph where the
  deletion ends keeps its own. The surviving newline keeps its own text
  style either way. (Live probes P1a-P1o, J1-J10, X5, X7.)
- createParagraphBullets sets levels relative to the range, not from
  absolute tab counts. A paragraph's effective indent is its leading tabs
  plus its indentStart in 36pt steps (the indent a removed bullet leaves
  behind); its level is the range's base plus its effective indent minus the
  smallest one in the range. The range joins the list of the paragraph just
  before it when that paragraph has the same preset, and the base is then
  that paragraph's level; otherwise it starts a new list at base 0. Bulleted
  paragraphs get the list level's indents, and deleteParagraphBullets keeps
  them. A range over a paragraph that already has a bullet converts or joins
  that list (live), which gdoc never intends, so the model refuses it.
- insertTable inserts a newline, which splits the paragraph at the location,
  then the table. The newline directly before a table and the segment's
  final newline cannot be deleted, unless the same deletion removes the
  table too.
- Inserted text, including insertTable's newline, takes the text style of
  the character before it, or of the character after it at a paragraph
  start. A newline inserted directly before a mark also restyles that mark's
  text to match (the empty paragraph a split at the end leaves).
- A named range grows only when text is inserted strictly inside it. A
  deletion clips it; a deletion that leaves it empty removes it.

It is not a full Docs emulator: it exists so tests can check the document a
request sequence produces instead of only the requests' shape.
"""

import copy
import json
import re
from unittest.mock import Mock


class Unit:
    __slots__ = ("ch", "ts", "ps", "bullet", "kind", "cont", "suggested")

    def __init__(self, ch, ts=None, ps=None, bullet=None, kind="text", cont=False):
        self.ch, self.kind, self.cont = ch, kind, cont
        self.ts, self.ps = dict(ts or {}), dict(ps or {})
        self.bullet = bullet
        self.suggested = None  # a suggestion ID tests set on a character


STRUCTURE = ("tstart", "row", "cell", "tend")


class NativeDoc:
    def __init__(self, *blocks):
        """Build a body from ``("p", text, style, bullet)`` / ``("t", rows)``.

        A body starting or ending with a table gets the empty paragraph Docs
        keeps there.
        """
        self.named = []  # [name, start, end]; name None once deleted
        self.lists = 0
        self.list_offsets = {}  # list -> extra level indent (see bullets)
        self.images = 0
        blocks = list(blocks) or [("p", "")]
        if blocks[0][0] == "t":
            blocks.insert(0, ("p", ""))
        if blocks[-1][0] == "t":
            blocks.append(("p", ""))
        self.units = [Unit("", kind="sb")]
        for block in blocks:
            if block[0] == "p":
                _, text, *rest = block
                style = rest[0] if rest else "NORMAL_TEXT"
                bullet = rest[1] if len(rest) > 1 else None
                self.units.extend(self._units(text, {}, None, None))
                self.units.append(Unit("\n", ps={"namedStyleType": style},
                                       bullet=copy.deepcopy(bullet)))
                if bullet:
                    self.lists = max(self.lists, bullet["list"])
            else:
                self.units.append(Unit("", kind="tstart"))
                for row in block[1]:
                    self.units.append(Unit("", kind="row"))
                    for cell in row:
                        self.units.append(Unit("", kind="cell"))
                        self.units.extend(self._units(cell, {}, None, None))
                        self.units.append(Unit("\n"))
                self.units.append(Unit("", kind="tend"))

    # -- helpers -------------------------------------------------------
    def _paragraph_end(self, index):
        while self.units[index].ch != "\n" or self.units[index].kind != "text":
            if self.units[index].kind in STRUCTURE:
                break
            index += 1
        return index

    def _check(self, index, what):
        assert 1 <= index <= len(self.units), f"{what}: index {index} out of range"
        assert not (index < len(self.units) and self.units[index].cont), (
            f"{what}: index {index} splits a surrogate pair")

    def paragraphs(self):
        start, found = 1, []
        for i in range(1, len(self.units)):
            unit = self.units[i]
            if unit.kind in STRUCTURE:
                start = i + 1
            elif unit.ch == "\n":
                found.append((start, i))
                start = i + 1
        return found

    def _paragraphs_in(self, start, end):
        return [(a, b) for a, b in self.paragraphs()
                if b >= start and a < max(end, start + 1)]

    def _units(self, text, ts, ps, bullet):
        out = []
        for ch in text:
            newline = ch == "\n"
            out.append(Unit(ch, ts, ps if newline else None,
                            copy.deepcopy(bullet) if newline else None))
            if ord(ch) > 0xFFFF:
                out.append(Unit("", ts, cont=True))
        return out

    def _shift_named(self, index, count):
        for named in self.named:
            if named[1] >= index:
                named[1] += count
            if named[2] > index:
                named[2] += count

    # -- requests ------------------------------------------------------
    def apply(self, request):
        (kind, value), = request.items()
        getattr(self, "op_" + re.sub(r"([A-Z])", r"_\1", kind).lower())(value)

    def op_insert_text(self, v):
        index, text = v["location"]["index"], v["text"]
        self._check(index, "insertText")
        assert index < len(self.units), "insertText after the final newline"
        assert self.units[index].kind not in ("tstart", "row", "cell"), (
            f"insertText at structural index {index}")
        before = self.units[index - 1]
        text_before = before.kind == "text" and before.ch != "\n"
        ts = before.ts if text_before else self.units[index].ts
        end = self._paragraph_end(index)
        new = self._units(text, ts, self.units[end].ps, self.units[end].bullet)
        if end == index and text.endswith("\n") and text_before:
            self.units[end].ts = dict(ts)
        self.units[index:index] = new
        self._shift_named(index, len(new))

    def op_delete_content_range(self, v):
        start, end = v["range"]["startIndex"], v["range"]["endIndex"]
        self._check(start, "delete start")
        self._check(end, "delete end")
        assert end < len(self.units), "delete includes the final newline"
        assert end > start, "empty delete"
        if end < len(self.units) and self.units[end].kind == "tstart":
            assert self.units[end - 1].ch != "\n", (
                "delete includes the newline before a table")
        first = self._paragraph_end(start)
        inherited = (self.units[first].ps, self.units[first].bullet)
        para_start = max(a for a, _ in self.paragraphs() if a <= start)
        del self.units[start:end]
        if first < end and start != para_start and self.units[first].kind == "text":
            mark = self.units[self._paragraph_end(start)]
            mark.ps, mark.bullet = dict(inherited[0]), copy.deepcopy(inherited[1])
        for named in self.named:
            if named[0] is None:
                continue
            for k in (1, 2):
                if named[k] >= end:
                    named[k] -= end - start
                elif named[k] > start:
                    named[k] = start
            if named[1] >= named[2]:
                named[0] = None

    def op_update_paragraph_style(self, v):
        start, end = v["range"]["startIndex"], v["range"]["endIndex"]
        assert end > start, "empty paragraph style range"
        fields = v["fields"]
        for _, mark in self._paragraphs_in(start, end):
            if fields == "*":
                self.units[mark].ps = dict(v["paragraphStyle"])
                continue
            for field in fields.split(","):
                if field in v["paragraphStyle"]:
                    self.units[mark].ps[field] = v["paragraphStyle"][field]
                else:
                    self.units[mark].ps.pop(field, None)

    def op_update_text_style(self, v):
        start, end = v["range"]["startIndex"], v["range"]["endIndex"]
        assert end > start, "empty text style range"
        fields = v["fields"]
        for unit in self.units[start:end]:
            if fields == "*":
                unit.ts = dict(v["textStyle"])
                continue
            for field in filter(None, fields.split(",")):
                if field in v["textStyle"]:
                    unit.ts[field] = v["textStyle"][field]
                else:
                    unit.ts.pop(field, None)

    def op_create_paragraph_bullets(self, v):
        start, end = v["range"]["startIndex"], v["range"]["endIndex"]
        preset = v["bulletPreset"]
        paragraphs = self._paragraphs_in(start, end)
        assert paragraphs, "createParagraphBullets without paragraphs"
        assert not any(self.units[mark].bullet for _, mark in paragraphs), (
            "createParagraphBullets over a paragraph that already has a bullet")
        first = paragraphs[0][0]
        prior = self.units[first - 1].bullet if first > 1 else None
        effective = []
        for a, mark in paragraphs:
            tabs = 0
            while self.units[a + tabs].ch == "\t":
                tabs += 1
            indent = self.units[mark].ps.get("indentStart", {}).get("magnitude", 0)
            effective.append((tabs, tabs + round(indent / 36)))
        least = min(e for _, e in effective)
        if prior and prior["preset"] == preset:
            list_id, base = prior["list"], prior["nest"]
        else:
            # A new list's level indents start at the range's least indent,
            # so nesting simulated by tabs alone shows no native level (live).
            self.lists += 1
            list_id, base = self.lists, 0
            self.list_offsets[list_id] = 36 * least
        offset = self.list_offsets.get(list_id, 0)
        for (a, mark), (tabs, e) in reversed(list(zip(paragraphs, effective))):
            nest = min(8, base + e - least)
            self.units[mark].bullet = {"preset": preset, "list": list_id, "nest": nest}
            self.units[mark].ps.update({
                "indentStart": {"magnitude": 36 * (nest + 1) + offset, "unit": "PT"},
                "indentFirstLine": {"magnitude": 36 * (nest + 1) + offset - 18,
                                    "unit": "PT"}})
            if tabs:
                self.op_delete_content_range(
                    {"range": {"startIndex": a, "endIndex": a + tabs}})

    def op_delete_paragraph_bullets(self, v):
        span = v["range"]
        for _, mark in self._paragraphs_in(span["startIndex"], span["endIndex"]):
            self.units[mark].bullet = None

    def op_insert_inline_image(self, v):
        index = v["location"]["index"]
        self._check(index, "image")
        self.images += 1
        self.units[index:index] = [Unit(f"[IMG:{v['uri']}]", self.units[index - 1].ts)]
        self._shift_named(index, 1)

    def op_create_named_range(self, v):
        span = v["range"]
        self._check(span["startIndex"], "named range")
        self._check(span["endIndex"], "named range end")
        self.named.append([v["name"], span["startIndex"], span["endIndex"]])

    def op_delete_named_range(self, v):
        self.named[int(v["namedRangeId"][2:])][0] = None

    def op_insert_table(self, v):
        index = v["location"]["index"]
        self._check(index, "insertTable")
        end = self._paragraph_end(index)
        before = self.units[index - 1]
        ts = (before.ts if before.kind == "text" and before.ch != "\n"
              else self.units[index].ts)
        new = [Unit("\n", ts=ts, ps=self.units[end].ps,
                    bullet=copy.deepcopy(self.units[end].bullet))]
        new.append(Unit("", kind="tstart"))
        for _ in range(v["rows"]):
            new.append(Unit("", kind="row"))
            for _ in range(v["columns"]):
                new.append(Unit("", kind="cell"))
                new.append(Unit("\n"))
        new.append(Unit("", kind="tend"))
        self.units[index:index] = new
        self._shift_named(index, len(new))

    # -- reads ---------------------------------------------------------
    def _paragraph(self, index, objects, lists):
        start, runs = index, []
        while True:
            unit = self.units[index]
            if unit.cont:
                runs[-1][2] = index + 1
                index += 1
                continue
            if unit.ch.startswith("[IMG:"):
                runs.append(["IMG", index, index + 1, unit])
                index += 1
                continue
            key = json.dumps([unit.ts, unit.suggested], sort_keys=True)
            if runs and runs[-1][0] == "T" and runs[-1][4] == key:
                runs[-1][3].append(unit.ch)
                runs[-1][2] = index + 1
            else:
                runs.append(["T", index, index + 1, [unit.ch], key, unit.ts,
                             unit.suggested])
            index += 1
            if unit.ch == "\n":
                break
        elements = []
        for run in runs:
            if run[0] == "IMG":
                object_id = f"obj{run[1]}"
                objects[object_id] = run[3].ch[5:-1]
                elements.append({"startIndex": run[1], "endIndex": run[2],
                                 "inlineObjectElement": {"inlineObjectId": object_id,
                                                         "textStyle": run[3].ts}})
            else:
                text_run = {"content": "".join(run[3]), "textStyle": run[5]}
                if run[6]:
                    text_run["suggestedInsertionIds"] = [run[6]]
                elements.append({"startIndex": run[1], "endIndex": run[2],
                                 "textRun": text_run})
        mark = self.units[index - 1]
        paragraph = {"elements": elements, "paragraphStyle": dict(mark.ps)}
        if mark.bullet:
            list_id = f"L{mark.bullet['list']}"
            paragraph["bullet"] = {"listId": list_id,
                                   "nestingLevel": mark.bullet["nest"]}
            lists[list_id] = mark.bullet["preset"]
        return {"startIndex": start, "endIndex": index, "paragraph": paragraph}, index

    def document_tab(self, tab_id="t.0"):
        objects, list_presets = {}, {}
        content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]
        index, count = 1, len(self.units)
        while index < count:
            if self.units[index].kind == "tstart":
                table_start, index, rows = index, index + 1, []
                while self.units[index].kind == "row":
                    row_start, index, cells = index, index + 1, []
                    while index < count and self.units[index].kind == "cell":
                        cell_start, index, paragraphs = index, index + 1, []
                        while index < count and self.units[index].kind == "text":
                            paragraph, index = self._paragraph(
                                index, objects, list_presets)
                            paragraphs.append(paragraph)
                        cells.append({"startIndex": cell_start, "endIndex": index,
                                      "content": paragraphs})
                    rows.append({"startIndex": row_start, "endIndex": index,
                                 "tableCells": cells})
                index += 1
                content.append({"startIndex": table_start, "endIndex": index, "table": {
                    "rows": len(rows), "columns": len(rows[0]["tableCells"]),
                    "tableRows": rows}})
            else:
                paragraph, index = self._paragraph(index, objects, list_presets)
                content.append(paragraph)
        lists = {}
        for list_id, preset in list_presets.items():
            # gdoc's two presets; any other (made in the Docs UI) differs.
            glyph = ({"glyphType": "DECIMAL"}
                     if preset == "NUMBERED_DECIMAL_ALPHA_ROMAN"
                     else {"glyphType": "UPPER_ROMAN"} if preset.startswith("NUMBERED")
                     else {"glyphSymbol": "●"} if preset == "BULLET_DISC_CIRCLE_SQUARE"
                     else {"glyphSymbol": "❖"})
            offset = self.list_offsets.get(int(list_id[1:]), 0)
            lists[list_id] = {"listProperties": {"nestingLevels": [{
                **glyph,
                "indentStart": {"magnitude": 36 * (k + 1) + offset, "unit": "PT"},
                "indentFirstLine": {"magnitude": 36 * (k + 1) + offset - 18,
                                    "unit": "PT"},
            } for k in range(9)]}}
        named = {}
        for k, (name, start, end) in enumerate(self.named):
            if name is not None:
                group = named.setdefault(name, {"name": name, "namedRanges": []})
                group["namedRanges"].append({
                    "namedRangeId": f"nr{k}", "name": name,
                    "ranges": [{"startIndex": start, "endIndex": end,
                                "tabId": tab_id}]})
        inline = {oid: {"objectId": oid, "inlineObjectProperties": {"embeddedObject": {
            "imageProperties": {"contentUri": uri},
            "size": {"width": {"magnitude": 10, "unit": "PT"},
                     "height": {"magnitude": 10, "unit": "PT"}}}}}
            for oid, uri in objects.items()}
        return {"body": {"content": content}, "lists": lists, "namedRanges": named,
                "inlineObjects": inline}


class NativeService:
    """A Docs service over one NativeDoc tab, pinned by revision."""

    def __init__(self, doc, tab_id="t.0", title="Main", extra_tabs=()):
        self.doc, self.tab_id, self.title = doc, tab_id, title
        self.revision, self.batches = 1, []
        self.extra_tabs = list(extra_tabs)

    def snapshot(self):
        properties = {"tabId": self.tab_id, "title": self.title, "index": 0}
        tabs = [{"tabProperties": properties,
                 "documentTab": self.doc.document_tab(self.tab_id)}]
        return {"documentId": "synthetic", "title": "Synthetic",
                "revisionId": f"r{self.revision}",
                "tabs": tabs + copy.deepcopy(self.extra_tabs)}

    def documents(self):
        return self

    def get(self, **kwargs):
        snapshot = self.snapshot()
        fields = kwargs.get("fields")
        if fields == "documentId,title,revisionId":
            snapshot = {k: snapshot[k] for k in fields.split(",")}
        return Mock(execute=lambda **_: snapshot)

    def batchUpdate(self, documentId, body):  # noqa: N802, N803 (Docs API names)
        service = self

        class Request:
            def execute(self, **_):
                required = body.get("writeControl", {}).get("requiredRevisionId")
                assert required == f"r{service.revision}", "stale or missing revision"
                service.batches.append(body["requests"])
                replies = []
                for request in body["requests"]:
                    service.doc.apply(request)
                    if "insertInlineImage" in request:
                        replies.append({"insertInlineImage": {
                            "objectId": f"img{service.doc.images}"}})
                    else:
                        replies.append({})
                service.revision += 1
                return {"replies": replies,
                        "writeControl": {"requiredRevisionId": f"r{service.revision}"}}

        return Request()


def styles(doc):
    """Paragraph texts with their named style and list identity, in order."""
    out = []
    for start, mark in doc.paragraphs():
        unit = doc.units[mark]
        text = "".join(u.ch for u in doc.units[start:mark] if not u.cont)
        bullet = unit.bullet and (unit.bullet["list"], unit.bullet["nest"])
        out.append((text, unit.ps.get("namedStyleType"), bullet))
    return out
