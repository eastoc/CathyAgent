"""AutoDL 上的 GR00T HTTP API，默认端口 6006；可以直接作为脚本运行。"""
from __future__ import annotations

import argparse
import os
import secrets
import threading
import time

import numpy as np

if __package__:
    from .contract import VERSION, UPPER_KEYS, observation_from_request, sim_metadata, upper_actions
else:
    from contract import VERSION, UPPER_KEYS, observation_from_request, sim_metadata, upper_actions


def policy_metadata(policy):
    configs = policy.get_modality_config()
    tag = policy.embodiment_tag.value
    params = policy.processor.state_action_processor.norm_params[tag]
    dimensions = {
        modality: {key: int(params[modality][key]["dim"].item())
                   for key in configs[modality].modality_keys}
        for modality in ("state", "action")
    }
    if not set(dimensions["state"]).issubset(UPPER_KEYS):
        raise ValueError("只支持 G1 上半身状态接口；不能把 SONIC latent 当作关节姿态")
    language_keys = configs["language"].modality_keys
    if len(language_keys) != 1 or configs["language"].delta_indices != [0]:
        raise ValueError("当前 HTTP 接口需要一个当前时刻的语言指令")
    return {
        "schema_version": VERSION, "embodiment": tag,
        "state_dims": dimensions["state"],
        "action_dims": {k: v for k, v in dimensions["action"].items() if k in UPPER_KEYS},
        "video_keys": list(configs["video"].modality_keys),
        "video_delta_indices": list(configs["video"].delta_indices),
        "state_delta_indices": list(configs["state"].delta_indices),
        "action_delta_indices": list(configs["action"].delta_indices),
        "language_key": language_keys[0],
        "action_representation": "absolute_physical",
        "output_scope": "upper_body_only",
        "discarded_action_keys": [k for k in dimensions["action"] if k not in UPPER_KEYS],
    }


class HoldPolicy:
    """仅用于传输和仿真联调；不会抓取，不代表 GR00T 推理结果。"""
    def get_action(self, observation):
        return {k: np.repeat(v[:, -1:, :], 40, axis=1)
                for k, v in observation["state"].items()}, {}


def create_app(policy, metadata, *, api_token=None):
    from fastapi import FastAPI, Header, HTTPException
    app = FastAPI(title="G1 upper-body GR00T API", version=VERSION)
    inference_lock = threading.Lock()

    def authorize(authorization):
        if api_token and not secrets.compare_digest(authorization or "", f"Bearer {api_token}"):
            raise HTTPException(401, "需要有效的 Bearer token")

    @app.get("/health")
    def health():
        return {"status": "ready", "backend": metadata["backend"], "schema_version": VERSION}

    @app.get("/metadata")
    def get_metadata(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return metadata

    @app.post("/infer")
    def infer(payload: dict, authorization: str | None = Header(default=None)):
        authorize(authorization)
        expected_hash = metadata.get("calibration_sha256")
        if expected_hash and payload.get("calibration_sha256") != expected_hash:
            raise HTTPException(409, "仿真标定与训练 checkpoint 的 calibration_sha256 不一致")
        try:
            observation = observation_from_request(payload, metadata)
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not inference_lock.acquire(blocking=False):
            raise HTTPException(503, "推理正在进行，请等待上一请求结束")
        try:
            start = time.perf_counter()
            action, _ = policy.get_action(observation)
            result = upper_actions(action, metadata)
            return {"schema_version": VERSION, "backend": metadata["backend"],
                    "action_representation": "absolute_physical", "actions": result,
                    "inference_ms": (time.perf_counter() - start) * 1000}
        finally:
            inference_lock.release()

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", default="nvidia/GR00T-N1.7-3B")
    parser.add_argument("--embodiment-tag", default="REAL_G1")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6006)
    parser.add_argument("--mock", action="store_true", help="保持原姿态的传输自检，非模型推理")
    parser.add_argument("--calibration", help="仅对训练时使用了此标定文件的自定义 checkpoint 设置")
    args = parser.parse_args()
    if args.mock:
        policy, metadata = HoldPolicy(), sim_metadata()
        metadata["backend"] = "mock_hold_not_gr00t"
    else:
        from gr00t.data.embodiment_tags import EmbodimentTag
        from gr00t.policy.gr00t_policy import Gr00tPolicy
        policy = Gr00tPolicy(embodiment_tag=EmbodimentTag.resolve(args.embodiment_tag),
                             model_path=args.model_path, device=args.device, strict=True)
        metadata = policy_metadata(policy)
        metadata.update(backend="gr00t_n1.7", model_path=args.model_path)
    if args.calibration:
        if __package__:
            from .contract import calibration_hash, load_calibration
        else:
            from contract import calibration_hash, load_calibration
        calibration = load_calibration(args.calibration)
        metadata.update(calibration_sha256=calibration_hash(calibration),
                        control_hz=calibration["control_hz"])
    import uvicorn
    uvicorn.run(create_app(policy, metadata, api_token=os.environ.get("GROOT_API_TOKEN")),
                host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
