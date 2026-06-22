"""DataUpdateCoordinator for Ozon buyer orders."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import OzonOrdersClient
from .api.cookies import load_cookies, session_expiry_info
from .api.errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .const import CONF_COOKIES_FILE, DEFAULT_COOKIES_FILE, DEFAULT_SCAN_INTERVAL, DOMAIN

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

    @property
    def cookies_path(self) -> str:
        filename = self.entry.data.get(CONF_COOKIES_FILE, DEFAULT_COOKIES_FILE)
        return self.hass.config.path(filename)

    async def _async_update_data(self) -> dict[str, Any]:
        path = self.cookies_path
        try:
            cookies = await self.hass.async_add_executor_job(load_cookies, path)
            session = await self.hass.async_add_executor_job(session_expiry_info, path)
            async with OzonOrdersClient(cookies) as client:
                payload = await client.fetch_order_list(active_only=True)
        except (OzonAuthError, OzonAntibotError, OzonOrdersError, OSError, ValueError) as err:
            self.connection_ok = False
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err

        self.connection_ok = True
        self.last_error = None

        orders: dict[str, dict[str, Any]] = {}
        for index, order in enumerate(payload.get("orders") or []):
            order_number = order.get("order_number")
            if not order_number:
                continue
            key = f"{order_number}_{index}"
            orders[key] = {**order, "order_key": key}

        return {
            "orders": orders,
            "summary": payload.get("summary") or {},
            "tracking": payload.get("tracking") or [],
            "user": payload.get("user") or {},
            "session": session,
            "fetched_at": payload.get("fetched_at"),
        }
