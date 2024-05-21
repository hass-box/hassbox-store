
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import selector

from .const import DOMAIN, HASSBOX_VERSION
from .base import HassBoxStore
from .utils.store import async_load_from_store, async_save_to_store
from .utils.logger import LOGGER

class HassBoxStoreConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    
    def __init__(self):
        self.log = LOGGER

    async def async_step_user(self, user_input=None):
        errors = {}
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if self.hass.data.get(DOMAIN):
            return self.async_abort(reason="single_instance_allowed")
        
        if user_input is None:
            user_input = {}
        else:
            token = user_input['token']
            uuid = self.hass.data["core.uuid"]
            try:
                response = await async_get_clientsession(self.hass).post(
                    f"https://hassbox.cn/api/public/integration/bindToken",
                    json={"uuid": uuid, "token": token}
                )
            except Exception as exception:
                raise Exception(f"Error fetching data from HassBox Store: {exception}") from exception
            
            result = await response.json()
            if response.status == 200:
                config = await async_load_from_store(self.hass, "hassbox_store.config") or {} 
                config["token"] = token
                config["certificate"] = result["certificate"]
                await async_save_to_store(self.hass, "hassbox_store.config", config)
                return self.async_create_entry(
                    title="HassBox集成商店",
                    data={}
                )
            else :
                errors["token"] = result['errmsg']
        
        data_schema = {
            vol.Required("token") : selector({
                "text": {
                    "type": "password"
                }
            })
        }
        
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(data_schema),
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(entry)

