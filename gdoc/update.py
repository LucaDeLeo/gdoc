"""Update checker for gdoc."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

_GITHUB_REPO = "LucaDeLeo/gdoc"
_PACKAGE_NAME = "gdoc"
_CACHE_FILE = Path.home() / ".config" / "gdoc" / "update_check.json"
_CHANGELOG_URL = f"https://github.com/{_GITHUB_REPO}/blob/main/CHANGELOG.md"
_AUTO_UPDATE_THROTTLE_SECONDS = 3600  # 1h
_NOTICE_THROTTLE_SECONDS = 86400  # 24h
_UV_INSTALL_TIMEOUT_SECONDS = 60

_ENV_AUTO_UPDATE = "GDOC_AUTO_UPDATE"
_ENV_SKIP_CHECK = "GDOC_SKIP_UPDATE_CHECK"


def _installed_version() -> str:
    from importlib.metadata import version
    return version(_PACKAGE_NAME)


def _latest_version() -> str | None:
    """Fetch latest version from GitHub (3s timeout)."""
    url = f"https://raw.githubusercontent.com/{_GITHUB_REPO}/main/pyproject.toml"
    try:
        with urlopen(url, timeout=3) as resp:
            content = resp.read().decode()
        match = re.search(r'version\s*=\s*"([^"]+)"', content)
        return match.group(1) if match else None
    except Exception:
        return None


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse the numeric parts of a version string for comparison."""
    return tuple(int(p) for p in re.findall(r"\d+", version))


def _is_newer(latest: str, current: str) -> bool:
    """True if latest is strictly newer than current.

    Plain inequality would offer an "update" to an older version whenever
    the GitHub raw cache lags behind the installed build (and `gdoc update`
    would actually downgrade).
    """
    return _version_tuple(latest) > _version_tuple(current)


def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


def _write_cache(latest: str) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "latest_version": latest,
            "checked_at": time.time(),
        }))
    except Exception:
        pass


def _get_latest_cached(throttle_seconds: int) -> str | None:
    """Return latest version, fetching from network only when cache is stale."""
    cache = _read_cache()
    if time.time() - cache.get("checked_at", 0) < throttle_seconds:
        return cache.get("latest_version")
    latest = _latest_version()
    if latest:
        _write_cache(latest)
    return latest


def check_for_update() -> None:
    """Print a notice to stderr if an update is available. Cached for 24h."""
    try:
        latest = _get_latest_cached(_NOTICE_THROTTLE_SECONDS)
        current = _installed_version()
        if latest and _is_newer(latest, current):
            pinned = pinned_install_reason()
            action = (
                f"This install is {pinned}; reinstall deliberately to update."
                if pinned else "Run `gdoc update` to update."
            )
            print(
                f"Update available: {current} → {latest}. "
                f"{action} "
                f"Changelog: {_CHANGELOG_URL}",
                file=sys.stderr,
            )
    except Exception:
        pass


def _install_origin() -> dict | None:
    """The installed distribution's PEP 610 ``direct_url.json``, or None.

    None means an index install or unreadable metadata: the source is
    unknown, so behavior stays as it always was.
    """
    try:
        from importlib.metadata import distribution

        text = distribution(_PACKAGE_NAME).read_text("direct_url.json")
        data = json.loads(text) if text else None
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _strip_userinfo(url: str) -> str:
    """The URL with any ``user:password@``, query and fragment removed, so it
    is safe to print (tokens can ride in any of them)."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host if "@" in parts.netloc else parts.netloc
        return urlunsplit(parts._replace(netloc=netloc, query="", fragment=""))
    except ValueError:  # e.g. a non-numeric port
        return "an unrecognized source"


def _repo_key(url: str) -> str | None:
    """Host, port and path of an https or ssh repository URL, ignoring user,
    case, a trailing slash and a ``.git`` suffix; None for anything else."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("https", "ssh"):
        return None
    path = parts.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    host = (parts.hostname or "").lower()
    if port is not None and port not in (443, 22):
        host = f"{host}:{port}"
    return f"{host}{path.lower()}"


