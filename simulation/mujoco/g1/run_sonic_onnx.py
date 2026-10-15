"""启动 Python ONNX Runtime 版 GEAR-SONIC 推理控制器。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .run_sonic_sim import DEFAULT_SONIC_ROOT, _configure_sonic_imports
from .sonic_onnx import SonicOnnxController, SonicOnnxPaths


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G1 SONIC ONNX Runtime controller")
    parser.add_argument("--sonic-root", type=Path, default=DEFAULT_SONIC_ROOT)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--interface", default="sim")
    parser.add_argument("--domain-id", type=int, default=0)
    parser.add_argument("--command-endpoint", default="tcp://127.0.0.1:5556")
    parser.add_argument("--control-hz", type=float, default=50.0)
    parser.add_argument("--planner-hz", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    root = args.sonic_root.expanduser().resolve()
    _configure_sonic_imports(root)
    from gear_sonic.utils.network.network_utils import resolve_interface

    interface, _ = resolve_interface(args.interface)
    controller = SonicOnnxController(
        SonicOnnxPaths.from_root(root),
        provider=args.provider,
        command_endpoint=args.command_endpoint,
        control_hz=args.control_hz,
        planner_hz=args.planner_hz,
    )
    controller.run(domain_id=args.domain_id, interface=interface)


if __name__ == "__main__":
    main()
