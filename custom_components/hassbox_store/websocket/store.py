"""Register info websocket commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
import voluptuous as vol

from ..const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..base import HassBoxStore


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/upgrade_store",
        vol.Required("appId"): str,
        vol.Required("version"): str,
        vol.Required("reload"): bool

    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def upgrade_store(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """upgrade Store."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    result = await hassboxStore.async_upgrade_store(msg["appId"], msg["version"], msg["reload"])
    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/get_login_qr_code"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_login_qr_code(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get Login QRCode."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    result = await hassboxStore.data_client.get_qrcode()
    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/check_state",
        vol.Required("version"): str
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def check_state(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Check State."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    result = await hassboxStore.data_client.check_state(msg["version"])
    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/refresh_data"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def refresh_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Refresh data."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    await hassboxStore.async_update_data(force=True)

    connection.send_message(websocket_api.result_message(msg["id"], None))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/dashboard"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_dashboard(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """get dashboard."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    dashboard = await hassboxStore.async_get_dashboard()

    connection.send_message(websocket_api.result_message(msg["id"], dashboard))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/app_list"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_app_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """get App List."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    appList = await hassboxStore.async_get_app_list()
    if appList is None:
        connection.send_message(websocket_api.result_message(msg["id"], []))
        return

    connection.send_message(websocket_api.result_message(msg["id"], appList))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/getAppInfo",
        vol.Required("appId"): str,
        vol.Required("refresh"): bool

    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_app_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """get App Info."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    appInfo = await hassboxStore.async_get_app_info(msg["appId"], msg["refresh"])

    connection.send_message(websocket_api.result_message(msg["id"], appInfo))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/downloadApp",
        vol.Required("appId"): str,
        vol.Required("domain"): str,
        vol.Required("version"): str
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def download_app(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """get App Info."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    result = await hassboxStore.async_download_app(msg["appId"], msg["version"], msg["domain"])

    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/deleteApp",
        vol.Required("appId"): str
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def delete_app(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """delete Local App File."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    result = await hassboxStore.async_delete_app(msg["appId"])

    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/getInstalledApp"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_installed_app(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """delete Local App File."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    result = await hassboxStore.async_get_installed_app()
    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/upgradeCore",
        vol.Required("appId"): str,
        vol.Required("machine"): str,
        vol.Required("version"): str
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def upgrade_core(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """upgrade Home Assistant."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    result = await hassboxStore.async_upgrade_core(msg["appId"], msg["version"], msg["machine"])

    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/startAssistant"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def start_assistant(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """start assistant."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    result = await hassboxStore.async_start_assistant()

    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/assistantState"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def assistant_state(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """get assistant state."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    result = await hassboxStore.async_get_assistant_state()

    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/upgradeCoreProgress"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def upgrade_core_progress(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """get core upgrade progress."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    result = hassboxStore.get_core_upgrade_progress()

    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/upgradeCoreContainer",
        vol.Required("version"): str
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def upgrade_core_container(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """upgrade core container."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    await hassboxStore.async_upgrade_core_container(msg["version"])

    connection.send_message(websocket_api.result_message(msg["id"], None))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/get_hassio_version"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_hassio_version(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get Hassio Version."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    result = await hassboxStore.data_client.get_hassio_version()
    connection.send_message(websocket_api.result_message(msg["id"], result))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/get_hassio_stable_version"
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def get_hassio_stable_version(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get Hassio Stable Version."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)

    versionInfo = {}
    async with hassboxStore.session.get("https://version.home-assistant.io/stable.json") as response:
        versionInfo = await response.json()
    connection.send_message(websocket_api.result_message(msg["id"], versionInfo))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/add_repo",
        vol.Required("repo"): str,
        vol.Required("appType"): str
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def add_repo(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """add Repo."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    result = await hassboxStore.data_client.add_repo(msg["repo"], msg["appType"])
    connection.send_message(websocket_api.result_message(msg["id"], result))
