"""SearchAgent：把 web_search 封装成可自主扩写 query 的搜索子 agent。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from cathy.llm_errors import AgentFailure, LLMCallError
from cathy.subagent import Subagent, SubagentResult


class SearchState(TypedDict, total=False):
    question: str
    max_queries: int
    max_results_per_query: int
    queries: list[str]
    candidates: list[dict[str, str]]
    selected: list[int]
    errors: list[str]
    final_answer: str
    finished: bool
    trace: list[dict[str, Any]]


def _trace(state: SearchState, step_type: str, payload: dict[str, Any]) -> None:
    state.setdefault("trace", []).append({"type": step_type, **payload})


def _llm_text(llm: Any, system: str, user: str) -> str:
    response = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return (response.choices[0].message.content or "").strip()


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _dedupe(items: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = " ".join(str(item or "").split())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _clip(text: str, limit: int = 500) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def _parse_web_search_results(raw: str, *, query: str) -> list[dict[str, str]]:
    if not raw or raw.startswith("[ToolError"):
        return []

    results: list[dict[str, str]] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        title = re.sub(r"^\d+[.)]\s*", "", lines[0]).strip()
        url = ""
        summary_parts: list[str] = []
        for line in lines[1:]:
            if line.startswith("链接:"):
                url = line.split(":", 1)[1].strip()
            elif line.startswith("摘要:"):
                summary_parts.append(line.split(":", 1)[1].strip())
            else:
                summary_parts.append(line)
        if title or url:
            results.append(
                {
                    "query": query,
                    "title": title or "(无标题)",
                    "url": url,
                    "summary": _clip(" ".join(summary_parts), 700),
                }
            )
    return results


class SearchAgent(Subagent):
    """面向父 agent 的搜索子 agent。"""

    name = "search_agent"
    description = (
        "搜索子 agent。用于需要联网搜索、事实核对、新闻/文档查询的问题。"
        "它会先根据用户问题自主扩写多个 query，批量搜索，"
        "再根据网页摘要与问题的相关性筛选出应返回的网页。"
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["question"],
        "properties": {
            "question": {
                "type": "string",
                "minLength": 1,
                "description": "需要搜索或事实核对的完整问题。请包含时间范围、地域、语言、引用要求等背景。",
            },
            "max_queries": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "最多扩写出的搜索 query 数，默认 3。",
            },
            "max_results_per_query": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "每个 query 返回的搜索结果数，默认 5。",
            },
        },
    }

    def __init__(self, *, llm: Any, tools: Any) -> None:
        self.llm = llm
        self.tools = tools
        self._graph = self._build_graph()

    def run(self, params: dict[str, Any]) -> SubagentResult:
        question = str(params.get("question") or "").strip()
        if not question:
            return SubagentResult(final_answer="[search_agent] question 不能为空", finished=False)

        max_queries = max(1, min(int(params.get("max_queries") or 4), 6))
        max_results = max(1, min(int(params.get("max_results_per_query") or 5), 10))
        try:
            state = self._graph.invoke(
                {
                    "question": question,
                    "max_queries": max_queries,
                    "max_results_per_query": max_results,
                    "trace": [],
                }
            )
        except Exception as exc:
            failure = _subagent_failure(exc, stage="search_agent")
            return SubagentResult(
                final_answer=f"[search_agent] 图执行失败: {type(exc).__name__}: {exc}",
                finished=False,
                status="failed",
                failure=failure,
            )

        return SubagentResult(
            final_answer=str(state.get("final_answer") or ""),
            finished=bool(state.get("finished", True)),
            trace=list(state.get("trace") or []),
        )

    def _build_graph(self):
        graph = StateGraph(SearchState)
        graph.add_node("expand_queries", self._node_expand_queries)
        graph.add_node("search_queries", self._node_search_queries)
        graph.add_node("select_relevant", self._node_select_relevant)
        graph.add_node("format_answer", self._node_format_answer)
        graph.set_entry_point("expand_queries")
        graph.add_edge("expand_queries", "search_queries")
        graph.add_edge("search_queries", "select_relevant")
        graph.add_edge("select_relevant", "format_answer")
        graph.add_edge("format_answer", END)
        return graph.compile()

    def _node_expand_queries(self, state: SearchState) -> SearchState:
        question = state["question"]
        queries = self._expand_queries(question, max_queries=int(state["max_queries"]))
        state["queries"] = queries
        _trace(state, "query_expansion", {"question": question, "queries": queries})
        return state

    def _node_search_queries(self, state: SearchState) -> SearchState:
        candidates: list[dict[str, str]] = []
        errors: list[str] = []
        max_results = int(state["max_results_per_query"])
        queries = state.get("queries") or [state["question"]]
        for query, raw in self._search_queries_concurrently(queries, max_results=max_results):
            _trace(state, "web_search", {"query": query, "result": raw})
            if raw.startswith("[ToolError"):
                errors.append(f"- `{query}`: {raw}")
                continue
            candidates.extend(_parse_web_search_results(raw, query=query))
        state["candidates"] = candidates
        state["errors"] = errors
        return state

    def _search_queries_concurrently(self, queries: list[str], *, max_results: int) -> list[tuple[str, str]]:
        return asyncio.run(self._search_queries_async(queries, max_results=max_results))

    async def _search_queries_async(self, queries: list[str], *, max_results: int) -> list[tuple[str, str]]:
        async def run_one(query: str) -> tuple[str, str]:
            try:
                raw = await asyncio.to_thread(
                    self.tools.call,
                    "web_search",
                    {"query": query, "max_results": max_results},
                )
                return query, str(raw)
            except Exception as exc:
                return query, f"[ToolError:web_search] {type(exc).__name__}: {exc}"

        return await asyncio.gather(*(run_one(query) for query in queries))

    def _node_select_relevant(self, state: SearchState) -> SearchState:
        candidates = state.get("candidates") or []
        if not candidates:
            state["selected"] = []
            _trace(state, "select_results", {"selected": []})
            return state
        selected = self._select_relevant(state["question"], candidates)
        state["selected"] = selected
        _trace(state, "select_results", {"selected": selected})
        return state

    def _node_format_answer(self, state: SearchState) -> SearchState:
        candidates = state.get("candidates") or []
        if not candidates:
            detail = "\n".join(state.get("errors") or []) or "未找到相关结果。"
            state["final_answer"] = f"[search_agent] 未找到可返回网页。\n{detail}"
            state["finished"] = False
            return state
        state["final_answer"] = self._format_answer(
            state["question"],
            state.get("queries") or [state["question"]],
            candidates,
            state.get("selected") or [],
        )
        state["finished"] = True
        return state

    def _expand_queries(self, question: str, *, max_queries: int) -> list[str]:
        system = """\
