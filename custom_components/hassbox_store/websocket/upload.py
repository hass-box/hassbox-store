"""Register local file upload websocket commands."""

from __future__ import annotations

import os
import shutil
import uuid
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
import voluptuous as vol

from ..const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..base import HassBoxStore

MAX_UPLOAD_SIZE = 100 * 1024 * 1024
MAX_DIRECTORY_DEPTH = 3
EXCLUDED_DIRS = {"deps", "tts", "backups", "__pycache__", "node_modules"}


class LocalUploadSession:
    """Local file upload session."""

    def __init__(self, fileName: str, fileSize: int, startMsgId: int) -> None:
        self.file_name = fileName
        self.file_size = fileSize
        self.start_msg_id = startMsgId
        self.buffer = bytearray()
        self.unregister = None
        self.error: str | None = None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/local_upload/start",
        vol.Required("fileName"): str,
        vol.Required("fileSize"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def local_upload_start(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Start a local file upload and register a binary handler."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    if hassboxStore is None:
        connection.send_error(msg["id"], "not_found", "HassBox Store 未加载")
        return

    if msg["fileSize"] <= 0 or msg["fileSize"] > MAX_UPLOAD_SIZE:
        connection.send_error(msg["id"], "invalid_format", "文件大小超出限制")
        return

    session = LocalUploadSession(msg["fileName"], msg["fileSize"], msg["id"])
    upload_id = uuid.uuid4().hex

    def _handle_binary(_hass, _connection, data: bytes) -> None:
        if session.error is not None:
            return
        if len(session.buffer) + len(data) > MAX_UPLOAD_SIZE:
            session.error = "文件大小超出限制"
            return
        session.buffer.extend(data)

    handler_id, unregister = connection.async_register_binary_handler(
        _handle_binary
    )
    session.unregister = unregister
    hassboxStore.local_uploads[upload_id] = session

    def _cleanup() -> None:
        hassboxStore.local_uploads.pop(upload_id, None)
        unregister()

    connection.subscriptions[msg["id"]] = _cleanup
    connection.send_result(
        msg["id"], {"uploadId": upload_id, "handlerId": handler_id}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/local_upload/finish",
        vol.Required("uploadId"): str,
        vol.Optional("appType", default=""): str,
        vol.Optional("targetPath", default=""): str,
        vol.Optional("extract", default=False): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def local_upload_finish(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Finish a local file upload and install it."""
    hassboxStore: HassBoxStore = hass.data.get(DOMAIN)
    session = (
        hassboxStore.local_uploads.pop(msg["uploadId"], None)
        if hassboxStore
        else None
    )
    if session is None:
        connection.send_error(msg["id"], "not_found", "上传会话不存在或已结束")
        return

    if session.unregister is not None:
        session.unregister()
    connection.subscriptions.pop(session.start_msg_id, None)

    if session.error is not None:
        connection.send_error(msg["id"], "invalid_format", session.error)
        return

    if len(session.buffer) != session.file_size:
        connection.send_error(
            msg["id"],
            "invalid_format",
            f"文件接收不完整（{len(session.buffer)}/{session.file_size}）",
        )
        return

    if msg.get("targetPath"):
        result = await hassboxStore.async_upload_local_file_to_dir(
            session.file_name,
            bytes(session.buffer),
            msg["targetPath"],
            msg.get("extract", False),
        )
    else:
        result = await hassboxStore.async_install_local_file(
            session.file_name, bytes(session.buffer), msg.get("appType") or None
        )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/list_directories",
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_list_directories(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List directories inside the configuration directory."""

    def _list() -> list[str]:
        config_dir = hass.config.config_dir
        directories: list[str] = []
        for root, dirs, _files in os.walk(config_dir):
            rel = os.path.relpath(root, config_dir)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            dirs[:] = sorted(
                directory
                for directory in dirs
                if not directory.startswith(".")
                and directory not in EXCLUDED_DIRS
            )
            if rel != ".":
                directories.append(rel)
            if depth >= MAX_DIRECTORY_DEPTH:
                dirs[:] = []
        return directories

    directories = await hass.async_add_executor_job(_list)
    connection.send_result(msg["id"], directories)


def _resolve_path(config_dir: str, path: str) -> tuple[str | None, str | None]:
    """Resolve a relative path and ensure it stays inside the config dir."""
    rel = (path or "").strip().strip("/")
    if not rel:
        return config_dir, None
    target = os.path.realpath(os.path.join(config_dir, rel))
    if target != config_dir and not target.startswith(config_dir + os.sep):
        return None, "路径超出配置目录范围"
    return target, None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/list_files",
        vol.Optional("path", default=""): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_list_files(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List files and directories inside a directory of the config dir."""
    config_dir = os.path.realpath(hass.config.config_dir)
    target, error = _resolve_path(config_dir, msg["path"])
    if error is not None:
        connection.send_error(msg["id"], "invalid_path", error)
        return

    def _list() -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not os.path.isdir(target):  # type: ignore[arg-type]
            return entries
        with os.scandir(target) as iterator:
            for entry in iterator:
                if entry.name.startswith("."):
                    continue
                try:
                    is_dir = entry.is_dir()
                    size = 0 if is_dir else entry.stat().st_size
                except OSError:
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "type": "dir" if is_dir else "file",
                        "size": size,
                    }
                )
        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].lower()))
        return entries

    entries = await hass.async_add_executor_job(_list)
    connection.send_result(msg["id"], entries)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/delete_path",
        vol.Required("path"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_delete_path(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete a file or directory inside the config dir."""
    config_dir = os.path.realpath(hass.config.config_dir)
    target, error = _resolve_path(config_dir, msg["path"])
    if error is not None:
        connection.send_error(msg["id"], "invalid_path", error)
        return
    if target == config_dir:
        connection.send_error(msg["id"], "invalid_path", "不能删除配置根目录")
        return

    def _delete() -> str | None:
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        elif os.path.exists(target) or os.path.islink(target):
            os.remove(target)
        else:
            return "目标不存在"
        return None

    error = await hass.async_add_executor_job(_delete)
    if error is not None:
        connection.send_error(msg["id"], "not_found", error)
        return
    connection.send_result(msg["id"], {"errcode": 0})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/create_directory",
        vol.Required("path"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_create_directory(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Create a directory inside the config dir."""
    config_dir = os.path.realpath(hass.config.config_dir)
    target, error = _resolve_path(config_dir, msg["path"])
    if error is not None:
        connection.send_error(msg["id"], "invalid_path", error)
        return
    if target == config_dir:
        connection.send_error(msg["id"], "invalid_path", "路径不能为空")
        return

    def _create() -> str | None:
        if os.path.exists(target):
            return "目录已存在"
        os.makedirs(target)
        return None

    error = await hass.async_add_executor_job(_create)
    if error is not None:
        connection.send_error(msg["id"], "invalid_path", error)
        return
    connection.send_result(msg["id"], {"errcode": 0})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hassbox/store/rename_path",
        vol.Required("path"): str,
        vol.Required("newName"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_rename_path(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Rename a file or directory inside the config dir."""
    config_dir = os.path.realpath(hass.config.config_dir)
    target, error = _resolve_path(config_dir, msg["path"])
    if error is not None:
        connection.send_error(msg["id"], "invalid_path", error)
        return
    if target == config_dir:
        connection.send_error(msg["id"], "invalid_path", "不能重命名配置根目录")
        return

    new_name = msg["newName"].strip()
    if not new_name or new_name in (".", "..") or os.sep in new_name:
        connection.send_error(msg["id"], "invalid_path", "名称不合法")
        return

    def _rename() -> str | None:
        if not os.path.exists(target):
            return "目标不存在"
        destination = os.path.join(os.path.dirname(target), new_name)
        if os.path.exists(destination):
            return "同名文件或目录已存在"
        os.rename(target, destination)
        return None

    error = await hass.async_add_executor_job(_rename)
    if error is not None:
        connection.send_error(msg["id"], "invalid_path", error)
        return
    connection.send_result(msg["id"], {"errcode": 0})
