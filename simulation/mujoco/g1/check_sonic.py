"""检查同进程 G1 导航所需的 SONIC 模型和 ONNX Runtime 环境。"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

from .run_sonic_sim import DEFAULT_SONIC_ROOT


MODEL_PATHS = (
    "gear_sonic_deploy/policy/release/model_encoder.onnx",
    "gear_sonic_deploy/policy/release/model_decoder.onnx",
    "gear_sonic_deploy/policy/release/observation_config.yaml",
    "gear_sonic_deploy/planner/target_vel/V2/planner_sonic.onnx",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 G1 SONIC 导航运行环境")
    parser.add_argument(
        "--sonic-root",
        type=Path,
        default=Path(os.environ.get("SONIC_ROOT", DEFAULT_SONIC_ROOT)),
    )
    parser.add_argument("--provider", default="CPUExecutionProvider")
    return parser.parse_args()


def _report(ok: bool, name: str, detail: str) -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> None:
    args = _parse_args()
    root = args.sonic_root.expanduser().resolve()
    simulation_ok = True
    inference_ok = True

    simulation_ok &= _report(root.is_dir(), "SONIC 源码", str(root))
    for relative in MODEL_PATHS:
        path = root / relative
        inference_ok &= _report(path.is_file() and path.stat().st_size > 1024, relative, str(path))

    for module in ("mujoco", "yaml", "zmq", "onnxruntime"):
        found = importlib.util.find_spec(module) is not None
        if module == "onnxruntime":
            inference_ok &= _report(found, f"Python {module}", sys.executable)
        else:
            simulation_ok &= _report(found, f"Python {module}", sys.executable)

    if importlib.util.find_spec("onnxruntime") is not None:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        inference_ok &= _report(
            args.provider in providers, "ONNX provider",
            f"requested={args.provider}, available={providers}",
        )

    print()
    print(f"MuJoCo 桥: {'可启动' if simulation_ok else '缺少依赖'}")
    print(f"SONIC ONNX Runtime 推理: {'可启动' if inference_ok else '缺少依赖'}")
    if not simulation_ok or not inference_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
