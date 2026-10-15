"""在当前主机测量 SONIC ONNX 编码器、解码器与规划器延迟。"""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import time

import numpy as np
import onnxruntime as ort

from .run_sonic_sim import DEFAULT_SONIC_ROOT


MODELS = {
    "encoder": "gear_sonic_deploy/policy/release/model_encoder.onnx",
    "decoder": "gear_sonic_deploy/policy/release/model_decoder.onnx",
    "planner": "gear_sonic_deploy/planner/target_vel/V2/planner_sonic.onnx",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SONIC ONNX 本机延迟基准")
    parser.add_argument("--sonic-root", type=Path, default=DEFAULT_SONIC_ROOT)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=20)
    return parser.parse_args()


def _input_value(item: ort.NodeArg) -> np.ndarray:
    shape = tuple(1 if not isinstance(dim, int) else dim for dim in item.shape)
    if item.type == "tensor(int64)":
        value = np.zeros(shape, dtype=np.int64)
    elif item.type == "tensor(int32)":
        value = np.zeros(shape, dtype=np.int32)
    else:
        value = np.zeros(shape, dtype=np.float32)

    if item.name == "allowed_pred_num_tokens":
        value[...] = np.arange(shape[-1], dtype=np.int64)
    elif item.name in {"movement_direction", "facing_direction"}:
        value[..., 0] = 1.0
    elif item.name == "target_vel":
        value[...] = 0.35
    return value


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> None:
    args = _parse_args()
    if args.provider not in ort.get_available_providers():
        raise RuntimeError(
            f"provider {args.provider!r} 不可用；可用项: {ort.get_available_providers()}"
        )
    if args.warmup < 0 or args.iterations < 1:
        raise ValueError("warmup 必须非负，iterations 必须为正数")

    root = args.sonic_root.expanduser().resolve()
    print(f"provider={args.provider}")
    for name, relative in MODELS.items():
        path = root / relative
        started = time.perf_counter()
        session = ort.InferenceSession(str(path), providers=[args.provider])
        load_seconds = time.perf_counter() - started
        feed = {item.name: _input_value(item) for item in session.get_inputs()}

        for _ in range(args.warmup):
            session.run(None, feed)

        latencies: list[float] = []
        for _ in range(args.iterations):
            started = time.perf_counter()
            session.run(None, feed)
            latencies.append((time.perf_counter() - started) * 1000.0)

        print(
            f"{name}: load={load_seconds:.2f}s "
            f"mean={statistics.fmean(latencies):.2f}ms "
            f"median={statistics.median(latencies):.2f}ms "
            f"p95={_percentile(latencies, 0.95):.2f}ms"
        )


if __name__ == "__main__":
    main()
