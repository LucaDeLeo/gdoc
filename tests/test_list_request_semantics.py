"""Offline checks of the list compiler on the native model's list rules."""

import pytest

from gdoc.api.docs import (
    _code_range_requests,
    _native_docs_requests,
    _strip_trailing_newline_unless_hr,
)
from gdoc.lossy import _numbered_list_hazards
from gdoc.mdparse import parse_markdown, to_docs_requests, utf16_len
from tests.native_model import NativeDoc


def apply_list_requests(requests):
    """Apply requests to the native model (observed Docs list rules).

    Requests before the first insert act on a previous body; the model starts
    from the empty tab they leave.
    """
    doc = NativeDoc()
    first = next(i for i, r in enumerate(requests) if "insertText" in r)
    for request in requests[first:]:
        doc.apply(request)
    paragraphs = []
    for start, mark in doc.paragraphs():
        unit = doc.units[mark]
        text = "".join(u.ch for u in doc.units[start:mark + 1] if not u.cont)
        bullet = unit.bullet or {}
        paragraphs.append({"text": text, "list": bullet.get("list"),
                           "depth": bullet.get("nest", 0),
                           "preset": bullet.get("preset")})
    return paragraphs


def labels(paragraphs):
    counters = {}
    result = []
    for paragraph in paragraphs:
        if paragraph['list'] is not None:
            key = paragraph['list'], paragraph['depth']
            counters[key] = counters.get(key, 0) + 1
            result.append(counters[key])
    return result


@pytest.mark.parametrize('builder', [to_docs_requests, _native_docs_requests])
@pytest.mark.parametrize('gap', ['', '\n', '\n\n'])
def test_adjacent_and_separated_restarts_remain_independent(builder, gap):
    parsed = parse_markdown('1. A😀\n2. B\n' + gap + '1. C\n2. D')
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(builder(parsed, 1, 't'))
    assert labels(paragraphs) == [1, 2, 1, 2]
    assert not parsed.non_default_list_starts


@pytest.mark.parametrize('builder', [to_docs_requests, _native_docs_requests])
@pytest.mark.parametrize('gap', ['\n', '\n\n'])
def test_continuation_spans_then_unbullets_blank_paragraphs(builder, gap):
    parsed = parse_markdown('1. A\n2. B\n' + gap + '3. C\n4. D')
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(builder(parsed, 1, 't'))
    assert labels(paragraphs) == [1, 2, 3, 4]
    assert all(p['list'] is None for p in paragraphs if p['text'] == '\n')
    assert not parsed.non_default_list_starts


@pytest.mark.parametrize('builder', [to_docs_requests, _native_docs_requests])
@pytest.mark.parametrize('indent', ['', '  '])
@pytest.mark.parametrize('tabs', ['\t', '\t\t'])
def test_literal_tabs_survive_without_becoming_nesting(builder, indent, tabs):
    parsed = parse_markdown('- Parent😀\n' + indent + '- ' + tabs + '**Cargo**\n'
                            '\n1. Later\n2. Last')
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(builder(parsed, 1, 't'))
    assert ''.join(p['text'] for p in paragraphs) == (
        'Parent😀\n' + tabs + 'Cargo\n\nLater\nLast\n')
    assert paragraphs[1]['depth'] == bool(indent)
    assert parsed.removed_tabs == bool(indent)


def test_literal_tabs_keep_following_image_table_and_code_coordinates():
    parsed = parse_markdown('- Parent😀\n  - \tCargo\n\n'
                            '![](https://example.org/image.png)\n\n'
                            '```\ncode\n```\n\n| A |\n| --- |\n| B |')
    requests = _native_docs_requests(parsed, 1, 't')
    image_at = next(r['insertInlineImage']['location']['index'] for r in requests
                    if 'insertInlineImage' in r)
    image_phase = next(i for i, r in enumerate(requests) if 'insertInlineImage' in r)
    # Stop before the image placeholder is deleted.
    paragraphs = apply_list_requests(requests[:image_phase - 1])
    text = ''.join(p['text'] for p in paragraphs)
    assert image_at == 1 + utf16_len(text[:text.index(' \n')])
    table = parsed.tables[0]
    assert table.removed_tabs_before == 1
    table_at = text.index('code\n') + len('code\n\n')
    assert (1 + utf16_len(parsed.plain_text[:table.plain_text_offset])
            - table.removed_tabs_before) == 1 + utf16_len(text[:table_at])
    code = _code_range_requests(parsed, 1, 't')[0]['createNamedRange']['range']
    assert code['startIndex'] == 1 + utf16_len(text[:text.index('code')])
    assert code['endIndex'] == code['startIndex'] + len('code\n')


