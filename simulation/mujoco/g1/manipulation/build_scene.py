"""从 Unitree 官方 G1 29-DoF + Dex3-1 派生全身可视、固定基座的操作场景。"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from .contract import (
    CALIBRATION, JOINT_GROUPS, SONIC_JOINTS, calibration_hash, load_calibration,
)
from .tasks import DEFAULT_TASK, TASKS, load_station

ASSETS = Path(__file__).resolve().parents[2] / "assets"
UPSTREAM = ASSETS / "g1_dex3" / "upstream"
DESTINATION = ASSETS / "g1_dex3"


def values(items):
    return " ".join(str(v) for v in items)


def camera_xyaxes(position, target):
    """生成朝向目标且保持世界 Z 向上的 MuJoCo camera xyaxes。"""
    import numpy as np
    forward = np.asarray(target, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    camera_z = -forward
    up = np.cross(camera_z, right)
    return (*right, *up)


def _indent(element, level=0):
    if hasattr(ET, "indent"):
        ET.indent(element, space="  ")
        return
    whitespace = "\n" + level * "  "
    child_whitespace = "\n" + (level + 1) * "  "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = child_whitespace
        for child in element:
            _indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = child_whitespace
        element[-1].tail = whitespace


def _add_task_assets(world):
    task = TASKS[DEFAULT_TASK]
    apple = ET.SubElement(
        world, "body", name=task.object_body, pos=values(task.object_spawn_pos_m)
    )
    ET.SubElement(apple, "freejoint", name=task.object_joint)
    ET.SubElement(
        apple, "geom", name="task_apple_geom", type="sphere", size="0.038",
        mass="0.11", rgba="0.82 0.04 0.025 1", friction="1.5 0.02 0.002",
        condim="4", solref="0.01 1",
    )
    ET.SubElement(
        apple, "geom", name="task_apple_stem", type="capsule",
        fromto="0 0 0.030 0 0 0.054", size="0.004", mass="0.003",
        rgba="0.22 0.10 0.025 1", friction="1.3 0.01 0.001",
    )

    plate = ET.SubElement(
        world, "body", name=task.target_body, mocap="true",
        pos=values(task.target_pos_m),
    )
    ET.SubElement(
        plate, "geom", name="target_plate_geom", type="cylinder",
        pos="0 0 0.008", size="0.105 0.008", rgba="0.90 0.92 0.94 1",
        friction="1.3 0.02 0.002",
    )
    ET.SubElement(
        plate, "site", name=task.target_site, type="cylinder", pos="0 0 0.055",
        size="0.085 0.045", rgba="0.2 0.9 0.3 0.25", group="4",
    )


def build_scene(calibration_path=CALIBRATION, output_dir=DESTINATION):
    cfg = load_calibration(calibration_path)
    station = load_station()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = UPSTREAM / "g1_body29_hand14.xml"
    mesh_dir = UPSTREAM / "meshes"
    if not source.is_file() or not mesh_dir.is_dir():
        raise FileNotFoundError(
            "缺少 Unitree Dex3 资产；请将 unitree_lerobot/eval_robot/assets/g1 "
            f"完整复制到 {UPSTREAM}"
        )
    root = ET.parse(source).getroot()
    root.set("model", "g1_sonic_visual_dex3")
    # 上游文件末尾自带独立预览地面和灯光；厨房场景已有同名元素，只保留机器人部分。
    for element in [*root.findall("statistic"), *root.findall("visual")]:
        root.remove(element)
    for element in root.findall("asset")[1:]:
        root.remove(element)
    for element in root.findall("worldbody")[1:]:
        root.remove(element)
    upper_joints = {name for names in JOINT_GROUPS.values() for name in names}
    visual_joints = upper_joints | set(SONIC_JOINTS)
    # Arena 策略仍只控制上身和 Dex3；保留 SONIC 的 12 个腿关节用于导航
    # 可视化。只移除 floating base，根位姿通过 pelvis body pose 镜像。
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "freejoint" or (
                child.tag == "joint" and child.get("name")
                and child.get("name") not in visual_joints
            ):
                parent.remove(child)
    actuators = root.find("actuator")
    for actuator in list(actuators):
        joint_name = actuator.get("joint")
        if joint_name not in visual_joints:
            actuators.remove(actuator)
            continue
        joint = root.find(f".//joint[@name='{joint_name}']")
        joint_range = joint.get("range")
        force_range = joint.get("actuatorfrcrange", "-5 5")
        is_hand = "_hand_" in joint_name
        actuator.tag = "position"
        actuator.attrib.clear()
        actuator.set("name", joint_name)
        actuator.set("joint", joint_name)
        actuator.set("kp", "25" if is_hand else "120")
        actuator.set("kv", "1" if is_hand else "4")
        actuator.set("ctrlrange", joint_range)
        actuator.set("ctrllimited", "true")
        actuator.set("forcerange", force_range)
        actuator.set("forcelimited", "true")
    keyframe = root.find("keyframe")
    if keyframe is not None:
        root.remove(keyframe)
    pelvis = root.find(".//body[@name='pelvis']")
    half_yaw = station.yaw_rad / 2
    pelvis.set("pos", values((*station.position_xy_m, 0.793)))
    pelvis.set("quat", values((math.cos(half_yaw), 0, 0, math.sin(half_yaw))))
    camera = cfg["camera"]
    torso = root.find(f".//body[@name='{camera['parent']}']")
    ET.SubElement(torso, "camera", name=camera["name"], pos=values(camera["pos_m"]),
                  xyaxes=values(camera["xyaxes"]), fovy=str(camera["fovy_deg"]))
    for side in ("left", "right"):
        wrist = root.find(f".//body[@name='{side}_wrist_yaw_link']")
        ET.SubElement(wrist, "site", name=f"{side}_wrist_eef", pos="0 0 0")
        hand = cfg["dex3"]
        ET.SubElement(wrist, "site", name=f"{side}_tcp", pos=values(hand["tcp_pos_m"]),
                      quat=values(hand["tcp_quat_wxyz"]), rgba="0 1 0 1", size="0.006")

        # 给官方手部碰撞几何添加稳定名称和抓取摩擦参数，便于闭环接触判定。
        palm = next((geom for geom in wrist.findall("geom")
                     if geom.get("mesh") == f"{side}_hand_palm_link"
                     and geom.get("contype") != "0"), None)
        if palm is not None:
            palm.set("name", f"{side}_hand_palm_contact")
            palm.set("friction", "1.5 0.02 0.002")
            palm.set("condim", "4")
        for body in root.iter("body"):
            body_name = body.get("name", "")
            if not body_name.startswith(f"{side}_hand_"):
                continue
            prefix = f"{side}_hand_"
            part = body_name[len(prefix):]
            if part.endswith("_link"):
                part = part[:-len("_link")]
            for index, geom in enumerate(body.findall("geom")):
                if geom.get("contype") == "0":
                    continue
                geom.set("name", f"{side}_hand_{part}_contact_{index}")
                geom.set("friction", "1.5 0.02 0.002")
                geom.set("condim", "4")
                geom.set("solref", "0.01 1")
    custom = root.find("custom")
    if custom is None:
        custom = ET.SubElement(root, "custom")
    ET.SubElement(custom, "text", name="calibration_sha256", data=calibration_hash(cfg))
    relative_meshdir = os.path.relpath(mesh_dir, output_dir)
    root.find("compiler").set("meshdir", relative_meshdir)
    _indent(root)
    robot_path = output_dir / "g1_dex3.xml"
    ET.ElementTree(root).write(robot_path, encoding="unicode")
    # 复用导航厨房，替换为固定根基座、全身关节可视的 Dex3 G1；两个场景
    # 使用同一世界坐标。到达操作站后根基座保持固定，GN1x 只驱动上身与双手。
    scene_root = ET.parse(ASSETS / "g1_open_kitchen/scene.xml").getroot()
    scene_root.set("model", "G1 kitchen SONIC mirror and upper-body manipulation")
    scene_root.find("compiler").set("meshdir", relative_meshdir)
    scene_root.find("include").set("file", "g1_dex3.xml")
    keyframe = scene_root.find("keyframe")
    if keyframe is not None:
        scene_root.remove(keyframe)
    global_visual = scene_root.find("./visual/global")
    global_visual.set("offwidth", str(camera["width"]))
    global_visual.set("offheight", str(camera["height"]))
    world = scene_root.find("worldbody")
    preview = world.find("./body[@name='navigation_task_preview']")
    if preview is not None:
        world.remove(preview)
    _add_task_assets(world)
    # 从机器人右后方观察手臂、抓取区和放置区，避免机身遮挡岛台前缘。
    observer_position = (4.75, -2.35, 2.15)
    observer_target = (3.35, -0.40, 1.05)
    ET.SubElement(world, "camera", name="overview", pos=values(observer_position),
                  xyaxes=values(camera_xyaxes(observer_position, observer_target)), fovy="46")
    _indent(scene_root)
    scene_path = output_dir / "scene.xml"
    ET.ElementTree(scene_root).write(scene_path, encoding="unicode")
    return scene_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--output-dir", type=Path, default=DESTINATION)
    args = parser.parse_args()
    print(build_scene(args.calibration, args.output_dir))


if __name__ == "__main__":
    main()
