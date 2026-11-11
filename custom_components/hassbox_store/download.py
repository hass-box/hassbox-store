"""HTTP endpoint for downloading files from the configuration directory."""

from __future__ import annotations

import os
from urllib.parse import quote

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import URL_BASE

DOWNLOAD_URL = f"{URL_BASE}/download"


class HassBoxDownloadView(HomeAssistantView):
    """Download a file inside the Home Assistant configuration directory."""

    url = DOWNLOAD_URL
    name = "api:hassbox_store:download"
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.StreamResponse:
        """Return the requested file as an attachment."""
        config_dir = os.path.realpath(self.hass.config.config_dir)
        rel = request.query.get("path", "").strip().strip("/")
        if not rel:
            return self.json_message("路径不能为空", status_code=400)

        target = os.path.realpath(os.path.join(config_dir, rel))
        if target != config_dir and not target.startswith(config_dir + os.sep):
            return self.json_message("路径超出配置目录范围", status_code=403)
        if not os.path.isfile(target):
            return self.json_message("文件不存在", status_code=404)

        filename = os.path.basename(target)
        return web.FileResponse(
            target,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
            },
        )
