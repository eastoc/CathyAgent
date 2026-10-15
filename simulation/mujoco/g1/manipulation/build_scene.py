"""从已有 Menagerie G1 派生固定下半身的双平行夹爪场景。"""
from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

from .contract import CALIBRATION, JOINT_GROUPS, calibration_hash, load_calibration

ASSETS = Path(__file__).resolve().parents[2] / "assets"
DESTINATION = ASSETS / "g1_gripper"


def values(items):
    return " ".join(str(v) for v in items)


def build_scene(calibration_path=CALIBRATION, output_dir=DESTINATION):
    cfg = load_calibration(calibration_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    root = ET.parse(ASSETS / "g1_open_kitchen/g1/g1.xml").getroot()
    root.set("model", "g1_fixed_lower_body_parallel_grippers")
    upper_joints = {name for names in JOINT_GROUPS.values() for name in names}
    # 固定基座和腿的运动学：云端输出无法写入根节点或下肢关节。
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "freejoint" or (
                child.tag == "joint" and child.get("name")
                and child.get("name") not in upper_joints
            ):
                parent.remove(child)
            elif child.tag == "geom" and "rubber_hand" in child.get("mesh", ""):
                parent.remove(child)
    for mesh in list(root.find("asset")):
        if "rubber_hand" in mesh.get("file", ""):
            root.find("asset").remove(mesh)
    actuators = root.find("actuator")
    for actuator in list(actuators):
        if actuator.get("joint") not in upper_joints:
            actuators.remove(actuator)
    root.remove(root.find("keyframe"))
    camera = cfg["camera"]
    torso = root.find(f".//body[@name='{camera['parent']}']")
    ET.SubElement(torso, "camera", name=camera["name"], pos=values(camera["pos_m"]),
                  xyaxes=values(camera["xyaxes"]), fovy=str(camera["fovy_deg"]))
    equality = ET.SubElement(root, "equality")
    for side in ("left", "right"):
        wrist = root.find(f".//body[@name='{side}_wrist_yaw_link']")
        ET.SubElement(wrist, "site", name=f"{side}_wrist_eef", pos="0 0 0")
        gripper = cfg["gripper"]
        mount = ET.SubElement(wrist, "body", name=f"{side}_gripper",
                              pos=values(gripper["mount_pos_m"][side]),
                              quat=values(gripper["mount_quat_wxyz"][side]))
        ET.SubElement(mount, "geom", name=f"{side}_gripper_palm", type="box",
                      pos="0.015 0 0", size="0.025 0.035 0.022", mass="0.20",
                      rgba="0.15 0.18 0.22 1", friction="1 0.01 0.001")
        ET.SubElement(mount, "site", name=f"{side}_tcp", pos=values(gripper["tcp_pos_m"]),
                      quat=values(gripper["tcp_quat_wxyz"]), rgba="0 1 0 1", size="0.006")
        for finger, sign in (("a", 1), ("b", -1)):
            name = f"{side}_finger_{finger}"
            body = ET.SubElement(mount, "body", name=name, pos=f"0.04 {sign * 0.006} 0")
            ET.SubElement(body, "joint", name=name + "_joint", type="slide", axis=f"0 {sign} 0",
                          range=f"0 {gripper['max_width_m'] / 2}", damping="2",
                          armature="0.001", frictionloss="0.05")
            ET.SubElement(body, "geom", name=name + "_pad", type="box", pos="0.035 0 0",
                          size="0.035 0.006 0.018", mass="0.04", rgba="0.25 0.32 0.38 1",
                          friction="1.5 0.02 0.002", condim="4", solref="0.01 1")
        ET.SubElement(equality, "joint", joint1=f"{side}_finger_a_joint",
                      joint2=f"{side}_finger_b_joint", polycoef="0 1 0 0 0",
                      solref="0.005 1", solimp="0.999 0.9999 0.001")
        ET.SubElement(actuators, "position", name=f"{side}_gripper", joint=f"{side}_finger_a_joint",
                      kp="300", kv="8", ctrlrange=f"0 {gripper['max_width_m'] / 2}",
                      forcerange="-25 25")
    ET.SubElement(root, "custom")
    ET.SubElement(root.find("custom"), "text", name="calibration_sha256", data=calibration_hash(cfg))
    ET.indent(root, space="  ")
    robot_path = output_dir / "g1_gripper.xml"
    ET.ElementTree(root).write(robot_path, encoding="unicode")
    import os
    meshdir = os.path.relpath(ASSETS / "g1_open_kitchen/g1/assets", output_dir)
    scene = f'''<mujoco model="G1 upper-body manipulation bench">
  <compiler angle="radian" meshdir="{meshdir}"/>
  <include file="g1_gripper.xml"/>
  <option timestep="0.002" integrator="implicitfast" cone="elliptic"/>
  <visual><global offwidth="{camera['width']}" offheight="{camera['height']}"/>
    <headlight ambient="0.5 0.5 0.5" diffuse="0.8 0.8 0.8"/>
    <map znear="0.005"/></visual>
  <worldbody>
    <light pos="0.5 -1 3"/>
    <geom name="floor" type="plane" size="3 3 0.1" rgba="0.80 0.82 0.85 1"/>
    <geom name="table" type="box" pos="0.55 0 0.73" size="0.28 0.52 0.025" rgba="0.55 0.42 0.30 1"/>
    <body name="red_cube" pos="0.48 0.16 0.78">
      <freejoint name="cube_free"/>
      <geom name="cube" type="box" size="0.025 0.025 0.025" mass="0.06" rgba="0.85 0.08 0.06 1" friction="1.5 0.02 0.002"/>
    </body>
    <site name="place_target" type="cylinder" pos="0.55 -0.20 0.757" size="0.07 0.001" rgba="0.1 0.7 0.2 0.7"/>
    <camera name="overview" pos="1.8 -1.8 1.5" xyaxes="0.707 0.707 0 -0.18 0.18 0.966" fovy="50"/>
  </worldbody>
</mujoco>
'''
    scene_path = output_dir / "scene.xml"
    scene_path.write_text(scene)
    return scene_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--output-dir", type=Path, default=DESTINATION)
    args = parser.parse_args()
    print(build_scene(args.calibration, args.output_dir))


if __name__ == "__main__":
    main()
