"""G1 高层 manipulation 技能；一次工具调用内完成 GN1x 闭环。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import threading
import time
import uuid
from typing import Any, Callable, Mapping

import numpy as np

from .client import Gr00tClient
from .config import G1ManipulationConfig
from .contract import validate_actions
from .runtime import G1ManipulationEnv
from .tasks import TASKS, validate_handoff


class G1ManipulationTools:
    TOOL_NAMES = frozenset({
        "g1_describe", "g1_observe", "g1_pick", "g1_place",
        "g1_release", "g1_stow", "g1_stop",
    })

    def __init__(
        self,
        config: G1ManipulationConfig,
        *,
        env: G1ManipulationEnv | None = None,
        client: Gr00tClient | None = None,
        navigation_pose_provider: Callable[[], Any] | None = None,
        viewer_enabled: bool = False,
    ) -> None:
        self.config = config
        self.env = env or G1ManipulationEnv(viewer_enabled=viewer_enabled)
        self.client = client or Gr00tClient(
            config.groot_url,
            timeout_seconds=config.inference_timeout_seconds,
        )
        self.navigation_pose_provider = navigation_pose_provider
        self._lock = asyncio.Lock()
        self._stop = threading.Event()
        self._active_request_id: str | None = None
        self._selected_task: str | None = None
        self._carried: dict[str, Any] | None = None
        self._completed: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        self._pose_mirror_stop = threading.Event()
        self._pose_mirror_thread: threading.Thread | None = None
        if viewer_enabled and navigation_pose_provider is not None:
            self._pose_mirror_thread = threading.Thread(
                target=self._mirror_navigation_pose,
                name="g1-navigation-viewer-mirror",
                daemon=True,
            )
            self._pose_mirror_thread.start()

    def _mirror_navigation_pose(self) -> None:
        """在导航期间把后台 SONIC 根位姿和关节状态投影到操作 Viewer。"""
        assert self.navigation_pose_provider is not None
        while not self._pose_mirror_stop.is_set():
            try:
                pose, root, joints, motion_active = self._navigation_sample()
                if root is not None and joints is not None:
                    self.env.set_navigation_state(root, joints)
                else:
                    self.env.set_navigation_pose(
                        float(pose.x), float(pose.y), float(pose.yaw)
                    )
            except ValueError:
                pass
            except RuntimeError:
                pass
            self._pose_mirror_stop.wait(0.05)

    def _navigation_sample(self):
        if self.navigation_pose_provider is None:
            raise RuntimeError("未配置 SONIC 导航状态提供器")
        value = self.navigation_pose_provider()
        sample_time = getattr(value, "monotonic_time", None)
        if sample_time is not None:
            age = time.monotonic() - float(sample_time)
            if age > 1.0:
                raise RuntimeError(f"SONIC MuJoCo 全身状态已过期: age={age:.3f}s")
        pose = getattr(value, "pose", value)
        return (
            pose,
            getattr(value, "root_qpos", None),
            getattr(value, "joint_position_hardware", None),
            getattr(value, "motion_active", None),
        )

    def request_stop(self, request_id: str | None = None) -> dict[str, Any]:
        active = self._active_request_id
        if request_id is None or request_id == active:
            self._stop.set()
        return {
            "status": "succeeded",
            "requested_stop_for": request_id,
            "active_request_id": active,
            "stop_requested": request_id is None or request_id == active,
        }

    async def _metadata(self) -> dict[str, Any]:
        metadata = await self.client.metadata()
        self.env.validate_server(metadata)
        return metadata

    async def describe(self) -> dict[str, Any]:
        local = self.env.describe()
        try:
            remote = await self.client.metadata()
            try:
                self.env.validate_server(remote)
                compatibility = {"compatible": True, "error": None}
            except (KeyError, TypeError, ValueError) as exc:
                compatibility = {"compatible": False, "error": str(exc)}
        except Exception as exc:
            remote = None
            compatibility = {"compatible": False, "error": str(exc)}
        return {
            **local,
            "groot_url": self.config.groot_url,
            "groot_metadata": remote,
            "checkpoint_compatibility": compatibility,
            "navigation_handoff": self._handoff_status(),
            "tools": sorted(self.TOOL_NAMES),
            "carried": dict(self._carried) if self._carried else None,
        }

    def observe(self) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
        state = self.env.state()
        payload = {
            "status": "succeeded",
            "task_id": self.env.current_task,
            "robot": {
                "hand_encoding": self.env.calibration["dex3"]["hand_encoding"],
                "left_hand": state["left_hand"].tolist(),
                "right_hand": state["right_hand"].tolist(),
                "left_hand_opening": self.env.hand_opening("left"),
                "right_hand_opening": self.env.hand_opening("right"),
                "left_arm": state["left_arm"].tolist(),
                "right_arm": state["right_arm"].tolist(),
                "waist": state["waist"].tolist(),
            },
            "carried": dict(self._carried) if self._carried else None,
            "navigation_handoff": self._handoff_status(),
            "privileged_object_state_exposed": False,
        }
        return payload, {
            "ego_view": self.env.render("ego_view"),
            "overview": self.env.render("overview"),
        }

    def _check_handoff(self) -> None:
        if self.navigation_pose_provider is None:
            return
        try:
            pose, root, joints, motion_active = self._navigation_sample()
            validate_handoff(float(pose.x), float(pose.y), float(pose.yaw))
            if motion_active is True:
                raise ValueError("SONIC 仍在执行行走动作，尚未完成操作交接")
            if self._pose_mirror_thread is not None and self._pose_mirror_thread.is_alive():
                # 只有操作技能真正开始时才停止镜像；此前持续显示 SONIC 的实际
                # 根位姿和关节状态，避免在“首次进入容差”时过早冻结 Viewer。
                self._pose_mirror_stop.set()
                self._pose_mirror_thread.join(timeout=1.0)
                if self._pose_mirror_thread.is_alive():
                    raise ValueError("SONIC 尚未完成落稳和关节状态交接")
            if root is not None and joints is not None:
                self.env.set_navigation_state(root, joints)
            else:
                self.env.set_navigation_pose(
                    float(pose.x), float(pose.y), float(pose.yaw)
                )
        except Exception as exc:
            raise ValueError(f"尚未满足导航到操作位的交接条件: {exc}") from exc

    def _handoff_status(self) -> dict[str, Any]:
        if self.navigation_pose_provider is None:
            return {"required": False, "ready": True, "pose": None, "error": None}
        try:
            pose, _, _, motion_active = self._navigation_sample()
            payload = {
                "xy_m": [float(pose.x), float(pose.y)],
                "yaw_rad": float(pose.yaw),
            }
            validate_handoff(float(pose.x), float(pose.y), float(pose.yaw))
            if motion_active is True:
                raise ValueError("SONIC 仍在执行行走动作")
            return {"required": True, "ready": True, "pose": payload, "error": None}
        except Exception as exc:
            return {"required": True, "ready": False, "pose": None, "error": str(exc)}

    def _select_task(self, task_id: str) -> None:
        if task_id not in TASKS:
            raise ValueError(f"未知 task_id {task_id!r}，可选值为 {sorted(TASKS)}")
        if self._selected_task is None:
            self.env.reset(task_id)
            self._selected_task = task_id
        elif self._selected_task != task_id:
            raise ValueError(
                f"当前 manipulation episode 已选择 {self._selected_task}；"
                "切换任务前必须重置 episode"
            )

    async def _prime_history(self, metadata: Mapping[str, Any]) -> None:
        del metadata
        # Arena GN1x modality 只消费当前 ego_view，不伪造历史帧。
        await asyncio.sleep(0)

    def _hold_target(self) -> dict[str, np.ndarray]:
        return self.env.hold_target()

    def _step_actions(self, actions: Mapping[str, np.ndarray], index: int) -> None:
        self.env.step_policy_action(
            {key: value[index] for key, value in actions.items()}, capture=True
        )

    @staticmethod
    def _failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
        return {"status": "failed", "error_code": code, "message": message, **extra}

    async def pick(self, params: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(params["task_id"])
        requested_hand = str(params.get("hand") or "auto")
        if requested_hand not in {"auto", "left", "right"}:
            raise ValueError("hand 必须是 auto、left 或 right")
        if self._carried is not None:
            return self._failure("hand_occupied", "已有物体处于持物状态", carried=self._carried)
        self._check_handoff()
        self._select_task(task_id)
        metadata = await self._metadata()
        execute_steps = int(params.get("execute_steps", self.config.default_execute_steps))
        max_calls = int(params.get("max_policy_calls", self.config.default_max_policy_calls))
        if not 1 <= execute_steps <= self.config.max_execute_steps:
            raise ValueError("execute_steps 超出允许范围")
        if not 1 <= max_calls <= 100:
            raise ValueError("max_policy_calls 必须在 [1,100]")
        await self._prime_history(metadata)
        if self._stop.is_set():
            return {"status": "cancelled", "termination": "operator_cancelled"}

        baseline_z = float(self.env.grasp_status(task_id=task_id)["object_position_m"][2])
        bilateral_seen: set[str] = set()
        stable_lift_steps = 0
        executed_steps = 0
        prompt = TASKS[task_id].instruction
        last_grasp = self.env.grasp_status(task_id=task_id)
        for policy_call in range(1, max_calls + 1):
            response = await self.client.infer(
                self.env.request(prompt, metadata)
            )
            if self._stop.is_set():
                return {"status": "cancelled", "termination": "operator_cancelled",
                        "policy_calls": policy_call - 1, "executed_steps": executed_steps}
            actions = validate_actions(response, metadata, execute_steps)
            for index in range(execute_steps):
                if self._stop.is_set():
                    return {"status": "cancelled", "termination": "operator_cancelled",
                            "policy_calls": policy_call, "executed_steps": executed_steps}
                self._step_actions(actions, index)
                executed_steps += 1
                last_grasp = self.env.grasp_status(task_id=task_id)
                hands = last_grasp["hands"]
                for side in ("left", "right"):
                    if hands[side]["bilateral_contact"]:
                        bilateral_seen.add(side)
                candidates = (
                    [requested_hand] if requested_hand != "auto"
                    else [side for side in ("left", "right") if side in bilateral_seen]
                )
                lifted = last_grasp["object_position_m"][2] >= baseline_z + self.config.pick_lift_m
                active = next((side for side in candidates
                               if side in bilateral_seen and hands[side]["object_contact"]), None)
                if lifted and active is not None:
                    stable_lift_steps += 1
                    if stable_lift_steps >= self.config.pick_stable_steps:
                        self._carried = {
                            "task_id": task_id,
                            "hand": active,
                            "object_body": TASKS[task_id].object_body,
                        }
                        return {
                            "status": "succeeded",
                            "termination": "grasp_and_lift_verified",
                            "task_id": task_id,
                            "hand": active,
                            "policy_calls": policy_call,
                            "executed_steps": executed_steps,
                            "lift_m": last_grasp["object_position_m"][2] - baseline_z,
                        }
                else:
                    stable_lift_steps = 0

                monitored = [requested_hand] if requested_hand != "auto" else ["left", "right"]
                empty = all(
                    hands[side]["opening"] <= self.config.closed_threshold
                    and not hands[side]["object_contact"]
                    for side in monitored
                )
                if empty:
                    return self._failure(
                        "empty_grasp", "夹爪闭合但没有目标物体接触",
                        task_id=task_id, policy_calls=policy_call,
                        executed_steps=executed_steps,
                    )
                await asyncio.sleep(0)
        return self._failure(
            "pick_policy_budget_exhausted",
            "达到抓取推理预算，但未验证接触后抬升",
            task_id=task_id, policy_calls=max_calls,
            executed_steps=executed_steps, last_grasp=last_grasp,
        )

    async def place(self, params: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(params["task_id"])
        if self._carried is None:
            return self._failure("not_holding", "当前没有已验证的持物状态")
        if self._carried["task_id"] != task_id:
            raise ValueError("place.task_id 与当前持物任务不一致")
        hand = str(params.get("hand") or self._carried["hand"])
        if hand != self._carried["hand"]:
            raise ValueError("place.hand 与 pick 记录的持物手不一致")
        metadata = await self._metadata()
        execute_steps = int(params.get("execute_steps", self.config.default_execute_steps))
        max_calls = int(params.get("max_policy_calls", self.config.default_max_policy_calls))
        settle_seconds = float(params.get("settle_timeout_seconds", self.config.settle_timeout_seconds))
        if not 1 <= execute_steps <= self.config.max_execute_steps:
            raise ValueError("execute_steps 超出允许范围")
        if not 1 <= max_calls <= 100 or not 0.5 <= settle_seconds <= 5.0:
            raise ValueError("place 推理预算或落稳超时不合法")
        await self._prime_history(metadata)
        prompt = TASKS[task_id].instruction
        contact_loss = 0
        executed_steps = 0
        for policy_call in range(1, max_calls + 1):
            response = await self.client.infer(
                self.env.request(prompt, metadata)
            )
            if self._stop.is_set():
                return {"status": "cancelled", "termination": "operator_cancelled",
                        "policy_calls": policy_call - 1, "executed_steps": executed_steps}
            actions = validate_actions(response, metadata, execute_steps)
            for index in range(execute_steps):
                if self._stop.is_set():
                    return {"status": "cancelled", "termination": "operator_cancelled",
                            "policy_calls": policy_call, "executed_steps": executed_steps}
                self._step_actions(actions, index)
                executed_steps += 1
                placement = self.env.task_status()
                if placement["success"]:
                    self._carried = None
                    return {"status": "succeeded", "termination": "placement_verified",
                            "policy_calls": policy_call, "executed_steps": executed_steps,
                            "placement": placement}
                grasp = self.env.grasp_status(hand, task_id)
                contact_loss = 0 if grasp["object_contact"] else contact_loss + 1
                if contact_loss >= self.config.contact_loss_steps:
                    if grasp["opening"] < self.config.release_open_threshold:
                        self._carried = None
                        return self._failure(
                            "grasp_lost", "夹爪尚未张开时丢失物体",
                            policy_calls=policy_call, executed_steps=executed_steps,
                            placement=placement,
                        )
                    return await self._settle(
                        task_id, settle_seconds, policy_call, executed_steps
                    )
                await asyncio.sleep(0)
        return self._failure(
            "place_policy_budget_exhausted",
            "达到放置推理预算，但模型尚未释放物体",
            policy_calls=max_calls, executed_steps=executed_steps,
            placement=self.env.task_status(),
        )

    async def _settle(self, task_id: str, timeout_seconds: float,
                      policy_calls: int, executed_steps: int) -> dict[str, Any]:
        steps = max(1, round(timeout_seconds / self.env.dt))
        for _ in range(steps):
            if self._stop.is_set():
                return {"status": "cancelled", "termination": "operator_cancelled",
                        "policy_calls": policy_calls, "executed_steps": executed_steps}
            self.env.step(capture=True)
            placement = self.env.task_status()
            if placement["success"]:
                self._carried = None
                return {"status": "succeeded", "termination": "placement_verified",
                        "policy_calls": policy_calls, "executed_steps": executed_steps,
                        "placement": placement}
            if placement["released"] and placement["settled"]:
                if not placement["inside_target"]:
                    self._carried = None
                    return self._failure("released_outside_target", "物体已在目标区域外落稳",
                                         placement=placement)
                if placement["upright_cosine"] < self.env.task.upright_cosine_min:
                    self._carried = None
                    return self._failure("invalid_object_orientation", "物体落稳但姿态不合格",
                                         placement=placement)
            await asyncio.sleep(0)
        self._carried = None
        return self._failure("settle_timeout", "释放后未在时间预算内得到稳定结果",
                             placement=self.env.task_status())

    async def release(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = str(params.get("hand") or "auto")
        if requested not in {"auto", "left", "right", "both"}:
            raise ValueError("hand 必须是 auto、left、right 或 both")
        if requested == "auto":
            sides = [self._carried["hand"]] if self._carried else ["left", "right"]
        elif requested == "both":
            sides = ["left", "right"]
        else:
            sides = [requested]
        target = self._hold_target()
        for side in sides:
            target[f"{side}_hand"] = self.env.open_hand_pose(side)
        for _ in range(20):
            if self._stop.is_set():
                return {"status": "cancelled", "termination": "operator_cancelled"}
            self.env.step(target, capture=True)
            await asyncio.sleep(0)
        if self._carried and self._carried["hand"] in sides:
            self._carried = None
        return {"status": "succeeded", "termination": "dex3_hand_opened",
                "hands": sides, "task_success_claimed": False}

    async def stow(self, params: Mapping[str, Any]) -> dict[str, Any]:
        if self._carried is not None:
            return self._failure("hand_occupied", "持物时不执行休息姿态；先 place 或 release")
        target = self._hold_target()
        for key in ("left_arm", "right_arm", "waist"):
            target[key] = np.asarray(
                self.env.calibration["initial_pose_rad"][key], dtype=float
            )
        for _ in range(40):
            if self._stop.is_set():
                return {"status": "cancelled", "termination": "operator_cancelled"}
            self.env.step(target, capture=True)
            await asyncio.sleep(0)
        return {"status": "succeeded", "termination": "stow_pose_reached"}

    async def _skill(self, tool_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(params.get("request_id") or uuid.uuid4())
        if not 1 <= len(request_id) <= 128:
            raise ValueError("request_id 必须是 1..128 字符")
        fingerprint = json.dumps([tool_name, dict(params)], sort_keys=True, ensure_ascii=False)
        previous = self._completed.get(request_id)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ValueError("同一 request_id 不能用于不同工具参数")
            return dict(previous[1])
        async with self._lock:
            previous = self._completed.get(request_id)
            if previous is not None:
                return dict(previous[1])
            self._stop.clear()
            self._active_request_id = request_id
            started = time.monotonic()
            try:
                if tool_name == "g1_pick":
                    result = await self.pick(params)
                elif tool_name == "g1_place":
                    result = await self.place(params)
                elif tool_name == "g1_release":
                    result = await self.release(params)
                elif tool_name == "g1_stow":
                    result = await self.stow(params)
                else:
                    raise KeyError(tool_name)
            except asyncio.CancelledError:
                self._stop.set()
                raise
            finally:
                self._active_request_id = None
            result = {**result, "request_id": request_id,
                      "wall_seconds": round(time.monotonic() - started, 3)}
            self._completed[request_id] = (fingerprint, result)
            if len(self._completed) > 128:
                self._completed.popitem(last=False)
            return dict(result)

    async def execute(self, tool_name: str, params: Mapping[str, Any]) -> Any:
        if tool_name == "g1_stop":
            return self.request_stop(params.get("request_id"))
        if tool_name == "g1_describe":
            async with self._lock:
                return await self.describe()
        if tool_name == "g1_observe":
            async with self._lock:
                return self.observe()
        if tool_name in {"g1_pick", "g1_place", "g1_release", "g1_stow"}:
            return await self._skill(tool_name, params)
        raise KeyError(f"未知 G1 manipulation 工具: {tool_name}")

    def close(self) -> None:
        self._stop.set()
        self._pose_mirror_stop.set()
        if self._pose_mirror_thread is not None:
            self._pose_mirror_thread.join(timeout=1.0)
            self._pose_mirror_thread = None
        self.client.close()
        self.env.close()


__all__ = ["G1ManipulationTools"]
