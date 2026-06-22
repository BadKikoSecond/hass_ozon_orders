#!/usr/bin/env python3
"""LAN relay for Ozon Orders — run on a PC/Mac where ozon.ru works in browser.

Home Assistant (or any client) POSTs page_url + cookies; this script calls Ozon
from the PC's network stack and returns JSON.

Usage:
  pip install curl_cffi aiohttp
  python scripts/ozon_relay.py --host 0.0.0.0 --port 18765

Then in HA integration options set relay URL, e.g. http://192.168.0.15:18765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from aiohttp import web
from curl_cffi.requests import AsyncSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_LOGGER = logging.getLogger("ozon_relay")

BASE_URL = "https://www.ozon.ru"
ENTRYPOINT = "/api/entrypoint-api.bx/page/json/v2"
IMPERSONATE = "chrome131"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.ozon.ru/my/orderlist",
    "Origin": "https://www.ozon.ru",
    "X-Requested-With": "XMLHttpRequest",
}

HTML_HEADERS = {
    **HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def _bootstrap(session: AsyncSession, cookies: dict[str, str]) -> dict[str, str]:
    jar = dict(cookies)
    for path in ("/", "/my/orderlist"):
        response = await session.get(
            f"{BASE_URL}{path}",
            cookies=jar,
            headers=HTML_HEADERS,
            allow_redirects=True,
        )
        for name, value in session.cookies.items():
            jar[str(name)] = str(value)
        if response.status_code in (401, 403):
            return jar
    return jar


async def fetch_ozon_page(page_url: str, cookies: dict[str, str]) -> dict[str, Any]:
    async with AsyncSession(impersonate=IMPERSONATE, timeout=45) as session:
        jar = await _bootstrap(session, cookies)
        encoded = quote(page_url, safe="")
        url = f"{BASE_URL}{ENTRYPOINT}?url={encoded}"
        response = await session.get(url, cookies=jar, headers=HEADERS, allow_redirects=True)

        if response.status_code in (401, 403):
            body = response.text[:400]
            if "challengeURL" in body or "captchaURL" in body:
                return {
                    "success": False,
                    "error_type": "antibot",
                    "error": (
                        "Ozon challenge на ПК: откройте ozon.ru в браузере, "
                        "пройдите проверку, экспортируйте cookies заново."
                    ),
                }
            return {
                "success": False,
                "error_type": "antibot",
                "error": f"HTTP {response.status_code}: {body}",
            }

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:300]}",
            }

        if "json" not in (response.headers.get("Content-Type") or "").lower():
            return {
                "success": False,
                "error_type": "antibot",
                "error": "Non-JSON response from Ozon",
            }

        data = response.json()
        user = (data.get("userInfo") or {}).get("user") or {}
        if not user.get("isLoggedIn"):
            return {
                "success": False,
                "error_type": "auth",
                "error": "isLoggedIn=false — обновите cookies с ozon.ru",
            }

        return {"success": True, "data": data}


async def handle_fetch(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"success": False, "error": "invalid JSON"}, status=400)

    page_url = payload.get("page_url")
    cookies = payload.get("cookies") or {}
    if not page_url or not isinstance(cookies, dict):
        return web.json_response(
            {"success": False, "error": "page_url and cookies required"},
            status=400,
        )

    result = await fetch_ozon_page(str(page_url), {str(k): str(v) for k, v in cookies.items()})
    status = 200 if result.get("success") else 502
    return web.json_response(result, status=status)


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "ozon_relay"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Ozon Orders LAN relay")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/fetch", handle_fetch)

    _LOGGER.info("Listening on http://%s:%s", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
