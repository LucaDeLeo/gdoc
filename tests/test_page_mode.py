"""Tests for page-mode configuration and application (pageless vs paged).

Covers:
- gdoc.api.docs.set_page_mode (updateDocumentStyle request + error translation)
- gdoc.util.get_default_page_mode / set_default_page_mode (config.json)
- gdoc.cli._apply_page_mode (flag > config > leave-as-is, best-effort,
  refreshes the state version after a write)
- gdoc.cli.cmd_config (get/set the default, honoring output mode)
- cmd_new wiring (blank + --file paths apply the resolved mode)
- gdoc.cli.build_parser (config subcommand + --pageless/--paged mutex)
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdoc.cli import _apply_page_mode, cmd_config, cmd_new
from gdoc.util import AuthError, GdocError


def _http_error(status, reason):
    resp = httplib2.Response({"status": str(status)})
    err = HttpError(resp, b"")
    err.reason = reason
    return err


def _call_body(mock_batch_update):
    call = mock_batch_update.call_args
    return call.kwargs.get("body", call[1].get("body"))


# --- set_page_mode API wrapper ---

class TestSetPageModeAPI:
    @patch("gdoc.api.docs.get_docs_service")
    def test_pageless_request_body(self, mock_get):
        svc = MagicMock()
        mock_get.return_value = svc
        from gdoc.api.docs import set_page_mode

        set_page_mode("doc1", pageless=True)

        call = svc.documents().batchUpdate.call_args
        body = call.kwargs.get("body", call[1].get("body"))
        req = body["requests"][0]["updateDocumentStyle"]
        assert (
            req["documentStyle"]["documentFormat"]["documentMode"] == "PAGELESS"
        )
        assert req["fields"] == "documentFormat.documentMode"
        assert call.kwargs.get(
            "documentId", call[1].get("documentId")
        ) == "doc1"

    @patch("gdoc.api.docs.get_docs_service")
    def test_paged_request_body(self, mock_get):
        svc = MagicMock()
        mock_get.return_value = svc
        from gdoc.api.docs import set_page_mode

        set_page_mode("doc1", pageless=False)

        body = _call_body(svc.documents().batchUpdate)
        req = body["requests"][0]["updateDocumentStyle"]
        assert req["documentStyle"]["documentFormat"]["documentMode"] == "PAGES"

    @patch("gdoc.api.docs.get_docs_service")
    def test_404_translated(self, mock_get):
        svc = MagicMock()
        mock_get.return_value = svc
        svc.documents().batchUpdate().execute.side_effect = _http_error(
            404, "Not Found",
        )
        from gdoc.api.docs import set_page_mode

        with pytest.raises(GdocError, match="Document not found: doc1"):
            set_page_mode("doc1", pageless=True)

    @patch("gdoc.api.docs.get_docs_service")
    def test_401_translated(self, mock_get):
        svc = MagicMock()
        mock_get.return_value = svc
        svc.documents().batchUpdate().execute.side_effect = _http_error(
            401, "Unauthorized",
        )
        from gdoc.api.docs import set_page_mode

        with pytest.raises(AuthError, match="Authentication expired"):
            set_page_mode("doc1", pageless=True)


# --- config get/set ---

class TestPageModeConfig:
    def test_default_is_none_when_unset(self, tmp_path):
        # Unset means "no explicit preference": leave the create path's mode
        # alone rather than forcing paged (which would override a pageless
        # account default on the blank path).
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            from gdoc.util import get_default_page_mode

            assert get_default_page_mode() is None

    def test_set_and_get_pageless(self, tmp_path):
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            from gdoc.util import (
                get_default_page_mode,
                set_default_page_mode,
            )

            set_default_page_mode("pageless")
            assert get_default_page_mode() == "pageless"

    def test_set_back_to_paged(self, tmp_path):
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            from gdoc.util import (
                get_default_page_mode,
                set_default_page_mode,
            )

            set_default_page_mode("pageless")
            set_default_page_mode("paged")
            assert get_default_page_mode() == "paged"

    def test_invalid_value_rejected(self, tmp_path):
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            from gdoc.util import set_default_page_mode

            with pytest.raises(GdocError) as exc_info:
                set_default_page_mode("landscape")
            assert exc_info.value.exit_code == 3

    def test_invalid_stored_value_falls_back_to_none(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text('{"page_mode": "bogus"}')
        with patch("gdoc.util.CONFIG_PATH", cfg):
            from gdoc.util import get_default_page_mode

            assert get_default_page_mode() is None

    def test_preserves_other_config_keys(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text('{"default_account": "pete@example.com"}')
        with patch("gdoc.util.CONFIG_PATH", cfg):
            from gdoc.util import set_default_page_mode

            set_default_page_mode("pageless")
            data = json.loads(cfg.read_text())
            assert data["default_account"] == "pete@example.com"
            assert data["page_mode"] == "pageless"


# --- _apply_page_mode resolution ---

class TestApplyPageMode:
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
    @patch("gdoc.api.docs.set_page_mode")
    def test_pageless_flag(self, mock_set, _ver):
        args = SimpleNamespace(pageless=True, paged=False)
        _apply_page_mode(args, "doc1")
        mock_set.assert_called_once_with("doc1", True)

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
    @patch("gdoc.api.docs.set_page_mode")
    def test_paged_flag(self, mock_set, _ver):
        args = SimpleNamespace(pageless=False, paged=True)
        _apply_page_mode(args, "doc1")
        mock_set.assert_called_once_with("doc1", False)

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
    @patch("gdoc.util.get_default_page_mode", return_value="pageless")
    @patch("gdoc.api.docs.set_page_mode")
    def test_falls_back_to_config_pageless(self, mock_set, _cfg, _ver):
        args = SimpleNamespace(pageless=False, paged=False)
        _apply_page_mode(args, "doc1")
        mock_set.assert_called_once_with("doc1", True)

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 7})
    @patch("gdoc.util.get_default_page_mode", return_value="paged")
    @patch("gdoc.api.docs.set_page_mode")
    def test_falls_back_to_config_paged(self, mock_set, _cfg, _ver):
        args = SimpleNamespace(pageless=False, paged=False)
        _apply_page_mode(args, "doc1")
        mock_set.assert_called_once_with("doc1", False)

    @patch("gdoc.util.get_default_page_mode", return_value=None)
    @patch("gdoc.api.docs.set_page_mode")
    def test_no_preference_leaves_doc_untouched(self, mock_set, _cfg):
        # No flag and no configured default: the doc keeps whatever mode the
        # create path produced. No API call, no version refresh.
        args = SimpleNamespace(pageless=False, paged=False)
        assert _apply_page_mode(args, "doc1") is None
        mock_set.assert_not_called()

    @patch("gdoc.api.drive.get_file_version", return_value={"version": 9})
    @patch("gdoc.api.docs.set_page_mode")
    def test_returns_refreshed_version_after_write(self, _set, _ver):
        # The updateDocumentStyle write bumps the Drive version; the caller
        # needs the post-write value to seed an accurate state baseline.
        args = SimpleNamespace(pageless=True, paged=False)
        assert _apply_page_mode(args, "doc1") == 9

    @patch(
        "gdoc.api.drive.get_file_version",
        side_effect=GdocError("refresh failed"),
    )
    @patch("gdoc.api.docs.set_page_mode")
    def test_version_refresh_failure_is_nonfatal(self, _set, _ver):
        # set_page_mode succeeded (the write landed) but the follow-up
        # version re-read failed: degrade to None rather than raise. The
        # caller then keeps the create-time version — a stale banner is
        # better than aborting after the doc exists.
        args = SimpleNamespace(pageless=True, paged=False)
        assert _apply_page_mode(args, "doc1") is None

    @patch("gdoc.api.docs.set_page_mode", side_effect=GdocError("boom"))
    def test_gdoc_error_is_nonfatal(self, mock_set, capsys):
        args = SimpleNamespace(pageless=True, paged=False)
        # Must not raise — the doc is already created.
        assert _apply_page_mode(args, "doc1") is None
        assert "could not set page mode" in capsys.readouterr().err

    @patch(
        "gdoc.api.docs.set_page_mode",
        side_effect=ConnectionError("network down"),
    )
    def test_non_gdoc_error_is_also_nonfatal(self, mock_set, capsys):
        # set_page_mode only translates HttpError; a transient socket/SSL/auth
        # failure is not a GdocError. The best-effort guarantee must still hold
        # (the doc already exists), so it is swallowed too.
        args = SimpleNamespace(pageless=True, paged=False)
        assert _apply_page_mode(args, "doc1") is None
        assert "could not set page mode" in capsys.readouterr().err


# --- cmd_config ---

class TestCmdConfig:
    def test_show_unset(self, tmp_path, capsys):
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            args = SimpleNamespace(page_mode=None)
            rc = cmd_config(args)
            assert rc == 0
            assert capsys.readouterr().out.strip() == "page_mode\tunset"

    def test_show_json(self, tmp_path, capsys):
        # config opts into --json/--verbose/--plain via output_parent, so it
        # must honor them like every other subcommand (machine-parseable out).
        cfg = tmp_path / "config.json"
        cfg.write_text('{"page_mode": "pageless"}')
        with patch("gdoc.util.CONFIG_PATH", cfg):
            args = SimpleNamespace(page_mode=None, json=True)
            rc = cmd_config(args)
            assert rc == 0
            assert json.loads(capsys.readouterr().out) == {
                "ok": True, "page_mode": "pageless",
            }

    def test_set_pageless(self, tmp_path, capsys):
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            args = SimpleNamespace(page_mode="pageless")
            rc = cmd_config(args)
            assert rc == 0
            assert "OK page_mode set to: pageless" in capsys.readouterr().err
            from gdoc.util import get_default_page_mode

            assert get_default_page_mode() == "pageless"

    def test_set_json(self, tmp_path, capsys):
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            args = SimpleNamespace(page_mode="paged", json=True)
            rc = cmd_config(args)
            assert rc == 0
            assert json.loads(capsys.readouterr().out) == {
                "ok": True, "page_mode": "paged",
            }

    def test_set_echoes_value_to_stdout(self, tmp_path, capsys):
        # SET must put the value on stdout (not only the stderr hint), so
        # `out=$(gdoc config --page-mode pageless)` captures it — the TSV
        # contract every other write command follows.
        with patch("gdoc.util.CONFIG_PATH", tmp_path / "config.json"):
            args = SimpleNamespace(page_mode="pageless")
            rc = cmd_config(args)
            assert rc == 0
            captured = capsys.readouterr()
            assert captured.out.strip() == "page_mode\tpageless"
            assert "OK page_mode set to: pageless" in captured.err


# --- cmd_new wiring ---

def _new_args(**overrides):
    defaults = {
        "command": "new",
        "title": "T",
        "folder": None,
        "file_path": None,
        "json": False,
        "verbose": False,
        "plain": False,
        "quiet": False,
        "pageless": False,
        "paged": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_BLANK_RESULT = {
    "id": "doc1",
    "name": "T",
    "version": 1,
    "webViewLink": "https://docs.google.com/document/d/doc1/edit",
}


class TestCmdNewAppliesPageMode:
    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    @patch("gdoc.api.docs.set_page_mode")
    @patch("gdoc.api.drive.create_doc", return_value=_BLANK_RESULT)
    def test_blank_new_pageless_flag(self, _create, mock_set, _ver, _update):
        cmd_new(_new_args(pageless=True))
        mock_set.assert_called_once_with("doc1", True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    @patch("gdoc.util.get_default_page_mode", return_value="pageless")
    @patch("gdoc.api.docs.set_page_mode")
    @patch("gdoc.api.drive.create_doc", return_value=_BLANK_RESULT)
    def test_blank_new_uses_config_default(
        self, _create, mock_set, _cfg, _ver, _update,
    ):
        # Mock a *pageless* config default so this actually discriminates
        # "config was consulted" from "hardcoded fallback": if _apply_page_mode
        # ignored config, it would pass the opposite value.
        cmd_new(_new_args())
        mock_set.assert_called_once_with("doc1", True)

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.util.get_default_page_mode", return_value=None)
    @patch("gdoc.api.docs.set_page_mode")
    @patch("gdoc.api.drive.create_doc", return_value=_BLANK_RESULT)
    def test_blank_new_no_preference_skips_write(
        self, _create, mock_set, _cfg, _update,
    ):
        # No flag, no config: leave the account default in place (no write).
        cmd_new(_new_args())
        mock_set.assert_not_called()

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 5})
    @patch("gdoc.api.docs.set_page_mode")
    @patch("gdoc.api.drive.create_doc", return_value=_BLANK_RESULT)
    def test_state_seeded_with_post_write_version(
        self, _create, _set, _ver, mock_update,
    ):
        # The page-mode write advances the Drive version (create-time was 1);
        # state must be seeded with the refreshed version (5), not the stale 1,
        # or the next command reports a spurious "doc edited" change.
        cmd_new(_new_args(pageless=True))
        assert mock_update.call_args.kwargs["command_version"] == 5

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 4})
    @patch("gdoc.api.docs.set_page_mode")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=_BLANK_RESULT,
    )
    def test_markdown_import_applies_pageless_flag(
        self, _create, mock_set, _ver, mock_update, tmp_path,
    ):
        md = tmp_path / "doc.md"
        md.write_text("# Hi\n")
        cmd_new(_new_args(file_path=str(md), pageless=True))
        mock_set.assert_called_once_with("doc1", True)
        # --file is the feature's motivating path: verify the refreshed
        # version (4) is folded into the state baseline, not the stale
        # create-time 1 (guards the same regression as the blank-path test).
        assert mock_update.call_args.kwargs["command_version"] == 4

    @patch("gdoc.state.update_state_after_command")
    @patch("gdoc.api.drive.get_file_version", return_value={"version": 2})
    @patch("gdoc.util.get_default_page_mode", return_value="pageless")
    @patch("gdoc.api.docs.set_page_mode")
    @patch(
        "gdoc.api.drive.create_doc_from_markdown",
        return_value=_BLANK_RESULT,
    )
    def test_markdown_import_uses_config_default(
        self, _create, mock_set, _cfg, _ver, _update, tmp_path,
    ):
        # The literal headline use case from the PR Usage block:
        # `gdoc config --page-mode pageless` then `gdoc new --file notes.md`
        # (no flag) must flip the imported doc to pageless via the config
        # default. End-to-end guard for _cmd_new_from_file's wiring.
        md = tmp_path / "doc.md"
        md.write_text("# Hi\n")
        cmd_new(_new_args(file_path=str(md)))
        mock_set.assert_called_once_with("doc1", True)


# --- argparse wiring (build_parser) ---

class TestParserWiring:
    """Guard the new parser wiring: dropping set_defaults, mistyping the
    choices, or breaking the mutex group would otherwise ship green (the
    handler tests bypass build_parser via SimpleNamespace).
    """

    def test_new_pageless_paged_mutually_exclusive(self):
        from gdoc.cli import build_parser

        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["new", "--pageless", "--paged", "T"])
        assert exc.value.code == 3

    def test_new_pageless_flag_parses(self):
        from gdoc.cli import build_parser, cmd_new

        args = build_parser().parse_args(["new", "--pageless", "T"])
        assert args.pageless is True
        assert args.paged is False
        assert args.func is cmd_new

    def test_new_paged_flag_parses(self):
        from gdoc.cli import build_parser

        args = build_parser().parse_args(["new", "--paged", "T"])
        assert args.paged is True
        assert args.pageless is False

    def test_config_rejects_bad_page_mode(self):
        from gdoc.cli import build_parser

        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["config", "--page-mode", "landscape"])
        assert exc.value.code == 3

    def test_config_dispatches(self):
        from gdoc.cli import build_parser, cmd_config

        args = build_parser().parse_args(["config", "--page-mode", "pageless"])
        assert args.func is cmd_config
        assert args.page_mode == "pageless"

    def test_config_no_args_dispatches(self):
        from gdoc.cli import build_parser, cmd_config

        args = build_parser().parse_args(["config"])
        assert args.func is cmd_config
        assert args.page_mode is None
