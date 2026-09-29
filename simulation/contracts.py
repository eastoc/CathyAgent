"""仿真器无关的最小运行时契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Pose:
    frame: str
    position: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reached: bool
    sim_time: float
    snapshot_id: int
    actual_pose: Pose | None = None
    position_error_m: float | None = None
    orientation_error_rad: float | None = None
    error_code: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["actual_pose"] = (
            self.actual_pose.to_dict() if self.actual_pose is not None else None
        )
        return payload


class SimulationRuntime(Protocol):
    async def describe(self) -> Mapping[str, Any]: ...

    async def observe(
        self,
        *,
        cameras: Sequence[str] | None = None,
        render: bool = True,
    ) -> Any: ...

    async def move_ee(
        self,
        *,
        frame: str,
        position: Sequence[float],
        quaternion_wxyz: Sequence[float] | None = None,
        duration_seconds: float = 1.5,
    ) -> ExecutionResult: ...

    async def set_gripper(self, *, opening: float) -> ExecutionResult: ...

    async def wait(self, *, sim_seconds: float) -> ExecutionResult: ...

    async def reset(self) -> Mapping[str, Any]: ...

    def close(self) -> None: ...
