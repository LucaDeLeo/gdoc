"""Known native structures that Markdown replacement cannot round-trip.

Scan only the caller's replacement scope. See README's hazard inventory;
ordinary styles and metadata are deliberately not a reason to refuse.
"""

import re
import sys

from gdoc.util import GdocError

# gdoc's own container ranges (see gdoc.api.docs._prefix_range_name).
_PREFIX_NAME = (r"gdoc:prefix:(?:v1:\d+:\d+|v2:\d+:\d+:\d+"
                r"|v3:(?:q|\d+)(?:\.(?:q|\d+))*)")

_ELEMENTS = {
    "person": "people chips",
    "richLink": "rich-link chips",
    "dateElement": "date chips",
    "inlineObjectElement": "inline images or embedded objects",
    "positionedObjectIds": "positioned objects",
    "footnoteReference": "footnotes",
    "equation": "equations",
    "autoText": "automatic text (such as page numbers)",
    "tableOfContents": "generated tables of contents",
    "pageBreak": "page breaks",
    "columnBreak": "column breaks",
}


# These fields contain ID/name -> object maps, not schema-keyed objects.
_MAP_FIELDS = {
    "headers", "footers", "footnotes", "inlineObjects", "positionedObjects",
    "lists", "namedRanges",
}


def _custom_section_layout(style: dict, document_style: dict) -> bool:
    """Recognize layout overrides without flagging the default section marker."""
    columns = style.get("columnProperties", [])
    if len(columns) > 1 or any(
        c.get("paddingEnd", {}).get("magnitude", 0) for c in columns
    ):
        return True
    for key, default in (
        ("columnSeparatorStyle", "NONE"),
        ("contentDirection", "LEFT_TO_RIGHT"),
        ("sectionType", "CONTINUOUS"),
    ):
        value = style.get(key, default)
        if value != default and not value.endswith("_UNSPECIFIED"):
            return True
    defaults = {"flipPageOrientation": False,
                "useFirstPageHeaderFooter": False, "pageNumberStart": 1}
    for key in (
        "marginTop", "marginBottom", "marginLeft", "marginRight",
        "marginHeader", "marginFooter", "flipPageOrientation",
        "useFirstPageHeaderFooter", "pageNumberStart",
    ):
        if key in style and style[key] != document_style.get(key, defaults.get(key)):
            return True
    return False


# Bounded, observed Drive-import defaults; this is not an unknown-field denylist.
_IMPORT_PAGE_SETUP = {
    "pageSize": {"height": {"magnitude": 792, "unit": "PT"},
                 "width": {"magnitude": 612, "unit": "PT"}},
    **{name: {"magnitude": 72, "unit": "PT"} for name in
       ("marginTop", "marginBottom", "marginLeft", "marginRight")},
    **{name: {"magnitude": 36, "unit": "PT"} for name in
       ("marginHeader", "marginFooter")},
    "documentFormat": {"documentMode": "PAGES"},
    "background": {"color": {}},
    "pageNumberStart": 1,
    "useCustomHeaderFooterMargins": False,
    "useFirstPageHeaderFooter": False,
    "flipPageOrientation": False,
}
_TEXT_STYLE_LOSSES = {
    "foregroundColor": "colour", "backgroundColor": "highlight",
    "underline": "underline", "smallCaps": "small caps", "fontSize": "font size",
    "weightedFontFamily": "font family", "baselineOffset": "baseline",
}
_PARAGRAPH_STYLE_LOSSES = {
    "alignment": "alignment", "lineSpacing": "line spacing",
    "spaceAbove": "paragraph spacing", "spaceBelow": "paragraph spacing",
    "keepWithNext": "paragraph layout", "keepLinesTogether": "paragraph layout",
    "avoidWidowAndOrphan": "paragraph layout", "pageBreakBefore": "paragraph layout",
    "indentStart": "indentation", "indentEnd": "indentation",
    "indentFirstLine": "indentation", "tabStops": "tab stops",
}


def _indented(paragraph: dict) -> bool:
    """Whether a paragraph has a start or first-line indent."""
    style = paragraph.get("paragraphStyle", {})
    return any((style.get(key) or {}).get("magnitude", 0) > 0
               for key in ("indentStart", "indentFirstLine"))


