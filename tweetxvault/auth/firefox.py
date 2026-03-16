"""Firefox cookie extraction."""

from __future__ import annotations

import configparser
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict

from tweetxvault.exceptions import AuthResolutionError

FIREFOX_PROFILES_INI = Path.home() / ".mozilla/firefox/profiles.ini"
COOKIE_NAMES = {"auth_token", "ct0", "twid"}
COOKIE_HOSTS = {"x.com", ".x.com", "twitter.com", ".twitter.com"}
SQLITE_SNAPSHOT_SUFFIXES = ("", "-wal", "-shm", "-journal")


class FirefoxCookieBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    auth_token: str | None = None
    ct0: str | None = None
    twid: str | None = None
    user_id: str | None = None
    profile_path: Path


@dataclass(slots=True)
class FirefoxProfile:
    name: str
    path: Path
    is_default: bool = False
    install_defaults: list[str] = field(default_factory=list)


def parse_twid(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    decoded = unquote(raw_value)
    if decoded.startswith("u="):
        candidate = decoded[2:]
        return candidate if candidate.isdigit() else None
    return decoded if decoded.isdigit() else None


def _resolve_profile_path(profiles_ini: Path, raw_path: str, *, is_relative: bool) -> Path:
    if is_relative:
        return (profiles_ini.parent / raw_path).expanduser()
    return Path(raw_path).expanduser()


def _load_profiles(profiles_ini: Path) -> list[FirefoxProfile]:
    parser = configparser.ConfigParser()
    parser.read(profiles_ini)
    profiles: list[FirefoxProfile] = []
    by_path: dict[Path, FirefoxProfile] = {}

    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        raw_path = parser.get(section, "Path", fallback="")
        if not raw_path:
            continue
        profile = FirefoxProfile(
            name=parser.get(section, "Name", fallback=raw_path),
            path=_resolve_profile_path(
                profiles_ini,
                raw_path,
                is_relative=parser.getboolean(section, "IsRelative", fallback=True),
            ),
            is_default=parser.getboolean(section, "Default", fallback=False),
        )
        profiles.append(profile)
        by_path[profile.path] = profile

    for section in parser.sections():
        if not section.startswith("Install"):
            continue
        raw_default = parser.get(section, "Default", fallback="")
        if not raw_default:
            continue
        profile = by_path.get(_resolve_profile_path(profiles_ini, raw_default, is_relative=True))
        if profile is not None:
            profile.install_defaults.append(section)

    return profiles


def _profile_summary(profiles: list[FirefoxProfile]) -> str:
    lines: list[str] = []
    for profile in profiles:
        tags: list[str] = []
        if profile.is_default:
            tags.append("default")
        if profile.install_defaults:
            tags.append("install-default")
        suffix = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"- {profile.name}: {profile.path}{suffix}")
    return "\n".join(lines)


def _discover_profiles_ini(env: Mapping[str, str]) -> Path:
    return Path(env.get("TWEETXVAULT_FIREFOX_PROFILES_INI", FIREFOX_PROFILES_INI)).expanduser()


def list_firefox_profiles(env: Mapping[str, str] | None = None) -> list[FirefoxProfile]:
    env = env or os.environ
    profiles_ini = _discover_profiles_ini(env)
    if not profiles_ini.exists():
        raise AuthResolutionError(
            "Firefox profiles.ini not found; set cookies via env/config instead."
        )
    profiles = _load_profiles(profiles_ini)
    if not profiles:
        raise AuthResolutionError("No Firefox profile entries found in profiles.ini.")
    return sorted(profiles, key=_profile_sort_key)


def _profile_sort_key(profile: FirefoxProfile) -> tuple[int, int, str, str]:
    return (
        0 if profile.install_defaults else 1,
        0 if profile.is_default else 1,
        profile.name.lower(),
        str(profile.path).lower(),
    )


def resolve_firefox_profile(
    explicit_path: str | None = None,
    explicit_profile: str | None = None,
    env: Mapping[str, str] | None = None,
    status: Callable[[str], None] | None = None,
) -> FirefoxProfile:
    env = env or os.environ
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise AuthResolutionError(f"Configured Firefox profile does not exist: {path}")
        if status is not None:
            status(f"using Firefox profile path {path}")
        return FirefoxProfile(name=path.name, path=path)

    profiles = list_firefox_profiles(env)
    if explicit_profile:
        if status is not None:
            status(f"looking for Firefox profile '{explicit_profile}'")
        matches = [
            profile
            for profile in profiles
            if profile.name == explicit_profile or profile.path.name == explicit_profile
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AuthResolutionError(
                "Multiple Firefox profiles matched that name. Use "
                "TWEETXVAULT_FIREFOX_PROFILE_PATH / --profile-path instead:\n"
                f"{_profile_summary(matches)}"
            )
        raise AuthResolutionError(
            f"No Firefox profile matched '{explicit_profile}'. Available profiles:\n"
            f"{_profile_summary(profiles)}"
        )

    for profile in profiles:
        if status is not None:
            status(f"probing Firefox profile {profile.name}")
        try:
            bundle = extract_firefox_cookies(profile.path)
        except AuthResolutionError:
            continue
        if bundle.auth_token and bundle.ct0:
            return profile

    raise AuthResolutionError(
        "Discovered Firefox profiles, but none contained X session cookies. "
        "Log into x.com in one of these profiles or set "
        "TWEETXVAULT_FIREFOX_PROFILE_PATH / auth.firefox_profile_path explicitly:\n"
        f"{_profile_summary(profiles)}"
    )


def discover_default_profile(
    explicit_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    return resolve_firefox_profile(explicit_path=explicit_path, env=env).path


@contextmanager
def _sqlite_snapshot(cookies_db: Path) -> Iterator[Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="tweetxvault-firefox-"))
    target = temp_dir / cookies_db.name
    try:
        try:
            # SQLite's backup API can block indefinitely against a busy live Firefox
            # profile. Copying the DB plus journal sidecars keeps cookie reads bounded.
            for suffix in SQLITE_SNAPSHOT_SUFFIXES:
                source = cookies_db if not suffix else Path(f"{cookies_db}{suffix}")
                if suffix and not source.exists():
                    continue
                try:
                    shutil.copy2(source, target if not suffix else Path(f"{target}{suffix}"))
                except FileNotFoundError:
                    if suffix:
                        continue
                    raise
        except OSError as exc:
            raise AuthResolutionError(
                f"Failed to snapshot Firefox cookies under {cookies_db.parent}: {exc}"
            ) from exc
        yield target
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def extract_firefox_cookies(profile_path: Path) -> FirefoxCookieBundle:
    cookies_db = profile_path / "cookies.sqlite"
    if not cookies_db.exists():
        raise AuthResolutionError(f"Firefox cookies DB not found under {profile_path}")

    with _sqlite_snapshot(cookies_db) as copied_db:
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
        except sqlite3.Error as exc:
            raise AuthResolutionError(
                f"Failed to read Firefox cookies under {profile_path}: {exc}"
            ) from exc

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
