"""Enrich order list tiles with details (date, product titles)."""

from __future__ import annotations

from typing import Any


def enrich_order_from_details(
    order: dict[str, Any],
    details: dict[str, Any],
    *,
    sibling_count: int,
) -> None:
    """Merge order-details page data into a list tile."""
    order["order_title"] = details.get("order_title")
    order["order_date"] = details.get("order_date")

    shipments = details.get("shipments") or []
    shipment = _match_shipment(order, shipments)
    if shipment:
        order["products"] = list(shipment.get("products") or [])
        order["shipment_id"] = shipment.get("shipment_id")
    elif shipments:
        products: list[dict[str, Any]] = []
        for item in shipments:
            products.extend(item.get("products") or [])
        order["products"] = products

    order["device_name"] = _device_name(order, sibling_count)


def _device_name(order: dict[str, Any], sibling_count: int) -> str:
    order_date = order.get("order_date")
    status = (order.get("status") or "").strip()
    order_number = order.get("order_number") or "?"

    if order_date and sibling_count > 1 and status:
        return f"{order_date} — {status}"
    if order_date:
        return order_date
    return f"Заказ {order_number}"


def _match_shipment(order: dict[str, Any], shipments: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not shipments:
        return None
    if len(shipments) == 1:
        return shipments[0]

    tile_images = {
        item.get("image_url")
        for item in order.get("tile_products") or []
        if item.get("image_url")
    }
    if tile_images:
        best: dict[str, Any] | None = None
        best_overlap = 0
        for shipment in shipments:
            ship_images = {
                product.get("image_url")
                for product in shipment.get("products") or []
                if product.get("image_url")
            }
            overlap = len(tile_images & ship_images)
            if overlap > best_overlap:
                best_overlap = overlap
                best = shipment
        if best and best_overlap > 0:
            return best

    tile_status = _normalize_status(order.get("status"))
    for shipment in shipments:
        ship_status = _normalize_status(shipment.get("status"))
        if ship_status and (ship_status in tile_status or tile_status in ship_status):
            return shipment

    tile_count = order.get("products_count") or len(order.get("tile_products") or [])
    for shipment in shipments:
        ship_count = len(shipment.get("products") or [])
        if tile_count and ship_count == tile_count:
            return shipment

    return None


def _normalize_status(value: str | None) -> str:
    if not value:
        return ""
    return value.lower().replace("\u00a0", " ").strip()
