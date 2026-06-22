"""Ozon buyer orders integration for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIES, DOMAIN, SERVICE_REFRESH
from .coordinator import OzonOrdersCoordinator

_LOGGER = logging.getLogger(__name__)

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
        async def _handle_refresh(_call) -> None:
            for entry_data in hass.data[DOMAIN].values():
                await entry_data["coordinator"].async_request_refresh()

        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
