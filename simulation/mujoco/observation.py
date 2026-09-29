"""Z1 状态快照、相机标定和 RGB Artifact 生成。"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import ImageBlock

from .backend import MujocoBackend
from .config import CameraConfig, ObservationConfig
from .errors import MujocoExecutionError


def encode_png(rgb: np.ndarray) -> bytes:
    image = np.asarray(rgb)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("RGB 图像必须是 H×W×3 uint8")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    height, width = image.shape[:2]
    scanlines = b"".join(b"\x00" + row.tobytes() for row in image)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


@dataclass(frozen=True)
class ObservationResult:
    state: dict[str, Any]
    images: tuple[ImageBlock, ...]


class Z1ObservationProvider:
    def __init__(
        self,
        backend: MujocoBackend,
        config: ObservationConfig,
        artifact_store: LocalArtifactStore,
    ) -> None:
        self.backend = backend
        self.config = config
        self.artifact_store = artifact_store
        self._cameras = {camera.name: camera for camera in config.cameras}
        self._renderers: dict[tuple[int, int], Any] = {}

    def _camera_info(self, camera: CameraConfig) -> dict[str, Any]:
        model, data = self.backend.model, self.backend.data
        camera_id = model.camera(camera.name).id
        focal = camera.height / (
            2 * math.tan(math.radians(model.cam_fovy[camera_id]) / 2)
        )
        rotation = data.cam_xmat[camera_id].reshape(3, 3)
        attached_body = model.body(model.cam_bodyid[camera_id]).name
        return {
            "name": camera.name,
            "width": camera.width,
            "height": camera.height,
            "attached_body": attached_body,
            "K": [
                [focal, 0, camera.width / 2],
                [0, focal, camera.height / 2],
                [0, 0, 1],
            ],
            "position_world": data.cam_xpos[camera_id].tolist(),
            "rotation_world_from_camera": rotation.tolist(),
            "rotation_world_from_opencv_camera": (
                rotation @ np.diag([1, -1, -1])
            ).tolist(),
            "intrinsics_convention": (
                "K maps OpenCV (+X right, +Y down, +Z forward) to pixels"
            ),
            "camera_axes": "+X right, +Y up, -Z forward (MuJoCo/OpenGL)",
            "mount": "eye-in-hand" if camera.name == "wrist" else "eye-to-hand",
        }

    def capture(
        self,
        *,
        snapshot_id: int,
        cameras: Sequence[str] | None,
        render: bool,
    ) -> ObservationResult:
        requested = tuple(cameras or (camera.name for camera in self.config.cameras))
        unknown = sorted(set(requested) - set(self._cameras))
        if unknown:
            raise MujocoExecutionError(f"未知相机: {', '.join(unknown)}")
        primary = next(camera.name for camera in self.config.cameras if camera.primary)
        backend = self.backend
        state: dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "sim_time": backend.sim_time,
            "frame": backend.config.robot.world_frame,
            "length_unit": "m",
            "angle_unit": "rad",
            "joint_names": list(backend.joint_names),
            "joint_positions": backend.robot_qpos().tolist(),
            "joint_velocities": backend.robot_qvel().tolist(),
            "actuator_controls": backend.controls().tolist(),
            "tcp_position": backend.tcp_position().tolist(),
            "tcp_rotation_world": backend.tcp_rotation().tolist(),
            "gripper_opening_command": float(
                (backend.controls()[-1] - backend.config.robot.gripper_close_control)
                / (
                    backend.config.robot.gripper_open_control
                    - backend.config.robot.gripper_close_control
                )
            ),
            "cameras": list(requested),
            "primary_camera": primary,
            "camera_calibration": {
                name: self._camera_info(self._cameras[name]) for name in requested
            },
        }
        if not render:
            return ObservationResult(state=state, images=())

        image_blocks: list[ImageBlock] = []
        for name in requested:
            camera = self._cameras[name]
            try:
                key = (camera.width, camera.height)
                renderer = self._renderers.get(key)
                if renderer is None:
                    renderer = backend.mujoco.Renderer(
                        backend.model,
                        height=camera.height,
                        width=camera.width,
                    )
                    self._renderers[key] = renderer
                renderer.update_scene(backend.data, camera=name)
                rgb = renderer.render().copy()
            except Exception as exc:
                raise MujocoExecutionError(
                    "RGB 渲染失败；macOS 无图形会话时请在桌面终端运行，"
                    "无 GPU 的 Linux 请配置 EGL/OSMesa，或传 render=false"
                ) from exc
            ref = self.artifact_store.put_bytes(
                encode_png(rgb),
                mime_type="image/png",
                filename=f"{backend.config.environment_id}-{snapshot_id}-{name}.png",
                width=camera.width,
                height=camera.height,
            )
            image_blocks.append(
                ImageBlock(
                    ref,
                    detail="high",
                    source=name,
                    frame_id=f"snapshot:{snapshot_id}:{name}",
                )
            )
        return ObservationResult(state=state, images=tuple(image_blocks))

    def close(self) -> None:
        for renderer in self._renderers.values():
            renderer.close()
        self._renderers.clear()
