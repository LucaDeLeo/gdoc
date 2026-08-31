"""Per-tab write-conflict baselines (0.22.0).

The lenient effective-baseline rule: a `write --tab X` is allowed when
max(whole-doc last_read_version, tab_read_versions[X]) equals the current doc
version. These tests cover the two defects per-tab state fixes — the
cross-tab false negative (read tab A, write tab B) and the same-tab false
positive (write tab X twice) — plus the sibling-edit conservatism and the
--force escape, at both the end-to-end (real state) and decision layers.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gdoc.cli import cmd_cat, cmd_write
from gdoc.notify import ChangeInfo
from gdoc.state import DocState, load_state, save_state
from gdoc.util import GdocError
from tests.conftest import doc_with_tabs as _doc_with_tabs

DOC_MIME = "application/vnd.google-apps.document"


def _tab_dict(tab_id, title):
    return {
        "id": tab_id, "title": title, "index": 0,
        "nesting_level": 0, "body": {"content": []},
    }


def _cat_args(doc, tab):
    return SimpleNamespace(
        command="cat", doc=doc, plain=False, comments=False, all=False,
        tab=tab, all_tabs=False, no_images=False, json=False, verbose=False,
        quiet=False, revision=None, range=None, max_bytes=0,
    )


def _write_args(doc, tab, file, **over):
    d = dict(
        command="write", doc=doc, file=file, force=False, json=False,
        verbose=False, quiet=True, tab=tab, force_collapse_tabs=False,
    )
    d.update(over)
    return SimpleNamespace(**d)


def _run_cat_tab(doc, tab_title, tab_id, version):
    """Run `gdoc cat --tab TITLE` non-quiet against a MULTI-tab doc; persists
    state under the caller's STATE_DIR patch. Stamps tab_read_versions[tab_id]
    = version. The mock carries a sibling tab on purpose: on a single-tab doc a
    `--tab` read would instead advance the whole-doc baseline."""
    change_info = ChangeInfo(current_version=version, mime_type=DOC_MIME)
    with patch("gdoc.notify.pre_flight", return_value=change_info), \
         patch("gdoc.api.docs.get_document_tabs",
               return_value=[
                   _tab_dict(tab_id, tab_title),
                   _tab_dict("t.sibling", "Sibling"),
               ]), \
         patch("gdoc.api.docs.get_tab_text", return_value="body\n"), \
         patch("gdoc.api.docs.get_docs_service"):
        assert cmd_cat(_cat_args(doc, tab_title)) == 0


class TestTabBaselineWorkflow:
    """End-to-end read -> write cycles against real (tmp) state, so the tab id
    cat stamps is exactly the key write checks."""

    def test_write_same_tab_twice_both_succeed(self, tmp_path):
        """False-positive fix: the writer's own output advances the tab
        baseline, so a second write to the same tab isn't blocked."""
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            _run_cat_tab("abc123", "X", "t.x", 100)
            st = load_state("abc123")
            assert st.last_read_version is None  # tab read, not whole-doc
            assert st.tab_read_versions == {"t.x": 100}

            # Model the doc version advancing on each (mocked) write.
            doc_version = [100]

            def fake_ver(doc_id):
                return {"version": doc_version[0]}

            def fake_insert(*a, **k):
                doc_version[0] += 1
                return {"tab_id": "t.x", "tab_title": "X", "insert_index": 1}

            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.x", "X"),
                                                   ("t.y", "Y"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab",
                       side_effect=fake_insert), \
                 patch("gdoc.api.drive.get_file_version", side_effect=fake_ver):
                assert cmd_write(_write_args("abc123", "X", str(f))) == 0
                assert cmd_write(_write_args("abc123", "X", str(f))) == 0

            st = load_state("abc123")
            assert st.tab_read_versions["t.x"] == doc_version[0]
            assert st.last_read_version is None  # never became a whole-doc read

    def test_read_tab_a_then_write_tab_b_blocked(self, tmp_path):
        """False-negative fix: reading tab A gives no baseline for tab B."""
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            _run_cat_tab("abc123", "A", "t.a", 100)
            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.a", "A"), ("t.b", "B"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab") as mock_insert, \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 100}):
                with pytest.raises(
                    GdocError, match="no read baseline for tab 'B'",
                ) as exc:
                    cmd_write(_write_args("abc123", "B", str(f)))
                assert exc.value.exit_code == 3
                mock_insert.assert_not_called()

    def test_plain_cat_then_write_tab_succeeds(self, tmp_path):
        """Lenient rule: a whole-doc cat is a genuine read of every tab, so
        its global baseline covers a subsequent tab write."""
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            change_info = ChangeInfo(current_version=100, mime_type=DOC_MIME)
            with patch("gdoc.notify.pre_flight", return_value=change_info), \
                 patch("gdoc.api.drive.export_doc", return_value="whole\n"):
                assert cmd_cat(_cat_args("abc123", None)) == 0
            assert load_state("abc123").last_read_version == 100

            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.b", "B"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab",
                       return_value={"tab_id": "t.b", "tab_title": "B",
                                     "insert_index": 1}), \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 100}):
                assert cmd_write(_write_args("abc123", "B", str(f))) == 0

    def test_single_tab_cat_then_whole_doc_write_succeeds(self, tmp_path):
        """R1 fix: on a single-tab doc, `cat --tab X` reads the whole document,
        so it advances the whole-doc baseline and a following whole-document
        `write DOC` is not blocked. (When tab reads stopped advancing the
        global baseline, this briefly errored 'no read baseline'.)"""
        f = tmp_path / "body.md"
        f.write_text("# edited\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            # Single-tab cat --tab is a whole-doc read: advances last_read.
            change_info = ChangeInfo(current_version=100, mime_type=DOC_MIME)
            with patch("gdoc.notify.pre_flight", return_value=change_info), \
                 patch("gdoc.api.docs.get_document_tabs",
                       return_value=[_tab_dict("t.only", "Only")]), \
                 patch("gdoc.api.docs.get_tab_text", return_value="body\n"), \
                 patch("gdoc.api.docs.get_docs_service"):
                assert cmd_cat(_cat_args("abc123", "Only")) == 0
            st = load_state("abc123")
            assert st.last_read_version == 100
            assert st.tab_read_versions == {}  # not a per-tab stamp

            # Whole-doc write at the same version passes on that baseline.
            with patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 100}), \
                 patch("gdoc.api.docs.count_document_tabs", return_value=1), \
                 patch("gdoc.api.drive.update_doc_content", return_value=101):
                assert cmd_write(_write_args("abc123", None, str(f))) == 0


