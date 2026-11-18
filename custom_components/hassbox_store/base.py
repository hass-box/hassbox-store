"""Base HassBoxStore class."""

from __future__ import annotations

import gzip
import io
import os
import pathlib
import random
import time
import asyncio
import tempfile
import zipfile
import tarfile
import shutil
import hashlib
import json
import socket

from datetime import datetime

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

DOCKER_SOCKET_PATH = "/var/run/docker.sock"
DOCKER_SOCKET_MISSING_MESSAGE = (
    "当前 Home Assistant 容器未挂载 /var/run/docker.sock，无法执行容器升级。"
    "如果你是通过 NAS/Docker 方式安装的 Home Assistant，请在容器设置中把宿主机的 "
    "/var/run/docker.sock 挂载到容器内的 /var/run/docker.sock（读写），"
    "然后重启 Home Assistant 后重试。"
)
ERR_DOCKER_SOCKET_MISSING = 2


def _docker_error_message(error: Exception) -> str:
    """把 Docker Socket 缺失、镜像拉取失败等错误转换为可读提示。"""
    text = str(error)
    lower = text.lower()
    if isinstance(error, PermissionError) or "permission denied" in lower:
        return (
            "无法访问 /var/run/docker.sock（权限不足）。"
            "请确认挂载为读写模式（rw），并检查该文件的权限。原始错误：" + text
        )
    if (
        isinstance(error, FileNotFoundError)
        or "no such file or directory" in lower
        or "docker.sock" in lower
    ):
        return DOCKER_SOCKET_MISSING_MESSAGE
    if "403" in text or "401" in text or "denied" in lower:
        return (
            "拉取镜像失败：镜像仓库拒绝访问。"
            "可能是该版本还没有同步到加速镜像，或仓库暂时限流，请稍后重试或换一个版本。"
            "原始错误：" + text
        )
    return text


CORE_IMAGE_REPOSITORY = "ghcr.io/home-assistant/home-assistant"
CORE_PULL_REPOSITORY = "registry.cn-hangzhou.aliyuncs.com/home-assistant-hub/home-assistant"
UPGRADE_RESULT_FILE = "hassbox_store_upgrade_result.json"

UPGRADE_HELPER_SCRIPT = r'''
import http.client, json, os, socket, time

SOCK = "/var/run/docker.sock"
OLD_ID = os.environ["OLD_ID"]
OLD_NAME = os.environ["OLD_NAME"]
NEW_NAME = os.environ["NEW_NAME"]
BACKUP_NAME = os.environ["BACKUP_NAME"]
RESULT_FILE = os.environ.get("RESULT_FILE", "")
VERSION = os.environ.get("VERSION", "")

class UnixConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost")

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCK)
        self.sock = sock

def api(method, path, body=None):
    conn = UnixConnection()
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, payload, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    if resp.status >= 400:
        raise RuntimeError(method + " " + path + " -> " + str(resp.status) + ": " + data.decode("utf-8", "ignore")[:300])
    if data:
        try:
            return json.loads(data)
        except ValueError:
            return None
    return None

def write_result(status, error=""):
    if not RESULT_FILE:
        return
    try:
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "status": status, "error": error, "time": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    except Exception:
        pass

def is_running(name):
    info = api("GET", "/containers/" + name + "/json")
    state = info.get("State") or {}
    return bool(state.get("Running"))

def remove_self():
    # 删除自身容器，避免升级完成后 helper 一直残留
    # Docker 未指定 hostname 时，容器内 hostname 就是短容器 ID
    self_id = socket.gethostname()
    try:
        api("DELETE", "/containers/" + self_id + "?force=1")
    except Exception:
        pass

old_renamed = False
new_renamed = False

try:
    api("POST", "/containers/" + OLD_ID + "/stop?t=15")
    api("POST", "/containers/" + OLD_ID + "/rename?name=" + BACKUP_NAME)
    old_renamed = True
    api("POST", "/containers/" + NEW_NAME + "/rename?name=" + OLD_NAME)
    new_renamed = True
    api("POST", "/containers/" + OLD_NAME + "/start")
    time.sleep(20)
    if not is_running(OLD_NAME):
        raise RuntimeError("新容器启动后未保持运行")
    try:
        api("DELETE", "/containers/" + BACKUP_NAME + "?force=1")
    except Exception:
        pass
    write_result("success")
except Exception as exc:
    if new_renamed:
        try:
            api("POST", "/containers/" + OLD_NAME + "/stop?t=5")
        except Exception:
            pass
        try:
            api("DELETE", "/containers/" + OLD_NAME + "?force=1")
        except Exception:
            pass
    else:
        try:
            api("DELETE", "/containers/" + NEW_NAME + "?force=1")
        except Exception:
            pass
    if old_renamed:
        try:
            api("POST", "/containers/" + BACKUP_NAME + "/rename?name=" + OLD_NAME)
        except Exception:
            pass
        try:
            api("POST", "/containers/" + OLD_NAME + "/start")
        except Exception:
            pass
    write_result("rolled_back", str(exc))
finally:
    remove_self()
'''


