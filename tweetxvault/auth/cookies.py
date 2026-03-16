"""Cookie resolution chain and browser-profile discovery."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tweetxvault.auth.chromium import (
    CHROMIUM_BROWSER_ORDER,
    extract_chromium_cookies,
    list_chromium_profiles,
    normalize_browser_name,
)
from tweetxvault.auth.firefox import (
    extract_firefox_cookies,
    list_firefox_profiles,
    resolve_firefox_profile,
)
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import AuthResolutionError


@dataclass(frozen=True, slots=True)
class BrowserCandidate:
    browser_id: str
    browser_name: str
    profile_name: str
    profile_path: Path
    is_default: bool = False
    is_last_used: bool = False

    @property
    def tags(self) -> str:
        tags: list[str] = []
        if self.is_last_used:
            tags.append("last-used")
        if self.is_default:
            tags.append("default")
        return ", ".join(tags)


class ResolvedAuthBundle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auth_token: str
    ct0: str
    user_id: str | None = None
    auth_token_source: str
    ct0_source: str
    user_id_source: str | None = None

    def validate_for_collection(self, collection: str) -> None:
        if collection == "likes" and not self.user_id:
            raise AuthResolutionError(
                "Likes sync requires a numeric user_id. Set TWEETXVAULT_USER_ID or allow "
                "browser cookie extraction to read the twid cookie."
            )
        if collection == "tweets" and not self.user_id:
            raise AuthResolutionError(
                "Own-tweet sync requires a numeric user_id. Set TWEETXVAULT_USER_ID or allow "
                "browser cookie extraction to read the twid cookie."
            )


def _emit_status(status: Callable[[str], None] | None, message: str) -> None:
    if status is not None:
        status(message)


def list_available_browser_candidates(
    *,
    browser: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[BrowserCandidate]:
    env = env or os.environ
    browser_ids = _browser_ids(browser)
    candidates: list[BrowserCandidate] = []

    if "firefox" in browser_ids:
        try:
            profiles = list_firefox_profiles(env)
        except AuthResolutionError:
            profiles = []
        for profile in profiles:
            try:
                bundle = extract_firefox_cookies(profile.path)
            except AuthResolutionError:
                continue
            if bundle.auth_token and bundle.ct0:
                candidates.append(
                    BrowserCandidate(
                        browser_id="firefox",
                        browser_name="Firefox",
                        profile_name=profile.name,
                        profile_path=profile.path,
                        is_default=profile.is_default or bool(profile.install_defaults),
                    )
                )

    for browser_id in [candidate for candidate in browser_ids if candidate != "firefox"]:
        for profile in list_chromium_profiles(browser_id, env):
            try:
                bundle = extract_chromium_cookies(
                    browser_id,
                    profile_path=profile.path,
                    env=env,
                )
            except AuthResolutionError:
                continue
            if bundle.auth_token and bundle.ct0:
                candidates.append(
                    BrowserCandidate(
                        browser_id=browser_id,
                        browser_name=profile.browser_name,
                        profile_name=profile.name,
                        profile_path=profile.path,
                        is_default=profile.is_default,
                        is_last_used=profile.is_last_used,
                    )
                )
    return sorted(candidates, key=_candidate_sort_key)


def resolve_auth_bundle(
    config: AppConfig,
    *,
    env: Mapping[str, str] | None = None,
    status: Callable[[str], None] | None = None,
) -> ResolvedAuthBundle:
    env = env or os.environ
    browser_bundle = None

    def _from_env(name: str) -> tuple[str | None, str | None]:
        value = env.get(name)
        return (value, "env") if value else (None, None)

    def _from_config(attr: str) -> tuple[str | None, str | None]:
        value = getattr(config.auth, attr)
        return (value, "config") if value else (None, None)

    def _from_browser(attr: str) -> tuple[str | None, str | None]:
        nonlocal browser_bundle
        if browser_bundle is None:
            _emit_status(status, "resolving browser cookies")
            browser_bundle = _resolve_browser_bundle(config, env, status=status)
        value = browser_bundle.get(attr)
        source = browser_bundle["source"]
        return (value, source) if value else (None, None)

    def _pick(env_name: str, config_attr: str, browser_attr: str) -> tuple[str | None, str | None]:
        for getter in (
            lambda: _from_env(env_name),
            lambda: _from_config(config_attr),
            lambda: _from_browser(browser_attr),
        ):
            value, source = getter()
            if value:
                return value, source
        return None, None

    auth_token, auth_token_source = _pick("TWEETXVAULT_AUTH_TOKEN", "auth_token", "auth_token")
    ct0, ct0_source = _pick("TWEETXVAULT_CT0", "ct0", "ct0")
    user_id, user_id_source = _pick("TWEETXVAULT_USER_ID", "user_id", "user_id")

    missing = [name for name, value in {"auth_token": auth_token, "ct0": ct0}.items() if not value]
    if missing:
        joined = ", ".join(missing)
        raise AuthResolutionError(
            f"Missing X session cookies: {joined}. Set TWEETXVAULT_AUTH_TOKEN/TWEETXVAULT_CT0, "
            "add them to config.toml, or ensure Firefox, Chrome, or another supported browser "
            "is logged into x.com."
        )

    return ResolvedAuthBundle(
        auth_token=auth_token,
        ct0=ct0,
        user_id=user_id,
        auth_token_source=auth_token_source or "unknown",
        ct0_source=ct0_source or "unknown",
        user_id_source=user_id_source,
    )


def _resolve_browser_bundle(
    config: AppConfig,
    env: Mapping[str, str],
    *,
    status: Callable[[str], None] | None = None,
) -> dict[str, str | None]:
    selection = _resolve_browser_selection(config)
    if selection["browser"] == "firefox":
        target = (
            selection["profile_path"] or selection["profile"] or "auto-detected Firefox profile"
        )
        _emit_status(status, f"trying Firefox cookies from {target}")
        bundle = _resolve_firefox_bundle(
            explicit_profile=selection["profile"],
            explicit_path=selection["profile_path"],
            env=env,
            status=status,
        )
        return {
            "auth_token": bundle["auth_token"],
            "ct0": bundle["ct0"],
            "user_id": bundle["user_id"],
            "source": "firefox",
        }

    if selection["browser"] is not None:
        target = selection["profile_path"] or selection["profile"] or "default profile"
        _emit_status(status, f"trying {selection['browser']} cookies from {target}")
        bundle = extract_chromium_cookies(
            selection["browser"],
            profile_name=selection["profile"],
            profile_path=selection["profile_path"],
            env=env,
        )
        return {
            "auth_token": bundle.auth_token,
            "ct0": bundle.ct0,
            "user_id": bundle.user_id,
            "source": selection["browser"],
        }

    _emit_status(status, "trying Firefox browser cookies")
    firefox_bundle = _attempt_firefox_bundle(env, status=status)
    if firefox_bundle is not None:
        return firefox_bundle

    for browser_id in CHROMIUM_BROWSER_ORDER:
        _emit_status(status, f"trying {browser_id} browser cookies")
        try:
            bundle = extract_chromium_cookies(browser_id, env=env)
        except AuthResolutionError:
            continue
        if bundle.auth_token and bundle.ct0:
            return {
                "auth_token": bundle.auth_token,
                "ct0": bundle.ct0,
                "user_id": bundle.user_id,
                "source": browser_id,
            }

    return {"auth_token": None, "ct0": None, "user_id": None, "source": None}


def _attempt_firefox_bundle(
    env: Mapping[str, str],
    *,
    status: Callable[[str], None] | None = None,
) -> dict[str, str | None] | None:
    try:
        bundle = _resolve_firefox_bundle(env=env, status=status)
    except AuthResolutionError:
        return None
    return {
        "auth_token": bundle["auth_token"],
        "ct0": bundle["ct0"],
        "user_id": bundle["user_id"],
        "source": "firefox",
    }


def _resolve_firefox_bundle(
    *,
    explicit_profile: str | None = None,
    explicit_path: Path | None = None,
    env: Mapping[str, str],
    status: Callable[[str], None] | None = None,
) -> dict[str, str | None]:
    profile = resolve_firefox_profile(
        explicit_path=str(explicit_path) if explicit_path is not None else None,
        explicit_profile=explicit_profile,
        env=env,
        status=status,
    )
    _emit_status(status, f"reading Firefox cookies from {profile.path}")
    bundle = extract_firefox_cookies(profile.path)
    return {
        "auth_token": bundle.auth_token,
        "ct0": bundle.ct0,
        "user_id": bundle.user_id,
    }


def _resolve_browser_selection(config: AppConfig) -> dict[str, str | Path | None]:
    browser = config.auth.browser
    profile = config.auth.browser_profile
    profile_path = config.auth.browser_profile_path
    firefox_profile_path = config.auth.firefox_profile_path

    if firefox_profile_path and browser and normalize_browser_name(browser) != "firefox":
        raise AuthResolutionError("auth.firefox_profile_path only applies when browser is Firefox.")

    if firefox_profile_path and profile_path:
        raise AuthResolutionError(
            "Set either auth.browser_profile_path or auth.firefox_profile_path, not both."
        )

    if firefox_profile_path and browser is None:
        browser = "firefox"
        profile_path = firefox_profile_path

    if (profile or profile_path) and browser is None:
        raise AuthResolutionError(
            "Selecting a browser profile requires auth.browser / TWEETXVAULT_BROWSER."
        )

    normalized = normalize_browser_name(browser) if browser else None
    return {
        "browser": normalized,
        "profile": profile,
        "profile_path": Path(profile_path).expanduser() if profile_path else None,
    }


def _browser_ids(browser: str | None) -> list[str]:
    if browser is None:
        return ["firefox", *CHROMIUM_BROWSER_ORDER]
    return [normalize_browser_name(browser)]


def _candidate_sort_key(candidate: BrowserCandidate) -> tuple[int, int, int, str, str]:
    browser_index = _browser_ids(None).index(candidate.browser_id)
    return (
        browser_index,
        0 if candidate.is_last_used else 1,
        0 if candidate.is_default else 1,
        candidate.profile_name.lower(),
        str(candidate.profile_path).lower(),
    )
