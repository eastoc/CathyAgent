"""Agent 可见的 Z1 高层工具；闭环留在一次工具调用内部。"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Mapping

import numpy as np

from ..controller import _matrix_to_quaternion
from .client import WMAClient
from .config import UnitreeZ1ToolsConfig


CANONICAL_PICK_OBJECT = "red cup"
CANONICAL_PICK_INSTRUCTION = "Pick up the red cup on the table."


class UnitreeZ1Tools:
    TOOL_NAMES = frozenset({"robot_pick", "robot_release", "robot_move_to"})

    def __init__(
        self,
        runtime: Any,
        config: UnitreeZ1ToolsConfig,
        *,
        client: WMAClient | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config
        self.client = client or WMAClient(
            config.wma_url,
            timeout_seconds=config.inference_timeout_seconds,
            action_horizon=config.action_horizon,
            image_height=config.image_height,
            image_width=config.image_width,
        )
        self._lock = asyncio.Lock()
        self._images: deque[np.ndarray] = deque(maxlen=2)
        self._states: deque[np.ndarray] = deque(maxlen=2)

    def _model_state(self, physical_state: np.ndarray) -> np.ndarray:
        state = np.asarray(physical_state, dtype=np.float32).copy()
        if state.shape != (7,) or not np.isfinite(state).all():
            raise ValueError(f"Z1 策略状态必须是有限的7维，实际 {state.shape}")
        robot = self.runtime.config.robot
        span = robot.gripper_close_control - robot.gripper_open_control
        if abs(span) < 1e-8:
            raise ValueError("Z1 夹爪开合控制范围无效")
        closed_ratio = np.clip(
            (state[-1] - robot.gripper_open_control) / span,
            0.0,
            1.0,
        )
        state[-1] = float(closed_ratio * 2.0 - 1.0)
        return state

    def _physical_action(self, model_action: np.ndarray) -> np.ndarray:
        action = np.asarray(model_action, dtype=np.float64).copy()
        if action.shape != (7,) or not np.isfinite(action).all():
            raise ValueError(f"WMA 单步动作必须是有限的7维，实际 {action.shape}")
        if abs(action[-1]) > 1.25:
            raise ValueError(f"WMA 夹爪动作 {action[-1]:.4f} 明显超出 [-1,1]")
        robot = self.runtime.config.robot
        ratio = (float(np.clip(action[-1], -1.0, 1.0)) + 1.0) / 2.0
        action[-1] = (
            robot.gripper_open_control
            + ratio * (robot.gripper_close_control - robot.gripper_open_control)
        )
        if not self._states:
            raise ValueError("执行 WMA 动作前缺少当前机器人状态")
        current = np.asarray(self._states[-1], dtype=np.float64).copy()
        current_ratio = (float(np.clip(current[-1], -1.0, 1.0)) + 1.0) / 2.0
        current[-1] = (
            robot.gripper_open_control
            + current_ratio
            * (robot.gripper_close_control - robot.gripper_open_control)
        )
        arm_step = (
            self.config.max_arm_speed_rad_s
            * self.config.action_period_seconds
        )
        action[:6] = np.clip(
            action[:6],
            current[:6] - arm_step,
            current[:6] + arm_step,
        )
        action[-1] = float(
            np.clip(
                action[-1],
                current[-1] - self.config.max_gripper_step_rad,
                current[-1] + self.config.max_gripper_step_rad,
            )
        )
        return action

    async def _observe(self) -> None:
        observation = await self.runtime.execute(
            "policy_observe",
            {
                "camera": self.config.policy_camera,
                "height": self.config.image_height,
                "width": self.config.image_width,
            },
        )
        image = np.asarray(observation["image"])
        state = self._model_state(np.asarray(observation["state"]))
        if image.shape != (
            self.config.image_height,
            self.config.image_width,
            3,
        ) or image.dtype != np.uint8:
            raise ValueError(
                f"策略相机必须是 uint8 H×W×3，实际 {image.shape}/{image.dtype}"
            )
        self._images.append(np.ascontiguousarray(image))
        self._states.append(state)

    async def _prime_observation_history(self) -> None:
        """按官方 30 Hz 客户端约定采集两张不同时间点的观测。"""

        await self._observe()
        await self.runtime.execute(
            "wait",
            {"sim_seconds": self.config.action_period_seconds},
        )
        await self._observe()

    @staticmethod
    def _canonical_pick_request(params: Mapping[str, Any]) -> tuple[str, str]:
        requested_object = " ".join(str(params["object_name"]).strip().lower().split())
        if requested_object not in {"red cup", "cup"}:
            raise ValueError(
                "当前 Z1 checkpoint 仅支持基准目标 red cup；"
                f"不支持 {requested_object or '<empty>'}"
            )
        requested_instruction = str(params.get("instruction") or "").strip()
        allowed = {
            "",
            "pick up the red cup",
            "pick up the red cup on the table",
        }
        if requested_instruction.lower().rstrip(".") not in allowed:
            raise ValueError(
                "当前 Z1 checkpoint 只接受训练内抓取指令："
                f"{CANONICAL_PICK_INSTRUCTION}"
            )
        return CANONICAL_PICK_OBJECT, CANONICAL_PICK_INSTRUCTION

    async def _lift_and_verify_grasp(self) -> dict[str, Any]:
        observation = await self.runtime.execute(
            "observe",
            {"cameras": [self.config.policy_camera], "render": False},
        )
        state = getattr(observation, "state", None)
        if not isinstance(state, Mapping):
            raise ValueError("抓取验证无法读取机器人位姿")
        position = np.asarray(state.get("tcp_position"), dtype=np.float64)
        rotation = np.asarray(state.get("tcp_rotation_world"), dtype=np.float64)
        if position.shape != (3,) or rotation.shape != (3, 3):
            raise ValueError("抓取验证收到非法 TCP 位姿")
        target = position.copy()
        target[2] += self.config.grasp_lift_m
        lift = await self.runtime.execute(
            "move_ee",
            {
                "frame": self.runtime.config.robot.world_frame,
                "position": target.tolist(),
                "quaternion_wxyz": list(_matrix_to_quaternion(rotation)),
                "duration_seconds": self.config.grasp_lift_duration_seconds,
            },
        )
        lift_payload = lift.to_dict() if hasattr(lift, "to_dict") else dict(lift)
        if lift_payload.get("status") != "succeeded" or not lift_payload.get(
            "reached", True
        ):
            return {
                "status": "failed",
                "error_code": "grasp_verification_lift_failed",
                "message": "夹爪形成接触，但验证抬升失败",
                "lift": lift_payload,
            }
        contact = await self.runtime.execute(
            "gripper_status",
            {"closed_threshold": self.config.gripper_closed_threshold},
        )
        if not bool(contact.get("bilateral_gripper_contact")):
            return {
                "status": "failed",
                "error_code": "grasp_lost_after_lift",
                "message": "验证抬升后双侧接触丢失",
                "lift": lift_payload,
                "contact": contact,
            }
        return {
            "status": "succeeded",
            "lift": lift_payload,
            "contact": contact,
            "lift_height_m": self.config.grasp_lift_m,
        }

    async def _pick(self, params: Mapping[str, Any]) -> dict[str, Any]:
        object_name, instruction = self._canonical_pick_request(params)
        max_rounds = int(
            params.get("max_rounds", self.config.default_max_rounds)
        )
        execute_steps = int(
            params.get("execute_steps", self.config.default_execute_steps)
        )
        if not 1 <= max_rounds <= 10:
            raise ValueError("max_rounds 必须在 [1,10]")
        if not 1 <= execute_steps <= self.config.action_horizon:
            raise ValueError("execute_steps 超出动作 horizon")

        self._images.clear()
        self._states.clear()
        policy_calls = 0
        executed_steps = 0
        last_step: dict[str, Any] | None = None
        await self._prime_observation_history()
        for round_index in range(max_rounds):
            actions = await self.client.predict(
                instruction,
                np.stack(tuple(self._images)),
                np.stack(tuple(self._states)),
            )
            policy_calls += 1
            for model_action in actions[:execute_steps]:
                physical_action = self._physical_action(model_action)
                last_step = await self.runtime.execute(
                    "joint_action",
                    {
                        "target": physical_action.tolist(),
                        "duration_seconds": self.config.action_period_seconds,
                        "closed_threshold": self.config.gripper_closed_threshold,
                    },
                )
                executed_steps += 1
                if bool(last_step.get("bilateral_gripper_contact")):
                    verification = await self._lift_and_verify_grasp()
                    if verification["status"] != "succeeded":
                        return {
                            "status": "failed",
                            "object_name": object_name,
                            "instruction": instruction,
                            "policy_calls": policy_calls,
                            "executed_steps": executed_steps,
                            "rounds": round_index + 1,
                            **verification,
                        }
                    return {
                        "status": "succeeded",
                        "object_name": object_name,
                        "instruction": instruction,
                        "termination": last_step.get("termination"),
                        "policy_calls": policy_calls,
                        "executed_steps": executed_steps,
                        "rounds": round_index + 1,
                        "verification_required": False,
                        "verification": verification,
                        "gripper_model_state": last_step.get(
                            "gripper_model_state"
                        ),
                    }
                if bool(last_step.get("gripper_closed")):
                    return {
                        "status": "failed",
                        "object_name": object_name,
                        "instruction": instruction,
                        "error_code": "empty_grasp",
                        "message": "夹爪已闭合，但没有检测到双侧物体接触",
                        "policy_calls": policy_calls,
                        "executed_steps": executed_steps,
                        "rounds": round_index + 1,
                        "last_step": last_step,
                    }
                await self._observe()
        return {
            "status": "failed",
            "object_name": object_name,
            "instruction": instruction,
            "error_code": "pick_step_budget_exhausted",
            "message": "达到抓取闭环预算，但未检测到夹爪闭合或双侧接触",
            "policy_calls": policy_calls,
            "executed_steps": executed_steps,
            "rounds": max_rounds,
            "last_step": last_step,
        }

    async def _release(self) -> Any:
        return await self.runtime.execute("set_gripper", {"opening": 1.0})

    async def _move_to(self, params: Mapping[str, Any]) -> Any:
        return await self.runtime.execute(
            "move_ee",
            {
                "frame": params["frame"],
                "position": params["position"],
                "quaternion_wxyz": params.get("quaternion_wxyz"),
                "duration_seconds": float(params.get("duration_seconds", 1.5)),
            },
        )

    async def execute(self, tool_name: str, params: Mapping[str, Any]) -> Any:
        async with self._lock:
            if tool_name == "robot_pick":
                return await self._pick(params)
            if tool_name == "robot_release":
                return await self._release()
            if tool_name == "robot_move_to":
                return await self._move_to(params)
            raise KeyError(f"未知 Unitree Z1 工具: {tool_name}")

    def close(self) -> None:
        self.client.close()
