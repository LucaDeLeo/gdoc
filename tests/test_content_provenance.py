"""Content exposure, own writes and collaborator revisions must stay distinct."""
import pytest

from gdoc import state
from gdoc.util import GdocError


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'STATE_DIR', tmp_path)


def test_selected_tab_read_does_not_authorize_unseen_sibling():
    state.record_content_read('doc', ['one'], 'r1')
    state.require_content_baseline('doc', ['one'], 'r1')
    with pytest.raises(GdocError, match='no complete read baseline'):
        state.require_content_baseline('doc', ['two'], 'r1')


def test_own_write_carries_only_current_exposed_tabs():
    state.record_content_read('doc', ['old'], 'r0')
    state.record_content_read('doc', ['one', 'two'], 'r1')
    state.record_content_write(
        'doc', input_revision_id='r1', acknowledged_revision_id='r2',
    )
    state.require_content_baseline('doc', ['one', 'two'], 'r2')
    with pytest.raises(GdocError, match='changed since last read'):
        state.require_content_baseline('doc', ['old'], 'r2')
    with pytest.raises(GdocError, match='changed since last read'):
        state.require_content_baseline('doc', ['one'], 'collaborator-r3')


def test_full_tab_replacement_exposes_sent_tab_only():
    state.record_content_write(
        'doc', input_revision_id='r1', acknowledged_revision_id='r2',
        replaced_tab_ids=['one'],
    )
    state.require_content_baseline('doc', ['one'], 'r2')
    with pytest.raises(GdocError, match='no complete read baseline'):
        state.require_content_baseline('doc', ['two'], 'r2')


def test_rebased_write_does_not_bless_intervening_content():
    state.record_content_read('doc', ['one', 'two'], 'r1')
    state.record_content_write(
        'doc', input_revision_id='r1', acknowledged_revision_id='r3',
        replaced_tab_ids=['one'], rebased=True,
    )
    assert state.load_state('doc').read_revision_ids == {'one': 'r1', 'two': 'r1'}
    for tab in ['one', 'two']:
        with pytest.raises(GdocError, match='changed since last read'):
            state.require_content_baseline('doc', [tab], 'r3')


def test_uncertain_write_cannot_advance_baseline():
    state.record_content_read('doc', ['one'], 'r1')
    state.record_content_write(
        'doc', input_revision_id='r1', acknowledged_revision_id='',
    )
    assert state.load_state('doc').read_revision_ids == {'one': 'r1'}


def test_old_drive_version_state_is_not_native_content_provenance():
    state.save_state('doc', state.DocState(last_read_version=10))
    with pytest.raises(GdocError, match='no complete read baseline'):
        state.require_content_baseline('doc', ['one'], 'r1')
    state.require_content_baseline('doc', ['one'], 'r1', force=True)
    with pytest.raises(GdocError, match='unpinned write'):
        state.require_content_baseline('doc', ['one'], '', force=True)
