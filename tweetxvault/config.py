"""Config models and XDG path helpers."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

APP_NAME = "tweetxvault"
API_BASE_URL = "https://x.com/i/api/graphql"
CLIENT_WEB_BUNDLE_BASE = "https://abs.twimg.com/responsive-web/client-web"
DISCOVERY_PAGE_URL = "https://x.com/?lang=en"
BUNDLE_URL_REGEX = r"https://abs\.twimg\.com/responsive-web/client-web/[A-Za-z0-9_.~-]+\.js"
PUBLIC_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16c"
    "HjhLTvJu4FA33AGWWjCpTnA"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
CONFIG_FILENAME = "config.toml"
QUERY_ID_CACHE_FILENAME = "query-ids.json"
LOCK_FILENAME = "sync.lock"
DB_DIRNAME = "archive.lancedb"


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth_token: str | None = None
    ct0: str | None = None
    user_id: str | None = None
    browser: str | None = None
    browser_profile: str | None = None
    browser_profile_path: str | None = None
    firefox_profile_path: str | None = None


class SyncConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_delay: float = Field(default=2.0, ge=0)
    detail_delay: float = Field(default=0.0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base: float = Field(default=2.0, ge=0)
    detail_max_retries: int = Field(default=2, ge=0)
    detail_backoff_base: float = Field(default=30.0, ge=0)
    cooldown_threshold: int = Field(default=3, ge=1)
    cooldown_duration: float = Field(default=300.0, ge=0)
    timeout: float = Field(default=30.0, ge=1.0)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth: AuthConfig = Field(default_factory=AuthConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)


class XDGPaths(BaseModel):
    """Resolved application paths."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_dir: Path
    data_dir: Path
    cache_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / CONFIG_FILENAME

    @property
    def query_id_cache_file(self) -> Path:
        return self.cache_dir / QUERY_ID_CACHE_FILENAME

    @property
    def lock_file(self) -> Path:
        return self.data_dir / LOCK_FILENAME

    @property
    def database_path(self) -> Path:
        return self.data_dir / DB_DIRNAME

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def database_file(self) -> Path:
        """Backward-compatible alias for older callers/tests."""
        return self.database_path


def resolve_paths(env: Mapping[str, str] | None = None) -> XDGPaths:
    import platformdirs

    env = env or os.environ
    # Allow explicit env-var overrides; otherwise use platformdirs for
    # cross-platform defaults (XDG on Linux, ~/Library on macOS, %APPDATA% on Windows).
    if raw := env.get("XDG_CONFIG_HOME"):
        config_dir = Path(raw).expanduser() / APP_NAME
    else:
        config_dir = Path(platformdirs.user_config_dir(APP_NAME))

    if raw := env.get("XDG_DATA_HOME"):
        data_dir = Path(raw).expanduser() / APP_NAME
    else:
        data_dir = Path(platformdirs.user_data_dir(APP_NAME))

    if raw := env.get("XDG_CACHE_HOME"):
        cache_dir = Path(raw).expanduser() / APP_NAME
    else:
        cache_dir = Path(platformdirs.user_cache_dir(APP_NAME))

    return XDGPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
    )


def ensure_paths(paths: XDGPaths) -> XDGPaths:
    for path in (paths.config_dir, paths.data_dir, paths.cache_dir):
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    return loaded if isinstance(loaded, dict) else {}


def _env_float(env: Mapping[str, str], name: str) -> float | None:
    value = env.get(name)
    return float(value) if value is not None else None


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    value = env.get(name)
    return int(value) if value is not None else None


def load_config(env: Mapping[str, str] | None = None) -> tuple[AppConfig, XDGPaths]:
    env = env or os.environ
    paths = ensure_paths(resolve_paths(env))
    raw = _load_config_file(paths.config_file)
    config = AppConfig.model_validate(raw)

    auth_updates = {
        "auth_token": env.get("TWEETXVAULT_AUTH_TOKEN"),
        "ct0": env.get("TWEETXVAULT_CT0"),
        "user_id": env.get("TWEETXVAULT_USER_ID"),
        "browser": env.get("TWEETXVAULT_BROWSER"),
        "browser_profile": env.get("TWEETXVAULT_BROWSER_PROFILE"),
        "browser_profile_path": env.get("TWEETXVAULT_BROWSER_PROFILE_PATH"),
        "firefox_profile_path": env.get("TWEETXVAULT_FIREFOX_PROFILE_PATH"),
    }
    auth_updates = {key: value for key, value in auth_updates.items() if value is not None}
    sync_updates = {
        "page_delay": _env_float(env, "TWEETXVAULT_PAGE_DELAY"),
        "detail_delay": _env_float(env, "TWEETXVAULT_DETAIL_DELAY"),
        "max_retries": _env_int(env, "TWEETXVAULT_MAX_RETRIES"),
        "backoff_base": _env_float(env, "TWEETXVAULT_BACKOFF_BASE"),
        "detail_max_retries": _env_int(env, "TWEETXVAULT_DETAIL_MAX_RETRIES"),
        "detail_backoff_base": _env_float(env, "TWEETXVAULT_DETAIL_BACKOFF_BASE"),
        "cooldown_threshold": _env_int(env, "TWEETXVAULT_COOLDOWN_THRESHOLD"),
        "cooldown_duration": _env_float(env, "TWEETXVAULT_COOLDOWN_DURATION"),
        "timeout": _env_float(env, "TWEETXVAULT_TIMEOUT"),
    }
    sync_updates = {key: value for key, value in sync_updates.items() if value is not None}

    if auth_updates:
        config.auth = config.auth.model_copy(update=auth_updates)
    if sync_updates:
        config.sync = config.sync.model_copy(update=sync_updates)
    return config, paths
