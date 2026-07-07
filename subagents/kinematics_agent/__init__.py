"""KinematicsAgent 子 agent。"""

from .schema import KinematicsAgentResult, KinematicsDecision

__all__ = ["KinematicsAgent", "KinematicsAgentResult", "KinematicsDecision"]


def __getattr__(name: str):
    if name == "KinematicsAgent":
        from .agent import KinematicsAgent

        return KinematicsAgent
    raise AttributeError(name)
