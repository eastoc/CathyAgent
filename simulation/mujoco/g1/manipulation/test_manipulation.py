"""GN1x Arena G1 契约、场景与可选 MuJoCo 回归。"""

from __future__ import annotations

import importlib.util
import threading
from types import SimpleNamespace
import time
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from .config import DEFAULT_GROOT_URL, G1ManipulationConfig
from .contract import (
    ACTION_DIMS, ACTION_DELTA_INDICES, EMBODIMENT, HAND_SUFFIXES, JOINT_GROUPS,
    MODEL_ID, SCENE, SONIC_JOINTS, STATE_DIMS, VERSION, calibration_hash, encode_image,
    load_calibration, observation_from_request, sim_metadata, validate_actions,
    validate_metadata,
)
from .tasks import DEFAULT_TASK, TASKS, load_station, validate_handoff
from .service import G1ManipulationTools


class ContractTests(unittest.TestCase):
    def test_groot_service_modality_shape(self):
        metadata = validate_metadata(sim_metadata())
        self.assertEqual(metadata["embodiment"], EMBODIMENT)
        self.assertEqual(metadata["video_keys"], ["ego_view"])
        self.assertEqual(metadata["video_delta_indices"], [0])
        self.assertEqual(metadata["state_dims"], {
            "left_arm": 7, "right_arm": 7, "left_hand": 7,
            "right_hand": 7, "waist": 3,
        })
        self.assertEqual(metadata["action_dims"], metadata["state_dims"])
        self.assertEqual(len(metadata["action_delta_indices"]), 40)

    def test_running_groot_metadata_without_legacy_fields_is_accepted(self):
        metadata = {
            **sim_metadata(),
            "backend": "gr00t_n1.7",
            "model_path": "/root/autodl-tmp/vla/models/"
            "GN1x-Tuned-Arena-G1-Static-PickNPlace",
        }
        self.assertEqual(validate_metadata(metadata)["backend"], "gr00t_n1.7")

    def test_dataset_joint_order_is_43_dof_g1_dex3(self):
        legs = tuple(
            f"{side}_{name}_joint"
            for side in ("left", "right")
            for name in ("hip_pitch", "hip_roll", "hip_yaw", "knee", "ankle_pitch", "ankle_roll")
        )
        dataset_order = (
            *legs[:6], *legs[6:], *JOINT_GROUPS["waist"],
            *JOINT_GROUPS["left_arm"], *JOINT_GROUPS["left_hand"],
            *JOINT_GROUPS["right_arm"], *JOINT_GROUPS["right_hand"],
        )
        self.assertEqual(len(dataset_order), 43)
        self.assertEqual(
            tuple(name[len("left_hand_"):-len("_joint")]
                  for name in JOINT_GROUPS["left_hand"]),
            HAND_SUFFIXES,
        )

    def test_observation_is_single_rgb_frame_and_five_state_groups(self):
        metadata = sim_metadata()
        image = np.full((480, 640, 3), 127, dtype=np.uint8)
        payload = {
            "schema_version": VERSION,
            "prompt": "move the apple to the plate",
            "video": {"ego_view": [encode_image(image)]},
            "state": {
                key: [np.zeros(dim, dtype=np.float32).tolist()]
                for key, dim in STATE_DIMS.items()
            },
        }
        observation = observation_from_request(payload, metadata)
        self.assertEqual(observation["video"]["ego_view"].shape, (1, 1, 480, 640, 3))
        self.assertEqual(observation["state"]["left_hand"].shape, (1, 1, 7))

    def test_actions_require_all_40_step_outputs(self):
        metadata = sim_metadata()
        response = {
            "schema_version": VERSION,
            "action_representation": "absolute_physical",
            "actions": {
                key: np.zeros((len(ACTION_DELTA_INDICES), dim), dtype=np.float32).tolist()
                for key, dim in ACTION_DIMS.items()
            },
        }
        result = validate_actions(response, metadata, 5)
        self.assertEqual(result["left_hand"].shape, (5, 7))
        del response["actions"]["right_hand"]
        with self.assertRaisesRegex(ValueError, "right_hand"):
            validate_actions(response, metadata, 5)

    def test_config_uses_groot_http_and_accepts_legacy_url_key(self):
        cfg = G1ManipulationConfig.from_mapping({"wla_url": DEFAULT_GROOT_URL})
        self.assertEqual(cfg.groot_url, DEFAULT_GROOT_URL)

    def test_only_checkpoint_supported_task_is_exposed(self):
        self.assertEqual(DEFAULT_TASK, "apple_to_plate")
        self.assertEqual(set(TASKS), {"apple_to_plate"})
        self.assertEqual(TASKS[DEFAULT_TASK].instruction, "move the apple to the plate")

    def test_navigation_handoff_uses_kitchen_station(self):
        station = load_station()
        validate_handoff(*station.position_xy_m, station.yaw_rad)
        with self.assertRaisesRegex(ValueError, "交接"):
            validate_handoff(
                station.position_xy_m[0] + 0.2,
                station.position_xy_m[1], station.yaw_rad,
            )

    def test_stale_sonic_state_is_not_accepted_for_handoff(self):
        tools = object.__new__(G1ManipulationTools)
        tools.navigation_pose_provider = lambda: SimpleNamespace(
            pose=SimpleNamespace(x=3.35, y=-1.10, yaw=1.5707963267948966),
            monotonic_time=time.monotonic() - 2.0,
            root_qpos=None,
            joint_position_hardware=None,
            motion_active=False,
        )
        with self.assertRaisesRegex(RuntimeError, "状态已过期"):
            tools._navigation_sample()

    def test_navigation_mirror_forwards_full_sonic_state(self):
        delivered = threading.Event()
        root = tuple([1.2, -0.4, 0.79, 1.0, 0.0, 0.0, 0.0])
        joints = tuple(np.linspace(-0.2, 0.2, 29))

        class FakeEnv:
            def set_navigation_state(self, actual_root, actual_joints):
                np.testing.assert_allclose(actual_root, root)
                np.testing.assert_allclose(actual_joints, joints)
                delivered.set()

        tools = object.__new__(G1ManipulationTools)
        tools.env = FakeEnv()
        tools.navigation_pose_provider = lambda: SimpleNamespace(
            pose=SimpleNamespace(x=1.2, y=-0.4, yaw=0.0),
            monotonic_time=time.monotonic(),
            root_qpos=root,
            joint_position_hardware=joints,
            motion_active=True,
        )
        tools._pose_mirror_stop = threading.Event()
        thread = threading.Thread(target=tools._mirror_navigation_pose)
        thread.start()
        try:
            self.assertTrue(delivered.wait(0.5))
        finally:
            tools._pose_mirror_stop.set()
            thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())


