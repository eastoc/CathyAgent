"""CathyAgent 内部稳定接口契约。"""

from .agent import AgentEvent, AgentRequest, AgentResult, RunContext
from .content import (
    AttachmentRef,
    AttachmentResolver,
    ContentBlock,
    FileBlock,
    ImageBlock,
    JsonBlock,
    TextBlock,
)
from .model import (
    AsyncModelClient,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    ModelTurnState,
)

__all__ = [
    "AgentEvent",
    "AgentRequest",
    "AgentResult",
    "AttachmentRef",
    "AttachmentResolver",
    "ContentBlock",
    "FileBlock",
    "ImageBlock",
    "JsonBlock",
    "AsyncModelClient",
    "ModelClient",
    "ModelEvent",
    "ModelRequest",
    "ModelResponse",
    "ModelTool",
    "ModelToolCall",
    "ModelTurnState",
    "RunContext",
    "TextBlock",
]
