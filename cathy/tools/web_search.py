"""web_search：基于 Tavily 的联网搜索工具。"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolError


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "在互联网上搜索最新信息。当用户问题涉及时效性、事实核对、新闻、"
        "你不知道或不确定的内容时调用。返回若干条带标题、链接、摘要的结果。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或完整问句，中英文均可",
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果条数，默认 5",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TAVILY_API_KEY 未配置")
        from tavily import TavilyClient

        self._client = TavilyClient(api_key=api_key)

    def execute(self, query: str, max_results: int = 5, **_: Any) -> str:
        query = (query or "").strip()
        if not query:
            raise ToolError("query 不能为空")

        max_results = max(1, min(int(max_results), 10))
        try:
            resp = self._client.search(query=query, max_results=max_results)
        except Exception as exc:
            raise ToolError(f"Tavily 调用失败: {exc}") from exc

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
