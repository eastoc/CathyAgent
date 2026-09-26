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
from .run import EventSink, RunRecord
from .tool import (
    ToolExecutionPolicy,
    ToolInvocation,
    ToolResult,
    ToolTaskRecord,
    tool_result_from_dict,
    tool_result_to_dict,
)

__all__ = [
    "AgentEvent",
    "AgentRequest",
    "AgentResult",
    "AttachmentRef",
    "AttachmentResolver",
    "ContentBlock",
    "EventSink",
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
    "RunRecord",
    "TextBlock",
    "ToolExecutionPolicy",
    "ToolInvocation",
    "ToolResult",
    "ToolTaskRecord",
    "tool_result_from_dict",
    "tool_result_to_dict",
]
