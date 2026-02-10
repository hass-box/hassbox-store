"""HassBox Store Data client."""

from __future__ import annotations
from .utils.store import async_save_to_store

import json

from .const import DOMAIN


def json_dumps(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


base_url = "https://hassbox.cn/api/public/"
app_id = "gh_07ec63f43481"


class HassBoxDataClient:
    hass = None
    config = None
    session = None
    token = None
    version = None

    def __init__(self, hass, session, config=None):
        self.hass = hass
        self.config = config
        self.session = session
        if "token" in config:
            self.token = config["token"]

    async def __fetch(self, api, data):
        data["appId"] = app_id
        data["token"] = self.token
        async with self.session.post(base_url + api, json=data) as response:
            return await response.json()

    async def get_qrcode(self):
        poat_data = {"token": self.token}
        result = await self.__fetch("store/getQRCode", poat_data)
        if "token" in result:
            self.token = result["token"]
        return result

    async def check_state(self, frontend_version):
        post_data = {}
        hassboxStore = self.hass.data.get(DOMAIN)
        post_data["integration_version"] = hassboxStore.version
        post_data["frontend_version"] = frontend_version

        result = await self.__fetch("store/checkState", post_data)
        if "token" in result:
            self.token = result["token"]
            self.config["token"] = result["token"]
            await async_save_to_store(self.hass, f"{DOMAIN}.config", self.config)
        return result

    async def get_data(self, version):
        post_data = {"version": version}
        return await self.__fetch("store/getStoreData", post_data)

    async def get_app_list(self):
        result = await self.__fetch("store/getAppList", {})
        if "data" in result:
            return result["data"]
        else:
            return None

    async def get_app_info(self, appId):
        post_data = {"app_id": appId}
        result = await self.__fetch("store/getAppInfo", post_data)
        if "data" in result:
            return result["data"]
        else:
            return result

    async def get_app_assets(self, appId, version, machine=None):
        post_data = {"app_id": appId, "version": version, "machine": machine}
        return await self.__fetch("store/getAppAssets", post_data)

    async def get_hassio_version(self):
        result = await self.__fetch("store/getHassioVersion", {})
        if "data" in result:
            return result["data"]
        else:
            return None

    async def add_repo(self, repo, appType):
        post_data = {"repo": repo, "appType": appType}
        return await self.__fetch("store/addRepo", post_data)