class SceneTests(unittest.TestCase):
    def test_scene_has_one_official_head_camera_and_dex3(self):
        calibration = load_calibration()
        scene = ET.parse(SCENE).getroot()
        include = (SCENE.parent / scene.find("include").get("file")).resolve()
        robot = ET.parse(include).getroot()
        cameras = [camera for camera in robot.iter("camera")]
        self.assertEqual([camera.get("name") for camera in cameras], ["ego_view"])
        camera = cameras[0]
        np.testing.assert_allclose(
            [float(value) for value in camera.get("pos").split()],
            calibration["camera"]["pos_m"], atol=1e-9,
        )
        np.testing.assert_allclose(
            [float(value) for value in camera.get("xyaxes").split()],
            calibration["camera"]["xyaxes"], atol=1e-8,
        )
        joints = {joint.get("name") for joint in robot.iter("joint")}
        self.assertTrue(set(JOINT_GROUPS["left_hand"]) <= joints)
        self.assertTrue(set(JOINT_GROUPS["right_hand"]) <= joints)
        self.assertIsNotNone(scene.find(".//body[@name='task_apple']"))
        self.assertIsNotNone(scene.find(".//body[@name='target_plate_body']"))
        chair = scene.find(".//body[@name='online_chair']")
        self.assertIsNotNone(chair)
        self.assertEqual(chair.get("pos"), "3.35 1.22 0")
        self.assertEqual(len(chair.findall("geom")), 8)

    def test_scene_is_50hz_integral_and_hash_matches(self):
        scene = ET.parse(SCENE).getroot()
        timestep = float(scene.find("option").get("timestep"))
        self.assertAlmostEqual((1 / 50) / timestep, 10.0)
        include = (SCENE.parent / scene.find("include").get("file")).resolve()
        robot = ET.parse(include).getroot()
        text = robot.find("./custom/text[@name='calibration_sha256']")
        self.assertEqual(text.get("data"), calibration_hash(load_calibration()))


