from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tweetxvault.auth.cookies import resolve_auth_bundle
from tweetxvault.auth.firefox import FirefoxCookieBundle, extract_firefox_cookies, parse_twid
from tweetxvault.config import AppConfig, AuthConfig


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
    profile = tmp_path / "profile"
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
        rows = [
            (".x.com", "/", 1, 0, "auth_token", "token-1"),
            (".x.com", "/", 1, 0, "ct0", "ct0-1"),
            (".x.com", "/", 1, 0, "twid", "u%3D42"),
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

    bundle = extract_firefox_cookies(profile)

    assert bundle.auth_token == "token-1"
    assert bundle.ct0 == "ct0-1"
    assert bundle.twid == "u%3D42"
    assert bundle.user_id == "42"
