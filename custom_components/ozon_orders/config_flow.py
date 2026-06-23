"""Config flow for Ozon Orders."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api.client import OzonOrdersClient
from .api.cookies import load_cookies, parse_cookies_input
from .api.errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .const import (
    ANTIBOT_COOKIE_NAME,
    CONF_COOKIES,
    CONF_DETAILS_BATCH_SIZE,
    CONF_DETAILS_TTL_HOURS,
    CONF_FETCH_DETAILS,
    CONF_MANUAL_REFRESH_MINUTES,
    CONF_REQUEST_DELAY_SEC,
    CONF_SCAN_INTERVAL,
    DEFAULT_DETAILS_BATCH_SIZE,
    DEFAULT_DETAILS_TTL_HOURS,
    DEFAULT_FETCH_DETAILS,
    DEFAULT_MANUAL_REFRESH_MINUTES,
    DEFAULT_REQUEST_DELAY_SEC,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_DETAILS_BATCH_SIZE,
    MAX_DETAILS_TTL_HOURS,
    MAX_MANUAL_REFRESH_MINUTES,
    MAX_REQUEST_DELAY_SEC,
    MAX_SCAN_INTERVAL,
    MIN_DETAILS_BATCH_SIZE,
    MIN_DETAILS_TTL_HOURS,
    MIN_MANUAL_REFRESH_MINUTES,
    MIN_REQUEST_DELAY_SEC,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_COOKIES): str})


def _sync_settings_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
            ),
            vol.Required(
                CONF_FETCH_DETAILS,
                default=defaults.get(CONF_FETCH_DETAILS, DEFAULT_FETCH_DETAILS),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DETAILS_TTL_HOURS,
                default=defaults.get(CONF_DETAILS_TTL_HOURS, DEFAULT_DETAILS_TTL_HOURS),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_DETAILS_TTL_HOURS, max=MAX_DETAILS_TTL_HOURS),
            ),
            vol.Required(
                CONF_DETAILS_BATCH_SIZE,
                default=defaults.get(CONF_DETAILS_BATCH_SIZE, DEFAULT_DETAILS_BATCH_SIZE),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_DETAILS_BATCH_SIZE, max=MAX_DETAILS_BATCH_SIZE),
            ),
            vol.Required(
                CONF_REQUEST_DELAY_SEC,
                default=defaults.get(CONF_REQUEST_DELAY_SEC, DEFAULT_REQUEST_DELAY_SEC),
            ): vol.All(
                vol.Coerce(float),
                vol.Range(min=MIN_REQUEST_DELAY_SEC, max=MAX_REQUEST_DELAY_SEC),
            ),
            vol.Required(
                CONF_MANUAL_REFRESH_MINUTES,
                default=defaults.get(
                    CONF_MANUAL_REFRESH_MINUTES, DEFAULT_MANUAL_REFRESH_MINUTES
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_MANUAL_REFRESH_MINUTES, max=MAX_MANUAL_REFRESH_MINUTES),
            ),
        }
    )


def _normalize_sync_options(user_input: dict[str, Any]) -> dict[str, Any]:
    options = dict(user_input)
    if not options.get(CONF_FETCH_DETAILS):
        options[CONF_DETAILS_BATCH_SIZE] = 0
    return options


def _options_defaults(config_entry: config_entries.ConfigEntry) -> dict[str, Any]:
    return {
        CONF_SCAN_INTERVAL: config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        ),
        CONF_FETCH_DETAILS: config_entry.options.get(
            CONF_FETCH_DETAILS, DEFAULT_FETCH_DETAILS
        ),
        CONF_DETAILS_TTL_HOURS: config_entry.options.get(
            CONF_DETAILS_TTL_HOURS, DEFAULT_DETAILS_TTL_HOURS
        ),
        CONF_DETAILS_BATCH_SIZE: config_entry.options.get(
            CONF_DETAILS_BATCH_SIZE, DEFAULT_DETAILS_BATCH_SIZE
        ),
        CONF_REQUEST_DELAY_SEC: config_entry.options.get(
            CONF_REQUEST_DELAY_SEC, DEFAULT_REQUEST_DELAY_SEC
        ),
        CONF_MANUAL_REFRESH_MINUTES: config_entry.options.get(
            CONF_MANUAL_REFRESH_MINUTES, DEFAULT_MANUAL_REFRESH_MINUTES
        ),
    }


async def _validate_connection(hass: HomeAssistant, cookies_raw: str) -> dict[str, Any]:
    cookies = await hass.async_add_executor_job(load_cookies, cookies_raw)
    async with OzonOrdersClient(cookies) as client:
        return await client.fetch_order_list(active_only=True)


class OzonOrdersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ozon Orders."""

    VERSION = 2

    def __init__(self) -> None:
        self._cookies_raw: str | None = None
        self._user_info: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            pasted = (user_input.get(CONF_COOKIES) or "").strip()
            try:
                parsed = await self.hass.async_add_executor_job(parse_cookies_input, pasted)
                cookies_raw = json.dumps(parsed, ensure_ascii=False)
                cookies = await self.hass.async_add_executor_job(load_cookies, cookies_raw)
                if ANTIBOT_COOKIE_NAME not in cookies:
                    errors["base"] = "missing_antibot_cookies"
                    raise ValueError("missing abt_data")

                result = await _validate_connection(self.hass, cookies_raw)
                await self.async_set_unique_id(str(result["user"].get("user_id")))
                self._abort_if_unique_id_configured()

                self._cookies_raw = cookies_raw
                self._user_info = result.get("user") or {}
                return await self.async_step_sync()
            except ValueError as err:
                if "base" not in errors:
                    _LOGGER.error("Cookie parse error: %s", err)
                    errors["base"] = (
                        "invalid_json"
                        if "format" in str(err).lower() or "json" in str(err).lower()
                        else "missing_auth_cookies"
                    )
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
        )

    async def async_step_sync(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            options = _normalize_sync_options(user_input)
            first_name = (self._user_info or {}).get("first_name") or "Ozon"
            return self.async_create_entry(
                title=f"Ozon — {first_name}",
                data={CONF_COOKIES: self._cookies_raw},
                options=options,
            )

        return self.async_show_form(
            step_id="sync",
            data_schema=_sync_settings_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OzonOrdersOptionsFlow:
        return OzonOrdersOptionsFlow()


class OzonOrdersOptionsFlow(config_entries.OptionsFlow):
    """Options flow — polling and antibot settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=_normalize_sync_options(user_input),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_sync_settings_schema(_options_defaults(self.config_entry)),
        )
