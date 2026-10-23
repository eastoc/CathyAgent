"""AutoDL 上的 GN1x Arena G1 HTTP 推理服务，默认端口 6006。"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import secrets
import threading
import time

import numpy as np

if __package__:
    from .contract import (
        ACTION_DIMS, EMBODIMENT, MODEL_ID, STATE_DIMS, VERSION,
        observation_from_request, sim_metadata, upper_actions, validate_metadata,
    )
else:
    from contract import (  # type: ignore
        ACTION_DIMS, EMBODIMENT, MODEL_ID, STATE_DIMS, VERSION,
        observation_from_request, sim_metadata, upper_actions, validate_metadata,
    )


def _load_modality_config(path: str | Path) -> None:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"找不到 Arena G1 modality 配置: {config_path}")
    spec = importlib.util.spec_from_file_location("cathy_arena_g1_modality", config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法导入 modality 配置: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def policy_metadata(policy) -> dict:
    configs = policy.get_modality_config()
    tag = policy.embodiment_tag.value
    params = policy.processor.state_action_processor.norm_params[tag]
    dimensions = {
        modality: {
            key: int(params[modality][key]["dim"].item())
            for key in configs[modality].modality_keys
        }
        for modality in ("state", "action")
    }
    metadata = sim_metadata()
    metadata.update(
        embodiment=tag,
        state_dims=dimensions["state"],
        action_dims=dimensions["action"],
        video_keys=list(configs["video"].modality_keys),
        video_delta_indices=list(configs["video"].delta_indices),
        state_delta_indices=list(configs["state"].delta_indices),
        action_delta_indices=list(configs["action"].delta_indices),
        language_key=configs["language"].modality_keys[0],
    )
    validate_metadata(metadata)
    return metadata


class HoldPolicy:
    """仅用于链路自检：保持关节、底盘命令恒零，不代表 GN1x 推理。"""

    def get_action(self, observation):
        horizon = 40
        result = {
            key: np.repeat(observation["state"][key][:, -1:, :], horizon, axis=1)
            for key in STATE_DIMS
        }
        result["base_height_command"] = np.zeros((1, horizon, 1), np.float32)
        result["navigate_command"] = np.zeros((1, horizon, 3), np.float32)
        return result, {}


def create_app(policy, metadata, *, api_token=None):
    from fastapi import FastAPI, Header, HTTPException

    validate_metadata(metadata)
    app = FastAPI(title="GN1x Arena G1 Static PickNPlace API", version=VERSION)
    inference_lock = threading.Lock()

    def authorize(authorization):
        if api_token and not secrets.compare_digest(
            authorization or "", f"Bearer {api_token}"
        ):
            raise HTTPException(401, "需要有效的 Bearer token")

    @app.get("/health")
    def health():
        return {
            "status": "ready", "backend": metadata["backend"],
            "schema_version": VERSION, "model_id": metadata["model_id"],
        }

    @app.get("/metadata")
    def get_metadata(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return metadata

    @app.post("/infer")
    def infer(payload: dict, authorization: str | None = Header(default=None)):
        authorize(authorization)
        expected_hash = metadata.get("calibration_sha256")
        if expected_hash and payload.get("calibration_sha256") != expected_hash:
            raise HTTPException(409, "本地相机/Dex3 标定与 AutoDL 服务不一致")
        try:
            observation = observation_from_request(payload, metadata)
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not inference_lock.acquire(blocking=False):
            raise HTTPException(503, "GN1x 推理正在进行，请等待上一请求结束")
        try:
            started = time.perf_counter()
            action, _ = policy.get_action(observation)
            result = upper_actions(action, metadata)
            return {
                "schema_version": VERSION,
                "backend": metadata["backend"],
                "model_id": metadata["model_id"],
                "action_representation": "absolute_physical",
                "actions": result,
                "inference_ms": (time.perf_counter() - started) * 1000,
            }
        finally:
            inference_lock.release()

    return app


def main() -> None:
    default_modality = Path(__file__).with_name("arena_g1_modality.py")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", default=MODEL_ID)
    parser.add_argument("--embodiment-tag", default=EMBODIMENT)
    parser.add_argument("--modality-config-path", type=Path, default=default_modality)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6006)
    parser.add_argument("--mock", action="store_true", help="保持姿态的链路自检，非模型推理")
    parser.add_argument("--calibration", type=Path, default=Path(__file__).with_name("calibration.json"))
    args = parser.parse_args()

    if args.mock:
        policy, metadata = HoldPolicy(), sim_metadata()
        metadata["backend"] = "mock_hold_not_gn1x"
    else:
        _load_modality_config(args.modality_config_path)
        from gr00t.data.embodiment_tags import EmbodimentTag
        from gr00t.policy.gr00t_policy import Gr00tPolicy

        policy = Gr00tPolicy(
            embodiment_tag=EmbodimentTag.resolve(args.embodiment_tag),
            model_path=args.model_path,
            device=args.device,
            strict=True,
        )
        metadata = policy_metadata(policy)
        metadata.update(
            backend="gr00t_n1.7_arena_g1_static_picknplace",
            model_id=MODEL_ID,
            model_path=args.model_path,
        )

    if __package__:
        from .contract import calibration_hash, load_calibration
    else:
        from contract import calibration_hash, load_calibration  # type: ignore
    calibration = load_calibration(args.calibration)
    metadata.update(
        calibration_sha256=calibration_hash(calibration),
        control_hz=calibration["control_hz"],
        training_alignment_verified=True,
    )
    validate_metadata(metadata)

    import uvicorn
    uvicorn.run(
        create_app(policy, metadata, api_token=os.environ.get("GROOT_API_TOKEN")),
        host=args.host, port=args.port, workers=1,
    )


if __name__ == "__main__":
    main()
