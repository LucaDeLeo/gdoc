"""Invented Markdown/native examples for the core representation contract."""

import time

import pytest

from gdoc.api.docs import _style_run_markdown, get_tab_text
from gdoc.mdparse import parse_inline, parse_markdown


def test_crlf_and_lone_cr_are_line_endings_in_code():
    assert parse_inline('`one\r\ntwo\rthree`')[0] == 'one two three'
    assert parse_markdown('```\r\n a\t \r\nb\r```\r\n').plain_text == ' a\t \nb\n'


def test_only_bare_long_enough_fence_closes_block():
    source = '```` python example\n```\n````not a closer\n x  \n`````\nnext\n'
    assert parse_markdown(source).plain_text == '```\n````not a closer\n x  \nnext\n'


def test_many_unfinished_links_are_bounded():
    source = '[word](' * 15000
    start = time.monotonic()
    assert parse_markdown(source).plain_text == source + '\n'
    assert time.monotonic() - start < 2


@pytest.mark.parametrize('text', ['a`b', '`', '  x  ', '\\*literal*', '   '])
def test_inline_code_export_preserves_literal_whitespace(text):
    style = {'weightedFontFamily': {'fontFamily': 'Courier New'}}
    rendered = _style_run_markdown(text, style)
    plain, styles = parse_inline(rendered)
    assert plain == text
    assert any(s.style == style and s.start == 0 and s.end == len(text)
               for s in styles)


def test_empty_mixed_list_items_survive_export():
    tab = {'body': {'content': [
        {'paragraph': {'bullet': {'listId': list_id, 'nestingLevel': level},
                       'elements': [{'textRun': {'content': '\n'}}]}}
        for list_id, level in [('bullet', 0), ('ordered', 1), ('bullet', 0)]
    ]}, 'lists': {'ordered': {'listProperties': {'nestingLevels': [
        {'glyphType': 'DECIMAL'}, {'glyphType': 'DECIMAL'},
    ]}}}}
    rendered = get_tab_text(tab, markdown=True)
    assert rendered == '- \n  1. \n- \n'
    parsed = parse_markdown(rendered)
    assert parsed.plain_text == '\n\t\n\n'
    assert len([s for s in parsed.styles if s.type == 'bullets']) == 3


def test_literal_image_opener_is_escaped_on_export():
    rendered = _style_run_markdown('![example](https://example.org/x)', {})
    assert parse_markdown(rendered).plain_text == '![example](https://example.org/x)\n'


def test_code_block_marker_preserves_blank_paragraphs_and_literal_syntax():
    code = '  *literal*\t \n\n``` inside\n'
    offset = 1
    paragraphs = []
    for line in code.splitlines(keepends=True):
        paragraphs.append({'startIndex': offset, 'endIndex': offset + len(line),
                           'paragraph': {'elements': [
                               {'textRun': {'content': line}},
                           ]}})
        offset += len(line)
    tab = {'body': {'content': paragraphs}, 'namedRanges': {
        'gdoc:code:v1': {'namedRanges': [{'name': 'gdoc:code:v1', 'ranges': [
            {'startIndex': 1, 'endIndex': offset},
        ]}]},
    }}
    rendered = get_tab_text(tab, markdown=True)
    assert rendered.startswith('````\n')
    parsed = parse_markdown(rendered)
    assert parsed.plain_text == code
    assert [(b.start, b.end) for b in parsed.code_blocks] == [(0, len(code))]


def test_empty_fenced_code_has_a_marked_native_paragraph():
    parsed = parse_markdown('```\n```\n')
    assert parsed.plain_text == '\n'
    assert [(block.start, block.end) for block in parsed.code_blocks] == [(0, 1)]


def test_heading_list_and_content_whitespace_survive():
    paragraph = {'bullet': {'listId': 'bullets'}, 'paragraphStyle': {
        'namedStyleType': 'HEADING_2',
    }, 'elements': [{'textRun': {'content': '  leading\t \n'}}]}
    rendered = get_tab_text({'body': {'content': [{'paragraph': paragraph}]}}, True)
    assert rendered == '- ##   leading\t \n'
    parsed = parse_markdown(rendered)
    assert parsed.plain_text == '  leading\t \n'
    assert parsed.styles[0].style == {'namedStyleType': 'HEADING_2'}


