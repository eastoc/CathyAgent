"""Calibration block · build123d 起步模板（benchmark #1）。

任务：100 × 60 × 20 mm 居中长方体 + 4 个 Φ8 vertical 通孔 + 顶面外周 2 mm 倒角。
本脚本可直接 `python calibration_block.py` 运行，导出 STEP + STL，并在
stdout 末尾打印一行 `INSPECT={...}` 让上层 agent 校验。

不依赖 GUI、不弹窗、不连任何远程；只用 build123d + OCP。
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from build123d import (
        BuildPart,
        BuildSketch,
        Circle,
        Locations,
        Mode,
        Rectangle,
        chamfer,
        extrude,
        export_step,
        export_stl,
    )
except ImportError as exc:  # pragma: no cover
    print(
        f"[cad-skill] 缺少依赖：{exc.name}。请先在当前 Python env 里运行："
        " `pip install build123d`",
        file=sys.stderr,
    )
    raise


# ---------------------------------------------------------------------------
# 1. 参数：所有数值集中在此，便于 agent 修改单个参数后重跑
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Params:
    L: float = 100.0      # 长（X）
    W: float = 60.0       # 宽（Y）
    T: float = 20.0       # 厚（Z，挤出方向）
    HOLE_D: float = 8.0   # 通孔直径
    HOLE_DX: float = 35.0 # 孔阵列在 X 方向上的半距（共 ±HOLE_DX）
    HOLE_DY: float = 20.0 # 孔阵列在 Y 方向上的半距（共 ±HOLE_DY）
    CHAMFER: float = 2.0  # 顶面外周倒角

    @property
    def basename(self) -> str:
        return "calibration_block"


# ---------------------------------------------------------------------------
# 2. 几何：build123d 流式 API 构造
# ---------------------------------------------------------------------------

def build_part(p: Params) -> BuildPart:
    with BuildPart() as part:
        # 基体板（在 XY 平面以原点为中心绘矩形 → 沿 +Z 挤出 T）
        with BuildSketch() as s:
            Rectangle(p.L, p.W)
        extrude(amount=p.T)

        # 4 个通孔（在 XY 平面以 ±HOLE_DX, ±HOLE_DY 阵列，挖穿）
        with BuildSketch() as s:
            with Locations(
                (+p.HOLE_DX, +p.HOLE_DY),
                (+p.HOLE_DX, -p.HOLE_DY),
                (-p.HOLE_DX, +p.HOLE_DY),
                (-p.HOLE_DX, -p.HOLE_DY),
            ):
                Circle(p.HOLE_D / 2)
        extrude(amount=p.T + 1, mode=Mode.SUBTRACT)

        # 顶面外周 2 mm 倒角：取最大 Z 的 4 条外边（不含孔的内边）
        if p.CHAMFER > 0:
            top_edges = part.edges().group_by()[-1]  # Z 最大的一组（顶面）
            outer = top_edges.filter_by(
                lambda e: not _is_circle_edge(e, p.HOLE_D / 2)
            )
            chamfer(outer, length=p.CHAMFER)
    return part


def _is_circle_edge(edge, radius: float, tol: float = 1e-3) -> bool:
    """识别孔顶的圆边：圆形且半径 ≈ HOLE_D / 2。"""
    try:
        bbox = edge.bounding_box()
        size = max(bbox.size.X, bbox.size.Y)
        return math.isclose(size / 2, radius, rel_tol=0, abs_tol=tol * 5)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 3. 自检报告（不打开 GUI，只输出一行 JSON）
# ---------------------------------------------------------------------------

def print_report(part_obj, name: str) -> None:
    solid = part_obj.part if hasattr(part_obj, "part") else part_obj
    bbox = solid.bounding_box()
    report = {
        "name": name,
        "bbox": {
            "x": round(bbox.size.X, 4),
            "y": round(bbox.size.Y, 4),
            "z": round(bbox.size.Z, 4),
        },
        "center": {
            "x": round(bbox.center().X, 4),
            "y": round(bbox.center().Y, 4),
            "z": round(bbox.center().Z, 4),
        },
        "volume": round(solid.volume, 4),
        "vertices": len(solid.vertices()),
        "edges": len(solid.edges()),
        "faces": len(solid.faces()),
        "solids": len(solid.solids()),
        "is_closed": bool(solid.is_valid()) if hasattr(solid, "is_valid") else None,
    }
    print(f"INSPECT={json.dumps(report, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# 4. 主入口：构建 + 导出 + 报告
# ---------------------------------------------------------------------------

def main() -> int:
    out_dir = Path(os.environ.get("CAD_OUT_DIR") or ".").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    p = Params()
    part = build_part(p)
    solid = part.part

    step_path = out_dir / f"{p.basename}.step"
    stl_path = out_dir / f"{p.basename}.stl"
    export_step(solid, str(step_path))
    try:
        export_stl(solid, str(stl_path))
    except Exception as exc:  # pragma: no cover
        print(f"[cad-skill] STL 导出失败（已忽略，仅留 STEP）：{exc}", file=sys.stderr)

    print(f"STEP={step_path}")
    if stl_path.exists():
        print(f"STL={stl_path}")
    print_report(part, p.basename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
