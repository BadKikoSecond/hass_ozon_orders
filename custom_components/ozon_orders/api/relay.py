"""Optional LAN relay for Ozon requests (run scripts/ozon_relay.py on a PC)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout

from .cookies import CookieJar
from .errors import OzonAntibotError, OzonOrdersError

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45.0


async def fetch_page_via_relay(
    relay_url: str,
    page_url: str,
    cookies: CookieJar,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Ask a local relay (PC on the same LAN) to call Ozon entrypoint API."""
    base = relay_url.rstrip("/")
    url = urljoin(f"{base}/", "fetch")
    payload = {"page_url": page_url, "cookies": cookies}

    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as session:
            async with session.post(url, json=payload) as response:
                body = await response.text()
                if response.status != 200:
                    raise OzonOrdersError(
                        f"Relay HTTP {response.status}: {body[:300]}"
                    )
                data = await response.json()
    except ClientError as err:
        raise OzonOrdersError(f"Relay unreachable at {base}: {err}") from err

    if not data.get("success"):
        error = data.get("error") or "unknown relay error"
        error_type = data.get("error_type", "")
        if error_type == "antibot":
            raise OzonAntibotError(error)
        raise OzonOrdersError(error)

    page = data.get("data")
    if not isinstance(page, dict):
        raise OzonOrdersError("Relay returned invalid payload")

    return page
