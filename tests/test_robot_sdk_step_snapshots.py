import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from robot_sdk.cad.bbox import CadBoundingBox
from robot_sdk.cad.export import (
    CadQueryExportResult,
    CadQueryStepPackageExportResult,
    CadQuerySubassemblyExportResult,
)
from robot_sdk.cad.snapshot import (
    StepSnapshotPackageResult,
    StepSnapshotResult,
    generate_step_package_snapshots,
    generate_step_snapshots,
)
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec
from robot_sdk.validation.visual import validate_visual_geometry


class RobotSdkStepSnapshotTest(unittest.TestCase):
    def test_generates_renderer_backed_svg_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            step_path = root / "robot.step"
            step_path.write_text("STEP", encoding="utf-8")
            export_result = CadQueryExportResult(
                path=step_path,
                export_type="STEP",
                exists=True,
                size_bytes=step_path.stat().st_size,
            )

            snapshots = generate_step_snapshots(
                export_result,
                root / "snapshots",
                views=("front",),
                cq_module=FakeSnapshotCadQuery,
            )

        self.assertEqual(len(snapshots), 1)
        self.assertTrue(snapshots[0].generated)
        self.assertFalse(snapshots[0].fallback_used)
        self.assertEqual(snapshots[0].renderer, "cadquery_step_import")

    def test_falls_back_to_bbox_svg_when_renderer_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            step_path = root / "robot.step"
            step_path.write_text("STEP", encoding="utf-8")
            export_result = CadQueryExportResult(
                path=step_path,
                export_type="STEP",
                exists=True,
                size_bytes=step_path.stat().st_size,
                bbox=CadBoundingBox(0.0, 0.0, 0.0, 100.0, 20.0, 30.0),
            )

            snapshots = generate_step_snapshots(
                export_result,
                root / "snapshots",
                views=("front",),
                cq_module=object(),
            )

        self.assertEqual(len(snapshots), 1)
        self.assertTrue(snapshots[0].generated)
        self.assertTrue(snapshots[0].fallback_used)
        self.assertEqual(snapshots[0].renderer, "bbox_svg_fallback")

    def test_generates_selected_subassembly_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = _snapshot_package_with_subassemblies(root)

            result = generate_step_package_snapshots(
                package,
                subassembly_names=["forearm"],
                views=("front",),
                cq_module=FakeSnapshotCadQuery,
            )

        self.assertEqual(
            [snapshot.target_name for snapshot in result.whole_machine_snapshots],
            ["whole_machine"],
        )
        self.assertEqual(
            [snapshot.target_name for snapshot in result.subassembly_snapshots],
            ["forearm"],
        )
        self.assertEqual(result.real_step_snapshot_count, 2)

    def test_visual_validation_reports_renderer_backed_snapshot(self) -> None:
        package = FakeSnapshotPackage(generated_count=3, real_count=3, fallback_count=0)

        report = validate_visual_geometry(snapshot_result=package)

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn("visual_snapshot_generated", {issue.code for issue in report.passes})

    def test_visual_validation_warns_on_fallback_only_snapshot(self) -> None:
        package = FakeSnapshotPackage(generated_count=3, real_count=0, fallback_count=3)

        report = validate_visual_geometry(snapshot_result=package)

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn(
            "visual_snapshot_fallback_only",
            {issue.code for issue in report.warnings},
        )

    def test_visual_validation_rejects_flat_high_dof_svg_silhouette(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = _svg_snapshot_package(
                Path(temp_dir),
                view="front",
                width=500.0,
                height=30.0,
            )

            report = validate_visual_geometry(
                kinematic_model=_planar_template_model(dof=6),
                snapshot_result=package,
            )

        self.assertFalse(report.ok)
        self.assertIn(
            "snapshot_horizontal_silhouette_rejected",
            {issue.code for issue in report.errors},
        )

    def test_visual_validation_accepts_non_flat_high_dof_svg_silhouette(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = _svg_snapshot_package(
                Path(temp_dir),
                view="front",
                width=400.0,
                height=160.0,
            )

            report = validate_visual_geometry(
                kinematic_model=_planar_template_model(dof=6),
                snapshot_result=package,
            )

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn(
            "visual_snapshot_silhouette_screened",
            {issue.code for issue in report.passes},
        )

    def test_visual_validation_rejects_flat_focus_subassembly_svg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = _svg_snapshot_package(
                Path(temp_dir),
                view="front",
                width=420.0,
                height=30.0,
                target_name="wrist_group",
                as_subassembly=True,
            )

            report = validate_visual_geometry(snapshot_result=package)

        self.assertFalse(report.ok)
        self.assertIn(
            "focus_subassembly_silhouette_rejected",
            {issue.code for issue in report.errors},
        )

    def test_visual_validation_accepts_focus_subassembly_svg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = _svg_snapshot_package(
                Path(temp_dir),
                view="front",
                width=180.0,
                height=80.0,
                target_name="wrist_group",
                as_subassembly=True,
            )

            report = validate_visual_geometry(snapshot_result=package)

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn(
            "focus_subassembly_snapshots_screened",
            {issue.code for issue in report.passes},
        )

    def test_visual_validation_requires_source_joint_review_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_package = _source_review_snapshot_package(
                Path(temp_dir),
                names=["wrist_l5_j6", "tool_end"],
            )

            report = validate_visual_geometry(
                kinematic_model=_planar_template_model(dof=6),
                step_package_result=SimpleNamespace(
                    whole_machine_assembly_source="source_joint"
                ),
                snapshot_result=snapshot_package,
            )

        self.assertFalse(report.ok)
        self.assertIn(
            "source_joint_review_snapshots_missing",
            {issue.code for issue in report.errors},
        )

    def test_visual_validation_accepts_source_joint_review_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_package = _source_review_snapshot_package(
                Path(temp_dir),
                names=[
                    "base_shoulder",
                    "upper_arm",
                    "forearm",
                    "wrist_l5_j6",
                    "tool_end",
                ],
            )

            report = validate_visual_geometry(
                kinematic_model=_planar_template_model(dof=6),
                step_package_result=SimpleNamespace(
                    whole_machine_assembly_source="source_joint"
                ),
                snapshot_result=snapshot_package,
            )

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn(
            "source_joint_review_snapshots_ready",
            {issue.code for issue in report.passes},
        )


class FakeSnapshotImporters:
    @staticmethod
    def importStep(path: str):
        return {"step_path": path}


class FakeSnapshotExporters:
    @staticmethod
    def export(obj, path: str, opt=None):
        Path(path).write_text("<svg><rect width='1' height='1'/></svg>", encoding="utf-8")


class FakeSnapshotCadQuery:
    importers = FakeSnapshotImporters
    exporters = FakeSnapshotExporters


class FakeSnapshotPackage:
    def __init__(self, *, generated_count: int, real_count: int, fallback_count: int) -> None:
        self.generated_count = generated_count
        self.real_step_snapshot_count = real_count
        self.fallback_count = fallback_count
        self.snapshots = []

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_count": self.generated_count,
            "real_step_snapshot_count": self.real_step_snapshot_count,
            "fallback_count": self.fallback_count,
        }


def _svg_snapshot_package(
    root: Path,
    *,
    view: str,
    width: float,
    height: float,
    target_name: str = "whole_machine",
    as_subassembly: bool = False,
) -> StepSnapshotPackageResult:
    svg_path = root / f"{target_name}_{view}.svg"
    svg_path.write_text(
        "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg">',
                '<path d="'
                f"M0,0 L{width},0 L{width},{height} L0,{height} L0,0"
                '" />',
                "</svg>",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = StepSnapshotResult(
        source_step_path=root / "robot.step",
        svg_path=svg_path,
        view=view,
        generated=True,
        renderer="cadquery_step_import",
        target_name=target_name,
        fallback_used=False,
        size_bytes=svg_path.stat().st_size,
    )
    whole = [] if as_subassembly else [snapshot]
    subassemblies = [snapshot] if as_subassembly else []
    return StepSnapshotPackageResult(
        root_dir=root,
        snapshots_dir=root,
        whole_machine_snapshots=whole,
        subassembly_snapshots=subassemblies,
    )


def _snapshot_package_with_subassemblies(root: Path) -> CadQueryStepPackageExportResult:
    whole_export = _step_export(root / "whole.step")
    forearm_export = _step_export(root / "forearm" / "forearm.step")
    wrist_export = _step_export(root / "wrist" / "wrist.step")
    return CadQueryStepPackageExportResult(
        root_dir=root,
        whole_machine_export=whole_export,
        subassemblies=[
            CadQuerySubassemblyExportResult(
                name="forearm",
                directory=root / "forearm",
                part_ids=["J3", "L3", "J4"],
                part_export_names={},
                part_exports=[],
                assembly_export=forearm_export,
            ),
            CadQuerySubassemblyExportResult(
                name="wrist",
                directory=root / "wrist",
                part_ids=["J4", "L4", "J5"],
                part_export_names={},
                part_exports=[],
                assembly_export=wrist_export,
            ),
        ],
    )


def _source_review_snapshot_package(
    root: Path,
    *,
    names: list[str],
) -> StepSnapshotPackageResult:
    whole = _snapshot_result(root, "whole_machine", view="front", width=360.0, height=160.0)
    subassemblies = [
        _snapshot_result(root, name, view="front", width=120.0, height=80.0)
        for name in names
    ]
    return StepSnapshotPackageResult(
        root_dir=root,
        snapshots_dir=root,
        whole_machine_snapshots=[whole],
        subassembly_snapshots=subassemblies,
    )


def _snapshot_result(
    root: Path,
    target_name: str,
    *,
    view: str,
    width: float,
    height: float,
) -> StepSnapshotResult:
    svg_path = root / f"{target_name}_{view}.svg"
    svg_path.write_text(
        "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg">',
                '<path d="'
                f"M0,0 L{width},0 L{width},{height} L0,{height} L0,0"
                '" />',
                "</svg>",
            ]
        ),
        encoding="utf-8",
    )
    return StepSnapshotResult(
        source_step_path=root / f"{target_name}.step",
        svg_path=svg_path,
        view=view,
        generated=True,
        renderer="cadquery_step_import",
        target_name=target_name,
        fallback_used=False,
        size_bytes=svg_path.stat().st_size,
    )


def _step_export(path: Path) -> CadQueryExportResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("STEP", encoding="utf-8")
    return CadQueryExportResult(
        path=path,
        export_type="STEP",
        exists=True,
        size_bytes=path.stat().st_size,
    )


def _planar_template_model(*, dof: int) -> KinematicModel:
    joints = []
    links = []
    dh_params = []
    previous_link = "base"
    for index in range(1, dof + 1):
        joint_id = f"J{index}"
        link_id = f"L{index}"
        joints.append(
            JointSpec(
                id=joint_id,
                type="revolute",
                parent_link=previous_link,
                child_link=link_id,
            )
        )
        links.append(LinkSpec(id=link_id, length=80.0, parent_joint=joint_id))
        dh_params.append(
            DHParam(
                joint_id=joint_id,
                a=80.0,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                variable=f"theta{index}",
            )
        )
        previous_link = link_id
    return KinematicModel(convention="dh", joints=joints, links=links, dh_params=dh_params)


if __name__ == "__main__":
    unittest.main()
