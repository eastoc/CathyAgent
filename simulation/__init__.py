"""CathyAgent 的可选仿真扩展。核心 Agent 不依赖具体仿真器。"""

from .contracts import ExecutionResult, Pose, SimulationRuntime

__all__ = ["ExecutionResult", "Pose", "SimulationRuntime"]
