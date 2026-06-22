"""Load cookies from JSON (browser export, dict, or Playwright storage_state)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CookieJar = dict[str, str]

_AUTH_COOKIE_NAMES = (
    "__Secure-access-token",
    "__Secure-refresh-token",
    "__Secure-user-id",
)

_OZON_SUFFIXES = (".ozon.ru", "ozon.ru", "www.ozon.ru")


def load_cookies(source: str | Path | Mapping[str, Any] | list[Any]) -> CookieJar:
    """Normalize cookies to ``{name: value}`` for the ``Cookie`` header."""
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

    return jar


def cookies_header(jar: CookieJar) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def session_expiry_info(source: str | Path | Mapping[str, Any] | list[Any]) -> dict[str, Any]:
    """Return auth cookie expiry timestamps parsed from the raw export."""
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
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return source


def _merge_cookie_list(jar: CookieJar, items: list[Any]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        domain = str(item.get("domain", ""))
        if domain and not _domain_matches(domain):
            continue
        jar[str(name)] = str(value)


def _domain_matches(domain: str) -> bool:
    domain = domain.lstrip(".")
    return any(domain == suffix.lstrip(".") or domain.endswith(suffix) for suffix in _OZON_SUFFIXES)
