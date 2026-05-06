"""get_current_datetime：返回当前系统时间。

当模型被问及"今天几号 / 现在几点 / 今天星期几"，或需要做时间计算时调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .base import Tool, ToolError

_WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class CurrentDatetimeTool(Tool):
    name = "get_current_datetime"
    description = (
        "获取当前系统时间和日期。当用户询问 '今天几号' / '现在几点' / "
        "'今天星期几'，或需要做时间相关计算（如 N 天后、N 小时前等）时调用。"
        "返回 ISO 时间、可读日期、星期与时区。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "可选 IANA 时区名，例如 'Asia/Shanghai'、'UTC'、"
                    "'America/New_York'。不填则使用本机本地时区。"
                ),
            },
        },
        "required": [],
    }

    def execute(self, timezone: str | None = None, **_: Any) -> str:
        if timezone:
            try:
                tz = ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise ToolError(f"未知时区: {timezone}") from exc
            now = datetime.now(tz)
        else:
            now = datetime.now().astimezone()

        weekday = _WEEKDAY_CN[now.weekday()]
        iso = now.isoformat(timespec="seconds")
        readable = now.strftime("%Y-%m-%d %H:%M:%S")
        tz_name = now.tzname() or str(now.tzinfo)

        return (
            f"ISO: {iso}\n"
            f"日期时间: {readable}\n"
            f"星期: {weekday}\n"
            f"时区: {tz_name}"
        )
