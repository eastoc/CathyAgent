"""CathyAgent 插件子系统。

公开 API：
    ToolPlugin   -- 所有插件的统一接口
    PluginError  -- 插件相关异常基类
    PluginRegistry -- 插件发现/校验/加载/调度
"""

from .base import PluginError, ToolPlugin
from .manifest import Execution, PluginManifest, ToolSpec
from .registry import PluginRegistry, ToolDescriptor, ToolView

__all__ = [
    "Execution",
    "PluginError",
    "PluginManifest",
    "PluginRegistry",
    "ToolDescriptor",
    "ToolPlugin",
    "ToolSpec",
    "ToolView",
]
