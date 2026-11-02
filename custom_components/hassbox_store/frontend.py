"""Starting setup task: Frontend."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from homeassistant.components.frontend import async_remove_panel
from homeassistant.core import HomeAssistant

from .const import URL_BASE

if TYPE_CHECKING:
    from .base import HassBoxStore

STATIC_URL_PATH = f"{URL_BASE}/frontend"
PANEL_URL_PATH = "hassbox-store"
WEBCOMPONENT_NAME = "hassbox-store-frontend"


async def async_register_frontend(
    hass: HomeAssistant, hassboxStore: HassBoxStore, frontendVersion: str
) -> None:
    """Register the frontend."""

    dev_mode = False
    if frontend_path := os.getenv("HB_STORE_FRONTEND_DIR"):
        hassboxStore.logger.warning(
            "<HassBox Store Frontend> Frontend development mode enabled. Do not run in production!"
        )
        # 开发模式：环境变量指向 hassbox-store-frontend 项目根目录
        static_path = os.path.join(frontend_path, "hassbox_store_frontend")
        dev_mode = True
    else:
        # 生产模式：前端产物随集成一起打包
        from .hassbox_store_frontend import locate_dir

        static_path = locate_dir()

    await _async_register_static_path(hass, static_path)
    await async_register_panel(hass, hassboxStore, frontendVersion, dev_mode)


async def _async_register_static_path(hass: HomeAssistant, static_path: str) -> None:
    """注册前端静态资源目录，兼容新旧版本 HA。"""
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL_PATH, static_path, False)]
        )
    except ImportError:
        hass.http.register_static_path(
            STATIC_URL_PATH, static_path, cache_headers=False
        )


async def async_register_panel(
    hass: HomeAssistant,
    hassboxStore: HassBoxStore,
    frontendVersion: str,
    dev_mode: bool | None = None,
) -> None:
    """注册侧边栏面板（幂等）。"""
    from homeassistant.components import panel_custom

    if dev_mode is None:
        dev_mode = bool(os.getenv("HB_STORE_FRONTEND_DIR"))

    if PANEL_URL_PATH in hass.data.get("frontend_panels", {}):
        async_remove_panel(hass, PANEL_URL_PATH)

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=WEBCOMPONENT_NAME,
        module_url=f"{STATIC_URL_PATH}/entrypoint.js?v={frontendVersion}",
        sidebar_title=hassboxStore.sidepanel_title,
        sidebar_icon=hassboxStore.sidepanel_icon,
        require_admin=True,
        config={"dev_mode": dev_mode},
    )
