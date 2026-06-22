"""Config flow for Ozon Orders."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .api.client import OzonOrdersClient
from .api.cookies import load_cookies, parse_cookies_input
from .api.errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .const import (
    CONF_COOKIES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_COOKIES): str})

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("scan_interval", default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
        ),
    }
)


async def _validate_connection(hass: HomeAssistant, cookies_raw: str) -> dict[str, Any]:
    cookies = await hass.async_add_executor_job(load_cookies, cookies_raw)
    async with OzonOrdersClient(cookies) as client:
        return await client.fetch_order_list(active_only=True)


class OzonOrdersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ozon Orders."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            pasted = (user_input.get(CONF_COOKIES) or "").strip()
            try:
                parsed = await self.hass.async_add_executor_job(parse_cookies_input, pasted)
                cookies_raw = json.dumps(parsed, ensure_ascii=False)
                await self.hass.async_add_executor_job(load_cookies, cookies_raw)

                result = await _validate_connection(self.hass, cookies_raw)
                await self.async_set_unique_id(str(result["user"].get("user_id")))
                self._abort_if_unique_id_configured()

                first_name = result["user"].get("first_name") or "Ozon"
                return self.async_create_entry(
                    title=f"Ozon — {first_name}",
                    data={CONF_COOKIES: cookies_raw},
                    options={"scan_interval": DEFAULT_SCAN_INTERVAL},
                )
            except ValueError as err:
                _LOGGER.error("Cookie parse error: %s", err)
                errors["base"] = "invalid_json" if "format" in str(err).lower() or "json" in str(err).lower() else "missing_auth_cookies"
            except OzonAuthError as err:
                _LOGGER.error("Ozon auth failed during setup: %s", err)
                errors["base"] = "invalid_auth"
            except OzonAntibotError as err:
                _LOGGER.error("Ozon antibot during setup: %s", err)
                errors["base"] = "antibot"
            except (OzonOrdersError, OSError) as err:
                _LOGGER.error("Ozon connection failed during setup: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "hint": "Cookie-Editor / EditThisCookie → Export → JSON, весь массив целиком",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OzonOrdersOptionsFlow:
        return OzonOrdersOptionsFlow()


class OzonOrdersOptionsFlow(config_entries.OptionsFlow):
    """Options flow — polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                STEP_OPTIONS_SCHEMA,
                {"scan_interval": self.config_entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)},
            ),
        )
