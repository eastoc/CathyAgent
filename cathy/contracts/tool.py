"""供应商无关的工具执行契约。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping, Sequence

from .content import (
    ContentBlock,
    coerce_content_blocks,
    content_blocks_to_text,
    deserialize_content_blocks,
    serialize_content_blocks,
)


ToolStatus = Literal["succeeded", "failed", "cancelled", "timed_out"]
ToolExecutionMode = Literal["inline", "background"]
ToolSideEffect = Literal["none", "reversible", "irreversible"]
ToolTaskStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "orphaned",
]


@dataclass(frozen=True)
class ToolExecutionPolicy:
    """Harness 调度工具时使用的执行约束。"""

    mode: ToolExecutionMode = "inline"
    timeout_seconds: float = 30.0
    cancellable: bool = True
    side_effect: ToolSideEffect = "none"
    concurrency_key: str | None = None
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"inline", "background"}:
            raise ValueError("tool mode 必须是 inline/background")
        if self.timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds 必须大于 0")
        if self.side_effect not in {"none", "reversible", "irreversible"}:
            raise ValueError(
                "tool side_effect 必须是 none/reversible/irreversible"
            )
        if self.concurrency_key is not None and not self.concurrency_key.strip():
            raise ValueError("tool concurrency_key 不能为空字符串")
        if self.max_concurrency < 1:
            raise ValueError("tool max_concurrency 必须大于等于 1")
        if self.mode == "background" and self.side_effect == "irreversible":
            raise ValueError("不可逆工具不能配置为 background")


@dataclass(frozen=True)
class ToolResult:
    """类型化工具结果；旧字符串插件会在 Registry 边界被自动包装。"""

    call_id: str
    tool_name: str
    status: ToolStatus
    content: Sequence[ContentBlock]
    artifacts: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ValueError(f"未知 ToolResult.status: {self.status}")
        metadata = dict(self.metadata)
        try:
            json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("ToolResult.metadata 必须可 JSON 序列化") from exc
        object.__setattr__(self, "content", coerce_content_blocks(self.content))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "metadata", metadata)

    @property
    def text(self) -> str:
        """面向旧模型消息和 Hook 的文本投影。"""

        return content_blocks_to_text(self.content)

    def with_identity(self, *, call_id: str, tool_name: str) -> "ToolResult":
        """由 Harness 写入可信调用标识，忽略插件自行声明的标识。"""

        return replace(self, call_id=call_id, tool_name=tool_name)

    def with_metadata(self, **values: Any) -> "ToolResult":
        return replace(self, metadata={**dict(self.metadata), **values})

    @classmethod
    def succeeded(
        cls,
        *,
        call_id: str,
        tool_name: str,
        content: str | Sequence[ContentBlock],
        artifacts: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            status="succeeded",
            content=coerce_content_blocks(content),
            artifacts=tuple(artifacts),
            metadata=metadata or {},
        )

    @classmethod
    def failed(
        cls,
        *,
        call_id: str,
        tool_name: str,
        message: str,
        error_code: str = "tool_error",
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        rendered = f"[ToolError:{tool_name}] {message}"
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            status="failed",
            content=coerce_content_blocks(rendered),
            metadata=metadata or {},
            error_code=error_code,
            error_message=message,
        )

    @classmethod
    def timed_out(
        cls,
        *,
        call_id: str,
        tool_name: str,
        timeout_seconds: float,
    ) -> "ToolResult":
        message = f"执行超时（{timeout_seconds:g}s）"
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            status="timed_out",
            content=coerce_content_blocks(f"[ToolError:{tool_name}][TIMEOUT] {message}"),
            metadata={"timeout_seconds": timeout_seconds},
            error_code="timeout",
            error_message=message,
        )

    @classmethod
    def cancelled(
        cls,
        *,
        call_id: str,
        tool_name: str,
        message: str = "任务已取消",
    ) -> "ToolResult":
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            status="cancelled",
            content=coerce_content_blocks(
                f"[ToolError:{tool_name}][CANCELLED] {message}"
            ),
            error_code="cancelled",
            error_message=message,
        )


@dataclass(frozen=True)
class ToolInvocation:
    """ToolScheduler 的最小输入，不依赖任何模型供应商。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True)
class ToolTaskRecord:
    """可持久化的后台工具任务；不保存进程内 asyncio.Task。"""

    task_id: str
    run_id: str
    session_id: str
    invocation: ToolInvocation
    status: ToolTaskStatus = "queued"
    provider: str | None = None
    provider_call_id: str | None = None
    provider_response_id: str | None = None
    latest_response_id: str | None = None
    result: ToolResult | None = None
    error_message: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("ToolTaskRecord.task_id 不能为空")
        if not self.run_id:
            raise ValueError("ToolTaskRecord.run_id 不能为空")
        if not self.session_id:
            raise ValueError("ToolTaskRecord.session_id 不能为空")
        if self.status not in {
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "orphaned",
        }:
            raise ValueError(f"未知 ToolTaskRecord.status: {self.status}")
        metadata = dict(self.metadata)
        try:
            json.dumps(metadata, ensure_ascii=False)
            json.dumps(dict(self.invocation.arguments), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("ToolTaskRecord 参数和 metadata 必须可 JSON 序列化") from exc
        object.__setattr__(self, "metadata", metadata)

    def with_updates(self, **values: Any) -> "ToolTaskRecord":
        values.setdefault("updated_at", time.time())
        return replace(self, **values)

    @classmethod
    def queued(
        cls,
        *,
        run_id: str,
        session_id: str,
        invocation: ToolInvocation,
        provider: str | None = None,
        provider_call_id: str | None = None,
        provider_response_id: str | None = None,
        latest_response_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> "ToolTaskRecord":
        return cls(
            task_id=task_id or f"task_{uuid.uuid4().hex}",
            run_id=run_id,
            session_id=session_id,
            invocation=invocation,
            provider=provider,
            provider_call_id=provider_call_id or invocation.call_id,
            provider_response_id=provider_response_id,
            latest_response_id=latest_response_id or provider_response_id,
            metadata=metadata or {},
        )


def tool_result_to_dict(result: ToolResult) -> dict[str, Any]:
    return {
        "call_id": result.call_id,
        "tool_name": result.tool_name,
        "status": result.status,
        "content": serialize_content_blocks(result.content),
        "artifacts": list(result.artifacts),
        "metadata": dict(result.metadata),
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


def tool_result_from_dict(value: Mapping[str, Any]) -> ToolResult:
    raw_content = value.get("content") or []
    if not isinstance(raw_content, Sequence) or isinstance(raw_content, (str, bytes)):
        raise ValueError("ToolResult.content 必须是内容块数组")
    return ToolResult(
        call_id=str(value.get("call_id") or ""),
        tool_name=str(value.get("tool_name") or ""),
        status=str(value.get("status") or "failed"),  # type: ignore[arg-type]
        content=deserialize_content_blocks(raw_content),
        artifacts=tuple(str(item) for item in value.get("artifacts") or []),
        metadata=dict(value.get("metadata") or {}),
        error_code=(
            str(value["error_code"])
            if value.get("error_code") is not None
            else None
        ),
        error_message=(
            str(value["error_message"])
            if value.get("error_message") is not None
            else None
        ),
    )
