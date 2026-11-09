"""Per-tab write baselines and `?tab=` URL targeting.

0.23.0 (#56) tracked a Drive-version read baseline per tab. The native
Markdown writer replaces that with per-tab read coverage at an exact Docs
revision (gdoc.state.record_content_read / require_content_baseline): a
tab-scoped write needs a complete read of that tab at the revision being
written. These tests pin #56's scenarios on that mechanism: the cross-tab
false negative (read tab A, write tab B), the same-tab false positive (write
tab X twice), the sibling-edit conservatism with its --force escape, URL tab
parity, and the URL-aware recovery steps for stale stamped files.
"""

import shlex
from types import SimpleNamespace

import pytest

from gdoc import state
from gdoc.cli import cmd_cat, cmd_write
from gdoc.notify import ChangeInfo
from gdoc.util import GdocError

DOC = "abc123"
URL = "https://docs.google.com/document/d/abc123/edit"


def _native_tab(tab_id, title, text="Remote notes\n"):
    end = 1 + len(text.encode("utf-16-le")) // 2
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {"body": {"content": [{
            "startIndex": 1, "endIndex": end,
            "paragraph": {"elements": [{
                "startIndex": 1, "endIndex": end,
                "textRun": {"content": text},
            }]},
        }]}},
    }


def _cat_args(doc, tab=None, **over):
    d = dict(
        command="cat", doc=doc, plain=False, comments=False, all=False,
        tab=tab, all_tabs=False, no_images=False, json=False, verbose=False,
        quiet=False, revision=None, range=None, max_bytes=0,
    )
    d.update(over)
    return SimpleNamespace(**d)


def _write_args(doc, tab, file, **over):
    d = dict(
        command="write", doc=doc, file=file, force=False, json=False,
        verbose=False, plain=False, quiet=False, tab=tab,
        force_collapse_tabs=False, allow_lossy=False,
    )
    d.update(over)
    return SimpleNamespace(**d)


@pytest.fixture
def docs(mocker, tmp_path):
    """A live document whose revision moves on each acknowledged write.

    Only the API boundary is mocked; reads, coverage and the write guard
    run for real against the tmp state directory.
    """
    live = {"revisionId": "r1", "tabs": [
        _native_tab("t.x", "X"), _native_tab("t.y", "Y"),
    ]}
    mocker.patch("gdoc.api.docs.get_document_with_tabs",
                 side_effect=lambda *a, **k: live)
    mocker.patch("gdoc.notify.pre_flight",
                 side_effect=lambda *a, quiet=False: None if quiet else
                 ChangeInfo(current_version=10, last_read_version=None))
    mocker.patch("gdoc.api.drive.get_file_version",
                 return_value={"version": 10})

    def write_tab(doc_id, tab_id, content, **kwargs):
        before = live["revisionId"]
        live["revisionId"] = f"r{int(before[1:]) + 1}"
        return {"tab_id": tab_id, "input_revision_id": before,
                "acknowledged_revision_id": live["revisionId"],
                "rebased": False}

    insert = mocker.patch("gdoc.api.docs.insert_markdown_into_tab",
                          side_effect=write_tab)
    body = tmp_path / "body.md"
    body.write_text("# new\n")

    def collaborator_edit():
        live["revisionId"] = f"r{int(live['revisionId'][1:]) + 1}"

    return SimpleNamespace(live=live, insert=insert, body=str(body),
                           collaborator_edit=collaborator_edit)


class TestTabBaselineWorkflow:
    """End-to-end read -> write cycles against real (tmp) state, so the tab
    the read covers is exactly the tab the write checks."""

    def test_write_same_tab_twice_both_succeed(self, docs):
        """False-positive fix: the writer's own output advances the tab's
        coverage, so a second write to the same tab isn't blocked."""
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        assert state.load_state(DOC).read_revision_ids == {"t.x": "r1"}
        assert cmd_write(_write_args(DOC, "X", docs.body)) == 0
        assert cmd_write(_write_args(DOC, "X", docs.body)) == 0
        assert docs.insert.call_count == 2
        assert state.load_state(DOC).read_revision_ids == {"t.x": "r3"}

    def test_read_tab_a_then_write_tab_b_blocked(self, docs):
        """False-negative fix: reading tab X gives no baseline for tab Y."""
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        with pytest.raises(GdocError, match="no complete read baseline") as exc:
            cmd_write(_write_args(DOC, "Y", docs.body))
        assert exc.value.exit_code == 3
        docs.insert.assert_not_called()

    def test_all_tabs_cat_then_write_tab_succeeds(self, docs):
        """A read of every tab covers each of them. (#56 credited a plain
        `cat` with this; a plain `cat` now reads only the first tab.)"""
        assert cmd_cat(_cat_args(DOC, all_tabs=True)) == 0
        assert cmd_write(_write_args(DOC, "Y", docs.body)) == 0
        docs.insert.assert_called_once()

    def test_plain_cat_covers_only_the_first_tab(self, docs):
        assert cmd_cat(_cat_args(DOC)) == 0
        assert state.load_state(DOC).read_revision_ids == {"t.x": "r1"}
        with pytest.raises(GdocError, match="no complete read baseline"):
            cmd_write(_write_args(DOC, "Y", docs.body))
        docs.insert.assert_not_called()

    def test_single_tab_cat_then_whole_doc_write_succeeds(self, docs):
        """On a single-tab document `cat --tab X` reads the tab a default
        `write` replaces, so that write is not blocked."""
        del docs.live["tabs"][1]
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        assert cmd_write(_write_args(DOC, None, docs.body)) == 0
        assert docs.insert.call_args.args[1] == "t.x"


