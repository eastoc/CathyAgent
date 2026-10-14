"""一个 Agent 技能调用内完成多轮观测/π推理/动作执行。"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import OrderedDict


class SkillExecutor:
    def __init__(self, runtime, policy, config: dict):
        self.runtime, self.policy, self.config = runtime, policy, config
        self.lock = asyncio.Lock()
        self.stop_event = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        self._completed = OrderedDict()
        self._cancelled = set()
        self._active_request_id = None
        self._closed = False

    def request_stop(self, request_id=None):
        if request_id is not None:
            self._cancelled.add(request_id)
        if request_id is None or request_id == self._active_request_id:
            self.stop_event.set()

    def close(self):
        self._closed = True
        self.stop_event.set()

    async def execute(self, instruction: str, arm: str | None = None,
                      max_policy_steps: int | None = None, request_id: str | None = None):
        policy_cfg = self.config["policy"]
        arm = arm or policy_cfg["active_arm"]
        steps = policy_cfg["default_skill_steps"] if max_policy_steps is None else max_policy_steps
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
            raise ValueError("instruction 必须是1..2000字符的子任务指令")
        if arm not in policy_cfg["allowed_arms"]:
            raise ValueError(f"未启用控制模式 {arm!r}；允许值为 {policy_cfg['allowed_arms']}")
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= policy_cfg["max_skill_steps"]:
            raise ValueError("max_policy_steps 超出技能预算")
        request_id = request_id or str(uuid.uuid4())
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("request_id 必须是1..128字符")
        fingerprint = (instruction, arm, steps)
        async with self.lock:
            if self._closed:
                raise RuntimeError("技能执行器已关闭")
            if request_id in self._completed:
                previous, result = self._completed[request_id]
                if previous != fingerprint:
                    raise ValueError("同一 request_id 不能用于不同任务参数")
                return result
            self.stop_event.clear()
            self._active_request_id = request_id
            # 支持宿主在 MCP 请求尚未进入此协程时取消，避免迟到任务重新启动。
            if request_id in self._cancelled:
                self.stop_event.set()
            self.idle.clear()
            start = time.monotonic()
            stats = {"policy_calls": 0, "executed_steps": 0}
            report = {"request_id": request_id, "arm": arm, "instruction": instruction,
                      "task_success": None, "robot_executed": False}
            try:
                await asyncio.wait_for(self._loop(instruction, arm, steps, stats),
                                       timeout=policy_cfg["skill_timeout_seconds"])
                report["status"] = "cancelled" if self.stop_event.is_set() else "step_budget_reached"
            except asyncio.TimeoutError:
                self.stop_event.set()
                report["status"] = "timed_out"
            except asyncio.CancelledError:
                self.stop_event.set()
                report["status"] = "cancelled"
                raise
            except Exception as exc:
                self.stop_event.set()
                report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                report.update(stats, wall_seconds=round(time.monotonic() - start, 3))
                report["robot_executed"] = stats["executed_steps"] > 0
                report["verification_required"] = True
                self._completed[request_id] = (fingerprint, report)
                if len(self._completed) > 128:
                    old_id, _ = self._completed.popitem(last=False)
                    self._cancelled.discard(old_id)
                self._active_request_id = None
                self.idle.set()
            return report

    async def _loop(self, instruction, arm, limit, stats):
        while stats["executed_steps"] < limit and not self.stop_event.is_set():
            observation = await self.runtime.observe_policy(arm)
            if self.stop_event.is_set():
                return
            stats["policy_calls"] += 1
            actions = await self.policy.predict(instruction, observation)
            # 推理返回后必须再检查取消状态；迟到的动作块不能执行。
            if self.stop_event.is_set():
                return
            count = min(self.config["policy"]["replan_steps"], len(actions),
                        limit - stats["executed_steps"])
            for action in actions[:count]:
                if self.stop_event.is_set():
                    return
                await self.runtime.step(action, arm, self.stop_event)
                stats["executed_steps"] += 1
