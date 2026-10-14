"""导入固定版本 gym-aloha 资产；无需安装 gym-aloha / dm_control / robosuite。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

REVISION = "bd3325740ea8d1c97411c41ea1e0f4ce0a7de8da"
REPO = "huggingface/gym-aloha"
DESTINATION = Path(__file__).resolve().parents[1] / "assets" / "aloha"
XML_FILES = {"bimanual_viperx_transfer_cube.xml", "scene.xml", "vx300s_dependencies.xml",
             "vx300s_left.xml", "vx300s_right.xml"}


def main():
    with urlopen(f"https://api.github.com/repos/{REPO}/git/trees/{REVISION}?recursive=1", timeout=30) as response:
        tree = json.load(response)
    if tree["sha"] != REVISION or tree.get("truncated"):
        raise RuntimeError("上游资产目录不完整或版本不符")
    selected = []
    for item in tree["tree"]:
        path = Path(item["path"])
        if item["type"] != "blob":
            continue
        if item["path"] == "LICENSE" or (
            path.parent.as_posix() == "gym_aloha/assets"
            and (path.name in XML_FILES or path.suffix == ".stl")
        ):
            selected.append(item)
    if not XML_FILES <= {Path(item["path"]).name for item in selected}:
        raise RuntimeError("上游缺少 ALOHA MJCF")
    DESTINATION.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        with urlopen(f"https://raw.githubusercontent.com/{REPO}/{REVISION}/{item['path']}", timeout=60) as response:
            data = response.read()
        digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if digest != item["sha"]:
            raise RuntimeError(f"资产校验失败: {item['path']}")
        target = DESTINATION / Path(item["path"]).name
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError(f"拒绝覆盖已修改资产: {target}")
        if not target.exists():
            target.write_bytes(data)
        return target.name

    with ThreadPoolExecutor(max_workers=6) as pool:
        for name in pool.map(fetch, selected):
            print(name)


if __name__ == "__main__":
    main()
