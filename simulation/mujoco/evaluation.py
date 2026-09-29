"""特权评分与确定性物理回归；此模块不得注册为 Agent 工具。"""

from __future__ import annotations

import threading
from typing import Any, Sequence

import numpy as np

from .backend import MujocoBackend
from .controller import Z1Controller


class Z1PickPlaceEvaluator:
    COLORS = ("red", "blue", "green")
    TABLE_Z = 0.75
    CUBE_HALF = 0.0175

    def __init__(self, backend: MujocoBackend, controller: Z1Controller) -> None:
        self.backend = backend
        self.controller = controller

    def in_container(self, color: str) -> bool:
        body = self.backend.data.body(f"{color}_cube")
        container = self.backend.data.body("container")
        local = body.xpos - container.xpos
        half_extent = np.abs(body.xmat.reshape(3, 3)) @ np.full(3, self.CUBE_HALF)
        return bool(
            abs(local[0]) + half_extent[0] < 0.095
            and abs(local[1]) + half_extent[1] < 0.085
            and 0.012 + half_extent[2] - 0.004
            <= local[2]
            <= 0.074 - half_extent[2] + 0.003
        )

    def privileged_state(self) -> dict[str, Any]:
        return {
            "privileged_simulator_state": True,
            "objects": {
                color: {
                    "position": self.backend.data.body(f"{color}_cube").xpos.tolist(),
                    "quaternion_wxyz": self.backend.data.body(
                        f"{color}_cube"
                    ).xquat.tolist(),
                    "in_container": self.in_container(color),
                }
                for color in self.COLORS
            },
            "container_position": self.backend.data.body("container").xpos.tolist(),
        }

    def scripted_pick_place(
        self,
        color: str,
        *,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        if color not in self.COLORS:
            raise ValueError(f"color 必须是 {self.COLORS} 之一")
        controller, backend = self.controller, self.backend
        controller.set_gripper(1, cancel=cancel)
        original = backend.data.body(f"{color}_cube").xpos.copy()
        above = np.r_[original[:2], 0.88]
        grasp = original + np.asarray([0, 0, 0.004])
        destination = backend.data.body("container").xpos.copy()
        destination[0] += (self.COLORS.index(color) - 1) * 0.045
        destination[2] = 0.88
        rotation = np.asarray(controller.config.default_down_rotation).reshape(3, 3)
        for target in (above, grasp, destination):
            controller.solve_ik(target, rotation)
        snapshot_id = 0
        controller.move_ee(
            frame=backend.config.robot.world_frame,
            position=above,
            quaternion_wxyz=None,
            duration_seconds=1.6,
            snapshot_id=snapshot_id,
            cancel=cancel,
        )
        controller.move_ee(
            frame=backend.config.robot.world_frame,
            position=grasp,
            quaternion_wxyz=None,
            duration_seconds=1.3,
            snapshot_id=snapshot_id,
            cancel=cancel,
        )
        controller.set_gripper(0, cancel=cancel)
        controller.move_ee(
            frame=backend.config.robot.world_frame,
            position=above,
            quaternion_wxyz=None,
            duration_seconds=1.6,
            snapshot_id=snapshot_id,
            cancel=cancel,
        )
        lift_z = float(backend.data.body(f"{color}_cube").xpos[2])
        if lift_z < self.TABLE_Z + 0.065:
            raise RuntimeError(f"{color} 方块未被抬起，z={lift_z:.4f}")
        controller.move_ee(
            frame=backend.config.robot.world_frame,
            position=destination,
            quaternion_wxyz=None,
            duration_seconds=6.0,
            snapshot_id=snapshot_id,
            cancel=cancel,
        )
        carried = backend.data.body(f"{color}_cube").xpos.copy()
        if carried[2] < self.TABLE_Z + 0.08 or np.linalg.norm(
            carried[:2] - destination[:2]
        ) > 0.05:
            raise RuntimeError(f"{color} 方块释放前滑落: {carried.tolist()}")
        controller.set_gripper(1, cancel=cancel)
        controller.hold(0.8, cancel=cancel)
        return {
            "color": color,
            "lift_height_above_table_m": lift_z - self.TABLE_Z,
            "position_before_release": carried.tolist(),
            "in_container": self.in_container(color),
            "final_position": backend.data.body(f"{color}_cube").xpos.tolist(),
        }

    def run_sequence(self, colors: Sequence[str] = COLORS) -> list[dict[str, Any]]:
        return [self.scripted_pick_place(color) for color in colors]
