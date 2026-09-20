"""模型客户端实现与构建入口。"""

from .factory import build_model_client
from .openai_compatible_chat import OpenAICompatibleChatClient

__all__ = ["OpenAICompatibleChatClient", "build_model_client"]
