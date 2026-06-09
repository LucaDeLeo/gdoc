"""Tests for gdoc.update — auto-update on help and helpers."""

import json
import subprocess

import pytest

from gdoc import update


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    """Redirect the update cache to a tmp path."""
    path = tmp_path / "update_check.json"
    monkeypatch.setattr(update, "_CACHE_FILE", path)
    return path


@pytest.fixture
def fake_uv_install(monkeypatch):
    """Make _is_uv_tool_install() return True."""
    monkeypatch.setattr(update, "_is_uv_tool_install", lambda: True)


class TestIsUvToolInstall:
    def test_detects_uv_tool_path(self, monkeypatch):
        monkeypatch.setattr(
            update.sys, "executable",
            "/Users/foo/.local/share/uv/tools/gdoc/bin/python",
        )
        assert update._is_uv_tool_install() is True

    def test_rejects_non_uv_path(self, monkeypatch):
        monkeypatch.setattr(update.sys, "executable", "/usr/bin/python3")
        assert update._is_uv_tool_install() is False

    def test_rejects_pip_install(self, monkeypatch):
        monkeypatch.setattr(
            update.sys, "executable",
            "/Users/foo/.venv/bin/python",
        )
        assert update._is_uv_tool_install() is False

    def test_rejects_path_with_segments_out_of_order(self, monkeypatch):
        # Adjacency check guards against substring-style false positives.
        monkeypatch.setattr(
            update.sys, "executable",
            "/home/uv/foo/tools/gdoc/bin/python",
        )
        assert update._is_uv_tool_install() is False


class TestAutoUpdateForHelpSkips:
    def test_skips_when_opt_out(self, monkeypatch, fake_uv_install):
        monkeypatch.setenv("GDOC_AUTO_UPDATE", "0")
        called = []
        monkeypatch.setattr(update, "_latest_version", lambda: called.append(1))
        update.auto_update_for_help()
        assert called == []

    def test_skips_when_recursion_guard_set(self, monkeypatch, fake_uv_install):
        monkeypatch.setenv("GDOC_SKIP_UPDATE_CHECK", "1")
        called = []
        monkeypatch.setattr(update, "_latest_version", lambda: called.append(1))
        update.auto_update_for_help()
        assert called == []

    def test_skips_when_not_uv_install(self, monkeypatch):
        monkeypatch.setattr(update, "_is_uv_tool_install", lambda: False)
        called = []
        monkeypatch.setattr(update, "_latest_version", lambda: called.append(1))
        update.auto_update_for_help()
        assert called == []

    def test_skips_when_already_up_to_date(
        self, monkeypatch, fake_uv_install, cache_file
    ):
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(update, "_latest_version", lambda: "1.2.3")
        monkeypatch.setattr(update, "_installed_version", lambda: "1.2.3")
        called = []
        monkeypatch.setattr(
            update.subprocess, "run",
            lambda *a, **kw: called.append(a) or subprocess.CompletedProcess(a, 0),
        )
        update.auto_update_for_help()
        assert called == []

    def test_skips_when_offline(self, monkeypatch, fake_uv_install, cache_file):
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(update, "_latest_version", lambda: None)
        called = []
        monkeypatch.setattr(
            update.subprocess, "run",
            lambda *a, **kw: called.append(a) or subprocess.CompletedProcess(a, 0),
        )
        update.auto_update_for_help()
        assert called == []

    def test_uses_cache_when_within_throttle(
        self, monkeypatch, fake_uv_install, cache_file
    ):
        import time
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "latest_version": "1.2.3",
            "checked_at": time.time(),  # fresh
        }))
        fetched = []
        monkeypatch.setattr(
            update, "_latest_version", lambda: fetched.append(1) or "9.9.9",
        )
        monkeypatch.setattr(update, "_installed_version", lambda: "1.2.3")
        update.auto_update_for_help()
        assert fetched == []  # used cache, didn't hit network


class TestAutoUpdateForHelpUpgrades:
    def test_runs_upgrade_when_newer(
        self, monkeypatch, fake_uv_install, cache_file, capsys
    ):
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(update, "_latest_version", lambda: "2.0.0")
        monkeypatch.setattr(update, "_installed_version", lambda: "1.0.0")
        ran = []
        monkeypatch.setattr(
            update.subprocess, "run",
            lambda *a, **kw: ran.append(a[0]) or subprocess.CompletedProcess(a, 0),
        )
        execed = []
        monkeypatch.setattr(
            update.os, "execvpe",
            lambda *a, **kw: execed.append(a),
        )
        update.auto_update_for_help()

        assert any("uv" in cmd and "tool" in cmd and "install" in cmd for cmd in ran)
        assert len(execed) == 1
        assert execed[0][0] == "gdoc"
        # Recursion guard set in env
        assert execed[0][2]["GDOC_SKIP_UPDATE_CHECK"] == "1"

        captured = capsys.readouterr()
        assert "1.0.0" in captured.err and "2.0.0" in captured.err

    def test_silent_skip_when_upgrade_fails(
        self, monkeypatch, fake_uv_install, cache_file, capsys
    ):
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(update, "_latest_version", lambda: "2.0.0")
        monkeypatch.setattr(update, "_installed_version", lambda: "1.0.0")
        monkeypatch.setattr(
            update.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 1),  # nonzero
        )
        execed = []
        monkeypatch.setattr(
            update.os, "execvpe",
            lambda *a, **kw: execed.append(a),
        )
        update.auto_update_for_help()

        assert execed == []  # no re-exec on failure
        captured = capsys.readouterr()
        assert "WARN" in captured.err


class TestTopLevelHelpDetection:
    """Tests for _is_top_level_help_invocation in cli.py."""

    def test_no_args_is_top_level(self):
        from gdoc.cli import _is_top_level_help_invocation
        assert _is_top_level_help_invocation(["gdoc"]) is True

    def test_dashdash_help_is_top_level(self):
        from gdoc.cli import _is_top_level_help_invocation
        assert _is_top_level_help_invocation(["gdoc", "--help"]) is True

    def test_dash_h_is_top_level(self):
        from gdoc.cli import _is_top_level_help_invocation
        assert _is_top_level_help_invocation(["gdoc", "-h"]) is True

    def test_subcommand_help_is_not_top_level(self):
        from gdoc.cli import _is_top_level_help_invocation
        assert _is_top_level_help_invocation(["gdoc", "cat", "--help"]) is False

    def test_subcommand_alone_is_not_top_level(self):
        from gdoc.cli import _is_top_level_help_invocation
        assert _is_top_level_help_invocation(["gdoc", "cat", "DOC"]) is False

    def test_version_is_not_top_level(self):
        from gdoc.cli import _is_top_level_help_invocation
        assert _is_top_level_help_invocation(["gdoc", "--version"]) is False
