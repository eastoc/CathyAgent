"""Tool 基类与注册表。

Phase 0 极简版：直接挂在内存 dict 中，无 manifest、无热加载。
Phase 1 会替换为 PluginRegistry，但调用方接口（list_tools / call）保持稳定。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolError(RuntimeError):
    """工具执行失败时使用，错误信息会原样返回给模型。"""


class Tool(ABC):
    """所有工具的统一基类。"""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        """执行工具。返回字符串（将作为 tool message 内容回传给模型）。"""

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    """简易工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool.name 不能为空")
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def openai_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def call(self, name: str, args: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"[ToolError] 未知工具: {name}"
        try:
            return tool.execute(**args)
        except ToolError as exc:
            return f"[ToolError:{name}] {exc}"
        except TypeError as exc:
            return f"[ToolError:{name}] 参数不合法: {exc}"
        except Exception as exc:
            return f"[ToolError:{name}] {type(exc).__name__}: {exc}"
