"""Update checker for gdoc."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

_GITHUB_REPO = "LucaDeLeo/gdoc"
_PACKAGE_NAME = "gdoc"
_CACHE_FILE = Path.home() / ".config" / "gdoc" / "update_check.json"


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


def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
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


def check_for_update() -> None:
    """Print a notice to stderr if an update is available. Cached for 24h."""
    try:
        cache = _read_cache()
        if time.time() - cache.get("checked_at", 0) < 86400:
            latest = cache.get("latest_version")
        else:
            latest = _latest_version()
            if latest:
                _write_cache(latest)
        if latest and latest != _installed_version():
            print(
                f"Update available: {_installed_version()} → {latest}. "
                f"Run `gdoc update` to update.",
                file=sys.stderr,
            )
    except Exception:
        pass


def run_update() -> int:
    """Check for and install updates."""
    current = _installed_version()
    print(f"Current version: {current}")
    print("Checking for updates...")
    latest = _latest_version()
    if latest is None:
        print("Could not check for updates. Are you online?")
        return 1
    if latest == current:
        print("Already up to date.")
        _write_cache(latest)
        return 0
    print(f"Updating: {current} → {latest}")
    result = subprocess.run(
        ["uv", "tool", "install", "--force",
         f"git+https://github.com/{_GITHUB_REPO}.git"],
    )
    if result.returncode == 0:
        print(f"\nUpdated to v{latest}.")
        _write_cache(latest)
        return 0
    else:
        print(f"\nUpdate failed. Try manually:")
        print(f"  uv tool install --force git+https://github.com/{_GITHUB_REPO}.git")
        return 1
