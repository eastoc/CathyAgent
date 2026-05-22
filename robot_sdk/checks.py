"""``RobotModel`` 校验工具。

在导出 MJCF 或跑仿真前调用 ``check_robot_model``。与「遇错即停」不同，
这里会把所有 ``CheckIssue`` 收集到 ``CheckReport``，方便一次性列出全部问题。

校验层次（按顺序）：

1. **名称唯一** — link / joint / actuator / sensor 不能重名
2. **引用合法** — 关节、执行器、传感器必须指向已存在的实体
3. **关节规则** — 运动轴、限位与关节类型要匹配
4. **Link 完整性** — 缺少 visual / collision / inertial 时给出 warning
5. **运动学树**（仅 ``strict=True``）— 恰好一个 root link，且全树连通
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Box, Cylinder, Geometry, Joint, Mesh, RobotModel, Sphere


@dataclass(frozen=True)
class CheckIssue:
    """``check_robot_model`` 产生的一条校验结果。"""

    severity: str  # 严重级别："error" 阻断导出，"warning" 仅提示
    code: str  # 稳定错误码，便于程序处理，如 "missing_parent"
    message: str  # 给人看的说明文字


@dataclass
class CheckReport:
    """校验报告：累积 issue，并提供 JSON 序列化。"""

    issues: list[CheckIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """没有 error 时为 True（warning 不影响 ok）。"""

        return not any(issue.severity == "error" for issue in self.issues)

    def error(self, code: str, message: str) -> None:
        """记录一条阻断性错误。"""

        self.issues.append(CheckIssue("error", code, message))

    def warning(self, code: str, message: str) -> None:
        """记录一条非阻断性警告。"""

        self.issues.append(CheckIssue("warning", code, message))

    def to_dict(self) -> dict:
        """序列化为 dict，供 plugin 返回或日志使用。"""

        return {
            "ok": self.ok,
            "issues": [
                {"severity": issue.severity, "code": issue.code, "message": issue.message}
                for issue in self.issues
            ],
        }

    def assert_ok(self) -> None:
        """存在 error 时抛出 ``ValueError``。"""

        if not self.ok:
            errors = [issue.message for issue in self.issues if issue.severity == "error"]
            raise ValueError("; ".join(errors))


def check_robot_model(model: RobotModel, *, strict: bool = True) -> CheckReport:
    """校验 ``RobotModel``，导出或仿真前应调用。

    ``strict=True``：要求单一连通运动学树（正式导出推荐）。
    ``strict=False``：允许未连完的半成品模型，仍检查引用与几何基础项。
    """

    report = CheckReport()

    # --- 基础结构：至少有一个 link ---
    if not model.links:
        report.error("no_links", "robot must contain at least one link")
        return report

    # --- 名称唯一性：四类实体各自不能重名 ---
    link_names = [link.name for link in model.links]
    _check_unique(link_names, "link", report)
    joint_names = [joint.name for joint in model.joints]
    _check_unique(joint_names, "joint", report)
    actuator_names = [actuator.name for actuator in model.actuators]
    _check_unique(actuator_names, "actuator", report)
    sensor_names = [sensor.name for sensor in model.sensors]
    _check_unique(sensor_names, "sensor", report)

    link_set = set(link_names)
    joint_set = set(joint_names)
    # child -> joint：记录每个 link 由哪个关节连到父 link（每个 child 最多一个父关节）
    child_to_joint: dict[str, str] = {}
    # parent -> [children]：邻接表，strict 模式下做连通性 DFS
    parent_to_children: dict[str, list[str]] = {name: [] for name in link_set}

    # --- 关节：parent/child 引用、单父约束、轴与 limit 规则 ---
    for joint in model.joints:
        if joint.parent not in link_set:
            report.error("missing_parent", f"joint {joint.name!r} references missing parent link {joint.parent!r}")
        if joint.child not in link_set:
            report.error("missing_child", f"joint {joint.name!r} references missing child link {joint.child!r}")
        if joint.child in child_to_joint:
            report.error(
                "multiple_parent_joints",
                f"link {joint.child!r} has multiple parent joints: {child_to_joint[joint.child]!r} and {joint.name!r}",
            )
        child_to_joint[joint.child] = joint.name
        parent_to_children.setdefault(joint.parent, []).append(joint.child)
        _check_joint(joint, report)

    # --- 执行器：必须绑定到已存在的 joint ---
    for actuator in model.actuators:
        if actuator.joint not in joint_set:
            report.error("actuator_missing_joint", f"actuator {actuator.name!r} references missing joint {actuator.joint!r}")

    # --- 传感器：jointpos/jointvel 指向 joint；其余类型指向 link ---
    for sensor in model.sensors:
        if sensor.sensor_type in {"jointpos", "jointvel"} and sensor.target not in joint_set:
            report.error("sensor_missing_joint", f"sensor {sensor.name!r} references missing joint {sensor.target!r}")
        if sensor.sensor_type in {"imu", "force", "touch", "camera"} and sensor.target not in link_set:
            report.error("sensor_missing_link", f"sensor {sensor.name!r} references missing link {sensor.target!r}")

    # --- link：visual/collision/inertial 完整性（warning）+ 几何尺寸 ---
    for link in model.links:
        if not link.visuals:
            report.warning("link_without_visual", f"link {link.name!r} has no visuals")
        if not link.collisions:
            report.warning("link_without_collision", f"link {link.name!r} has no collisions")
        if link.inertial is None:
            report.warning("link_without_inertial", f"link {link.name!r} has no inertial")
        for visual in link.visuals:
            _check_geometry(visual.geometry, f"link {link.name!r} visual", report)
        for collision in link.collisions:
            _check_geometry(collision.geometry, f"link {link.name!r} collision", report)

    # --- strict：单 root + 全 link 可达 ---
    if strict:
        _check_tree_connectivity(link_set, child_to_joint, parent_to_children, report)

    return report


def _check_unique(names: list[str], label: str, report: CheckReport) -> None:
    """同一类别内名称去重。"""

    seen: set[str] = set()
    for name in names:
        if name in seen:
            report.error(f"duplicate_{label}", f"duplicate {label} name: {name!r}")
        seen.add(name)


def _check_joint(joint: Joint, report: CheckReport) -> None:
    """按关节类型检查运动轴与 limit 是否合法。"""

    # 有运动自由度的关节，axis 不能是零向量
    if joint.joint_type in {"revolute", "continuous", "prismatic"}:
        if sum(float(v) ** 2 for v in joint.axis) <= 0.0:
            report.error("zero_joint_axis", f"joint {joint.name!r} axis must be non-zero")
    # 有限位的旋转/平移关节必须提供 limit
    if joint.joint_type in {"revolute", "prismatic"} and joint.limit is None:
        report.error("missing_joint_limit", f"joint {joint.name!r} requires a limit")
    # continuous 可以没有 limit，但不能设 lower/upper
    if joint.joint_type == "continuous" and joint.limit is not None:
        if joint.limit.lower is not None or joint.limit.upper is not None:
            report.error("continuous_joint_limits", f"continuous joint {joint.name!r} cannot set lower/upper limits")
    # fixed / ball / free 不支持 limit
    if joint.joint_type in {"fixed", "ball", "free"} and joint.limit is not None:
        report.error("unsupported_joint_limit", f"joint {joint.name!r} type {joint.joint_type!r} does not support limits")


def _check_geometry(geometry: Geometry, context: str, report: CheckReport) -> None:
    """检查基本几何体尺寸为正，mesh 必须有 filename。"""

    if isinstance(geometry, Box):
        if any(v <= 0.0 for v in geometry.size):
            report.error("invalid_box", f"{context} box size values must be positive")
    elif isinstance(geometry, Cylinder):
        if geometry.radius <= 0.0 or geometry.length <= 0.0:
            report.error("invalid_cylinder", f"{context} cylinder radius/length must be positive")
    elif isinstance(geometry, Sphere):
        if geometry.radius <= 0.0:
            report.error("invalid_sphere", f"{context} sphere radius must be positive")
    elif isinstance(geometry, Mesh):
        if not geometry.filename:
            report.error("invalid_mesh", f"{context} mesh filename is required")
    else:
        report.error("unknown_geometry", f"{context} has unsupported geometry {type(geometry).__name__}")


def _check_tree_connectivity(
    link_set: set[str],
    child_to_joint: dict[str, str],
    parent_to_children: dict[str, list[str]],
    report: CheckReport,
) -> None:
    """要求恰好一个 root link，且所有 link 在同一棵连通树上。"""

    # 没有作为 child 出现的 link 就是 root（挂在 world 上）
    roots = sorted(link_set - set(child_to_joint))
    if not roots:
        report.error("no_root_link", "robot has no root link")
        return
    if len(roots) > 1:
        report.error("multiple_root_links", f"robot must have exactly one root link; found {roots}")
        return

    # 从唯一 root 向下 DFS，未访问到的 link 说明孤立或成环外分支
    visited: set[str] = set()
    stack = [roots[0]]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        stack.extend(parent_to_children.get(current, []))
    if visited != link_set:
        report.error("unreachable_links", f"robot has unreachable links: {sorted(link_set - visited)}")
