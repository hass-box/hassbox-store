"""Register_commands."""

from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .store import (
    check_state,
    upgrade_store,
    get_login_qr_code,
    get_dashboard,
    get_app_list,
    get_app_info,
    download_app,
    delete_app,
    get_installed_app,
    upgrade_core
)


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register_commands."""
    websocket_api.async_register_command(hass, check_state)
    websocket_api.async_register_command(hass, upgrade_store)
    websocket_api.async_register_command(hass, get_login_qr_code)
    websocket_api.async_register_command(hass, get_dashboard)
    websocket_api.async_register_command(hass, get_app_list)
    websocket_api.async_register_command(hass, get_app_info)
    websocket_api.async_register_command(hass, download_app)
    websocket_api.async_register_command(hass, delete_app)
    websocket_api.async_register_command(hass, get_installed_app)
    websocket_api.async_register_command(hass, upgrade_core)
