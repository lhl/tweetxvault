from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tweetxvault.auth.cookies import resolve_auth_bundle
from tweetxvault.auth.firefox import (
    FirefoxCookieBundle,
    discover_default_profile,
    extract_firefox_cookies,
    parse_twid,
)
from tweetxvault.config import AppConfig, AuthConfig
from tweetxvault.exceptions import AuthResolutionError


def test_parse_twid() -> None:
    assert parse_twid("u%3D123456") == "123456"
    assert parse_twid("u=987") == "987"
    assert parse_twid("not-a-user") is None


def test_resolve_auth_bundle_prefers_env_then_config_then_firefox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0"))

    monkeypatch.setattr(
        "tweetxvault.auth.cookies.discover_default_profile",
        lambda explicit_path, env: Path("/fake-profile"),
    )
    monkeypatch.setattr(
        "tweetxvault.auth.cookies.extract_firefox_cookies",
        lambda path: FirefoxCookieBundle(
            auth_token="ff-token",
            ct0="ff-ct0",
            twid="u%3D42",
            user_id="42",
            profile_path=path,
        ),
    )

    bundle = resolve_auth_bundle(
        config,
        env={
            "TWEETXVAULT_AUTH_TOKEN": "env-token",
        },
    )

    assert bundle.auth_token == "env-token"
    assert bundle.ct0 == "config-ct0"
    assert bundle.user_id == "42"
    assert bundle.auth_token_source == "env"
    assert bundle.ct0_source == "config"
    assert bundle.user_id_source == "firefox"


def test_extract_firefox_cookies_reads_sqlite_fixture(tmp_path: Path) -> None:
    profile = _make_firefox_profile(
        tmp_path,
        "profile",
        cookies={
            "auth_token": "token-1",
            "ct0": "ct0-1",
            "twid": "u%3D42",
        },
    )

    bundle = extract_firefox_cookies(profile)

    assert bundle.auth_token == "token-1"
    assert bundle.ct0 == "ct0-1"
    assert bundle.twid == "u%3D42"
    assert bundle.user_id == "42"


def test_discover_default_profile_prefers_profile_with_x_cookies(tmp_path: Path) -> None:
    profiles_ini = _write_profiles_ini(
        tmp_path,
        """
[Install46F492E0ACFF84D4]
Default=profile.dev-edition-default-1
Locked=1

[Install4F96D1932A9F858E]
Default=profile.default

[Profile1]
Name=dev-edition-default
IsRelative=1
Path=profile.dev-edition-default

[Install92C1343B158958A9]
Default=profile.dev-edition-default

[Profile0]
Name=default
IsRelative=1
Path=profile.default
Default=1

[General]
StartWithLastProfile=1
Version=2

[Profile2]
Name=dev-edition-default-1
IsRelative=1
Path=profile.dev-edition-default-1
""",
    )
    _make_firefox_profile(tmp_path, "profile.default")
    _make_firefox_profile(tmp_path, "profile.dev-edition-default")
    expected = _make_firefox_profile(
        tmp_path,
        "profile.dev-edition-default-1",
        cookies={
            "auth_token": "token-1",
            "ct0": "ct0-1",
            "twid": "u%3D42",
        },
    )

    discovered = discover_default_profile(
        env={"TWEETXVAULT_FIREFOX_PROFILES_INI": str(profiles_ini)}
    )

    assert discovered == expected


def test_discover_default_profile_lists_matching_options_when_ambiguous(tmp_path: Path) -> None:
    profiles_ini = _write_profiles_ini(
        tmp_path,
        """
[Profile0]
Name=default
IsRelative=1
Path=first.default
Default=1

[Profile1]
Name=dev-edition-default
IsRelative=1
Path=second.dev-edition-default
""",
    )
    first = _make_firefox_profile(
        tmp_path,
        "first.default",
        cookies={"auth_token": "token-1", "ct0": "ct0-1"},
    )
    second = _make_firefox_profile(
        tmp_path,
        "second.dev-edition-default",
        cookies={"auth_token": "token-2", "ct0": "ct0-2"},
    )

    with pytest.raises(AuthResolutionError) as exc_info:
        discover_default_profile(env={"TWEETXVAULT_FIREFOX_PROFILES_INI": str(profiles_ini)})

    message = str(exc_info.value)
    assert "Multiple Firefox profiles contain X session cookies" in message
    assert str(first) in message
    assert str(second) in message
    assert "TWEETXVAULT_FIREFOX_PROFILE_PATH" in message


def test_discover_default_profile_lists_available_options_when_none_match(tmp_path: Path) -> None:
    profiles_ini = _write_profiles_ini(
        tmp_path,
        """
[Profile0]
Name=default
IsRelative=1
Path=first.default
Default=1

[Profile1]
Name=dev-edition-default
IsRelative=1
Path=second.dev-edition-default
""",
    )
    first = _make_firefox_profile(tmp_path, "first.default")
    second = _make_firefox_profile(tmp_path, "second.dev-edition-default")

    with pytest.raises(AuthResolutionError) as exc_info:
        discover_default_profile(env={"TWEETXVAULT_FIREFOX_PROFILES_INI": str(profiles_ini)})

    message = str(exc_info.value)
    assert "none contained X session cookies" in message
    assert str(first) in message
    assert str(second) in message


def _make_firefox_profile(
    base: Path,
    name: str,
    *,
    cookies: dict[str, str] | None = None,
) -> Path:
    profile = base / name
    profile.mkdir()
    cookies_db = profile / "cookies.sqlite"
    connection = sqlite3.connect(cookies_db)
    try:
        connection.execute(
            """
            CREATE TABLE moz_cookies (
                id INTEGER PRIMARY KEY,
                host TEXT,
                path TEXT,
                isSecure INTEGER,
                expiry INTEGER,
                name TEXT,
                value TEXT
            )
            """
        )
        if cookies:
            rows = [
                (".x.com", "/", 1, 0, cookie_name, cookie_value)
                for cookie_name, cookie_value in cookies.items()
            ]
            connection.executemany(
                (
                    "INSERT INTO moz_cookies "
                    "(host, path, isSecure, expiry, name, value) "
                    "VALUES (?, ?, ?, ?, ?, ?)"
                ),
                rows,
            )
        connection.commit()
    finally:
        connection.close()
    return profile


def _write_profiles_ini(base: Path, contents: str) -> Path:
    profiles_ini = base / "profiles.ini"
    profiles_ini.write_text(contents.strip() + "\n", encoding="utf-8")
    return profiles_ini
