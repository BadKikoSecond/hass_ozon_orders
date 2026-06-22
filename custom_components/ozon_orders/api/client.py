"""Async HTTP client for Ozon buyer order pages."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession, Cookies

from .cookies import CookieJar
from .errors import OzonAntibotError, OzonAuthError
from .parser import parse_order_details, parse_order_list_page

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.ozon.ru"
ENTRYPOINT = "/api/entrypoint-api.bx/page/json/v2"
# Latest Chrome JA3 fingerprint — tracks curl_cffi updates automatically.
BROWSER_IMPERSONATE = "chrome"

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.ozon.ru/my/orderlist",
    "Origin": "https://www.ozon.ru",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class OzonOrdersClient:
    """Fetch buyer orders via Ozon entrypoint API using exported cookies."""

    def __init__(
        self,
        cookies: CookieJar | Cookies,
        *,
        session: AsyncSession | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._cookies = cookies
        self._session = session
        self._owns_session = session is None
        self._timeout = timeout

    async def __aenter__(self) -> OzonOrdersClient:
        if self._session is None:
            self._session = AsyncSession(
                impersonate=BROWSER_IMPERSONATE,
                timeout=self._timeout,
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def get_page(self, page_url: str) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("Use async with OzonOrdersClient(...)")

        encoded = quote(page_url, safe="")
        url = f"{BASE_URL}{ENTRYPOINT}?url={encoded}"
        response = await self._session.get(
            url,
            cookies=self._cookies,
            headers=DEFAULT_HEADERS,
            allow_redirects=True,
        )

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


def _validate_logged_in(data: dict[str, Any]) -> dict[str, Any]:
    user = (data.get("userInfo") or {}).get("user") or {}
    if not user.get("isLoggedIn"):
        _LOGGER.warning("Ozon isLoggedIn=false, userInfo=%s", user)
        raise OzonAuthError(
            "Ozon не видит авторизацию (isLoggedIn=false). "
            "Откройте ozon.ru/my/orderlist в браузере, убедитесь что заказы видны, "
            "сразу экспортируйте все cookies домена .ozon.ru."
        )
    return data


def _map_access_error(status: int, body: str) -> OzonAntibotError | OzonAuthError:
    snippet = body[:500].replace("\n", " ")
    _LOGGER.error("Ozon HTTP %s body: %s", status, snippet)

    challenge_hint = _challenge_hint(body)
    if challenge_hint:
        return OzonAntibotError(challenge_hint)

    lowered = body.lower()
    if status == 403:
        return OzonAntibotError(
            "HTTP 403: Ozon antibot (Variti). Откройте ozon.ru в браузере на этом же "
            "компьютере/сети, пройдите проверку, подождите 10–15 минут и экспортируйте "
            "cookies заново. Повторные попытки из HA только усугубляют блокировку."
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
            "Ozon challenge: IP попал в антибот. Без браузера это не обойти — "
            "на ПК откройте ozon.ru, пройдите проверку, подождите 10–15 минут, "
            "экспортируйте все cookies .ozon.ru и обновите интеграцию. "
            "Не жмите «Повторить» много раз подряд."
        )
    return None
