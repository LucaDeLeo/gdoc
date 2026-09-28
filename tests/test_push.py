"""Push frontmatter transport and native content provenance."""

import json
from unittest import mock

import pytest
import test_write as write_tests
from test_write import _make_args, native_tab

from gdoc import state
from gdoc.cli import cmd_push
from gdoc.util import GdocError

native_write = write_tests.native_write

FRONTMATTER = "---\ngdoc: abc123\ngdoc-revision: r10\ntitle: Harbor\n---\n"


@pytest.mark.parametrize(
    "doc", ["abc123", "https://docs.google.com/document/d/abc123/edit"]
)
@pytest.mark.parametrize("body", ["# Heading\n\nBody", "", "Changed\n"])
def test_push_transports_only_body_with_native_snapshot(native_write, doc, body):
    env = native_write
    env.path.write_text(f"---\ngdoc: {doc}\ngdoc-revision: r10\n---\n{body}")
    assert cmd_push(_make_args(file=str(env.path))) == 0
    env.write.assert_called_once_with(
        "abc123",
        body,
        expected_version=10,
        document=env.document,
        allow_lossy=False,
        collapse_tabs=False,
        result_details=env.details,
        image_aliases={},
    )
    assert state.load_state("abc123").read_revision_ids == {"first": "r11"}


@pytest.mark.parametrize("mode", ["json", "plain", "terse"])
def test_push_output_identifies_file_and_selected_tab(native_write, capsys, mode):
    env = native_write
    env.path.write_text(FRONTMATTER + "Body")
    assert (
        cmd_push(
            _make_args(file=str(env.path), **({mode: True} if mode != "terse" else {}))
        )
        == 0
    )
    out = capsys.readouterr().out
    if mode == "json":
        assert json.loads(out) == {
            "ok": True,
            "pushed": True,
            "tab_id": "first",
            "tab_title": "Draft",
            "revision_id": "r11",
            "version": 42,
            "file": str(env.path),
        }
    elif mode == "plain":
        assert (
            "id\tabc123" in out and "tab_id\tfirst" in out and "status\tupdated" in out
        )
    else:
        assert "OK pushed" in out


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("revision", [None, "r9"])
def test_push_refuses_absent_or_foreign_content_baseline(native_write, quiet, revision):
    env = native_write
    env.path.write_text(FRONTMATTER + "Body")
    state.save_state(
        "abc123",
        state.DocState(
            last_read_version=10,
            read_revision_ids={"first": revision} if revision else {},
        ),
    )
    before = state.load_state("abc123")
    with pytest.raises(GdocError) as error:
        cmd_push(_make_args(file=str(env.path), quiet=quiet))
    assert error.value.exit_code == 3
    assert (
        "changed since last read" in str(error.value)
        if revision
        else "complete read baseline" in str(error.value)
    )
    env.write.assert_not_called()
    assert state.load_state("abc123") == before


@pytest.mark.parametrize("quiet", [False, True])
def test_push_force_uses_current_native_revision(native_write, quiet):
    env = native_write
    env.path.write_text(FRONTMATTER + "Body")
    state.save_state("abc123", state.DocState())
    assert cmd_push(_make_args(file=str(env.path), quiet=quiet, force=True)) == 0
    env.preflight.assert_called_once_with("abc123", quiet=quiet)
    assert env.write.call_args.kwargs["document"]["revisionId"] == "r10"
    assert state.load_state("abc123").read_revision_ids == {"first": "r11"}


@pytest.mark.parametrize("baseline", [None, "r9"])
def test_push_noop_uses_native_content_and_revision(native_write, baseline, capsys):
    env = native_write
    env.path.write_text(FRONTMATTER + "Remote notes\n")
    state.save_state(
        "abc123",
        state.DocState(read_revision_ids={"first": baseline} if baseline else {}),
    )
    assert cmd_push(_make_args(file=str(env.path), json=True)) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "in_sync": True,
        "tab_id": "first",
        "revision_id": "r10",
    }
    env.write.assert_not_called()
    # Without a gdoc-tab-sha256 the revision string alone is no read (R8-14).
    assert state.load_state("abc123").read_revision_ids == (
        {"first": baseline} if baseline else {})


def test_push_default_preserves_sibling_and_tab_frontmatter_selects_it(native_write):
    env = native_write
    env.document["tabs"].append(native_tab("second", "Reference"))
    env.path.write_text(FRONTMATTER + "Default change")
    assert cmd_push(_make_args(file=str(env.path))) == 0
    assert env.write.call_args.kwargs["collapse_tabs"] is False
    assert "second" not in state.load_state("abc123").read_revision_ids
    env.path.write_text("---\ngdoc: abc123\ngdoc-revision: r10\n"
                        "tab: Reference\n---\nScoped change")
    with pytest.raises(GdocError, match="complete read baseline"):
        cmd_push(_make_args(file=str(env.path)))
    state.record_content_read("abc123", ["second"], "r10")
    assert cmd_push(_make_args(file=str(env.path))) == 0
    env.tab_write.assert_called_once_with(
        "abc123",
        "second",
        "Scoped change",
        replace=True,
        allow_lossy=False,
        document=env.document,
        image_aliases={},
    )


