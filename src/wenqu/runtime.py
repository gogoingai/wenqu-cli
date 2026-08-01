"""Installation and diagnostic routines for managed Wenqu capabilities."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .config import WenquPaths, configuration_status, ensure_directories


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def library_doctor(target: WenquPaths) -> dict[str, Any]:
    """Return machine-readable health information without changing state."""
    components = {
        "wenqu-cli": {"available": True, "version": package_version("wenqu-cli")},
        "crawl4ai": {"available": package_version("crawl4ai") is not None, "version": package_version("crawl4ai")},
        "playwright": {"available": package_version("playwright") is not None, "version": package_version("playwright")},
    }
    browser_path = _chromium_path() if components["playwright"]["available"] else None
    components["chromium"] = {"available": bool(browser_path and browser_path.exists()), "version": None}
    return {
        "paths": _path_dict(target),
        "components": components,
        "ready": all(component["available"] for component in components.values()),
        "configuration": configuration_status(target),
    }


def setup_library(target: WenquPaths, *, install_browser: bool, dry_run: bool) -> dict[str, Any]:
    """Prepare persistent directories and the browser required by Crawl4AI.

    Python dependencies are package dependencies of ``wenqu-cli`` and are
    installed with Wenqu itself. The browser is deliberately downloaded only by
    this explicit setup command because it is a substantial side effect.
    """
    commands: list[list[str]] = []
    if install_browser:
        commands.append([sys.executable, "-m", "playwright", "install", "chromium"])
    if dry_run:
        return {"dryRun": True, "commands": commands, "paths": _path_dict(target)}

    ensure_directories(target)
    completed: list[list[str]] = []
    for command in commands:
        subprocess.run(command, check=True)
        completed.append(command)
    state = {
        "library": {
            "configured": True,
            "browserInstalled": install_browser,
            "crawl4aiVersion": package_version("crawl4ai"),
        }
    }
    target.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"dryRun": False, "commands": completed, "paths": _path_dict(target), "doctor": library_doctor(target)}


def _path_dict(target: WenquPaths) -> dict[str, str]:
    return {"config": str(target.config_dir), "data": str(target.data_dir), "cache": str(target.cache_dir)}


def _chromium_path() -> Path | None:
    """Find Playwright's Chromium cache without starting the Playwright driver.

    Starting and immediately stopping the driver created a noisy TargetClosedError
    on current Playwright versions. The dry-run installer reports the exact cache
    directory without downloading or launching a browser.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--dry-run"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"Install location:\s+(.+)", result.stdout)
    return Path(match.group(1).strip()) if match else None
