"""显式开启的真实物理 smoke；不连接云端、不调用模型。"""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from simulation.mujoco.vla.config import LEGACY_VLA_ENVIRONMENT as DEFAULT_VLA_ENVIRONMENT, load_vla_config
from simulation.mujoco.vla.runtime import PandaRuntime


@unittest.skipUnless(os.environ.get("CATHY_VLA_PHYSICS_TEST") == "1",
                     "需要 MuJoCo 图形驱动与 CATHY_VLA_PHYSICS_TEST=1")
class RealRendererTest(unittest.TestCase):
    def test_camera_can_render_and_close_on_worker_thread(self):
        # 独立测试 macOS 工作线程 CGL 路径，不依赖 robosuite 控制器版本。
        def render_on_worker():
            import mujoco
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            model = mujoco.MjModel.from_xml_string("""
                <mujoco><worldbody>
                  <light pos="0 0 3"/>
                  <camera name="agentview" pos="0 0 2"/>
                  <geom type="box" size="0.2 0.2 0.2" rgba="1 0 0 1"/>
                </worldbody></mujoco>
            """)
            runtime = PandaRuntime.__new__(PandaRuntime)
            runtime._viewer = None
            runtime.env = None
            runtime.data = mujoco.MjData(model)
            mujoco.mj_forward(model, runtime.data)
            runtime._render_options = mujoco.MjvOption()
            runtime._renderer = mujoco.Renderer(model, height=64, width=64)
            try:
                rgb = runtime._render_camera("agentview")
                self.assertEqual(rgb.shape, (64, 64, 3))
                self.assertEqual(rgb.dtype, np.uint8)
                self.assertGreater(np.ptp(rgb), 0)
            finally:
                runtime._cleanup()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(render_on_worker).result(timeout=30)


@unittest.skipUnless(os.environ.get("CATHY_VLA_PHYSICS_TEST") == "1",
                     "需要独立 robosuite 环境与 CATHY_VLA_PHYSICS_TEST=1")
class RealSceneTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_pandas_three_cubes_open_box_and_three_cameras(self):
        runtime = PandaRuntime(load_vla_config(DEFAULT_VLA_ENVIRONMENT), viewer_enabled=False)
        try:
            self.assertFalse(runtime.env.has_offscreen_renderer)
            self.assertFalse(runtime.env.use_camera_obs)
            before, _ = await runtime.snapshot()
            await runtime.step([0, 0, 0, 0, 0, 0, -1], "left", threading.Event())
            observation = await runtime.observe_policy("left")
            self.assertEqual(observation["state"].shape, (8,))
            self.assertEqual(observation["image"].shape, (256, 256, 3))
            self.assertEqual(runtime.env.action_dim, 14)
            state, images = await runtime.snapshot()
            self.assertEqual(state["episode_steps"], before["episode_steps"] + 1)
            self.assertGreater(state["sim_time"], before["sim_time"])
            self.assertEqual(len(images), 3)
            self.assertNotIn("objects", state)
            for color in ("red", "green", "blue"):
                self.assertGreaterEqual(runtime.env.sim.model.body_name2id(f"{color}_cube_main"), 0)
            self.assertGreaterEqual(runtime.env.sim.model.body_name2id("target_box"), 0)
            output = Path(__file__).resolve().parents[2] / "outputs" / "vla-smoke"
            output.mkdir(parents=True, exist_ok=True)
            for camera, png in images:
                (output / f"{camera}.png").write_bytes(png)
            print(f"三路相机帧: {output}")
        finally:
            runtime.close()
