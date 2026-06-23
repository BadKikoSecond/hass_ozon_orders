"""Ozon buyer orders integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIES, DOMAIN, SERVICE_REFRESH
from .coordinator import OzonOrdersCoordinator

_LOGGER = logging.getLogger(__name__)
_UTC = timezone.utc

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate v1 file-based cookies to v2 pasted cookies storage."""
    if config_entry.version != 1:
        return True

    cookies_file = config_entry.data.get("cookies_file", "ozon_cookies.json")
    path = hass.config.path(cookies_file)
    if not Path(path).is_file():
        _LOGGER.error("Migration failed: cookies file %s not found", path)
        return False

    cookies_raw = Path(path).read_text(encoding="utf-8")
    hass.config_entries.async_update_entry(
        config_entry,
        data={CONF_COOKIES: cookies_raw},
        version=2,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = OzonOrdersCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "sensor_add_entities": None,
        "binary_add_entities": None,
        "order_manager": None,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        last_manual_refresh: dict[str, datetime] = {}

        async def _handle_refresh(_call) -> None:
            now = datetime.now(_UTC)
            min_gap = timedelta(minutes=coordinator.manual_refresh_minutes)
            for entry_id, entry_data in hass.data[DOMAIN].items():
                coordinator: OzonOrdersCoordinator = entry_data["coordinator"]
                if coordinator.backoff_until and now < coordinator.backoff_until:
                    _LOGGER.warning(
                        "Ozon refresh skipped for %s: antibot backoff until %s",
                        entry_id,
                        coordinator.backoff_until.isoformat(timespec="minutes"),
                    )
                    continue
                previous = last_manual_refresh.get(entry_id)
                if previous and now - previous < min_gap:
                    _LOGGER.warning(
                        "Ozon refresh skipped for %s: wait %s minutes between manual refreshes",
                        entry_id,
                        coordinator.manual_refresh_minutes,
                    )
                    continue
                last_manual_refresh[entry_id] = now
                await coordinator.async_request_refresh()

        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        await hass.data[DOMAIN][entry.entry_id]["coordinator"].async_shutdown()
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
