import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from robot_sdk.cad.bbox import CadBoundingBox
from robot_sdk.cad.source_export import export_source_robot_step_package
from robot_sdk.cad.source_robot_builder import build_simple_source_serial_robot
from robot_sdk.validation.source_joint import validate_source_joint_assembly


class _FakePartCatalog:
    def __init__(self, part_ids: list[str]) -> None:
        self._part_ids = part_ids

    def part_ids(self) -> list[str]:
        return list(self._part_ids)


class _FakeSourceCadResult:
    def __init__(
        self,
        *,
        part_ids: list[str],
        source_mates: list[dict],
        joint_count: int | None = None,
    ) -> None:
        self.part_catalog = _FakePartCatalog(part_ids)
        self.metadata = {
            "production_assembly_source": "source_joint",
            "assembly_mode": "source_joint",
            "semantic_constraints_applied_to_solver": "source_joint",
            "source_mates": source_mates,
        }
        if joint_count is not None:
            self.metadata["joint_count"] = joint_count


class RobotSdkSourceJointValidationTest(unittest.TestCase):
    def test_source_joint_validation_accepts_connected_step_package(self) -> None:
        source_result = build_simple_source_serial_robot(joint_count=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            package = export_source_robot_step_package(source_result, temp_dir)

            report = validate_source_joint_assembly(
                cad_result=_FakeSourceCadResult(
                    part_ids=source_result.part_ids,
                    source_mates=source_result.source_mates(),
                ),
                step_package_result=package,
            )

        self.assertTrue(report.ok, report.errors)
        pass_codes = {issue.code for issue in report.passes}
        self.assertIn("source_joint_mates_complete", pass_codes)
        self.assertIn("source_joint_chain_connected", pass_codes)
        self.assertIn("source_joint_promotion_gate_ready", pass_codes)
        self.assertIn("source_joint_step_package_ready", pass_codes)

    def test_source_joint_validation_rejects_disconnected_chain(self) -> None:
        source_result = build_simple_source_serial_robot(joint_count=2)
        broken_mates = source_result.source_mates()[:-1]

        report = validate_source_joint_assembly(
            cad_result=_FakeSourceCadResult(
                part_ids=source_result.part_ids,
                source_mates=broken_mates,
            ),
        )

        self.assertFalse(report.ok)
        error_codes = {issue.code for issue in report.errors}
        self.assertIn("source_joint_serial_edge_count_mismatch", error_codes)

    def test_source_joint_validation_rejects_bad_review_bbox_ratio(self) -> None:
        part_ids = [
            "base",
            "J1",
            "L1",
            "J2",
            "L2",
            "J3",
            "L3",
            "J4",
            "L4",
            "J5",
            "L5",
            "J6",
            "L6",
            "end_effector",
        ]
        report = validate_source_joint_assembly(
            cad_result=_FakeSourceCadResult(
                part_ids=part_ids,
                source_mates=_chain_mates(part_ids),
                joint_count=6,
            ),
            step_package_result=_review_package(
                {
                    "base_shoulder": CadBoundingBox(0, 0, 0, 240, 120, 80),
                    "upper_arm": CadBoundingBox(0, 0, 0, 60, 50, 45),
                    "forearm": CadBoundingBox(0, 0, 0, 250, 58, 56),
                    "wrist_l5_j6": CadBoundingBox(0, 0, 0, 130, 68, 68),
                    "tool_end": CadBoundingBox(0, 0, 0, 120, 80, 80),
                }
            ),
        )

        self.assertFalse(report.ok)
        error_codes = {issue.code for issue in report.errors}
        self.assertIn("source_joint_review_bbox_ratio_failed", error_codes)


def _chain_mates(part_ids: list[str]) -> list[dict]:
    return [
        {
            "label": f"{left}_to_{right}",
            "relation": "face_to_face",
            "fixed": "output",
            "moving": "input",
            "fixed_endpoint": {
                "part": left,
                "frame": "output",
                "location": {"position": [0.0, 0.0, 0.0]},
            },
            "moving_endpoint": {
                "part": right,
                "frame": "input",
                "location": {"position": [0.0, 0.0, 0.0]},
            },
        }
        for left, right in zip(part_ids, part_ids[1:])
    ]


def _review_package(
    bboxes: dict[str, CadBoundingBox],
) -> SimpleNamespace:
    subassemblies = [
        SimpleNamespace(
            name=name,
            part_ids=[],
            assembly_export=_export(name, bbox),
        )
        for name, bbox in bboxes.items()
    ]
    all_exports = [_export("whole", CadBoundingBox(0, 0, 0, 500, 200, 200))]
    all_exports.extend(item.assembly_export for item in subassemblies)
    return SimpleNamespace(
        whole_machine_assembly_source="source_joint",
        subassemblies=subassemblies,
        all_exports=all_exports,
    )


def _export(name: str, bbox: CadBoundingBox) -> SimpleNamespace:
    return SimpleNamespace(
        path=Path(f"/tmp/{name}.step"),
        exists=True,
        size_bytes=128,
        bbox=bbox,
    )


if __name__ == "__main__":
    unittest.main()
