"""Parse Ozon entrypoint ``widgetStates`` into HA-friendly structures."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

_ORDER_LINK_RE = re.compile(r"order=([0-9]+-[0-9]+)")
_AT_PICKUP_RE = re.compile(
    r"(ожидает в пункте|можно забирать|хранится до|готов к получению|в пункте выдачи)",
    re.I,
)
_IN_TRANSIT_RE = re.compile(
    r"(в пути|переда[её]м|переда[её]тся|в службе доставки|в сборке|доставля)",
    re.I,
)
_STORAGE_UNTIL_RE = re.compile(r"хранится до\s+(.+)", re.I)


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")


def parse_widget_states(page: dict[str, Any]) -> dict[str, Any]:
    raw = page.get("widgetStates") or {}
    parsed: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                parsed[key] = value
        else:
            parsed[key] = value
    return parsed


def find_widget(parsed_states: dict[str, Any], prefix: str) -> tuple[str, Any] | None:
    for key, value in parsed_states.items():
        if key.startswith(prefix):
            return key, value
    return None


def atom_text(node: Any) -> str | None:
    if node is None:
        return None
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "text" in node and isinstance(node["text"], str):
            return node["text"]
        for child in node.values():
            text = atom_text(child)
            if text:
                return text
    if isinstance(node, list):
        for child in node:
            text = atom_text(child)
            if text:
                return text
    return None


def extract_order_number(link: str | None) -> str | None:
    if not link:
        return None
    match = _ORDER_LINK_RE.search(link)
    return match.group(1) if match else None


def parse_order_list_page(page: dict[str, Any]) -> dict[str, Any]:
    states = parse_widget_states(page)
    _, order_list = find_widget(states, "orderList-") or (None, None)
    _, tracking = find_widget(states, "orderTracking-") or (None, None)
    _, paginator = find_widget(states, "paginator-") or (None, None)

    orders = _parse_order_tiles(order_list)
    tracking_items = parse_order_tracking(tracking) if tracking else []

    return {
        "user": _user_info(page),
        "page": page.get("pageInfo"),
        "next_page": (paginator or {}).get("nextPage"),
        "orders": orders,
        "tracking": tracking_items,
        "summary": _build_summary(orders, tracking_items),
    }


def parse_order_details(page: dict[str, Any]) -> dict[str, Any]:
    states = parse_widget_states(page)
    shipments = []
    for key, value in states.items():
        if key.startswith("shipmentWidget-"):
            shipments.append(_parse_shipment_widget(value, key))

    _, address_widget = find_widget(states, "orderDetailsItem-") or (None, None)
    pickup_address = None
    if address_widget:
        pickup_address = atom_text((address_widget or {}).get("cell", {}).get("subtitle"))

    _, total_widget = find_widget(states, "orderDoneTotal-") or (None, None)
    total_price = None
    if total_widget:
        total_price = atom_text((total_widget or {}).get("total", {}).get("right", {}).get("price"))

    order_number = None
    page_url = (page.get("pageInfo") or {}).get("url", "")
    if "order=" in page_url:
        order_number = parse_qs(urlparse(page_url).query).get("order", [None])[0]

    return {
        "user": _user_info(page),
        "order_number": order_number,
        "pickup_address": pickup_address,
        "total_price_text": total_price,
        "shipments": shipments,
        "summary": _build_summary_from_shipments(shipments),
    }


def parse_order_tracking(tracking: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not tracking:
        return []

    items = []
    for entry in tracking.get("list") or []:
        title = atom_text(entry.get("title"))
        subtitle = atom_text(entry.get("subtitle"))
        description = atom_text(entry.get("description"))
        link = (entry.get("action") or {}).get("link")
        order_number = extract_order_number(link)
        status_blob = _normalize_text(" ".join(filter(None, [title, subtitle, description])))

        items.append(
            {
                "order_number": order_number,
                "status": title,
                "delivery_type": subtitle,
                "eta_text": description,
                "detail_url": link,
                "is_at_pickup_point": bool(_AT_PICKUP_RE.search(status_blob)),
                "storage_until": _storage_until(description or subtitle or title),
            }
        )
    return items


def _parse_order_tiles(order_list: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not order_list:
        return []

    tiles = []
    for tile in order_list.get("ordersV2") or []:
        left = tile.get("leftBlock") or {}
        right = tile.get("rightBlock") or {}
        common = tile.get("common") or {}

        status = atom_text(left.get("textIcon", {}).get("text")) or atom_text(left.get("textIcon"))
        delivery_type = atom_text(left.get("title"))
        eta_text = atom_text(left.get("subtitle"))
        timeline = left.get("timeline") or {}
        link = (common.get("action") or {}).get("link")
        order_number = extract_order_number(link)
        if not order_number:
            auto_id = (common.get("testInfo") or {}).get("automatizationId", "")
            if auto_id.startswith("orderListTile_"):
                order_number = auto_id.removeprefix("orderListTile_")

        products = ((right.get("products") or {}).get("products")) or []
        payment_status = atom_text((products[0] or {}).get("badgeStatus")) if products else None
        status_blob = _normalize_text(" ".join(filter(None, [status, eta_text, delivery_type])))

        tiles.append(
            {
                "order_number": order_number,
                "status": status,
                "delivery_type": delivery_type,
                "eta_text": eta_text,
                "is_at_pickup_point": bool(_AT_PICKUP_RE.search(status_blob)),
                "is_in_transit": bool(_IN_TRANSIT_RE.search(status_blob)),
                "storage_until": _storage_until(eta_text or status),
                "timeline_step": timeline.get("currentStep"),
                "timeline_steps": [
                    (step.get("title") or {}).get("text")
                    for step in (timeline.get("steps") or [])
                    if (step.get("title") or {}).get("text")
                ],
                "products_count": len(products),
                "payment_status": payment_status,
                "detail_url": link,
            }
        )
    return tiles


def _parse_shipment_widget(widget: dict[str, Any], widget_key: str) -> dict[str, Any]:
    header = (widget.get("header") or [{}])[0]
    text_icon = header.get("textIcon") or {}
    status = atom_text(text_icon.get("text"))
    timeline = text_icon.get("timeline") or {}
    shipment_id = widget.get("shipmentId")
    if not shipment_id:
        auto_id = (widget.get("testInfo") or {}).get("automatizationId", "")
        if auto_id.startswith("shipment-id-"):
            shipment_id = auto_id.removeprefix("shipment-id-")

    products = []
    for block in widget.get("items") or []:
        for seller in block.get("sellers") or []:
            for product in seller.get("products") or []:
                products.append(
                    {
                        "title": atom_text((product.get("title") or {}).get("name")),
                        "price_text": atom_text(product.get("price")),
                    }
                )

    current_step = timeline.get("currentStep")
    steps = timeline.get("steps") or []
    current_title = None
    storage_note = None
    if isinstance(current_step, int) and 0 <= current_step < len(steps):
        current_title = (steps[current_step].get("title") or {}).get("text")
        storage_note = steps[current_step].get("subtitle")

    return {
        "widget_id": widget_key,
        "shipment_id": shipment_id,
        "status": status,
        "current_step": current_title,
        "storage_note": storage_note,
        "timeline_step": current_step,
        "products": products,
    }


def _storage_until(text: str | None) -> str | None:
    if not text:
        return None
    match = _STORAGE_UNTIL_RE.search(text)
    return match.group(1).strip() if match else None


def _user_info(page: dict[str, Any]) -> dict[str, Any]:
    user = (page.get("userInfo") or {}).get("user") or {}
    return {
        "user_id": user.get("userId"),
        "is_logged_in": user.get("isLoggedIn"),
        "email": user.get("email"),
        "first_name": user.get("firstName"),
    }


def _build_summary(orders: list[dict[str, Any]], tracking: list[dict[str, Any]]) -> dict[str, int]:
    at_pickup = sum(1 for o in orders if o.get("is_at_pickup_point"))
    in_transit = sum(1 for o in orders if o.get("is_in_transit"))
    return {
        "orders_on_page": len(orders),
        "at_pickup_point": at_pickup,
        "in_transit": in_transit,
        "tracking_items": len(tracking),
    }


def _build_summary_from_shipments(shipments: list[dict[str, Any]]) -> dict[str, int]:
    at_pickup = sum(
        1
        for s in shipments
        if s.get("current_step") == "ОЖИДАЕТ В ПУНКТЕ ВЫДАЧИ"
    )
    in_transit = sum(
        1
        for s in shipments
        if s.get("current_step") in {"В ПУТИ", "ПЕРЕДАЁТСЯ В ДОСТАВКУ", "ПЕРЕДАЕТСЯ В ДОСТАВКУ", "В СБОРКЕ"}
    )
    return {
        "shipments": len(shipments),
        "at_pickup_point": at_pickup,
        "in_transit": in_transit,
    }
