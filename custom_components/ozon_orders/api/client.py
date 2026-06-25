"""Async HTTP client for Ozon buyer order pages."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

from .cookies import (
    CookieJar,
    access_token_part6,
    parse_set_cookie_header,
)
from .errors import OzonAntibotError, OzonAuthError
from .parser import parse_order_details, parse_order_list_page

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.ozon.ru"
ENTRYPOINT = "/api/entrypoint-api.bx/page/json/v2"
SUMMARY_URL = f"{BASE_URL}/api/composer-api.bx/_action/summary"
BROWSER_IMPERSONATE = "firefox133"

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{BASE_URL}/my/orderlist",
    "Origin": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class OzonOrdersClient:
    """Fetch buyer orders via Ozon entrypoint API using exported cookies."""

    def __init__(
        self,
        cookies: CookieJar,
        *,
        session: AsyncSession | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._cookies = dict(cookies)
        self._session = session
        self._owns_session = session is None
        self._timeout = timeout
        self._last_refresh_attempt: datetime | None = None

    @property
    def cookies(self) -> CookieJar:
        return dict(self._cookies)

    def merge_config_cookies(self, config: CookieJar) -> None:
        """Apply cookies from HA config when user re-imports an export."""
        runtime_p6 = access_token_part6(self._cookies)
        config_p6 = access_token_part6(config)
        if config_p6 and (not runtime_p6 or config_p6 >= runtime_p6):
            self._cookies.update(config)
            return
        for name, value in config.items():
            if name not in ("__Secure-access-token", "__Secure-refresh-token"):
                self._cookies[name] = value

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

    def _merge_response_cookies(self, response: Any) -> bool:
        """Merge Set-Cookie from response. Return True if access-token rotated."""
        updates = parse_set_cookie_header(response.headers.get("set-cookie"))
        if not updates:
            return False
        previous = self._cookies.get("__Secure-access-token")
        self._cookies.update(updates)
        current = self._cookies.get("__Secure-access-token")
        return bool(current and current != previous)

    async def maybe_refresh_session(self) -> bool:
        """Try silent refresh via ``GET _action/summary`` (after part[6] expiry).

        Observed in browser soak: no Set-Cookie while token is alive; new
        ``__Secure-access-token`` arrives once operational expiry has passed.
        """
        if self._session is None:
            raise RuntimeError("Use async with OzonOrdersClient(...)")

        now = datetime.now(timezone.utc)
        if self._last_refresh_attempt and (now - self._last_refresh_attempt).total_seconds() < 60:
            return False
        self._last_refresh_attempt = now

        try:
            response = await self._session.get(
                SUMMARY_URL,
                cookies=self._cookies,
                headers=DEFAULT_HEADERS,
            )
        except Exception as err:
            _LOGGER.debug("Ozon summary refresh request failed: %s", err)
            return False

        if response.status_code in (401, 403):
            _LOGGER.debug(
                "Ozon summary refresh HTTP %s (no rotation)",
                response.status_code,
            )
            return False

        rotated = self._merge_response_cookies(response)
        if rotated:
            _LOGGER.info(
                "Ozon access-token rotated via _action/summary part6=%s",
                access_token_part6(self._cookies),
            )
        return rotated

    async def get_page(self, page_url: str) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("Use async with OzonOrdersClient(...)")

        encoded = quote(page_url, safe="")
        url = f"{BASE_URL}{ENTRYPOINT}?url={encoded}"
        response = await self._session.get(
            url,
            cookies=self._cookies,
            headers=DEFAULT_HEADERS,
        )
        self._merge_response_cookies(response)

        body = response.text
        if response.status_code in (401, 403):
            raise _map_access_error(response.status_code, body)
        if response.status_code != 200:
            raise OzonAntibotError(f"HTTP {response.status_code}: {body[:300]}")

        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise OzonAntibotError("Non-JSON response (likely antibot HTML)")

        data = response.json()
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
            "HTTP 403: Ozon antibot (Variti). Обновите cookies из браузера, "
            "где вы уже прошли проверку."
        )
    if any(marker in lowered for marker in ("variti", "puzzle", "<html", "captcha", "access denied")):
        return OzonAntibotError(f"HTTP {status}: Ozon antibot: {snippet[:200]}")
    return OzonAuthError(f"HTTP {status}: сессия отклонена — {snippet[:200]}")
