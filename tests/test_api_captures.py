"""Offline semantic checks against frozen, anonymous Docs API shapes."""

import json
from pathlib import Path

import pytest

from gdoc.api import docs
from gdoc.cli import _resolve_insert_index, _try_anchored_comment

CAPTURES = Path(__file__).parent / 'fixtures' / 'docs-api'
PROCEDURE = 'tests/fixtures/docs-api/README.md#supersession'

# This is a closed vocabulary for these small v1 fixtures, not the Docs schema.
# Map entries use '*' for anonymous identities; lists contain one item schema.
V1 = {
    'Envelope': {'fixture_schema': int, 'fixture_version': int,
                 'capture': 'Capture', 'document': 'Document'},
    'Capture': {'kind': str, 'api': str, 'api_shape': str, 'method': str,
                'includeTabsContent': bool, 'constructed_on': str, 'provenance': str},
    'Document': {'body': 'Body', 'inlineObjects': 'InlineMap',
                 'positionedObjects': 'PositionedMap', 'footnotes': 'FootnoteMap',
                 'tabs': ['Tab']},
    'Body': {'content': ['StructuralElement']},
    'StructuralElement': {'startIndex': int, 'endIndex': int,
                          'sectionBreak': 'SectionBreak', 'paragraph': 'Paragraph'},
    'SectionBreak': {'sectionStyle': 'Empty'},
    'Paragraph': {'elements': ['Element'], 'paragraphStyle': 'ParagraphStyle',
                  'positionedObjectIds': [str]},
    'ParagraphStyle': {'namedStyleType': str},
    'Element': {'startIndex': int, 'endIndex': int, 'textRun': 'TextRun',
                'inlineObjectElement': 'InlineRef', 'footnoteReference': 'FootnoteRef'},
    'TextRun': {'content': str, 'textStyle': 'Empty'},
    'InlineRef': {'inlineObjectId': str, 'textStyle': 'Empty'},
    'FootnoteRef': {'footnoteId': str, 'footnoteNumber': str},
    'FootnoteMap': {'*': 'Footnote'},
    'Footnote': {'footnoteId': str, 'content': ['StructuralElement']},
    'InlineMap': {'*': 'InlineObject'},
    'InlineObject': {'objectId': str, 'inlineObjectProperties': 'ObjectProperties'},
    'PositionedMap': {'*': 'PositionedObject'},
    'PositionedObject': {'objectId': str,
                         'positionedObjectProperties': 'ObjectProperties'},
    'ObjectProperties': {'embeddedObject': 'Embedded', 'positioning': 'Positioning'},
    'Embedded': {'size': 'Size', 'imageProperties': 'Empty',
                 'embeddedDrawingProperties': 'Empty'},
    'Size': {'width': 'Dimension', 'height': 'Dimension'},
    'Dimension': {'magnitude': (int, float), 'unit': str},
    'Positioning': {'layout': str, 'leftOffset': 'Dimension', 'topOffset': 'Dimension'},
    'Tab': {'tabProperties': 'TabProperties', 'documentTab': 'Document',
            'childTabs': ['Tab']},
    'TabProperties': {'tabId': str, 'title': str, 'index': int},
    'Empty': {},
}


def _unsupported(path, reason):
    raise ValueError(f'{path}: unsupported capture shape ({reason}); see {PROCEDURE}')


def _validate(value, schema, path):
    kind = schema if isinstance(schema, str) else None
    if isinstance(schema, str):
        schema = V1[schema]
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            _unsupported(path, 'expected object')
        if kind in ('Envelope', 'Capture') and schema.keys() - value.keys():
            _unsupported(path, f'missing fields {sorted(schema.keys() - value.keys())}')
        variants = {
            'Document': {'body', 'tabs'},
            'StructuralElement': {'paragraph', 'sectionBreak'},
            'Element': {'textRun', 'inlineObjectElement', 'footnoteReference'},
        }.get(kind)
        if variants and len(variants & value.keys()) != 1:
            _unsupported(path, f'expected exactly one of {sorted(variants)}')
        for key, child in value.items():
            if key not in schema and '*' not in schema:
                _unsupported(f'{path}.{key}', 'unknown field')
            _validate(child, schema.get(key, schema.get('*')), f'{path}.{key}')
    elif isinstance(schema, list):
        if not isinstance(value, list):
            _unsupported(path, 'expected array')
        for index, child in enumerate(value):
            _validate(child, schema[0], f'{path}[{index}]')
    elif type(value) not in (schema if isinstance(schema, tuple) else (schema,)):
        _unsupported(path, 'unexpected scalar type')


def load_capture(name):
    """Validate v1 and pass API structure through unchanged (no normalization)."""
    path = CAPTURES / name
    capture = json.loads(path.read_text())
    _validate(capture, 'Envelope', name)
    if capture.get('fixture_schema') != 1 or capture.get('fixture_version') != 1:
        _unsupported(name, 'expected fixture_schema=1 and fixture_version=1')
    metadata = capture['capture']
    expected_shape = ('document-tabs-v1' if metadata['includeTabsContent']
                      else 'document-body-v1')
    if (metadata['api'] != 'docs/v1' or metadata['api_shape'] != expected_shape
            or metadata['method'] != 'documents.get'):
        _unsupported(name, f'expected docs/v1 {expected_shape}')
    document = capture['document']
    return document


@pytest.fixture(autouse=True)
def no_network(monkeypatch, mocker):
    def forbidden(*args, **kwargs):
        pytest.fail('API capture tests must remain offline')

    monkeypatch.setattr('socket.socket.connect', forbidden)
    mocker.patch.object(docs, 'get_docs_service', side_effect=forbidden)


