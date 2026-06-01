"""SearchAgent 测试：query 扩写、web_search 调用与结果筛选。"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from subagents.search_agent import SearchAgent  # noqa: E402


def _make_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


class _ScriptedLLM:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> SimpleNamespace:
        self.calls.append(messages)
        if not self.outputs:
            raise AssertionError("LLM 调用次数超出脚本")
        return _make_response(self.outputs.pop(0))


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, tool_name: str, params: dict[str, Any]) -> str:
        self.calls.append({"tool": tool_name, "params": dict(params)})
        query = params["query"]
        if "max_completion_tokens" in query:
            return (
                "1. OpenAI Chat Completions 参数说明\n"
                "   链接: https://platform.openai.com/docs/api-reference/chat/create\n"
                "   摘要: max_tokens 已弃用，部分模型应使用 max_completion_tokens。"
            )
        return (
            "1. 无关网页\n"
            "   链接: https://example.com/unrelated\n"
            "   摘要: 这是一条无关摘要。"
        )


class _SlowTools:
    def __init__(self, delay: float = 0.2) -> None:
        self.delay = delay

    def call(self, tool_name: str, params: dict[str, Any]) -> str:
        time.sleep(self.delay)
        query = params["query"]
        return (
            f"1. {query}\n"
            f"   链接: https://example.com/{query}\n"
            f"   摘要: {query} summary"
        )


class SearchAgentTest(unittest.TestCase):
    def test_expands_queries_searches_and_filters_results(self) -> None:
        llm = _ScriptedLLM(
            [
                '{"queries":["OpenAI max_completion_tokens unsupported max_tokens","unrelated query"]}',
                '{"selected":[1]}',
            ]
        )
        tools = _FakeTools()
        agent = SearchAgent(llm=llm, tools=tools)

        result = agent.run(
            {
                "question": "OpenAI 报 max_tokens unsupported 怎么办？",
                "max_queries": 2,
                "max_results_per_query": 3,
            }
        )

        self.assertTrue(result.finished)
        self.assertEqual([c["tool"] for c in tools.calls], ["web_search", "web_search"])
        self.assertEqual(
            [c["params"]["query"] for c in tools.calls],
            ["OpenAI max_completion_tokens unsupported max_tokens", "unrelated query"],
        )
        self.assertIn("https://platform.openai.com/docs/api-reference/chat/create", result.final_answer)
        self.assertNotIn("https://example.com/unrelated", result.final_answer)

    def test_search_queries_run_concurrently(self) -> None:
        llm = _ScriptedLLM(["{}"])
        agent = SearchAgent(llm=llm, tools=_SlowTools(delay=0.2))

        start = time.perf_counter()
        out = agent._search_queries_concurrently(["a", "b", "c"], max_results=1)
        elapsed = time.perf_counter() - start

        self.assertEqual([query for query, _raw in out], ["a", "b", "c"])
        self.assertLess(elapsed, 0.45)


if __name__ == "__main__":
    unittest.main()
