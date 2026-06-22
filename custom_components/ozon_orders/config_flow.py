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
    CONF_RELAY_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COOKIES): str,
        vol.Optional(CONF_RELAY_URL, default=""): str,
    }
)

STEP_RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COOKIES): str,
        vol.Optional(CONF_RELAY_URL, default=""): str,
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("scan_interval", default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
        ),
        vol.Optional(CONF_RELAY_URL, default=""): str,
    }
)


def _relay_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value or None


async def _validate_connection(
    hass: HomeAssistant,
    cookies_raw: str,
    *,
    relay_url: str | None = None,
) -> dict[str, Any]:
    cookies = await hass.async_add_executor_job(load_cookies, cookies_raw)
    async with OzonOrdersClient(cookies, relay_url=relay_url) as client:
        return await client.fetch_order_list(active_only=True)


class OzonOrdersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ozon Orders."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            pasted = (user_input.get(CONF_COOKIES) or "").strip()
            relay_url = _relay_url(user_input.get(CONF_RELAY_URL))
            try:
                parsed = await self.hass.async_add_executor_job(parse_cookies_input, pasted)
                cookies_raw = json.dumps(parsed, ensure_ascii=False)
                await self.hass.async_add_executor_job(load_cookies, cookies_raw)

                result = await _validate_connection(
                    self.hass,
                    cookies_raw,
                    relay_url=relay_url,
                )
                await self.async_set_unique_id(str(result["user"].get("user_id")))
                self._abort_if_unique_id_configured()

                first_name = result["user"].get("first_name") or "Ozon"
                options = {"scan_interval": DEFAULT_SCAN_INTERVAL}
                if relay_url:
                    options[CONF_RELAY_URL] = relay_url
                return self.async_create_entry(
                    title=f"Ozon — {first_name}",
                    data={CONF_COOKIES: cookies_raw},
                    options=options,
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
                "relay_hint": "Если HA получает 403 — запустите scripts/ozon_relay.py на ПК и укажите http://IP_ПК:18765",
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            pasted = (user_input.get(CONF_COOKIES) or "").strip()
            relay_url = _relay_url(user_input.get(CONF_RELAY_URL))
            try:
                parsed = await self.hass.async_add_executor_job(parse_cookies_input, pasted)
                cookies_raw = json.dumps(parsed, ensure_ascii=False)
                await self.hass.async_add_executor_job(load_cookies, cookies_raw)
                await _validate_connection(
                    self.hass,
                    cookies_raw,
                    relay_url=relay_url,
                )

                options = dict(reconfigure_entry.options)
                if relay_url:
                    options[CONF_RELAY_URL] = relay_url
                else:
                    options.pop(CONF_RELAY_URL, None)

                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data={CONF_COOKIES: cookies_raw},
                    options=options,
                )
            except ValueError as err:
                _LOGGER.error("Cookie parse error: %s", err)
                errors["base"] = "invalid_json" if "format" in str(err).lower() or "json" in str(err).lower() else "missing_auth_cookies"
            except OzonAuthError as err:
                _LOGGER.error("Ozon auth failed during reconfigure: %s", err)
                errors["base"] = "invalid_auth"
            except OzonAntibotError as err:
                _LOGGER.error("Ozon antibot during reconfigure: %s", err)
                errors["base"] = "antibot"
            except (OzonOrdersError, OSError) as err:
                _LOGGER.error("Ozon connection failed during reconfigure: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_RECONFIGURE_SCHEMA,
                {
                    CONF_COOKIES: "",
                    CONF_RELAY_URL: reconfigure_entry.options.get(CONF_RELAY_URL, ""),
                },
            ),
            errors=errors,
            description_placeholders={
                "hint": "Вставьте новый JSON cookies после прохождения проверки на ozon.ru",
                "relay_hint": "Relay URL (ПК в LAN): http://192.168.0.X:18765",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OzonOrdersOptionsFlow:
        return OzonOrdersOptionsFlow()


class OzonOrdersOptionsFlow(config_entries.OptionsFlow):
    """Options flow — polling interval and optional relay."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            data: dict[str, Any] = {
                "scan_interval": user_input["scan_interval"],
                CONF_RELAY_URL: _relay_url(user_input.get(CONF_RELAY_URL)) or "",
            }
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                STEP_OPTIONS_SCHEMA,
                {
                    "scan_interval": self.config_entry.options.get(
                        "scan_interval", DEFAULT_SCAN_INTERVAL
                    ),
                    CONF_RELAY_URL: self.config_entry.options.get(CONF_RELAY_URL, ""),
                },
            ),
        )