class TestTabBaselineDecision:
    """Focused conflict decisions with an injected state, pinning the sibling
    -edit conservatism, its message, and the --force escape."""

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 101})
    @patch("gdoc.api.docs.insert_markdown_into_tab")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.state.load_state")
    @patch("gdoc.notify.pre_flight")
    def test_sibling_edit_blocks_tab_write(
        self, mock_pf, mock_load, mock_doc, mock_insert, _ver, _upd, tmp_path,
    ):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        # Tab X read at v100; the doc has since moved to v101 (a sibling edit).
        mock_load.return_value = DocState(
            last_read_version=None, tab_read_versions={"t.x": 100},
        )
        mock_pf.return_value = ChangeInfo(
            current_version=101, last_read_version=None,
        )
        mock_doc.return_value = _doc_with_tabs(("t.x", "X"))
        with pytest.raises(
            GdocError, match=r"tab 'X' may have changed .*v100 -> v101",
        ) as exc:
            cmd_write(_write_args("abc123", "X", str(f), quiet=False))
        assert exc.value.exit_code == 3
        mock_insert.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 100})
    @patch("gdoc.api.docs.insert_markdown_into_tab",
           return_value={"tab_id": "t.x", "tab_title": "X", "insert_index": 1})
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.state.load_state")
    @patch("gdoc.notify.pre_flight")
    def test_nonquiet_write_succeeds_on_own_tab_baseline(
        self, mock_pf, mock_load, mock_doc, mock_insert, _ver, _upd, tmp_path,
    ):
        """Non-quiet flagship happy path: with no whole-doc baseline, tab X
        read at v100 and the doc still at v100, `write --tab X` succeeds — the
        per-tab entry alone satisfies the guard. The non-quiet branch sources
        the global part from pre_flight (None here) and the per-tab part from
        state, so this locks that split, which the quiet success tests don't."""
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        mock_load.return_value = DocState(
            last_read_version=None, tab_read_versions={"t.x": 100},
        )
        mock_pf.return_value = ChangeInfo(
            current_version=100, last_read_version=None,
        )
        mock_doc.return_value = _doc_with_tabs(("t.x", "X"))
        rc = cmd_write(_write_args("abc123", "X", str(f), quiet=False))
        assert rc == 0
        mock_insert.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 101})
    @patch("gdoc.api.docs.insert_markdown_into_tab",
           return_value={"tab_id": "t.x", "tab_title": "X", "insert_index": 1})
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.state.load_state")
    @patch("gdoc.notify.pre_flight")
    def test_force_bypasses_sibling_edit(
        self, mock_pf, mock_load, mock_doc, mock_insert, _ver, _upd, tmp_path,
    ):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        mock_load.return_value = DocState(
            last_read_version=None, tab_read_versions={"t.x": 100},
        )
        mock_pf.return_value = ChangeInfo(
            current_version=101, last_read_version=None,
        )
        mock_doc.return_value = _doc_with_tabs(("t.x", "X"))
        rc = cmd_write(_write_args("abc123", "X", str(f), quiet=False, force=True))
        assert rc == 0
        mock_insert.assert_called_once()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 101})
    @patch("gdoc.api.docs.insert_markdown_into_tab")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.state.load_state")
    def test_quiet_sibling_edit_blocks_tab_write(
        self, mock_load, mock_doc, mock_insert, _ver, _upd, tmp_path,
    ):
        """The --quiet path reads the same per-tab baseline from state."""
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        mock_load.return_value = DocState(
            last_read_version=None, tab_read_versions={"t.x": 100},
        )
        mock_doc.return_value = _doc_with_tabs(("t.x", "X"))
        with pytest.raises(
            GdocError, match=r"tab 'X' may have changed .*v100 -> v101",
        ) as exc:
            cmd_write(_write_args("abc123", "X", str(f), quiet=True))
        assert exc.value.exit_code == 3
        mock_insert.assert_not_called()


