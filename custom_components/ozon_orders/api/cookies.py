"""Load cookies from JSON (browser export, dict, or Playwright storage_state)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CookieJar = dict[str, str]

_AUTH_COOKIE_NAMES = (
    "__Secure-access-token",
    "__Secure-refresh-token",
    "__Secure-user-id",
)

_SET_COOKIE_SKIP = frozenset(
    {"expires", "max-age", "path", "domain", "samesite", "secure", "httponly"}
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def parse_cookies_input(text: str) -> Any:
    """Parse cookies pasted from browser extensions or DevTools."""
    cleaned = _CODE_FENCE_RE.sub("", text.strip()).strip()
    if not cleaned:
        raise ValueError("empty cookies input")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    if "=" in cleaned and not cleaned.startswith(("[", "{")):
        jar: CookieJar = {}
        for part in cleaned.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip()
            if name:
                jar[name] = value
        if jar:
            return jar

    raise ValueError("unsupported cookies format")


def load_cookies(source: str | Path | Mapping[str, Any] | list[Any]) -> CookieJar:
    """Normalize cookies to ``{name: value}`` for the ``Cookie`` header."""
    if isinstance(source, str) and not Path(source).exists():
        source = parse_cookies_input(source)

    data = _read_source(source)
    jar: CookieJar = {}

    if isinstance(data, dict) and "cookies" in data and isinstance(data["cookies"], list):
        _merge_cookie_list(jar, data["cookies"])
    elif isinstance(data, list):
        _merge_cookie_list(jar, data)
    elif isinstance(data, dict):
        for key, value in data.items():
            if key in ("cookies", "origins", "localStorage"):
                continue
            if isinstance(value, str):
                jar[key] = value
    else:
        raise ValueError("Unsupported cookies JSON format")

    if not jar:
        raise ValueError("No cookies found in JSON")

    if not any(name in jar for name in _AUTH_COOKIE_NAMES):
        raise ValueError(
            "Missing Ozon auth cookies (__Secure-access-token). "
            "Export all cookies for .ozon.ru from a logged-in browser."
        )

    return jar


def cookies_header(jar: CookieJar) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def parse_set_cookie_header(header: str | None) -> CookieJar:
    """Parse ``Set-Cookie`` response header into a cookie jar fragment."""
    out: CookieJar = {}
    if not header:
        return out
    chunks = re.split(r", (?=[^;]+=)", header)
    for chunk in chunks:
        match = re.match(r"([^=]+)=([^;]*)", chunk.strip())
        if not match:
            continue
        name, value = match.group(1), match.group(2)
        if name.lower() in _SET_COOKIE_SKIP:
            continue
        out[name] = value
    return out


def access_token_part6(jar: CookieJar) -> str | None:
    token = jar.get("__Secure-access-token", "")
    parts = token.split(".")
    return parts[6] if len(parts) > 6 else None


def access_token_operational_expiry(jar: CookieJar) -> datetime | None:
    part6 = access_token_part6(jar)
    if part6 and len(part6) == 14 and part6.isdigit():
        return datetime.strptime(part6, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return None


def session_info_from_jar(
    jar: CookieJar,
    raw_source: str | Path | Mapping[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    """Session metadata: operational part[6] expiry plus browser export dates."""
    browser = (
        session_expiry_info(raw_source)
        if raw_source is not None
        else {
            "access_token_expires": None,
            "refresh_token_expires": None,
            "session_expires": None,
            "days_remaining": None,
        }
    )
    operational = access_token_operational_expiry(jar)
    now = datetime.now(timezone.utc)
    minutes_remaining = None
    if operational:
        minutes_remaining = int((operational - now).total_seconds() // 60)

    primary = operational or browser.get("session_expires")
    days_remaining = browser.get("days_remaining")
    if minutes_remaining is not None:
        days_remaining = round(max(0, minutes_remaining) / 1440, 2)

    return {
        **browser,
        "session_expires": primary,
        "operational_expires": operational,
        "minutes_remaining": minutes_remaining,
        "access_token_part6": access_token_part6(jar),
        "days_remaining": days_remaining,
        "token_operational_expired": (
            minutes_remaining is not None and minutes_remaining <= 0
        ),
    }


def session_expiry_info(source: str | Path | Mapping[str, Any] | list[Any]) -> dict[str, Any]:
    """Return auth cookie expiry timestamps parsed from the raw export."""
    if isinstance(source, str) and not Path(source).exists():
        source = parse_cookies_input(source)

    data = _read_source(source)
    items = _cookie_items(data)
    expiries: dict[str, datetime] = {}
    for item in items:
        name = item.get("name")
        if name not in _AUTH_COOKIE_NAMES:
            continue
        exp = _cookie_expiry_datetime(item)
        if exp is not None:
            expiries[str(name)] = exp

    if not expiries:
        return {
            "access_token_expires": None,
            "refresh_token_expires": None,
            "session_expires": None,
            "days_remaining": None,
        }

    access = expiries.get("__Secure-access-token")
    refresh = expiries.get("__Secure-refresh-token")
    session = min(expiries.values())
    days = None
    if session:
        days = max(0, (session - datetime.now(timezone.utc)).total_seconds() / 86400)

    return {
        "access_token_expires": access,
        "refresh_token_expires": refresh,
        "session_expires": session,
        "days_remaining": round(days, 1) if days is not None else None,
    }


def _cookie_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        return [c for c in data["cookies"] if isinstance(c, dict)]
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    return []


def _cookie_expiry_datetime(item: dict[str, Any]) -> datetime | None:
    exp = item.get("expirationDate") or item.get("expires")
    if not exp:
        return None
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_source(source: str | Path | Mapping[str, Any] | list[Any]) -> Any:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_file():
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        return parse_cookies_input(str(source))
    return source


def _merge_cookie_list(jar: CookieJar, items: list[Any]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        jar[str(name)] = str(value)