@unittest.skipUnless(importlib.util.find_spec("mujoco"), "需要 MuJoCo")
class PhysicsTests(unittest.TestCase):
    def setUp(self):
        from .runtime import G1ManipulationEnv
        self.env = G1ManipulationEnv(viewer_enabled=False)

    def tearDown(self):
        self.env.close()

    def test_state_and_actuator_scope_match_gn1x(self):
        self.assertEqual(
            {key: value.shape[0] for key, value in self.env.state().items()},
            STATE_DIMS,
        )
        self.assertEqual(self.env.model.nu, 43)
        self.assertEqual(len(self.env.navigation_joints), 29)
        self.assertEqual(
            tuple(self.env.model.joint(int(joint_id)).name
                  for joint_id in self.env.navigation_joints),
            SONIC_JOINTS,
        )

    def test_sonic_full_body_state_maps_into_visible_dex3_scene(self):
        joints = np.linspace(-0.2, 0.2, 29)
        root = np.asarray([1.2, -0.4, 0.79, 1.0, 0.0, 0.0, 0.0])
        self.env.set_navigation_state(root, joints)
        np.testing.assert_allclose(
            self.env.data.qpos[self.env.navigation_qadr], joints, atol=1e-9
        )
        np.testing.assert_allclose(
            self.env.model.body_pos[self.env.model.body("pelvis").id], root[:3],
            atol=1e-9,
        )

    def test_dex3_open_close_and_speed_limit(self):
        target = self.env.hold_target()
        target["left_hand"] = self.env.closed_hand_pose("left")
        before = self.env.data.ctrl[self.env.actuators["left_hand"]].copy()
        self.env.step(target)
        max_delta = self.env.calibration["execution"]["max_hand_speed_rad_s"] * self.env.dt
        self.assertLessEqual(
            float(np.max(np.abs(self.env.data.ctrl[self.env.actuators["left_hand"]] - before))),
            max_delta + 1e-9,
        )
        for _ in range(30):
            self.env.step(target)
        self.assertLess(self.env.hand_opening("left"), 0.15)
        target["left_hand"] = self.env.open_hand_pose("left")
        for _ in range(80):
            self.env.step(target)
        self.assertGreater(self.env.hand_opening("left"), 0.85)

    def test_policy_action_uses_only_upper_body_keys(self):
        target = self.env.policy_target(self.env.hold_target())
        self.assertEqual(set(target), set(JOINT_GROUPS))

    def test_camera_and_request_contract(self):
        rgb = self.env.render("ego_view")
        self.assertEqual(rgb.shape, (480, 640, 3))
        camera_id = self.env.model.camera("ego_view").id
        look = -self.env.data.cam_xmat[camera_id].reshape(3, 3)[:, 2]
        self.assertLess(look[2], -0.5)
        request = self.env.request(TASKS[DEFAULT_TASK].instruction, sim_metadata())
        self.assertEqual(set(request["state"]), set(STATE_DIMS))
        self.assertEqual(len(request["video"]["ego_view"]), 1)


if __name__ == "__main__":
    unittest.main()
