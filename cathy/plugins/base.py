"""ToolPlugin 接口与异常基类。

对齐 ARCHITECTURE.md §7.5。Phase 1 仅实现同步版本，async 后置。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PluginError(RuntimeError):
    """插件相关异常。execute 抛出后会被 Registry 捕获并以结构化错误回传给模型。"""


class ToolPlugin(ABC):
    """所有插件的统一接口。一个插件可提供多个 tool（按 manifest.tools 列表）。"""

    @abstractmethod
    def initialize(self, config: dict[str, Any]) -> None:
        """加载时调用一次。config 是 PluginRegistry 注入的插件级配置字典。"""

    @abstractmethod
    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        """执行工具。

        Args:
            tool_name: manifest.tools[].name 之一。
            params: 已通过 input_schema 校验的参数。
        Returns:
            工具结果字符串（将作为 role=tool 消息回传给模型）。
        Raises:
            PluginError: 工具内可控的失败（参数业务错误等）。
            其它异常会被 Registry 兜底转成结构化错误。
        """

    def health_check(self) -> bool:
        """Registry 可定期探活；默认返回 True。"""
        return True

    def shutdown(self) -> None:
        """卸载时调用，子类按需释放资源。"""
