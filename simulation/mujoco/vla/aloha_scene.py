"""使用原版 gym-aloha MJCF 资产，直接创建原生 MuJoCo 模型。"""
from pathlib import Path
import xml.etree.ElementTree as ET

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "aloha"
JOINT_NAMES = tuple(f"vx300s_{arm}/{joint}" for arm in ("left", "right")
                    for joint in ("waist", "shoulder", "elbow", "forearm_roll", "wrist_angle",
                                  "wrist_rotate", "left_finger", "right_finger"))
CAMERA_MAP = {"cam_high": "top", "cam_left_wrist": "left_wrist", "cam_right_wrist": "right_wrist"}


def _expand_includes(element):
    for child in list(element):
        if child.tag == "include":
            path = (ASSET_DIR / child.attrib["file"]).resolve()
            if path.parent != ASSET_DIR.resolve():
                raise ValueError("ALOHA include 必须位于资产目录")
            included = ET.parse(path).getroot()
            _expand_includes(included)
            offset = list(element).index(child)
            element.remove(child)
            for i, item in enumerate(list(included)):
                element.insert(offset + i, item)
        else:
            _expand_includes(child)


def make_aloha_model(config):
    import mujoco
    source = ASSET_DIR / "bimanual_viperx_transfer_cube.xml"
    if not source.is_file():
        raise RuntimeError("缺少 ALOHA 资产，请运行 python -m simulation.mujoco.vla.fetch_aloha_assets")
    root = ET.parse(source).getroot()
    _expand_includes(root)
    root.set("model", "cathy_aloha_three_cube")
    for mesh in root.iter("mesh"):
        if "file" in mesh.attrib:
            mesh.set("file", str(ASSET_DIR / mesh.attrib["file"]))
    for item in list(root.findall("keyframe")):
        root.remove(item)
    ET.SubElement(root, "option", timestep="0.002", integrator="implicitfast")
    for camera in root.iter("camera"):
        if camera.get("name") in {"left_wrist", "right_wrist"}:
            camera.set("fovy", str(config["observation"]["wrist_fovy"]))
    scene = config["scene"]
    for world in root.findall("worldbody"):
        old = world.find("body[@name='box']")
        if old is not None:
            world.remove(old)
        table = world.find("body[@name='table']")
        if table is not None:
            table.set("pos", f"0 0.6 {scene['table_height']}")
            geom = table.find("geom")
            geom.attrib.pop("mesh", None)
            geom.set("type", "box")
            sx, sy, sz = scene["table_size"]
            geom.set("size", f"{sx / 2} {sy / 2} {sz / 2}")
            geom.set("pos", f"0 0 {-sz / 2}")
    world = ET.SubElement(root, "worldbody")
    half = scene["cube_half_size"]
    colors = {"red": "0.9 0.04 0.04 1", "green": "0.04 0.8 0.08 1", "blue": "0.04 0.15 0.95 1"}
    for color, rgba in colors.items():
        x, y = scene["cubes"][color]
        body = ET.SubElement(world, "body", name=f"{color}_cube",
                             pos=f"{x} {y} {scene['table_height'] + half + 0.002}")
        ET.SubElement(body, "freejoint", name=f"{color}_cube_joint")
        mass = 500 * (2 * half) ** 3
        inertia = 2 * mass * half ** 2 / 3
        ET.SubElement(body, "inertial", pos="0 0 0", mass=str(mass),
                      diaginertia=" ".join([str(inertia)] * 3))
        ET.SubElement(body, "geom", name=f"{color}_cube_geom", type="box", size=f"{half} {half} {half}",
                      rgba=rgba, friction="1 0.005 0.0001", condim="4")
    x, y = scene["box_xy"]
    hx, hy = scene["box_inner_half_size"]
    wall, height = scene["box_wall_thickness"], scene["box_height"]
    box = ET.SubElement(world, "body", name="target_box", pos=f"{x} {y} {scene['table_height']}")
    for name, pos, size in (
        ("bottom", [0, 0, wall / 2], [hx + wall, hy + wall, wall / 2]),
        ("left", [-hx - wall / 2, 0, height / 2], [wall / 2, hy + wall, height / 2]),
        ("right", [hx + wall / 2, 0, height / 2], [wall / 2, hy + wall, height / 2]),
        ("front", [0, -hy - wall / 2, height / 2], [hx, wall / 2, height / 2]),
        ("back", [0, hy + wall / 2, height / 2], [hx, wall / 2, height / 2]),
    ):
        ET.SubElement(box, "geom", name=f"target_box_{name}", type="box",
                      pos=" ".join(map(str, pos)), size=" ".join(map(str, size)),
                      rgba="0.55 0.4 0.22 1", friction="1 0.005 0.0001")
    return mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
