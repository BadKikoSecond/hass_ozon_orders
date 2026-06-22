"""Async HTTP client for Ozon buyer order pages."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

from .cookies import CookieJar
from .errors import OzonAntibotError, OzonAuthError
from .parser import parse_order_details, parse_order_list_page
from .relay import fetch_page_via_relay

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.ozon.ru"
ENTRYPOINT = "/api/entrypoint-api.bx/page/json/v2"
BROWSER_IMPERSONATE = "chrome131"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.ozon.ru/my/orderlist",
    "Origin": "https://www.ozon.ru",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

HTML_HEADERS = {
    **DEFAULT_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class OzonOrdersClient:
    """Fetch buyer orders via Ozon entrypoint API using exported cookies."""

    def __init__(
        self,
        cookies: CookieJar,
        *,
        relay_url: str | None = None,
        session: AsyncSession | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._cookies = dict(cookies)
        self._relay_url = (relay_url or "").strip() or None
        self._session = session
        self._owns_session = session is None
        self._timeout = timeout
        self._bootstrapped = False

    async def __aenter__(self) -> OzonOrdersClient:
        if self._relay_url is None and self._session is None:
            self._session = AsyncSession(
                impersonate=BROWSER_IMPERSONATE,
                timeout=self._timeout,
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def _bootstrap_cookies(self) -> None:
        """Visit Ozon pages to pick up antibot cookies (abt_data, __Secure-ETC)."""
        if self._bootstrapped or self._session is None:
            return

        jar = dict(self._cookies)
        for index, path in enumerate(("/", "/my/orderlist")):
            headers = HTML_HEADERS if index == 0 else {
                **HTML_HEADERS,
                "Referer": f"{BASE_URL}/",
                "Sec-Fetch-Site": "same-origin",
            }
            try:
                response = await self._session.get(
                    f"{BASE_URL}{path}",
                    cookies=jar,
                    headers=headers,
                    allow_redirects=True,
                )
            except Exception as err:
                _LOGGER.debug("Ozon bootstrap %s failed: %s", path, err)
                continue

            jar.update(_session_cookies(self._session))
            if response.status_code in (401, 403):
                raise _map_access_error(response.status_code, response.text)

        self._cookies = jar
        self._bootstrapped = True

    async def get_page(self, page_url: str) -> dict[str, Any]:
        if self._relay_url:
            data = await fetch_page_via_relay(
                self._relay_url,
                page_url,
                self._cookies,
                timeout=max(self._timeout, 45.0),
            )
            return _validate_logged_in(data)

        if self._session is None:
            raise RuntimeError("Use async with OzonOrdersClient(...)")

        await self._bootstrap_cookies()

        encoded = quote(page_url, safe="")
        url = f"{BASE_URL}{ENTRYPOINT}?url={encoded}"
        response = await self._session.get(
            url,
            cookies=self._cookies,
            headers=DEFAULT_HEADERS,
            allow_redirects=True,
        )
        self._cookies.update(_session_cookies(self._session))

        body = response.text
        if response.status_code in (401, 403):
            raise _map_access_error(response.status_code, body)
        if response.status_code != 200:
            raise OzonAntibotError(f"HTTP {response.status_code}: {body[:300]}")

        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise OzonAntibotError("Non-JSON response (likely antibot HTML)")

        return _validate_logged_in(response.json())

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


def _session_cookies(session: AsyncSession) -> CookieJar:
    jar: CookieJar = {}
    try:
        items = session.cookies.items()
    except Exception:
        return jar
    for name, value in items:
        jar[str(name)] = str(value)
    return jar


def _validate_logged_in(data: dict[str, Any]) -> dict[str, Any]:
    user = (data.get("userInfo") or {}).get("user") or {}
    if not user.get("isLoggedIn"):
        _LOGGER.warning(
            "Ozon isLoggedIn=false, userInfo=%s",
            user,
        )
        raise OzonAuthError(
            "Ozon не видит авторизацию (isLoggedIn=false). "
            "Экспортируйте все cookies с ozon.ru из браузера, где вы залогинены."
        )
    return data


def _map_access_error(status: int, body: str) -> OzonAntibotError | OzonAuthError:
    snippet = body[:500].replace("\n", " ")
    _LOGGER.error("Ozon HTTP %s body: %s", status, snippet)

    lowered = body.lower()
    challenge_hint = _challenge_hint(body)
    if challenge_hint:
        return OzonAntibotError(challenge_hint)

    if status == 403:
        return OzonAntibotError(
            "HTTP 403: Ozon antibot (Variti). IP сервера в challenge — "
            "откройте ozon.ru в браузере на ПК, пройдите проверку, "
            "экспортируйте cookies заново или укажите relay URL (ПК в той же сети)."
        )
    if any(marker in lowered for marker in ("variti", "puzzle", "<html", "captcha", "access denied")):
        return OzonAntibotError(f"HTTP {status}: Ozon antibot: {snippet[:200]}")
    return OzonAuthError(f"HTTP {status}: сессия отклонена — {snippet[:200]}")


def _challenge_hint(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    if payload.get("challengeURL") or payload.get("captchaURL"):
        return (
            "Ozon challenge: сервер заблокирован антиботом. "
            "На ПК откройте ozon.ru и пройдите проверку, затем экспортируйте cookies. "
            "Если HA на VPS/датацентре — запустите scripts/ozon_relay.py на домашнем ПК "
            "и укажите relay URL в настройках интеграции."
        )
    return None
