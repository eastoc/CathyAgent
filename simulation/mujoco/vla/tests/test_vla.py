from __future__ import annotations

import asyncio
import base64
import io
import tempfile
import threading
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import ImageBlock, JsonBlock
from cathy.mcp import McpHub, McpToolPlugin, build_mcp_manifest
from cathy.plugins import PluginRegistry
from simulation.mujoco.observation import encode_png
from simulation.mujoco.vla.config import LEGACY_VLA_ENVIRONMENT as DEFAULT_VLA_ENVIRONMENT, load_vla_config
from simulation.mujoco.vla.executor import SkillExecutor
from simulation.mujoco.vla.policy import PiMcpPolicy, encode_policy_image, parse_actions
from simulation.mujoco.vla.runtime import PandaRuntime, libero_state
from simulation.mujoco.vla.server import create_skill_server


def config():
    return load_vla_config(DEFAULT_VLA_ENVIRONMENT)


def observation():
    return {"state": np.zeros(8), "image": np.zeros((256, 256, 3), dtype=np.uint8),
            "wrist_image": np.zeros((256, 256, 3), dtype=np.uint8), "snapshot_id": 0}


class FakeRuntime:
    @staticmethod
    def check_dependencies():
        pass

    def __init__(self, *args, **kwargs):
        self.steps = []
        self.frames = []

    def close(self):
        pass

    def describe(self):
        return {"allowed_arms": ["left"], "coordinated_bimanual_supported": False}

    async def observe_policy(self, arm):
        self.frames.append(len(self.steps))
        obs = observation()
        obs["snapshot_id"] = len(self.steps)
        return obs

    async def step(self, action, arm, cancel):
        if cancel.is_set():
            raise RuntimeError("cancelled")
        self.steps.append((arm, np.asarray(action)))
        await asyncio.sleep(0)

    async def snapshot(self):
        return {"snapshot_id": len(self.steps)}, [("agentview", encode_png(observation()["image"]))]


class FakePolicy:
    async def predict(self, prompt, obs):
        return np.tile([0.1, 0, 0, 0, 0, 0, -1], (10, 1))


class PolicyContractTest(unittest.TestCase):
    def test_dependency_check_rejects_new_mass_matrix_api(self):
        mujoco = SimpleNamespace(__version__="3.10.0")
        with patch("importlib.metadata.version", return_value="1.4.1"), \
                patch.dict(sys.modules, {"mujoco": mujoco}):
            with self.assertRaisesRegex(RuntimeError, "mj_fullM"):
                PandaRuntime.check_dependencies()

    def test_dependency_check_accepts_pinned_mujoco(self):
        mujoco = SimpleNamespace(__version__="3.3.0")
        with patch("importlib.metadata.version", return_value="1.4.1"), \
                patch.dict(sys.modules, {"mujoco": mujoco}):
            PandaRuntime.check_dependencies()

    def test_server_actions_and_shape(self):
        value = SimpleNamespace(structured_content={"actions": [[0] * 7], "shape": [1, 7],
                                                   "robot_executed": False})
        self.assertEqual(parse_actions(value).shape, (1, 7))
        for actions in ([[0] * 14], [[float("nan")] * 7], [[3] * 7], []):
            value.structured_content["actions"] = actions
            with self.assertRaises(ValueError):
                parse_actions(value)

    def test_remote_execution_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_actions(SimpleNamespace(data={"actions": [[0] * 7], "robot_executed": True}))

    def test_rotation_once_resize_left_to_server(self):
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        frame[0, 0] = [255, 20, 30]
        result = Image.open(io.BytesIO(base64.b64decode(encode_policy_image(frame))))
        self.assertEqual(result.size, (256, 256))
        self.assertEqual(result.getpixel((255, 255)), (255, 20, 30))

    def test_libero_state_uses_two_finger_positions(self):
        obs = {"robot0_eef_pos": [1, 2, 3], "robot0_eef_quat": [0, 0, 0, 1],
               "robot0_gripper_qpos": [0.03, -0.03]}
        np.testing.assert_allclose(libero_state(obs, 0), [1, 2, 3, 0, 0, 0, 0.03, -0.03])

    def test_native_renderer_preserves_libero_image_convention(self):
        runtime = PandaRuntime.__new__(PandaRuntime)
        runtime._viewer = None
        runtime.data = object()
        runtime._render_options = object()
        frame = np.arange(27, dtype=np.uint8).reshape(3, 3, 3)
        runtime._renderer = Mock()
        runtime._renderer.render.return_value = frame
        raw = runtime._render_camera("agentview")
        runtime._renderer.update_scene.assert_called_once_with(
            runtime.data, camera="agentview", scene_option=runtime._render_options)
        np.testing.assert_array_equal(raw, frame[::-1])
        self.assertTrue(raw.flags.c_contiguous)
        aligned = np.asarray(Image.open(io.BytesIO(base64.b64decode(encode_policy_image(raw)))))
        np.testing.assert_array_equal(aligned, frame[:, ::-1])

    def test_renderer_cleanup_even_if_viewer_close_fails(self):
        runtime = PandaRuntime.__new__(PandaRuntime)
        runtime._viewer = Mock()
        runtime._viewer.close.side_effect = RuntimeError("viewer")
        renderer = runtime._renderer = Mock()
        runtime.env = Mock()
        with self.assertRaisesRegex(RuntimeError, "viewer"):
            runtime._cleanup()
        renderer.close.assert_called_once_with()
        runtime.env.close.assert_called_once_with()
        self.assertIsNone(runtime._renderer)

    def test_config_rejects_bimanual(self):
        invalid = deepcopy(config())
        invalid["policy"]["allowed_arms"] = ["both"]
        with patch("simulation.mujoco.vla.config._load_yaml", return_value=invalid):
            with self.assertRaises(ValueError):
                load_vla_config("unused")

    def test_step_routes_only_active_arm(self):
        runtime = PandaRuntime.__new__(PandaRuntime)
        runtime.config = config()
        runtime._steps = runtime._snapshot = 0
        runtime._stop = threading.Event()
        runtime._viewer = None
        runtime._gripper_commands = [-1, 1]
        runtime.data = SimpleNamespace(qpos=np.zeros(18), qvel=np.zeros(18))
        recorded = []
        runtime.env = SimpleNamespace(step=lambda action: (recorded.append(action.copy()) or {}, 0, False, {}))
        action = np.array([0.1, 0.2, 0.3, 0, 0, 0, -1])
        runtime._step(action, "left", threading.Event())
        np.testing.assert_array_equal(recorded[0][:7], action)
        np.testing.assert_array_equal(recorded[0][7:], [0, 0, 0, 0, 0, 0, 1])
        runtime._stop.set()
        with self.assertRaises(RuntimeError):
            runtime._step(action, "left", threading.Event())
        self.assertEqual(len(recorded), 1)


class ExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = FakeRuntime()
        self.executor = SkillExecutor(self.runtime, FakePolicy(), config())

    async def test_replans_on_fresh_state_without_llm(self):
        report = await self.executor.execute("put the red cube in the box", max_policy_steps=12)
        self.assertEqual(self.runtime.frames, [0, 5, 10])
        self.assertEqual(report["policy_calls"], 3)
        self.assertEqual(report["executed_steps"], 12)
        self.assertEqual(report["status"], "step_budget_reached")
        self.assertIsNone(report["task_success"])

    async def test_idempotency_and_conflicting_reuse(self):
        first = await self.executor.execute("pick red", max_policy_steps=3, request_id="same")
        second = await self.executor.execute("pick red", max_policy_steps=3, request_id="same")
        self.assertEqual(first, second)
        self.assertEqual(len(self.runtime.steps), 3)
        with self.assertRaises(ValueError):
            await self.executor.execute("pick blue", max_policy_steps=3, request_id="same")

    async def test_stop_during_inference_discards_late_actions(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class DelayedPolicy:
            async def predict(self, prompt, obs):
                entered.set()
                await release.wait()
                return np.zeros((5, 7))
        self.executor.policy = DelayedPolicy()
        task = asyncio.create_task(self.executor.execute("pick red"))
        await entered.wait()
        self.executor.request_stop()
        release.set()
        report = await task
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(self.runtime.steps, [])

    async def test_timeout_is_not_success(self):
        class SlowPolicy:
            async def predict(self, prompt, obs):
                await asyncio.Event().wait()
        self.executor.policy = SlowPolicy()
        self.executor.config["policy"]["skill_timeout_seconds"] = 0.02
        report = await self.executor.execute("pick red")
        self.assertEqual(report["status"], "timed_out")
        self.assertIsNone(report["task_success"])
        self.assertTrue(self.executor.idle.is_set())
        self.assertEqual(self.runtime.steps, [])

    async def test_task_cancellation_stops_execution(self):
        entered = asyncio.Event()
        class SlowPolicy:
            async def predict(self, prompt, obs):
                entered.set()
                await asyncio.Event().wait()
        self.executor.policy = SlowPolicy()
        task = asyncio.create_task(self.executor.execute("pick red", request_id="cancelled"))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.executor.stop_event.is_set())
        self.assertTrue(self.executor.idle.is_set())
        replay = await self.executor.execute("pick red", request_id="cancelled")
        self.assertEqual(replay["status"], "cancelled")
        self.assertEqual(self.runtime.steps, [])

    async def test_cancel_before_server_starts_request(self):
        self.executor.request_stop("before-start")
        report = await self.executor.execute("pick red", request_id="before-start")
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(self.runtime.steps, [])

    async def test_close_prevents_late_request(self):
        self.executor.close()
        with self.assertRaises(RuntimeError):
            await self.executor.execute("pick red")
        self.assertEqual(self.runtime.steps, [])

    async def test_right_arm_disabled_by_default(self):
        with self.assertRaises(ValueError):
            await self.executor.execute("pick red", arm="right")


class McpIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.artifacts = LocalArtifactStore(self.temp.name)
        self.runtime = FakeRuntime()
        self.executor = SkillExecutor(self.runtime, FakePolicy(), config())
        self.hub = McpHub(create_skill_server(self.executor), server_names=["robot_vla"], connect_timeout=10)
        self.hub.start()

    async def asyncTearDown(self):
        self.hub.shutdown()
        self.temp.cleanup()

    async def test_agent_tools_high_level_only(self):
        tools = {tool.outer_name for tool in self.hub.list_tools()}
        self.assertEqual(tools, {"mcp__robot_vla__robot_describe", "mcp__robot_vla__robot_observe",
                                 "mcp__robot_vla__robot_execute_skill"})

    async def test_mixed_mcp_images_and_json_reach_agent(self):
        plugin = McpToolPlugin(self.hub, artifact_store=self.artifacts)
        result = await plugin.aexecute("mcp__robot_vla__robot_execute_skill", {
            "instruction": "put red in the box", "max_policy_steps": 2,
        })
        self.assertTrue(any(isinstance(block, ImageBlock) for block in result.content))
        reports = [b.value for b in result.content if isinstance(b, JsonBlock)]
        self.assertEqual(reports[0]["execution"]["executed_steps"], 2)
        self.assertEqual(reports[0]["observation"]["snapshot_id"], 2)
        self.assertIsNone(reports[0]["execution"]["task_success"])

    async def test_real_pi_contract_with_inmemory_server(self):
        from fastmcp import FastMCP
        remote = FastMCP("PiFixture")
        @remote.tool()
        def pi_predict(prompt: str, state: list[float], image_base64: str,
                       wrist_image_base64: str) -> dict:
            self.assertEqual(len(state), 8)
            self.assertEqual(Image.open(io.BytesIO(base64.b64decode(image_base64))).size, (256, 256))
            return {"actions": [[0] * 7] * 5, "shape": [5, 7], "robot_executed": False}
        hub = McpHub(remote, connect_timeout=10)
        hub.start()
        try:
            policy = PiMcpPolicy(hub, "mcp__pi_predict", timeout=5, rotate_180=True)
            actions = await policy.predict("pick red", observation())
            self.assertEqual(actions.shape, (5, 7))
        finally:
            hub.shutdown()

    async def test_registry_returns_typed_image_blocks(self):
        registry = PluginRegistry(plugins_dirs=[])
        registry.register_internal_plugin(build_mcp_manifest(self.hub, project_root=Path.cwd()),
                                          McpToolPlugin(self.hub, artifact_store=self.artifacts),
                                          skip_initialize=True)
        result = await registry.acall_result("mcp__robot_vla__robot_observe", {})
        self.assertTrue(any(isinstance(block, ImageBlock) for block in result.content))

    async def test_bootstrap_registers_background_skill_and_both_mcp_hops(self):
        from fastmcp import FastMCP
        from simulation.mujoco.vla.bootstrap import register_vla
        remote = FastMCP("CloudPiFixture")
        calls = []
        @remote.tool(name="robot_vla_pi_predict")
        def pi_predict(prompt: str, state: list[float], image_base64: str,
                       wrist_image_base64: str) -> dict:
            calls.append(prompt)
            return {"actions": [[0] * 7] * 5, "robot_executed": False}

        def make_hub(source, **kwargs):
            return McpHub(remote if isinstance(source, dict) else source, **kwargs)

        registry = PluginRegistry(plugins_dirs=[])
        with patch("simulation.mujoco.vla.bootstrap.McpHub", side_effect=make_hub), \
             patch("simulation.mujoco.vla.bootstrap.PandaRuntime", FakeRuntime):
            register_vla(registry, {"MCP": {"enabled": True, "mcp_servers": {
                "robot_vla": {"url": "https://fixture.invalid/mcp"},
            }}}, DEFAULT_VLA_ENVIRONMENT, self.artifacts, False)
        try:
            name = "mcp__robot_vla__robot_execute_skill"
            execution_policy = registry.get_execution_policy(name)
            self.assertEqual(execution_policy.mode, "background")
            self.assertGreater(execution_policy.timeout_seconds, config()["policy"]["skill_timeout_seconds"])
            result = await registry.acall_result(name, {"instruction": "pick red", "max_policy_steps": 7})
            self.assertEqual(result.status, "succeeded", result.text)
            self.assertEqual(calls, ["pick red", "pick red"])
            reports = [block.value for block in result.content if isinstance(block, JsonBlock)]
            self.assertEqual(reports[0]["execution"]["executed_steps"], 7)
            self.assertTrue(any(isinstance(block, ImageBlock) for block in result.content))
        finally:
            registry.shutdown()


if __name__ == "__main__":
    unittest.main()
