"""DataUpdateCoordinator for Ozon buyer orders."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import OzonOrdersClient
from .api.cookies import load_cookies, session_expiry_info
from .api.enrich import enrich_order_from_details
from .api.errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .const import CONF_COOKIES, CONF_RELAY_URL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class OzonOrdersCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch Ozon order list and session metadata."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        scan_minutes = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
        )
        self.entry = entry
        self.connection_ok = True
        self.last_error: str | None = None
        self._details_cache: dict[str, dict[str, Any]] = {}

    @property
    def cookies_raw(self) -> str:
        return self.entry.data[CONF_COOKIES]

    async def _async_update_data(self) -> dict[str, Any]:
        cookies_raw = self.cookies_raw
        try:
            cookies = await self.hass.async_add_executor_job(load_cookies, cookies_raw)
            session = await self.hass.async_add_executor_job(session_expiry_info, cookies_raw)
            relay_url = self.entry.options.get(CONF_RELAY_URL) or None
            async with OzonOrdersClient(cookies, relay_url=relay_url) as client:
                payload = await client.fetch_order_list(active_only=True)
                orders = await self._build_orders(client, payload.get("orders") or [])
        except (OzonAuthError, OzonAntibotError, OzonOrdersError, OSError, ValueError) as err:
            self.connection_ok = False
            self.last_error = str(err)
            _LOGGER.error("Ozon update failed: %s", err)
            raise UpdateFailed(str(err)) from err

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

        for order_number, keys in siblings.items():
            details = await self._get_order_details(client, order_number)
            if not details:
                for key in keys:
                    orders[key]["device_name"] = f"Заказ {order_number}"
                continue

            for key in keys:
                enrich_order_from_details(
                    orders[key],
                    details,
                    sibling_count=len(keys),
                )

        return orders

    async def _get_order_details(
        self,
        client: OzonOrdersClient,
        order_number: str,
    ) -> dict[str, Any] | None:
        if order_number in self._details_cache:
            return self._details_cache[order_number]

        try:
            details = await client.fetch_order_details(order_number)
        except (OzonAuthError, OzonAntibotError, OzonOrdersError) as err:
            _LOGGER.warning("Ozon details for %s failed: %s", order_number, err)
            return None

        self._details_cache[order_number] = details
        return details