@pytest.mark.parametrize(
    "covered,force", [(False, False), (True, False), (False, True)]
)
def test_push_explicit_collapse_requires_coverage_or_force(
    native_write, covered, force
):
    env = native_write
    env.path.write_text(FRONTMATTER + "Replacement")
    env.document["tabs"].append(native_tab("second", "Reference"))
    if covered:
        state.record_content_read("abc123", ["second"], "r10")
    args = _make_args(file=str(env.path), force_collapse_tabs=True, force=force)
    if not covered and not force:
        with pytest.raises(GdocError, match="complete read baseline"):
            cmd_push(args)
        env.write.assert_not_called()
    else:
        assert cmd_push(args) == 0
        # The safety snapshot, then at most the post-acknowledgement read that
        # records the pushed file's native tab fingerprint.
        assert env.read.call_args_list[0] == mock.call("abc123")
        assert env.read.call_count <= 2
        assert env.write.call_args.kwargs["collapse_tabs"] is True


@pytest.mark.parametrize(
    "text,message",
    [
        ("# No frontmatter", "no gdoc frontmatter"),
        ("---\ntitle: Harbor\n---\nBody", "no gdoc frontmatter"),
        ("---\nsource: abc123\nrevision: 12\n---\nBody", "past revision"),
        ("---\ngdoc: !!invalid!!\n---\nBody", "invalid"),
    ],
)
def test_push_invalid_file_content_never_reads_or_mutates(native_write, text, message):
    native_write.path.write_text(text)
    with pytest.raises(GdocError, match=message) as error:
        cmd_push(_make_args(file=str(native_write.path)))
    assert error.value.exit_code == 3
    native_write.preflight.assert_not_called()
    native_write.read.assert_not_called()
    native_write.write.assert_not_called()


def test_push_missing_file_never_reads_or_mutates(native_write):
    with pytest.raises(GdocError, match="file not found"):
        cmd_push(_make_args(file=str(native_write.path.with_name("missing.md"))))
    native_write.read.assert_not_called()
    native_write.write.assert_not_called()


def test_push_state_update_retains_command_identity(native_write, mocker):
    native_write.path.write_text(FRONTMATTER + "Body")
    update = mocker.spy(state, "update_state_after_command")
    assert cmd_push(_make_args(file=str(native_write.path))) == 0
    update.assert_called_once_with(
        "abc123", native_write.info, command="push", quiet=False, command_version=42
    )


@pytest.mark.parametrize(
    ('line', 'error'),
    [
        ('gdoc-revision: r9\n', 'file gdoc-revision is stale'),
        ('gdoc-revision: \n', 'no gdoc-revision'),
        ('', 'no gdoc-revision'),
    ],
)
def test_file_provenance_cannot_be_healed_by_newer_cat(native_write, line, error):
    env = native_write
    env.path.write_text(f'---\ngdoc: abc123\n{line}---\nStale body')
    # The shared state already says r10: another cat or pull cannot bless this file.
    with pytest.raises(GdocError, match=error) as refused:
        cmd_push(_make_args(file=str(env.path)))
    assert refused.value.exit_code == 3
    env.write.assert_not_called()
    env.tab_write.assert_not_called()


def test_push_refreshes_file_provenance_for_next_push(native_write):
    from gdoc.frontmatter import parse_frontmatter

    env = native_write
    env.path.write_text(FRONTMATTER + 'First edit')
    args = _make_args(file=str(env.path))
    assert cmd_push(args) == 0
    metadata, body = parse_frontmatter(env.path.read_text())
    assert metadata['gdoc-revision'] == 'r11'
    assert body == 'First edit'
    env.document['revisionId'] = 'r11'
    env.details.update(input_revision_id='r11', acknowledged_revision_id='r12')
    env.path.write_text(env.path.read_text().replace('First edit', 'Second edit'))
    assert cmd_push(args) == 0
    assert parse_frontmatter(env.path.read_text())[0]['gdoc-revision'] == 'r12'
    assert env.write.call_count == 2


def test_pulled_tab_metadata_allows_explicit_collapse(native_write):
    env = native_write
    env.document['tabs'].append(native_tab('second', 'Reference'))
    state.record_content_read('abc123', ['second'], 'r10')
    env.path.write_text(FRONTMATTER.replace(
        'title: Harbor', 'title: Harbor\ntab: first') + 'Replacement')
    assert cmd_push(_make_args(file=str(env.path), force_collapse_tabs=True)) == 0
    assert env.write.call_args.kwargs['collapse_tabs'] is True


def test_tab_push_records_post_write_display_version(native_write):
    env = native_write
    env.path.write_text(FRONTMATTER.replace(
        'title: Harbor', 'title: Harbor\ntab: first') + 'Replacement')
    assert cmd_push(_make_args(file=str(env.path))) == 0
    assert state.load_state('abc123').last_version == 42


def test_missing_file_revision_pushes_with_explicit_force(native_write):
    env = native_write
    env.path.write_text('---\ngdoc: abc123\n---\nForced body')
    assert cmd_push(_make_args(file=str(env.path), force=True)) == 0
    env.write.assert_called_once()


def test_stale_file_matching_live_markdown_is_not_blessed(native_write, capsys):
    """Matching Markdown is not evidence the tab is unchanged natively."""
    from gdoc.frontmatter import parse_frontmatter

    env = native_write
    stale = '---\ngdoc: abc123\ngdoc-revision: r9\n---\nRemote notes\n'
    env.path.write_text(stale)
    assert cmd_push(_make_args(file=str(env.path))) == 0
    assert 'already in sync' in capsys.readouterr().out
    env.write.assert_not_called()
    metadata, _ = parse_frontmatter(env.path.read_text())
    assert metadata['gdoc-revision'] == 'r9'
    assert state.load_state("abc123") is None or "t.0" not in (
        state.load_state("abc123").read_revision_ids)
    # A later change from this file is still refused as stale.
    env.path.write_text(stale.replace("Remote notes", "Changed notes"))
    with pytest.raises(GdocError, match="stale"):
        cmd_push(_make_args(file=str(env.path)))
    env.write.assert_not_called()
