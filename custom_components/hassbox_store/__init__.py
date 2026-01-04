"""
HassBox Store gives you a simple way to handle downloads of all your custom needs.

For more details about this integration, please refer to the documentation at
https://hassbox.cn/
"""

from __future__ import annotations
from typing import Any
import os

import voluptuous as vol
from homeassistant.components.frontend import async_remove_panel
from homeassistant.components.lovelace.system_health import system_health_info
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .base import HassBoxStore
from .const import DOMAIN, STARTUP
from .hassbox_store_frontend import VERSION as FE_VERSION
from .data_client import HassBoxDataClient
from .frontend import async_register_frontend
from .websocket import async_register_websocket_commands
from .utils.store import async_load_from_store, async_save_to_store

CONFIG_SCHEMA = vol.Schema({DOMAIN: {
    vol.Optional("panel_name", default="HassBox 应用商店"): str,
}}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up this integration using yaml."""
    return await async_initialize_integration(hass=hass, config=config)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    config_entry.async_on_unload(config_entry.add_update_listener(async_reload_entry))
    setup_result = await async_initialize_integration(hass=hass, config_entry=config_entry)
    return setup_result


async def async_initialize_integration(
    hass: HomeAssistant,
    *,
    config_entry: ConfigEntry | None = None,
    config: dict[str, Any] | None = None,
) -> bool:
    """Initialize the integration"""
    hass.data[DOMAIN] = hassboxStore = HassBoxStore()

    if config is not None:
        if DOMAIN not in config:
            return True
        if 'panel_name' in config[DOMAIN]:
            hassboxStore.sidepanel_title = config[DOMAIN]['panel_name']

    if config_entry is not None:
        if 'panel_name' in config_entry.data:
            hassboxStore.sidepanel_title = config_entry.data['panel_name']

        if config_entry.source == SOURCE_IMPORT:
            hass.async_create_task(hass.config_entries.async_remove(config_entry.entry_id))
            return False

    hassboxStore.hass = hass
    hassboxStore.session = async_get_clientsession(hass, False)
    hassboxStore.config = await async_load_from_store(hass, f"{DOMAIN}.config") or {}
    hassboxStore.data_client = HassBoxDataClient(hass, hassboxStore.session, hassboxStore.config)

    integration = await async_get_integration(hass, DOMAIN)

    hassboxStore.integration = integration
    hassboxStore.version = integration.version

    hassboxStore.logger.info(STARTUP, integration.version)

    async def async_startup():
        """HassBox Store startup tasks."""
        async_register_websocket_commands(hass)
        await async_register_frontend(hass, hassboxStore, FE_VERSION)
        await hassboxStore.async_update_data()
        await hassboxStore.async_clear()
        return True

    await async_startup()

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    try:
        if hass.data.get("frontend_panels", {}).get("hassbox-store"):
            async_remove_panel(hass, "hassbox-store")
    except AttributeError:
        pass

    hass.data.pop(DOMAIN, None)

    return True


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload the config entry."""
    if not await async_unload_entry(hass, config_entry):
        return
    await async_setup_entry(hass, config_entry)