class TestTabBaselineUrlParity:
    """A ?tab= URL drives the same per-tab conflict check as --tab."""

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 100})
    @patch("gdoc.api.docs.insert_markdown_into_tab")
    @patch("gdoc.api.docs.get_document_with_tabs")
    @patch("gdoc.state.load_state")
    def test_url_tab_blocked_without_baseline(
        self, mock_load, mock_doc, mock_insert, _ver, _upd, tmp_path,
    ):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        mock_load.return_value = None  # no prior read of anything
        mock_doc.return_value = _doc_with_tabs(("t.second", "Second"))
        args = _write_args(
            "https://docs.google.com/document/d/abc123/edit?tab=t.second",
            None, str(f), quiet=True,
        )
        with pytest.raises(
            GdocError, match="no read baseline for tab 't.second'",
        ) as exc:
            cmd_write(args)
        assert exc.value.exit_code == 3
        mock_insert.assert_not_called()
        # With no baseline the write is rejected regardless of version, so
        # the quiet path must not spend a get_file_version call to learn it.
        _ver.assert_not_called()


class TestLegacyBaselineProvenance:
    """F18: a pre-0.22 state file's last_read_version is ambiguous (it may
    record a tab-scoped read) and must not authorize a tab write."""

    def test_legacy_global_baseline_is_not_trusted(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            # Simulates a pre-0.22 file: field filtered in as default False.
            save_state("abc123", DocState(last_version=100,
                                          last_read_version=100))
            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.x", "X"),
                                                   ("t.y", "Y"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab"), \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 100}):
                with pytest.raises(GdocError, match="no read baseline"):
                    cmd_write(_write_args("abc123", "X", str(f)))

    def test_marked_global_baseline_is_trusted(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            save_state("abc123", DocState(
                last_version=100, last_read_version=100,
                global_read_covers_doc=True,
            ))
            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.x", "X"),
                                                   ("t.y", "Y"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab",
                       return_value={"tab_id": "t.x", "tab_title": "X",
                                     "insert_index": 1}), \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 100}):
                assert cmd_write(_write_args("abc123", "X", str(f))) == 0

    def test_whole_doc_cat_sets_provenance_marker(self, tmp_path):
        from gdoc.state import update_state_after_command

        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=100), command="cat",
                quiet=False,
            )
            st = load_state("abc123")
            assert st.last_read_version == 100
            assert st.global_read_covers_doc is True

    def test_tab_read_does_not_set_marker(self, tmp_path):
        from gdoc.state import update_state_after_command

        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=100), command="cat",
                quiet=False, read_tab_id="t.x",
            )
            st = load_state("abc123")
            assert st.last_read_version is None
            assert st.global_read_covers_doc is False


