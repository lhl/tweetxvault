"""Cookie resolution chain."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from tweetxvault.auth.firefox import discover_default_profile, extract_firefox_cookies
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import AuthResolutionError


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
                "Likes sync requires a numeric user_id. Set TWEETXVAULT_USER_ID or allow Firefox "
                "cookie extraction to read the twid cookie."
            )


def resolve_auth_bundle(
    config: AppConfig,
    *,
    env: Mapping[str, str] | None = None,
) -> ResolvedAuthBundle:
    env = env or os.environ
    firefox_bundle = None

    def _from_env(name: str) -> tuple[str | None, str | None]:
        value = env.get(name)
        return (value, "env") if value else (None, None)

    def _from_config(attr: str) -> tuple[str | None, str | None]:
        value = getattr(config.auth, attr)
        return (value, "config") if value else (None, None)

    def _from_firefox(attr: str) -> tuple[str | None, str | None]:
        nonlocal firefox_bundle
        if firefox_bundle is None:
            profile_path = discover_default_profile(config.auth.firefox_profile_path, env)
            firefox_bundle = extract_firefox_cookies(profile_path)
        value = getattr(firefox_bundle, attr)
        return (value, "firefox") if value else (None, None)

    def _pick(env_name: str, config_attr: str, firefox_attr: str) -> tuple[str | None, str | None]:
        for getter in (
            lambda: _from_env(env_name),
            lambda: _from_config(config_attr),
            lambda: _from_firefox(firefox_attr),
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
            "add them to config.toml, or ensure Firefox is logged into x.com."
        )

    return ResolvedAuthBundle(
        auth_token=auth_token,
        ct0=ct0,
        user_id=user_id,
        auth_token_source=auth_token_source or "unknown",
        ct0_source=ct0_source or "unknown",
        user_id_source=user_id_source,
    )