def pinned_install_reason() -> str | None:
    """Why this install must not be replaced from upstream main, or None.

    An install that requested a revision (``git+URL@sha``, a tag or a
    branch) or came from another source (a fork, a local checkout) was a
    deliberate choice. Auto-update and ``gdoc update`` reinstall from
    upstream main, which would silently undo that choice: for a tool holding
    a Drive token, a pin is how a user controls what code runs. Metadata
    that can't be understood counts as pinned: skipping an update is the
    safe failure.
    """
    origin = _install_origin()
    if origin is None:
        return None
    try:
        url = origin.get("url")
        if not isinstance(url, str):
            return None
        vcs = origin.get("vcs_info")
        if isinstance(vcs, dict) and vcs.get("requested_revision"):
            return f"pinned to {vcs['requested_revision']}"
        key = _repo_key(url)
        if (not isinstance(vcs, dict) or key is None
                or key != _repo_key(f"https://github.com/{_GITHUB_REPO}")):
            return f"installed from {_strip_userinfo(url)}"
        return None
    except Exception:  # noqa: BLE001 — never let metadata break help output
        return "installed from an unrecognized source"


def _is_uv_tool_install() -> bool:
    """uv tool install lays out interpreters at `.../uv/tools/<pkg>/...`.

    Adjacency check avoids false positives from paths that happen to
    contain those segments out of order.
    """
    parts = Path(sys.executable).resolve().parts
    for i in range(len(parts) - 2):
        if (
            parts[i] == "uv"
            and parts[i + 1] == "tools"
            and parts[i + 2] == _PACKAGE_NAME
        ):
            return True
    return False


def auto_update_for_help() -> None:
    """Block on `uv tool install --force` if a newer version exists, then re-exec.

    Called before argparse for top-level help requests so agents inspecting
    the CLI surface get the freshest help text. Silent on every failure mode
    (offline, non-uv install, upgrade error) — never blocks help output.
    Pinned installs (a requested revision, a fork, a local checkout) are
    never replaced: see ``pinned_install_reason``.
    """
    if os.environ.get(_ENV_AUTO_UPDATE, "1") == "0":
        return
    if os.environ.get(_ENV_SKIP_CHECK) == "1":
        return
    if not _is_uv_tool_install():
        return
    if pinned_install_reason():
        return

    try:
        latest = _get_latest_cached(_AUTO_UPDATE_THROTTLE_SECONDS)
        if not latest:
            return
        current = _installed_version()
        if not _is_newer(latest, current):
            return
    except Exception:
        return

    print(f"Updating gdoc: {current} → {latest}...", file=sys.stderr)
    try:
        result = subprocess.run(
            ["uv", "tool", "install", "--force",
             f"git+https://github.com/{_GITHUB_REPO}.git"],
            capture_output=True,
            timeout=_UV_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            "WARN: gdoc auto-update timed out; continuing with current version",
            file=sys.stderr,
        )
        return
    except Exception:
        print(
            "WARN: gdoc auto-update failed; continuing with current version",
            file=sys.stderr,
        )
        return
    if result.returncode != 0:
        print(
            "WARN: gdoc auto-update failed; continuing with current version",
            file=sys.stderr,
        )
        stderr_text = (result.stderr or b"").decode(errors="replace").strip()
        if stderr_text:
            print(stderr_text, file=sys.stderr)
        return
    print(f"✓ updated to v{latest}", file=sys.stderr)

    env = os.environ.copy()
    env[_ENV_SKIP_CHECK] = "1"
    os.execvpe("gdoc", ["gdoc", *sys.argv[1:]], env)


def run_update() -> int:
    """Check for and install updates."""
    current = _installed_version()
    print(f"Current version: {current}")
    print("Checking for updates...")
    latest = _latest_version()
    if latest is None:
        print("Could not check for updates. Are you online?")
        return 1
    if not _is_newer(latest, current):
        print("Already up to date.")
        _write_cache(latest)
        return 0
    pinned = pinned_install_reason()
    if pinned:
        print(f"Update available: {current} → {latest}.")
        print(
            f"Not replacing this install: it is {pinned}, and `gdoc update` "
            "would reinstall from upstream main."
        )
        print("To switch to the latest upstream release deliberately:")
        print(f"  uv tool install --force git+https://github.com/{_GITHUB_REPO}.git")
        print(f"Changelog: {_CHANGELOG_URL}")
        return 1
    print(f"Updating: {current} → {latest}")
    result = subprocess.run(
        ["uv", "tool", "install", "--force",
         f"git+https://github.com/{_GITHUB_REPO}.git"],
    )
    if result.returncode == 0:
        print(f"\nUpdated to v{latest}.")
        print(f"Changelog: {_CHANGELOG_URL}")
        _write_cache(latest)
        return 0
    else:
        print("\nUpdate failed. Try manually:")
        print(f"  uv tool install --force git+https://github.com/{_GITHUB_REPO}.git")
        return 1
