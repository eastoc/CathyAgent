"""无 GPU 回归：通信契约、动作隔离和 MuJoCo 夹爪物理。"""
from __future__ import annotations

import copy
import importlib.util
import tempfile
from pathlib import Path
import unittest

import numpy as np

from .contract import VERSION, SIM_DIMS, encode_image, observation_from_request, sim_metadata, upper_actions


def request_fixture():
    return {
        "schema_version": VERSION, "prompt": "pick the red cube",
        "state": {k: [[0.2] * d] for k, d in SIM_DIMS.items()},
        "video": {"ego_view": [encode_image(np.full((16, 24, 3), i, dtype=np.uint8)) for i in (10, 20)]},
    }


class ContractTests(unittest.TestCase):
    def test_rgb_time_and_batch_dimensions(self):
        obs = observation_from_request(request_fixture(), sim_metadata())
        self.assertEqual(obs["video"]["ego_view"].shape, (1, 2, 16, 24, 3))
        np.testing.assert_array_equal(obs["video"]["ego_view"][0, :, 0, 0, 0], [10, 20])
        self.assertEqual(obs["state"]["left_arm"].shape, (1, 1, 7))

    def test_wrong_hand_dimension_and_nan_rejected(self):
        for value in ([[0] * 7], [[float("nan")]]):
            request = request_fixture()
            request["state"]["left_hand"] = value
            with self.assertRaises(ValueError):
                observation_from_request(request, sim_metadata())

    def test_lower_body_is_discarded_without_readding_state(self):
        metadata = sim_metadata()
        metadata["action_dims"].update(navigate_command=3, base_height_command=1)
        actions = {k: np.full((1, 40, d), 0.7, dtype=np.float32) for k, d in metadata["action_dims"].items()}
        result = upper_actions(actions, metadata)
        self.assertEqual(set(result), set(SIM_DIMS))
        self.assertAlmostEqual(result["left_arm"][0][0], 0.7)


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "需要 requirements-server.txt")
class HttpTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from .server import create_app, HoldPolicy
        metadata = sim_metadata()
        metadata.update(backend="mock_hold_not_gr00t", calibration_sha256="test-calibration")
        self.client = TestClient(create_app(HoldPolicy(), metadata, api_token="test-token"))
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        self.client.close()

    def test_auth_health_metadata_and_infer(self):
        self.assertEqual(self.client.get("/health").json()["backend"], "mock_hold_not_gr00t")
        self.assertEqual(self.client.get("/metadata").status_code, 401)
        self.assertEqual(self.client.get("/metadata", headers=self.headers).status_code, 200)
        request = request_fixture()
        request["calibration_sha256"] = "test-calibration"
        reply = self.client.post("/infer", json=request, headers=self.headers)
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(np.asarray(reply.json()["actions"]["left_arm"]).shape, (40, 7))
        self.assertNotIn("navigate_command", reply.json()["actions"])

    def test_malformed_request_and_calibration_rejected(self):
        request = request_fixture()
        self.assertEqual(self.client.post("/infer", json=request, headers=self.headers).status_code, 409)
        request["calibration_sha256"] = "test-calibration"
        request["state"]["left_hand"] = [[0, 0, 0]]
        self.assertEqual(self.client.post("/infer", json=request, headers=self.headers).status_code, 422)


@unittest.skipUnless(importlib.util.find_spec("mujoco"), "需要 MuJoCo")
class PhysicsTests(unittest.TestCase):
    def setUp(self):
        from .runtime import G1ManipulationEnv
        self.env = G1ManipulationEnv()

    def tearDown(self):
        self.env.close()

    def target(self):
        return {k: v for k, v in self.env.state().items() if not k.endswith("eef_9d")}

    def test_fixed_lower_body_and_named_joint_order(self):
        env = self.env
        pelvis = env.data.body("pelvis").xpos.copy()
        foot = env.data.body("left_ankle_roll_link").xpos.copy()
        target = self.target()
        target["left_arm"] = np.array([0.5, 0.2, 0, 1, 0, 0, 0])
        target["waist"] = np.array([0.2, 0, 0])
        for _ in range(30):
            env.step(target)
        np.testing.assert_array_equal(env.data.body("pelvis").xpos, pelvis)
        np.testing.assert_array_equal(env.data.body("left_ankle_roll_link").xpos, foot)
        self.assertGreater(env.state()["left_arm"][0], 0.35)
        self.assertEqual(env.model.nu, 19)  # 17 上半身关节 + 2 夹爪驱动

    def test_grippers_close_open_and_remain_symmetric(self):
        target = self.target()
        target.update(left_hand=[0], right_hand=[0])
        for _ in range(30):
            self.env.step(target)
        self.assertLess(self.env.state()["left_hand"][0], 0.03)
        target.update(left_hand=[1], right_hand=[1])
        for _ in range(30):
            self.env.step(target)
        self.assertGreater(self.env.state()["left_hand"][0], 0.97)
        for side in ("left", "right"):
            q = [self.env.data.qpos[self.env.model.joint(f"{side}_finger_{f}_joint").qposadr[0]] for f in ("a", "b")]
            self.assertLess(abs(q[0] - q[1]), 1e-4)

    def test_atomic_rejection_and_velocity_limit(self):
        target = self.target()
        old_ctrl = self.env.data.ctrl.copy()
        target["right_hand"] = [float("inf")]
        with self.assertRaises(ValueError):
            self.env.step(target)
        np.testing.assert_array_equal(self.env.data.ctrl, old_ctrl)
        target = self.target()
        target["left_arm"][0] = 1.2
        self.env.step(target)
        actuator = self.env.model.actuator("left_shoulder_pitch_joint").id
        self.assertAlmostEqual(self.env.data.ctrl[actuator] - old_ctrl[actuator], self.env.dt)

    def test_history_and_checkpoint_mismatch(self):
        metadata = sim_metadata()
        with self.assertRaisesRegex(ValueError, "历史"):
            self.env.request("test", metadata)
        # 合成图像用于检查索引；真实运行由 capture() 在每个控制周期采集。
        for i in range(21):
            self.env.history.append(np.full((2, 2, 3), i, np.uint8))
        obs = observation_from_request(self.env.request("test", metadata), metadata)
        np.testing.assert_array_equal(obs["video"]["ego_view"][0, :, 0, 0, 0], [0, 20])
        changed = copy.deepcopy(metadata)
        changed["state_dims"]["left_hand"] = 7
        with self.assertRaisesRegex(ValueError, "NEW_EMBODIMENT"):
            self.env.validate_server(changed)

    def test_camera_direction_and_eef_reference(self):
        camera = self.env.model.camera("ego_view").id
        look = -self.env.data.cam_xmat[camera].reshape(3, 3)[:, 2]
        self.assertGreater(look[0], 0.7)
        self.assertLess(look[2], -0.4)
        state = self.env.state()
        for side in ("left", "right"):
            wrist = self.env.data.site(f"{side}_wrist_eef").xpos
            pelvis = self.env.data.body("pelvis").xpos
            np.testing.assert_allclose(state[f"{side}_wrist_eef_9d"][:3], wrist - pelvis, atol=1e-6)

    def test_calibration_mismatch_requires_rebuild(self):
        import json
        from .contract import CALIBRATION
        from .runtime import G1ManipulationEnv
        changed = json.loads(CALIBRATION.read_text())
        changed["camera"]["pos_m"][0] += 0.01
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "build_scene"):
                G1ManipulationEnv(calibration_path=path)


if __name__ == "__main__":
    unittest.main()
