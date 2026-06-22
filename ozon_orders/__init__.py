"""Ozon buyer orders client — cookies + entrypoint API, HACS-friendly (aiohttp only)."""

from .client import OzonOrdersClient
from .cookies import load_cookies
from .errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .parser import parse_order_details, parse_order_list_page, parse_order_tracking

__all__ = [
    "OzonOrdersClient",
    "OzonAntibotError",
    "OzonAuthError",
    "OzonOrdersError",
    "load_cookies",
    "parse_order_details",
    "parse_order_list_page",
    "parse_order_tracking",
]

__version__ = "0.1.0"