def test_table_alignment_and_quote_rule_recognition():
    parsed = parse_markdown('| A | B | C |\n| :--- | :---: | ---: |\n| x | y | z |\n')
    assert parsed.tables[0].alignments == ['START', 'CENTER', 'END']
    content = [
        {'paragraph': {'paragraphStyle': {
            'indentStart': {'magnitude': 36, 'unit': 'PT'},
            'indentFirstLine': {'magnitude': 36, 'unit': 'PT'},
        }, 'elements': [{'textRun': {'content': 'quotation\n'}}]}},
        {'paragraph': {'elements': [{'horizontalRule': {}},
                                    {'textRun': {'content': '\n'}}]}},
    ]
    assert get_tab_text({'body': {'content': content}}, True) == '> quotation\n---\n'


def test_image_offsets_after_nested_lists_and_surrounding_unicode():
    parsed = parse_markdown(
        '- root\n  - 😀 ![map](https://example.org/a_(b).png) done\n'
    )
    image = parsed.images[0]
    assert image.uri == 'https://example.org/a_(b).png'
    assert image.alt == 'map'
    assert image.removed_tabs_before == 1
    offset = image.plain_text_offset
    assert parsed.plain_text[offset - 2:offset] == '😀 '
    assert parsed.plain_text[image.plain_text_offset:] == '  done\n'


def test_existing_image_exports_snapshot_reference_and_alt_without_mutation():
    paragraph = {'elements': [
        {'textRun': {'content': 'Look '}},
        {'inlineObjectElement': {'inlineObjectId': 'synthetic-image'}},
        {'textRun': {'content': '\n'}},
    ]}
    tab = {'body': {'content': [{'paragraph': paragraph}]}, 'inlineObjects': {
        'synthetic-image': {'inlineObjectProperties': {'embeddedObject': {
            'description': 'A [map]', 'imageProperties': {
                'contentUri': 'https://example.org/temporary',
            },
        }}},
    }}
    rendered = get_tab_text(tab, True)
    assert 'temporary' not in rendered
    image = parse_markdown(rendered).images[0]
    assert (image.uri, image.alt) == ('gdoc-image:synthetic-image', 'A [map]')
    assert '_markdown_image_alt' not in paragraph['elements'][1]


def test_code_span_ending_backslash_does_not_hide_image():
    parsed = parse_markdown('`path\\` ![drawing](https://example.org/d.png)\n')
    assert parsed.plain_text == 'path\\  \n'
    assert parsed.images[0].alt == 'drawing'


def test_list_ids_starts_and_resumption_remain_distinct():
    def item(list_id, level=0):
        return {'paragraph': {'bullet': {'listId': list_id, 'nestingLevel': level},
                              'elements': [{'textRun': {'content': 'item\n'}}]}}
    content = [item('first'), item('second'),
               {'paragraph': {'elements': [{'textRun': {'content': 'pause\n'}}]}},
               item('first')]
    lists = {key: {'listProperties': {'nestingLevels': [{
        'glyphType': 'DECIMAL', 'startNumber': start,
    }]}} for key, start in [('first', 4), ('second', 1)]}
    assert get_tab_text({'body': {'content': content}, 'lists': lists}, True) == (
        '4. item\n1. item\npause\n5. item\n'
    )


def test_code_pipe_and_literal_entities_in_table_cells():
    from gdoc.api.docs import _table_markdown

    table = {'tableRows': [{'tableCells': [{'content': [{'paragraph': {'elements': [
        {'textRun': {'content': 'a|b', 'textStyle': {
            'weightedFontFamily': {'fontFamily': 'Courier New'},
        }}}, {'textRun': {'content': ' &#32;\n'}},
    ]}}]}]}]}
    rendered = _table_markdown(table)
    cell = parse_markdown(rendered).tables[0].rows[0][0]
    assert parse_inline(cell)[0] == 'a|b &#32;'


def test_supported_core_styles_and_images_need_no_loss_override(capsys):
    from gdoc.lossy import check_markdown_replacement

    scope = {'body': {'content': [{'paragraph': {
        'paragraphStyle': {'indentStart': {'magnitude': 36, 'unit': 'PT'},
                           'indentFirstLine': {'magnitude': 36, 'unit': 'PT'}},
        'elements': [{'textRun': {'content': 'literal\n', 'textStyle': {
            'weightedFontFamily': {'fontFamily': 'Courier New'},
        }}}, {'inlineObjectElement': {'inlineObjectId': 'picture'}}],
    }}]}, 'inlineObjects': {'picture': {'inlineObjectProperties': {
        'embeddedObject': {'imageProperties': {'contentUri': 'https://example.org/x'}},
    }}}, 'namedRanges': {'gdoc:code:v1': {'namedRanges': [{
        'name': 'gdoc:code:v1', 'ranges': [{'startIndex': 1, 'endIndex': 9}],
    }]}}}
    check_markdown_replacement(scope, tab_body=True)
    assert capsys.readouterr().err == ''
    scope['inlineObjects']['picture']['inlineObjectProperties']['embeddedObject'] = {}
    with pytest.raises(Exception, match='embedded objects'):
        check_markdown_replacement(scope, tab_body=True)


