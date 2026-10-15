"""不加载 GPU，检查下载后的 processor_config.json 和 statistics.json。"""
import argparse
import json
from pathlib import Path


def inspect(path, tag):
    path = Path(path)
    config = json.loads((path / "processor_config.json").read_text())
    stats = json.loads((path / "statistics.json").read_text())
    modalities = config["processor_kwargs"]["modality_configs"]
    if tag not in modalities:
        raise ValueError(f"checkpoint 没有 {tag}；支持 {list(modalities)}")
    selected = modalities[tag]
    dimensions = {modality: {key: len(stats[tag][modality][key]["mean"])
                             for key in selected[modality]["modality_keys"]}
                  for modality in ("state", "action")}
    return {"embodiment": tag, "dimensions": dimensions,
            "video": selected["video"], "language": selected["language"],
            "action_horizon": len(selected["action"]["delta_indices"]),
            "action_configs": selected["action"].get("action_configs"),
            "note": "字段和维度不等于物理标定；核实手部编码、相机 K/外参、腕/TCP 和 fps。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--tag", default="real_g1_relative_eef_relative_joints")
    args = parser.parse_args()
    print(json.dumps(inspect(args.checkpoint, args.tag), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