class TestSoleTabWholeDocRule:
    """F21: replacing a document's only tab IS a whole-doc write."""

    def test_sole_tab_write_advances_global_baseline(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.x", "X"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab",
                       return_value={"tab_id": "t.x", "tab_title": "X",
                                     "insert_index": 1}), \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 101}):
                args = _write_args("abc123", "X", str(f), force=True)
                assert cmd_write(args) == 0
            st = load_state("abc123")
            # Whole-doc baseline advanced: a following whole-doc write
            # won't conflict against our own mutation.
            assert st.last_read_version == 101
            assert st.global_read_covers_doc is True
            assert st.tab_read_versions == {"t.x": 101}


class TestBlankTabRejected:
    """F20: a blank --tab must error, not silently read as no tab."""

    def test_blank_flag_tab_errors(self):
        from gdoc.cli import _effective_tab

        with pytest.raises(GdocError, match="non-empty") as exc:
            _effective_tab("t.second", "  ")
        assert exc.value.exit_code == 3


class TestInfoDoesNotAuthorizeTabWrites:
    """`info` shows metadata only: it advances the whole-doc guard's
    baseline (status quo) but must not stamp whole-doc content provenance,
    which is what authorizes tab-scoped writes."""

    def test_info_does_not_set_provenance_marker(self, tmp_path):
        from gdoc.state import update_state_after_command

        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=100), command="info",
                quiet=False, command_version=100,
            )
            st = load_state("abc123")
            assert st.last_read_version == 100  # whole-doc guard status quo
            assert st.global_read_covers_doc is False

    def test_info_then_tab_write_blocked(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            from gdoc.state import update_state_after_command

            update_state_after_command(
                "abc123", ChangeInfo(current_version=100), command="info",
                quiet=False, command_version=100,
            )
            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.x", "X"),
                                                   ("t.y", "Y"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab") as mi, \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 100}):
                with pytest.raises(GdocError, match="no read baseline"):
                    cmd_write(_write_args("abc123", "X", str(f)))
                mi.assert_not_called()


    def test_info_after_edit_voids_stale_provenance(self, tmp_path):
        """cat (marker True) -> unseen edit -> info: the marker must be
        voided — the versions between the cat and the info were never
        read, so leaving it True would launder the edit into the guard."""
        from gdoc.state import update_state_after_command

        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=5), command="cat",
                quiet=False,
            )
            assert load_state("abc123").global_read_covers_doc is True

            update_state_after_command(
                "abc123", ChangeInfo(current_version=6), command="info",
                quiet=False,
            )
            st = load_state("abc123")
            assert st.last_read_version == 6
            assert st.global_read_covers_doc is False

    def test_info_at_same_version_preserves_provenance(self, tmp_path):
        """An info that confirms nothing moved keeps the cat's provenance:
        the authorization still traces to a genuine content read."""
        from gdoc.state import update_state_after_command

        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=5), command="cat",
                quiet=False,
            )
            update_state_after_command(
                "abc123", ChangeInfo(current_version=5), command="info",
                quiet=False,
            )
            assert load_state("abc123").global_read_covers_doc is True

    def test_quiet_info_after_edit_voids_stale_provenance(self, tmp_path):
        from gdoc.state import update_state_after_command

        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=5), command="cat",
                quiet=False,
            )
            update_state_after_command(
                "abc123", None, command="info", quiet=True,
                command_version=6,
            )
            st = load_state("abc123")
            assert st.last_read_version == 6
            assert st.global_read_covers_doc is False

    def test_cat_edit_info_write_tab_blocked_end_to_end(self, tmp_path):
        """The full laundering sequence must fail closed: cat v5 ->
        collaborator edit -> info v6 -> write --tab B is blocked."""
        from gdoc.state import update_state_after_command

        f = tmp_path / "body.md"
        f.write_text("# new\n")
        with patch("gdoc.state.STATE_DIR", tmp_path):
            update_state_after_command(
                "abc123", ChangeInfo(current_version=5), command="cat",
                quiet=False,
            )
            update_state_after_command(
                "abc123", ChangeInfo(current_version=6), command="info",
                quiet=False,
            )
            with patch("gdoc.api.docs.get_document_with_tabs",
                       return_value=_doc_with_tabs(("t.a", "A"),
                                                   ("t.b", "B"))), \
                 patch("gdoc.api.docs.insert_markdown_into_tab") as mi, \
                 patch("gdoc.api.drive.get_file_version",
                       return_value={"version": 6}):
                with pytest.raises(GdocError, match="no read baseline"):
                    cmd_write(_write_args("abc123", "B", str(f)))
                mi.assert_not_called()
