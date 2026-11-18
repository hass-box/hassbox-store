"""JSON utils."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["json_loads"]


def json_loads(data: str | bytes) -> Any:
    """解析 JSON。

    旧实现直接复用 ``homeassistant.helpers.json.json_loads``，
    但该函数并非所有 HA 版本都提供（会抛 ImportError 导致集成加载失败），
    这里改用标准库实现，行为一致。
    """
    return json.loads(data)