@pytest.mark.parametrize('literal', ['a**b', 'a~~b', '[x](y)', '![x](y)', '`x`'])
@pytest.mark.parametrize('style', [
    {'bold': True}, {'italic': True}, {'bold': True, 'italic': True},
    {'strikethrough': True}, {'link': {'url': 'https://example.org/a_(b)'}},
])
def test_code_punctuation_cannot_close_surrounding_formatting(literal, style):
    style = {**style, 'weightedFontFamily': {'fontFamily': 'Courier New'}}
    rendered = _style_run_markdown(literal, style)
    plain, ranges = parse_inline(rendered)
    assert plain == literal
    actual = [{} for _ in plain]
    for span in ranges:
        for offset in range(span.start, span.end):
            actual[offset].update(span.style)
    assert actual == [style] * len(literal)


def test_code_styled_terminal_newline_does_not_create_literal_delimiters():
    code = {'weightedFontFamily': {'fontFamily': 'Courier New'}}
    assert _style_run_markdown('\n', code) == '\n'


def test_html_break_spelling_inside_table_code_is_literal():
    parsed = parse_markdown('| `a<br>b` |\n| --- |\n| first<br>second |\n')
    assert parse_inline(parsed.tables[0].rows[0][0])[0] == 'a<br>b'
    assert parse_inline(parsed.tables[0].rows[1][0])[0] == 'first\nsecond'


@pytest.mark.parametrize('literal', ['+ item', '+', '1.', '42.', '1. ', '---'])
def test_new_empty_list_grammar_never_consumes_exported_literal_text(literal):
    tab = {'body': {'content': [{'paragraph': {'elements': [
        {'textRun': {'content': literal + '\n'}},
    ]}}]}}
    parsed = parse_markdown(get_tab_text(tab, True))
    assert parsed.plain_text == literal + '\n'
    assert not [style for style in parsed.styles if style.type == 'bullets']


def test_reference_images_in_table_cells_are_self_contained_for_native_writer():
    from gdoc.api.docs import _table_cell_requests

    parsed = parse_markdown(
        '| Picture | Literal |\n| --- | --- |\n'
        '| ![a [map]][drawing] | `![x][drawing]` |\n'
        '[drawing]: https://example.org/map_(1).png\n'
    )
    table = parsed.tables[0]
    assert table.rows[1][0] == r'![a [map]](https://example.org/map_\(1\).png)'
    assert table.rows[1][1] == '`![x][drawing]`'
    requests = _table_cell_requests([[5, 7], [11, 13]], table, 'tab-one')
    images = [r['insertInlineImage'] for r in requests if 'insertInlineImage' in r]
    assert len(images) == 1
    assert images[0]['uri'] == 'https://example.org/map_(1).png'


def test_image_reference_spelling_inside_link_url_stays_literal():
    parsed = parse_markdown(
        '| [site](https://example.org/![x][drawing]) |\n| --- |\n'
        '[drawing]: https://example.org/map.png\n'
    )
    cell = parsed.tables[0].rows[0][0]
    plain, styles = parse_inline(cell)
    assert plain == 'site'
    assert styles[0].style == {'link': {'url': 'https://example.org/![x][drawing]'}}


def test_mixed_nested_child_does_not_warn_about_parent_numbering(capsys):
    from gdoc.lossy import check_markdown_replacement

    def item(list_id, indent):
        return {'paragraph': {
            'bullet': {'listId': list_id},
            'paragraphStyle': {'indentStart': {'magnitude': indent, 'unit': 'PT'},
                               'indentFirstLine': {'magnitude': indent - 18,
                                                   'unit': 'PT'}},
            'elements': [{'textRun': {'content': 'item\n'}}],
        }}

    scope = {'body': {'content': [item('parent', 36), item('child', 72),
                                  item('parent', 36)]}, 'lists': {
        'parent': {'listProperties': {'nestingLevels': [{'glyphType': 'DECIMAL'}]}},
        'child': {'listProperties': {'nestingLevels': [{'glyphSymbol': '●'}]}},
    }}
    check_markdown_replacement(scope, tab_body=True)
    assert capsys.readouterr().err == ''
