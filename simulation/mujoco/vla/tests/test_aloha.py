"""ALOHA 本地/云端契约测试；真实场景需显式开启环境变量。"""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import ImageBlock, JsonBlock
from cathy.mcp import McpHub
from cathy.plugins import PluginRegistry
from simulation.mujoco.vla.aloha_contract import (
    ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION, pack_state, unpack_action, validate_actions, validate_state,
)
from simulation.mujoco.vla.aloha_mcp_server import create_server, decode_image
from simulation.mujoco.vla.aloha_policy import PiAlohaMcpPolicy, encode_aloha_image, parse_aloha_actions
from simulation.mujoco.vla.aloha_runtime import AlohaRuntime
from simulation.mujoco.vla.config import DEFAULT_VLA_ENVIRONMENT, load_vla_config
from simulation.mujoco.observation import encode_png


def cfg():
    return load_vla_config(DEFAULT_VLA_ENVIRONMENT)


def obs():
    return {"state": np.array([0, -0.96, 1.16, 0, -0.3, 0, 0.8] * 2),
            "images": {name: np.zeros((48, 64, 3), dtype=np.uint8) for name in CAMERAS}, "snapshot_id": 0}


def metadata():
    return {"contract_version": CONTRACT_VERSION, "policy_config": "pi05_aloha", "control_hz": 50,
            "action_representation": ACTION_REPRESENTATION, "state_dim": 14, "action_dim": 14,
            "cameras": list(CAMERAS)}


class FakeModel:
    def __init__(self):
        self.calls = []

    def get_server_metadata(self):
        return metadata()

    def infer(self, observation):
        self.calls.append(observation)
        return {"actions": np.tile(observation["state"], (5, 1))}


class FakeAlohaRuntime:
    @staticmethod
    def check_dependencies():
        pass

    def __init__(self, *args, **kwargs):
        self.steps = 0

    async def observe_policy(self, arm):
        assert arm == "both"
        return obs()

    async def step(self, action, arm, cancel):
        assert arm == "both" and np.asarray(action).shape == (14,) and not cancel.is_set()
        self.steps += 1

    async def snapshot(self):
        return {"snapshot_id": self.steps}, [(name, encode_png(img)) for name, img in obs()["images"].items()]

    def describe(self):
        return {"allowed_arms": ["both"]}

    def close(self):
        pass


