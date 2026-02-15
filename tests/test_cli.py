"""Tests for gdoc.cli: argument parsing, exit codes, and error formatting."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def run_gdoc(*args: str) -> subprocess.CompletedProcess:
    """Run gdoc as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "gdoc", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class TestExitCode3OnUsageErrors:
    def test_no_command(self):
        result = run_gdoc()
        assert result.returncode == 3

    def test_bad_flag(self):
        result = run_gdoc("--bad-flag")
        assert result.returncode == 3

    def test_missing_required_arg(self):
        result = run_gdoc("cat")
        assert result.returncode == 3


class TestExitCode4OnStubs:
    def test_edit_no_longer_stub(self):
        result = run_gdoc("edit", "doc123", "old", "new")
        assert result.returncode != 4  # no longer a stub

    def test_write_no_longer_stub(self):
        result = run_gdoc("write", "doc123", "/tmp/nonexistent.md")
        assert result.returncode != 4  # no longer a stub

    def test_new_no_longer_stub(self):
        result = run_gdoc("new", "Test Title")
        assert result.returncode != 4  # no longer a stub

    def test_cp_no_longer_stub(self):
        result = run_gdoc("cp", "doc123", "Copy Title")
        assert result.returncode != 4  # no longer a stub

    def test_share_no_longer_stub(self):
        result = run_gdoc("share", "doc123", "alice@co.com")
        assert result.returncode != 4  # no longer a stub


class TestMutuallyExclusiveFlags:
    def test_json_and_verbose_conflict(self):
        result = run_gdoc("--json", "--verbose", "ls")
        assert result.returncode == 3

    def test_json_accepted(self):
        result = run_gdoc("--json", "comment", "doc123", "text")
        assert result.returncode != 3  # flag accepted (not a usage error)

    def test_verbose_accepted(self):
        result = run_gdoc("--verbose", "comment", "doc123", "text")
        assert result.returncode != 3  # flag accepted (not a usage error)

    def test_json_after_subcommand(self):
        result = run_gdoc("comment", "1aBcDeFg", "text", "--json")
        assert result.returncode != 3  # flag accepted (not a usage error)

    def test_verbose_after_subcommand(self):
        result = run_gdoc("comment", "doc123", "text", "--verbose")
        assert result.returncode != 3  # flag accepted (not a usage error)

    def test_json_and_verbose_conflict_after_subcommand(self):
        result = run_gdoc("ls", "--json", "--verbose")
        assert result.returncode != 0

    def test_json_before_verbose_after_subcommand_conflict(self):
        result = run_gdoc("--json", "ls", "--verbose")
        assert result.returncode == 3
        assert "ERR:" in result.stderr

    def test_verbose_before_json_after_subcommand_conflict(self):
        result = run_gdoc("--verbose", "ls", "--json")
        assert result.returncode == 3
        assert "ERR:" in result.stderr


class TestHelpText:
    def test_help_exits_0(self):
        result = run_gdoc("--help")
        assert result.returncode == 0
        assert "auth" in result.stdout
        assert "cat" in result.stdout
        assert "edit" in result.stdout

    def test_auth_help_shows_no_browser(self):
        result = run_gdoc("auth", "--help")
        assert result.returncode == 0
        assert "--no-browser" in result.stdout


class TestPlainFlag:
    def test_plain_accepted(self):
        result = run_gdoc("--plain", "comment", "doc123", "text")
        assert result.returncode != 3  # flag accepted

    def test_plain_after_subcommand(self):
        result = run_gdoc("comment", "1aBcDeFg", "text", "--plain")
        assert result.returncode != 3  # flag accepted

    def test_plain_and_json_conflict(self):
        result = run_gdoc("--plain", "--json", "ls")
        assert result.returncode == 3

    def test_plain_and_verbose_conflict(self):
        result = run_gdoc("--plain", "--verbose", "ls")
        assert result.returncode == 3


class TestAllowCommands:
    def test_allow_commands_permits_listed(self):
        result = run_gdoc("--allow-commands", "cat", "cat")
        # cat requires a doc arg → usage error (3), not allowlist
        assert result.returncode == 3
        assert "command not allowed" not in result.stderr

    def test_allow_commands_blocks_unlisted(self):
        result = run_gdoc("--allow-commands", "cat", "edit", "doc123", "old", "new")
        assert result.returncode == 3
        assert "command not allowed: edit" in result.stderr

    def test_allow_commands_env_var(self):
        env_result = subprocess.run(
            [sys.executable, "-m", "gdoc", "edit", "doc123", "old", "new"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**__import__("os").environ, "GDOC_ALLOW_COMMANDS": "cat"},
        )
        assert env_result.returncode == 3
        assert "command not allowed: edit" in env_result.stderr

    def test_allow_commands_empty_allows_all(self):
        result = run_gdoc("--allow-commands", "", "cat")
        # Empty allowlist = no restriction, so cat fails for missing arg, not allowlist
        assert "command not allowed" not in result.stderr


class TestCommentInfoSubcommand:
    def test_comment_info_help(self):
        result = run_gdoc("comment-info", "--help")
        assert result.returncode == 0
        assert "comment_id" in result.stdout

    def test_comment_info_missing_args(self):
        result = run_gdoc("comment-info")
        assert result.returncode == 3


class TestResolveMessageFlag:
    def test_resolve_help_shows_message(self):
        result = run_gdoc("resolve", "--help")
        assert result.returncode == 0
        assert "--message" in result.stdout
        assert "-m" in result.stdout


class TestDeleteCommentForceFlag:
    def test_delete_comment_help_shows_force(self):
        result = run_gdoc("delete-comment", "--help")
        assert result.returncode == 0
        assert "--force" in result.stdout


class TestErrorFormat:
    def test_stub_error_prefix(self):
        result = run_gdoc("comment", "doc123", "text")
        assert "ERR: " in result.stderr

    def test_usage_error_prefix(self):
        result = run_gdoc("cat")
        assert "ERR: " in result.stderr
