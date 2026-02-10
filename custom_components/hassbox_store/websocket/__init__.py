"""Register_commands."""

from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .store import (
    check_state,
    upgrade_store,
    get_login_qr_code,
    refresh_data,
    get_dashboard,
    get_app_list,
    get_app_info,
    download_app,
    delete_app,
    get_installed_app,
    start_assistant,
    assistant_state,
    upgrade_core,
    upgrade_core_progress,
    upgrade_core_container,
    get_hassio_version,
    get_hassio_stable_version,
    add_repo
)


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register_commands."""
    websocket_api.async_register_command(hass, check_state)
    websocket_api.async_register_command(hass, upgrade_store)
    websocket_api.async_register_command(hass, get_login_qr_code)
    websocket_api.async_register_command(hass, refresh_data)
    websocket_api.async_register_command(hass, get_dashboard)
    websocket_api.async_register_command(hass, get_app_list)
    websocket_api.async_register_command(hass, get_app_info)
    websocket_api.async_register_command(hass, download_app)
    websocket_api.async_register_command(hass, delete_app)
    websocket_api.async_register_command(hass, get_installed_app)
    websocket_api.async_register_command(hass, start_assistant)
    websocket_api.async_register_command(hass, assistant_state)
    websocket_api.async_register_command(hass, upgrade_core)
    websocket_api.async_register_command(hass, upgrade_core_progress)
    websocket_api.async_register_command(hass, upgrade_core_container)
    websocket_api.async_register_command(hass, get_hassio_version)
    websocket_api.async_register_command(hass, get_hassio_stable_version)
    websocket_api.async_register_command(hass, add_repo)
