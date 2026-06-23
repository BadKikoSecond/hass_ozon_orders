"""DataUpdateCoordinator for Ozon buyer orders."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import OzonOrdersClient
from .api.cookies import load_cookies, session_expiry_info
from .api.enrich import enrich_order_from_details
from .api.errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .const import (
    ANTIBOT_BACKOFF_BASE_MINUTES,
    ANTIBOT_BACKOFF_MAX_MINUTES,
    CONF_COOKIES,
    CONF_DETAILS_BATCH_SIZE,
    CONF_DETAILS_TTL_HOURS,
    CONF_FETCH_DETAILS,
    CONF_MANUAL_REFRESH_MINUTES,
    CONF_REQUEST_DELAY_SEC,
    CONF_SCAN_INTERVAL,
    DEFAULT_DETAILS_BATCH_SIZE,
    DEFAULT_DETAILS_TTL_HOURS,
    DEFAULT_FETCH_DETAILS,
    DEFAULT_MANUAL_REFRESH_MINUTES,
    DEFAULT_REQUEST_DELAY_SEC,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
_UTC = timezone.utc


@dataclass(slots=True)
class _DetailsCacheEntry:
    data: dict[str, Any]
    fetched_at: datetime
    list_fingerprint: str


class OzonOrdersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch Ozon order list and session metadata."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        options = entry.options
        scan_minutes = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._normal_interval = timedelta(minutes=scan_minutes)
        self._fetch_details = options.get(CONF_FETCH_DETAILS, DEFAULT_FETCH_DETAILS)
        self._details_ttl = timedelta(
            hours=options.get(CONF_DETAILS_TTL_HOURS, DEFAULT_DETAILS_TTL_HOURS)
        )
        configured_batch = options.get(CONF_DETAILS_BATCH_SIZE, DEFAULT_DETAILS_BATCH_SIZE)
        self._details_batch_size = 0 if not self._fetch_details else configured_batch
        self._request_delay = options.get(CONF_REQUEST_DELAY_SEC, DEFAULT_REQUEST_DELAY_SEC)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=self._normal_interval,
        )
        self.entry = entry
        self.connection_ok = True
        self.last_error: str | None = None
        self.backoff_until: datetime | None = None

        self._client: OzonOrdersClient | None = None
        self._details_cache: dict[str, _DetailsCacheEntry] = {}
        self._antibot_strikes = 0

    @property
    def cookies_raw(self) -> str:
        return self.entry.data[CONF_COOKIES]

    @property
    def manual_refresh_minutes(self) -> int:
        return self.entry.options.get(
            CONF_MANUAL_REFRESH_MINUTES, DEFAULT_MANUAL_REFRESH_MINUTES
        )

    async def async_shutdown(self) -> None:
        """Close the persistent HTTP session."""
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(_UTC)
        if self.backoff_until and now < self.backoff_until:
            if self.data:
                _LOGGER.debug(
                    "Ozon antibot backoff until %s — keeping previous data",
                    self.backoff_until.isoformat(),
                )
                return self.data
            raise UpdateFailed(
                f"Ozon antibot backoff until {self.backoff_until.isoformat(timespec='minutes')}"
            )

        cookies_raw = self.cookies_raw
        try:
            cookies = await self.hass.async_add_executor_job(load_cookies, cookies_raw)
            session = await self.hass.async_add_executor_job(session_expiry_info, cookies_raw)
            client = await self._ensure_client(cookies)
            payload = await client.fetch_order_list(active_only=True)
            orders = await self._build_orders(client, payload.get("orders") or [])
        except OzonAntibotError as err:
            self._register_antibot_backoff(now)
            self.connection_ok = False
            self.last_error = str(err)
            _LOGGER.error("Ozon update failed (antibot): %s", err)
            raise UpdateFailed(str(err)) from err
        except (OzonAuthError, OzonOrdersError, OSError, ValueError) as err:
            self.connection_ok = False
            self.last_error = str(err)
            _LOGGER.error("Ozon update failed: %s", err)
            raise UpdateFailed(str(err)) from err

        self._register_success(now)
        self.connection_ok = True
        self.last_error = None

        return {
            "orders": orders,
            "summary": payload.get("summary") or {},
            "tracking": payload.get("tracking") or [],
            "user": payload.get("user") or {},
            "session": session,
            "fetched_at": payload.get("fetched_at"),
        }

    async def _ensure_client(self, cookies: dict[str, str]) -> OzonOrdersClient:
        if self._client is None:
            self._client = OzonOrdersClient(cookies)
            await self._client.__aenter__()
        else:
            self._client.set_cookies(cookies)
        return self._client

    def _register_antibot_backoff(self, now: datetime) -> None:
        self._antibot_strikes += 1
        backoff_minutes = min(
            ANTIBOT_BACKOFF_BASE_MINUTES * (2 ** (self._antibot_strikes - 1)),
            ANTIBOT_BACKOFF_MAX_MINUTES,
        )
        self.backoff_until = now + timedelta(minutes=backoff_minutes)
        backoff_interval = timedelta(minutes=backoff_minutes)
        self.async_set_update_interval(backoff_interval)
        _LOGGER.warning(
            "Ozon antibot strike %s — pausing polls for %s minutes",
            self._antibot_strikes,
            backoff_minutes,
        )

    def _register_success(self, now: datetime) -> None:
        if self._antibot_strikes:
            _LOGGER.info("Ozon poll recovered after antibot backoff")
        self._antibot_strikes = 0
        self.backoff_until = None
        if self.update_interval != self._normal_interval:
            self.async_set_update_interval(self._normal_interval)

    async def _build_orders(
        self,
        client: OzonOrdersClient,
        order_list: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        orders: dict[str, dict[str, Any]] = {}
        for index, order in enumerate(order_list):
            order_number = order.get("order_number")
            if not order_number:
                continue
            key = f"{order_number}_{index}"
            orders[key] = {**order, "order_key": key}

        siblings: dict[str, list[str]] = defaultdict(list)
        for key, order in orders.items():
            if order_number := order.get("order_number"):
                siblings[order_number].append(key)

        refresh_queue = self._details_refresh_queue(orders, siblings)
        fetched = 0
        for order_number in refresh_queue:
            if fetched >= self._details_batch_size:
                break
            if fetched:
                await asyncio.sleep(self._request_delay)
            details = await self._fetch_order_details(client, order_number, orders, siblings)
            if details is not None:
                fetched += 1

        for order_number, keys in siblings.items():
            cache_entry = self._details_cache.get(order_number)
            if not cache_entry:
                for key in keys:
                    orders[key]["device_name"] = f"Заказ {order_number}"
                continue
            for key in keys:
                enrich_order_from_details(
                    orders[key],
                    cache_entry.data,
                    sibling_count=len(keys),
                )

        return orders

    def _details_refresh_queue(
        self,
        orders: dict[str, dict[str, Any]],
        siblings: dict[str, list[str]],
    ) -> list[str]:
        """Prioritize never-seen orders, then stale/changed ones."""
        now = datetime.now(_UTC)
        ttl = self._details_ttl
        missing: list[str] = []
        stale: list[str] = []

        for order_number, keys in siblings.items():
            fingerprint = self._list_fingerprint(orders[keys[0]])
            cache_entry = self._details_cache.get(order_number)
            if cache_entry is None:
                missing.append(order_number)
                continue
            if now - cache_entry.fetched_at > ttl:
                stale.append(order_number)
                continue
            if cache_entry.list_fingerprint != fingerprint:
                stale.append(order_number)

        return missing + stale

    async def _fetch_order_details(
        self,
        client: OzonOrdersClient,
        order_number: str,
        orders: dict[str, dict[str, Any]],
        siblings: dict[str, list[str]],
    ) -> dict[str, Any] | None:
        keys = siblings.get(order_number) or []
        fingerprint = self._list_fingerprint(orders[keys[0]]) if keys else ""

        try:
            details = await client.fetch_order_details(order_number)
        except OzonAntibotError:
            raise
        except (OzonAuthError, OzonOrdersError) as err:
            _LOGGER.warning("Ozon details for %s failed: %s", order_number, err)
            return None

        self._details_cache[order_number] = _DetailsCacheEntry(
            data=details,
            fetched_at=datetime.now(_UTC),
            list_fingerprint=fingerprint,
        )
        return details

    @staticmethod
    def _list_fingerprint(order: dict[str, Any]) -> str:
        return "|".join(
            str(order.get(field) or "")
            for field in ("status", "eta_text", "products_count", "payment_status")
        )
