"""Starting setup task: Frontend."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.components.frontend import async_register_built_in_panel

from .const import DOMAIN, URL_BASE
from .hassbox_store_frontend import locate_dir


if TYPE_CHECKING:
    from .base import HassBoxStore


async def async_register_frontend(hass: HomeAssistant, hassboxStore: HassBoxStore, frontendVersion: str) -> None:
    """Register the frontend."""

    # Register frontend
    if (frontend_path := os.getenv("FRONTEND_DIR")):
        hassboxStore.logger.warning(
            "<HassBox Store Frontend> Frontend development mode enabled. Do not run in production!"
        )
        try:
            from homeassistant.components.http import StaticPathConfig
            await hass.http.async_register_static_paths([StaticPathConfig(f"{URL_BASE}/frontend", f"{frontend_path}/hassbox_store_frontend", False)])
        except ImportError:
            hass.http.register_static_path(
                f"{URL_BASE}/frontend", f"{frontend_path}/hassbox_store_frontend", cache_headers=False
            )
    else:
        try:
            from homeassistant.components.http import StaticPathConfig
            await hass.http.async_register_static_paths([StaticPathConfig(f"{URL_BASE}/frontend", locate_dir(), False)])
        except ImportError:
            hass.http.register_static_path(
                f"{URL_BASE}/frontend", locate_dir(), cache_headers=False)

    await async_register_panel(hass, hassboxStore, frontendVersion)


async def async_register_panel(hass: HomeAssistant, hassboxStore: HassBoxStore, frontendVersion: str) -> None:
    # Add to sidepanel if needed
    if DOMAIN not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title=hassboxStore.sidepanel_title,
            sidebar_icon=hassboxStore.sidepanel_icon,
            frontend_url_path="hassbox-store",
            config={
                "_panel_custom": {
                    "name": "hassbox-store-frontend",
                    "embed_iframe": True,
                    "trust_external": False,
                    "js_url": f"{URL_BASE}/frontend/entrypoint.js?version={frontendVersion}",
                }
            },
            require_admin=True,
            update=True
        )
