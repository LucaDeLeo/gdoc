"""Tests for update.py version comparison."""

from unittest.mock import patch

from gdoc.update import _is_newer, _version_tuple, run_update


class TestVersionTuple:
    def test_simple(self):
        assert _version_tuple("0.7.6") == (0, 7, 6)

    def test_double_digit_parts(self):
        assert _version_tuple("0.10.2") == (0, 10, 2)


class TestIsNewer:
    def test_newer(self):
        assert _is_newer("0.8.0", "0.7.6") is True

    def test_older(self):
        assert _is_newer("0.7.6", "0.8.0") is False

    def test_equal(self):
        assert _is_newer("0.8.0", "0.8.0") is False

    def test_numeric_not_lexicographic(self):
        # String comparison would call 0.9.9 newer than 0.10.0
        assert _is_newer("0.10.0", "0.9.9") is True
        assert _is_newer("0.9.9", "0.10.0") is False


class TestRunUpdateStaleRemote:
    @patch("gdoc.update._write_cache")
    @patch("gdoc.update.subprocess.run")
    @patch("gdoc.update._latest_version", return_value="0.7.6")
    @patch("gdoc.update._installed_version", return_value="0.8.0")
    def test_no_downgrade_when_remote_is_older(
        self, _cur, _latest, mock_run, _cache, capsys
    ):
        # Stale GitHub raw cache reports an older version than installed:
        # must say up to date, never downgrade.
        rc = run_update()
        assert rc == 0
        assert "Already up to date." in capsys.readouterr().out
        mock_run.assert_not_called()

    @patch("gdoc.update._write_cache")
    @patch("gdoc.update.subprocess.run")
    @patch("gdoc.update._latest_version", return_value="0.8.1")
    @patch("gdoc.update._installed_version", return_value="0.8.0")
    def test_updates_when_remote_is_newer(
        self, _cur, _latest, mock_run, _cache, capsys
    ):
        mock_run.return_value.returncode = 0
        rc = run_update()
        assert rc == 0
        assert "Updating: 0.8.0 → 0.8.1" in capsys.readouterr().out
        mock_run.assert_called_once()
