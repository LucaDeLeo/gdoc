"""Write transport, native snapshot provenance and explicit tab scope."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from gdoc import state
from gdoc.cli import cmd_write
from gdoc.notify import ChangeInfo
from gdoc.util import AuthError, GdocError


def _make_args(**overrides):
    return SimpleNamespace(
        **{
            "command": "write",
            "doc": "abc123",
            "file": "/missing.md",
            "force": False,
            "quiet": False,
            "json": False,
            "verbose": False,
            "plain": False,
            "tab": None,
            "force_collapse_tabs": False,
            "allow_lossy": False,
            **overrides,
        }
    )


def native_tab(tab_id="first", title="Draft", text="Remote notes\n"):
    end = 1 + len(text.encode("utf-16-le")) // 2
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": end,
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": 1,
                                    "endIndex": end,
                                    "textRun": {"content": text},
                                }
                            ],
                        },
                    }
                ]
            }
        },
    }


@pytest.fixture
def native_write(mocker, tmp_path):
    """Mock mutation boundaries; keep native read coverage and state code real."""
    document = {"revisionId": "r10", "tabs": [native_tab()]}
    read = mocker.patch("gdoc.api.docs.get_document_with_tabs", return_value=document)
    info = ChangeInfo(current_version=10, last_read_version=10)
    preflight = mocker.patch(
        "gdoc.notify.pre_flight",
        side_effect=lambda *a, quiet=False: None if quiet else info,
    )
    version = mocker.patch(
        "gdoc.api.drive.get_file_version", return_value={"version": 42}
    )
    details = {
        "input_revision_id": "r10",
        "acknowledged_revision_id": "r11",
        "rebased": False,
    }

    def update(*args, result_details, **kwargs):
        result_details.update(details)
        return 42

    write = mocker.patch("gdoc.api.drive.update_doc_content", side_effect=update)
    tab_write = mocker.patch(
        "gdoc.api.docs.insert_markdown_into_tab",
        side_effect=lambda *a, **kw: dict(details),
    )
    state.record_content_read("abc123", ["first"], "r10")
    path = tmp_path / "content.md"
    path.write_text("# New heading\n\nNew content.\n")
    return SimpleNamespace(
        document=document,
        read=read,
        preflight=preflight,
        info=info,
        version=version,
        write=write,
        tab_write=tab_write,
        details=details,
        path=path,
    )


@pytest.mark.parametrize(
    "doc", ["abc123", "https://docs.google.com/document/d/abc123/edit"]
)
@pytest.mark.parametrize(
    "text", ["# Heading\n\nContent.", "", "---\ngdoc: abc123\ngdoc-revision: r10\n---\n# Body\n"]
)
def test_write_transports_content_and_native_snapshot(native_write, doc, text):
    env = native_write
    env.path.write_text(text)
    before = deepcopy(env.document)
    assert cmd_write(_make_args(doc=doc, file=str(env.path))) == 0
    expected = "# Body\n" if text.startswith("---\n") else text
    env.write.assert_called_once_with(
        "abc123",
        expected,
        expected_version=10,
        document=env.document,
        allow_lossy=False,
        collapse_tabs=False,
        result_details=env.details,
        image_aliases={},
    )
    assert env.document == before
    assert state.load_state("abc123").read_revision_ids == {"first": "r11"}


@pytest.mark.parametrize("mode", ["terse", "json", "plain"])
def test_write_output_names_scope_and_acknowledged_revision(native_write, capsys, mode):
    assert (
        cmd_write(
            _make_args(
                file=str(native_write.path), **({mode: True} if mode != "terse" else {})
            )
        )
        == 0
    )
    output = capsys.readouterr().out
    if mode == "json":
        assert json.loads(output) == {
            "ok": True,
            "written": True,
            "tab_id": "first",
            "tab_title": "Draft",
            "revision_id": "r11",
            "version": 42,
        }
    elif mode == "plain":
        assert (
            "id\tabc123" in output
            and "tab_id\tfirst" in output
            and "status\tupdated" in output
        )
    else:
        assert "OK written" in output and "Draft" in output


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("baseline", [None, "stale", "metadata"])
def test_write_requires_complete_native_revision(native_write, quiet, baseline):
    saved = state.DocState(last_read_version=10, last_version=10)
    if baseline == "stale":
        saved.read_revision_ids = {"first": "r9"}
    state.save_state("abc123", saved)
    before = state.load_state("abc123")
    with pytest.raises(GdocError) as error:
        cmd_write(_make_args(file=str(native_write.path), quiet=quiet))
    assert error.value.exit_code == 3
    assert (
        "changed since last read" in str(error.value)
        if baseline == "stale"
        else "no complete read baseline" in str(error.value)
    )
    assert "--force" in str(error.value)
    native_write.write.assert_not_called()
    native_write.tab_write.assert_not_called()
    assert state.load_state("abc123") == before


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_write_uses_snapshot_revision_despite_drive_history(native_write, quiet, force):
    native_write.info.last_read_version = 3
    native_write.info.current_version = 99
    assert (
        cmd_write(_make_args(file=str(native_write.path), quiet=quiet, force=force))
        == 0
    )
    native_write.preflight.assert_called_once_with("abc123", quiet=quiet)
    assert native_write.write.call_args.kwargs["document"]["revisionId"] == "r10"
    assert state.load_state("abc123").read_revision_ids["first"] == "r11"


@pytest.mark.parametrize("quiet", [False, True])
def test_force_authorizes_new_snapshot_but_requires_revision(native_write, quiet):
    state.save_state("abc123", state.DocState())
    assert (
        cmd_write(_make_args(file=str(native_write.path), quiet=quiet, force=True)) == 0
    )
    native_write.write.reset_mock()
    native_write.document.pop("revisionId")
    with pytest.raises(GdocError, match="unpinned write"):
        cmd_write(_make_args(file=str(native_write.path), quiet=quiet, force=True))
    native_write.write.assert_not_called()


@pytest.mark.parametrize("baseline", [None, "stale"])
@pytest.mark.parametrize("tab", [None, "Draft"])
def test_matching_native_content_is_noop_and_establishes_no_read(
    native_write, baseline, tab, capsys
):
    """Equal Markdown is not a read: invisible native changes could differ."""
    state.save_state(
        "abc123",
        state.DocState(read_revision_ids={"first": baseline} if baseline else {}),
    )
    native_write.path.write_text("Remote notes\n")
    assert cmd_write(_make_args(file=str(native_write.path), tab=tab, json=True)) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "in_sync": True,
        "tab_id": "first",
        "revision_id": "r10",
    }
    native_write.write.assert_not_called()
    native_write.tab_write.assert_not_called()
    assert state.load_state("abc123").read_revision_ids == (
        {"first": baseline} if baseline else {})


def test_default_write_preserves_siblings_without_collapse_consent(native_write):
    native_write.document["tabs"].append(
        native_tab("second", "Reference", "Protected\n")
    )
    before = deepcopy(native_write.document)
    assert cmd_write(_make_args(file=str(native_write.path))) == 0
    assert native_write.write.call_args.kwargs["collapse_tabs"] is False
    assert native_write.document == before
    assert state.load_state("abc123").read_revision_ids == {"first": "r11"}


@pytest.mark.parametrize(
    "coverage,force,allowed",
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_explicit_collapse_requires_all_tab_coverage_or_force(
    native_write, coverage, force, allowed
):
    native_write.document["tabs"].append(native_tab("second", "Reference"))
    if coverage:
        state.record_content_read("abc123", ["second"], "r10")
    args = _make_args(
        file=str(native_write.path), force_collapse_tabs=True, force=force
    )
    if not allowed:
        with pytest.raises(GdocError, match="complete read baseline"):
            cmd_write(args)
        native_write.write.assert_not_called()
    else:
        assert cmd_write(args) == 0
        native_write.read.assert_called_once_with("abc123")
        assert native_write.write.call_args.kwargs["collapse_tabs"] is True
        assert native_write.write.call_args.kwargs["document"] is native_write.document


@pytest.mark.parametrize("force", [False, True])
def test_tab_write_uses_same_snapshot_and_preserves_unread_sibling(native_write, force):
    env = native_write
    env.document["tabs"].append(native_tab("second", "Reference"))
    args = _make_args(file=str(env.path), tab="Reference", force=force)
    if not force:
        with pytest.raises(GdocError, match="complete read baseline"):
            cmd_write(args)
        env.tab_write.assert_not_called()
        state.record_content_read("abc123", ["second"], "r10")
    assert cmd_write(args) == 0
    env.tab_write.assert_called_once_with(
        "abc123",
        "second",
        env.path.read_text(),
        replace=True,
        allow_lossy=False,
        document=env.document,
        image_aliases={},
    )
    env.write.assert_not_called()
    assert state.load_state("abc123").read_revision_ids == {
        "first": "r11",
        "second": "r11",
    }


def test_tab_write_strips_frontmatter_and_forced_write_exposes_only_target(
    native_write,
):
    env = native_write
    state.save_state("abc123", state.DocState())
    env.path.write_text("---\ngdoc: abc123\n---\n# Body\n")
    assert cmd_write(_make_args(file=str(env.path), tab="Draft", force=True)) == 0
    assert env.tab_write.call_args.args == ("abc123", "first", "# Body\n")
    assert state.load_state("abc123").read_revision_ids == {"first": "r11"}


def test_tab_and_collapse_conflict_precedes_read(native_write):
    with pytest.raises(GdocError, match="cannot be combined"):
        cmd_write(
            _make_args(
                file=str(native_write.path), tab="Draft", force_collapse_tabs=True
            )
        )
    native_write.read.assert_not_called()
    native_write.write.assert_not_called()


@pytest.mark.parametrize(
    "failure", [GdocError("synthetic API failure"), AuthError("synthetic auth failure")]
)
def test_write_failure_preserves_read_baseline(native_write, failure):
    native_write.write.side_effect = failure
    before = state.load_state("abc123")
    with pytest.raises(type(failure), match=str(failure)):
        cmd_write(_make_args(file=str(native_write.path)))
    assert state.load_state("abc123") == before


@pytest.mark.parametrize("kind", ["missing", "unreadable", "invalid-id"])
def test_write_local_errors_precede_api_and_state(native_write, kind):
    env = native_write
    args = _make_args(file=str(env.path))
    if kind == "missing":
        args.file = str(env.path.with_name("missing.md"))
    elif kind == "unreadable":
        env.path.chmod(0o000)
    else:
        args.doc = "!!invalid!!"
    before = state.load_state("abc123")
    try:
        with pytest.raises(GdocError):
            cmd_write(args)
    finally:
        env.path.chmod(0o644)
    env.preflight.assert_not_called()
    env.read.assert_not_called()
    env.write.assert_not_called()
    assert state.load_state("abc123") == before


def test_single_tab_inspection_header_refused(native_write):
    native_write.path.write_text('=== Tab: Draft ===\nRemote notes\n')
    with pytest.raises(GdocError, match='inspection view'):
        cmd_write(_make_args(file=str(native_write.path)))
    native_write.write.assert_not_called()


def test_inspection_header_inside_code_is_supported_text(native_write):
    native_write.path.write_text('```\n=== Tab: Draft ===\n```\n')
    assert cmd_write(_make_args(file=str(native_write.path))) == 0
    native_write.write.assert_called_once()
