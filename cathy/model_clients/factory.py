"""从供应商配置构建模型客户端。"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import ModelClient
from .openai_compatible_chat import OpenAICompatibleChatClient
from .openai_responses import OpenAIResponsesClient

_OPENAI_COMPATIBLE_CHAT_TYPES = frozenset(
    {"openai_compatible", "openai_compatible_chat", "chat_completions"}
)
_OPENAI_RESPONSES_TYPES = frozenset({"openai_responses", "responses"})


def build_model_client(config: Mapping[str, Any]) -> ModelClient:
    """按 ``client_type`` / 旧 ``type`` 创建模型客户端。

    ``openai_compatible`` 是已有配置类型，保留为兼容别名；新配置应使用
    ``openai_compatible_chat``，以明确其线协议而非模型品牌。
    """
    client_type = str(
        config.get("client_type") or config.get("type") or "openai_compatible_chat"
    ).strip().lower()
    if client_type not in _OPENAI_COMPATIBLE_CHAT_TYPES | _OPENAI_RESPONSES_TYPES:
        raise ValueError(
            f"暂不支持的模型客户端类型 {client_type!r}；"
            "当前支持: openai_compatible_chat, openai_responses"
        )

    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("api_base") or config.get("base_url") or "")
    model = str(config.get("model") or "")
    if not base_url:
        raise ValueError("LLM api_base 未配置")
    if not model:
        raise ValueError("LLM model 未配置")

    if client_type in _OPENAI_RESPONSES_TYPES:
        max_output_tokens = config.get("max_output_tokens", 8192)
        return OpenAIResponsesClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            reasoning_effort=str(config.get("reasoning_effort") or "low"),
            max_output_tokens=(
                int(max_output_tokens) if max_output_tokens is not None else None
            ),
            timeout=float(config.get("timeout_sec") or 300),
            max_retries=int(config.get("max_retries") or 2),
            retry_backoff_initial_sec=float(
                config.get("retry_backoff_initial_sec") or 1
            ),
            retry_backoff_max_sec=float(config.get("retry_backoff_max_sec") or 20),
            store=bool(config.get("store", True)),
            parallel_tool_calls=bool(config.get("parallel_tool_calls", True)),
        )

    return OpenAICompatibleChatClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=float(config.get("temperature", 0.7)),
        max_tokens=int(config.get("max_tokens") or 4096),
        timeout=float(config.get("timeout_sec") or 60),
        max_retries=int(config.get("max_retries") or 2),
        retry_backoff_initial_sec=float(config.get("retry_backoff_initial_sec") or 1),
        retry_backoff_max_sec=float(config.get("retry_backoff_max_sec") or 20),
        extra_body=_mapping_or_none(config.get("extra_body")),
    )


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("LLM extra_body 必须是 YAML 映射")
    return value
