"""current_datetime 插件实现。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cathy.plugins import PluginError, ToolPlugin

_WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class CurrentDatetimePlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        # 该插件无需任何配置
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "get_current_datetime":
            raise PluginError(f"未知工具: {tool_name}")

        tz_name = params.get("timezone")
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError as exc:
                raise PluginError(f"未知时区: {tz_name}") from exc
            now = datetime.now(tz)
        else:
            now = datetime.now().astimezone()

        weekday = _WEEKDAY_CN[now.weekday()]
        iso = now.isoformat(timespec="seconds")
        readable = now.strftime("%Y-%m-%d %H:%M:%S")
        tz_label = now.tzname() or str(now.tzinfo)

        return (
            f"ISO: {iso}\n"
            f"日期时间: {readable}\n"
            f"星期: {weekday}\n"
            f"时区: {tz_label}"
        )
