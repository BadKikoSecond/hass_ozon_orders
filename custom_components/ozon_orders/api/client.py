"""Async HTTP client for Ozon buyer order pages."""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .cookies import CookieJar
from .errors import OzonAntibotError, OzonAuthError
from .parser import parse_order_details, parse_order_list_page

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.ozon.ru"
ENTRYPOINT = "/api/entrypoint-api.bx/page/json/v2"
OZON_URL = URL(BASE_URL)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.ozon.ru/my/orderlist",
    "Origin": "https://www.ozon.ru",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def _build_session(cookies: CookieJar, timeout: float) -> aiohttp.ClientSession:
    jar = aiohttp.CookieJar(unsafe=True)
    jar.update_cookies(cookies, response_url=OZON_URL)
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    return aiohttp.ClientSession(
        headers=DEFAULT_HEADERS,
        cookies=jar,
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=timeout),
    )


class OzonOrdersClient:
    """Fetch buyer orders via Ozon entrypoint API using exported cookies."""

    def __init__(
        self,
        cookies: CookieJar,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._cookies = cookies
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._warmed_up = False

    async def __aenter__(self) -> OzonOrdersClient:
        if self._session is None:
            self._session = _build_session(self._cookies, self._timeout.total or 30.0)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def _warmup(self) -> None:
        if self._warmed_up or self._session is None:
            return
        try:
            async with self._session.get(f"{BASE_URL}/my/orderlist", allow_redirects=True) as response:
                await response.text()
        except aiohttp.ClientError as err:
            _LOGGER.debug("Ozon warmup request failed: %s", err)
        self._warmed_up = True

    async def get_page(self, page_url: str) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("Use async with OzonOrdersClient(...)")

        await self._warmup()

        encoded = quote(page_url, safe="")
        url = f"{BASE_URL}{ENTRYPOINT}?url={encoded}"
        async with self._session.get(url) as response:
            body = await response.text()
            if response.status in (401, 403):
                raise _map_access_error(response.status, body)
            if response.status != 200:
                raise OzonAntibotError(f"HTTP {response.status}: {body[:300]}")

            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                raise OzonAntibotError("Non-JSON response (likely antibot HTML)")

            data = await response.json(content_type=None)
            user = (data.get("userInfo") or {}).get("user") or {}
            if not user.get("isLoggedIn"):
                _LOGGER.warning(
                    "Ozon isLoggedIn=false, userInfo=%s, cookie_names=%s",
                    user,
                    list(self._cookies.keys()),
                )
                raise OzonAuthError(
                    "Ozon не видит авторизацию (isLoggedIn=false). "
                    "Экспортируйте все cookies с ozon.ru из браузера, где вы залогинены."
                )
            return data

    async def fetch_order_list(self, *, active_only: bool = False) -> dict[str, Any]:
        page_url = "/my/orderlist?selectedTab=active" if active_only else "/my/orderlist"
        page = await self.get_page(page_url)
        parsed = parse_order_list_page(page)
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": page_url,
            **parsed,
        }

    async def fetch_order_details(self, order_number: str) -> dict[str, Any]:
        page_url = f"/my/orderdetails/?order={order_number}"
        page = await self.get_page(page_url)
        parsed = parse_order_details(page)
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": page_url,
            **parsed,
        }

    async def fetch_all_active(self) -> dict[str, Any]:
        """Convenience payload for HA sensors."""
        data = await self.fetch_order_list(active_only=True)
        return {
            "fetched_at": data["fetched_at"],
            "summary": data["summary"],
            "orders": data["orders"],
            "tracking": data["tracking"],
        }


def _map_access_error(status: int, body: str) -> OzonAntibotError | OzonAuthError:
    snippet = body[:500].replace("\n", " ")
    _LOGGER.error("Ozon HTTP %s body: %s", status, snippet)

    lowered = body.lower()
    if status == 403:
        return OzonAntibotError(
            "HTTP 403: Ozon antibot (Variti). Cookies с ПК верные, но контейнер HA "
            "отправляет запрос не как браузер (TLS fingerprint). Нужен sidecar на хосте."
        )
    if any(marker in lowered for marker in ("variti", "puzzle", "<html", "captcha", "access denied")):
        return OzonAntibotError(f"HTTP {status}: Ozon antibot: {snippet[:200]}")
    return OzonAuthError(f"HTTP {status}: сессия отклонена — {snippet[:200]}")
