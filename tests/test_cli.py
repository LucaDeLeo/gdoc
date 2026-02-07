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
    def test_edit_stub(self):
        result = run_gdoc("edit", "doc123", "old", "new")
        assert result.returncode == 4
        assert "ERR: edit is not yet implemented" in result.stderr


class TestMutuallyExclusiveFlags:
    def test_json_and_verbose_conflict(self):
        result = run_gdoc("--json", "--verbose", "ls")
        assert result.returncode == 3

    def test_json_accepted(self):
        result = run_gdoc("--json", "edit", "doc123", "old", "new")
        assert result.returncode == 4  # stub runs, flag accepted

    def test_verbose_accepted(self):
        result = run_gdoc("--verbose", "edit", "doc123", "old", "new")
        assert result.returncode == 4  # stub runs, flag accepted

    def test_json_after_subcommand(self):
        result = run_gdoc("edit", "1aBcDeFg", "old", "new", "--json")
        assert result.returncode == 4  # stub runs, flag accepted

    def test_verbose_after_subcommand(self):
        result = run_gdoc("edit", "doc123", "old", "new", "--verbose")
        assert result.returncode == 4  # stub runs, flag accepted

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


class TestErrorFormat:
    def test_stub_error_prefix(self):
        result = run_gdoc("edit", "doc123", "old", "new")
        assert result.stderr.startswith("ERR: ")

    def test_usage_error_prefix(self):
        result = run_gdoc("cat")
        assert "ERR: " in result.stderr