class OptionsFlowHandler(config_entries.OptionsFlow):

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.log = LOGGER
        self.config_entry = config_entry
        
    async def async_step_init(self, user_input=None):
        self.hassbox: HassBoxStore = self.hass.data.get(DOMAIN)
        await self.hassbox.async_update_data()
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        if self.hassbox.enable is False:
            return self.async_abort(reason="disabled", description_placeholders={'message': self.hassbox.disabled_reason})

        options = {
            "install_integration": "安装 新的集成、卡片和主题样式",
        }

        installedRepo = await async_load_from_store(self.hass, "hassbox.installed")
        if len(installedRepo) > 0:
            options["delete_integration"] = "删除 已安装的集成、卡片和主题样式"
            options["view_integration"] = "查看 已安装的集成、卡片和主题样式"

        
        repoList = await async_load_from_store(self.hass, "hassbox.repo") or []
        has_update = 0
        for id in installedRepo.keys():
            for repo in repoList:
                if repo["id"] == id:
                    if self.hassbox.has_update(installedRepo[id], repo):
                        has_update += 1
                    break

        hassbox_version = self.hassbox.get_repo_version(self.hassbox.config["integration"])
        if HASSBOX_VERSION != hassbox_version["name"]:
            has_update += 1

        if has_update > 0:
            options["update_integration"] = "有 " + str(has_update) + " 个更新！"

        message = self.hassbox.config["message"]

        return self.async_show_menu(
            step_id="user",
            menu_options=options,
            description_placeholders={'message': message}
        )
    
    async def async_step_install_integration(self, user_input=None):
        errors = {}
        version_incompatible = ""
        
        repoList = await async_load_from_store(self.hass, "hassbox.repo") or []

        if user_input is None:
            user_input = {}
        else:
            selectedRepos = []
            integrations_errors = []
            for id in user_input['integrations']:
                for repo in repoList:
                    if repo["id"] == id:
                        selectedRepos.append(repo)
                        break
            if len(integrations_errors) == 0:
                return await self.async_step_install(selectedRepos, "安装")
            else:
                version_incompatible = "\n\n".join(integrations_errors)
                errors["integrations"] = "version_incompatible"
        
        installedRepo = await async_load_from_store(self.hass, "hassbox.installed")
        installedKeys = installedRepo.keys()
        repoList = [d for d in repoList if d['id'] not in installedKeys]
        repoList = sorted(repoList, key=lambda repo: (repo['star_count'], repo['forks_count']), reverse=True)
        options = []
        for repo in repoList:
            options.append({"label": repo["name"], "value": repo["id"]})
            
        data_schema = {
            vol.Required("integrations") : selector({
                "select": {
                    "options": options,
                    "mode": "dropdown",
                    "multiple": True
                }
            })
        }

        return self.async_show_form(
            step_id="install_integration", 
            data_schema=vol.Schema(data_schema),
            errors=errors,
            description_placeholders={'version_incompatible': version_incompatible}
        )

    async def async_step_delete_integration(self, user_input=None):
        errors = {}

        installedRepo = await async_load_from_store(self.hass, "hassbox.installed")
        if user_input is None:
            user_input = {}
        else:
            selectedRepos = []
            for id in user_input['integrations']:
                selectedRepos.append(installedRepo[id])
            return await self.async_step_delete(selectedRepos)
        
        options = []
        for key in installedRepo:
            repo = installedRepo[key]
            options.append({"label": repo["name"], "value": key})

        data_schema = {
            vol.Required("integrations") : selector({
                "select": {
                    "options": options,
                    "mode": "dropdown",
                    "multiple": True
                }
            })
        }

        return self.async_show_form(
            step_id="delete_integration", 
            data_schema=vol.Schema(data_schema),
            errors=errors
        )
    
    async def async_step_view_integration(self, user_input=None):
        installedRepo = await async_load_from_store(self.hass, "hassbox.installed")
        integrationRepo = []
        cardRepo = []
        themeRepo = []
        for key in installedRepo:
            repo = installedRepo[key]
            if repo["type"] == "integration":
                integrationRepo.append(repo)
            elif repo["type"] == "card":
                cardRepo.append(repo)
            elif repo["type"] == "theme":
                themeRepo.append(repo)

        viewMessage = ""
        if len(integrationRepo) > 0:
            viewMessage += "\n\n**集成**：\n\n"
            for repo in integrationRepo:
                viewMessage += "* " + self.get_repo_display(repo) + "\n"

        if len(cardRepo) > 0:
            viewMessage += "\n\n**卡片**：\n\n"
            for repo in cardRepo:
                viewMessage += "* " + self.get_repo_display(repo) + "\n"
                    
        if len(themeRepo) > 0:
            viewMessage += "\n\n**主题**：\n\n"
            for repo in themeRepo:
                viewMessage += "* " + self.get_repo_display(repo) + "\n"

        return self.async_abort(reason="view_installed", description_placeholders={'message': viewMessage})

    async def async_step_update_integration(self, user_input=None):
        errors = {}
        version_incompatible = ""

        repoList = await async_load_from_store(self.hass, "hassbox.repo") or []

        if user_input is None:
            user_input = {}
        else:
            selectedRepos = []
            integrations_errors = []

            if "hass-box/hassbox-integration" in user_input['integrations']:
                selectedRepos.append(self.hassbox.config["integration"])

            for id in user_input['integrations']:
                for repo in repoList:
                    if repo["id"] == id:
                        selectedRepos.append(repo)
                        break
            if len(integrations_errors) == 0:
                return await self.async_step_install(selectedRepos, "更新")
            else:
                version_incompatible = "\n\n".join(integrations_errors)
                errors["integrations"] = "version_incompatible"
        
        updateRepo = []

        hassbox_version = self.hassbox.get_repo_version(self.hassbox.config["integration"])
        if HASSBOX_VERSION != hassbox_version["name"]:
            updateRepo.append(self.hassbox.config["integration"])

        installedRepo = await async_load_from_store(self.hass, "hassbox.installed")
        for id in installedRepo.keys():
            for repo in repoList:
                if repo["id"] == id:
                    if self.hassbox.has_update(installedRepo[id], repo):
                        updateRepo.append(repo)
                    break
        
        options = []
        for repo in updateRepo:
            options.append({"label": repo["name"], "value": repo["id"]})

        data_schema = {
            vol.Required("integrations") : selector({
                "select": {
                    "options": options,
                    "mode": "dropdown",
                    "multiple": True
                }
            })
        }

        return self.async_show_form(
            step_id="update_integration", 
            data_schema=vol.Schema(data_schema), 
            errors=errors,
            description_placeholders={'version_incompatible': version_incompatible}
        )

    async def async_step_install(self, selectedRepos, type):
        install_success = ""
        install_failure = ""
        for repo in selectedRepos:
            result = await self.hassbox.async_install_integration(repo)
            if result:
                install_success += "* " + repo["name"] + (repo["extra"] if repo.get("extra") else "") + "\n"
            else :
                install_failure += "* " + repo["name"] + "\n"
                
        if len(install_failure) == 0:
            return self.async_abort(reason="install_success", description_placeholders={'type': type, 'message': install_success})
        else :
            return self.async_abort(reason="install_failure", description_placeholders={'type': type, 'message': install_failure})
            
    async def async_step_delete(self, selectedRepos):
        delete_message = ""
        for repo in selectedRepos:
            await self.hassbox.async_delete_integration(repo)
            delete_message += "* " + repo["name"] + "\n"

        return self.async_abort(reason="reboot", description_placeholders={'message': delete_message},)

    def get_repo_display(self, repo):
        display = repo["name"]
        
        if repo.get("extra"):
            display += repo["extra"]

        return display
