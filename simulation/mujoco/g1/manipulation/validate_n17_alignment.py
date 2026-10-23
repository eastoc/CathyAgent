"""核对本地 MuJoCo 与 GN1x Arena G1 Static PickNPlace 契约。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .contract import (
    ACTION_DIMS, CALIBRATION, HAND_SUFFIXES, MODEL_ID, STATE_DIMS,
    VIDEO_KEYS, calibration_hash, load_calibration, sim_metadata,
    validate_actions,
)
from .runtime import G1ManipulationEnv


def validate(calibration_path=CALIBRATION, shadow_response: Path | None = None):
    calibration = load_calibration(calibration_path)
    env = G1ManipulationEnv(calibration_path=calibration_path)
    try:
        state = env.state()
        result = {
            "model_id": MODEL_ID,
            "contract": {
                "control_hz_50": calibration["control_hz"] == 50,
                "single_ego_view": tuple(VIDEO_KEYS) == ("ego_view",),
                "rgb_640x480": (
                    calibration["camera"]["width"] == 640
                    and calibration["camera"]["height"] == 480
                ),
                "state_dimensions": {
                    key: int(np.asarray(value).size) for key, value in state.items()
                } == STATE_DIMS,
                "action_dimensions": ACTION_DIMS,
                "dex3_dataset_order": (
                    tuple(calibration["dex3"]["joint_order"]) == HAND_SUFFIXES
                ),
                "scene_calibration_hash": env.calibration_sha256
                == calibration_hash(calibration),
                "controlled_actuators": int(env.model.nu),
            },
            "camera": calibration["camera"],
            "initial_state": {
                key: np.asarray(value).tolist() for key, value in state.items()
            },
        }
        result["contract"]["matches"] = all(
            value for key, value in result["contract"].items()
            if key not in {"action_dimensions", "controlled_actuators"}
        ) and result["contract"]["controlled_actuators"] == sum(STATE_DIMS.values())

        if shadow_response:
            response = json.loads(Path(shadow_response).read_text(encoding="utf-8"))
            actions = validate_actions(response, sim_metadata(), 40)
            result["shadow_inference"] = {
                "validated": True,
                "max_abs_static_command": max(
                    float(np.max(np.abs(actions["base_height_command"]))),
                    float(np.max(np.abs(actions["navigate_command"]))),
                ),
                "applied_to_simulation": False,
            }
        return result
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--shadow-response", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.calibration, args.shadow_response)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
