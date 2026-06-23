#!/usr/bin/env python3
"""Reproduce Ozon antibot responses locally for integration testing.

Reliable triggers on a home IP (without getting IP-banned):
  direct   — curl_cffi straight to entrypoint-api, NO warmup (403 + challengeURL)
  aiohttp  — plain aiohttp, no TLS impersonation (redirect loop)
  strip    — curl_cffi without abt_data cookie (403 + challengeURL)

The normal client path (warmup + curl_cffi) usually still works on a home IP even
when the modes above fail — that matches "works on PC, 403 on HA" behaviour.

Usage:
  python scripts/test_antibot.py direct cookie.json
  python scripts/test_antibot.py aiohttp cookie.json
  python scripts/test_antibot.py client cookie.json          # baseline: should OK
  python scripts/test_antibot.py hammer cookie.json --seconds 120 --interval 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ozon_orders import OzonAntibotError, OzonAuthError, OzonOrdersClient, load_cookies

API_PATH = "/my/orderlist?selectedTab=active"
API_URL = (
    "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
    f"?url={quote(API_PATH, safe='')}"
)
WARMUP_URL = "https://www.ozon.ru/my/orderlist"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.ozon.ru/my/orderlist",
    "Origin": "https://www.ozon.ru",
    "X-Requested-With": "XMLHttpRequest",
}


def _snippet(body: str, limit: int = 160) -> str:
    return body[:limit].replace("\n", " ")


async def mode_direct(cookies: dict[str, str]) -> None:
    from curl_cffi.requests import AsyncSession

    print("=== direct (curl_cffi, no warmup) ===")
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(API_URL, cookies=cookies, headers=HEADERS)
        body = response.text
        print(f"status={response.status_code}")
        print(f"content-type={response.headers.get('Content-Type', '')}")
        print(f"body={_snippet(body)}")
        if response.status_code == 403 and "challengeURL" in body:
            print("-> antibot challenge JSON (typical HA/VPS 403)")


async def mode_strip(cookies: dict[str, str]) -> None:
    from curl_cffi.requests import AsyncSession

    stripped = {k: v for k, v in cookies.items() if k != "abt_data"}
    print("=== strip abt_data (curl_cffi, no warmup) ===")
    print(f"cookies: {len(cookies)} -> {len(stripped)} (removed abt_data)")
    async with AsyncSession(impersonate="chrome120") as session:
        response = await session.get(API_URL, cookies=stripped, headers=HEADERS)
        body = response.text
        print(f"status={response.status_code}")
        print(f"body={_snippet(body)}")


async def mode_aiohttp(cookies: dict[str, str]) -> None:
    import aiohttp
    from yarl import URL

    print("=== aiohttp (no TLS impersonation) ===")
    jar = aiohttp.CookieJar(unsafe=True)
    jar.update_cookies(cookies, response_url=URL("https://www.ozon.ru"))
    async with aiohttp.ClientSession(headers=HEADERS, cookie_jar=jar) as session:
        try:
            async with session.get(API_URL, allow_redirects=True, max_redirects=5) as response:
                body = await response.text()
                print(f"status={response.status}")
                print(f"body={_snippet(body)}")
        except Exception as err:
            print(f"exception={type(err).__name__}: {err}")
            print("-> redirect loop / challenge page (typical non-curl_cffi client)")


async def mode_client(cookies: dict[str, str]) -> None:
    print("=== client (warmup + curl_cffi — production path) ===")
    try:
        async with OzonOrdersClient(cookies) as client:
            payload = await client.fetch_order_list(active_only=True)
            user = payload.get("user") or {}
            print(
                f"OK logged_in={user.get('is_logged_in')} "
                f"orders={len(payload.get('orders') or [])}"
            )
    except OzonAntibotError as err:
        print(f"ANTIBOT: {err}")
    except OzonAuthError as err:
        print(f"AUTH: {err}")


async def mode_hammer(
    cookies: dict[str, str],
    *,
    seconds: int,
    interval: float,
) -> None:
    print(
        f"=== hammer (old HA: new session + list + all details, "
        f"every {interval}s for {seconds}s) ==="
    )
    deadline = time.monotonic() + seconds
    cycle = 0
    while time.monotonic() < deadline:
        cycle += 1
        t0 = time.monotonic()
        label = "OK"
        try:
            async with OzonOrdersClient(cookies) as client:
                payload = await client.fetch_order_list(active_only=True)
                seen: set[str] = set()
                for order in payload.get("orders") or []:
                    number = order.get("order_number")
                    if number and number not in seen:
                        seen.add(number)
                        await client.fetch_order_details(number)
        except OzonAntibotError as err:
            label = f"ANTIBOT: {err}"
        except OzonAuthError as err:
            label = f"AUTH: {err}"
        except Exception as err:
            label = f"{type(err).__name__}: {err}"

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"#{cycle:03d} {label} ({elapsed_ms}ms)", flush=True)
        if label.startswith("ANTIBOT"):
            break
        await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))


MODES = {
    "direct": mode_direct,
    "strip": mode_strip,
    "aiohttp": mode_aiohttp,
    "client": mode_client,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce Ozon antibot for local testing")
    parser.add_argument(
        "mode",
        choices=[*MODES.keys(), "hammer", "all"],
        help="direct/strip/aiohttp = antibot; client = baseline OK; hammer = stress",
    )
    parser.add_argument(
        "cookies",
        nargs="?",
        type=Path,
        default=Path("cookie.json"),
        help="Cookies JSON export (default: cookie.json)",
    )
    parser.add_argument("--seconds", type=int, default=120, help="hammer duration")
    parser.add_argument("--interval", type=float, default=2.0, help="hammer interval seconds")
    args = parser.parse_args()

    try:
        cookies = load_cookies(args.cookies)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        print(f"Cookies error: {err}", file=sys.stderr)
        return 2

    print(f"cookies={len(cookies)} from {args.cookies}")

    async def run() -> None:
        if args.mode == "all":
            for name in ("direct", "strip", "aiohttp", "client"):
                await MODES[name](cookies)
                print()
            return
        if args.mode == "hammer":
            await mode_hammer(cookies, seconds=args.seconds, interval=args.interval)
            return
        await MODES[args.mode](cookies)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
