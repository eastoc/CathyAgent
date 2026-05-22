"""Robot SDK 插件：生成 scaffold、校验模型、导出 MJCF/URDF。

典型工作流：create_robot_template → 编辑 robot_model.py → compile_robot_model；
结构复杂时先用 probe_robot_model 看清 link/joint 拓扑再改。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from cathy.plugins import PluginError, ToolPlugin
from robot_sdk import Mesh, RobotModel, check_robot_model, export_mjcf, export_urdf
from robot_sdk.sim.mujoco import smoke_test_mjcf

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_FILES = {
    "empty": "empty.py",
    "two_link": "two_link.py",
    "three_dof_arm": "three_dof_arm.py",
}

_ISSUE_SUGGESTIONS = {
    "no_links": "Create at least one root link, for example robot.link('base').",
    "missing_joint_limit": "Add JointLimit to revolute/prismatic joints.",
    "multiple_root_links": "Connect isolated links with joints so the robot has exactly one root link.",
    "no_root_link": "Break the cycle or choose one base link that is not a joint child.",
    "unreachable_links": "Connect every link into the same parent-child joint tree.",
    "missing_parent": "Fix the joint parent name or create the referenced parent link.",
    "missing_child": "Fix the joint child name or create the referenced child link.",
    "actuator_missing_joint": "Bind each actuator to an existing joint name.",
    "sensor_missing_joint": "Bind joint position/velocity sensors to existing joint names.",
    "sensor_missing_link": "Bind body sensors to existing link names.",
    "zero_joint_axis": "Use a non-zero axis vector, usually (0, 0, 1), (0, 1, 0), or (1, 0, 0).",
}


class RobotSdkPlugin(ToolPlugin):
    """Robot SDK 工具插件，所有读写路径限制在当前 session 工作空间内。

    workspace 分层：
    - _workspace_parent：配置里的 SANDBOX.workspace_root（如 CAD/）
    - _workspace_root：当前会话目录 <parent>/<session_id>/，由 attach_session 切换
    """

    def __init__(self) -> None:
        self._workspace_parent: Path | None = None
        self._workspace_root: Path | None = None

    def initialize(self, config: dict[str, Any]) -> None:
        # workspace_root 由 cli._build_plugin_configs 从 SANDBOX 段注入。
        root = Path(str(config.get("workspace_root") or "workspaces")).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._workspace_parent = root
        self._workspace_root = root

    def attach_session(self, session_id: str) -> None:
        """切换到会话专属工作空间：<workspace_root>/<session_id>/。"""
        if self._workspace_parent is None:
            raise PluginError("robot_sdk plugin is not initialized")
        sid = (session_id or "").strip()
        if not sid:
            raise PluginError("session_id cannot be empty")
        root = (self._workspace_parent / sid).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._workspace_root = root

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "create_robot_template":
            return self._create_robot_template(**params)
        if tool_name == "compile_robot_model":
            return self._compile_robot_model(**params)
        if tool_name == "probe_robot_model":
            return self._probe_robot_model(**params)
        raise PluginError(f"unknown tool: {tool_name}")

    def _create_robot_template(
        self,
        path: str = "robot_model.py",
        *,
        template: str = "two_link",
        overwrite: bool = False,
        **_: Any,
    ) -> str:
        """生成模板 scaffold：把内置 scaffold 复制到工作空间，供 Agent 后续直接编辑。"""
        target = self._resolve_in_workspace(path)
        if target.exists() and not overwrite:
            raise PluginError(f"file already exists: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        template_text = _load_template(template)
        target.write_text(template_text, encoding="utf-8")
        return json.dumps(
            {
                "ok": True,
                "path": target.relative_to(self._workspace_root).as_posix(),  # type: ignore[arg-type]
                "template": template,
                "available_templates": sorted(_TEMPLATE_FILES),
            },
            ensure_ascii=False,
        )

    def _compile_robot_model(
        self,
        path: str = "robot_model.py",
        *,
        output_dir: str = "build/robot",
        strict: bool = True,
        run_smoke_test: bool = False,
        smoke_steps: int = 200,
        **_: Any,
    ) -> str:
        """编译成 MJCF/URDF
        加载 robot_model.py，校验通过后导出 MJCF/URDF 与 checks 报告。
        校验失败时仍写入 .checks.json，但不生成仿真文件（mjcf_path/urdf_path 为 null）。
        """
        script_path = self._resolve_in_workspace(path)
        if not script_path.exists():
            raise PluginError(f"robot model script does not exist: {path}")
        if script_path.is_dir():
            raise PluginError(f"robot model path is a directory: {path}")

        model = _load_robot_model(script_path)
        report = check_robot_model(model, strict=bool(strict))

        out_dir = self._resolve_in_workspace(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        mjcf_path = out_dir / f"{model.name}.xml"
        urdf_path = out_dir / f"{model.name}.urdf"
        report_path = out_dir / f"{model.name}.checks.json"

        summary = _report_summary(report)
        obj_paths = _mesh_asset_paths(model, self._workspace_root) if report.ok and self._workspace_root else []
        # 仅在校验通过时导出仿真文件；报告始终落盘，便于 Agent 按 issue 修复。
        if report.ok:
            export_mjcf(model, mjcf_path)
            export_urdf(model, urdf_path)
        report_payload = report.to_dict()
        report_payload["summary"] = summary
        report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        smoke = None
        if report.ok and run_smoke_test:
            smoke = smoke_test_mjcf(mjcf_path, steps=int(smoke_steps)).to_dict()

        payload = {
            "ok": report.ok,
            "robot": model.name,
            "links": [link.name for link in model.links],
            "joints": [joint.name for joint in model.joints],
            "actuators": [actuator.name for actuator in model.actuators],
            "mjcf_path": mjcf_path.relative_to(self._workspace_root).as_posix() if report.ok else None,  # type: ignore[arg-type]
            "urdf_path": urdf_path.relative_to(self._workspace_root).as_posix() if report.ok else None,  # type: ignore[arg-type]
            "obj_paths": obj_paths if report.ok else [],
            "report_path": report_path.relative_to(self._workspace_root).as_posix(),  # type: ignore[arg-type]
            "checks": report_payload,
            "summary": summary,
            "smoke_test": smoke,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _probe_robot_model(
        self,
        path: str = "robot_model.py",
        *,
        strict: bool = True,
        **_: Any,
    ) -> str:
        """只读探测：汇总 link/joint 拓扑、缺失项和校验结果，不导出文件。
        分析已有模型结构
        用于 parent/child 关系复杂或模型较大时，让 Agent 先理解结构再编辑。
        """
        script_path = self._resolve_in_workspace(path)
        if not script_path.exists():
            raise PluginError(f"robot model script does not exist: {path}")
        if script_path.is_dir():
            raise PluginError(f"robot model path is a directory: {path}")

        model = _load_robot_model(script_path)
        report = check_robot_model(model, strict=bool(strict))
        # 通过 joint 的 parent/child 关系推断根 link 与末端 link。
        child_links = {joint.child for joint in model.joints}
        roots = [link.name for link in model.links if link.name not in child_links]
        parent_links = {joint.parent for joint in model.joints}
        leaves = sorted(link.name for link in model.links if link.name not in parent_links)
        actuated_joints = {actuator.joint for actuator in model.actuators}
        total_mass = sum(link.inertial.mass for link in model.links if link.inertial is not None)
        payload = {
            "ok": report.ok,
            "robot": model.name,
            "root_links": roots,
            "leaf_links": leaves,
            "joint_chain": _joint_chain(model),
            "total_mass": total_mass,
            "missing_actuator_joints": sorted(
                joint.name for joint in model.joints if joint.joint_type != "fixed" and joint.name not in actuated_joints
            ),
            "links_without_visual": sorted(link.name for link in model.links if not link.visuals),
            "links_without_collision": sorted(link.name for link in model.links if not link.collisions),
            "links_without_inertial": sorted(link.name for link in model.links if link.inertial is None),
            "links": [
                {
                    "name": link.name,
                    "visuals": len(link.visuals),
                    "collisions": len(link.collisions),
                    "mass": link.inertial.mass if link.inertial is not None else None,
                }
                for link in model.links
            ],
            "joints": [
                {
                    "name": joint.name,
                    "type": joint.joint_type,
                    "parent": joint.parent,
                    "child": joint.child,
                    "axis": joint.axis,
                    "has_limit": joint.limit is not None,
                    "has_dynamics": joint.dynamics is not None,
                }
                for joint in model.joints
            ],
            "actuators": [
                {"name": actuator.name, "joint": actuator.joint, "type": actuator.actuator_type}
                for actuator in model.actuators
            ],
            "sensors": [
                {"name": sensor.name, "type": sensor.sensor_type, "target": sensor.target}
                for sensor in model.sensors
            ],
            "checks": report.to_dict(),
            "summary": _report_summary(report),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _resolve_in_workspace(self, path: str) -> Path:
        """把相对路径解析到 _workspace_root 下，禁止 .. 等方式越界。"""
        if self._workspace_root is None:
            raise PluginError("robot_sdk plugin is not initialized")
        if not path:
            raise PluginError("path cannot be empty")
        target = (self._workspace_root / path).resolve()
        if target != self._workspace_root and self._workspace_root not in target.parents:
            raise PluginError(f"path escapes workspace: {path}")
        return target


def _load_robot_model(path: Path) -> RobotModel:
    module = _load_module(path)
    old_cwd = Path.cwd()
    try:
        os.chdir(path.parent)
        builder = getattr(module, "build_robot_model", None)
        if callable(builder):
            model = builder()
        else:
            model = getattr(module, "robot_model", None)
    finally:
        os.chdir(old_cwd)
    if not isinstance(model, RobotModel):
        raise PluginError("script must define build_robot_model() -> RobotModel or robot_model = RobotModel(...)")
    return model


def _load_module(path: Path) -> ModuleType:
    module_name = f"_cathy_robot_model_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"cannot load robot model script: {path}")
    module = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    old_cwd = Path.cwd()
    try:
        sys.path.insert(0, str(path.parent))
        os.chdir(path.parent)
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginError(f"robot model script failed: {type(exc).__name__}: {exc}") from exc
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
    return module


def _load_template(name: str) -> str:
    key = str(name or "two_link").strip()
    filename = _TEMPLATE_FILES.get(key)
    if filename is None:
        available = ", ".join(sorted(_TEMPLATE_FILES))
        raise PluginError(f"unknown robot template: {key!r}; available: {available}")
    path = _TEMPLATE_DIR / filename
    if not path.exists():
        raise PluginError(f"robot template is missing: {key}")
    return path.read_text(encoding="utf-8")


def _report_summary(report) -> dict[str, Any]:
    errors = [issue for issue in report.issues if issue.severity == "error"]
    warnings = [issue for issue in report.issues if issue.severity == "warning"]
    suggestions = []
    seen: set[str] = set()
    for issue in errors:
        suggestion = _ISSUE_SUGGESTIONS.get(issue.code)
        if suggestion and suggestion not in seen:
            suggestions.append({"code": issue.code, "suggestion": suggestion})
            seen.add(suggestion)
    if errors:
        next_actions = [
            "Fix blocking errors in summary.errors before tuning warnings.",
            "Edit robot_model.py, then run compile_robot_model again.",
        ]
    elif warnings:
        next_actions = [
            "Review warnings and decide whether to add missing visual, collision, or inertial data.",
            "If warnings are intentional, the MJCF/URDF artifacts are still generated.",
        ]
    else:
        next_actions = [
            "Use mjcf_path, urdf_path, and report_path as the generated artifacts.",
            "If further model edits are needed, re-run compile_robot_model afterward.",
        ]
    return {
        "status": "success" if report.ok else "error",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "blocking_errors": [{"code": issue.code, "message": issue.message} for issue in errors],
        "errors": [{"code": issue.code, "message": issue.message} for issue in errors],
        "warnings": [{"code": issue.code, "message": issue.message} for issue in warnings],
        "suggestions": suggestions,
        "next_actions": next_actions,
    }


def _mesh_asset_paths(model: RobotModel, workspace_root: Path) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for link in model.links:
        geometries = [visual.geometry for visual in link.visuals]
        geometries.extend(collision.geometry for collision in link.collisions)
        for geometry in geometries:
            if not isinstance(geometry, Mesh) or not geometry.materialized_path:
                continue
            path = Path(geometry.materialized_path).expanduser()
            try:
                value = path.resolve().relative_to(workspace_root).as_posix()
            except ValueError:
                value = path.as_posix()
            if value not in seen:
                paths.append(value)
                seen.add(value)
    return paths


def _joint_chain(model: RobotModel) -> list[dict[str, str]]:
    return [
        {
            "joint": joint.name,
            "type": joint.joint_type,
            "parent": joint.parent,
            "child": joint.child,
        }
        for joint in model.joints
    ]
