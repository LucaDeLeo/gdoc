"""Tests for gdoc.format: output mode selection and formatting."""

import json
from types import SimpleNamespace

from gdoc.format import format_error, format_success, get_output_mode


class TestGetOutputMode:
    def test_json_mode(self):
        args = SimpleNamespace(json=True, verbose=False)
        assert get_output_mode(args) == "json"

    def test_verbose_mode(self):
        args = SimpleNamespace(json=False, verbose=True)
        assert get_output_mode(args) == "verbose"

    def test_terse_mode(self):
        args = SimpleNamespace(json=False, verbose=False)
        assert get_output_mode(args) == "terse"

    def test_missing_attributes_default_terse(self):
        args = SimpleNamespace()
        assert get_output_mode(args) == "terse"


class TestFormatSuccess:
    def test_terse_returns_plain(self):
        assert format_success("done") == "done"

    def test_json_returns_valid_json(self):
        result = format_success("done", mode="json")
        parsed = json.loads(result)
        assert parsed == {"ok": True, "message": "done"}

    def test_verbose_returns_plain(self):
        assert format_success("done", mode="verbose") == "done"


class TestFormatError:
    def test_prefixes_with_err(self):
        assert format_error("something failed") == "ERR: something failed"

    def test_empty_message(self):
        assert format_error("") == "ERR: "
