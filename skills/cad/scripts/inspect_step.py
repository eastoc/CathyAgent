"""无 GUI 的 STEP / STL inspect CLI。

用法：
    python inspect_step.py <path-to-step-or-stl> [--ndigits 4]

输出 1 行 JSON，键与 calibration_block.py 模板的 INSPECT 行一致，便于
上层 agent 用相同的解析逻辑做断言。

实现策略：
    * STEP：用 build123d.import_step → Compound/Solid；
    * STL ：用 build123d.import_stl  → 三角网格（faces/edges 计数会偏多）；
    * 通过 OCP / build123d 的 bounding_box / volume / topology 计数得到统一字段。

不依赖 FreeCAD、不打开任何 GUI。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _import_or_die() -> None:
    try:
        import build123d  # noqa: F401
    except ImportError as exc:
        print(
            f"[cad-skill] 缺少依赖：{exc.name}。请先 `pip install build123d`",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _load(path: Path):
    from build123d import import_step, import_stl

    suffix = path.suffix.lower()
    if suffix in {".step", ".stp"}:
        return import_step(str(path))
    if suffix == ".stl":
        return import_stl(str(path))
    raise SystemExit(f"[cad-skill] 不支持的后缀: {suffix!r}")


def inspect(path: Path, ndigits: int = 4) -> dict:
    obj = _load(path)
    bbox = obj.bounding_box()
    report = {
        "path": str(path),
        "bbox": {
            "x": round(bbox.size.X, ndigits),
            "y": round(bbox.size.Y, ndigits),
            "z": round(bbox.size.Z, ndigits),
        },
        "center": {
            "x": round(bbox.center().X, ndigits),
            "y": round(bbox.center().Y, ndigits),
            "z": round(bbox.center().Z, ndigits),
        },
        "volume": round(getattr(obj, "volume", 0.0), ndigits),
        "vertices": len(obj.vertices()),
        "edges": len(obj.edges()),
        "faces": len(obj.faces()),
        "solids": len(obj.solids()) if hasattr(obj, "solids") else None,
        "is_closed": bool(obj.is_valid()) if hasattr(obj, "is_valid") else None,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless STEP/STL inspect")
    parser.add_argument("path", help="STEP/STP/STL 文件路径")
    parser.add_argument(
        "--ndigits", type=int, default=4, help="数值字段保留的小数位（默认 4）"
    )
    args = parser.parse_args()

    p = Path(args.path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        print(f"[cad-skill] 文件不存在: {p}", file=sys.stderr)
        return 2

    _import_or_die()
    report = inspect(p, ndigits=args.ndigits)
    print(f"INSPECT={json.dumps(report, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