def test_continuation_and_restarts_stop_warning():
    def paragraph(identity=None, text='item\n'):
        result = {'elements': [{'textRun': {'content': text}}]}
        if identity:
            result['bullet'] = {'listId': identity}
        return {'paragraph': result}

    lists = {key: {'listProperties': {'nestingLevels': [{'glyphType': 'DECIMAL'}]}}
             for key in ['a', 'b']}
    assert not _numbered_list_hazards([paragraph('a'), paragraph('b')], lists)
    assert not _numbered_list_hazards(
        [paragraph('a'), paragraph(text='\n'), paragraph('a')], lists)
    assert not _numbered_list_hazards(
        [paragraph('a'), paragraph(text='Prose\n'), paragraph('a')], lists)
    assert _numbered_list_hazards(
        [paragraph('a'), paragraph('b'), paragraph('a')], lists)


@pytest.mark.parametrize('builder', [to_docs_requests, _native_docs_requests])
def test_final_empty_item_includes_retained_paragraph_mark(builder):
    parsed = parse_markdown('1. First\n2. ')
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(builder(parsed, 1, 't'))
    assert labels(paragraphs) == [1, 2]
    assert paragraphs[-1]['text'] == '\n'


def test_mixed_child_restores_literal_tab_after_both_bullet_operations():
    parsed = parse_markdown('1. Parent\n  - \tChild\n2. Sibling')
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(_native_docs_requests(parsed, 1, 't'))
    assert paragraphs[1]['text'] == '\tChild\n'
    assert paragraphs[1]['depth'] == 1
    assert paragraphs[0]['list'] == paragraphs[2]['list']


@pytest.mark.parametrize('builder', [to_docs_requests, _native_docs_requests])
@pytest.mark.parametrize('source,expected', [
    ('1. Parent\n  1. A\n  2. B\n  1. C\n  2. D\n2. End', [1, 1, 2, 1, 2, 2]),
    ('1. A\n- Bullet\n2. B\n- Other\n3. C', [1, 1, 2, 1, 3]),
    ('1. Parent\n  1. A\n  - Bullet\n  2. B\n2. End', [1, 1, 1, 2, 2]),
    ('1. A\nprose\n2. B', [1, 2]),
    ('1. Parent😀\n  1. \tA\n  1. \tB\n2. End', [1, 1, 1, 2]),
])
def test_nested_restarts_and_interleaved_continuity(builder, source, expected):
    parsed = parse_markdown(source)
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(builder(parsed, 1, 't'))
    assert labels(paragraphs) == expected
    expected_text = parsed.plain_text
    for item in reversed([s for s in parsed.styles if s.type == 'bullets']):
        expected_text = (expected_text[:item.start]
                         + expected_text[item.start + item.list_depth:])
    assert ''.join(p['text'] for p in paragraphs) == expected_text + '\n'
    assert not parsed.non_default_list_starts


def test_isolated_child_restart_retains_parent_and_continuing_child_ids():
    source = ('1. Parent😀\n  1. First\n  - Bullet\n  2. Second\n'
              '  1. Restart\n  2. Continued\n2. Parent end')
    parsed = parse_markdown(source)
    _strip_trailing_newline_unless_hr(parsed)
    paragraphs = apply_list_requests(_native_docs_requests(parsed, 1, 't'))
    assert labels(paragraphs) == [1, 1, 1, 2, 1, 2, 2]
    assert paragraphs[0]['list'] == paragraphs[6]['list']
    assert paragraphs[1]['list'] == paragraphs[3]['list']
    assert paragraphs[4]['list'] == paragraphs[5]['list']
    assert len({paragraphs[i]['list'] for i in [0, 1, 2, 4]}) == 4
    assert [p['depth'] for p in paragraphs] == [0, 1, 1, 1, 1, 1, 0]


def test_native_nested_restart_has_no_loss_warning():
    lists = {key: {'listProperties': {'nestingLevels': [
        {'glyphType': 'DECIMAL'}, {'glyphType': 'ALPHA'}
    ]}} for key in ['parent', 'child-a', 'child-b']}
    content = [{'paragraph': {
        'elements': [{'textRun': {'content': 'Item\n'}}],
        'bullet': {'listId': identity, 'nestingLevel': depth},
    }} for identity, depth in [('parent', 0), ('child-a', 1), ('child-b', 1),
                              ('parent', 0)]]
    assert not _numbered_list_hazards(content, lists)


def test_separated_numbering_does_not_clear_unrelated_quote_indentation():
    from gdoc.mdparse import list_requests, parse_markdown

    parsed = parse_markdown("> Protected quote\n\n1. Parent\n  1. Child\n"
                            "  1. Restart\n2. Parent next\n")
    requests = list_requests(parsed, 1)
    quote_end = len("Protected quote\n") + 1
    assert not any(
        request.get("deleteParagraphBullets", {}).get("range", {}).get(
            "startIndex", quote_end,
        ) < quote_end for request in requests
    )


def test_quote_spanned_by_numbered_continuation_restores_its_style():
    from gdoc.mdparse import list_requests, parse_markdown

    parsed = parse_markdown("1. First\n> Quoted interruption\n2. Second\n")
    requests = list_requests(parsed, 1)
    assert any(
        request.get("updateParagraphStyle", {}).get("paragraphStyle", {}).get(
            "indentFirstLine",
        ) == {"magnitude": 36, "unit": "PT"} for request in requests
    )
