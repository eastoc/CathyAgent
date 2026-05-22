"""Optional MuJoCo smoke-test adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MujocoSmokeResult:
    ok: bool
    steps: int
    message: str

    def to_dict(self) -> dict:
        return {"ok": self.ok, "steps": self.steps, "message": self.message}


def smoke_test_mjcf(path: str | Path, *, steps: int = 200) -> MujocoSmokeResult:
    """Load an MJCF and step it if mujoco is installed."""
    try:
        import mujoco  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        return MujocoSmokeResult(ok=False, steps=0, message=f"mujoco package is not installed: {exc}")

    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    for _ in range(max(0, int(steps))):
        mujoco.mj_step(model, data)
    return MujocoSmokeResult(ok=True, steps=max(0, int(steps)), message="MJCF loaded and stepped successfully")
