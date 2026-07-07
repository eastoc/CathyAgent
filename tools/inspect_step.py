"""Generate SVG review snapshots for a STEP file."""

from __future__ import annotations

import argparse
from pathlib import Path

from robot_sdk.cad.export import CadQueryExportResult
from robot_sdk.cad.snapshot import generate_step_snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step_path", help="Path to the STEP file to inspect.")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory for SVG snapshots. Defaults to <step parent>/snapshots.",
    )
    parser.add_argument(
        "--views",
        default="front,top,isometric",
        help="Comma-separated view names. Defaults to front,top,isometric.",
    )
    args = parser.parse_args()

    step_path = Path(args.step_path)
    out_dir = Path(args.out_dir) if args.out_dir else step_path.parent / "snapshots"
    views = [item.strip() for item in args.views.split(",") if item.strip()]
    export_result = CadQueryExportResult(
        path=step_path,
        export_type="STEP",
        exists=step_path.exists(),
        size_bytes=step_path.stat().st_size if step_path.exists() else None,
    )
    results = generate_step_snapshots(export_result, out_dir, views=views)
    for result in results:
        status = "generated" if result.generated else "failed"
        fallback = " fallback" if result.fallback_used else ""
        print(f"{status}{fallback}: {result.view} -> {result.svg_path}")
        if result.error:
            print(f"  error: {result.error}")
    return 0 if any(result.generated for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
