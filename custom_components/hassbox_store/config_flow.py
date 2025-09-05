"""Adds config flow for HassBox Store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
import voluptuous as vol

from .const import DOMAIN


if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HassBoxStoreFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for HassBox Store."""

    VERSION = 1

    hass: HomeAssistant

    def __init__(self) -> None:
        """Initialize."""
        self._errors = {}
        self._user_input = {}

    async def async_step_user(self, user_input):
        """Handle a flow initialized by the user."""

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if self.hass.data.get(DOMAIN):
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(
            title="HassBox 应用商店",
            data={
                "panel_name": "HassBox 应用商店"
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HassBoxStoreOptionsFlowHandler()


class HassBoxStoreOptionsFlowHandler(OptionsFlow):
    """HassBox Store config flow options handler."""

    @property
    def config_entry(self):
        return self.hass.config_entries.async_get_entry(self.handler)

    async def async_step_init(self, user_input=None):
        """Manage the options."""

        errors = {}
        if user_input is not None:
            panel_name = user_input['panel_name']
            if len(panel_name) == 0:
                panel_name = "HassBox 应用商店"

            self.hass.config_entries.async_update_entry(
                self.config_entry, data={"panel_name": panel_name}
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(data={})

        panel_name = self.config_entry.data["panel_name"]

        data_schema = {
            vol.Required("panel_name", default=panel_name): str,
        }

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(data_schema), errors=errors
        )