def _table_style_losses(table: dict) -> set[str]:
    """Visual table styling a pipe table cannot carry: warned, not refused.

    Docs' defaults (no shading, 1pt solid black borders, evenly distributed
    columns) are not reported.
    """
    losses = set()
    for row in table.get("tableRows", []):
        for cell in row.get("tableCells", []):
            style = cell.get("tableCellStyle", {})
            # An empty backgroundColor is no shading; any rgbColor, even an
            # empty one (black, its zero components omitted), is shading.
            if "rgbColor" in style.get("backgroundColor", {}).get("color", {}):
                losses.add("table cell shading")
            for side in ("borderLeft", "borderRight", "borderTop", "borderBottom"):
                border = style.get(side)
                # A width whose magnitude is omitted is 0pt (the API omits
                # zero values); only a missing width is the 1pt default.
                width = (border or {}).get("width")
                if border and (
                    (width is not None and width.get("magnitude", 0) != 1)
                    or border.get("dashStyle", "SOLID") != "SOLID"
                    or any(border.get("color", {}).get("color", {})
                           .get("rgbColor", {}).values())
                ):
                    losses.add("table borders")
    if any(column.get("widthType") == "FIXED_WIDTH"
           for column in table.get("tableStyle", {}).get("tableColumnProperties", [])):
        losses.add("table column widths")
    return losses


def _numbered_list_hazards(content: list, lists: dict) -> set[str]:
    """Find numbering boundaries and starts reconstruction cannot preserve."""
    from gdoc.api.docs import _indent_nesting_level, _list_is_ordered

    hazards = set()
    run = {}
    seen = set()
    for element in content:
        paragraph = element.get("paragraph", {})
        bullet = paragraph.get("bullet")
        if bullet is None:
            continue
        list_id = bullet.get("listId", "")
        native_level = bullet.get("nestingLevel", 0)
        definitions = lists.get(list_id, {}).get("listProperties", {}).get(
            "nestingLevels", [],
        )
        definition = (definitions[native_level]
                      if native_level < len(definitions) else {})
        indent = paragraph.get("paragraphStyle", {}).get(
            "indentStart", definition.get("indentStart", {}),
        )
        level = _indent_nesting_level(native_level, indent, definitions)
        for deeper in [key for key in run if key > level]:
            del run[deeper]
        if not _list_is_ordered(lists, list_id, native_level):
            continue
        start = definition.get("startNumber", 1)
        if start != 1:
            hazards.add(f"numbered list {list_id!r} starts at {start} (reset to 1)")
        previous = run.get(level)
        identity = (list_id, native_level)
        if identity in seen and previous != list_id:
            hazards.add(f"numbered list {list_id!r} resumes after a break")
        run[level] = list_id
        seen.add(identity)
    return hazards



def check_markdown_replacement(
    scope: dict, *, tab_body: bool = False, allow_lossy: bool = False,
) -> None:
    """Refuse known lossy structures in a body or a complete Docs response."""
    hazards, styles, numbering = markdown_hazards(scope, tab_body=tab_body)
    if hazards and not allow_lossy:
        where = "selected tab body" if tab_body else "whole document"
        raise GdocError(
            f"Markdown replacement refused: {where} contains "
            + ", ".join(sorted(hazards))
            + ". No content was written. Use targeted edits to preserve native "
            "content, or pass --allow-lossy to knowingly discard it. "
            "--force only bypasses conflicts; --force-collapse-tabs only "
            "allows tab collapse.",
            exit_code=3,
        )
    if numbering:
        print("WARN: Native numbering may reset on reconstruction: " +
              "; ".join(sorted(numbering)), file=sys.stderr)
    if hazards:
        print("WARN: Markdown replacement will discard: " +
              ", ".join(sorted(hazards)), file=sys.stderr)
    if styles:
        print("WARN: Markdown replacement may reset styles: " +
              ", ".join(sorted(styles)), file=sys.stderr)


