"""Base HassBoxStore class."""

from __future__ import annotations

import gzip
import os
import pathlib
import time
import asyncio
import tempfile
import zipfile
import tarfile
import shutil
import hashlib
import json

import copy

from typing import Any, cast

from aiohttp.client import ClientSession, ClientTimeout
from awesomeversion import AwesomeVersion

from homeassistant.components.frontend import async_remove_panel
from homeassistant.loader import Manifest, async_get_integration

from homeassistant.core import HomeAssistant
from homeassistant.loader import Integration

from homeassistant.components.lovelace.resources import ResourceStorageCollection

from awesomeversion import AwesomeVersion
from homeassistant.const import __version__ as HA_VERSION

from .const import DOMAIN
from .data_client import HassBoxDataClient

from .frontend import async_register_panel
from .utils.json import json_loads
from .utils.logger import LOGGER
from .utils.store import async_load_from_store, async_save_to_store

import docker


def json_dumps(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


class HassBoxStore:
    hass: HomeAssistant | None = None
    session: ClientSession | None = None
    config: dict[str, Any] | None = None
    data_client: HassBoxDataClient | None = None
    disabled_reason: str | None = None
    first_time: bool = True
    appList: list[Any] | None = None
    dashboard: list[Any] | None = None
    lastTimeUpdate: float = 0
    version: AwesomeVersion | None = None
    integration: Integration | None = None
    upgrade: bool = False

    sidepanel_icon: str = "mdi:storefront"
    sidepanel_title: str = "HassBox 应用商店"

    layer_progress = {}
    pull_status = 0
    pull_error = ""

    def __init__(self) -> None:
        """Initialize."""
        self.logger = LOGGER
        self.appMap = {}

    async def async_update_data(self, force=False):
        self.lastTimeUpdate = int(time.time())

        cachedData = await async_load_from_store(self.hass, f"{DOMAIN}.data") or {}

        appMap = {}
        if "appList" in cachedData:
            for app in cachedData["appList"]:
                appMap[app['appId']] = app

        if "dashboard" in cachedData:
            self.dashboard = cachedData["dashboard"]

        try:
            version = -1
            if force is False and "lastTimeUpdate" in cachedData:
                version = cachedData["lastTimeUpdate"]

            result = await self.data_client.get_data(version)

            if "appList" in result:
                for app in result["appList"]:
                    appMap[app['appId']] = app

            if "dashboard" in result:
                self.dashboard = result["dashboard"]

        except (
            BaseException
        ) as error:
            self.logger.error("数据刷新失败：%s", error)

        self.appList = list(appMap.values())
        self.appMap = {}

        await async_save_to_store(self.hass, f"{DOMAIN}.data", {"appList": self.appList, "dashboard": [self.dashboard], "lastTimeUpdate": self.lastTimeUpdate})

    async def async_get_dashboard(self):
        if int(time.time()) > (self.lastTimeUpdate + 3600 * 24):
            await self.async_update_data()
        return self.dashboard

    async def async_get_app_list(self):
        return self.appList

    async def async_upgrade_store(self, appId, version, reload):
        if self.upgrade:
            return {"errcode": 0}

        result = await self.data_client.get_app_assets(appId, version)
        if "errcode" in result and result['errcode'] != 0:
            return result

        appInfo = await self.async_get_app_info(appId)
        if appInfo is None:
            return {"errcode": 302, "errmsg": "not_found"}

        result = await self.async_install_integration(appInfo, version, result, False)
        if result is True:
            self.upgrade = True
            if reload:
                async_remove_panel(self.hass, "hassbox-store")
                hassboxStore = self.hass.data.get(DOMAIN)
                await async_register_panel(self.hass, hassboxStore, version)

            return {"errcode": 0}
        else:
            return {"errcode": 1, "errmsg": result}

    async def async_get_app_info(self, appId, refresh=False):
        appInfo = None
        if appId in self.appMap:
            appInfo = self.appMap[appId]
            if refresh or int(time.time()) > (appInfo['lastTimeUpdate'] + 3600):
                appInfo = None

        if appInfo is None:
            appInfo = await self.data_client.get_app_info(appId)
            if appInfo is None:
                return None
            appInfo['lastTimeUpdate'] = int(time.time())
            self.appMap[appId] = appInfo

        if "buildIn" in appInfo and appInfo["buildIn"] == 1:
            try:
                integration = await async_get_integration(self.hass, appInfo["domain"])
                appInfo["installed"] = True
            except (BaseException) as error:
                self.logger.error("Integration %s is not loaded. %s", appInfo["domain"], error)
                appInfo["installed"] = False
        else:
            appInstalled = await async_load_from_store(self.hass, f"{DOMAIN}.installed") or {}
            if appId in appInstalled:
                if appInfo["appType"] == "addon":
                    hassio = self.hass.data["hassio"]
                    try:
                        slug = "local_" + appInfo["domain"]
                        result = await hassio.send_command(f"/store/addons/{slug}", method="get")
                        appInfo["installed"] = result['data']['installed']
                        appInfo["installed_version"] = result['data']["version"]
                    except (BaseException) as error:
                        self.logger.error("Addon %s is not install. %s", appInfo["domain"], error)
                        appInfo["installed"] = False

                elif appInfo["appType"] == "integration":
                    manifest_path = pathlib.Path(appInstalled[appId]["component_directory"]) / "manifest.json"
                    if manifest_path.exists():
                        appInfo["installed"] = True
                        appInfo["installed_version"] = appInstalled[appId]["installed_version"]
                    else:
                        appInfo["installed"] = False

                else:
                    appInfo["installed"] = True
                    appInfo["installed_version"] = appInstalled[appId]["installed_version"]
                    if appInfo["appType"] == "card":
                        appInfo["card_name"] = appInstalled[appId]["card_name"]

            else:
                appInfo["installed"] = False

        if appInfo["installed"] == True:
            if appInfo["appType"] == "integration":
                try:
                    integration = await async_get_integration(self.hass, appInfo["domain"])
                    appInfo["config_flow"] = integration.config_flow
                    appInfo["loaded"] = True
                except (BaseException) as error:
                    self.logger.error("Integration %s is not loaded. %s", appInfo["domain"], error)
                    appInfo["loaded"] = False

                entries = self.hass.config_entries.async_entries(appInfo["domain"])
                if len(entries) > 0:
                    appInfo["integrated"] = True
                else:
                    appInfo["integrated"] = False

            elif appInfo["appType"] == "card" or appInfo["appType"] == "theme":
                if "need_restart" in appInfo:
                    appInfo["loaded"] = False
                else:
                    appInfo["loaded"] = True
            else:
                appInfo["loaded"] = False

        return appInfo

    async def async_download_app(self, appId, version, domain):
        appInfo = self.appMap[appId]
        if appInfo is None:
            return {"errcode": 1, "errmsg": "not_found"}

        result = await self.data_client.get_app_assets(appId, version)
        if "errcode" in result and result['errcode'] != 0:
            return result

        appAssets = result

        if appInfo['appType'] == "integration" or appInfo['appType'] == "card" or appInfo['appType'] == "theme":
            result = await self.async_install_integration(appInfo, version, appAssets)
            if result is True:
                if appInfo["appType"] == "card" or appInfo["appType"] == "theme":
                    appInfo["need_restart"] = True
                return await self.async_get_app_info(appId)
            else:
                return {"errcode": 1, "errmsg": result}
        elif appInfo['appType'] == "addon":
            appInfo['slug'] = domain
            result = await self.async_install_addon(appInfo, version, appAssets)
            if result is True:
                return await self.async_get_app_info(appId)
            else:
                return {"errcode": 1, "errmsg": result}
        else:
            return {"errcode": 1, "errmsg": "unkown"}

    async def async_install_integration(self, appInfo: dict[str, Any], version, appAssets, saveToStore=True):
        appInfo = copy.deepcopy(appInfo)
        app_version = self.get_repo_version(appInfo, version)
        if app_version is None:
            return "without version"

        assetsName = appAssets['assetsName']
        assets_download_url = appAssets['assetsDownloadUrl']
        filecontent = await self.async_download_file(assets_download_url)
        if filecontent is None:
            return "not downloaded"

        temp_assets_dir = await self.hass.async_add_executor_job(tempfile.mkdtemp)
        temp_assets_file = f"{temp_assets_dir}/{assetsName}"
        result = await self.async_save_file(temp_assets_file, filecontent)
        if not result:
            return "unable save download file"

        assets_filename = assetsName.split('.')[0]
        temp_assets_extract_dir = f"{temp_assets_dir}/{assets_filename}"
        if assetsName.endswith('.zip'):
            def _extract_zip_file():
                with zipfile.ZipFile(temp_assets_file, "r") as zip_file:
                    zip_file.extractall(temp_assets_extract_dir)
            await self.hass.async_add_executor_job(_extract_zip_file)
        elif assetsName.endswith('.tar.gz'):
            def _extract_tar_file():
                tar = tarfile.open(temp_assets_file)
                tar.extractall(path=temp_assets_extract_dir)
            await self.hass.async_add_executor_job(_extract_tar_file)

        hassConfigPath = self.hass.config.path()
        installed = False

        if appInfo["appType"] == "integration":
            component_directory = f"{hassConfigPath}/custom_components"

            def _walk_extract_dir():
                component_name = None
                found_component = False
                for root, dirs, files in os.walk(temp_assets_extract_dir):
                    for f in files:
                        if f == "manifest.json":
                            component_name = os.path.basename(root)
                            local_dir = f"{component_directory}/{component_name}"
                            if os.path.exists(local_dir):
                                shutil.rmtree(local_dir)
                            shutil.move(root, local_dir)
                            found_component = True
                            break

                    if found_component:
                        break

                    for d in dirs:
                        if d == "custom_components":
                            files = os.listdir(os.path.join(root, d))
                            for file in files:
                                if os.path.isdir(os.path.join(root, d, file)) and os.path.exists(os.path.join(root, d, file, "manifest.json")):
                                    component_name = file
                                    local_dir = f"{component_directory}/{component_name}"
                                    if os.path.exists(local_dir):
                                        shutil.rmtree(local_dir)
                                    shutil.move(os.path.join(
                                        root, d, file), component_directory)
                                    found_component = True
                return component_name, found_component

            component_name, found_component = await self.hass.async_add_executor_job(_walk_extract_dir)

            if found_component:
                appInfo["component_directory"] = f"{component_directory}/{component_name}"
                appInfo["component_name"] = component_name
                manifest_path = pathlib.Path(appInfo["component_directory"]) / "manifest.json"
                manifest = cast(Manifest, await self.async_load_manifest(manifest_path))
                appInfo["domain"] = manifest["domain"]
                installed = True

        elif appInfo["appType"] == "theme":
            theme_directory = f"{hassConfigPath}/themes/{appInfo['repoId'].split('/')[1]}"

            def _walk_extract_dir():
                found_theme = False
                for root, dirs, files in os.walk(temp_assets_extract_dir):
                    for d in dirs:
                        if d == "themes":
                            files = os.listdir(os.path.join(root, d))
                            for file in files:
                                # if file.endswith(".yaml"):
                                #     await self.async_replace_file(os.path.join(root, d, file), "hacsfiles", "local")
                                if not os.path.exists(theme_directory):
                                    os.makedirs(theme_directory)
                                shutil.move(os.path.join(root, d, file),
                                            os.path.join(theme_directory, file))
                            found_theme = True
                return found_theme

            found_theme = await self.hass.async_add_executor_job(_walk_extract_dir)

            if found_theme:
                appInfo["theme_directory"] = theme_directory
                installed = True

        elif appInfo["appType"] == "card":
            card_directory = f"{hassConfigPath}/www/{appInfo['appId']}"

            def _walk_extract_dir():
                card_name = None
                found_card = False

                if assetsName.endswith('.js'):
                    card_name = assetsName
                    if not os.path.exists(card_directory):
                        os.makedirs(card_directory)
                    local_file = f"{card_directory}/{card_name}"
                    if os.path.exists(local_file):
                        os.remove(local_file)
                    shutil.move(temp_assets_file, local_file)

                    with open(local_file, "rb") as f_in:
                        with gzip.open(local_file + ".gz", "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)

                    found_card = True
                else:
                    if app_version.get("filename"):
                        valid_filenames = (app_version["filename"],)
                    else:
                        name = appInfo['repoId'].split("/")[1]
                        valid_filenames = (
                            f"{name.replace('lovelace-', '')}.js",
                            f"{name}.js",
                            f"{name}.umd.js",
                            f"{name}-bundle.js",
                        )

                    for root, dirs, files in os.walk(temp_assets_extract_dir):
                        for file in files:
                            if file.endswith('.js'):
                                for filename in valid_filenames:
                                    if file.lower() == filename.lower():
                                        card_name = file
                                        if not os.path.exists(card_directory):
                                            os.makedirs(card_directory)
                                        local_file = f"{card_directory}/{card_name}"
                                        if os.path.exists(local_file):
                                            os.remove(local_file)
                                        shutil.move(os.path.join(
                                            root, file), local_file)

                                        with open(local_file, "rb") as f_in:
                                            with gzip.open(local_file + ".gz", "wb") as f_out:
                                                shutil.copyfileobj(f_in, f_out)

                                        found_card = True
                                        break
                return card_name, found_card

            card_name, found_card = await self.hass.async_add_executor_job(_walk_extract_dir)

            if found_card:
                appInfo["card_directory"] = card_directory
                appInfo["card_name"] = card_name
                installed = True
                resource_url = "/local/" + appInfo['appId'] + "/" + card_name + "?tag=" + str(int(time.time()))
                await self.update_dashboard_resources(appInfo['appId'], resource_url)

        if installed and saveToStore:
            result = await async_load_from_store(self.hass, f"{DOMAIN}.installed") or {}
            appInfo['installed_version'] = app_version['versionName']
            del appInfo['version']
            result[appInfo['appId']] = appInfo

            await async_save_to_store(self.hass, f"{DOMAIN}.installed", result)

        def cleanup_temp_assets_dir():
            if os.path.exists(temp_assets_dir):
                shutil.rmtree(temp_assets_dir)

        await self.hass.async_add_executor_job(cleanup_temp_assets_dir)

        return installed

    async def async_install_addon(self, appInfo: dict[str, Any], version, appAssets):
        appInfo = copy.deepcopy(appInfo)
        data = {}
        data["app_id"] = appInfo["appId"]
        data["token"] = ""
        data["addon_url"] = appAssets['assetsDownloadUrl']
        result = await self.async_handle_addon("install", data, 3)
        if result is not None and result.startswith("{"):
            result = json.loads(result)
            if result["status"] == 2:
                result = await async_load_from_store(self.hass, f"{DOMAIN}.installed") or {}
                appInfo['installed_version'] = version
                del appInfo['version']
                result[appInfo['appId']] = appInfo
                await async_save_to_store(self.hass, f"{DOMAIN}.installed", result)
                return True
            else:
                return "download error"
        return result

    async def async_delete_addon(self, appInfo: dict[str, Any]):
        data = {}
        data["app_id"] = appInfo["appId"]
        result = await self.async_handle_addon("uninstall", data, 3)
        if result.startswith("{"):
            result = json.loads(result)
            if result["status"] == 2:
                return True
            else:
                return "delete_error"
        return result

    async def async_delete_app(self, appId: str):
        appInstalled = await async_load_from_store(self.hass, f"{DOMAIN}.installed") or {}
        if appId in appInstalled:
            appInfo = appInstalled[appId]
            result = await self.async_delete_local(appInfo)
            if result is True:
                return await self.async_get_app_info(appId)
            else:
                return result
        else:
            return None

    async def async_delete_local(self, appInfo: dict[str, Any]):
        local_dir = None
        if appInfo["appType"] == "integration":
            local_dir = appInfo["component_directory"]

        elif appInfo["appType"] == "theme":
            local_dir = appInfo["theme_directory"]

        elif appInfo["appType"] == "card":
            local_dir = appInfo["card_directory"]
            await self.remove_dashboard_resources(appInfo['appId'])

        if local_dir:
            def _rm_local_dir():
                if os.path.exists(local_dir):
                    shutil.rmtree(local_dir)
            await self.hass.async_add_executor_job(_rm_local_dir)

        if appInfo["appType"] == "addon":
            result = await self.async_delete_addon(appInfo)
            if result is not True:
                return result

        result = await async_load_from_store(self.hass, f"{DOMAIN}.installed") or {}
        result.pop(appInfo["appId"])
        await async_save_to_store(self.hass, f"{DOMAIN}.installed", result)

        return True

    async def async_get_installed_app(self):
        appInstalled = await async_load_from_store(self.hass, f"{DOMAIN}.installed") or {}

        appList = []
        for app in appInstalled.values():
            appList.append(app)
        return appList

    async def async_upgrade_core(self, appId, version, machine=None):
        if not (hass_data := self.hass.data):
            return {"errcode": 1, "errmsg": "Can not access the hass data"}

        if hass_data.get("hassio") is None:
            self.pull_status = 0
            self.layer_progress = {}

            def _pull_image():
                try:
                    client = docker.from_env()
                    pull_logs = client.api.pull(
                        repository="registry.cn-hangzhou.aliyuncs.com/home-assistant-hub/home-assistant",
                        tag=version,
                        stream=True,
                        decode=True
                    )
                    self.pull_status = 1
                    for log in pull_logs:
                        self._handle_pull_log(log)
                    self.pull_status = 2
                    client.api.inspect_image("registry.cn-hangzhou.aliyuncs.com/home-assistant-hub/home-assistant:" + version)
                    self.pull_status = 3
                except Exception as e:
                    self.logger.error("pull core image error:  %s", e)
                    self.pull_status = -1
                    self.pull_error = str(e)

            self.hass.async_add_executor_job(_pull_image)
            return {"errcode": 0}
        else:
            result = await self.data_client.get_app_assets(appId, version, machine)
            if "errcode" in result and result['errcode'] != 0:
                return result

            appAssets = result

            data = {}
            data["app_id"] = appId
            data["token"] = ""
            data["addon_url"] = appAssets["assetsDownloadUrl"]
            result = await self.async_handle_addon("install", data, 3)
            if result is not None and result.startswith("{"):
                result = json.loads(result)
                if result["status"] == 2:
                    return {"errcode": 0}
                else:
                    return {"errcode": 1, "errmsg": "download_error"}
            return {"errcode": 1, "errmsg": result}

    def get_core_upgrade_progress(self):
        return {"progress": list(self.layer_progress.values()), "status": self.pull_status, "error": self.pull_error}

    async def async_upgrade_core_container(self, version):
        try:
            data = {}
            data["version"] = version
            response = await self.session.post("http://localhost:9222/upgrade", json=data)
            self.logger.error("upgrade result: %s", await response.text())
        except Exception as e:
            self.logger.error("upgrade core error:  %s", e)

    async def async_start_assistant(self):
        self.pull_status = 0
        self.pull_error = ""

        def _start_assistant():
            try:
                client = docker.from_env()
                result = self.get_assistant_container(client)
                if result == 0:
                    self.install_assistant(client)
                elif result is Exception:
                    self.pull_status = -1
                    self.pull_error = str(result)
                else:
                    self.logger.error("start assistant...")
                    self.pull_status = 3
                    result.start()

            except Exception as e:
                self.logger.error("start assistant error:  %s", e)
                self.pull_status = -1
                self.pull_error = str(e)

        await self.hass.async_add_executor_job(_start_assistant)

    async def async_get_assistant_state(self):
        if self.pull_status == 3:
            try:
                request = await self.session.get("http://localhost:9222")
                if request.status == 200:
                    self.pull_status = 4
            except Exception as e:
                self.logger.error("get assistant error:  %s", e)

        return {"progress": list(self.layer_progress.values()), "status": self.pull_status, "error": self.pull_error}

    def get_assistant_container(self, client: docker.DockerClient):
        try:
            container = client.containers.get("hassbox-store-assistant")
            return container
        except docker.errors.NotFound:
            return 0
        except Exception as e:
            self.logger.error("get hassbox-store-assistant container error: %s", e)
            return e

    def install_assistant(self, client: docker.DockerClient):
        try:
            pull_logs = client.api.pull(
                repository="registry.cn-hangzhou.aliyuncs.com/hassbox/hassbox-store-assistant",
                tag="latest",
                stream=True,
                decode=True
            )
            self.pull_status = 1
            for log in pull_logs:
                self._handle_pull_log(log)
            self.pull_status = 2
            container_params = {
                "name": "hassbox-store-assistant",
                "detach": True,
                "privileged": True,
                "volumes": {
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock",
                        "mode": "rw"
                    }
                },
                "network_mode": "host",
                "image": "registry.cn-hangzhou.aliyuncs.com/hassbox/hassbox-store-assistant:latest"
            }

            client.containers.run(** container_params)
            self.pull_status = 3
        except Exception as e:
            self.logger.error("install assistant error:  %s", e)
            self.pull_status = -1
            self.pull_error = str(e)

    def _handle_pull_log(self, log):
        status = log.get("status", "")
        progress = log.get("progress", "")
        layer_id = log.get("id", "")

        if "Pulling from" in status:
            self.layer_progress["Pulling"] = f"{status}"
            return
        elif "Digest" in status:
            self.layer_progress["Digest"] = f"{status}"
            return
        elif "Status: " in status:
            self.layer_progress["Status"] = f"{status}"
            return

        if layer_id:
            self.layer_progress[layer_id] = f"{layer_id}: {status} {progress}"
            success_status = ["Download complete", "Pull complete", "Already exists"]
            if any(s in status for s in success_status):
                if layer_id in self.layer_progress:
                    del self.layer_progress[layer_id]
                    return

    async def async_download_file(self, url):
        if url is None:
            return None

        try:
            request = await self.session.get(
                url=url,
                timeout=ClientTimeout(total=30)
            )

            if request.status == 200:
                return await request.read()

            self.logger.error("Download failed - %d", request.status)
        except (
            BaseException
        ) as exception:
            self.logger.error("Download failed - %s", exception)
        return None

    async def async_handle_addon(self, action, data, retryCount=0):
        result = None
        try:
            request = await self.session.post("http://localhost:9222/" + action, json=data)
            if request.status == 200:
                result = await request.text()
            else:
                self.logger.error("Download failed - %d", request.status)
        except (
            BaseException
        ) as exception:
            self.logger.error("Download failed - %s", exception)
            if retryCount == 0:
                return str(exception)
            else:
                await asyncio.sleep(3)
                return await self.async_handle_addon(action, data, retryCount - 1)

        if result is not None:
            return result
        return "download_error"

    async def async_clear(self):
        if not (hass_data := self.hass.data):
            self.logger.error("Can not access the hass data")
            return
        if (hassio := hass_data.get("hassio")) is None:
            return

        try:
            addons = []
            plugins = ["supervisor", "core", "observer", "dns", "cli", "audio", "multicast"]
            versionMap = {}
            for plugin in plugins:
                result = await hassio.send_command(f"/{plugin}/info", method="get")
                versionMap[plugin] = result["data"]["version"]
                if "supervisor" == plugin:
                    addons = result["data"]["addons"]

            for addon in addons:
                if addon["slug"] == "local_home_assistant_supervisor":
                    if AwesomeVersion(addon["version"]) > versionMap["supervisor"]:
                        continue
                elif addon["slug"] == "local_home_assistant_core":
                    if AwesomeVersion(addon["version"]) > versionMap["core"]:
                        continue
                elif addon["slug"] == "local_hassio-observer":
                    if AwesomeVersion(addon["version"]) > versionMap["observer"]:
                        continue
                elif addon["slug"] == "local_hassio-dns":
                    if AwesomeVersion(addon["version"]) > versionMap["dns"]:
                        continue
                elif addon["slug"] == "local_hassio-cli":
                    if AwesomeVersion(addon["version"]) > versionMap["cli"]:
                        continue
                elif addon["slug"] == "local_hassio-audio":
                    if AwesomeVersion(addon["version"]) > versionMap["audio"]:
                        continue
                elif addon["slug"] == "local_hassio-multicast":
                    if AwesomeVersion(addon["version"]) > versionMap["multicast"]:
                        continue
                else:
                    continue

                await hassio.send_command(f"/addons/{addon["slug"]}/uninstall", payload={"remove_config": False})
        except (
            BaseException
        ) as error:
            self.logger.error("Could not clear data %s", error)

    async def async_save_file(self, file_path, content):

        def _write_file():
            with open(
                file_path,
                mode="w" if isinstance(content, str) else "wb",
                encoding="utf-8" if isinstance(content, str) else None,
                errors="ignore" if isinstance(content, str) else None,
            ) as file_handler:
                file_handler.write(content)

        try:
            await self.hass.async_add_executor_job(_write_file)
        except (
            BaseException
        ) as error:
            self.logger.error("Could not write data to %s - %s", file_path, error)
            return False

        return os.path.exists(file_path)

    async def async_replace_file(self, file_path, search_text, replace_text):

        def _replace_file():
            with open(file_path, 'r') as file:
                filedata = file.read()
            filedata = filedata.replace(search_text, replace_text)
            with open(file_path, 'w') as file:
                file.write(filedata)

        try:
            await self.hass.async_add_executor_job(_replace_file)
        except (
            BaseException
        ) as error:
            self.logger.error("Could not replace %s to %s - %s",
                              search_text, file_path, error)
            return False

        return True

    async def update_dashboard_resources(self, appId, url) -> None:
        """Update dashboard resources."""
        if not (resources := self._get_resource_handler()):
            return

        if not resources.loaded:
            await resources.async_load()

        for entry in resources.async_items():
            if appId in (entry_url := entry["url"]):
                if entry_url != url:
                    self.logger.info(
                        "Updating existing dashboard resource from %s to %s",
                        entry_url,
                        url,
                    )
                    await resources.async_update_item(entry["id"], {"url": url})
                return

        # Nothing was updated, add the resource
        self.logger.info("Adding dashboard resource %s", url)
        await resources.async_create_item({"res_type": "module", "url": url})

    async def remove_dashboard_resources(self, appId) -> None:
        """Remove dashboard resources."""
        if not (resources := self._get_resource_handler()):
            return

        if not resources.loaded:
            await resources.async_load()

        for entry in resources.async_items():
            if appId in entry["url"]:
                self.logger.info("Removing dashboard resource %s", entry["url"])
                await resources.async_delete_item(entry["id"])
                return

    def _get_resource_handler(self) -> ResourceStorageCollection | None:
        """Get the resource handler."""
        resources: ResourceStorageCollection | None
        if not (hass_data := self.hass.data):
            self.logger.error("Can not access the hass data")
            return

        if (lovelace_data := hass_data.get("lovelace")) is None:
            self.logger.warning("Can not access the lovelace integration data")
            return

        if AwesomeVersion(HA_VERSION) > "2025.1.99":
            # Changed to 2025.2.0
            # Changed in https://github.com/home-assistant/core/pull/136313
            resources = lovelace_data.resources
        else:
            resources = lovelace_data.get("resources")

        if resources is None:
            self.logger.warning("Can not access the dashboard resources")
            return

        if not hasattr(resources, "store") or resources.store is None:
            self.logger.info("YAML mode detected, can not update resources")
            return

        if resources.store.key != "lovelace_resources" or resources.store.version != 1:
            self.logger.warning("Can not use the dashboard resources")
            return

        return resources

    async def get_md5(self, data):
        m = hashlib.md5()
        m.update(data.encode())
        return m.hexdigest()

    def get_repo_version(self, repo: dict[str, Any], versionName):
        for version in repo['version']:
            if version['versionName'] == versionName:
                return version
        return None

    async def async_load_manifest(self, manifest_path) -> None:
        if manifest_path.exists():
            def _load_manifest():
                with open(manifest_path, encoding="utf-8") as manifestFile:
                    return json.load(manifestFile)

            return await self.hass.async_add_executor_job(_load_manifest)

        return None
