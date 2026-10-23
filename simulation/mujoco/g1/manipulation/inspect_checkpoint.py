"""不加载 GPU，检查 GN1x checkpoint 的 processor 与统计契约。"""
import argparse
import json
from pathlib import Path

from .contract import ACTION_DIMS, EMBODIMENT, STATE_DIMS, VIDEO_KEYS


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
    expected = {"state": STATE_DIMS, "action": ACTION_DIMS}
    if dimensions != expected:
        raise ValueError(f"GN1x 维度不匹配: {dimensions}，需要 {expected}")
    video_keys = selected["video"]["modality_keys"]
    if video_keys != list(VIDEO_KEYS):
        raise ValueError(f"GN1x 视频键不匹配: {video_keys}")
    return {"embodiment": tag, "dimensions": dimensions,
            "video": selected["video"], "language": selected["language"],
            "action_horizon": len(selected["action"]["delta_indices"]),
            "action_configs": selected["action"].get("action_configs"),
            "contract_matches": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--tag", default=EMBODIMENT)
    args = parser.parse_args()
    print(json.dumps(inspect(args.checkpoint, args.tag), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
