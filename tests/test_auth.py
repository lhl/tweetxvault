from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tweetxvault.auth.chromium import extract_chromium_cookies, list_chromium_profiles
from tweetxvault.auth.cookies import list_available_browser_candidates, resolve_auth_bundle
from tweetxvault.auth.firefox import (
    FirefoxCookieBundle,
    FirefoxProfile,
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


def test_resolve_auth_bundle_prefers_env_then_config_then_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(auth=AuthConfig(auth_token="config-token", ct0="config-ct0"))

    monkeypatch.setattr(
        "tweetxvault.auth.cookies._resolve_browser_bundle",
        lambda config, env: {
            "auth_token": "browser-token",
            "ct0": "browser-ct0",
            "user_id": "42",
            "source": "chrome",
        },
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
    assert bundle.user_id_source == "chrome"


def test_resolve_auth_bundle_falls_back_to_chrome_after_firefox_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig()
    monkeypatch.setattr("tweetxvault.auth.cookies._attempt_firefox_bundle", lambda env: None)

    def fake_extract(browser_id: str, **_: object) -> SimpleNamespace:
        if browser_id == "chrome":
            return SimpleNamespace(auth_token="chrome-token", ct0="chrome-ct0", user_id="42")
        raise AuthResolutionError("missing")

    monkeypatch.setattr("tweetxvault.auth.cookies.extract_chromium_cookies", fake_extract)

    bundle = resolve_auth_bundle(config, env={})

    assert bundle.auth_token == "chrome-token"
    assert bundle.ct0 == "chrome-ct0"
    assert bundle.user_id == "42"
    assert bundle.auth_token_source == "chrome"
    assert bundle.ct0_source == "chrome"
    assert bundle.user_id_source == "chrome"


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


def test_discover_default_profile_prefers_default_match_when_multiple_have_cookies(
    tmp_path: Path,
) -> None:
    profiles_ini = _write_profiles_ini(
        tmp_path,
        """
[Profile0]
Name=default
IsRelative=1
Path=first.default
Default=1

[Profile1]
Name=secondary
IsRelative=1
Path=second.default
""",
    )
    expected = _make_firefox_profile(
        tmp_path,
        "first.default",
        cookies={"auth_token": "token-1", "ct0": "ct0-1"},
    )
    _make_firefox_profile(
        tmp_path,
        "second.default",
        cookies={"auth_token": "token-2", "ct0": "ct0-2"},
    )

    discovered = discover_default_profile(
        env={"TWEETXVAULT_FIREFOX_PROFILES_INI": str(profiles_ini)}
    )

    assert discovered == expected


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


def test_list_chromium_profiles_reads_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".config" / "google-chrome"
    _make_chromium_profile(root, "Default")
    _make_chromium_profile(root, "Profile 2")
    (root / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "last_used": "Profile 2",
                    "info_cache": {
                        "Default": {"name": "Personal"},
                        "Profile 2": {"name": "Work"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = list_chromium_profiles("chrome")

    assert [profile.name for profile in profiles] == ["Work", "Personal"]
    assert [profile.path.name for profile in profiles] == ["Profile 2", "Default"]


def test_extract_chromium_cookies_uses_selected_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _make_chromium_profile(tmp_path, "Default")
    calls: dict[str, str | None] = {}

    def fake_loader(*, cookie_file=None, key_file=None, domain_name=""):
        calls["cookie_file"] = cookie_file
        calls["key_file"] = key_file
        calls["domain_name"] = domain_name
        return [
            SimpleNamespace(name="auth_token", value="chrome-token", domain=".x.com"),
            SimpleNamespace(name="ct0", value="chrome-ct0", domain=".x.com"),
            SimpleNamespace(name="twid", value="u%3D42", domain=".x.com"),
        ]

    monkeypatch.setattr(
        "tweetxvault.auth.chromium._load_browser_cookie3",
        lambda: SimpleNamespace(chrome=fake_loader),
    )

    bundle = extract_chromium_cookies("chrome", profile_path=profile)

    assert calls["cookie_file"] == str(profile / "Cookies")
    assert bundle.auth_token == "chrome-token"
    assert bundle.ct0 == "chrome-ct0"
    assert bundle.user_id == "42"
    assert bundle.browser_id == "chrome"


def test_list_available_browser_candidates_includes_firefox_and_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firefox_profile = FirefoxProfile(name="default", path=Path("/firefox"), is_default=True)
    chrome_profile = SimpleNamespace(
        browser_name="Chrome",
        name="Work",
        path=Path("/chrome/Profile 2"),
        is_default=False,
        is_last_used=True,
    )
    monkeypatch.setattr(
        "tweetxvault.auth.cookies.list_firefox_profiles",
        lambda env=None: [firefox_profile],
    )
    monkeypatch.setattr(
        "tweetxvault.auth.cookies.extract_firefox_cookies",
        lambda path: FirefoxCookieBundle(
            auth_token="ff-token",
            ct0="ff-ct0",
            twid="u%3D1",
            user_id="1",
            profile_path=path,
        ),
    )
    monkeypatch.setattr(
        "tweetxvault.auth.cookies.list_chromium_profiles",
        lambda browser_id, env=None: [chrome_profile] if browser_id == "chrome" else [],
    )
    monkeypatch.setattr(
        "tweetxvault.auth.cookies.extract_chromium_cookies",
        lambda browser_id, **kwargs: SimpleNamespace(
            auth_token=f"{browser_id}-token",
            ct0=f"{browser_id}-ct0",
            user_id="2",
        ),
    )

    candidates = list_available_browser_candidates()

    assert [(candidate.browser_id, candidate.profile_name) for candidate in candidates[:2]] == [
        ("firefox", "default"),
        ("chrome", "Work"),
    ]


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


def _make_chromium_profile(base: Path, name: str) -> Path:
    profile = base / name
    profile.mkdir(parents=True)
    (profile / "Cookies").touch()
    return profile


def _write_profiles_ini(base: Path, contents: str) -> Path:
    profiles_ini = base / "profiles.ini"
    profiles_ini.write_text(contents.strip() + "\n", encoding="utf-8")
    return profiles_ini