你是搜索 query 规划器。请把用户问题扩写成一组互补的搜索 query。
要求：
- 覆盖同义词、关键实体、时间范围、官方来源/文档来源等角度。
- 不要编造不存在的实体。
- 只输出 JSON 对象：{"queries": ["...", "..."]}。
"""
        user = f"用户问题：{question}\n最多输出 {max_queries} 个 query。"
        try:
            obj = _parse_json_object(_llm_text(self.llm, system, user)) or {}
            raw_queries = obj.get("queries") if isinstance(obj.get("queries"), list) else []
            queries = _dedupe([str(q) for q in raw_queries], limit=max_queries)
        except Exception:
            queries = []
        return queries or [question]

    def _select_relevant(self, question: str, candidates: list[dict[str, str]]) -> list[int]:
        lines = []
        for i, item in enumerate(candidates, 1):
            lines.append(
                f"{i}. 标题: {item['title']}\n"
                f"   链接: {item['url']}\n"
                f"   来源 query: {item['query']}\n"
                f"   摘要: {item['summary']}"
            )
        system = """\
你是搜索结果筛选器。请只根据标题和摘要判断网页是否能帮助回答用户问题。
只返回 JSON 对象：{"selected": [1, 2, 3]}。
如果都不相关，返回 {"selected": []}。
"""
        user = f"用户问题：{question}\n\n候选网页：\n" + "\n\n".join(lines[:30])
        try:
            obj = _parse_json_object(_llm_text(self.llm, system, user)) or {}
            raw = obj.get("selected") if isinstance(obj.get("selected"), list) else []
            selected = []
            for item in raw:
                try:
                    idx = int(item)
                except (TypeError, ValueError):
                    continue
                if 1 <= idx <= len(candidates) and idx not in selected:
                    selected.append(idx)
            return selected[:10]
        except Exception:
            return list(range(1, min(len(candidates), 5) + 1))

    def _format_answer(
        self,
        question: str,
        queries: list[str],
        candidates: list[dict[str, str]],
        selected: list[int],
    ) -> str:
        if not selected:
            return (
                f"[search_agent] 已搜索但没有筛出与问题高度相关的网页。\n\n"
                f"问题：{question}\n"
                f"扩写 query：{', '.join(f'`{q}`' for q in queries)}"
            )

        lines = [
            "[search_agent] 已完成 query 扩写、搜索与相关性筛选。",
            "",
            f"问题：{question}",
            "",
            "扩写 query：",
        ]
        lines.extend(f"- `{q}`" for q in queries)
        lines.extend(["", "相关网页："])
        for rank, idx in enumerate(selected, 1):
            item = candidates[idx - 1]
            lines.append(
                f"{rank}. {item['title']}\n"
                f"   链接: {item['url']}\n"
                f"   命中 query: `{item['query']}`\n"
                f"   摘要: {item['summary']}"
            )
        return "\n".join(lines)


def _subagent_failure(exc: Exception, *, stage: str) -> AgentFailure:
    if isinstance(exc, LLMCallError):
        return exc.failure
    return AgentFailure(
        stage=stage,
        error_type=type(exc).__name__,
        reason="unknown",
        retryable=False,
        message=str(exc),
    )
