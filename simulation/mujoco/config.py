"""自包含 MuJoCo 环境配置加载与交叉字段校验。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import MujocoConfigError

MODULE_ROOT = Path(__file__).resolve().parent
DEFAULT_ENVIRONMENT = "configs/environments/z1_three_cube.yaml"


def _mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MujocoConfigError(f"{name} 必须是 YAML 映射")
    return dict(value)


def resolve_resource(path: str | Path) -> Path:
    candidate = Path(path)
    target = candidate.resolve() if candidate.is_absolute() else (MODULE_ROOT / candidate).resolve()
    try:
        target.relative_to(MODULE_ROOT)
    except ValueError as exc:
        raise MujocoConfigError(f"资源路径不能逃逸 simulation/mujoco: {path}") from exc
    return target


def _load_yaml(path: str | Path) -> dict[str, Any]:
    resolved = resolve_resource(path)
    if not resolved.is_file():
        raise MujocoConfigError(f"配置文件不存在: {resolved}")
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    return _mapping(payload, name=str(resolved))


def _strings(value: Any, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
        raise MujocoConfigError(f"{name} 必须是非空字符串数组")
    return tuple(value)


def _floats(value: Any, *, name: str, length: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise MujocoConfigError(f"{name} 必须包含 {length} 个数值")
    try:
        parsed = tuple(float(x) for x in value)
    except (TypeError, ValueError) as exc:
        raise MujocoConfigError(f"{name} 必须包含 {length} 个数值") from exc
    if not all(math.isfinite(item) for item in parsed):
        raise MujocoConfigError(f"{name} 必须全部是有限数")
    return parsed


@dataclass(frozen=True)
class RobotConfig:
    robot_id: str
    world_frame: str
    base_body: str
    tcp_site: str
    arm_joints: tuple[str, ...]
    arm_actuators: tuple[str, ...]
    gripper_joint: str
    gripper_actuator: str
    gripper_open_control: float
    gripper_close_control: float
    home: tuple[float, ...]
    workspace_min: tuple[float, float, float]
    workspace_max: tuple[float, float, float]


@dataclass(frozen=True)
class ControllerConfig:
    control_hz: float
    max_duration_seconds: float
    position_tolerance_m: float
    orientation_tolerance_rad: float
    execution_position_tolerance_m: float
    execution_orientation_tolerance_rad: float
    max_iterations: int
    damping: float
    rotation_weight: float
    interpolation_step_limit: float
    default_down_rotation: tuple[float, ...]
    ik_seeds: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class CameraConfig:
    name: str
    width: int
    height: int
    primary: bool = False


@dataclass(frozen=True)
class ObservationConfig:
    cameras: tuple[CameraConfig, ...]


@dataclass(frozen=True)
class ViewerConfig:
    enabled: bool
    show_left_ui: bool
    show_right_ui: bool


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str
    command_timeout_seconds: float
    settle_seconds: float
    max_agent_actions: int
    max_sim_seconds: float
    viewer: ViewerConfig


@dataclass(frozen=True)
class MujocoEnvironmentConfig:
    environment_id: str
    model_path: Path
    expected_timestep_seconds: float
    robot: RobotConfig
    controller: ControllerConfig
    observation: ObservationConfig
    runtime: RuntimeConfig


def _parse_robot(raw: dict[str, Any]) -> RobotConfig:
    frames = _mapping(raw.get("frames"), name="robot.frames")
    arm = _mapping(raw.get("arm"), name="robot.arm")
    gripper = _mapping(raw.get("gripper"), name="robot.gripper")
    workspace = _mapping(raw.get("workspace"), name="robot.workspace")
    joints = _strings(arm.get("joints"), name="robot.arm.joints")
    actuators = _strings(arm.get("actuators"), name="robot.arm.actuators")
    if len(joints) != len(actuators):
        raise MujocoConfigError("arm joints 与 actuators 数量必须一致")
    home = _floats(raw.get("home"), name="robot.home", length=len(joints) + 1)
    low = _floats(workspace.get("min"), name="robot.workspace.min", length=3)
    high = _floats(workspace.get("max"), name="robot.workspace.max", length=3)
    if any(a >= b for a, b in zip(low, high)):
        raise MujocoConfigError("workspace.min 必须逐轴小于 workspace.max")
    result = RobotConfig(
        robot_id=str(raw.get("robot_id") or ""),
        world_frame=str(frames.get("world") or "world"),
        base_body=str(frames.get("base_body") or ""),
        tcp_site=str(frames.get("tcp_site") or ""),
        arm_joints=joints,
        arm_actuators=actuators,
        gripper_joint=str(gripper.get("joint") or ""),
        gripper_actuator=str(gripper.get("actuator") or ""),
        gripper_open_control=float(gripper.get("open_control")),
        gripper_close_control=float(gripper.get("close_control")),
        home=home,
        workspace_min=low,  # type: ignore[arg-type]
        workspace_max=high,  # type: ignore[arg-type]
    )
    required_names = {
        "robot_id": result.robot_id,
        "base_body": result.base_body,
        "tcp_site": result.tcp_site,
        "gripper_joint": result.gripper_joint,
        "gripper_actuator": result.gripper_actuator,
    }
    missing = [name for name, value in required_names.items() if not value]
    if missing:
        raise MujocoConfigError(f"robot 缺少必填名称: {', '.join(missing)}")
    if not all(
        math.isfinite(value)
        for value in (result.gripper_open_control, result.gripper_close_control)
    ):
        raise MujocoConfigError("gripper control 必须是有限数")
    return result


def _parse_controller(raw: dict[str, Any], arm_dof: int) -> ControllerConfig:
    ik = _mapping(raw.get("ik"), name="controller.ik")
    execution = _mapping(raw.get("execution") or {}, name="controller.execution")
    seeds_raw = ik.get("seeds") or []
    if not isinstance(seeds_raw, list):
        raise MujocoConfigError("controller.ik.seeds 必须是数组")
    seeds = tuple(
        _floats(seed, name=f"controller.ik.seeds[{index}]", length=arm_dof)
        for index, seed in enumerate(seeds_raw)
    )
    return ControllerConfig(
        control_hz=float(raw.get("control_hz", 50)),
        max_duration_seconds=float(raw.get("max_duration_seconds", 30)),
        position_tolerance_m=float(ik.get("position_tolerance_m", 0.005)),
        orientation_tolerance_rad=float(ik.get("orientation_tolerance_rad", 0.05)),
        execution_position_tolerance_m=float(
            execution.get("position_tolerance_m", 0.005)
        ),
        execution_orientation_tolerance_rad=float(
            execution.get("orientation_tolerance_rad", 0.05)
        ),
        max_iterations=int(ik.get("max_iterations", 180)),
        damping=float(ik.get("damping", 0.0001)),
        rotation_weight=float(ik.get("rotation_weight", 0.2)),
        interpolation_step_limit=float(ik.get("interpolation_step_limit", 0.18)),
        default_down_rotation=_floats(
            raw.get("default_down_rotation"),
            name="controller.default_down_rotation",
            length=9,
        ),
        ik_seeds=seeds,
    )


def _parse_observation(raw: dict[str, Any]) -> ObservationConfig:
    cameras_raw = raw.get("cameras")
    if not isinstance(cameras_raw, list) or not cameras_raw:
        raise MujocoConfigError("observation.cameras 必须是非空数组")
    cameras = tuple(
        CameraConfig(
            name=str(_mapping(item, name="camera").get("name") or ""),
            width=int(item.get("width", 960)),
            height=int(item.get("height", 720)),
            primary=bool(item.get("primary", False)),
        )
        for item in cameras_raw
    )
    if any(not camera.name or camera.width <= 0 or camera.height <= 0 for camera in cameras):
        raise MujocoConfigError("camera name/width/height 不合法")
    if len({camera.name for camera in cameras}) != len(cameras):
        raise MujocoConfigError("camera name 不能重复")
    if sum(camera.primary for camera in cameras) != 1:
        raise MujocoConfigError("必须且只能配置一个 primary camera")
    return ObservationConfig(cameras=cameras)


def load_environment_config(
    path: str | Path = DEFAULT_ENVIRONMENT,
) -> MujocoEnvironmentConfig:
    raw = _load_yaml(path)
    if int(raw.get("schema_version", 0)) != 1:
        raise MujocoConfigError("只支持 schema_version=1")
    includes = _mapping(raw.get("includes"), name="environment.includes")
    robot_raw = _load_yaml(str(includes.get("robot") or ""))
    robot = _parse_robot(robot_raw)
    controller = _parse_controller(
        _load_yaml(str(includes.get("controller") or "")),
        len(robot.arm_joints),
    )
    observation = _parse_observation(
        _load_yaml(str(includes.get("observation") or ""))
    )
    runtime_raw = _mapping(raw.get("runtime") or {}, name="environment.runtime")
    reset_raw = _mapping(raw.get("reset") or {}, name="environment.reset")
    episode_raw = _mapping(raw.get("episode") or {}, name="environment.episode")
    viewer_raw = _mapping(runtime_raw.get("viewer") or {}, name="runtime.viewer")
    runtime = RuntimeConfig(
        mode=str(runtime_raw.get("mode") or "stepped"),
        command_timeout_seconds=float(runtime_raw.get("command_timeout_seconds", 15)),
        settle_seconds=float(reset_raw.get("settle_seconds", 0.5)),
        max_agent_actions=int(episode_raw.get("max_agent_actions", 100)),
        max_sim_seconds=float(episode_raw.get("max_sim_seconds", 120)),
        viewer=ViewerConfig(
            enabled=bool(viewer_raw.get("enabled", True)),
            show_left_ui=bool(viewer_raw.get("show_left_ui", True)),
            show_right_ui=bool(viewer_raw.get("show_right_ui", True)),
        ),
    )
    if runtime.mode != "stepped":
        raise MujocoConfigError("第一版只支持 stepped runtime")
    if not str(raw.get("environment_id") or "").strip():
        raise MujocoConfigError("environment_id 不能为空")
    positive_values = (
        controller.control_hz,
        controller.max_duration_seconds,
        controller.position_tolerance_m,
        controller.orientation_tolerance_rad,
        controller.execution_position_tolerance_m,
        controller.execution_orientation_tolerance_rad,
        controller.max_iterations,
        controller.damping,
        controller.interpolation_step_limit,
        runtime.command_timeout_seconds,
        runtime.max_agent_actions,
        runtime.max_sim_seconds,
    )
    if not all(math.isfinite(float(value)) and value > 0 for value in positive_values):
        raise MujocoConfigError("频率、容差、预算、迭代次数与超时必须是正有限数")
    model_path = resolve_resource(str(raw.get("model_path") or ""))
    if not model_path.is_file():
        raise MujocoConfigError(f"MJCF 不存在: {model_path}")
    return MujocoEnvironmentConfig(
        environment_id=str(raw.get("environment_id") or ""),
        model_path=model_path,
        expected_timestep_seconds=float(raw.get("expected_timestep_seconds", 0.002)),
        robot=robot,
        controller=controller,
        observation=observation,
        runtime=runtime,
    )
