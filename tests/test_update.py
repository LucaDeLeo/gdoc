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


UPSTREAM_UNPINNED = {
    "url": "https://github.com/LucaDeLeo/gdoc.git",
    "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
}


@pytest.fixture
def fake_uv_install(monkeypatch):
    """Make _is_uv_tool_install() return True, installed from upstream main."""
    monkeypatch.setattr(update, "_is_uv_tool_install", lambda: True)
    monkeypatch.setattr(update, "_install_origin", lambda: UPSTREAM_UNPINNED)


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


def _origin(url="https://github.com/LucaDeLeo/gdoc.git", rev=None, **extra):
    vcs = {"vcs": "git", "commit_id": "b" * 40}
    if rev is not None:
        vcs["requested_revision"] = rev
    return {"url": url, "vcs_info": vcs, **extra}


class TestPinnedInstallReason:
    """A deliberate install source must never be swapped for upstream main."""

    @pytest.mark.parametrize("origin", [
        UPSTREAM_UNPINNED,
        _origin(url="https://github.com/LucaDeLeo/gdoc"),
        _origin(url="https://github.com/lucadeleo/gdoc.git/"),
        _origin(url="ssh://git@github.com/LucaDeLeo/gdoc.git"),
        None,  # index or unknown install: unchanged behavior
    ])
    def test_upstream_main_is_not_pinned(self, monkeypatch, origin):
        monkeypatch.setattr(update, "_install_origin", lambda: origin)
        assert update.pinned_install_reason() is None

    def test_requested_commit_is_pinned(self, monkeypatch):
        monkeypatch.setattr(update, "_install_origin", lambda: _origin(rev="8bfe7f9"))
        assert update.pinned_install_reason() == "pinned to 8bfe7f9"

    def test_requested_tag_or_branch_is_pinned(self, monkeypatch):
        monkeypatch.setattr(update, "_install_origin", lambda: _origin(rev="v0.24.0"))
        assert "v0.24.0" in update.pinned_install_reason()

    def test_fork_is_pinned_even_without_revision(self, monkeypatch):
        monkeypatch.setattr(
            update, "_install_origin",
            lambda: _origin(url="https://github.com/someone/gdoc.git"),
        )
        assert update.pinned_install_reason() == (
            "installed from https://github.com/someone/gdoc.git"
        )

    def test_local_directory_install_is_pinned(self, monkeypatch):
        monkeypatch.setattr(
            update, "_install_origin",
            lambda: {"url": "file:///home/me/gdoc", "dir_info": {}},
        )
        assert update.pinned_install_reason() == (
            "installed from file:///home/me/gdoc"
        )

    def test_credentials_in_url_are_never_shown(self, monkeypatch):
        monkeypatch.setattr(
            update, "_install_origin",
            lambda: _origin(url="https://user:s3cret@example.com/x/gdoc.git"),
        )
        reason = update.pinned_install_reason()
        assert "s3cret" not in reason and "user" not in reason
        assert reason == "installed from https://example.com/x/gdoc.git"

    def test_reads_pep610_direct_url_of_installed_dist(self, monkeypatch):
        class Dist:
            def read_text(self, name):
                assert name == "direct_url.json"
                return json.dumps(_origin(rev="abc123"))

        monkeypatch.setattr(
            "importlib.metadata.distribution", lambda name: Dist()
        )
        assert update._install_origin()["vcs_info"]["requested_revision"] == (
            "abc123"
        )

    def test_unreadable_metadata_is_unknown(self, monkeypatch):
        def boom(name):
            raise RuntimeError("no metadata")

        monkeypatch.setattr("importlib.metadata.distribution", boom)
        assert update._install_origin() is None


class TestPinnedInstallsAreNotReplaced:
    @pytest.fixture
    def pinned(self, monkeypatch):
        monkeypatch.setattr(update, "_is_uv_tool_install", lambda: True)
        monkeypatch.setattr(update, "_install_origin", lambda: _origin(rev="8bfe7f9"))
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(update, "_latest_version", lambda: "2.0.0")
        monkeypatch.setattr(update, "_installed_version", lambda: "1.0.0")
        ran = []
        monkeypatch.setattr(
            update.subprocess, "run",
            lambda *a, **kw: ran.append(a[0]) or subprocess.CompletedProcess(a, 0),
        )
        monkeypatch.setattr(
            update.os, "execvpe",
            lambda *a, **kw: pytest.fail("pinned install must not re-exec"),
        )
        return ran

    def test_help_does_not_auto_update_a_pinned_install(
        self, pinned, cache_file, capsys
    ):
        update.auto_update_for_help()
        assert pinned == []
        assert capsys.readouterr().err == ""  # silent, like every other skip

    def test_gdoc_update_declines_to_replace_a_pinned_install(
        self, pinned, cache_file, capsys
    ):
        assert update.run_update() == 1
        assert pinned == []
        out = capsys.readouterr().out
        assert "pinned to 8bfe7f9" in out
        assert "uv tool install --force" in out

    def test_notice_does_not_recommend_gdoc_update_when_pinned(
        self, pinned, cache_file, capsys
    ):
        update.check_for_update()
        err = capsys.readouterr().err
        assert "1.0.0" in err and "2.0.0" in err
        assert "pinned to 8bfe7f9" in err
        assert "Run `gdoc update`" not in err


class TestPinnedInstallUrlEdgeCases:
    @pytest.mark.parametrize("url", [
        "http://github.com/LucaDeLeo/gdoc.git",          # not https or ssh
        "https://github.com:8443/LucaDeLeo/gdoc.git",    # another port
        "https://u:p@host:abc/x/gdoc.git",               # unparseable port
    ])
    def test_unusual_urls_count_as_pinned(self, monkeypatch, url):
        monkeypatch.setattr(update, "_install_origin", lambda: _origin(url=url))
        assert update.pinned_install_reason() is not None

    def test_bad_port_never_breaks_help(self, monkeypatch, cache_file):
        monkeypatch.setattr(update, "_is_uv_tool_install", lambda: True)
        monkeypatch.setattr(
            update, "_install_origin",
            lambda: _origin(url="https://u:p@host:abc/x/gdoc.git"),
        )
        monkeypatch.delenv("GDOC_AUTO_UPDATE", raising=False)
        monkeypatch.delenv("GDOC_SKIP_UPDATE_CHECK", raising=False)
        monkeypatch.setattr(
            update.subprocess, "run",
            lambda *a, **kw: pytest.fail("must not reinstall"),
        )
        update.auto_update_for_help()  # no exception, no reinstall

    def test_query_and_fragment_secrets_are_never_shown(self, monkeypatch):
        monkeypatch.setattr(
            update, "_install_origin",
            lambda: _origin(url="https://example.com/x/gdoc.git?token=SECRET#frag"),
        )
        reason = update.pinned_install_reason()
        assert "SECRET" not in reason and "frag" not in reason
        assert reason == "installed from https://example.com/x/gdoc.git"

    def test_explicit_default_ports_are_still_upstream(self, monkeypatch):
        monkeypatch.setattr(
            update, "_install_origin",
            lambda: _origin(url="https://github.com:443/LucaDeLeo/gdoc.git"),
        )
        assert update.pinned_install_reason() is None