def json_dumps(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _parse_image_created(value):
    """Docker 返回的 Created 可能是 ISO 时间字符串，也可能已是 Unix 时间戳。"""
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return 0
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # 兼容纳秒级小数秒，fromisoformat 最多支持微秒
    if "." in text:
        head, rest = text.split(".", 1)
        index = 0
        while index < len(rest) and rest[index].isdigit():
            index += 1
        text = head + "." + rest[:index][:6] + rest[index:]
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


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
        self.local_uploads = {}
        self.upgrade_result = None

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

    def _safe_local_name(self, fileName: str) -> str:
        base = os.path.basename(fileName or "")
        for suffix in (".tar.gz", ".tgz", ".zip", ".js", ".yaml"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
                break
        return "".join(c if c.isalnum() or c in "-_." else "-" for c in base) or "local"

    def _detect_local_app_type(self, fileName: str, filecontent: bytes):
        """Detect app type and integration domain from an uploaded file."""
        lower_name = (fileName or "").lower()

        if lower_name.endswith(".js"):
            return "card", None

        try:
            if lower_name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(filecontent)) as archive:
                    names = archive.namelist()
                    manifests = [
                        name
                        for name in names
                        if name.rstrip("/").endswith("manifest.json")
                    ]
                    if manifests:
                        with archive.open(manifests[0]) as manifest_file:
                            manifest = json.load(manifest_file)
                        return "integration", manifest.get("domain")
            elif lower_name.endswith((".tar.gz", ".tgz")):
                with tarfile.open(fileobj=io.BytesIO(filecontent)) as archive:
                    names = archive.getnames()
                    manifests = [
                        member
                        for member in archive.getmembers()
                        if member.name.rstrip("/").endswith("manifest.json")
                    ]
                    if manifests:
                        manifest_file = archive.extractfile(manifests[0])
                        if manifest_file is not None:
                            manifest = json.load(manifest_file)
                            return "integration", manifest.get("domain")
            else:
                return None, None
        except BaseException as error:
            self.logger.error("无法解析上传文件 %s: %s", fileName, error)
            return None, None

        if any(
            name.rstrip("/") == "themes" or "/themes/" in name or name.startswith("themes/")
            for name in names
        ):
            return "theme", None

        if any(name.lower().endswith(".js") for name in names):
            return "card", None

        return None, None

    async def async_install_local_file(
        self, fileName: str, filecontent: bytes, appType: str | None = None
    ):
        """Install an uploaded file without recording it in the installed store."""
        fileName = os.path.basename(fileName or "")
        if not fileName or not filecontent:
            return {"errcode": 1, "errmsg": "文件无效"}

        detected_type, domain = self._detect_local_app_type(fileName, filecontent)
        if appType:
            if detected_type is not None and detected_type != appType:
                return {"errcode": 1, "errmsg": "文件内容与所选类型不匹配"}
        else:
            appType = detected_type

        if appType not in ("integration", "card", "theme"):
            return {"errcode": 1, "errmsg": "无法识别文件类型，请手动选择"}

        local_version = "local"
        if appType == "card":
            local_name = self._safe_local_name(fileName)
            version = {"versionName": local_version}
            if fileName.lower().endswith(".js"):
                version["filename"] = fileName
            appInfo = {
                "appId": local_name,
                "appType": "card",
                "repoId": f"local/{local_name}",
                "version": [version],
            }
        elif appType == "theme":
            local_name = self._safe_local_name(fileName)
            appInfo = {
                "appId": local_name,
                "appType": "theme",
                "repoId": f"local/{local_name}",
                "version": [{"versionName": local_version}],
            }
        else:
            appInfo = {
                "appId": "local_upload",
                "appType": "integration",
                "repoId": "local/upload",
                "version": [{"versionName": local_version}],
            }

        appAssets = {"assetsName": fileName}
        result = await self.async_install_integration(
            appInfo, local_version, appAssets, False, filecontent=filecontent
        )
        if result is not True:
            return {
                "errcode": 1,
                "errmsg": result if isinstance(result, str) else "安装失败，未找到有效的应用文件",
            }

        return {
            "errcode": 0,
            "appType": appType,
            "domain": domain,
            "needRestart": appType == "integration",
        }

    def _safe_extract_zip(self, archive: zipfile.ZipFile, target_dir: str) -> None:
        base = os.path.realpath(target_dir)
        for member in archive.infolist():
            dest = os.path.realpath(os.path.join(base, member.filename))
            if dest != base and not dest.startswith(base + os.sep):
                raise ValueError(f"非法路径：{member.filename}")
        archive.extractall(base)

    def _safe_extract_tar(self, archive: tarfile.TarFile, target_dir: str) -> None:
        base = os.path.realpath(target_dir)
        for member in archive.getmembers():
            dest = os.path.realpath(os.path.join(base, member.name))
            if dest != base and not dest.startswith(base + os.sep):
                raise ValueError(f"非法路径：{member.name}")
        archive.extractall(base)

    async def async_upload_local_file_to_dir(
        self,
        fileName: str,
        filecontent: bytes,
        targetPath: str,
        extract: bool = False,
    ):
        """Upload a local file into a directory without installing it."""
        fileName = os.path.basename(fileName or "")
        targetPath = (targetPath or "").strip()
        if not fileName or not filecontent:
            return {"errcode": 1, "errmsg": "文件无效"}
        if not targetPath:
            return {"errcode": 1, "errmsg": "请填写目标目录"}

        config_dir = os.path.realpath(self.hass.config.config_dir)
        target_dir = targetPath
        if not os.path.isabs(target_dir):
            target_dir = os.path.join(config_dir, target_dir)
        target_dir = os.path.realpath(target_dir)
        if target_dir != config_dir and not target_dir.startswith(
            config_dir + os.sep
        ):
            return {"errcode": 1, "errmsg": "目标目录必须位于配置目录内"}

        def _write():
            os.makedirs(target_dir, exist_ok=True)
            lower_name = fileName.lower()
            if extract and lower_name.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(filecontent)) as archive:
                    self._safe_extract_zip(archive, target_dir)
                return target_dir
            if extract and lower_name.endswith((".tar.gz", ".tgz")):
                with tarfile.open(fileobj=io.BytesIO(filecontent)) as archive:
                    self._safe_extract_tar(archive, target_dir)
                return target_dir
            file_path = os.path.join(target_dir, fileName)
            with open(file_path, "wb") as file_handle:
                file_handle.write(filecontent)
            return file_path

        try:
            saved_path = await self.hass.async_add_executor_job(_write)
        except BaseException as error:
            self.logger.error("上传文件到目录失败：%s", error)
            return {"errcode": 1, "errmsg": f"上传失败：{error}"}

        return {"errcode": 0, "mode": "upload", "path": saved_path}

    async def async_install_integration(
        self, appInfo: dict[str, Any], version, appAssets, saveToStore=True, filecontent=None
    ):
        appInfo = copy.deepcopy(appInfo)
        app_version = self.get_repo_version(appInfo, version)
        if app_version is None:
            return "without version"

        assetsName = appAssets['assetsName']
        if filecontent is None:
            filecontent = await self.async_download_assets(appAssets)
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
        elif assetsName.endswith(('.tar.gz', '.tgz')):
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

                    js_files = [
                        os.path.join(root, file)
                        for root, _dirs, files in os.walk(temp_assets_extract_dir)
                        for file in files
                        if file.endswith('.js')
                    ]
                    candidate = None
                    for js_file in js_files:
                        if os.path.basename(js_file).lower() in (
                            filename.lower() for filename in valid_filenames
                        ):
                            candidate = js_file
                            break
                    if candidate is None and len(js_files) == 1:
                        candidate = js_files[0]

                    if candidate is not None:
                        card_name = os.path.basename(candidate)
                        if not os.path.exists(card_directory):
                            os.makedirs(card_directory)
                        local_file = f"{card_directory}/{card_name}"
                        if os.path.exists(local_file):
                            os.remove(local_file)
                        shutil.move(candidate, local_file)

                        with open(local_file, "rb") as f_in:
                            with gzip.open(local_file + ".gz", "wb") as f_out:
                                shutil.copyfileobj(f_in, f_out)

                        found_card = True
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
            if not await self.hass.async_add_executor_job(self._docker_socket_available):
                return {
                    "errcode": ERR_DOCKER_SOCKET_MISSING,
                    "errmsg": DOCKER_SOCKET_MISSING_MESSAGE,
                }
            self.pull_status = 0
            self.pull_error = ""
            self.layer_progress = {}

            def _pull_image():
                if not self._docker_socket_available():
                    self._docker_socket_missing()
                    return
                try:
                    client = docker.from_env()
                    pull_logs = client.api.pull(
                        repository=CORE_PULL_REPOSITORY,
                        tag=version,
                        stream=True,
                        decode=True
                    )
                    self.pull_status = 1
                    for log in pull_logs:
                        self._handle_pull_log(log)
                    # 镜像从加速仓库拉取后，重新标记为官方镜像名，容器使用官方镜像
                    pull_image = client.images.get(CORE_PULL_REPOSITORY + ":" + version)
                    pull_image.tag(CORE_IMAGE_REPOSITORY, tag=version)
                    self.pull_status = 2
                    client.api.inspect_image(CORE_IMAGE_REPOSITORY + ":" + version)
                    self.pull_status = 3
                except Exception as e:
                    self.logger.error("pull core image error:  %s", e)
                    self.pull_status = -1
                    self.pull_error = _docker_error_message(e)

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

    async def async_get_core_images(self):
        try:
            return await self.hass.async_add_executor_job(
                self._get_core_images_sync
            )
        except Exception as e:
            self.logger.error("get core images error:  %s", e)
            return {"errcode": 1, "errmsg": _docker_error_message(e)}

    async def async_clean_core_images(self, image_ids):
        try:
            return await self.hass.async_add_executor_job(
                self._clean_core_images_sync, image_ids
            )
        except Exception as e:
            self.logger.error("clean core images error:  %s", e)
            return {"errcode": 1, "errmsg": _docker_error_message(e)}

    def _get_core_images_sync(self):
        """列出 Home Assistant 镜像，并标记是否正被容器使用。"""
        if not self._docker_socket_available():
            return {
                "errcode": ERR_DOCKER_SOCKET_MISSING,
                "errmsg": DOCKER_SOCKET_MISSING_MESSAGE,
            }

        client = docker.from_env()
        self._cleanup_upgrade_helpers(client)
        return {"errcode": 0, "images": self._collect_core_images(client)}

    def _collect_core_images(self, client):
        # 收集所有容器（含已停止的备份容器）正在使用的镜像
        in_use = set()
        for container in client.containers.list(all=True):
            image_id = container.attrs.get("Image")
            if image_id:
                in_use.add(image_id)

        images = []
        for image in client.images.list():
            repoTags = image.attrs.get("RepoTags") or []
            core_tags = [
                tag
                for tag in repoTags
                if tag.startswith(CORE_IMAGE_REPOSITORY + ":")
                or tag.startswith(CORE_PULL_REPOSITORY + ":")
            ]
            if not core_tags:
                continue
            images.append(
                {
                    "id": image.id,
                    "tags": core_tags,
                    "size": int(image.attrs.get("Size") or 0),
                    "created": _parse_image_created(image.attrs.get("Created")),
                    "in_use": image.id in in_use,
                }
            )
        return images

    def _clean_core_images_sync(self, image_ids):
        """删除用户选中且未被任何容器使用的 Home Assistant 镜像。"""
        if not self._docker_socket_available():
            return {
                "errcode": ERR_DOCKER_SOCKET_MISSING,
                "errmsg": DOCKER_SOCKET_MISSING_MESSAGE,
            }

        client = docker.from_env()
        self._cleanup_upgrade_helpers(client)

        targets = set(image_ids or [])
        removed = 0
        freed = 0
        for info in self._collect_core_images(client):
            if info["id"] not in targets or info["in_use"]:
                continue
            try:
                client.images.get(info["id"]).remove(force=True)
                removed += 1
                freed += info["size"]
            except Exception as e:
                self.logger.warning("remove core image error: %s", e)

        return {"errcode": 0, "count": removed, "size": freed}

    def _docker_socket_available(self) -> bool:
        """检查是否可以通过 Docker Socket 访问 Docker。"""
        if os.environ.get("DOCKER_HOST"):
            return True
        return os.path.exists(DOCKER_SOCKET_PATH)

    def _docker_socket_missing(self) -> None:
        self.logger.error(DOCKER_SOCKET_MISSING_MESSAGE)
        self.pull_status = -1
        self.pull_error = DOCKER_SOCKET_MISSING_MESSAGE

    async def async_upgrade_core_container(self, version):
        try:
            return await self.hass.async_add_executor_job(
                self._upgrade_core_container_sync, version
            )
        except Exception as e:
            self.logger.error("upgrade core container error:  %s", e)
            return {"errcode": 1, "errmsg": _docker_error_message(e)}

    def _get_self_container(self, client):
        """定位当前运行 Home Assistant 的容器。"""
        hostname = socket.gethostname()
        try:
            return client.containers.get(hostname)
        except docker.errors.NotFound:
            pass
        for path in ("/proc/self/cgroup", "/proc/1/cpuset"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                continue
            for i in range(len(text) - 63):
                token = text[i:i + 64]
                if all(c in "0123456789abcdef" for c in token):
                    try:
                        return client.containers.get(token)
                    except Exception:
                        pass
        for container in client.containers.list():
            config = container.attrs.get("Config") or {}
            if config.get("Hostname") == hostname:
                return container
        raise RuntimeError("无法定位当前 Home Assistant 容器")

    def _build_host_config(self, host_config):
        keys = (
            "Binds", "NetworkMode", "PortBindings", "RestartPolicy", "AutoRemove",
            "VolumeDriver", "VolumesFrom", "CapAdd", "CapDrop", "CgroupnsMode",
            "Dns", "DnsOptions", "DnsSearch", "ExtraHosts", "GroupAdd", "IpcMode",
            "Links", "OomScoreAdj", "PidMode", "Privileged", "PublishAllPorts",
            "ReadonlyRootfs", "SecurityOpt", "UTSMode", "UsernsMode", "ShmSize",
            "Runtime", "Isolation", "CpuShares", "Memory", "NanoCpus", "CpuPeriod",
            "CpuQuota", "CpuRealtimePeriod", "CpuRealtimeRuntime", "CpusetCpus",
            "CpusetMems", "CpuCount", "BlkioWeight", "BlkioWeightDevice",
            "BlkioDeviceReadBps", "BlkioDeviceWriteBps", "BlkioDeviceReadIOps",
            "BlkioDeviceWriteIOps", "DeviceCgroupRules", "DeviceRequests",
            "MemoryReservation", "MemorySwap", "MemorySwappiness", "OomKillDisable",
            "PidsLimit", "Ulimits", "Tmpfs", "Sysctls", "Init", "Devices",
            "MaskedPaths", "ReadonlyPaths", "LogConfig", "Cgroup", "CgroupParent",
        )
        result = {key: copy.deepcopy(host_config[key]) for key in keys if key in host_config}
        result["AutoRemove"] = False
        destinations = set()
        for bind in host_config.get("Binds") or []:
            parts = bind.split(":")
            if len(parts) >= 2:
                destinations.add(parts[1])
        mounts = [
            copy.deepcopy(mount)
            for mount in host_config.get("Mounts") or []
            if mount.get("Destination") not in destinations
        ]
        if mounts:
            result["Mounts"] = mounts
        return result

    def _upgrade_result_path(self):
        return os.path.join(self.hass.config.config_dir, UPGRADE_RESULT_FILE)

    def _write_upgrade_result(self, status, error="", version=""):
        try:
            with open(self._upgrade_result_path(), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": version,
                        "status": status,
                        "error": error,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            self.logger.warning("write upgrade result error: %s", e)

    def _remove_upgrade_result(self):
        try:
            os.remove(self._upgrade_result_path())
        except Exception:
            pass

    def _cleanup_upgrade_helpers(self, client=None):
        """删除已退出的升级 helper 容器，避免升级完成后残留。"""
        try:
            if client is None:
                if not self._docker_socket_available():
                    return
                client = docker.from_env()
            for c in client.containers.list(
                all=True, filters={"label": "hassbox.store.upgrade=helper"}
            ):
                if c.status != "running":
                    c.remove(force=True)
        except Exception as e:
            self.logger.warning("cleanup upgrade helper error: %s", e)

    def _upgrade_core_container_sync(self, version):
        if not self._docker_socket_available():
            return {
                "errcode": ERR_DOCKER_SOCKET_MISSING,
                "errmsg": DOCKER_SOCKET_MISSING_MESSAGE,
            }

        client = docker.from_env()
        self._cleanup_upgrade_helpers(client)

        old = self._get_self_container(client)
        image_ref = CORE_IMAGE_REPOSITORY + ":" + version

        try:
            client.images.get(image_ref)
        except docker.errors.ImageNotFound:
            client.api.pull(repository=CORE_PULL_REPOSITORY, tag=version)
            client.images.get(CORE_PULL_REPOSITORY + ":" + version).tag(
                CORE_IMAGE_REPOSITORY, tag=version
            )

        host_config = copy.deepcopy(old.attrs.get("HostConfig") or {})
        config = copy.deepcopy(old.attrs.get("Config") or {})
        config["Image"] = image_ref
        config.pop("Hostname", None)
        config.pop("Domainname", None)
        config["HostConfig"] = self._build_host_config(host_config)

        suffix = str(int(time.time()))
        old_name = old.name
        new_name = old_name + "-new-" + suffix
        backup_name = old_name + "-backup-" + suffix
        helper_name = old_name + "-upgrader-" + suffix

        new_container = client.api.create_container_from_config(config, name=new_name)
        new_id = new_container["Id"]

        try:
            network_mode = host_config.get("NetworkMode")
            networks = (old.attrs.get("NetworkSettings") or {}).get("Networks") or {}
            for network_name, network_info in networks.items():
                if network_name in ("bridge", "host", "none") or network_name == network_mode:
                    continue
                try:
                    client.networks.get(network_name).connect(
                        new_id, aliases=network_info.get("Aliases")
                    )
                except Exception:
                    pass

            volumes = {DOCKER_SOCKET_PATH: {"bind": DOCKER_SOCKET_PATH, "mode": "rw"}}
            result_file = ""
            for bind in host_config.get("Binds") or []:
                parts = bind.split(":")
                if len(parts) >= 2 and parts[1] == "/config":
                    volumes[parts[0]] = {"bind": "/config", "mode": "rw"}
                    result_file = "/config/" + UPGRADE_RESULT_FILE
                    break
            if not result_file:
                for mount in host_config.get("Mounts") or []:
                    if mount.get("Destination") == "/config" and mount.get("Source"):
                        volumes[mount["Source"]] = {"bind": "/config", "mode": "rw"}
                        result_file = "/config/" + UPGRADE_RESULT_FILE
                        break

            self._write_upgrade_result("pending", version=version)
            helper = client.containers.create(
                image=old.attrs["Image"],
                name=helper_name,
                entrypoint=["python3", "-c", UPGRADE_HELPER_SCRIPT],
                command=[],
                environment={
                    "OLD_ID": old.id,
                    "OLD_NAME": old_name,
                    "NEW_NAME": new_name,
                    "BACKUP_NAME": backup_name,
                    "RESULT_FILE": result_file,
                    "VERSION": version,
                },
                volumes=volumes,
                detach=True,
                labels={"hassbox.store.upgrade": "helper"},
            )
            helper.start()
        except Exception:
            self._remove_upgrade_result()
            for name in (new_id, helper_name):
                try:
                    client.containers.get(name).remove(force=True)
                except Exception:
                    pass
            raise

        return {"errcode": 0, "errmsg": ""}

    async def async_load_upgrade_result(self):
        await self.hass.async_add_executor_job(self._cleanup_upgrade_helpers)
        result = await self.hass.async_add_executor_job(self._read_upgrade_result)
        if not result:
            return
        if result.get("status") == "pending":
            self.hass.async_create_task(self._async_watch_upgrade_result())
        else:
            await self._async_handle_upgrade_result(result)

    def _read_upgrade_result(self):
        path = self._upgrade_result_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.warning("read upgrade result error: %s", e)
            return None

    async def _async_watch_upgrade_result(self):
        for _ in range(40):
            await asyncio.sleep(3)
            result = await self.hass.async_add_executor_job(self._read_upgrade_result)
            if result and result.get("status") != "pending":
                await self._async_handle_upgrade_result(result)
                return

    async def _async_handle_upgrade_result(self, result):
        self.upgrade_result = result
        await self.hass.async_add_executor_job(self._remove_upgrade_result)
        if result.get("status") == "success":
            self.logger.info("Home Assistant 已升级到 %s", result.get("version"))
            return
        error = result.get("error") or "未知错误"
        self.logger.error(
            "Home Assistant 升级到 %s 失败，已回滚: %s",
            result.get("version"),
            error,
        )
        try:
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass,
                "升级到版本 %s 失败：%s。已自动回滚到原版本。"
                % (result.get("version"), error),
                title="HassBox 应用商店升级失败",
                notification_id="hassbox_store_upgrade_failed",
            )
        except Exception as e:
            self.logger.error("create upgrade notification error: %s", e)

    async def async_start_assistant(self):
        self.pull_status = 0
        self.pull_error = ""

        if not self._docker_socket_available():
            self._docker_socket_missing()
            return

        def _start_assistant():
            try:
                client = docker.from_env()
                result = self.get_assistant_container(client)
                if result == 0:
                    self.install_assistant(client)
                elif result is Exception:
                    self.pull_status = -1
                    self.pull_error = _docker_error_message(result)
                else:
                    self.logger.error("start assistant...")
                    self.pull_status = 3
                    result.start()

            except Exception as e:
                self.logger.error("start assistant error:  %s", e)
                self.pull_status = -1
                self.pull_error = _docker_error_message(e)

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

    async def async_download_assets(self, appAssets):
        fast_urls = appAssets.get("fastDownloadUrl")
        github_url = appAssets.get("githubAssetsDownloadUrl")

        if fast_urls and github_url:
            urls = [
                url.replace("$url", github_url)
                for url in fast_urls
                if url and "$url" in url
            ]
            random.shuffle(urls)
            for url in urls:
                filecontent = await self.async_download_file(url)
                if filecontent is not None:
                    return filecontent

        return await self.async_download_file(appAssets.get("assetsDownloadUrl"))

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
