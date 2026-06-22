#!/usr/bin/env python3
"""CLI: fetch Ozon buyer orders using cookies JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ozon_orders import OzonAntibotError, OzonAuthError, OzonOrdersClient, load_cookies


def main() -> int:
    parser = argparse.ArgumentParser(description="Ozon buyer orders via cookies JSON")
    parser.add_argument(
        "--cookies",
        required=True,
        type=Path,
        help="Path to cookies JSON (dict, array, or Playwright storage_state)",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only /my/orderlist?selectedTab=active",
    )
    parser.add_argument(
        "--order",
        metavar="NUMBER",
        help="Fetch order details, e.g. 50761504-0120",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON (default: on)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Compact JSON output",
    )
    args = parser.parse_args()

    try:
        cookies = load_cookies(args.cookies)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cookies error: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(_run(args, cookies))


async def _run(args: argparse.Namespace, cookies: dict[str, str]) -> int:
    try:
        async with OzonOrdersClient(cookies) as client:
            if args.order:
                payload = await client.fetch_order_details(args.order)
            else:
                payload = await client.fetch_order_list(active_only=args.active_only)
    except OzonAuthError as exc:
        print(f"Auth: {exc}", file=sys.stderr)
        return 3
    except OzonAntibotError as exc:
        print(f"Antibot: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    indent = None if args.compact else 2
    print(json.dumps(payload, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
