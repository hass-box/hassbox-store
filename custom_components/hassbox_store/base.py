from __future__ import annotations

import gzip
import os, re
import pathlib
import shutil
import zipfile
import tarfile
import hashlib
import json
from typing import Any
import logging
import time
import tempfile

from homeassistant.core import HomeAssistant
from homeassistant.const import __version__ as HAVERSION
from aiohttp.client import ClientSession, ClientTimeout
from urllib.parse import urlparse, parse_qs
from .utils.logger import LOGGER
from .utils.store import async_save_to_store, async_load_from_store
from packaging.version import parse as parse_version

class HassBoxStore:
    hass: HomeAssistant | None = None
    session: ClientSession | None = None
    config: dict[str, Any] | None = None
    enable: bool = False
    disabled_reason: str | None = None
    log: logging.Logger = LOGGER
    first_time: bool = True

    async def async_check_valid(self):
        response = await self.get("https://hassbox.cn/api/public/integration/checkValid")
        if response.status == 200:
            self.enable = True
        else:
            self.enable = False
            result = await response.json()
            self.disabled_reason = result['errmsg']
            self.log.error(self.disabled_reason)

    async def async_update_data(self):
        last_time_update = 0
        if self.config.get("last_time_update"):
            last_time_update = self.config["last_time_update"]

        if (last_time_update + 3600 * 24) > time.time() and self.first_time == False:
            return

        self.first_time = False
        
        response = await self.get("https://hassbox.cn/api/public/integration/data")
        if response.status != 200:
            return
        
        result = await response.json()
        self.config["message"] = result["message"]
        self.config["integration"] = result["integration"]
        self.config["last_time_update"] = time.time()

        await async_save_to_store(self.hass, "hassbox_store.config", self.config)

        response = await self.get(result["data_source_url"])
        if response.status != 200:
            return
        
        result = await response.json()
        await async_save_to_store(self.hass, "hassbox.repo", result)

        result = await async_load_from_store(self.hass, "lovelace_resources") or {}

    async def async_install_integration(self, repo: dict[str, Any]):
        repo_version = self.get_repo_version(repo)
        if repo_version is None:
            self.log.error("%s withou version", repo['id'])
            return False
        
        assets_download_url = "https://get.hassbox.cn/integration/" + repo["id"] + "/" + repo_version["name"] + "/" + repo_version["assets_name"]
        filecontent = await self.async_download_file(assets_download_url)
        if filecontent is None:
            self.log.error("%s was not downloaded", assets_download_url)
            return False
        
        temp_assets_dir = await self.hass.async_add_executor_job(tempfile.mkdtemp)
        temp_assets_file = f"{temp_assets_dir}/{repo_version['assets_name']}"
        result = await self.async_save_file(temp_assets_file, filecontent)
        if not result:
            self.log.error("Could not save download file")
            return False
        
        assets_filename = repo_version['assets_name'].split('.')[0]
        temp_assets_extract_dir = f"{temp_assets_dir}/{assets_filename}"
        if repo_version['assets_name'].endswith('.zip'):
            with zipfile.ZipFile(temp_assets_file, "r") as zip_file:
                zip_file.extractall(temp_assets_extract_dir)
        elif repo_version['assets_name'].endswith('.tar.gz'):
            tar = tarfile.open(temp_assets_file)
            tar.extractall(path = temp_assets_extract_dir)

        hassConfigPath = self.hass.config.path()
        installed = False

        if repo["type"] == "integration":
            component_directory = f"{hassConfigPath}/custom_components"
            component_name = None
            found_component = False

            for root, dirs, files in os.walk(temp_assets_extract_dir):
                for f in files:
                    if f == "manifest.json":
                        component_name = os.path.basename(root)
                        local_dir = f"{component_directory}/{component_name}"
                        if os.path.exists(local_dir):
                            shutil.rmtree(local_dir)
                        shutil.move(root, component_directory)
                        found_component = True
                        break

                if found_component:
                    break

                for d in dirs:
                    if d == "custom_components" :
                        files = os.listdir(os.path.join(root, d))
                        for file in files:
                            if os.path.isdir(os.path.join(root, d, file)) and os.path.exists(os.path.join(root, d, file, "manifest.json")):
                                component_name = file
                                local_dir = f"{component_directory}/{component_name}"
                                if os.path.exists(local_dir):
                                    shutil.rmtree(local_dir)
                                shutil.move(os.path.join(root, d, file), component_directory)
                                found_component = True

            if found_component:
                repo["component_directory"] = f"{component_directory}/{component_name}"
                repo["component_name"] = component_name
                installed = True

        elif repo["type"] == "theme":
            theme_directory = f"{hassConfigPath}/themes/{repo['id'].split('/')[1]}"
            found_theme = False

            for root, dirs, files in os.walk(temp_assets_extract_dir):
                for d in dirs:
                    if d == "themes" :
                        files = os.listdir(os.path.join(root, d))
                        for file in files:
                            if file.endswith(".yaml"):
                                await self.async_replace_file(os.path.join(root, d, file), "hacsfiles", "local")
                            if not os.path.exists(theme_directory):
                                os.makedirs(theme_directory)
                            shutil.move(os.path.join(root, d, file), os.path.join(theme_directory, file))
                        found_theme = True
            
            if found_theme:
                repo["theme_directory"] = theme_directory
                installed = True

        elif repo["type"] == "card":
            card_directory = f"{hassConfigPath}/www/{repo['id'].split('/')[1]}"
            card_name = None
            found_card = False

            if repo_version['assets_name'].endswith('.js'):
                card_name = repo_version['assets_name']
                if not os.path.exists(card_directory):
                    os.makedirs(card_directory)
                local_file = f"{card_directory}/{card_name}"
                if os.path.exists(local_file):
                    os.remove(local_file)
                shutil.move(temp_assets_file, local_file)
                found_card = True
            else :
                if repo_version.get("filename"):
                    valid_filenames = (repo_version["filename"],)
                else:
                    name = repo['id'].split("/")[1]
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
                                if file == filename:
                                    card_name = file
                                    if not os.path.exists(card_directory):
                                        os.makedirs(card_directory)
                                    local_file = f"{card_directory}/{card_name}"
                                    if os.path.exists(local_file):
                                        os.remove(local_file)
                                    shutil.move(os.path.join(root, file), local_file)

                                    with open(local_file, "rb") as f_in:
                                        with gzip.open(local_file + ".gz", "wb") as f_out:
                                            shutil.copyfileobj(f_in, f_out)
                                            
                                    found_card = True
                                    break

            if found_card:
                repo["card_directory"] = card_directory
                repo["card_name"] = card_name
                installed = True
                resource_url = "/local/" + repo['id'].split('/')[1] + "/" + card_name
                lovelace_resources = await async_load_from_store(self.hass, "lovelace_resources") or {}
                installedBefore = False
                for item in lovelace_resources["items"]:
                    if item["url"].startswith(resource_url):
                        item["url"] = resource_url + "?tag=" + str(int(time.time()))
                        installedBefore = True
                        break
                if not installedBefore:
                    resource_url = resource_url + "?tag=" + str(int(time.time()))
                    id = self.get_md5(resource_url)
                    lovelace_resources["items"].append({"url": resource_url, "type": "module", "id": id })
                await async_save_to_store(self.hass, "lovelace_resources", lovelace_resources)

        if installed and repo["id"] != "hass-box/hassbox-integration":
            result = await async_load_from_store(self.hass, "hassbox.installed") or {}
            repo['version_name'] = repo_version['name']
            del repo['version_simple']
            result[repo['id']] = repo
            await async_save_to_store(self.hass, "hassbox.installed", result)

        def cleanup_temp_assets_dir():
            if os.path.exists(temp_assets_dir):
                shutil.rmtree(temp_assets_dir)

        await self.hass.async_add_executor_job(cleanup_temp_assets_dir)
            
        return installed
    
    async def async_delete_integration(self, repo: dict[str, Any]):
        local_dir = None
        if repo["type"] == "integration":
            local_dir = repo["component_directory"]
            
        elif repo["type"] == "theme":
            local_dir = repo["theme_directory"]

        elif repo["type"] == "card":
            local_dir = repo["card_directory"]
            resource_url = "/local/" + repo['id'].split('/')[1] + "/" + repo['card_name']
            lovelace_resources = await async_load_from_store(self.hass, "lovelace_resources") or {}
            item_id = None
            for item in lovelace_resources["items"]:
                if item["url"].startswith(resource_url):
                    item_id = item["id"]
                    break
            
            if item_id:
                lovelace_resources["items"] = [d for d in lovelace_resources["items"] if d['id'] != item_id]
                await async_save_to_store(self.hass, "lovelace_resources", lovelace_resources)

        if local_dir:
            shutil.rmtree(local_dir)

        result = await async_load_from_store(self.hass, "hassbox.installed") or {}
        result.pop(repo["id"])
        await async_save_to_store(self.hass, "hassbox.installed", result)

        return True
    
    async def get(self, url: str):
        try:
            uuid = self.hass.data["core.uuid"]
            token = self.config["token"]
            certificate = self.config["certificate"]
            params = {"uuid": uuid, "token": token, "certificate": certificate}
            response = await self.session.get(url, params=params)
        except Exception as exception:
            raise Exception(f"Error fetching data from HassBox Store: {exception}") from exception
        
        return response
    
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

        except (
            BaseException
        ) as exception:
            self.log.error("Download failed - %s", exception)
        return None
    
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
            self.log.error("Could not write data to %s - %s", file_path, error)
            return False

        return os.path.exists(file_path)
    
    async def async_replace_file(self, file_path, search_text, replace_text):

        def _replace_file():
            with open(file_path, 'r') as file :
                filedata = file.read()
            filedata = filedata.replace(search_text, replace_text)
            with open(file_path, 'w') as file:
                file.write(filedata)

        try:
            await self.hass.async_add_executor_job(_replace_file)
        except (
            BaseException
        ) as error:
            self.log.error("Could not replace %s to %s - %s", search_text, file_path, error)
            return False

        return True
    
    async def get_md5(self, data):
        m = hashlib.md5()
        m.update(data.encode())
        return m.hexdigest()

    def get_repo_version(self, repo: dict[str, Any]):
        for version in repo['version_simple']:
            if repo.get("homeassistant"):
                if parse_version(repo["homeassistant"]) >= parse_version(HAVERSION):
                    return version
            else:
                return version

        return None

    def has_update(self, installedRepo: dict[str, Any], updatedRepo: dict[str, Any]):
        version = self.get_repo_version(updatedRepo)
        if version is None:
            return False
        if  version["name"] != installedRepo["version_name"]:
            return True
        else:
            return False
        
