"""Load cookies from JSON (browser export, dict, or Playwright storage_state)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

CookieJar = dict[str, str]

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
