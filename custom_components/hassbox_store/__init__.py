"""
HassBox Store gives you a simple way to handle downloads of all your custom needs.

For more details about this integration, please refer to the documentation at
https://hassbox.cn/
"""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .base import HassBoxStore
from .utils.store import async_load_from_store

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    hass.data[DOMAIN] = hassbox = HassBoxStore()
    hassbox.hass = hass
    hassbox.session = async_get_clientsession(hass)
    hassbox.config = await async_load_from_store(hass, "hassbox_store.config") or {} 
    await hassbox.async_check_valid()
    return True