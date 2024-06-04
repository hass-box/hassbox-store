
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import selector

from .const import DOMAIN, STORE_VERSION, STORE_ID
from .data_client import HassBoxDataClient
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

        config = await async_load_from_store(self.hass, "hassbox_store.config") or None
        self.data_client = HassBoxDataClient(hass=self.hass, config=config)
        result = await self.data_client.get_qrcode()
        
        if "errcode" in result and result["errcode"] == 200:
            return self.async_create_entry(title="HassBox集成商店", data={})

        if "ticket" not in result:
            return self.async_abort(
                reason="qrcode_error",
                description_placeholders={"errmsg": result["errmsg"]},
            )

        return self.async_show_form(
            step_id="bind_wechat",
            description_placeholders={
                "qr_image": '<img src="https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=' + result["ticket"] + '" width="250"/>'
            },
        )

    async def async_step_bind_wechat(self, user_input=None):
        result = await self.data_client.check_state()

        if result["errcode"] != 0:
            return self.async_abort(
                reason="qrcode_error",
                description_placeholders={"errmsg": result["errmsg"]},
            )

        return self.async_create_entry(title="HassBox集成商店", data={})

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

        self.installedRepoMap = await async_load_from_store(self.hass, "hassbox_store.installed")
        self.installedRepoList = []
        for id in self.installedRepoMap:
            self.installedRepoList.append(self.installedRepoMap[id])
            
        if len(self.installedRepoList) > 0:
            options["delete_integration"] = "删除 已安装的集成、卡片和主题样式"
            options["view_integration"] = "查看 已安装的集成、卡片和主题样式"

        
        self.repoList = await async_load_from_store(self.hass, "hassbox_store.repo") or []
        self.repoMap = {}
        for repo in self.repoList:
            self.repoMap[repo["id"]] = repo

        has_update = 0
        for installedRepo in self.installedRepoList:
            if self.hassbox.has_update(installedRepo, self.repoMap[installedRepo["id"]]):
                has_update += 1
        
        self.hassboxStoreRepo = { "id": STORE_ID, "version_name": STORE_VERSION }
        if self.hassbox.has_update(self.hassboxStoreRepo, self.repoMap[self.hassboxStoreRepo["id"]]):
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

        if user_input is None:
            user_input = {}
        else:
            selectedRepos = []
            integrations_errors = []
            for id in user_input['integrations']:
                selectedRepos.append(self.repoMap[id])
            if len(integrations_errors) == 0:
                return await self.async_step_install(selectedRepos, "安装")
            else:
                version_incompatible = "\n\n".join(integrations_errors)
                errors["integrations"] = "version_incompatible"
        
        installedKeys = list(self.installedRepoMap.keys())
        installedKeys.append(STORE_ID)
        uninstalledRepoList = [d for d in self.repoList if d['id'] not in installedKeys]
        uninstalledRepoList = sorted(uninstalledRepoList, key=lambda repo: (repo['star_count'], repo['forks_count']), reverse=True)
        options = []
        for repo in uninstalledRepoList:
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

        if user_input is None:
            user_input = {}
        else:
            selectedRepos = []
            for id in user_input['integrations']:
                selectedRepos.append(self.installedRepoMap[id])
            return await self.async_step_delete(selectedRepos)
        
        options = []
        for installedRepo in self.installedRepoList:
            options.append({"label": installedRepo["name"], "value": installedRepo["id"]})

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
        integrationRepo = []
        cardRepo = []
        themeRepo = []
        for repo in self.installedRepoList:
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

        if user_input is None:
            user_input = {}
        else:
            selectedRepos = []
            integrations_errors = []

            for id in user_input['integrations']:
                selectedRepos.append(self.repoMap[id])
            if len(integrations_errors) == 0:
                return await self.async_step_install(selectedRepos, "更新")
            else:
                version_incompatible = "\n\n".join(integrations_errors)
                errors["integrations"] = "version_incompatible"
        
        updateRepo = []

        for repo in self.installedRepoList:
            if self.hassbox.has_update(repo, self.repoMap[repo["id"]]):
                updateRepo.append(repo)
        
        self.hassboxStoreRepo = { "id": STORE_ID, "name": "HassBox集成商店", "version_name": STORE_VERSION }
        if self.hassbox.has_update(self.hassboxStoreRepo, self.repoMap[self.hassboxStoreRepo["id"]]):
            updateRepo.append(self.hassboxStoreRepo)
        
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
