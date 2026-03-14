"""Firefox cookie extraction."""

from __future__ import annotations

import configparser
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict

from tweetxvault.exceptions import AuthResolutionError

FIREFOX_PROFILES_INI = Path.home() / ".mozilla/firefox/profiles.ini"
COOKIE_NAMES = {"auth_token", "ct0", "twid"}
COOKIE_HOSTS = {"x.com", ".x.com", "twitter.com", ".twitter.com"}


class FirefoxCookieBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    auth_token: str | None = None
    ct0: str | None = None
    twid: str | None = None
    user_id: str | None = None
    profile_path: Path


def parse_twid(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    decoded = unquote(raw_value)
    if decoded.startswith("u="):
        candidate = decoded[2:]
        return candidate if candidate.isdigit() else None
    return decoded if decoded.isdigit() else None


def discover_default_profile(
    explicit_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    env = env or os.environ
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise AuthResolutionError(f"Configured Firefox profile does not exist: {path}")
        return path

    profiles_ini = Path(
        env.get("TWEETXVAULT_FIREFOX_PROFILES_INI", FIREFOX_PROFILES_INI)
    ).expanduser()
    if not profiles_ini.exists():
        raise AuthResolutionError(
            "Firefox profiles.ini not found; set cookies via env/config instead."
        )

    parser = configparser.ConfigParser()
    parser.read(profiles_ini)
    candidates: list[Path] = []
    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        is_relative = parser.getboolean(section, "IsRelative", fallback=True)
        raw_path = parser.get(section, "Path", fallback="")
        if not raw_path:
            continue
        profile_path = profiles_ini.parent / raw_path if is_relative else Path(raw_path)
        if parser.getboolean(section, "Default", fallback=False):
            return profile_path.expanduser()
        candidates.append(profile_path.expanduser())

    if candidates:
        return candidates[0]
    raise AuthResolutionError("No Firefox profile entries found in profiles.ini.")


def _copy_sqlite_bundle(cookies_db: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="tweetxvault-firefox-"))
    target = temp_dir / "cookies.sqlite"
    shutil.copy2(cookies_db, target)
    for suffix in ("-wal", "-shm"):
        sidecar = cookies_db.with_name(cookies_db.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, temp_dir / sidecar.name)
    return target


def extract_firefox_cookies(profile_path: Path) -> FirefoxCookieBundle:
    cookies_db = profile_path / "cookies.sqlite"
    if not cookies_db.exists():
        raise AuthResolutionError(f"Firefox cookies DB not found under {profile_path}")

    copied_db = _copy_sqlite_bundle(cookies_db)
    try:
        connection = sqlite3.connect(f"file:{copied_db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT name, value, host
                FROM moz_cookies
                WHERE name IN (?, ?, ?)
                  AND host IN (?, ?, ?, ?)
                ORDER BY CASE
                    WHEN host IN ('.x.com', 'x.com') THEN 0
                    ELSE 1
                END
                """,
                ("auth_token", "ct0", "twid", *COOKIE_HOSTS),
            ).fetchall()
        finally:
            connection.close()
    finally:
        shutil.rmtree(copied_db.parent, ignore_errors=True)

    values: dict[str, str] = {}
    for row in rows:
        name = row["name"]
        if name not in values:
            values[name] = row["value"]

    return FirefoxCookieBundle(
        auth_token=values.get("auth_token"),
        ct0=values.get("ct0"),
        twid=values.get("twid"),
        user_id=parse_twid(values.get("twid")),
        profile_path=profile_path,
    )