@pytest.mark.parametrize(('name', 'left', 'right_start'), [
    ('inline-positioned-v1.json', 'Left😀', 8),
    ('footnote-v1.json', 'Left', 6),
])
def test_native_element_is_a_range_boundary(name, left, right_start):
    document = load_capture(name)
    before = json.loads(json.dumps(document))
    assert docs.find_text_in_document(document, left + 'Right') == []
    assert docs.find_text_in_document(document, left) == [
        {'startIndex': 1, 'endIndex': right_start - 1}]
    assert docs.find_text_in_document(document, 'Right') == [
        {'startIndex': right_start, 'endIndex': right_start + 5}]
    assert document == before  # Helpers must not mutate native structures.


@pytest.mark.parametrize(('name', 'end'), [
    ('inline-positioned-v1.json', 13), ('footnote-v1.json', 11),
])
@pytest.mark.parametrize('fold_quotes', [False, True])
def test_non_destructive_anchors_can_span_native_elements(mocker, name, end,
                                                        fold_quotes):
    document = load_capture(name)
    if fold_quotes:
        # An in-memory variant exercises both callers' normalization fallback.
        run = document['body']['content'][1]['paragraph']['elements'][0]['textRun']
        run['content'] = run['content'].replace('Left', 'L’ft')
    quote = docs.get_tab_text(document).rstrip('\n').replace('’', "'")
    assert docs.find_text_in_document(document, quote, normalize=True) == []
    assert _resolve_insert_index(document['body'], None, quote) == end
    mocker.patch.object(docs, 'get_document_with_tabs', return_value=document)
    insert = mocker.patch.object(docs, 'insert_comment', return_value='comment-1')
    document['revisionId'] = 'capture-revision'
    result = _try_anchored_comment('unused', 'Note.', quote)
    assert result.status == 'anchored'
    assert result.comment_id == 'comment-1'
    insert.assert_called_once_with(
        'unused', 'Note.', 1, end, tab_id=None, revision_id='capture-revision',
    )


def test_inline_identity_and_positioned_drawing(mocker):
    document = load_capture('inline-positioned-v1.json')
    assert docs._collect_object_refs(document['body']) == [
        ('inline-1', 7, 'inline'), ('positioned-1', 1, 'positioned')]
    mocker.patch.object(docs, 'get_document_with_tabs', return_value=document)
    objects = docs.list_inline_objects('unused')
    assert [(o['id'], o['type'], o['start_index'], o['width_pt'], o['height_pt'])
            for o in objects] == [
                ('inline-1', 'image', 7, 72, 36),
                ('positioned-1', 'drawing', 1, 72, 36)]


def test_footnote_body_is_not_document_body():
    document = load_capture('footnote-v1.json')
    reference = document['body']['content'][1]['paragraph']['elements'][1]
    footnote = document['footnotes'][reference['footnoteReference']['footnoteId']]
    assert footnote['footnoteId'] == 'footnote-1'
    # #61 includes footnotes with their own segment coordinates.
    assert docs.find_text_in_document(document, 'Note.') == [
        {'startIndex': 0, 'endIndex': 5, 'segmentId': 'footnote-1',
         'container': 'footnote'}]
    assert docs.find_text_in_document(None, 'Note.', body=footnote) == [
        {'startIndex': 0, 'endIndex': 5}]


def test_blank_selected_tab_stays_blank(mocker):
    document = load_capture('sibling-tabs-v1.json')
    tabs = docs.flatten_tabs(document['tabs'])
    blank = docs.resolve_tab(tabs, 'Blank')
    raw = docs.resolve_raw_tab(document['tabs'], 'tab-blank')
    assert blank['body'] == raw['documentTab']['body']
    assert docs.get_tab_text(blank, markdown=True) == '\n'
    assert docs._tab_body_range(blank['body']) == (1, 1)
    assert docs.find_text_in_document(document, 'Right', body=blank['body']) == []
    assert docs._collect_object_refs(blank['body']) == []
    rich = docs.resolve_tab(tabs, 'Rich')
    assert docs.find_text_in_document(None, 'Right', body=rich['body']) == [
        {'startIndex': 8, 'endIndex': 13}]
    mocker.patch.object(docs, 'get_document_with_tabs', return_value=document)
    assert [(o['id'], o['tab']) for o in docs.list_inline_objects('unused')] == [
        ('inline-1', 'tab-rich'), ('positioned-1', 'tab-rich')]
    assert docs.find_object_tab(document, 'inline-1') == 'tab-rich'


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('fixture_schema', 2, 'expected fixture_schema=1'),
    ('fixture_version', 2, 'expected fixture_schema=1'),
    ('api_shape', 'future-v2', 'expected docs/v1 document-body-v1'),
    ('unknown', {}, 'document.body.content[1].paragraph.elements[1].futureObject'),
    ('type', '7', 'unexpected scalar type'),
])
def test_unsupported_shape_points_to_supersession(tmp_path, monkeypatch,
                                                 field, value, message):
    capture = json.loads((CAPTURES / 'inline-positioned-v1.json').read_text())
    if field == 'api_shape':
        capture['capture'][field] = value
    elif field in ('unknown', 'type'):
        element = capture['document']['body']['content'][1]['paragraph']['elements'][1]
        element['futureObject' if field == 'unknown' else 'startIndex'] = value
    else:
        capture[field] = value
    (tmp_path / 'future.json').write_text(json.dumps(capture))
    monkeypatch.setattr(__name__ + '.CAPTURES', tmp_path)
    with pytest.raises(ValueError) as error:
        load_capture('future.json')
    assert message in str(error.value)
    assert PROCEDURE in str(error.value)