class TestTabBaselineDecision:
    """The sibling-edit conservatism and the --force escape. The revision
    is document-wide, so an edit to any tab moves it."""

    def test_sibling_edit_blocks_tab_write(self, docs):
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        docs.collaborator_edit()
        with pytest.raises(GdocError, match="changed since last read") as exc:
            cmd_write(_write_args(DOC, "X", docs.body))
        assert exc.value.exit_code == 3
        docs.insert.assert_not_called()

    def test_force_bypasses_sibling_edit(self, docs):
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        docs.collaborator_edit()
        assert cmd_write(_write_args(DOC, "X", docs.body, force=True)) == 0
        docs.insert.assert_called_once()

    def test_quiet_sibling_edit_blocks_tab_write(self, docs):
        """--quiet skips pre-flight but never the coverage check."""
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        docs.collaborator_edit()
        with pytest.raises(GdocError, match="changed since last read"):
            cmd_write(_write_args(DOC, "X", docs.body, quiet=True))
        docs.insert.assert_not_called()


class TestTabBaselineUrlParity:
    """A ?tab= URL drives the same per-tab check as --tab."""

    def test_url_tab_blocked_without_baseline(self, docs):
        with pytest.raises(GdocError, match="no complete read baseline") as exc:
            cmd_write(_write_args(f"{URL}?tab=t.y", None, docs.body))
        assert exc.value.exit_code == 3
        docs.insert.assert_not_called()

    def test_url_read_then_url_write(self, docs):
        assert cmd_cat(_cat_args(f"{URL}?tab=t.y")) == 0
        assert state.load_state(DOC).read_revision_ids == {"t.y": "r1"}
        assert cmd_write(_write_args(f"{URL}?tab=t.y", None, docs.body)) == 0
        assert docs.insert.call_args.args[1] == "t.y"

    def test_url_tab_is_matched_by_id_only(self, docs):
        """A tab titled like the URL's tab ID must not capture it."""
        docs.live["tabs"][0]["tabProperties"]["title"] = "t.z"
        with pytest.raises(GdocError, match="tab not found: t.z"):
            cmd_cat(_cat_args(f"{URL}?tab=t.z"))

    def test_url_tab_with_collapse_is_refused(self, docs):
        with pytest.raises(GdocError, match="the URL targets tab 't.y'") as exc:
            cmd_write(_write_args(f"{URL}?tab=t.y", None, docs.body,
                                  force_collapse_tabs=True))
        assert exc.value.exit_code == 3
        docs.insert.assert_not_called()


class TestStateFromOtherVersions:
    """State files written by 0.23.0 (#56's Drive-version baselines) or
    earlier carry no native read coverage: they load, and a tab write still
    needs a fresh read."""

    def test_0_23_0_state_file_loads_and_does_not_authorize(self, docs):
        import json

        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (state.STATE_DIR / f"{DOC}.json").write_text(json.dumps({
            "last_version": 10, "last_read_version": 10,
            "tab_read_versions": {"t.x": 10}, "global_read_covers_doc": True,
        }))
        loaded = state.load_state(DOC)
        assert loaded is not None and loaded.last_read_version == 10
        with pytest.raises(GdocError, match="no complete read baseline"):
            cmd_write(_write_args(DOC, "X", docs.body))
        docs.insert.assert_not_called()


class TestSoleTabWholeDocRule:
    """Replacing a document's only tab covers what a default write replaces."""

    def test_sole_tab_write_then_default_write(self, docs):
        del docs.live["tabs"][1]
        assert cmd_write(_write_args(DOC, "X", docs.body, force=True)) == 0
        assert state.load_state(DOC).read_revision_ids == {"t.x": "r2"}
        assert cmd_write(_write_args(DOC, None, docs.body)) == 0


class TestBlankTabRejected:
    """A blank --tab must error, not silently read as no tab."""

    def test_blank_flag_tab_errors(self):
        from gdoc.cli import _effective_tab

        with pytest.raises(GdocError, match="non-empty") as exc:
            _effective_tab("t.second", "  ")
        assert exc.value.exit_code == 3


