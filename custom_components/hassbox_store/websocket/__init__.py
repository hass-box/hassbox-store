"""Register_commands."""

from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .store import (
    check_state,
    unbind,
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
from .upload import (
    local_upload_start,
    local_upload_finish,
    websocket_list_directories,
    websocket_list_files,
    websocket_delete_path,
    websocket_create_directory,
    websocket_rename_path,
)


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register_commands."""
    websocket_api.async_register_command(hass, check_state)
    websocket_api.async_register_command(hass, unbind)
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
    websocket_api.async_register_command(hass, local_upload_start)
    websocket_api.async_register_command(hass, local_upload_finish)
    websocket_api.async_register_command(hass, websocket_list_directories)
    websocket_api.async_register_command(hass, websocket_list_files)
    websocket_api.async_register_command(hass, websocket_delete_path)
    websocket_api.async_register_command(hass, websocket_create_directory)
    websocket_api.async_register_command(hass, websocket_rename_path)
