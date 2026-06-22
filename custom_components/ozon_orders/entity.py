"""Shared helpers for Ozon Orders entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription

from .const import DOMAIN, MANUFACTURER


def sanitize_order_key(order_key: str) -> str:
    return order_key.replace("-", "_").replace(" ", "_")


def hub_device_info(entry_id: str, user: dict | None = None) -> DeviceInfo:
    name = "Ozon"
    if user and user.get("first_name"):
        name = f"Ozon — {user['first_name']}"
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=name,
        manufacturer=MANUFACTURER,
        model="Buyer account",
    )


def order_device_info(entry_id: str, order: dict) -> DeviceInfo:
    order_number = order.get("order_number") or "unknown"
    status = order.get("status") or ""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id, order["order_key"])},
        name=f"Order {order_number}",
        manufacturer=MANUFACTURER,
        model=status[:60] if status else "Shipment",
        via_device=(DOMAIN, entry_id),
    )


class OzonOrderEntityDescription(EntityDescription):
    """Description with order key binding."""

    order_key: str | None = None
