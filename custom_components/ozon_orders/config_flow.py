"""Config flow for Ozon Orders."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .api.client import OzonOrdersClient
from .api.cookies import load_cookies
from .api.errors import OzonAntibotError, OzonAuthError, OzonOrdersError
from .const import (
    CONF_COOKIES_FILE,
    CONF_COOKIES_JSON,
    DEFAULT_COOKIES_FILE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_COOKIES_FILE, default=DEFAULT_COOKIES_FILE): str,
        vol.Optional(CONF_COOKIES_JSON): str,
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("scan_interval", default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
        ),
    }
)


class CannotConnect(HomeAssistantError):
    """Failed to connect to Ozon."""


class InvalidAuth(HomeAssistantError):
    """Invalid or expired cookies."""


class AntibotBlocked(HomeAssistantError):
    """Ozon antibot blocked the request."""


async def _validate_connection(hass: HomeAssistant, cookies_path: str) -> dict[str, Any]:
    cookies = await hass.async_add_executor_job(load_cookies, cookies_path)
    async with OzonOrdersClient(cookies) as client:
        return await client.fetch_order_list(active_only=True)


class OzonOrdersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ozon Orders."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cookies_file = user_input[CONF_COOKIES_FILE].strip() or DEFAULT_COOKIES_FILE
            cookies_path = self.hass.config.path(cookies_file)
            pasted = (user_input.get(CONF_COOKIES_JSON) or "").strip()

            try:
                if pasted:
                    raw = json.loads(pasted)
                    await self.hass.async_add_executor_job(load_cookies, raw)
                    await self.hass.async_add_executor_job(_write_cookies_file, cookies_path, pasted)
                elif not Path(cookies_path).is_file():
                    errors["base"] = "file_not_found"
                    raise ValueError("cookies file missing")

                result = await _validate_connection(self.hass, cookies_path)
                await self.async_set_unique_id(str(result["user"].get("user_id")))
                self._abort_if_unique_id_configured()

                first_name = result["user"].get("first_name") or "Ozon"
                return self.async_create_entry(
                    title=f"Ozon — {first_name}",
                    data={CONF_COOKIES_FILE: cookies_file},
                    options={"scan_interval": DEFAULT_SCAN_INTERVAL},
                )
            except json.JSONDecodeError:
                errors["base"] = "invalid_json"
            except OzonAuthError:
                errors["base"] = "invalid_auth"
            except OzonAntibotError:
                errors["base"] = "antibot"
            except (OzonOrdersError, OSError, ValueError):
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
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


def _write_cookies_file(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")
