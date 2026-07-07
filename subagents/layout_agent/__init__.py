"""LayoutAgent 子 agent。"""

from .schema import LayoutAgentResult, LayoutDecision

__all__ = ["LayoutAgent", "LayoutAgentResult", "LayoutDecision"]


def __getattr__(name: str):
    if name == "LayoutAgent":
        from .agent import LayoutAgent

        return LayoutAgent
    raise AttributeError(name)
