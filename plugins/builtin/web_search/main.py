"""web_search 插件实现：调用 Tavily API。"""

from __future__ import annotations

from typing import Any

from cathy.plugins import PluginError, ToolPlugin


class WebSearchPlugin(ToolPlugin):
    def __init__(self) -> None:
        self._client: Any = None

    def initialize(self, config: dict[str, Any]) -> None:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            raise PluginError("web_search 缺少 api_key（在 config.yaml 中配置 TAVILY_API_KEY）")

        from tavily import TavilyClient

        self._client = TavilyClient(api_key=api_key)

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "web_search":
            raise PluginError(f"未知工具: {tool_name}")

        query = (params.get("query") or "").strip()
        if not query:
            raise PluginError("query 不能为空")
        max_results = max(1, min(int(params.get("max_results", 5)), 10))

        try:
            resp = self._client.search(query=query, max_results=max_results)
        except Exception as exc:
            raise PluginError(f"Tavily 调用失败: {exc}") from exc

        items = resp.get("results") or []
        if not items:
            return "未找到相关结果。"

        lines: list[str] = []
        for i, item in enumerate(items, 1):
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            content = (item.get("content") or "").strip()[:500]
            lines.append(f"{i}. {title}\n   链接: {url}\n   摘要: {content}")
        return "\n\n".join(lines)
