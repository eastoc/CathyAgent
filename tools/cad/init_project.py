"""``python -m tools.cad.init_project <PATH>``：在任意路径初始化一个 CAD 项目骨架。

详见 ``ROADMAP_CAD_AGENT_v0.5.md`` §3.1 / Phase 0 验收。

用法：

.. code-block:: bash

    python -m tools.cad.init_project /tmp/test1
    python -m tools.cad.init_project ~/work/my_arm --machine-type robotic_arm --dof 6
    python -m tools.cad.init_project ~/work/x --machine-id arm_x_v1 --machine-type robotic_arm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许 ``python tools/cad/init_project.py`` 直接跑（脚本路径）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.cad import Project, ProjectError  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools.cad.init_project",
        description="在任意路径初始化一个 CAD 项目骨架（parts/modules/artifacts/stages/...）。",
    )
    p.add_argument("path", type=Path, help="项目根目录路径（可以是新目录或空目录）")
    p.add_argument(
        "--machine-id",
        default="machine_v1",
        help="machine.meta.yaml 中的 machine_id（默认 machine_v1）",
    )
    p.add_argument(
        "--machine-type",
        default="robotic_arm",
        help="机型，写入 machine.meta + motion_spec（默认 robotic_arm）",
    )
    p.add_argument(
        "--dof",
        type=int,
        default=6,
        help="自由度数（写入 machine.meta.dof_count，默认 6）",
    )
    p.add_argument(
        "--no-readme",
        action="store_true",
        help="不写 README.md",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="允许在非空目录上 init（默认即允许，本 flag 仅为兼容预留）",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        proj = Project.init(
            args.path,
            machine_id=args.machine_id,
            machine_type=args.machine_type,
            dof_count=args.dof,
            exist_ok=True,  # 路线图明确：init 应支持已存在的目录（不覆盖既有 meta）
            write_readme=not args.no_readme,
        )
    except ProjectError as e:
        print(f"[init_project] 失败: {e}", file=sys.stderr)
        return 2

    print(f"[init_project] 完成: {proj.root}")
    print(f"  parts_dir:           {proj.parts_dir}")
    print(f"  modules_dir:         {proj.modules_dir}")
    print(f"  artifacts_dir:       {proj.artifacts_dir}")
    print(f"  stages_dir:          {proj.stages_dir}")
    print(f"  machine.meta.yaml:   {proj.machine_meta_path}")
    print(f"  motion_spec.json:    {proj.motion_spec_path}")
    print(f"  .cad_todo.json:      {proj.todo_path}")
    print("下一步：python -m tools.cad.validate_project '%s'" % proj.root)
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
