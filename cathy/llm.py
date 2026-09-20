"""旧 LLM 导入路径的兼容层。

新的调用方应从 ``cathy.model_clients`` 使用按线协议命名的客户端；保留本模块
避免现有 Agent、插件和第三方扩展因导入路径变化而中断。
"""

from .model_clients.openai_compatible_chat import (
    OpenAICompatibleChatClient,
    apply_temperature,
    apply_token_limit,
    normalize_chat_response as _normalize_chat_response,
    supports_configurable_temperature,
    uses_max_completion_tokens,
)

LLMClient = OpenAICompatibleChatClient

__all__ = [
    "LLMClient",
    "OpenAICompatibleChatClient",
    "_normalize_chat_response",
    "apply_temperature",
    "apply_token_limit",
    "supports_configurable_temperature",
    "uses_max_completion_tokens",
]
