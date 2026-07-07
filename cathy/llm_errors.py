"""LLM failure classification and retry metadata.

The framework keeps provider-specific exceptions out of agent/subagent code by
normalizing them into a small, serializable failure object.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

try:  # pragma: no cover - import shape depends on installed openai version
    from openai import (
        APIConnectionError,
        APIError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        PermissionDeniedError,
        RateLimitError,
    )
except Exception:  # pragma: no cover
    APIConnectionError = APIError = APITimeoutError = Exception
    AuthenticationError = BadRequestError = Exception
    InternalServerError = PermissionDeniedError = RateLimitError = Exception


FailureReason = Literal[
    "timeout",
    "rate_limit",
    "overload",
    "server_error",
    "connection_error",
    "auth_error",
    "permission_error",
    "bad_request",
    "context_too_long",
    "unknown",
]


@dataclass(frozen=True)
class AgentFailure:
    """Structured failure information that can be returned to agents."""

    stage: str
    error_type: str
    reason: FailureReason
    retryable: bool
    message: str
    status_code: int | None = None
    request_id: str | None = None
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LLMCallError(RuntimeError):
    """Raised when an LLM call fails after classification/retries."""

    def __init__(self, failure: AgentFailure) -> None:
        super().__init__(
            f"{failure.stage} failed after {failure.attempts} attempt(s): "
            f"{failure.error_type}: {failure.message}"
        )
        self.failure = failure


def classify_llm_exception(
    exc: BaseException,
    *,
    stage: str,
    attempts: int = 1,
) -> AgentFailure:
    """Map provider exceptions into a stable CathyAgent failure schema."""

    error_type = type(exc).__name__
    message = str(exc)
    status_code = _status_code(exc)
    request_id = _request_id(exc)

    reason: FailureReason = "unknown"
    retryable = False
    lowered = message.lower()

    if isinstance(exc, APITimeoutError):
        reason = "timeout"
        retryable = True
    elif isinstance(exc, RateLimitError):
        reason = "rate_limit"
        retryable = True
    elif isinstance(exc, APIConnectionError):
        reason = "connection_error"
        retryable = True
    elif isinstance(exc, AuthenticationError):
        reason = "auth_error"
        retryable = False
    elif isinstance(exc, PermissionDeniedError):
        reason = "permission_error"
        retryable = False
    elif isinstance(exc, BadRequestError):
        reason = "context_too_long" if _looks_like_context_error(lowered) else "bad_request"
        retryable = False
    elif isinstance(exc, InternalServerError) or (status_code is not None and status_code >= 500):
        reason = "overload" if _looks_like_overload(lowered) else "server_error"
        retryable = True
    elif isinstance(exc, APIError):
        if status_code in {408, 409, 429}:
            reason = "rate_limit" if status_code == 429 else "server_error"
            retryable = True
        elif status_code is not None and status_code >= 500:
            reason = "server_error"
            retryable = True

    return AgentFailure(
        stage=stage,
        error_type=error_type,
        reason=reason,
        retryable=retryable,
        message=message,
        status_code=status_code,
        request_id=request_id,
        attempts=max(1, int(attempts)),
    )


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _request_id(exc: BaseException) -> str | None:
    value = getattr(exc, "request_id", None)
    if value:
        return str(value)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if isinstance(headers, dict):
        for key in ("x-request-id", "request-id"):
            if headers.get(key):
                return str(headers[key])
    return None


def _looks_like_context_error(message: str) -> bool:
    return any(
        token in message
        for token in (
            "context length",
            "context_length",
            "maximum context",
            "too many tokens",
            "token limit",
        )
    )


def _looks_like_overload(message: str) -> bool:
    return any(token in message for token in ("overload", "capacity", "temporarily unavailable"))