class TestInfoDoesNotAuthorizeTabWrites:
    """`info` shows metadata only and records no read coverage, so it can
    neither authorize a tab write nor launder an unseen edit into one."""

    def _info(self, mocker):
        """What `info` records: the interaction, not a content read."""
        from gdoc.state import update_state_after_command

        update_state_after_command(
            DOC, ChangeInfo(current_version=10), command="info",
            quiet=False, command_version=10,
        )

    def test_info_then_tab_write_blocked(self, docs, mocker):
        self._info(mocker)
        with pytest.raises(GdocError, match="no complete read baseline"):
            cmd_write(_write_args(DOC, "X", docs.body))
        docs.insert.assert_not_called()

    def test_cat_edit_info_write_tab_blocked_end_to_end(self, docs, mocker):
        """cat --tab X -> collaborator edit -> info -> write --tab X fails
        closed: the coverage is pinned to the revision the cat saw."""
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        docs.collaborator_edit()
        self._info(mocker)
        with pytest.raises(GdocError, match="changed since last read"):
            cmd_write(_write_args(DOC, "X", docs.body))
        docs.insert.assert_not_called()


class TestFlagTabKeepsWhitespace:
    """A non-blank --tab is used as given: titles can carry spaces."""

    def test_value_is_not_stripped(self):
        from gdoc.cli import _effective_tab

        assert _effective_tab(None, " Notes ") == (" Notes ", False)

    def test_spaced_title_targets_its_own_tab(self, docs):
        docs.live["tabs"][0]["tabProperties"]["title"] = "Notes"
        docs.live["tabs"][1]["tabProperties"]["title"] = " Notes "
        assert cmd_write(_write_args(DOC, " Notes ", docs.body, force=True)) == 0
        assert docs.insert.call_args.args[1] == "t.y"


class TestUnpinnedWriteFailsClosed:
    """#56 refused a tab write when Drive returned no version. The guard is
    now the native revision: a read with no revision cannot pin a write.
    (The Drive version is display-only for a tab write.)"""

    def test_missing_revision_refuses(self, docs):
        assert cmd_cat(_cat_args(DOC, "X")) == 0
        docs.live["revisionId"] = ""
        with pytest.raises(GdocError, match="unpinned write"):
            cmd_write(_write_args(DOC, "X", docs.body))
        docs.insert.assert_not_called()

    def test_stamped_file_without_drive_version_refuses(self, docs, mocker):
        mocker.patch("gdoc.api.drive.get_file_version", return_value={})
        mocker.patch("gdoc.api.drive.export_doc", return_value="other\n")
        f = docs.body
        with open(f, "w") as fh:
            fh.write("---\ngdoc: abc123\ngdoc-version: 100\n---\n# new\n")
        with pytest.raises(GdocError, match="no gdoc-revision"):
            cmd_write(_write_args(DOC, "X", f))
        docs.insert.assert_not_called()


class TestStaleStampedTabWriteRecovery:
    """A stale 0.21.1-style stamped file written to one tab recovers by
    that tab, with the selector the user gave."""

    @pytest.fixture
    def stale(self, docs, mocker):
        mocker.patch("gdoc.api.drive.get_file_version",
                     return_value={"version": 101})
        mocker.patch("gdoc.api.drive.export_doc", return_value="other\n")
        with open(docs.body, "w") as fh:
            fh.write("---\ngdoc: abc123\ngdoc-version: 100\n---\n# new\n")
        return docs

    def test_recovery_stays_on_the_tab(self, stale):
        with pytest.raises(GdocError) as exc:
            cmd_write(_write_args(DOC, "X", stale.body))
        msg = str(exc.value)
        assert exc.value.exit_code == 3
        assert "gdoc cat abc123 --tab X >" in msg
        assert "gdoc write abc123 --tab X " in msg
        assert "push" not in msg
        stale.insert.assert_not_called()

    def test_url_write_recovers_by_url(self, stale):
        url = f"{URL}?tab=t.y"
        with pytest.raises(GdocError) as exc:
            cmd_write(_write_args(url, None, stale.body))
        msg = str(exc.value)
        quoted = shlex.quote(url)
        assert f"gdoc cat {quoted} >" in msg
        assert f"gdoc write {quoted} " in msg
        assert "--tab" not in msg
        stale.insert.assert_not_called()


class TestTabScopedChangeWarning:
    """`nest` warns against the tab it changes, from that tab's coverage."""

    def _warn(self, change_info, tab_id, revision, capsys):
        from gdoc.cli import _warn_if_tab_changed

        _warn_if_tab_changed(DOC, change_info, tab_id, revision)
        return "WARN: doc changed since last read" in capsys.readouterr().err

    def test_tab_read_counts(self, capsys):
        state.record_content_read(DOC, ["t.x"], "r5")
        info = ChangeInfo(current_version=5, last_read_version=None)
        assert not self._warn(info, "t.x", "r5", capsys)
        assert self._warn(info, "t.x", "r6", capsys)
        # No recorded read of a tab is no sign of a change (the write is
        # pinned to the revision it reads either way).
        assert not self._warn(info, "t.y", "r5", capsys)

    def test_quiet_never_warns(self, capsys):
        state.record_content_read(DOC, ["t.x"], "r5")
        assert not self._warn(None, "t.x", "r6", capsys)