class AlohaContractTest(unittest.TestCase):
    def test_default_does_not_require_robosuite(self):
        self.assertEqual(cfg()["backend"], "native_aloha")
        with patch("importlib.metadata.version", side_effect=AssertionError("must not inspect robosuite")):
            AlohaRuntime.check_dependencies()

    def test_state_action_roundtrip_and_finger_symmetry(self):
        state = obs()["state"]
        state[0] = 2.0
        raw = unpack_action(state)
        self.assertEqual(raw.shape, (16,))
        self.assertEqual(raw[6], -raw[7])
        self.assertEqual(raw[14], -raw[15])
        np.testing.assert_allclose(pack_state(raw), state, atol=1e-6)
        self.assertEqual(validate_actions([state])[0, 0], 2.0)

    def test_rejects_libero_invalid_shapes_and_nonfinite(self):
        for value in ([[0] * 7], [[0] * 32], [], [[float("nan")] * 14], [[20] * 14]):
            with self.assertRaises(ValueError):
                validate_actions(value)
        with self.assertRaises(ValueError):
            validate_state([0] * 8)

    def test_no_rotation_or_channel_swap(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[0, 0] = [255, 20, 30]
        encoded = encode_aloha_image(frame)
        decoded = decode_image(encoded)
        self.assertEqual(decoded.shape, (3, 48, 64))
        np.testing.assert_array_equal(decoded[:, 0, 0], [255, 20, 30])
        np.testing.assert_array_equal(decoded.transpose(1, 2, 0), frame)

    def test_decode_rejects_malformed_and_oversized(self):
        for value in ("data:image/png;base64,AAAA", "invalid!", "A" * 8_000_001):
            with self.assertRaises((ValueError, OSError)):
                decode_image(value)

    def test_wrong_websocket_server_fails_before_mcp_start(self):
        with self.assertRaisesRegex(RuntimeError, "契约"):
            create_server(SimpleNamespace(get_server_metadata=lambda: {"reset_pose": [0] * 6}))

    def test_response_contract_and_shape_are_mandatory(self):
        payload = {**metadata(), "actions": [obs()["state"].tolist()], "shape": [1, 14], "robot_executed": False}
        self.assertEqual(parse_aloha_actions(SimpleNamespace(data=payload)).shape, (1, 14))
        for field, value in (("contract_version", "libero"), ("action_representation", "delta"),
                             ("robot_executed", True), ("policy_config", "pi0_aloha_sim"), ("shape", [50, 14])):
            bad = {**payload, field: value}
            with self.assertRaises(ValueError):
                parse_aloha_actions(SimpleNamespace(data=bad))

    def test_config_rejects_cross_model_and_single_arm(self):
        for section, key, value in (("policy", "allowed_arms", ["left"]), ("policy", "tool", "pi_predict"),
                                    ("runtime", "control_hz", 20), ("observation", "policy_rotate_180", True)):
            bad = deepcopy(cfg())
            bad[section][key] = value
            with patch("simulation.mujoco.vla.config._load_yaml", return_value=bad):
                with self.assertRaises(ValueError):
                    load_vla_config("unused")


class AlohaMcpTest(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_server_script_and_local_policy_use_same_contract(self):
        model = FakeModel()
        hub = McpHub(create_server(model), connect_timeout=10)
        hub.start()
        try:
            policy = PiAlohaMcpPolicy(hub, "mcp__pi_aloha_predict", timeout=5)
            result = await policy.predict("put red in the box", obs())
            self.assertEqual(result.shape, (5, 14))
            self.assertEqual(set(model.calls[0]), {"state", "images", "prompt"})
            self.assertEqual(model.calls[0]["images"]["cam_high"].shape, (3, 48, 64))
            info = await hub.acall_tool_raw("mcp__pi_aloha_info", {})
            self.assertFalse(info.is_error)
        finally:
            hub.shutdown()

    async def test_bootstrap_both_mcp_hops_with_new_default(self):
        from simulation.mujoco.vla.bootstrap import register_vla
        model = FakeModel()
        remote = create_server(model)
        registry = PluginRegistry(plugins_dirs=[])
        def make_hub(source, **kwargs):
            return McpHub(remote if isinstance(source, dict) else source, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with patch("simulation.mujoco.vla.bootstrap.McpHub", side_effect=make_hub), \
                    patch("simulation.mujoco.vla.bootstrap.AlohaRuntime", FakeAlohaRuntime):
                register_vla(registry, {"MCP": {"enabled": True, "mcp_servers": {
                    "robot_vla": {"url": "https://fixture.invalid/mcp"}}}},
                    DEFAULT_VLA_ENVIRONMENT, LocalArtifactStore(directory), False)
            try:
                name = "mcp__robot_vla__robot_execute_skill"
                result = await registry.acall_result(name, {"instruction": "put red in box", "max_policy_steps": 7})
                self.assertEqual(result.status, "succeeded", result.text)
                self.assertEqual(len(model.calls), 2)
                report = next(b.value for b in result.content if isinstance(b, JsonBlock))["execution"]
                self.assertEqual(report["arm"], "both")
                self.assertEqual(report["executed_steps"], 7)
                self.assertIsNone(report["task_success"])
                self.assertEqual(sum(isinstance(b, ImageBlock) for b in result.content), 3)
            finally:
                registry.shutdown()


@unittest.skipUnless(os.environ.get("CATHY_VLA_PHYSICS_TEST") == "1", "需显式开启真实 MuJoCo 渲染测试")
class AlohaPhysicsTest(unittest.IsolatedAsyncioTestCase):
    async def test_load_render_control_and_cancel(self):
        runtime = AlohaRuntime(cfg(), viewer_enabled=False)
        try:
            self.assertEqual(runtime.model.nu, 16)
            self.assertFalse(runtime.describe()["robosuite_required"])
            observation = await runtime.observe_policy("both")
            self.assertEqual(observation["state"].shape, (14,))
            for frame in observation["images"].values():
                self.assertEqual(frame.shape, (480, 640, 3))
                self.assertGreater(np.ptp(frame), 0)
            before, _ = await runtime.snapshot()
            action = observation["state"].copy()
            action[0] += 0.25
            action[7] -= 0.25
            for _ in range(10):
                await runtime.step(action, "both", threading.Event())
            state, images = await runtime.snapshot()
            self.assertEqual(state["episode_steps"], 10)
            self.assertAlmostEqual(state["sim_time"] - before["sim_time"], 0.2, places=5)
            self.assertGreater(state["joint_state"][0], before["joint_state"][0])
            self.assertLess(state["joint_state"][7], before["joint_state"][7])
            self.assertTrue(np.isfinite(runtime.data.qpos).all())
            for color in ("red", "green", "blue"):
                self.assertGreaterEqual(runtime.model.body(f"{color}_cube").id, 0)
            self.assertGreaterEqual(runtime.model.body("target_box").id, 0)
            self.assertNotIn("objects", state)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaisesRegex(RuntimeError, "取消"):
                await runtime.step(action, "both", cancel)
            with self.assertRaises(ValueError):
                await runtime.step([0] * 7, "both", threading.Event())
            after, _ = await runtime.snapshot()
            self.assertEqual(after["episode_steps"], 10)
            output = Path(__file__).resolve().parents[2] / "outputs" / "aloha-smoke"
            output.mkdir(parents=True, exist_ok=True)
            for name, png in images:
                (output / f"{name}.png").write_bytes(png)
            print(f"ALOHA 三路相机帧: {output}")
        finally:
            runtime.close()


@unittest.skipUnless(os.environ.get("CATHY_VLA_VIEWER_TEST") == "1", "需 mjpython 与显式 GUI 测试开关")
class AlohaViewerTest(unittest.IsolatedAsyncioTestCase):
    async def test_passive_viewer_and_offscreen_renderer_coexist(self):
        runtime = AlohaRuntime(cfg(), viewer_enabled=True)
        try:
            self.assertTrue(runtime._viewer.is_running)
            observation = await runtime.observe_policy("both")
            await runtime.step(observation["state"], "both", threading.Event())
            state, images = await runtime.snapshot()
            self.assertEqual(state["episode_steps"], 1)
            self.assertEqual(len(images), 3)
            self.assertTrue(runtime._viewer.is_running)
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
