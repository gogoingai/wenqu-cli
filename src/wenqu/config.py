"""Paths and non-secret configuration for Wenqu runtime state."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any


@dataclass(frozen=True)
class WenquPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def credentials_file(self) -> Path:
        return self.config_dir / "credentials.env"

def paths(environment: dict[str, str] | None = None) -> WenquPaths:
    env = environment or dict(os.environ)
    home = Path(env.get("HOME", str(Path.home())))
    root = home / ".gogoingai" / "wenqu-skills"
    return WenquPaths(root, root / "runtime", root / "cache")


def ensure_directories(target: WenquPaths) -> None:
    for directory in (target.config_dir, target.data_dir, target.cache_dir):
        directory.mkdir(parents=True, exist_ok=True)


def load_config(target: WenquPaths) -> dict[str, Any]:
    if not target.config_file.exists():
        return {}
    try:
        decoded = json.loads(target.config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read Wenqu configuration: {error}") from error
    if not isinstance(decoded, dict):
        raise ValueError("Wenqu configuration must be a JSON object.")
    return decoded


def initialize_config(target: WenquPaths, *, dry_run: bool = False) -> dict[str, object]:
    """Create safe, user-editable configuration files without storing a secret."""
    created: list[str] = []
    if target.config_file.exists():
        config_action = "exists"
    else:
        config_action = "create"
        if not dry_run:
            target.config_dir.mkdir(parents=True, exist_ok=True)
            target.config_file.write_text(
                json.dumps({"library": {"proxyUrl": None}}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            created.append(str(target.config_file))
    if target.credentials_file.exists():
        credentials_action = "exists"
    else:
        credentials_action = "create"
        if not dry_run:
            target.config_dir.mkdir(parents=True, exist_ok=True)
            target.credentials_file.write_text(
                "# Optional credentials for Wenqu. Keep this file private.\n# EXA_API_KEY=...\n",
                encoding="utf-8",
            )
            target.credentials_file.chmod(0o600)
            created.append(str(target.credentials_file))
    return {
        "dryRun": dry_run,
        "configFile": str(target.config_file),
        "credentialsFile": str(target.credentials_file),
        "configAction": config_action,
        "credentialsAction": credentials_action,
        "created": created,
    }


def load_credentials(target: WenquPaths, environment: dict[str, str] | None = None) -> dict[str, str]:
    """Load known optional credentials without ever printing their values."""
    loaded: dict[str, str] = {}
    if target.credentials_file.exists():
        mode = stat.S_IMODE(target.credentials_file.stat().st_mode)
        if mode & 0o077:
            raise ValueError(f"Credentials file must be readable only by its owner: chmod 600 {target.credentials_file}")
        for source_line in target.credentials_file.read_text(encoding="utf-8").splitlines():
            line = source_line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator != "=" or key not in {"EXA_API_KEY"} or not value.strip():
                continue
            loaded[key] = value.strip().strip("\"'")
    env = environment or dict(os.environ)
    for key in ("EXA_API_KEY",):
        if env.get(key):
            loaded[key] = env[key]
    return loaded


def configuration_status(target: WenquPaths, environment: dict[str, str] | None = None) -> dict[str, object]:
    credentials = load_credentials(target, environment)
    exa_configured = bool(credentials.get("EXA_API_KEY"))
    return {
        "configFile": str(target.config_file),
        "credentialsFile": str(target.credentials_file),
        "configExists": target.config_file.exists(),
        "credentialsFileExists": target.credentials_file.exists(),
        "optionalEngines": {
            "exa": {
                "configured": exa_configured,
                "configure": None if exa_configured else f"Run `wenqu config init`, then set EXA_API_KEY in {target.credentials_file}.",
            }
        },
    }


def library_proxy(target: WenquPaths, environment: dict[str, str] | None = None) -> str | None:
    env = environment or dict(os.environ)
    if env.get("WENQU_PROXY_URL"):
        return env["WENQU_PROXY_URL"]
    library = load_config(target).get("library", {})
    if not isinstance(library, dict):
        return None
    proxy = library.get("proxyUrl")
    return proxy if isinstance(proxy, str) and proxy else None
