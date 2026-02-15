"""Tests for gdoc.util: URL extraction and error classes."""

from unittest.mock import patch

import pytest

from gdoc.util import AuthError, GdocError, confirm_destructive, extract_doc_id


class TestExtractDocId:
    def test_standard_docs_url(self):
        url = "https://docs.google.com/document/d/1aBcDeFg/edit"
        assert extract_doc_id(url) == "1aBcDeFg"

    def test_standard_drive_url(self):
        url = "https://drive.google.com/file/d/1aBcDeFg/view"
        assert extract_doc_id(url) == "1aBcDeFg"

    def test_query_param_url(self):
        url = "https://drive.google.com/open?id=1aBcDeFg"
        assert extract_doc_id(url) == "1aBcDeFg"

    def test_query_param_with_other_params(self):
        url = "https://drive.google.com/uc?export=download&id=1aBcDeFg"
        assert extract_doc_id(url) == "1aBcDeFg"

    def test_url_with_fragment(self):
        url = "https://docs.google.com/document/d/1aBcDeFg/edit#heading=h.abc"
        assert extract_doc_id(url) == "1aBcDeFg"

    def test_bare_document_id(self):
        bare_id = "1aBcDeFgHiJkLmNoPqRsTuVwXyZ"
        assert extract_doc_id(bare_id) == bare_id

    def test_whitespace_around_input(self):
        assert extract_doc_id("  1aBcDeFg  ") == "1aBcDeFg"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty string"):
            extract_doc_id("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty string"):
            extract_doc_id("   ")

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            extract_doc_id("https://example.com/not-a-doc")

    def test_special_characters_raise(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            extract_doc_id("hello world!")

    def test_folder_url(self):
        url = "https://drive.google.com/drive/folders/1aBcDeFg"
        assert extract_doc_id(url) == "1aBcDeFg"

    def test_folder_url_with_params(self):
        url = "https://drive.google.com/drive/folders/abc123?usp=sharing"
        assert extract_doc_id(url) == "abc123"


class TestErrorClasses:
    def test_gdoc_error_default_exit_code(self):
        err = GdocError("test")
        assert err.exit_code == 1
        assert str(err) == "test"

    def test_gdoc_error_custom_exit_code(self):
        err = GdocError("test", exit_code=5)
        assert err.exit_code == 5

    def test_auth_error_exit_code(self):
        err = AuthError("auth failed")
        assert err.exit_code == 2
        assert str(err) == "auth failed"

    def test_auth_error_is_gdoc_error(self):
        assert issubclass(AuthError, GdocError)

    def test_gdoc_error_is_exception(self):
        assert issubclass(GdocError, Exception)


class TestConfirmDestructive:
    def test_force_bypasses_prompt(self):
        confirm_destructive("delete something", force=True)

    def test_non_tty_without_force_raises(self):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(GdocError, match="non-interactive") as exc:
                confirm_destructive("delete something", force=False)
            assert exc.value.exit_code == 3

    def test_user_accepts(self):
        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="y"):
            mock_stdin.isatty.return_value = True
            confirm_destructive("delete something", force=False)

    def test_user_declines(self):
        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="n"):
            mock_stdin.isatty.return_value = True
            with pytest.raises(GdocError, match="Cancelled") as exc:
                confirm_destructive("delete something", force=False)
            assert exc.value.exit_code == 3
