"""Chromium-family cookie extraction and profile discovery."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from http.cookiejar import Cookie
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, ConfigDict

from tweetxvault.auth.firefox import parse_twid
from tweetxvault.exceptions import AuthResolutionError


@dataclass(frozen=True, slots=True)
class ChromiumBrowser:
    browser_id: str
    display_name: str
    loader_name: str
    linux_roots: tuple[str, ...]
    mac_roots: tuple[str, ...]
    windows_roots: tuple[tuple[str, str], ...]
    direct_profile_root: bool = False


@dataclass(frozen=True, slots=True)
class ChromiumProfile:
    browser_id: str
    browser_name: str
    name: str
    path: Path
    is_default: bool = False
    is_last_used: bool = False


class ChromiumCookieBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    auth_token: str | None = None
    ct0: str | None = None
    twid: str | None = None
    user_id: str | None = None
    browser_id: str
    browser_name: str
    profile_path: Path | None = None


CHROMIUM_BROWSERS: dict[str, ChromiumBrowser] = {
    "chrome": ChromiumBrowser(
        browser_id="chrome",
        display_name="Chrome",
        loader_name="chrome",
        linux_roots=(
            "~/.config/google-chrome",
            "~/.config/google-chrome-beta",
            "~/.config/google-chrome-unstable",
            "~/.var/app/com.google.Chrome/config/google-chrome",
            "~/.var/app/com.google.Chrome/config/google-chrome-beta",
            "~/.var/app/com.google.Chrome/config/google-chrome-unstable",
        ),
        mac_roots=(
            "~/Library/Application Support/Google/Chrome",
            "~/Library/Application Support/Google/Chrome Beta",
            "~/Library/Application Support/Google/Chrome Dev",
        ),
        windows_roots=(
            ("LOCALAPPDATA", r"Google\Chrome\User Data"),
            ("APPDATA", r"Google\Chrome\User Data"),
            ("LOCALAPPDATA", r"Google\Chrome Beta\User Data"),
            ("APPDATA", r"Google\Chrome Beta\User Data"),
            ("LOCALAPPDATA", r"Google\Chrome Dev\User Data"),
            ("APPDATA", r"Google\Chrome Dev\User Data"),
        ),
    ),
    "chromium": ChromiumBrowser(
        browser_id="chromium",
        display_name="Chromium",
        loader_name="chromium",
        linux_roots=(
            "~/.config/chromium",
            "~/.var/app/org.chromium.Chromium/config/chromium",
        ),
        mac_roots=("~/Library/Application Support/Chromium",),
        windows_roots=(
            ("LOCALAPPDATA", r"Chromium\User Data"),
            ("APPDATA", r"Chromium\User Data"),
        ),
    ),
    "brave": ChromiumBrowser(
        browser_id="brave",
        display_name="Brave",
        loader_name="brave",
        linux_roots=(
            "~/.config/BraveSoftware/Brave-Browser",
            "~/.config/BraveSoftware/Brave-Browser-Beta",
            "~/.config/BraveSoftware/Brave-Browser-Dev",
            "~/.config/BraveSoftware/Brave-Browser-Nightly",
            "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
            "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser-Beta",
            "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser-Dev",
            "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser-Nightly",
        ),
        mac_roots=(
            "~/Library/Application Support/BraveSoftware/Brave-Browser",
            "~/Library/Application Support/BraveSoftware/Brave-Browser-Beta",
            "~/Library/Application Support/BraveSoftware/Brave-Browser-Dev",
            "~/Library/Application Support/BraveSoftware/Brave-Browser-Nightly",
        ),
        windows_roots=(
            ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\User Data"),
            ("APPDATA", r"BraveSoftware\Brave-Browser\User Data"),
            ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser-Beta\User Data"),
            ("APPDATA", r"BraveSoftware\Brave-Browser-Beta\User Data"),
            ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser-Dev\User Data"),
            ("APPDATA", r"BraveSoftware\Brave-Browser-Dev\User Data"),
            ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser-Nightly\User Data"),
            ("APPDATA", r"BraveSoftware\Brave-Browser-Nightly\User Data"),
        ),
    ),
    "edge": ChromiumBrowser(
        browser_id="edge",
        display_name="Edge",
        loader_name="edge",
        linux_roots=(
            "~/.config/microsoft-edge",
            "~/.config/microsoft-edge-beta",
            "~/.config/microsoft-edge-dev",
            "~/.var/app/com.microsoft.Edge/config/microsoft-edge",
            "~/.var/app/com.microsoft.Edge/config/microsoft-edge-beta",
            "~/.var/app/com.microsoft.Edge/config/microsoft-edge-dev",
        ),
        mac_roots=(
            "~/Library/Application Support/Microsoft Edge",
            "~/Library/Application Support/Microsoft Edge Beta",
            "~/Library/Application Support/Microsoft Edge Dev",
            "~/Library/Application Support/Microsoft Edge Canary",
        ),
        windows_roots=(
            ("LOCALAPPDATA", r"Microsoft\Edge\User Data"),
            ("APPDATA", r"Microsoft\Edge\User Data"),
            ("LOCALAPPDATA", r"Microsoft\Edge Beta\User Data"),
            ("APPDATA", r"Microsoft\Edge Beta\User Data"),
            ("LOCALAPPDATA", r"Microsoft\Edge Dev\User Data"),
            ("APPDATA", r"Microsoft\Edge Dev\User Data"),
            ("LOCALAPPDATA", r"Microsoft\Edge SxS\User Data"),
            ("APPDATA", r"Microsoft\Edge SxS\User Data"),
        ),
    ),
    "opera": ChromiumBrowser(
        browser_id="opera",
        display_name="Opera",
        loader_name="opera",
        linux_roots=(
            "~/.config/opera",
            "~/.config/opera-beta",
            "~/.config/opera-developer",
            "~/.var/app/com.opera.Opera/config/opera",
            "~/.var/app/com.opera.Opera/config/opera-beta",
            "~/.var/app/com.opera.Opera/config/opera-developer",
        ),
        mac_roots=(
            "~/Library/Application Support/com.operasoftware.Opera",
            "~/Library/Application Support/com.operasoftware.OperaNext",
            "~/Library/Application Support/com.operasoftware.OperaDeveloper",
        ),
        windows_roots=(
            ("APPDATA", r"Opera Software\Opera Stable"),
            ("APPDATA", r"Opera Software\Opera Next"),
            ("APPDATA", r"Opera Software\Opera Developer"),
        ),
        direct_profile_root=True,
    ),
    "opera_gx": ChromiumBrowser(
        browser_id="opera_gx",
        display_name="Opera GX",
        loader_name="opera_gx",
        linux_roots=(),
        mac_roots=("~/Library/Application Support/com.operasoftware.OperaGX",),
        windows_roots=(("APPDATA", r"Opera Software\Opera GX Stable"),),
        direct_profile_root=True,
    ),
    "vivaldi": ChromiumBrowser(
        browser_id="vivaldi",
        display_name="Vivaldi",
        loader_name="vivaldi",
        linux_roots=(
            "~/.config/vivaldi",
            "~/.config/vivaldi-snapshot",
            "~/.var/app/com.vivaldi.Vivaldi/config/vivaldi",
        ),
        mac_roots=("~/Library/Application Support/Vivaldi",),
        windows_roots=(
            ("LOCALAPPDATA", r"Vivaldi\User Data"),
            ("APPDATA", r"Vivaldi\User Data"),
        ),
    ),
    "arc": ChromiumBrowser(
        browser_id="arc",
        display_name="Arc",
        loader_name="arc",
        linux_roots=(),
        mac_roots=("~/Library/Application Support/Arc/User Data",),
        windows_roots=(),
    ),
}

CHROMIUM_BROWSER_ORDER = tuple(CHROMIUM_BROWSERS)
COOKIE_HOST_SUFFIXES = ("x.com", "twitter.com")


def normalize_browser_name(raw: str) -> str:
    key = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "google_chrome": "chrome",
        "microsoft_edge": "edge",
        "opera_gx_stable": "opera_gx",
    }
    key = aliases.get(key, key)
    if key == "firefox":
        return key
    if key not in CHROMIUM_BROWSERS:
        supported = ", ".join(["firefox", *CHROMIUM_BROWSER_ORDER])
        raise AuthResolutionError(f"Unsupported browser '{raw}'. Choose one of: {supported}.")
    return key


def browser_display_name(browser_id: str) -> str:
    return CHROMIUM_BROWSERS[browser_id].display_name


def list_chromium_profiles(
    browser_id: str,
    env: Mapping[str, str] | None = None,
) -> list[ChromiumProfile]:
    browser = CHROMIUM_BROWSERS[normalize_browser_name(browser_id)]
    env = env or os.environ
    profiles: list[ChromiumProfile] = []
    seen: set[Path] = set()
    for root in _user_data_roots(browser, env):
        if browser.direct_profile_root:
            if _cookie_db_for_profile(root) is None or root in seen:
                continue
            seen.add(root)
            profiles.append(
                ChromiumProfile(
                    browser_id=browser.browser_id,
                    browser_name=browser.display_name,
                    name=root.name,
                    path=root,
                    is_default=True,
                )
            )
            continue

        local_state = _read_local_state(root)
        info_cache = (
            local_state.get("profile", {}).get("info_cache", {})
            if isinstance(local_state.get("profile"), dict)
            else {}
        )
        last_used = (
            local_state.get("profile", {}).get("last_used")
            if isinstance(local_state.get("profile"), dict)
            else None
        )
        candidates = [root / "Default", *sorted(root.glob("Profile *"))]
        for candidate in candidates:
            if not candidate.is_dir() or candidate in seen:
                continue
            if _cookie_db_for_profile(candidate) is None:
                continue
            seen.add(candidate)
            info = info_cache.get(candidate.name, {}) if isinstance(info_cache, dict) else {}
            name = info.get("name") or candidate.name
            profiles.append(
                ChromiumProfile(
                    browser_id=browser.browser_id,
                    browser_name=browser.display_name,
                    name=name,
                    path=candidate,
                    is_default=candidate.name == "Default",
                    is_last_used=candidate.name == last_used,
                )
            )
    return sorted(profiles, key=_profile_sort_key)


def extract_chromium_cookies(
    browser_id: str,
    *,
    profile_name: str | None = None,
    profile_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ChromiumCookieBundle:
    browser = CHROMIUM_BROWSERS[normalize_browser_name(browser_id)]
    if profile_name and profile_path is not None:
        raise AuthResolutionError("Choose either a browser profile name or profile path, not both.")

    selected_path = profile_path
    if profile_name:
        selected_path = _match_profile(browser.browser_id, profile_name, env).path

    cookie_file = _resolve_cookie_file(selected_path) if selected_path is not None else None
    key_file = _key_file_for_profile(selected_path) if selected_path is not None else None

    browser_cookie3 = _load_browser_cookie3()
    loader = getattr(browser_cookie3, browser.loader_name)
    try:
        jar = loader(
            cookie_file=str(cookie_file) if cookie_file is not None else None,
            key_file=str(key_file) if key_file is not None else None,
        )
    except Exception as exc:  # pragma: no cover - library-specific failures vary by OS
        target = selected_path if selected_path is not None else browser.display_name
        raise AuthResolutionError(
            f"Failed to read {browser.display_name} cookies from {target}: {exc}"
        ) from exc

    values = _extract_target_cookies(jar)
    return ChromiumCookieBundle(
        auth_token=values.get("auth_token"),
        ct0=values.get("ct0"),
        twid=values.get("twid"),
        user_id=parse_twid(values.get("twid")),
        browser_id=browser.browser_id,
        browser_name=browser.display_name,
        profile_path=selected_path,
    )


def _profile_sort_key(profile: ChromiumProfile) -> tuple[int, int, str, str]:
    return (
        0 if profile.is_last_used else 1,
        0 if profile.is_default else 1,
        profile.name.lower(),
        str(profile.path).lower(),
    )


def _match_profile(
    browser_id: str,
    profile_name: str,
    env: Mapping[str, str] | None = None,
) -> ChromiumProfile:
    profiles = list_chromium_profiles(browser_id, env)
    matches = [
        profile
        for profile in profiles
        if profile.name == profile_name or profile.path.name == profile_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = "\n".join(f"- {profile.name}: {profile.path}" for profile in matches)
        raise AuthResolutionError(
            f"Multiple {browser_display_name(browser_id)} profiles matched '{profile_name}'. "
            f"Use --profile-path instead:\n{options}"
        )
    available = "\n".join(f"- {profile.name}: {profile.path}" for profile in profiles)
    raise AuthResolutionError(
        f"No {browser_display_name(browser_id)} profile matched '{profile_name}'. "
        "Available profiles:\n"
        f"{available or '- none discovered'}"
    )


def _read_local_state(root: Path) -> dict[str, object]:
    path = root / "Local State"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _user_data_roots(
    browser: ChromiumBrowser,
    env: Mapping[str, str],
) -> Iterable[Path]:
    if os.name == "nt":
        for var_name, suffix in browser.windows_roots:
            base = env.get(var_name)
            if not base:
                continue
            path = Path(base) / suffix
            if path.exists():
                yield path
        return

    patterns = browser.mac_roots if os.uname().sysname == "Darwin" else browser.linux_roots
    for raw in patterns:
        path = Path(raw).expanduser()
        if path.exists():
            yield path


def _cookie_db_for_profile(profile_path: Path) -> Path | None:
    if profile_path.is_file():
        return profile_path
    candidates = (profile_path / "Cookies", profile_path / "Network" / "Cookies")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_cookie_file(profile_path: Path) -> Path:
    cookie_file = _cookie_db_for_profile(profile_path)
    if cookie_file is None:
        raise AuthResolutionError(
            f"Chromium cookies DB not found under {profile_path}. "
            "Pass a profile directory or Cookies DB path."
        )
    return cookie_file


def _key_file_for_profile(profile_path: Path) -> Path | None:
    if profile_path.is_file():
        path = profile_path
        if path.name == "Cookies":
            if path.parent.name == "Network":
                profile_dir = path.parent.parent
            else:
                profile_dir = path.parent
        else:
            return None
    else:
        profile_dir = profile_path

    candidate = profile_dir.parent / "Local State"
    return candidate if candidate.exists() else None


def _load_browser_cookie3() -> ModuleType:
    try:
        import browser_cookie3
    except ImportError as exc:  # pragma: no cover - dependency is installed in normal use
        raise AuthResolutionError(
            "Chromium-family cookie extraction requires browser-cookie3. "
            "Run `uv sync` to install runtime dependencies."
        ) from exc
    return browser_cookie3


def _extract_target_cookies(cookies: Iterable[Cookie]) -> dict[str, str]:
    by_name: dict[str, tuple[int, str]] = {}
    for cookie in cookies:
        if cookie.name not in {"auth_token", "ct0", "twid"}:
            continue
        domain = cookie.domain.lstrip(".").lower()
        matches_domain = any(
            domain == suffix or domain.endswith(f".{suffix}") for suffix in COOKIE_HOST_SUFFIXES
        )
        if not matches_domain:
            continue
        priority = 0 if domain.endswith("x.com") else 1
        existing = by_name.get(cookie.name)
        if existing is None or priority < existing[0]:
            by_name[cookie.name] = (priority, cookie.value)
    return {name: value for name, (_, value) in by_name.items()}
