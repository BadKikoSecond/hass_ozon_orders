"""Ozon buyer orders integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SERVICE_REFRESH
from .coordinator import OzonOrdersCoordinator
from .entity_manager import OzonOrderEntityManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


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


def register_platform_add_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    platform: str,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Store platform callbacks and start the order entity manager when both are ready."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    key = f"{platform}_add_entities"
    entry_data[key] = async_add_entities

    if entry_data["order_manager"] is not None:
        return
    if not entry_data["sensor_add_entities"] or not entry_data["binary_add_entities"]:
        return

    manager = OzonOrderEntityManager(
        entry_data["coordinator"],
        entry_data["sensor_add_entities"],
        entry_data["binary_add_entities"],
    )
    manager.async_setup()
    entry_data["order_manager"] = manager


def get_coordinator(hass: HomeAssistant, entry_id: str) -> OzonOrdersCoordinator:
    return hass.data[DOMAIN][entry_id]["coordinator"]
