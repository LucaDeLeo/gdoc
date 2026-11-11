"""UTF-8 console output regardless of the platform's locale code page.

On Windows a piped or redirected stdout/stderr defaults to the ANSI code page
(cp1252 and friends), so the awareness banner's emoji came out as literal
``\\U0001f4c4`` escapes and ``cat --plain`` mangled en dashes.
"""

import io
import os
import subprocess
import sys
from pathlib import Path

from gdoc.cli import _ensure_utf8_stdio

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def _legacy_stream(errors: str = "strict") -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors=errors)


def install_legacy_stdio(monkeypatch):
    """Swap in cp1252 streams. Call from the test body: pytest's capture
    plugin re-installs its own sys streams after fixture setup."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    streams = {
        "stdin": io.TextIOWrapper(
            io.BytesIO("en – dash\n".encode()), encoding="cp1252"
        ),
        "stdout": _legacy_stream("strict"),
        "stderr": _legacy_stream("backslashreplace"),
    }
    for name, stream in streams.items():
        monkeypatch.setattr(sys, name, stream)
    return streams


def test_reconfigures_legacy_streams_to_utf8(monkeypatch):
    legacy_stdio = install_legacy_stdio(monkeypatch)
    _ensure_utf8_stdio()

    print("cat – plain", end="")
    print("\U0001f4c4 banner", end="", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()

    assert legacy_stdio["stdout"].buffer.getvalue() == "cat – plain".encode()
    assert legacy_stdio["stderr"].buffer.getvalue() == "\U0001f4c4 banner".encode()
    assert sys.stdin.read() == "en – dash\n"


def test_keeps_each_streams_error_handler(monkeypatch):
    install_legacy_stdio(monkeypatch)
    _ensure_utf8_stdio()

    assert sys.stdout.errors == "strict"
    assert sys.stderr.errors == "backslashreplace"


def test_explicit_pythonioencoding_is_respected(monkeypatch):
    install_legacy_stdio(monkeypatch)
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    _ensure_utf8_stdio()

    assert sys.stdout.encoding == "cp1252"
    assert sys.stderr.encoding == "cp1252"


def test_streams_without_reconfigure_are_left_alone(monkeypatch):
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    plain = io.StringIO()
    monkeypatch.setattr(sys, "stdout", plain)
    _ensure_utf8_stdio()
    assert sys.stdout is plain


def test_subprocess_pipe_gets_utf8_under_legacy_locale():
    """End to end on a real pipe: a non-UTF-8 locale and no PYTHONIOENCODING
    still yields UTF-8 bytes (the agent-harness case)."""
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("PYTHONIOENCODING", "PYTHONUTF8")
    }
    # Force the legacy (non-UTF-8) stdio default on every platform.
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C")
    # Raw string: the child decodes the escapes, so no non-ASCII crosses argv
    # (a C locale would surrogate-escape it on POSIX).
    code = (
        r"import sys; from gdoc.cli import _ensure_utf8_stdio; "
        r"_ensure_utf8_stdio(); "
        r"print('cat \u2013 plain'); "
        r"print('\U0001f4c4 banner', file=sys.stderr)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=REPO_ROOT, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "cat – plain".encode() in result.stdout
    assert "📄 banner".encode() in result.stderr
    assert rb"\U0001f4c4" not in result.stderr


def test_main_sets_up_utf8_before_anything_else(monkeypatch):
    import gdoc.cli as cli

    calls = []
    monkeypatch.setattr(cli, "_ensure_utf8_stdio", lambda: calls.append("utf8"))
    monkeypatch.setattr(cli, "_is_top_level_help_invocation", lambda argv: False)
    monkeypatch.setattr(cli, "run_argv", lambda *a, **k: calls.append("run") or 0)
    assert cli.main() == 0
    assert calls == ["utf8", "run"]
