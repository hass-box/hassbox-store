import json
import aiohttp

from .utils.logger import LOGGER
from .utils.store import async_save_to_store

def json_dumps(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

base_url = "https://hassbox.cn/api/public/"
app_id = "gh_07ec63f43481"

class HassBoxDataClient:
    hass = None
    session = None
    token = None

    def __init__(self, hass, config=None):
        self.hass = hass
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        if config is not None:
            LOGGER.error(config["token"])
            self.token = config["token"]

    async def __fetch(self, api, data, header=None):
        data["appId"] = app_id
        async with self.session.post(base_url + api, json=data) as response:
            result = await response.json()
            LOGGER.error(json_dumps(result))
            return result

    async def get_qrcode(self):
        poat_data = { "token": self.token }
        result = await self.__fetch("store/getQRCode", poat_data)
        LOGGER.error(json_dumps(result))
        if "token" in result:
            self.token = result["token"]
        return result

    async def check_state(self):
        post_data = {"token": self.token}
        result = await self.__fetch("store/checkState", post_data)
        LOGGER.error(json_dumps(result))
        if "token" in result:
            self.token = result["token"]
            await async_save_to_store(
                self.hass, "hassbox_store.config", {"token": result["token"]}
            )
            return {"errcode": 0}
        else:
            return {"errcode": 1, "errmsg": result["errmsg"]}
    
    async def get_data(self):
        post_data = {"token": self.token}
        return await self.__fetch("store/data", post_data)