def markdown_hazards(
    scope: dict, *, tab_body: bool = False,
) -> tuple[set[str], set[str], set[str]]:
    """Content, style and numbering losses of a Markdown round trip of *scope*.

    Content hazards are native content the Markdown cannot represent; reads
    report them as omissions and replacements require consent to lose them.
    """
    from gdoc.api.docs import _table_markdown

    hazards: set[str] = set()
    styles: set[str] = set()
    numbering: set[str] = set()

    def visit(value, table_depth=0, document_style=None, lists=None, images=None,
              prefixes=None, supported_prefix=False):
        """Collect known hazards recursively, tracking nested table depth."""
        if isinstance(value, list):
            for item in value:
                visit(item, table_depth, document_style, lists, images,
                      prefixes, supported_prefix)
        elif isinstance(value, dict):
            # Each documentTab owns its defaults; recursive calls keep them
            # local so a sibling or child tab cannot inherit the wrong style.
            document_style = value.get("documentStyle", document_style or {})
            lists = value.get("lists", {} if "body" in value else lists or {})
            images = value.get("inlineObjects", images or {})
            if "body" in value or isinstance(value.get("namedRanges"), dict):
                prefixes = [r for group in value.get("namedRanges", {}).values()
                            for named in group.get("namedRanges", [])
                            if re.fullmatch(_PREFIX_NAME,
                                            named.get("name", group.get("name", "")))
                            for r in named.get("ranges", [])]
            if "paragraph" in value:
                supported_prefix = any(
                    r.get("startIndex", 0) <= value.get("startIndex", -1)
                    < r.get("endIndex", 0) for r in prefixes or []
                )
            if "content" in value and isinstance(value["content"], list):
                numbering.update(_numbered_list_hazards(value["content"], lists))
            if "sectionBreak" in value:
                # Tab deletion starts at 1: the initial [0, 1) marker and
                # its section style survive, even in an otherwise blank tab.
                initial = value.get("startIndex", 0) == 0
                if tab_body and initial and value.get("endIndex", 1) <= 1:
                    return
                style = value["sectionBreak"].get("sectionStyle", {})
                if not initial or _custom_section_layout(
                    style, document_style,
                ):
                    hazards.add("section boundaries or custom section layout")
            if not tab_body:
                title = value.get("tabProperties", {}).get("title")
                if title and title != "Tab 1":
                    hazards.add(f"tab title {title!r} (import resets it to 'Tab 1')")
                page_style = value.get("documentStyle", {})
                if any(page_style.get(key, default) != default
                       for key, default in _IMPORT_PAGE_SETUP.items()):
                    hazards.add("page setup "
                                "(import resets page size, margins or page mode)")
            for field, label in _TEXT_STYLE_LOSSES.items():
                if field in value.get("textStyle", {}):
                    text_style = value["textStyle"]
                    if text_style[field] in (None, False, {}):
                        continue
                    if field in ("foregroundColor", "underline") and text_style.get(
                        "link",
                    ):
                        continue
                    if field == "weightedFontFamily" and value["textStyle"][field].get(
                        "fontFamily",
                    ) in ("Courier New", "Consolas", "monospace"):
                        continue
                    styles.add(label)
            paragraph_style = value.get("paragraphStyle", {})
            quote = all(paragraph_style.get(key) == {"magnitude": 36, "unit": "PT"}
                        for key in ("indentStart", "indentFirstLine"))
            for field, label in _PARAGRAPH_STYLE_LOSSES.items():
                if field not in paragraph_style:
                    continue
                setting = paragraph_style[field]
                if setting in (None, False, {}, []):
                    continue
                if field == "lineSpacing" and setting == 100:
                    continue
                if field in ("spaceAbove", "spaceBelow", "indentStart", "indentEnd",
                             "indentFirstLine") and setting.get("magnitude", 0) == 0:
                    continue
                if field in ("indentStart", "indentFirstLine") and (
                    quote or "bullet" in value or supported_prefix
                ):
                    continue
                if field == "alignment" and table_depth and paragraph_style[field] in (
                    "START", "CENTER", "END",
                ):
                    continue
                styles.add(label)
            if paragraph_style.get("shading", {}).get("backgroundColor", {}).get(
                "color",
            ):
                styles.add("paragraph shading")
            if any(paragraph_style.get(side, {}).get("width", {}).get("magnitude", 0)
                   > 0 for side in ("borderTop", "borderLeft", "borderRight",
                                    "borderBetween")):
                styles.add("paragraph borders")
            if paragraph_style.get("direction") == "RIGHT_TO_LEFT":
                styles.add("right-to-left paragraph direction")
            border = paragraph_style.get("borderBottom", {})
            if border.get("width", {}).get("magnitude", 0) > 0:
                text = "".join(e.get("textRun", {}).get("content", "")
                               for e in value.get("elements", []))
                if text.removesuffix("\n"):
                    hazards.add("border-bottom paragraphs (custom borders are lost)")
            if value.get("listProperties"):
                levels = value["listProperties"].get("nestingLevels", [])
                if any(level.get("glyphSymbol", "") not in ("", "●", "○", "■", "•")
                       or level.get("glyphType", "DECIMAL") not in (
                           "DECIMAL", "ALPHA", "ROMAN", "GLYPH_TYPE_UNSPECIFIED",
                       ) for level in levels):
                    styles.add("list glyphs and list styling")
            if "bullet" in value:
                # Inspect definitions only when content references the list.
                visit(lists.get(value["bullet"].get("listId"), {}),
                      table_depth, document_style, lists, images,
                      prefixes, supported_prefix)
            for key, child in value.items():
                # Defaults are metadata, not replaceable content. Page setup
                # is checked above; referenced list definitions are checked
                # at their paragraphs instead of scanning the whole registry.
                if key in ("namedStyles", "documentStyle", "lists",
                           "suggestedNamedStylesChanges",
                           "suggestedDocumentStyleChanges"):
                    continue
                # Empty objects are valid paragraph-element markers (equation,
                # pageBreak, etc.); empty reference lists are not hazards.
                if key in _ELEMENTS and child is not None and child != []:
                    supported_image = False
                    if key == "inlineObjectElement":
                        embedded = images.get(child.get("inlineObjectId"), {}).get(
                            "inlineObjectProperties", {},
                        ).get("embeddedObject", {})
                        # A linked Sheets chart also carries imageProperties;
                        # rewriting it would insert only its rendered image.
                        linked = "linkedContentReference" in embedded
                        if linked:
                            hazards.add("linked charts (a rewrite keeps "
                                        "only a static image)")
                        supported_image = linked or (
                            "imageProperties" in embedded
                            and "embeddedDrawingProperties" not in embedded)
                    if not supported_image:
                        hazards.add(_ELEMENTS[key])
                if key.startswith("suggested") and child:
                    hazards.add("pending suggestions")
                if key == "namedRanges" and isinstance(child, dict):
                    for group in child.values():
                        for named in group.get("namedRanges", []):
                            name = named.get("name", group.get("name", ""))
                            if (named.get("ranges") and name != "gdoc:code:v1"
                                    and not re.fullmatch(_PREFIX_NAME, name)):

                                hazards.add("custom named ranges")
                if key == "link" and isinstance(child, dict) and any(
                    k in child for k in (
                        "bookmarkId", "headingId", "tabId",
                        "bookmark", "heading",
                    )
                ):
                    hazards.add("internal document links")
                if key == "table":
                    if table_depth:
                        hazards.add("nested tables")
                    index = value.get("startIndex", "unknown")
                    if _table_markdown(child) is None:
                        hazards.add(
                            f"table at index {index} (cannot export as a pipe table)"
                        )
                    # The exporter renders cell runs without paragraph bullets;
                    # pipe-table reconstruction parses only inline formatting.
                    if any(
                        "bullet" in block.get("paragraph", {})
                        for row in child.get("tableRows", [])
                        for cell in row.get("tableCells", [])
                        for block in cell.get("content", [])
                    ):
                        hazards.add(
                            f"table at index {index} "
                            "(list paragraphs become plain text)"
                        )
                    if any(
                        block.get("paragraph", {}).get("paragraphStyle", {}).get(
                            "namedStyleType", "NORMAL_TEXT",
                        ) != "NORMAL_TEXT"
                        for row in child.get("tableRows", [])
                        for cell in row.get("tableCells", [])
                        for block in cell.get("content", [])
                    ):
                        hazards.add(
                            f"table at index {index} "
                            "(named paragraph styles become plain text)"
                        )
                    cell_paragraphs = [
                        block["paragraph"]
                        for row in child.get("tableRows", [])
                        for cell in row.get("tableCells", [])
                        for block in cell.get("content", [])
                        if "paragraph" in block
                    ]
                    # A pipe-table cell holds inline Markdown only: a native
                    # rule or an indented (quote-like) paragraph in a cell
                    # has no spelling there.
                    if any("horizontalRule" in element
                           for paragraph in cell_paragraphs
                           for element in paragraph.get("elements", [])):
                        hazards.add(
                            f"table at index {index} (rules inside cells are dropped)"
                        )
                    if any(_indented(paragraph) for paragraph in cell_paragraphs):
                        hazards.add(
                            f"table at index {index} "
                            "(indented cell paragraphs become plain text)"
                        )
                    styles.update(_table_style_losses(child))
                    # Markdown aligns a whole column like its header cell.
                    rows = [row.get("tableCells", [])
                            for row in child.get("tableRows", [])]

                    def alignments(cell):
                        return [block["paragraph"].get("paragraphStyle", {}).get(
                            "alignment", "START") for block in cell.get("content", [])
                            if "paragraph" in block]

                    headers = [(alignments(cell) or ["START"])[0]
                               for cell in (rows[0] if rows else [])]
                    if any(
                        alignment != headers[column]
                        for row in rows
                        for column, cell in enumerate(row[:len(headers)])
                        for alignment in alignments(cell)
                    ):
                        hazards.add(
                            f"table at index {index} "
                            "(cell alignment differs from its column header)"
                        )
                if key in ("rowSpan", "columnSpan") and child > 1:
                    hazards.add("merged table cells")
                if not tab_body and key in ("headers", "footers", "footnotes"):
                    if child:
                        hazards.add(key)
                if isinstance(child, dict) and (
                    key in _MAP_FIELDS or key.startswith("suggested")
                ):
                    # Map entry names are arbitrary, including "person" or
                    # "rowSpan"; only their values have Docs schema fields.
                    visit(list(child.values()), table_depth,
                          document_style, lists, images, prefixes, supported_prefix)
                else:
                    visit(child, table_depth + (key == "table"),
                          document_style, lists, images, prefixes, supported_prefix)

    visit(scope)
    return hazards, styles, numbering
